from flask import Blueprint, abort, render_template, request

import app as main_app
from routes._helpers import _selected_instance_or_400
from pathlib import Path

rsi_bp = Blueprint("rsi", __name__)

# Import shared utilities from main app module
safe_join = main_app.safe_join
list_rsi_states = main_app.list_rsi_states
build_rsi_tree_recursive = main_app.build_rsi_tree_recursive


@rsi_bp.route("/rsi")
def rsi_explorer():
    selected = _selected_instance_or_400()
    textures_root = Path(selected["root_path"]) / "Resources" / "Textures"

    if not textures_root.exists():
        return render_template("rsi_explorer.html", rsi_tree=[], selected=selected)

    rsi_tree = build_rsi_tree_recursive(
        textures_root,
        textures_root
    )

    print("RSI TREE SIZE:", len(rsi_tree))

    return render_template("rsi_explorer.html", rsi_tree=rsi_tree, selected=selected)


@rsi_bp.route("/rsi/view")
def rsi_view():
    from app import json

    selected = _selected_instance_or_400()

    sprite = request.args.get("sprite", "").strip().replace("\\", "/")
    if not sprite:
        abort(400)

    textures_root = Path(selected["root_path"]) / "Resources" / "Textures"
    rsi_dir = safe_join(textures_root, sprite)

    if not rsi_dir or not rsi_dir.exists() or not rsi_dir.is_dir():
        abort(404)

    if not str(rsi_dir).endswith(".rsi"):
        abort(400)

    states = list_rsi_states(rsi_dir)

    if not states:
        states = [p.stem for p in rsi_dir.glob("*.png")]

    states = states or []

    meta_path = rsi_dir / "meta.json"
    meta = None

    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = None

    return render_template(
        "rsi_view.html",
        selected=selected,
        sprite=sprite,
        states=states,
        meta=meta
    )


@rsi_bp.route("/rsi/preview")
def rsi_preview():
    from app import DEFAULT_THUMB_SCALE, Image, io, send_file, json

    selected = _selected_instance_or_400()

    sprite = request.args.get("sprite", "").strip().replace("\\", "/")
    state_name = request.args.get("state", "icon").strip()
    scale = int(request.args.get("scale", str(DEFAULT_THUMB_SCALE)))
    direction = int(request.args.get("direction", "0"))

    if scale < 1 or scale > 16:
        abort(400)

    textures_root = Path(selected["root_path"]) / "Resources" / "Textures"
    rsi_dir = safe_join(textures_root, sprite)

    if not rsi_dir or not rsi_dir.exists():
        abort(404)

    image_path = safe_join(rsi_dir, f"{state_name}.png")
    meta_path = rsi_dir / "meta.json"

    if not image_path or not image_path.exists():
        abort(404)

    meta = None
    if meta_path.exists():
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass

    state_meta = None
    if meta and "states" in meta:
        for s in meta["states"]:
            if s.get("name") == state_name:
                state_meta = s
                break

    with Image.open(image_path) as im:
        im = im.convert("RGBA")

        if not state_meta or "delays" not in state_meta:
            out = im.resize(
                (im.width * scale, im.height * scale),
                Image.Resampling.NEAREST
            )
            buffer = io.BytesIO()
            out.save(buffer, format="PNG")
            buffer.seek(0)
            return send_file(buffer, mimetype="image/png")

        directions = state_meta.get("directions", 1)
        delays = state_meta["delays"]

        frame_delays = delays[0]
        frame_count = len(frame_delays)

        frame_width = im.width // frame_count
        frame_height = im.height // directions

        frames = []

        for i in range(frame_count):
            frame = im.crop((
                i * frame_width,
                direction * frame_height,
                (i + 1) * frame_width,
                (direction + 1) * frame_height
            ))

            frame = frame.resize(
                (frame.width * scale, frame.height * scale),
                Image.Resampling.NEAREST
            )

            frames.append(frame)

        durations = [int(d * 1000) for d in frame_delays]

        buffer = io.BytesIO()
        frames[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            disposal=2
        )

        buffer.seek(0)
        return send_file(buffer, mimetype="image/gif")