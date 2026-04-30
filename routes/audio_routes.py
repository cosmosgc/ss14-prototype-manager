from flask import Blueprint, abort, jsonify, render_template, request, url_for, send_file
from pathlib import Path

from app import (
    selected_instance_or_400, safe_join, DEFAULT_THUMB_SCALE, Image, io, send_file,
)

audio_bp = Blueprint("audio", __name__, url_prefix="/audio")


@audio_bp.route("/")
def audio_explorer():
    from app import session

    selected = selected_instance_or_400()
    audio_root = Path(selected["root_path"]) / "Resources" / "Audio"
    if not audio_root.exists():
        return render_template("audio_explorer.html", audio_files=[], selected=selected)

    files = []
    for root, dirs, filenames in sorted(audio_root.walk()):
        for filename in sorted(filenames):
            full_path = Path(root) / filename
            rel_path = full_path.relative_to(audio_root).as_posix()
            files.append({
                "path": f"/Audio/{rel_path}",
                "name": filename,
                "size": full_path.stat().st_size if full_path.exists() else 0,
            })

    return render_template("audio_explorer.html", audio_files=files, selected=selected)


@audio_bp.route("/play")
def audio_play():
    from app import session

    selected = selected_instance_or_400()
    rel = request.args.get("path", "").strip()
    if not rel:
        abort(400)
    audio_root = Path(selected["root_path"]) / "Resources" / "Audio"
    file_path = safe_join(audio_root, rel)
    if not file_path.exists():
        abort(404)
    return send_file(file_path)


@audio_bp.route("/preview")
def audio_preview():
    from app import session

    selected = selected_instance_or_400()
    path = request.args.get("path", "").strip()
    if not path:
        abort(400)

    audio_root = Path(selected["root_path"]) / "Resources" / "Audio"
    file_path = safe_join(audio_root, path)

    if not file_path or not file_path.exists():
        abort(404)

    return send_file(file_path)


@audio_bp.route("/api/suggest")
def api_audio_suggest():
    from app import session

    selected = selected_instance_or_400()
    q = request.args.get("q", "").strip().lower()
    if not q:
        return jsonify([])
    audio_root = Path(selected["root_path"]) / "Resources" / "Audio"
    out = []
    if audio_root.exists():
        for p in audio_root.rglob("*"):
            if p.suffix.lower() in {".ogg", ".mp3"}:
                rel = p.relative_to(audio_root).as_posix()
                if q in rel.lower():
                    out.append(rel)
                    if len(out) >= 40:
                        break
    return jsonify(sorted(out))


def safe_join_or_none(base: Path, relative: str):
    from app import safe_join as _safe_join
    try:
        return _safe_join(base, relative)
    except Exception:
        return None
