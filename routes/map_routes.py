from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from pathlib import Path

from app import (
    selected_instance_or_400, safe_join, list_prototype_files, build_file_entries,
    build_tree, load_yaml_documents, validate_yaml_text,
)

map_bp = Blueprint("map", __name__, url_prefix="/maps")


@map_bp.route("/")
def maps():
    selected = selected_instance_or_400()
    map_root = Path(selected["root_path"]) / "Resources" / "Maps"
    query = request.args.get("q", "").strip().lower()
    files = list_prototype_files(map_root)
    if query:
        files = [f for f in files if query in f.lower()]
    file_entries = build_file_entries(map_root, files, Path(selected["root_path"]))
    tree = build_tree(file_entries)
    return render_template("map.html", tree=tree, query=query, selected=selected)


@map_bp.route("/view", methods=["GET", "POST"])
def map_view():
    from app import session

    selected = selected_instance_or_400()
    rel_file = request.args.get("file", "").strip()
    if not rel_file:
        abort(400)
    map_root = Path(selected["root_path"]) / "Resources" / "Maps"
    file_path = safe_join(map_root, rel_file)

    if request.method == "POST":
        new_content = request.form.get("content", "")
        ok, error = validate_yaml_text(new_content)
        if not ok:
            flash(f"YAML parse error: {error}", "error")
        else:
            normalized = new_content.replace("\r\n", "\n").replace("\r", "\n")
            with file_path.open("w", encoding="utf-8", newline="\n") as f:
                f.write(normalized)
            flash("Map saved.", "success")
        return redirect(url_for("map.map_view", file=rel_file))

    raw_text = file_path.read_text(encoding="utf-8")
    docs = load_yaml_documents(file_path)
    _, parse_error = validate_yaml_text(raw_text)

    return render_template(
        "map_view.html",
        rel_file=rel_file,
        docs=docs,
        raw_text=raw_text,
        parse_ok=not parse_error,
        parse_error=parse_error,
    )
