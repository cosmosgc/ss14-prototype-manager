from flask import Blueprint, abort, flash, redirect, render_template, request, url_for, send_file, jsonify
from pathlib import Path
import base64
import struct
import json
import hashlib
import sqlite3
from PIL import Image, ImageDraw
import yaml

from app import (
    selected_instance_or_400, safe_join, list_prototype_files, build_file_entries,
    build_tree, validate_yaml_text, get_db, load_yaml_documents,
    find_first_sprite_in_text, list_rsi_states, IgnoreUnknownTagLoader,
)

CHUNK_SIZE = 16
TILE_SIZE_PX = 32
TILE_DATA_SIZE = 7  # SS14 format 6/7: 7 bytes per tile

# Tile colors for rendering (hardcoded for now, later fetch from RSI)
TILE_COLORS = {
    "Space": (0, 0, 0, 255),
    "FloorAstroGrass": (34, 139, 34, 255),
    "FloorAstroSnow": (255, 250, 250, 255),
    "FloorBlueCircuit": (0, 0, 255, 255),
    "FloorDark": (64, 64, 64, 255),
    "FloorDarkMini": (72, 72, 72, 255),
    "FloorDarkMono": (80, 80, 80, 255),
    "FloorDarkOffset": (70, 70, 70, 255),
    "FloorDirt": (139, 69, 19, 255),
    "FloorFreezer": (173, 216, 230, 255),
    "FloorGrass": (50, 205, 50, 255),
    "FloorGrassDark": (40, 100, 40, 255),
    "FloorGrassJungle": (34, 139, 34, 255),
    "FloorGrassLight": (144, 238, 144, 255),
    "FloorHydro": (107, 142, 35, 255),
    "FloorJungleAstroGrass": (34, 139, 34, 255),
    "FloorKitchen": (255, 228, 196, 255),
    "FloorMowedAstroGrass": (34, 139, 34, 255),
    "FloorPlanetDirt": (139, 69, 19, 255),
    "FloorPlanetGrass": (50, 205, 50, 255),
    "FloorReinforced": (100, 100, 100, 255),
    "FloorSnow": (255, 250, 250, 255),
    "FloorSteel": (192, 192, 192, 255),
    "FloorSteelMini": (200, 200, 200, 255),
    "FloorSteelMono": (208, 208, 208, 255),
    "FloorTechMaint2": (128, 128, 128, 255),
    "FloorWhite": (255, 255, 255, 255),
    "FloorWhiteDiagonal": (245, 245, 245, 255),
    "FloorWhiteDiagonalMini": (250, 250, 250, 255),
    "FloorWhiteMini": (255, 255, 255, 255),
    "FloorWhiteMono": (240, 240, 240, 255),
    "FloorWood": (165, 42, 42, 255),
    "FloorWoodLarge": (160, 40, 40, 255),
    "FloorWoodTile": (170, 44, 44, 255),
    "Lattice": (150, 150, 150, 255),
    "Plating": (120, 120, 120, 255),
    "PlatingAsteroid": (100, 100, 100, 255),
}

# Custom YAML loader to ignore SS14 tags
class IgnoreTagsLoader(yaml.SafeLoader):
    pass

