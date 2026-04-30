from flask import Blueprint, abort, jsonify, request, send_file
from pathlib import Path
import os

import app as main_app

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.get("/instances")
def api_instances():
    return jsonify(main_app.load_instances())


@api_bp.get("/current-instance")
def api_current_instance():
    selected = main_app.selected_instance_or_400()
    return jsonify(selected)


@api_bp.get("/crate-parent-suggest")
def api_crate_parent_suggest():
    selected = main_app.selected_instance_or_400()
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    candidates = main_app.search_ids(selected["name"], q)[:120]
    out = []
    for row in candidates:
        proto_id = row["proto_id"]
        ok, _ = main_app.validate_crate_parent_compatibility(selected, proto_id)
        if not ok:
            continue
        sprite, state = main_app.resolve_preview_for_prototype_id(selected, proto_id)
        out.append(
            {
                "proto_id": proto_id,
                "rel_path": row["rel_path"],
                "sprite": sprite,
                "state": state,
            }
        )
        if len(out) >= 30:
            break
    return jsonify(out)


@api_bp.get("/sprite/preview")
def sprite_preview():
    selected = main_app.selected_instance_or_400()

    sprite = request.args.get("sprite", "").strip()
    state = request.args.get("state", "").strip()
    scale = int(request.args.get("scale", str(main_app.DEFAULT_THUMB_SCALE)))

    if scale < 1 or scale > 16:
        abort(400)

    textures_root = Path(selected["root_path"]) / "Resources" / "Textures"
    sprite_dir = main_app.safe_join(textures_root, sprite)

    if not sprite_dir or not sprite_dir.exists():
        abort(404)

    # Try requested state
    image_path = main_app.safe_join(sprite_dir, f"{state}.png") if state else None

    # Fallbacks
    if not image_path or not image_path.exists():
        pngs = list(sprite_dir.glob("*.png"))

        if not pngs:
            abort(404)

        # Prefer icon if exists
        icon = next((p for p in pngs if p.stem == "icon"), None)
        image_path = icon or pngs[0]

    with main_app.Image.open(image_path) as im:
        im = im.convert("RGBA")
        out = im.resize(
            (im.width * scale, im.height * scale),
            main_app.Image.Resampling.NEAREST
        )

        buffer = main_app.io.BytesIO()
        out.save(buffer, format="PNG")
        buffer.seek(0)

        return send_file(buffer, mimetype="image/png")


@api_bp.get("/open-explorer")
def open_explorer():
    selected = main_app.selected_instance_or_400()
    target = request.args.get("target", "").strip().lower()
    referrer = request.args.get("back") or request.referrer or main_app.url_for("index")
    root = Path(selected["root_path"])

    path = None
    select_file = False
    if target == "yml":
        rel_file = request.args.get("file", "").strip()
        if not rel_file:
            abort(400)
        proto_root = root / "Resources" / "Prototypes"
        path = main_app.safe_join(proto_root, rel_file)
        select_file = True
    elif target == "yml-vscode":
        rel_file = request.args.get("file", "").strip()
        if not rel_file:
            abort(400)
        proto_root = root / "Resources" / "Prototypes"
        path = main_app.safe_join(proto_root, rel_file)
    elif target == "rsi":
        sprite = request.args.get("sprite", "").strip()
        if not sprite:
            abort(400)
        textures_root = root / "Resources" / "Textures"
        path = main_app.safe_join(textures_root, sprite)
    elif target == "audio":
        rel_audio = request.args.get("path", "").strip()
        if not rel_audio:
            abort(400)
        audio_root = root / "Resources" / "Audio"
        path = main_app.safe_join(audio_root, rel_audio)
        select_file = True
    else:
        abort(400, "Invalid target.")

    if not path.exists():
        main_app.flash("Path does not exist on disk.", "error")
        return main_app.redirect(referrer)

    if os.name != "nt":
        main_app.flash("Explorer opening is currently supported only on Windows.", "error")
        return main_app.redirect(referrer)

    if target == "yml-vscode":
        vscode_target = str(path.resolve())
        code_cmd = main_app.find_vscode_cli()
        if not code_cmd:
            main_app.flash(
                f'VS Code CLI not found. Install "code" in PATH. Target: "{vscode_target}"',
                "error",
            )
            return main_app.redirect(referrer)
        try:
            result = main_app.subprocess.run(
                [code_cmd, "-g", vscode_target],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if result.returncode == 0:
                main_app.flash(f'Opened in VS Code: "{vscode_target}"', "success")
            else:
                details = (result.stderr or result.stdout or "No output").strip()
                main_app.flash(f'VS Code exit code {result.returncode}: {details}', "error")
        except FileNotFoundError:
            main_app.flash(
                f'VS Code CLI not found. Install "code" in PATH. Target: "{vscode_target}"',
                "error",
            )
        except main_app.subprocess.TimeoutExpired:
            main_app.flash("Timed out while trying to open VS Code.", "error")
        return main_app.redirect(referrer)
    if select_file and path.is_file():
        main_app.subprocess.Popen(["explorer.exe", "/select,", str(path)])
    else:
        main_app.subprocess.Popen(["explorer.exe", str(path)])

    return main_app.redirect(referrer)