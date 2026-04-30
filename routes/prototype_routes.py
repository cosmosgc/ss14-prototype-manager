from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from pathlib import Path

from app import (
    selected_instance_or_400, safe_join, list_prototype_files, build_file_entries,
    build_tree, load_yaml_documents, validate_yaml_text, collect_sprite_refs,
    collect_audio_refs, collect_prototype_like_refs, build_sprite_cards,
    build_audio_cards, build_prototype_ref_cards, DEFAULT_THUMB_SCALE,
)

prototype_bp = Blueprint("prototype", __name__, url_prefix="/prototypes")


@prototype_bp.route("/")
def prototypes():
    requested_instance = request.args.get("instance", "").strip()
    if requested_instance:
        from app import load_instances, get_instance_by_name
        instances = load_instances()
        match = get_instance_by_name(requested_instance, instances)
        if not match:
            abort(404, "Instance not found.")
        session["selected_instance"] = match["name"]
    selected = selected_instance_or_400()
    proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
    query = request.args.get("q", "").strip().lower()
    files = list_prototype_files(proto_root)
    if query:
        files = [f for f in files if query in f.lower()]
    file_entries = build_file_entries(proto_root, files, Path(selected["root_path"]))
    tree = build_tree(file_entries)
    return render_template("prototypes.html", tree=tree, query=query, selected=selected)


@prototype_bp.route("/view", methods=["GET", "POST"])
def prototype_view():
    from app import session

    selected = selected_instance_or_400()
    rel_file = request.args.get("file", "").strip()
    if not rel_file:
        abort(400)
    proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
    file_path = safe_join(proto_root, rel_file)

    if request.method == "POST":
        new_content = request.form.get("content", "")
        ok, error = validate_yaml_text(new_content)
        if not ok:
            flash(f"YAML parse error: {error}", "error")
        else:
            normalized = new_content.replace("\r\n", "\n").replace("\r", "\n")
            with file_path.open("w", encoding="utf-8", newline="\n") as f:
                f.write(normalized)
            flash("Prototype saved.", "success")
        return redirect(url_for("prototype.prototype_view", file=rel_file))

    raw_text = file_path.read_text(encoding="utf-8")
    docs = load_yaml_documents(file_path)
    sprite_refs = collect_sprite_refs(docs)
    sprite_state_pairs = collect_sprite_state_pairs(docs)
    audio_refs = collect_audio_refs(docs)
    prototype_refs = collect_prototype_like_refs(docs)
    sprite_cards = build_sprite_cards(Path(selected["root_path"]), sprite_refs, sprite_state_pairs)
    audio_cards = build_audio_cards(Path(selected["root_path"]), audio_refs)
    prototype_ref_cards = build_prototype_ref_cards(selected["name"], prototype_refs)
    _, parse_error = validate_yaml_text(raw_text)

    return render_template(
        "prototype_view.html",
        rel_file=rel_file,
        docs=docs,
        raw_text=raw_text,
        sprite_refs=sprite_refs,
        audio_refs=audio_refs,
        prototype_refs=prototype_refs,
        sprite_cards=sprite_cards,
        audio_cards=audio_cards,
        prototype_ref_cards=prototype_ref_cards,
        parse_ok=not parse_error,
        parse_error=parse_error,
    )


@prototype_bp.route("/by-id/<proto_id>")
def prototype_by_id(proto_id: str):
    from app import load_instances, get_instance_by_name

    requested_instance = request.args.get("instance", "").strip()
    if requested_instance:
        instances = load_instances()
        match = get_instance_by_name(requested_instance, instances)
        if not match:
            abort(404, "Instance not found.")
        session["selected_instance"] = match["name"]

    selected = selected_instance_or_400()
    proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"

    rel_files = find_prototype_paths_by_id(selected["name"], proto_id)
    if not rel_files:
        abort(404, f"No prototype found with ID '{proto_id}' in instance '{selected['name']}'")

    # Use the first matching file
    rel_file = rel_files[0]
    file_path = safe_join(proto_root, rel_file)

    return redirect(url_for("prototype.prototype_view", file=rel_file))


def find_prototype_paths_by_id(instance_name: str, proto_id: str):
    from app import get_db

    with get_db() as conn:
        rows = conn.execute(
            "SELECT rel_path FROM prototype_ids WHERE instance_name = ? AND proto_id = ? ORDER BY rel_path",
            (instance_name, proto_id),
        ).fetchall()
    return [r["rel_path"] for r in rows]


def collect_sprite_state_pairs(node):
    pairs = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            sprite = current.get("sprite")
            state = current.get("state")
            if isinstance(sprite, str) and sprite.endswith(".rsi") and isinstance(state, str):
                pairs.append({"sprite": sprite, "state": state})
            for value in current.values():
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    unique = {(p["sprite"], p["state"]) for p in pairs}
    return [{"sprite": s, "state": st} for s, st in sorted(unique)]


def build_prototype_ref_cards(instance_name: str, refs):
    from app import find_first_prototype_path_by_id

    cards = []
    for ref in refs:
        direct_file = find_first_prototype_path_by_id(instance_name, ref["id"])
        cards.append(
            {
                "key": ref["key"],
                "id": ref["id"],
                "direct_file": direct_file,
            }
        )
    return cards
