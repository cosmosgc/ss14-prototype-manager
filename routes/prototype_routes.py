from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from pathlib import Path

from app import (
    selected_instance_or_400, safe_join, list_prototype_files, build_file_entries,
    build_tree, load_yaml_documents_with_error, collect_all_refs, build_sprite_cards,
    build_audio_cards, build_prototype_ref_cards, DEFAULT_THUMB_SCALE,
    resolve_preview_batch, extract_prototypes, get_db, validate_yaml_text,
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
    import time
    _t0 = time.perf_counter()

    selected = selected_instance_or_400()
    _t1 = time.perf_counter(); print(f"[DEBUG] selected_instance_or_400: {_t1-_t0:.3f}s")

    rel_file = request.args.get("file", "").strip()
    if not rel_file:
        abort(400)
    proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
    file_path = safe_join(proto_root, rel_file)
    _t2 = time.perf_counter(); print(f"[DEBUG] path setup: {_t2-_t1:.3f}s")

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
    _t3 = time.perf_counter(); print(f"[DEBUG] read_text: {_t3-_t2:.3f}s")

    docs, parse_error = load_yaml_documents_with_error(file_path)
    _t4 = time.perf_counter(); print(f"[DEBUG] load_yaml_documents_with_error: {_t4-_t3:.3f}s")

    if parse_error:
        return render_template(
            "prototype_view.html",
            rel_file=rel_file,
            docs=[],
            raw_text=raw_text,
            sprite_refs=[],
            audio_refs=[],
            prototype_refs=[],
            sprite_cards=[],
            audio_cards=[],
            prototype_ref_cards=[],
            parse_ok=False,
            parse_error=parse_error,
            all_prototypes=[],
            all_instances=[],
        )

    sprite_refs, audio_refs, prototype_refs, sprite_state_pairs = collect_all_refs(docs)
    _t5 = time.perf_counter(); print(f"[DEBUG] collect_all_refs: {_t5-_t4:.3f}s (sprites={len(sprite_refs)}, audio={len(audio_refs)}, protos={len(prototype_refs)})")

    sprite_cards = build_sprite_cards(Path(selected["root_path"]), sprite_refs, sprite_state_pairs)
    _t6 = time.perf_counter(); print(f"[DEBUG] build_sprite_cards: {_t6-_t5:.3f}s")

    audio_cards = build_audio_cards(Path(selected["root_path"]), audio_refs)
    _t7 = time.perf_counter(); print(f"[DEBUG] build_audio_cards: {_t7-_t6:.3f}s")

    prototype_ref_cards = build_prototype_ref_cards(selected["name"], prototype_refs)
    _t8 = time.perf_counter(); print(f"[DEBUG] build_prototype_ref_cards: {_t8-_t7:.3f}s")

    proto_ids = []
    for doc in docs:
        for proto in extract_prototypes(doc):
            pid = proto.get("id")
            if pid:
                proto_ids.append(pid)
    _t9 = time.perf_counter(); print(f"[DEBUG] extract_prototypes: {_t9-_t8:.3f}s (count={len(proto_ids)})")

    preview_map = resolve_preview_batch(selected["name"], selected["root_path"], proto_ids)
    _t10 = time.perf_counter(); print(f"[DEBUG] resolve_preview_batch: {_t10-_t9:.3f}s")

    all_prototypes = []
    for doc in docs:
        for proto in extract_prototypes(doc):
            proto_id = proto.get("id")
            proto_type = proto.get("type")

            if not proto_id:
                continue

            sprite, state = preview_map.get(proto_id, ("", "icon"))

            all_prototypes.append({
                "id": proto_id,
                "type": proto_type or "unknown",
                "sprite": sprite,
                "state": state,
                "doc": proto
            })
    _t11 = time.perf_counter(); print(f"[DEBUG] build all_prototypes: {_t11-_t10:.3f}s")

    from app import load_instances
    all_instances = load_instances()
    _t12 = time.perf_counter(); print(f"[DEBUG] load_instances: {_t12-_t11:.3f}s")

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
        all_prototypes=all_prototypes,
        all_instances=all_instances,
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


def build_prototype_ref_cards(instance_name: str, refs: list[dict[str, str]]) -> list[dict[str, str | None]]:
    if not refs:
        return []
    ref_ids = [ref["id"] for ref in refs]
    with get_db() as conn:
        rows = conn.execute(
            "SELECT proto_id, rel_path FROM prototype_ids WHERE instance_name = ? AND proto_id IN ("
            + ",".join("?" * len(ref_ids))
            + ") ORDER BY proto_id, rel_path",
            (instance_name, *ref_ids),
        ).fetchall()
    first_path_by_id: dict[str, str | None] = {}
    for r in rows:
        pid = r["proto_id"]
        if pid not in first_path_by_id:
            first_path_by_id[pid] = r["rel_path"]
    return [{"key": ref["key"], "id": ref["id"], "direct_file": first_path_by_id.get(ref["id"])} for ref in refs]
