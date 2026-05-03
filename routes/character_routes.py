from flask import Blueprint, abort, flash, redirect, render_template, request, url_for, jsonify, send_file
from pathlib import Path
import json
import os

from app import (
    selected_instance_or_400, safe_join, list_prototype_files, build_file_entries,
    build_tree, load_yaml_documents, validate_yaml_text, collect_sprite_refs,
    get_db, load_instances, get_instance_by_name,
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
    char_id = request.form.get("char_id", "").strip()

    if not char_id:
        flash("Character ID is required.", "error")
        return redirect(url_for("character.characters"))

    cache_root = Path("static") / "character_cache" / selected["name"] / char_id
    cache_root.mkdir(parents=True, exist_ok=True)

    proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
    char_data = _parse_character_from_prototypes(proto_root, char_id, selected["root_path"])

    if not char_data:
        flash(f"Character '{char_id}' not found in prototypes.", "error")
        return redirect(url_for("character.characters"))

    meta = {
        "id": char_id,
        "name": char_data.get("name", char_id),
        "species": char_data.get("species", "Human"),
        "slots": char_data.get("available_slots", []),
    }

    with open(cache_root / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    with open(cache_root / "data.json", "w", encoding="utf-8") as f:
        json.dump(char_data, f, indent=2)

    flash(f"Character '{char_id}' imported successfully.", "success")
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

    return render_template(
        "character_view.html",
        char_id=char_id,
        meta=meta,
        char_data=char_data,
        templates=templates,
        selected=selected,
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