from flask import Blueprint, abort, flash, redirect, render_template, request, url_for, jsonify, send_file
from pathlib import Path
import json
import os
import yaml

from app import (
    selected_instance_or_400, safe_join, list_prototype_files, build_file_entries,
    build_tree, load_yaml_documents, validate_yaml_text, collect_sprite_refs,
    get_db, load_instances, get_instance_by_name, load_instances,
    resolve_preview_batch,
)

character_bp = Blueprint("character", __name__, url_prefix="/characters")


@character_bp.route("/")
def characters():
    requested_instance = request.args.get("instance", "").strip()
    if requested_instance:
        instances = load_instances()
        match = get_instance_by_name(requested_instance, instances)
        if not match:
            abort(404, "Instance not found.")
        session["selected_instance"] = match["name"]

    selected = selected_instance_or_400()
    cache_root = Path("static") / "character_cache" / selected["name"]

    characters_list = []
    if cache_root.exists():
        for char_dir in cache_root.iterdir():
            if char_dir.is_dir():
                meta_path = char_dir / "meta.json"
                if meta_path.exists():
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                        characters_list.append({
                            "id": char_dir.name,
                            "name": meta.get("name", char_dir.name),
                            "species": meta.get("species", "Unknown"),
                            "preview": meta.get("preview"),
                        })

    return render_template(
        "characters.html",
        characters=characters_list,
        selected=selected,
    )