def ignore_unknown(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None

IgnoreTagsLoader.add_multi_constructor('!', ignore_unknown)


def get_tile_sprite_info(instance_name: str, tile_name: str) -> tuple[str, str] | None:
    """Look up sprite info for a tile by ID - scans tile definition files"""
    if not tile_name or tile_name == "Space":
        return None
    
    instance_root = None
    with get_db() as conn:
        inst_row = conn.execute(
            "SELECT root_path FROM instances WHERE name = ?", (instance_name,)
        ).fetchone()
        if inst_row:
            instance_root = Path(inst_row["root_path"])
    
    if not instance_root:
        return None
    
    # Scan tile files directly for this tile
    tile_files = [
        "Tiles/floors.yml",
        "Tiles/plating.yml",
        "_DV/Tiles/floors.yml", 
        "_DV/Tiles/plating.yml",
        "_Nuclear14/Tiles/floors.yml",
        "_DV/CosmicCult/Tileset/floors.yml",
    ]
    
    proto_root = instance_root / "Resources" / "Prototypes"
    for tile_file in tile_files:
        proto_path = proto_root / tile_file
        if not proto_path.exists():
            continue
        try:
            docs = load_yaml_documents(proto_path)
            # Each doc is a list of tile definitions
            all_tiles = docs[0] if docs else []
            for tile in all_tiles:
                if isinstance(tile, dict) and tile.get("id") == tile_name:
                    sprite = tile.get("sprite")
                    if sprite and isinstance(sprite, str):
                        sprite = sprite.strip()
                        if sprite.startswith("/"):
                            sprite = sprite[1:]
                        # Strip leading "Textures/" or "textures/"
                        if sprite.lower().startswith("textures/"):
                            sprite = sprite[9:]
                        if sprite.endswith(".png"):
                            sprite = sprite[:-4]
                        elif sprite.endswith(".rsi"):
                            sprite = sprite[:-4]
                        if sprite:
                            return sprite, "icon"
        except Exception:
            continue
    
    return None


def extract_tile_texture(textures_root: Path, sprite: str, state: str) -> Image.Image | None:
    """Load a tile texture image from RSI or direct PNG"""
    # Check for direct PNG first
    direct_png = textures_root / f"{sprite}.png"
    if direct_png.exists():
        try:
            with Image.open(direct_png) as im:
                return im.convert("RGBA")
        except Exception:
            pass
    
    # Try RSI folder
    rsi_dir = safe_join(textures_root, sprite)
    if not rsi_dir or not rsi_dir.exists():
        return None
    
    # Check for meta.json states or fall back to PNG files
    available_states = list_rsi_states(rsi_dir)
    actual_state = state if state in available_states else "icon"
    if actual_state not in available_states:
        actual_state = available_states[0] if available_states else None
    
    if not actual_state:
        return None
    
    png_path = rsi_dir / f"{actual_state}.png"
    if not png_path.exists():
        return None
    
    try:
        with Image.open(png_path) as im:
            return im.convert("RGBA")
    except Exception:
        return None

map_bp = Blueprint("map", __name__, url_prefix="/maps")


def get_map_cache_key(instance_name, rel_file):
    """Generate cache key based on instance + file path"""
    return hashlib.md5(f"{instance_name}:{rel_file}".encode()).hexdigest()


def decode_tile_data(encoded_str):
    """Decode SS14's base64 tile data (7 bytes per tile for format 6/7)"""
    try:
        cleaned = "".join(encoded_str.strip().split())
        if not cleaned:
            return [[0]*CHUNK_SIZE for _ in range(CHUNK_SIZE)]
        
        decoded = base64.b64decode(cleaned)
        
        grid = []
        for y in range(CHUNK_SIZE):
            row = []
            for x in range(CHUNK_SIZE):
                offset = (y * CHUNK_SIZE + x) * TILE_DATA_SIZE
                if offset + 2 > len(decoded):
                    row.append(0)
                else:
                    tile_id = struct.unpack('<H', decoded[offset:offset+2])[0]
                    row.append(tile_id)
            grid.append(row)
        return grid
    except Exception as e:
        print(f"Tile decode error: {e}")
        return [[0]*CHUNK_SIZE for _ in range(CHUNK_SIZE)]


def render_chunk_png(tiles, tilemap, instance_root: Path, instance_name: str, output_path):
    """Render a single chunk as PNG using actual tile textures"""
    img = Image.new('RGBA', (CHUNK_SIZE * TILE_SIZE_PX, CHUNK_SIZE * TILE_SIZE_PX))
    draw = ImageDraw.Draw(img)
    
    textures_root = instance_root / "Resources" / "Textures"
    sprite_cache: dict[str, Image.Image] = {}
    
    for y in range(CHUNK_SIZE):
        for x in range(CHUNK_SIZE):
            tile_id = tiles[y][x]
            tile_name = tilemap.get(tile_id, "Space")
            
            if tile_name == "Space":
                color = (0, 0, 0, 255)
                draw.rectangle([x * TILE_SIZE_PX, y * TILE_SIZE_PX, (x + 1) * TILE_SIZE_PX - 1, (y + 1) * TILE_SIZE_PX - 1], fill=color)
                continue
            
            sprite_info = get_tile_sprite_info(instance_name, tile_name)
            
            if sprite_info:
                sprite, state = sprite_info
                cache_key = f"{sprite}:{state}"
                if cache_key not in sprite_cache:
                    tex = extract_tile_texture(textures_root, sprite, state)
                    if tex:
                        sprite_cache[cache_key] = tex
                
                tile_img = sprite_cache.get(cache_key)
                if tile_img:
                    img.paste(tile_img, (x * TILE_SIZE_PX, y * TILE_SIZE_PX), tile_img)
                    continue
            
            color = TILE_COLORS.get(tile_name, (255, 0, 255, 255))
            draw.rectangle([x * TILE_SIZE_PX, y * TILE_SIZE_PX, (x + 1) * TILE_SIZE_PX - 1, (y + 1) * TILE_SIZE_PX - 1], fill=color)
    
    img.save(output_path)
    return output_path


def render_full_map_png(tilemap, grid_chunks, instance_root: Path, instance_name: str, output_path, scale=4):
    """Render the entire map as a single PNG image (flipped Y for OpenLayers)"""
    if not grid_chunks:
        print("DEBUG: No chunks to render")
        return None
    
    # Find map bounds
    min_cx = min(c["x"] for c in grid_chunks)
    max_cx = max(c["x"] for c in grid_chunks)
    min_cy = min(c["y"] for c in grid_chunks)
    max_cy = max(c["y"] for c in grid_chunks)
    
    # Create chunk lookup
    chunk_lookup = {(c["x"], c["y"]): c for c in grid_chunks}
    
    # Calculate image dimensions
    map_width = (max_cx - min_cx + 1) * CHUNK_SIZE
    map_height = (max_cy - min_cy + 1) * CHUNK_SIZE
    
    img = Image.new('RGBA', (map_width * scale, map_height * scale))
    draw = ImageDraw.Draw(img)
    
    textures_root = instance_root / "Resources" / "Textures"
    sprite_cache: dict[str, Image.Image] = {}
    
    for (cx, cy), chunk in chunk_lookup.items():
        tiles = chunk["tiles"]
        # Flip Y: SS14 Y-up -> OpenLayers Y-down
        offset_x = (cx - min_cx) * CHUNK_SIZE
        offset_y = (max_cy - cy) * CHUNK_SIZE
        
        for y in range(CHUNK_SIZE):
            for x in range(CHUNK_SIZE):
                tile_id = tiles[y][x]
                tile_name = tilemap.get(tile_id, "Space")
                
                if tile_name == "Space":
                    color = (0, 0, 0, 255)
                    px = (offset_x + x) * scale
                    py = (offset_y + y) * scale
                    draw.rectangle([px, py, px + scale - 1, py + scale - 1], fill=color)
                    continue
                
                sprite_info = get_tile_sprite_info(instance_name, tile_name)
                
                if sprite_info:
                    sprite, state = sprite_info
                    cache_key = f"{sprite}:{state}"
                    if cache_key not in sprite_cache:
                        tex = extract_tile_texture(textures_root, sprite, state)
                        if tex:
                            sprite_cache[cache_key] = tex
                    
                    tile_img = sprite_cache.get(cache_key)
                    if tile_img:
                        px = (offset_x + x) * scale
                        py = (offset_y + y) * scale
                        # Scale down the texture to fit
                        if scale == 1:
                            img.paste(tile_img, (px, py), tile_img)
                        else:
                            small = tile_img.resize((scale, scale), Image.Resampling.NEAREST)
                            img.paste(small, (px, py), small)
                        continue
                
                color = TILE_COLORS.get(tile_name, (255, 0, 255, 255))
                px = (offset_x + x) * scale
                py = (offset_y + y) * scale
                draw.rectangle([px, py, px + scale - 1, py + scale - 1], fill=color)
    
    img.save(output_path)
    print(f"DEBUG: Saved full map preview (flipped): {output_path}")
    return output_path


def load_map_yaml(file_path):
    """Load SS14 map YAML file using custom loader"""
    with open(file_path, "r", encoding="utf-8") as f:
        doc = yaml.load(f, Loader=IgnoreTagsLoader)
    return doc


def parse_map_data(doc):
    """Parse map data from YAML document - matches test.py logic"""
    tilemap = {}
    grid_chunks = []
    entities = []
    proto_cache = {}
    
    if not isinstance(doc, dict):
        return tilemap, grid_chunks, entities
    
    # Parse tilemap
    if "tilemap" in doc:
        tilemap = {int(k): v for k, v in doc["tilemap"].items()}
    
    # Parse entities/grids
    for group in doc.get("entities", []):
        if not isinstance(group, dict):
            continue
        
        proto_name = group.get("proto")
        
        if proto_name == "":
            # Root grid entity
            for ent in group.get("entities", []):
                for comp in ent.get("components", []):
                    if comp.get("type") == "MapGrid":
                        chunks = comp.get("chunks", {})
                        for chunk_key, chunk_data in chunks.items():
                            try:
                                cx, cy = map(int, chunk_key.split(","))
                            except:
                                continue
                            tiles = decode_tile_data(chunk_data.get("tiles", ""))
                            grid_chunks.append({
                                "x": cx,
                                "y": cy,
                                "tiles": tiles
                            })
        
        elif proto_name:
            # Regular entities
            for ent in group.get("entities", []):
                pos_x, pos_y = 0.0, 0.0
                name = ""
                
                for comp in ent.get("components", []):
                    if comp.get("type") == "Transform":
                        try:
                            x, y = comp.get("pos", "0,0").split(",")
                            pos_x = float(x)
                            pos_y = float(y)
                        except:
                            pass
                    if comp.get("type") == "MetaData":
                        name = comp.get("name", "")
                
                entities.append({
                    "proto": proto_name,
                    "x": pos_x,
                    "y": pos_y,
                    "name": name
                })
    
    return tilemap, grid_chunks, entities


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
    _, parse_error = validate_yaml_text(raw_text)
    
    # Load and parse map data using test.py approach
    doc = load_map_yaml(file_path)
    tilemap, grid_chunks, entities = parse_map_data(doc)
    
    print(f"DEBUG: Tilemap entries: {len(tilemap)}")
    print(f"DEBUG: Grid chunks: {len(grid_chunks)}")
    print(f"DEBUG: Entities: {len(entities)}")
    
    # Generate cache key and tile images
    cache_key = get_map_cache_key(selected["name"], rel_file)
    tile_cache_dir = Path("static") / "map_cache" / cache_key
    tiles_dir = tile_cache_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    
    # Check if we need to regenerate tiles
    meta_path = tile_cache_dir / "meta.json"
    regenerate = False
    
    if meta_path.exists():
        with open(meta_path, "r") as f:
            cached_meta = json.load(f)
        if cached_meta.get("file_mtime") != file_path.stat().st_mtime:
            regenerate = True
            print("DEBUG: File modified, regenerating tiles")
        else:
            print("DEBUG: Using cached tiles (no changes)")
    else:
        regenerate = True
        print("DEBUG: No cache found, generating tiles")
    
    print(f"DEBUG: regenerate flag = {regenerate}")
    
    if regenerate:
        print(f"DEBUG: Generating {len(grid_chunks)} tile images...")
        tiles_dir = tile_cache_dir / "tiles"
        tiles_dir.mkdir(parents=True, exist_ok=True)
        
        instance_root = Path(selected["root_path"])
        instance_name = selected["name"]
        
        for chunk in grid_chunks:
            cx = chunk["x"]
            cy = chunk["y"]
            chunk_file = tiles_dir / f"chunk_{cx}_{cy}.png"
            print(f"DEBUG: Rendering chunk ({cx}, {cy}) to {chunk_file}")
            render_chunk_png(chunk["tiles"], tilemap, instance_root, instance_name, chunk_file)
        
        # Generate full map preview
        print("DEBUG: Generating full map preview...")
        preview_path = tile_cache_dir / "preview.png"
        render_full_map_png(tilemap, grid_chunks, instance_root, instance_name, preview_path)
        
        # Save metadata
        with open(meta_path, "w") as f:
            json.dump({
                "file_mtime": file_path.stat().st_mtime,
                "chunks": [{"x": c["x"], "y": c["y"]} for c in grid_chunks]
            }, f)
        print("DEBUG: Tile generation complete")
    else:
        print("DEBUG: Skipping tile generation (cached)")
    
    return render_template(
        "map_view.html",
        rel_file=rel_file,
        raw_text=raw_text,
        parse_ok=not parse_error,
        parse_error=parse_error,
        tilemap=tilemap,
        grid_chunks=grid_chunks,
        entities=entities,
        cache_key=cache_key,
    )


@map_bp.route("/api/tiles/<cache_key>/<path:filename>")
def get_tile(cache_key, filename):
    """Serve cached tile images"""
    tile_path = Path("static") / "map_cache" / cache_key / "tiles" / filename
    print(f"DEBUG: Serving tile: {tile_path} - Exists: {tile_path.exists()}")
    if not tile_path.exists():
        abort(404)
    return send_file(tile_path, mimetype='image/png')


@map_bp.route("/api/map-bounds/<cache_key>")
def map_bounds(cache_key):
    """Return map bounds"""
    meta_path = Path("static") / "map_cache" / cache_key / "meta.json"
    if not meta_path.exists():
        return jsonify({"error": "No cached data"}), 404
    
    with open(meta_path, "r") as f:
        meta = json.load(f)
    
    chunks = meta.get("chunks", [])
    if not chunks:
        return jsonify({"error": "No chunks"}), 404
    
    min_x = min(c["x"] for c in chunks) * CHUNK_SIZE
    min_y = min(c["y"] for c in chunks) * CHUNK_SIZE
    max_x = (max(c["x"] for c in chunks) * CHUNK_SIZE) + CHUNK_SIZE
    max_y = (max(c["y"] for c in chunks) * CHUNK_SIZE) + CHUNK_SIZE
    
    return jsonify({
        "minX": min_x,
        "minY": min_y,
        "maxX": max_x,
        "maxY": max_y,
    })


@map_bp.route("/api/preview")
def map_preview():
    """Serve full map preview image"""
    cache_key = request.args.get("cache", "").strip()
    if not cache_key:
        abort(404)
    preview_path = Path("static") / "map_cache" / cache_key / "preview.png"
    if not preview_path.exists():
        abort(404)
    return send_file(preview_path, mimetype='image/png')
