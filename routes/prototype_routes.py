from flask import Blueprint, abort, flash, redirect, render_template, request, url_for


import app as main_app
from pathlib import Path

# Import shared utilities from main app module
safe_join = main_app.safe_join
validate_yaml_text = main_app.validate_yaml_text
load_yaml_documents = main_app.load_yaml_documents
collect_sprite_refs = main_app.collect_sprite_refs
collect_sprite_state_pairs = main_app.collect_sprite_state_pairs
collect_audio_refs = main_app.collect_audio_refs
collect_prototype_like_refs = main_app.collect_prototype_like_refs
build_sprite_cards = main_app.build_sprite_cards
build_audio_cards = main_app.build_audio_cards
find_prototype_paths_by_id = main_app.find_prototype_paths_by_id
build_file_entries = main_app.build_file_entries
build_tree = main_app.build_tree


prototype_bp = Blueprint(
    "prototypes",
    __name__,
    url_prefix="/prototypes"
)

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
    selected = _get_selected_instance_or_400()
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
    from app import safe_join
    
    selected = _get_selected_instance_or_400()
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
        return redirect(url_for("prototype_view", file=rel_file))

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
        parse_error=parse_error,
        sprite_cards=sprite_cards,
        audio_cards=audio_cards,
        prototype_ref_cards=prototype_ref_cards,
        selected=selected,
    )


@prototype_bp.get("/by-id")
def prototype_by_id():
    from app import find_prototype_paths_by_id
    
    selected = _get_selected_instance_or_400()
    proto_id = request.args.get("id", "").strip()
    if not proto_id:
        abort(400, "Missing id.")
    files = find_prototype_paths_by_id(selected["name"], proto_id)
    if not files:
        flash(f"Prototype id not found: {proto_id}", "error")
        return redirect(url_for("prototype_view", file=files[0]))
    return redirect(url_for("prototype_view", file=files[0]))
