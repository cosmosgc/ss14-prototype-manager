from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from pathlib import Path
import base64
import zlib
import struct

from app import (
    selected_instance_or_400, safe_join, list_prototype_files, build_file_entries,
    build_tree, load_yaml_documents, validate_yaml_text, get_db,
)

CHUNK_SIZE = 16  # SS14 map chunks are 16x16 tiles

def decode_tile_data(encoded_str):
    """Decode SS14's base64+zlib compressed tile data into a 16x16 grid of tile IDs."""
    try:
        decoded = base64.b64decode(encoded_str)
        decompressed = zlib.decompress(decoded)
        grid = []
        for y in range(CHUNK_SIZE):
            row = []
            for x in range(CHUNK_SIZE):
                offset = (y * CHUNK_SIZE + x) * 2
                if offset + 2 > len(decompressed):
                    row.append(0)
                else:
                    tile_id = struct.unpack('<H', decompressed[offset:offset+2])[0]
                    row.append(tile_id)
            grid.append(row)
        return grid
    except Exception as e:
        print(f"Tile decode error: {e}")
        return [[0]*CHUNK_SIZE for _ in range(CHUNK_SIZE)]

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

    # Process map data
    tilemap = {}
    grid_chunks = []
    entities = []
    proto_cache = {}

    for doc in docs:
        # Extract tilemap
        if "tilemap" in doc and isinstance(doc["tilemap"], dict):
            tilemap = {int(k): v for k, v in doc["tilemap"].items()}

        # Extract grid chunks (from empty proto entity)
        if doc.get("proto") == "":
            for ent in doc.get("entities", []):
                for comp in ent.get("components", []):
                    if comp.get("type") == "MapGrid":
                        chunks = comp.get("chunks", {})
                        for chunk_key, chunk_data in chunks.items():
                            try:
                                chunk_x, chunk_y = map(int, chunk_key.split(","))
                            except:
                                continue
                            encoded_tiles = chunk_data.get("tiles", "")
                            decoded_tiles = decode_tile_data(encoded_tiles)
                            grid_chunks.append({
                                "chunk_x": chunk_x,
                                "chunk_y": chunk_y,
                                "tiles": decoded_tiles,
                                "version": chunk_data.get("version", 0),
                            })

        # Extract entities with prototype info from DB
        proto_name = doc.get("proto")
        if proto_name and proto_name != "":
            if proto_name not in proto_cache:
                with get_db() as conn:
                    row = conn.execute(
                        "SELECT proto_id, type FROM prototype_ids WHERE proto_id = ? LIMIT 1",
                        (proto_name,),
                    ).fetchone()
                    proto_cache[proto_name] = (
                        {"id": row["proto_id"], "type": row["type"]}
                        if row
                        else {"id": proto_name, "type": "unknown"}
                    )

            for ent in doc.get("entities", []):
                pos_x, pos_y = 0.0, 0.0
                for comp in ent.get("components", []):
                    if comp.get("type") == "Transform":
                        pos = comp.get("pos", "0,0")
                        if isinstance(pos, str):
                            try:
                                pos_x, pos_y = map(float, pos.split(","))
                            except:
                                pass
                        elif isinstance(pos, dict):
                            pos_x = pos.get("x", 0.0)
                            pos_y = pos.get("y", 0.0)
                        break

                entities.append({
                    "uid": ent.get("uid"),
                    "proto": proto_name,
                    "x": pos_x,
                    "y": pos_y,
                    "proto_type": proto_cache[proto_name]["type"],
                })

    return render_template(
        "map_view.html",
        rel_file=rel_file,
        docs=docs,
        raw_text=raw_text,
        parse_ok=not parse_error,
        parse_error=parse_error,
        tilemap=tilemap,
        grid_chunks=grid_chunks,
        entities=entities,
    )