@character_bp.route("/import", methods=["POST"])
def character_import():
    selected = selected_instance_or_400()

    file = request.files.get("file")
    if not file or file.filename == "":
        char_id = request.form.get("char_id", "").strip()
        if not char_id:
            flash("Character file or ID is required.", "error")
            return redirect(url_for("character.characters"))
    else:
        char_id = Path(file.filename).stem

    cache_root = Path("static") / "character_cache" / selected["name"] / char_id
    cache_root.mkdir(parents=True, exist_ok=True)

    if file and file.filename != "":
        content = file.read()
        if content.startswith(b"\x89PNG"):
            flash("Character file appears to be an image, not a YAML file.", "error")
            return redirect(url_for("character.characters"))

        try:
            char_data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            flash(f"Failed to parse YAML: {e}", "error")
            return redirect(url_for("character.characters"))

        parsed = _parse_character_file(char_data)

        with open(cache_root / "original.yaml", "wb") as f:
            f.write(content)
    else:
        flash("Import from prototypes not yet implemented. Please upload a character file.", "error")
        return redirect(url_for("character.characters"))

    available_slots = _collect_all_slots(parsed.get("loadouts", {}))

    meta = {
        "id": char_id,
        "name": parsed.get("name", char_id),
        "species": parsed.get("species", "Human"),
        "slots": available_slots,
    }

    with open(cache_root / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    with open(cache_root / "data.json", "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2)

    flash(f"Character '{parsed.get('name', char_id)}' imported successfully.", "success")
    return redirect(url_for("character.character_view", char_id=char_id))


@character_bp.route("/<char_id>")
def character_view(char_id: str):
    selected = selected_instance_or_400()
    cache_root = Path("static") / "character_cache" / selected["name"] / char_id

    meta_path = cache_root / "meta.json"
    data_path = cache_root / "data.json"

    if not meta_path.exists() or not data_path.exists():
        abort(404, "Character not found in cache.")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    with open(data_path, "r", encoding="utf-8") as f:
        char_data = json.load(f)

    templates = _load_templates(cache_root)

    all_proto_ids = _collect_all_proto_ids_from_character(char_data)
    preview_map = resolve_preview_batch(selected["name"], selected["root_path"], all_proto_ids)

    return render_template(
        "character_view.html",
        char_id=char_id,
        meta=meta,
        char_data=char_data,
        templates=templates,
        selected=selected,
        preview_map=preview_map,
    )


@character_bp.route("/<char_id>/dressup", methods=["POST"])
def character_dressup(char_id: str):
    selected = selected_instance_or_400()
    cache_root = Path("static") / "character_cache" / selected["name"] / char_id

    data_path = cache_root / "data.json"
    if not data_path.exists():
        abort(404, "Character not found.")

    with open(data_path, "r", encoding="utf-8") as f:
        char_data = json.load(f)

    slot = request.form.get("slot", "").strip()
    item_id = request.form.get("item_id", "").strip()

    if slot and item_id:
        char_data["equipment"] = char_data.get("equipment", {})
        char_data["equipment"][slot] = item_id

        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(char_data, f, indent=2)

        flash(f"Equipped {item_id} in {slot}.", "success")

    return redirect(url_for("character.character_view", char_id=char_id))


@character_bp.route("/<char_id>/save-template", methods=["POST"])
def save_template(char_id: str):
    selected = selected_instance_or_400()
    cache_root = Path("static") / "character_cache" / selected["name"] / char_id

    data_path = cache_root / "data.json"
    if not data_path.exists():
        abort(404, "Character not found.")

    template_name = request.form.get("template_name", "").strip()
    if not template_name:
        flash("Template name is required.", "error")
        return redirect(url_for("character.character_view", char_id=char_id))

    with open(data_path, "r", encoding="utf-8") as f:
        char_data = json.load(f)

    template = {
        "name": template_name,
        "equipment": char_data.get("equipment", {}),
        "created_at": str(Path(__file__).stat().st_mtime),
    }

    template_path = cache_root / "templates" / f"{template_name}.json"
    template_path.parent.mkdir(parents=True, exist_ok=True)

    with open(template_path, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2)

    flash(f"Template '{template_name}' saved.", "success")
    return redirect(url_for("character.character_view", char_id=char_id))


@character_bp.route("/<char_id>/load-template", methods=["POST"])
def load_template(char_id: str):
    selected = selected_instance_or_400()
    cache_root = Path("static") / "character_cache" / selected["name"] / char_id

    data_path = cache_root / "data.json"
    template_name = request.form.get("template_name", "").strip()

    if not data_path.exists() or not template_name:
        abort(404, "Character or template not found.")

    template_path = cache_root / "templates" / f"{template_name}.json"
    if not template_path.exists():
        flash(f"Template '{template_name}' not found.", "error")
        return redirect(url_for("character.character_view", char_id=char_id))

    with open(template_path, "r", encoding="utf-8") as f:
        template = json.load(f)

    with open(data_path, "r", encoding="utf-8") as f:
        char_data = json.load(f)

    char_data["equipment"] = template.get("equipment", {})

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(char_data, f, indent=2)

    flash(f"Template '{template_name}' loaded.", "success")
    return redirect(url_for("character.character_view", char_id=char_id))


@character_bp.route("/<char_id>/delete-template", methods=["POST"])
def delete_template(char_id: str):
    selected = selected_instance_or_400()
    cache_root = Path("static") / "character_cache" / selected["name"] / char_id

    template_name = request.form.get("template_name", "").strip()
    if not template_name:
        flash("Template name is required.", "error")
        return redirect(url_for("character.character_view", char_id=char_id))

    template_path = cache_root / "templates" / f"{template_name}.json"
    if template_path.exists():
        template_path.unlink()
        flash(f"Template '{template_name}' deleted.", "success")

    return redirect(url_for("character.character_view", char_id=char_id))


@character_bp.route("/search-prototypes")
def search_character_prototypes():
    selected = selected_instance_or_400()
    query = request.args.get("q", "").strip().lower()

    if not query:
        return jsonify([])

    proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
    all_files = list_prototype_files(proto_root)

    matching_prototypes = []

    for rel_file in all_files:
        file_path = safe_join(proto_root, rel_file)
        if not file_path or not file_path.exists():
            continue

        try:
            docs = load_yaml_documents(file_path)
            for doc in docs:
                for proto in _extract_prototypes(doc):
                    proto_id = proto.get("id", "")
                    if query in proto_id.lower():
                        proto_type = proto.get("type", "")
                        if _is_character_related(proto):
                            matching_prototypes.append({
                                "id": proto_id,
                                "type": proto_type,
                                "file": rel_file,
                            })
        except Exception:
            continue

    return jsonify(matching_prototypes[:50])


def _extract_prototypes(doc):
    if isinstance(doc, dict):
        if "type" in doc:
            yield doc
        for value in doc.values():
            yield from _extract_prototypes(value)
    elif isinstance(doc, list):
        for item in doc:
            yield from _extract_prototypes(item)


def _is_character_related(proto: dict) -> bool:
    if not isinstance(proto, dict):
        return False

    type_val = proto.get("type", "").lower()
    if "humanoid" in type_val or "species" in type_val or "character" in type_val:
        return True

    if "components" in proto:
        components = proto["components"]
        if isinstance(components, dict):
            comp_keys = components.keys()
        elif isinstance(components, list):
            comp_keys = set(str(c) for c in components)
        else:
            comp_keys = set()

        character_components = {
            "holding", "wearing", "inventory", "hands", "container",
            "character", "humanoid", "dna", "species",
        }
        if any(c.lower() in character_components for c in comp_keys):
            return True

    return False


def _parse_character_from_prototypes(proto_root: Path, char_id: str, root_path: str) -> dict | None:
    all_files = list_prototype_files(proto_root)

    for rel_file in all_files:
        file_path = safe_join(proto_root, rel_file)
        if not file_path or not file_path.exists():
            continue

        try:
            docs = load_yaml_documents(file_path)
            for doc in docs:
                for proto in _extract_prototypes(doc):
                    if proto.get("id") == char_id:
                        return _build_character_data(proto, root_path)
        except Exception:
            continue

    return None


def _build_character_data(proto: dict, root_path: str) -> dict:
    char_data = {
        "id": proto.get("id"),
        "name": proto.get("name", proto.get("id", "Unknown")),
        "type": proto.get("type", "unknown"),
    }

    species = proto.get("species") or proto.get("components", {}).get("species")
    char_data["species"] = species or "Human"

    components = proto.get("components", {})

    available_slots = []
    for comp_name in components.keys():
        comp_name_lower = comp_name.lower()
        if any(s in comp_name_lower for s in ["holding", "wear", "hand", "slot", "inventory", "container"]):
            slot_name = comp_name_lower.replace("component", "").strip()
            if slot_name and slot_name not in available_slots:
                available_slots.append(slot_name)

    available_slots.extend(["head", "back", "uniform", "outer", "belt", "gloves", "shoes", "id", "pda", "hand1", "hand2"])
    char_data["available_slots"] = list(dict.fromkeys(available_slots))

    char_data["equipment"] = {}
    char_data["components"] = components

    return char_data


def _load_templates(cache_root: Path) -> list[dict]:
    templates = []
    templates_dir = cache_root / "templates"

    if templates_dir.exists():
        for tmpl_file in templates_dir.glob("*.json"):
            with open(tmpl_file, "r", encoding="utf-8") as f:
                tmpl = json.load(f)
                tmpl["filename"] = tmpl_file.name
                templates.append(tmpl)

    return templates


def _collect_all_proto_ids_from_character(char_data: dict) -> list[str]:
    ids = set()

    equipment = char_data.get("equipment", {})
    for item in equipment.values():
        if item:
            ids.add(item)

    loadouts = char_data.get("loadouts", {})
    for job, loadout_data in loadouts.items():
        selected = loadout_data.get("selectedLoadouts", {})
        if isinstance(selected, dict):
            for items in selected.values():
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and item.get("prototype"):
                            ids.add(item["prototype"])
                        elif isinstance(item, str) and item:
                            ids.add(item)

    for mark_id in char_data.get("markingIds", []):
        if mark_id:
            ids.add(mark_id)

    return list(ids)


def _parse_character_file(data: dict) -> dict:
    profile = data.get("profile", {})
    appearance = profile.get("appearance", {})

    parsed = {
        "name": profile.get("name", "Unknown"),
        "species": profile.get("species", "Human"),
        "gender": profile.get("gender", "Unknown"),
        "sex": profile.get("sex", "Unknown"),
        "age": profile.get("age", 0),
        "flavorText": profile.get("flavorText", ""),
        "version": data.get("version", 1),
        "forkId": data.get("forkId", ""),
    }

    parsed["appearance"] = {
        "skinColor": appearance.get("skinColor", "#000000"),
        "eyeColor": appearance.get("eyeColor", "#000000"),
        "hairColor": appearance.get("hairColor", "#000000"),
        "facialHairColor": appearance.get("facialHairColor", "#000000"),
        "hair": appearance.get("hair", ""),
        "facialHair": appearance.get("facialHair", ""),
    }

    markings = []
    for mark in appearance.get("markings", []):
        mark_entry = {
            "id": mark.get("markingId", ""),
            "visible": mark.get("visible", True),
            "colors": mark.get("markingColor", []),
        }
        markings.append(mark_entry)
    parsed["appearance"]["markings"] = markings

    parsed["markingIds"] = [m["id"] for m in markings if m["id"]]
    parsed["markingColors"] = {}
    for m in markings:
        if m["id"]:
            parsed["markingColors"][m["id"]] = m["colors"]

    loadouts = profile.get("_loadouts", {})
    parsed["loadouts"] = loadouts

    all_item_ids = _collect_all_item_ids(loadouts)
    parsed["allItemIds"] = all_item_ids

    slots = _collect_all_slots(loadouts)
    parsed["availableSlots"] = slots

    parsed["equipment"] = {}
    parsed["currentJob"] = "JobPassenger"

    traits = profile.get("_traitPreferences", [])
    parsed["traits"] = traits

    job_priorities = profile.get("_jobPriorities", {})
    parsed["jobPriorities"] = job_priorities

    cosmatic_records = profile.get("cosmaticDriftCharacterRecords", {})

    medical = cosmatic_records.get("medicalEntries", [])
    parsed["medicalEntries"] = medical

    security = cosmatic_records.get("securityEntries", [])
    parsed["securityEntries"] = security

    employment = cosmatic_records.get("employmentEntries", [])
    parsed["employmentEntries"] = employment

    parsed["weight"] = profile.get("weight", 0)
    parsed["height"] = profile.get("height", 0)
    parsed["identifyingFeatures"] = profile.get("identifyingFeatures", "")
    parsed["allergies"] = profile.get("allergies", [])
    parsed["drugAllergies"] = profile.get("drugAllergies", "")
    parsed["emergencyContactName"] = profile.get("emergencyContactName", "")

    return parsed


def _collect_all_item_ids(loadouts: dict) -> list[str]:
    item_ids = []
    for job, loadout_data in loadouts.items():
        selected = loadout_data.get("selectedLoadouts", {})
        if isinstance(selected, dict):
            for slot_name, items in selected.items():
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and "prototype" in item:
                            pid = item["prototype"]
                            if pid and pid not in item_ids:
                                item_ids.append(pid)
                        elif isinstance(item, str) and item:
                            if item not in item_ids:
                                item_ids.append(item)
    return sorted(item_ids)


def _collect_all_slots(loadouts: dict) -> list[str]:
    slot_names = set()
    slot_mapping = {
        "Head": "head",
        "OuterClothing": "outer",
        "InnerClothing": "uniform",
        "Backpack": "back",
        "Belt": "belt",
        "Gloves": "gloves",
        "Shoes": "shoes",
        "Neck": "neck",
        "Mask": "mask",
        "Eyewear": "eyes",
        "PDA": "pda",
        "Id": "id",
        "Trinkets": "trinkets",
        "Scarfs": "scarf",
        "Jumpsuit": "jumpsuit",
        "TankHarness": "tank",
    }

    for job, loadout_data in loadouts.items():
        selected = loadout_data.get("selectedLoadouts", {})
        if isinstance(selected, dict):
            for slot_name in selected.keys():
                normalized = slot_mapping.get(slot_name, slot_name.lower())
                slot_names.add(normalized)

    standard_slots = [
        "head", "mask", "eyes", "neck", "uniform", "outer", "gloves",
        "shoes", "belt", "back", "id", "pda", "hand1", "hand2", "trinkets", "scarf"
    ]
    for s in standard_slots:
        slot_names.add(s)

    return sorted(slot_names)