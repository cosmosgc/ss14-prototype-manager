from flask import Blueprint, abort, flash, jsonify, render_template, request, url_for
from pathlib import Path
import json

from app import (
    selected_instance_or_400, safe_join, list_rsi_states, build_rsi_tree_recursive,
    DEFAULT_THUMB_SCALE, Image, io, send_file,
)

rsi_bp = Blueprint("rsi", __name__, url_prefix="/rsi")


@rsi_bp.route("/")
def rsi_explorer():
    selected = selected_instance_or_400()
    textures_root = Path(selected["root_path"]) / "Resources" / "Textures"
    if not textures_root.exists():
        return render_template("rsi_explorer.html", rsi_tree=[], selected=selected)
    rsi_tree = build_rsi_tree_recursive(
        textures_root,
        textures_root
    )
    print("RSI TREE SIZE:", len(rsi_tree))

    return render_template("rsi_explorer.html", rsi_tree=rsi_tree, selected=selected)


@rsi_bp.route("/view")
def rsi_view():
    from app import session

    selected = selected_instance_or_400()

    sprite = request.args.get("sprite", "").strip().replace("\\", "/")
    if not sprite:
        abort(400)

    textures_root = Path(selected["root_path"]) / "Resources" / "Textures"
    rsi_dir = safe_join(textures_root, sprite)

    if not rsi_dir or not rsi_dir.exists() or not rsi_dir.is_dir():
        abort(404)

    # Optional: enforce .rsi
    if not str(rsi_dir).endswith(".rsi"):
        abort(400)

    states = list_rsi_states(rsi_dir)

    # Fallback: if no states found, try to infer from files
    if not states:
        states = [p.stem for p in rsi_dir.glob("*.png")]

    # Still nothing? then empty list (don't fake "icon")
    states = states or []

    meta_path = rsi_dir / "meta.json"
    meta = None

    if meta_path.exists():
        try:
            import json
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


