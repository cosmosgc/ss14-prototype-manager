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
        # Clean the string - remove whitespace
        cleaned = "".join(encoded_str.strip().split())
        if not cleaned:
            return [[0]*CHUNK_SIZE for _ in range(CHUNK_SIZE)]
        decoded = base64.b64decode(cleaned)
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
    decals = []
    proto_cache = {}

    for doc_idx, doc in enumerate(docs):
        # Document can be a dict or a list
        items = []
        if isinstance(doc, list):
            items = doc
        elif isinstance(doc, dict):
            items = [doc]
        else:
            continue

        for item in items:
            if not isinstance(item, dict):
                continue

            # Extract tilemap from top-level doc (first doc with tilemap)
            if "tilemap" in item and isinstance(item["tilemap"], dict) and not tilemap:
                tilemap = {int(k): v for k, v in item["tilemap"].items()}

            # Check proto
            proto_name = item.get("proto", None)

            # If proto is empty string, this contains map grid and global components
            if proto_name == "":
                for ent in item.get("entities", []):
                    if not isinstance(ent, dict):
                        continue
                    for comp in ent.get("components", []):
                        if not isinstance(comp, dict):
                            continue
                        comp_type = comp.get("type", "")

                        # MapGrid - extract chunks
                        if comp_type == "MapGrid":
                            chunks = comp.get("chunks", {})
                            for chunk_key, chunk_data in chunks.items():
                                try:
                                    chunk_x, chunk_y = map(int, str(chunk_key).split(","))
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

                        # DecalGrid - extract decals
                        elif comp_type == "DecalGrid":
                            chunk_collection = comp.get("chunkCollection", {})
                            if not chunk_collection:
                                chunk_collection = comp.get("chunkCollection", {})
                            nodes = chunk_collection.get("nodes", [])
                            for node in nodes:
                                if not isinstance(node, dict):
                                    continue
                                decal_id = node.get("id", "")
                                decal_color = node.get("color", "")
                                decal_decals = node.get("decals", {})
                                for tile_key, decal_list in decal_decals.items():
                                    try:
                                        dx, dy = map(int, str(tile_key).split(","))
                                    except:
                                        continue
                                    for decal in (decal_list if isinstance(decal_list, list) else [decal_list]):
                                        decals.append({
                                            "x": dx,
                                            "y": dy,
                                            "id": decal_id,
                                            "color": decal_color,
                                        })

            # If proto is a non-empty string, this is an entity entry
            elif proto_name and isinstance(proto_name, str):
                if proto_name not in proto_cache:
                    with get_db() as conn:
                        row = conn.execute(
                            "SELECT proto_id, type FROM prototype_ids WHERE proto_id = ? LIMIT 1",
                            (proto_name,),
                        ).fetchone()
                        if row:
                            proto_cache[proto_name] = {"id": row["proto_id"], "type": row["type"]}
                        else:
                            proto_cache[proto_name] = {"id": proto_name, "type": "unknown"}

                for ent in item.get("entities", []):
                    if not isinstance(ent, dict):
                        continue
                    uid = ent.get("uid")
                    pos_x, pos_y = 0.0, 0.0
                    ent_name = ""
                    missing_components = ent.get("missingComponents", [])

                    for comp in ent.get("components", []):
                        if not isinstance(comp, dict):
                            continue
                        comp_type = comp.get("type", "")

                        if comp_type == "Transform":
                            pos = comp.get("pos", "0,0")
                            if isinstance(pos, str):
                                try:
                                    parts = pos.split(",")
                                    pos_x, pos_y = float(parts[0]), float(parts[1])
                                except:
                                    pass
                            elif isinstance(pos, dict):
                                pos_x = float(comp.get("x", comp.get("pos", {}).get("x", 0.0)))
                                pos_y = float(comp.get("y", comp.get("pos", {}).get("y", 0.0)))
                            break

                        elif comp_type == "MetaData":
                            ent_name = comp.get("name", "")

                    entities.append({
                        "uid": uid,
                        "proto": proto_name,
                        "name": ent_name,
                        "x": pos_x,
                        "y": pos_y,
                        "proto_type": proto_cache[proto_name]["type"],
                        "missing_components": missing_components,
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
        decals=decals,
    )
