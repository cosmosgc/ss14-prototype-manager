from flask import Blueprint, abort, jsonify, request

import app as main_app
from routes._helpers import _selected_instance_or_400
from pathlib import Path

audio_bp = Blueprint("audio", __name__)

safe_join_or_none = main_app.safe_join_or_none


@audio_bp.get("/api/id-suggest")
def api_id_suggest():
    from app import search_ids, resolve_preview_for_row
    
    selected = _selected_instance_or_400()
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])

    rows = search_ids(selected["name"], q)[:30]
    out = []

    for row in rows:
        sprite, state = resolve_preview_for_row(selected, row["rel_path"], row["proto_id"])
        out.append({
            "proto_id": row["proto_id"],
            "rel_path": row["rel_path"],
            "sprite": sprite,
            "state": state,
        })

    return jsonify(out)


@audio_bp.get("/api/rsi-suggest")
def api_rsi_suggest():
    selected = _selected_instance_or_400()
    q = request.args.get("q", "").strip().lower()

    if not q:
        return jsonify([])

    textures_root = Path(selected["root_path"]) / "Resources" / "Textures"
    out = []

    if textures_root.exists():
        for p in textures_root.rglob("*.rsi"):
            rel = p.relative_to(textures_root).as_posix()
            if q in rel.lower():
                out.append(rel)
                if len(out) >= 40:
                    break

    return jsonify(sorted(out))


@audio_bp.get("/api/rsi-states")
def api_rsi_states():
    from app import json

    selected = _selected_instance_or_400()
    sprite = request.args.get("sprite", "").strip()

    if not sprite:
        return jsonify([])

    textures_root = Path(selected["root_path"]) / "Resources" / "Textures"
    rsi_dir = safe_join_or_none(textures_root, sprite)

    if not rsi_dir or not rsi_dir.exists():
        return jsonify([])

    meta_path = rsi_dir / "meta.json"
    states = []

    if meta_path.exists():
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            states = [s.get("name") for s in meta.get("states", []) if isinstance(s, dict) and s.get("name")]
        except Exception:
            states = []

    if not states:
        states = [p.stem for p in sorted(rsi_dir.glob("*.png"))]

    return jsonify(sorted(set(states)))


@audio_bp.get("/audio/play")
def audio_play():
    from app import send_file, safe_join

    selected = _selected_instance_or_400()
    rel = request.args.get("path", "").strip()

    if not rel:
        abort(400)

    audio_root = Path(selected["root_path"]) / "Resources" / "Audio"
    file_path = safe_join(audio_root, rel)

    if not file_path.exists():
        abort(404)

    return send_file(file_path)