@rsi_bp.route("/preview")
def rsi_preview():
    selected = selected_instance_or_400()

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

    # Load meta
    meta = None
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Warning: Failed to parse meta.json for {sprite}: {e}")

    # Find state in meta
    state_meta = None
    if meta and "states" in meta:
        for s in meta["states"]:
            if isinstance(s, dict) and s.get("name") == state_name:
                state_meta = s
                break

    with Image.open(image_path) as im:
        im = im.convert("RGBA")

        # Get state-specific directions and delays
        directions = 1
        if state_meta and "directions" in state_meta:
            directions = state_meta["directions"]

        # No animation → just return scaled image, handling direction if present
        if not state_meta or "delays" not in state_meta:
            # Handle directions without animation
            if directions > 1:
                # Validate direction parameter
                if direction < 0 or direction >= directions:
                    direction = 0

                # Get size from meta.json or calculate from image
                size_x = 32
                size_y = 32
                if meta and "size" in meta:
                    size_x = meta["size"].get("x", 32)
                    size_y = meta["size"].get("y", 32)

                # Calculate frame height based on directions
                frame_height = im.height // directions

                # Extract the direction row
                y1 = direction * frame_height
                y2 = y1 + frame_height

                # Ensure within bounds
                y2 = min(y2, im.height)

                if y2 > y1:
                    frame = im.crop((0, y1, im.width, y2))
                    # Scale to the correct size
                    frame = frame.resize((size_x * scale, size_y * scale), Image.Resampling.NEAREST)
                    buffer = io.BytesIO()
                    frame.save(buffer, format="PNG")
                    buffer.seek(0)
                    return send_file(buffer, mimetype="image/png")

            # No directions or single direction
            out = im.resize((im.width * scale, im.height * scale), Image.Resampling.NEAREST)
            buffer = io.BytesIO()
            out.save(buffer, format="PNG")
            buffer.seek(0)
            return send_file(buffer, mimetype="image/png")

        # Debug: Print meta info
        # print(f"Animation debug for {sprite}:{state_name}")
        # print(f"Image size: {im.width}x{im.height}")
        # print(f"State meta: {state_meta}")

        # --- ANIMATION ---
        delays = state_meta.get("delays", [])
        directions = state_meta.get("directions", 1)

        print(f"Delays structure: {delays}")
        print(f"Directions: {directions}")

        # Handle delays structure - can be 1D or 2D array
        if not delays:
            # No delays, fallback to static
            out = im.resize((im.width * scale, im.height * scale), Image.Resampling.NEAREST)
            buffer = io.BytesIO()
            out.save(buffer, format="PNG")
            buffer.seek(0)
            return send_file(buffer, mimetype="image/png")

        # Validate direction parameter
        if direction < 0 or direction >= directions:
            direction = 0

        # Extract frame delays for the current direction
        if isinstance(delays, list) and len(delays) > 0:
            if isinstance(delays[0], list):
                # 2D array: delays[direction][frame]
                if direction < len(delays):
                    frame_delays = delays[direction]
                else:
                    frame_delays = delays[0]  # fallback to first direction
            else:
                # 1D array: same delays for all directions
                frame_delays = delays
        else:
            frame_delays = [0.1]  # fallback delay

        frame_count = len(frame_delays)
        print(f"Frame count: {frame_count}, Frame delays: {frame_delays}")

        # Calculate frame dimensions - assume frames are horizontal, directions vertical
        if directions > 1:
            frame_width = im.width // frame_count
            frame_height = im.height // directions
        else:
            # Single direction, all frames horizontal
            frame_width = im.width // frame_count
            frame_height = im.height

        print(f"Frame dimensions: {frame_width}x{frame_height}, Image: {im.width}x{im.height}")

        # Ensure we have valid dimensions
        if frame_width <= 0 or frame_height <= 0 or frame_count <= 1:
            # Fallback to static image if dimensions are invalid or only one frame
            out = im.resize((im.width * scale, im.height * scale), Image.Resampling.NEAREST)
            buffer = io.BytesIO()
            out.save(buffer, format="PNG")
            buffer.seek(0)
            return send_file(buffer, mimetype="image/png")

        frames = []

        # Extract frames for the specified direction
        for i in range(frame_count):
            try:
                x1 = i * frame_width
                y1 = direction * frame_height
                x2 = (i + 1) * frame_width
                y2 = (direction + 1) * frame_height

                # Ensure coordinates are within image bounds
                x2 = min(x2, im.width)
                y2 = min(y2, im.height)

                if x2 <= x1 or y2 <= y1:
                    print(f"Warning: Invalid frame coordinates for frame {i}: ({x1},{y1}) to ({x2},{y2})")
                    continue

                frame = im.crop((x1, y1, x2, y2))

                frame = frame.resize(
                    (frame.width * scale, frame.height * scale),
                    Image.Resampling.NEAREST
                )

                frames.append(frame)
                # print(f"Extracted frame {i}: {frame.width}x{frame.height}")
            except Exception as e:
                # If cropping fails, skip this frame
                print(f"Warning: Failed to extract frame {i}: {e}")
                continue

        if len(frames) <= 1:
            # Only one frame or no frames, return static image
            out = im.resize((im.width * scale, im.height * scale), Image.Resampling.NEAREST)
            buffer = io.BytesIO()
            out.save(buffer, format="PNG")
            buffer.seek(0)
            return send_file(buffer, mimetype="image/png")

        # Convert delays to milliseconds (clamp to reasonable range)
        durations = []
        for i, delay in enumerate(frame_delays):
            try:
                # delays are typically in seconds, convert to milliseconds
                duration_ms = max(10, min(2000, int(float(delay) * 1000)))
                durations.append(duration_ms)
                # print(f"Frame {i} delay: {delay}s -> {duration_ms}ms")
            except (ValueError, TypeError, IndexError):
                durations.append(100)  # Default 100ms

        # Ensure we have durations for all frames
        while len(durations) < len(frames):
            durations.append(100)

        try:
            buffer = io.BytesIO()
            frames[0].save(
                buffer,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                duration=durations[:len(frames)],
                loop=0,
                disposal=2
            )
            buffer.seek(0)
            print(f"Successfully created GIF with {len(frames)} frames")
            return send_file(buffer, mimetype="image/gif")
        except Exception as e:
            # If GIF creation fails, return static image
            print(f"Warning: Failed to create GIF animation: {e}")
            out = im.resize((im.width * scale, im.height * scale), Image.Resampling.NEAREST)
            buffer = io.BytesIO()
            out.save(buffer, format="PNG")
            buffer.seek(0)
            return send_file(buffer, mimetype="image/png")


@rsi_bp.route("/api/suggest")
def api_rsi_suggest():
    from app import session

    selected = selected_instance_or_400()
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


@rsi_bp.route("/api/states")
def api_rsi_states():
    from app import session

    selected = selected_instance_or_400()
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
            import json
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            states = [s.get("name") for s in meta.get("states", []) if isinstance(s, dict) and s.get("name")]
        except Exception:
            states = []
    if not states:
        states = [p.stem for p in sorted(rsi_dir.glob("*.png"))]
    return jsonify(sorted(set(states)))


@rsi_bp.route("/api/state-info")
def api_rsi_state_info():
    selected = selected_instance_or_400()
    sprite = request.args.get("sprite", "").strip()
    state = request.args.get("state", "icon").strip()
    if not sprite:
        return jsonify({})
    textures_root = Path(selected["root_path"]) / "Resources" / "Textures"
    rsi_dir = safe_join_or_none(textures_root, sprite)
    if not rsi_dir or not rsi_dir.exists():
        return jsonify({})
    meta_path = rsi_dir / "meta.json"
    result = {"directions": 1, "delays": None, "size": None}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            result["size"] = meta.get("size")
            for s in meta.get("states", []):
                if isinstance(s, dict) and s.get("name") == state:
                    result["directions"] = s.get("directions", 1)
                    result["delays"] = s.get("delays")
                    break
        except Exception:
            pass
    return jsonify(result)


def safe_join_or_none(base: Path, relative: str):
    from app import safe_join as _safe_join
    try:
        return _safe_join(base, relative)
    except Exception:
        return None
