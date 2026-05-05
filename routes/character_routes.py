from flask import Blueprint, abort, flash, redirect, render_template, request, url_for, jsonify, send_file, session
from pathlib import Path
import json
import os
import yaml

from app import (
    selected_instance_or_400, safe_join, list_prototype_files, build_file_entries,
    build_tree, load_yaml_documents, validate_yaml_text, collect_sprite_refs,
    get_db, load_instances, get_instance_by_name, load_instances,
    resolve_preview_batch, get_rsi_state_info,
    find_first_prototype_path_by_id,
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

        enriched = _enrich_character_data_with_paths(parsed, selected["name"], selected["root_path"])

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
        json.dump(enriched, f, indent=2)

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

    enriched_data = _enrich_character_data_with_paths(char_data, selected["name"], selected["root_path"])

    templates = _load_templates(cache_root)

    all_proto_ids, loadout_entity_map = _collect_all_proto_ids_from_character(enriched_data)
    entity_ids_for_preview = set(all_proto_ids)
    for loadout_id, entity_id in loadout_entity_map.items():
        if entity_id:
            entity_ids_for_preview.add(entity_id)

    proto_data_map = _build_proto_data_map(selected["name"], list(entity_ids_for_preview))
    preview_map = resolve_preview_batch(selected["name"], selected["root_path"], list(entity_ids_for_preview))

    extended_preview_map = dict(preview_map)
    for loadout_id, entity_id in loadout_entity_map.items():
        if entity_id and entity_id in preview_map:
            extended_preview_map[loadout_id] = preview_map[entity_id]

    direction_info = {}
    for proto_id in all_proto_ids:
        preview = extended_preview_map.get(proto_id)
        entity_id = loadout_entity_map.get(proto_id, proto_id)
        if entity_id and entity_id in preview_map:
            preview = preview_map[entity_id]
        if preview and preview[0]:
            state_info = get_rsi_state_info(selected, preview[0], preview[1])
            state_info["proto_paths"] = proto_data_map.get(entity_id, {}).get("proto_paths", [])
            direction_info[proto_id] = state_info

    equipment_layers = _build_equipment_layers(char_data.get("equipment", {}), extended_preview_map, direction_info)

    character_viewer = _build_character_viewer(char_data, preview_map, direction_info)
    character_viewer["species_parts"] = {}

    species = char_data.get("species", "").lower()
    gender = char_data.get("gender", "").lower()
    sex = char_data.get("sex", "").lower()

    if species and species != "human":
        suffix = "_m"
        if gender == "female" or sex == "female":
            suffix = "_f"

        parts_path = f"_DV/Mobs/Species/{species}/parts.rsi"
        character_viewer["species_parts"] = {
            "sprite": parts_path,
            "torso": f"torso{suffix}",
            "head": f"head{suffix}",
            "l_arm": "l_arm",
            "r_arm": "r_arm",
            "l_leg": "l_leg",
            "r_leg": "r_leg",
            "l_hand": "l_hand",
            "r_hand": "r_hand",
            "l_foot": "l_foot",
            "r_foot": "r_foot",
        }

    return render_template(
        "character_view.html",
        char_id=char_id,
        meta=meta,
        char_data=char_data,
        templates=templates,
        selected=selected,
        preview_map=preview_map,
        direction_info=direction_info,
        equipment_layers=equipment_layers,
        character_viewer=character_viewer,
        proto_data_map=proto_data_map,
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

        enriched = _enrich_character_data_with_paths(char_data, selected["name"], selected["root_path"])

        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(enriched, f, indent=2)

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

    enriched = _enrich_character_data_with_paths(char_data, selected["name"], selected["root_path"])

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2)

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

    loadout_entity_map = {}
    loadouts = char_data.get("loadouts", {})
    for job, loadout_data in loadouts.items():
        selected = loadout_data.get("selectedLoadouts", {})
        if isinstance(selected, dict):
            for slot_name, items in selected.items():
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and item.get("prototype"):
                            proto = item.get("prototype")
                            loadout_id = item.get("id", "")
                            if loadout_id:
                                loadout_entity_map[loadout_id] = proto
                            ids.add(proto)
                        elif isinstance(item, str) and item:
                            ids.add(item)

    for mark_id in char_data.get("markingIds", []):
        if mark_id:
            ids.add(mark_id)

    return list(ids), loadout_entity_map


def _build_proto_data_map(instance_name: str, proto_ids: list[str]) -> dict[str, dict]:
    if not proto_ids:
        return {}

    with get_db() as conn:
        placeholders = ",".join("?" * len(proto_ids))
        rows = conn.execute(
            f"SELECT proto_id, rel_path FROM prototype_ids WHERE instance_name = ? AND proto_id IN ({placeholders})",
            (instance_name, *proto_ids),
        ).fetchall()

    proto_data = {}
    for r in rows:
        pid = r["proto_id"]
        if pid not in proto_data:
            proto_data[pid] = {"proto_paths": [], "rsi_path": None}
        proto_data[pid]["proto_paths"].append(r["rel_path"])

    return proto_data


def _enrich_character_data_with_paths(char_data: dict, instance_name: str, root_path: str) -> dict:
    all_proto_ids, loadout_entity_map = _collect_all_proto_ids_from_character(char_data)
    if not all_proto_ids:
        return char_data

    entity_ids_for_preview = set(all_proto_ids)
    for loadout_id, entity_id in loadout_entity_map.items():
        if entity_id:
            entity_ids_for_preview.add(entity_id)

    proto_data_map = _build_proto_data_map(instance_name, list(entity_ids_for_preview))
    preview_map = resolve_preview_batch(instance_name, root_path, list(entity_ids_for_preview))

    enriched = dict(char_data)

    if "equipment" not in enriched:
        enriched["equipment"] = {}
    enriched["_proto_paths"] = {}
    enriched["_rsi_paths"] = {}
    enriched["_loadout_entity_map"] = loadout_entity_map

    for proto_id in all_proto_ids:
        proto_paths = proto_data_map.get(proto_id, {}).get("proto_paths", [])
        if proto_paths:
            enriched["_proto_paths"][proto_id] = proto_paths

        entity_id = loadout_entity_map.get(proto_id, proto_id)
        preview = preview_map.get(entity_id) or preview_map.get(proto_id)
        if preview and preview[0]:
            enriched["_rsi_paths"][proto_id] = {
                "sprite": preview[0],
                "state": preview[1],
            }

    return enriched


def _build_equipment_layers(equipment: dict, preview_map: dict, direction_info: dict) -> list[dict]:
    slot_order = [
        "inner", "uniform", "neck", "head", "mask", "eyes",
        "outer", "back", "belt", "gloves", "shoes",
        "id", "pda", "hand1", "hand2", "trinkets", "scarf"
    ]

    slot_layer_map = {slot: i for i, slot in enumerate(slot_order)}

    layers = []
    for slot, item in equipment.items():
        if not item:
            continue
        preview = preview_map.get(item, ("", ""))
        if not preview or not preview[0]:
            continue

        state_info = direction_info.get(item, {})
        layer_index = slot_layer_map.get(slot, 999)

        layers.append({
            "slot": slot,
            "item": item,
            "sprite": preview[0],
            "state": preview[1],
            "layer": layer_index,
            "directions": state_info.get("directions", 1),
            "proto_paths": state_info.get("proto_paths", []),
        })

    layers.sort(key=lambda x: x["layer"])
    return layers


def _build_character_viewer(char_data: dict, preview_map: dict, direction_info: dict) -> dict:
    slot_regions = {
        "head": ["head", "hair", "facialHair"],
        "eyes": ["eyes", "mask"],
        "neck": ["neck", "scarf"],
        "body": ["uniform", "inner"],
        "outer": ["outer"],
        "back": ["back"],
        "belt": ["belt"],
        "hands": ["gloves", "hand1", "hand2"],
        "feet": ["shoes"],
        "id": ["id", "pda"],
    }

    regions = {}
    for region, slots in slot_regions.items():
        region_items = []
        for slot in slots:
            if slot == "hair" and char_data.get("appearance", {}).get("hair"):
                hair_id = char_data["appearance"]["hair"]
                preview = preview_map.get(hair_id, ("", ""))
                if preview and preview[0]:
                    state_info = direction_info.get(hair_id, {})
                    region_items.append({
                        "slot": "hair",
                        "item": hair_id,
                        "sprite": preview[0],
                        "state": preview[1],
                        "directions": state_info.get("directions", 1),
                        "proto_paths": state_info.get("proto_paths", []),
                        "color": char_data.get("appearance", {}).get("hairColor", "#000000"),
                    })
            elif slot == "facialHair" and char_data.get("appearance", {}).get("facialHair"):
                fh_id = char_data["appearance"]["facialHair"]
                preview = preview_map.get(fh_id, ("", ""))
                if preview and preview[0]:
                    state_info = direction_info.get(fh_id, {})
                    region_items.append({
                        "slot": "facialHair",
                        "item": fh_id,
                        "sprite": preview[0],
                        "state": preview[1],
                        "directions": state_info.get("directions", 1),
                        "proto_paths": state_info.get("proto_paths", []),
                        "color": char_data.get("appearance", {}).get("facialHairColor", "#000000"),
                    })
            elif slot in char_data.get("equipment", {}):
                item = char_data["equipment"][slot]
                if item:
                    preview = preview_map.get(item, ("", ""))
                    if preview and preview[0]:
                        state_info = direction_info.get(item, {})
                        region_items.append({
                            "slot": slot,
                            "item": item,
                            "sprite": preview[0],
                            "state": preview[1],
                            "directions": state_info.get("directions", 1),
                            "proto_paths": state_info.get("proto_paths", []),
                        })
        regions[region] = region_items

    markings_list = []
    for mark_id in char_data.get("markingIds", []):
        preview = preview_map.get(mark_id, ("", ""))
        if preview and preview[0]:
            state_info = direction_info.get(mark_id, {})
            markings_list.append({
                "item": mark_id,
                "sprite": preview[0],
                "state": preview[1],
                "directions": state_info.get("directions", 1),
                "proto_paths": state_info.get("proto_paths", []),
                "colors": char_data.get("markingColors", {}).get(mark_id, []),
            })

    return {
        "regions": regions,
        "base_layer": char_data.get("appearance", {}),
        "markings": markings_list,
    }


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


@character_bp.route("/api/outfit", methods=["GET"])
def api_outfit_get():
    char_id = request.args.get("char_id", "").strip()
    if not char_id:
        return jsonify({})
    key = f"outfit_{char_id}"
    outfit = session.get(key, {})
    return jsonify(outfit)


@character_bp.route("/api/outfit", methods=["POST"])
def api_outfit_update():
    char_id = request.args.get("char_id", "").strip()
    if not char_id:
        return jsonify({"success": False, "error": "No char_id"})

    action = request.args.get("action", "equip")
    item_id = request.args.get("item_id", "").strip()
    slot = request.args.get("slot", "").strip()
    color = request.args.get("color", "").strip()

    key = f"outfit_{char_id}"
    outfit = session.get(key, {})

    if action == "equip":
        if not item_id or not slot:
            return jsonify({"success": False, "error": "item_id and slot required"})
        if slot not in outfit:
            outfit[slot] = {}
        outfit[slot]["item"] = item_id
        if color:
            outfit[slot]["color"] = color
    elif action == "unequip":
        if slot in outfit:
            del outfit[slot]
    elif action == "set_color":
        if slot in outfit and color:
            outfit[slot]["color"] = color
    elif action == "clear":
        outfit = {}

    session[key] = outfit
    return jsonify({"success": True, "outfit": outfit})


@character_bp.route("/api/item-list", methods=["GET"])
def api_item_list():
    selected = selected_instance_or_400()
    q = request.args.get("q", "").strip().lower()
    slot_filter = request.args.get("slot", "").strip()
    with_preview = request.args.get("preview", "").strip() == "1"

    where_clauses = ["pc.component_type = 'Clothing'"]
    params = [selected["name"]]

    if slot_filter:
        where_clauses.append("""
            EXISTS (
                SELECT 1 FROM prototype_component_fields pcf
                WHERE pcf.instance_name = pc.instance_name
                AND pcf.proto_id = pc.proto_id
                AND pcf.component_type = 'Clothing'
                AND pcf.field_name = 'slots'
                AND pcf.field_value LIKE ?
            )
        """)
        params.append(f"%{slot_filter}%")

    if q:
        where_clauses.append("(pc.proto_id LIKE ?)")
        params.append(f"%{q}%")

    where_sql = " AND ".join(where_clauses)

    query = f"""
        SELECT DISTINCT pc.proto_id
        FROM prototype_components pc
        WHERE pc.instance_name = ? AND {where_sql}
        ORDER BY pc.proto_id
        LIMIT 100
    """

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    items = []
    for row in rows:
        item_data = {"id": row["proto_id"]}

        if with_preview:
            preview = resolve_preview_batch(selected["name"], selected["root_path"], [row["proto_id"]]).get(row["proto_id"])
            if preview and preview[0]:
                item_data["sprite"] = preview[0]
                item_data["state"] = preview[1]

        items.append(item_data)

    return jsonify(items)