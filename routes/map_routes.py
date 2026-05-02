from flask import Blueprint, abort, flash, redirect, render_template, request, url_for, send_file, jsonify
from pathlib import Path
import base64
import struct
import json
import hashlib
import sqlite3
from PIL import Image, ImageDraw, ImageFont
import yaml
import math

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
    if state in available_states:
        actual_state = state
    elif "icon" in available_states:
        actual_state = "icon"
    elif available_states:
        actual_state = available_states[0]
    else:
        return None
    print(f"Entity {proto} → px={px}, py={py}")
    
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


def get_entity_type(proto: str) -> str:
    """Extract entity type group from prototype name"""
    proto_lower = proto.lower()
    if any(x in proto_lower for x in ['wall', 'grille']):
        return 'walls'
    if any(x in proto_lower for x in ['door', 'airlock']):
        return 'doors'
    if any(x in proto_lower for x in ['cable', 'wire']):
        return 'cables'
    if any(x in proto_lower for x in ['apc', 'power', 'smes']):
        return 'power'
    if any(x in proto_lower for x in ['thruster', 'engine']):
        return 'thrusters'
    if any(x in proto_lower for x in ['seat', 'chair', 'bed']):
        return 'furniture'
    if any(x in proto_lower for x in ['light', 'lamp']):
        return 'lights'
    if any(x in proto_lower for x in ['med', 'chem', 'pill']):
        return 'medical'
    if any(x in proto_lower for x in ['weapon', 'gun', 'laser']):
        return 'weapons'
    if any(x in proto_lower for x in ['tank', 'canister']):
        return 'gas'
    return 'other'


ENTITY_COLORS = {
    'walls': (100, 100, 100, 200),
    'doors': (0, 150, 200, 200),
    'cables': (255, 200, 0, 200),
    'power': (200, 200, 0, 200),
    'thrusters': (255, 100, 0, 200),
    'furniture': (150, 100, 50, 200),
    'lights': (255, 255, 100, 200),
    'medical': (255, 100, 100, 200),
    'weapons': (255, 50, 50, 200),
    'gas': (100, 200, 255, 200),
    'other': (150, 150, 150, 200),
}


def get_entity_icon(instance_name: str, proto: str, icon_size: int = 32) -> Image.Image | None:
    """Get or create cached entity icon"""
    # Check cache first
    cache_dir = Path("static") / "entity_cache" / instance_name
    cache_dir.mkdir(parents=True, exist_ok=True)
    icon_path = cache_dir / f"{proto}.png"
    
    if icon_path.exists():
        try:
            with Image.open(icon_path) as im:
                return im.convert("RGBA")
        except Exception:
            pass
    
    # Try to get sprite from prototype
    with get_db() as conn:
        row = conn.execute(
            "SELECT rel_path FROM prototype_ids WHERE proto_id = ? AND instance_name = ? LIMIT 1",
            (proto, instance_name)
        ).fetchone()
    
    if not row:
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
    
    proto_root = instance_root / "Resources" / "Prototypes"
    proto_path = safe_join(proto_root, row["rel_path"])
    
    if not proto_path or not proto_path.exists():
        return None
    
    try:
        docs = load_yaml_documents(proto_path)
    except Exception:
        return None
    
    sprite = None
    state = None

    for doc in docs:
        if isinstance(doc, dict) and doc.get("id") == proto:
            components = doc.get("components", [])

            if isinstance(components, list):
                for comp in components:
                    if not isinstance(comp, dict):
                        continue

                    if comp.get("type") == "Icon":
                        sprite = comp.get("sprite")
                        state = comp.get("state")
                        break

                    if comp.get("type") == "Sprite":
                        sprite = comp.get("sprite")

                        # Try direct state
                        if isinstance(comp.get("state"), str):
                            state = comp["state"]
                            break

                        # Try layers
                        layers = comp.get("layers")
                        if isinstance(layers, list) and layers:
                            layer = layers[0]
                            if isinstance(layer, dict):
                                state = layer.get("state")
                            break

            # fallback to root
            if not sprite:
                sprite = doc.get("sprite")
                state = doc.get("state")

            break
    
    if not sprite or not isinstance(sprite, str):
        return None
    
    sprite = sprite.strip()
    if sprite.startswith("/"):
        sprite = sprite[1:]
    if sprite.endswith(".png"):
        sprite = sprite[:-4]
    elif sprite.endswith(".rsi"):
        sprite = sprite[:-4]
    
    textures_root = instance_root / "Resources" / "Textures"
    # Robust state fallback
    rsi_dir = safe_join(textures_root, sprite)
    available_states = list_rsi_states(rsi_dir) if rsi_dir else []

    if state in available_states:
        chosen_state = state
    elif "icon" in available_states:
        chosen_state = "icon"
    elif available_states:
        chosen_state = available_states[0]
    else:
        chosen_state = None

    if not chosen_state:
        return None
    print(f"{proto} → sprite={sprite}, state={state}, available={available_states}")

    tex = extract_tile_texture(textures_root, sprite, chosen_state)
    
    if not tex:
        return None
    
    # Resize to icon size
    if tex.size != (icon_size, icon_size):
        tex = tex.resize((icon_size, icon_size), Image.Resampling.NEAREST)
    
    # Save to cache
    try:
        tex.save(icon_path)
    except Exception:
        pass
    
    return tex


def render_entity_layer(entities: list, instance_name: str, 
                       min_cx: int, min_cy: int, 
                       x_range: int, y_range: int,
                       output_path: Path, scale: int = 64) -> Path | None:
    """Render entities as a separate PNG layer"""
    if not entities:
        return None
    
    # Image dimensions
    map_width = (x_range + 1) * CHUNK_SIZE
    map_height = (y_range + 1) * CHUNK_SIZE
    
    img = Image.new('RGBA', (map_width * scale, map_height * scale))
    
    # Cache for entity icons
    icon_cache = {}
    
    
    for ent in entities:
        x = ent.get("x", 0)
        y = ent.get("y", 0)
        proto = ent.get("proto", "")
        
        # SS14 has Y-up, images have Y-down
        # Step 1: Get chunk indices (SS14 space)
        
                
        chunk_x = math.floor(x / CHUNK_SIZE)
        chunk_y = math.floor(y / CHUNK_SIZE)

        local_x = int(x) % CHUNK_SIZE
        local_y = int(y) % CHUNK_SIZE

        cx_index = chunk_x - min_cx
        cy_index = chunk_y - min_cy
        cy_flipped = y_range - cy_index

        offset_x = cx_index * CHUNK_SIZE
        offset_y = cy_flipped * CHUNK_SIZE

        px = (offset_x + local_x) * scale
        py = (offset_y + (CHUNK_SIZE - 1 - local_y)) * scale
        # Get entity icon
        if proto not in icon_cache:
            
            print(f"getting Entity icon {proto} → px={px}, py={py}")
            icon = get_entity_icon(instance_name, proto, scale)
            if icon:
                icon_cache[proto] = icon
                print(f"Cached icon for {proto}")
        
        icon = icon_cache.get(proto)
        if icon:
            img.paste(icon, (int(px), int(py)), icon)
        else:
            # Fallback: colored circle
            ent_type = get_entity_type(proto)
            color = ENTITY_COLORS.get(ent_type, (255, 0, 255, 200))
            draw = ImageDraw.Draw(img)
            r = scale // 2
            draw.ellipse([px - r, py - r, px + r, py + r], fill=color)
    print(f"DEBUG: Rendered {len(entities)} entities on layer")
    img.save(output_path)
    return output_path


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


def render_chunk_png(tiles, tilemap, texture_cache: dict, output_path):
    """Render a single chunk as PNG using pre-cached textures"""
    img = Image.new('RGBA', (CHUNK_SIZE * TILE_SIZE_PX, CHUNK_SIZE * TILE_SIZE_PX))
    draw = ImageDraw.Draw(img)
    
    for y in range(CHUNK_SIZE):
        for x in range(CHUNK_SIZE):
            tile_id = tiles[y][x]
            tile_name = tilemap.get(tile_id, "Space")
            
            x0 = x * TILE_SIZE_PX
            y0 = y * TILE_SIZE_PX
            
            texture = texture_cache.get(tile_name)
            if texture:
                img.paste(texture, (x0, y0), texture)
            else:
                color = TILE_COLORS.get(tile_name, (255, 0, 255, 255))
                draw.rectangle([x0, y0, x0 + TILE_SIZE_PX - 1, y0 + TILE_SIZE_PX - 1], fill=color)
    
    img.save(output_path)
    return output_path

def render_full_map_png(tilemap, grid_chunks, texture_cache: dict, output_path, scale=64):
    """Render the entire map as a single PNG image (flipped Y for OpenLayers)"""

    if not grid_chunks:
        print("DEBUG: No chunks to render")
        return None

    # ---- Bounds ----
    min_cx = min(c["x"] for c in grid_chunks)
    max_cx = max(c["x"] for c in grid_chunks)
    min_cy = min(c["y"] for c in grid_chunks)
    max_cy = max(c["y"] for c in grid_chunks)

    # Normalize range
    x_range = max_cx - min_cx
    y_range = max_cy - min_cy

    # ---- Image size ----
    map_width = (x_range + 1) * CHUNK_SIZE
    map_height = (y_range + 1) * CHUNK_SIZE

    img = Image.new('RGBA', (map_width * scale, map_height * scale))
    draw = ImageDraw.Draw(img)

    # ---- Pre-scale textures ONCE ----
    scaled_texture_cache = {}

    if scale == 1:
        scaled_texture_cache = texture_cache
    else:
        for tile_name, tex in texture_cache.items():
            scaled_texture_cache[tile_name] = tex.resize(
                (scale, scale),
                Image.Resampling.NEAREST
            )

    # ---- Pre-create fallback tiles (faster than draw each time) ----
    fallback_cache = {}

    def get_fallback(tile_name):
        if tile_name not in fallback_cache:
            color = TILE_COLORS.get(tile_name, (255, 0, 255, 255))
            fallback = Image.new("RGBA", (scale, scale), color)
            fallback_cache[tile_name] = fallback
        return fallback_cache[tile_name]

    # ---- Render ----
    for chunk in grid_chunks:
        cx = chunk["x"]
        cy = chunk["y"]
        tiles = chunk["tiles"]

        # Normalize → flip (CORRECT ORDER)
        cx_index = cx - min_cx
        cy_index = cy - min_cy
        cy_flipped = y_range - cy_index

        offset_x = cx_index * CHUNK_SIZE
        offset_y = cy_flipped * CHUNK_SIZE

        for y in range(CHUNK_SIZE):
            for x in range(CHUNK_SIZE):
                tile_id = tiles[y][x]
                tile_name = tilemap.get(tile_id, "Space")

                px = (offset_x + x) * scale
                # py = (offset_y + (y)) * scale
                py = (offset_y + (CHUNK_SIZE - 1 - y)) * scale

                # Fast path for space
                if tile_name == "Space":
                    continue

                texture = scaled_texture_cache.get(tile_name)

                if texture:
                    img.paste(texture, (px, py), texture)
                else:
                    fallback = get_fallback(tile_name)
                    img.paste(fallback, (px, py))

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
    
    # Build entity types (always)
    entity_types = {}
    for ent in entities:
        proto = ent.get("proto", "")
        ent_type = get_entity_type(proto)
        entity_types[proto] = ent_type
    
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
        
        # Step 1: Build tile -> sprite mapping ONCE for all unique tiles
        tile_sprite_map = {}
        unique_tile_names = set(tilemap.values())
        print(f"DEBUG: Resolving sprites for {len(unique_tile_names)} unique tiles...")
        
        for tile_name in unique_tile_names:
            if tile_name and tile_name != "Space":
                tile_sprite_map[tile_name] = get_tile_sprite_info(instance_name, tile_name)
        
        # Step 2: Preload all textures ONCE
        textures_root = instance_root / "Resources" / "Textures"
        texture_cache = {}
        print(f"DEBUG: Loading textures...")
        
        for tile_name, sprite_info in tile_sprite_map.items():
            if not sprite_info:
                continue
            sprite, state = sprite_info
            tex = extract_tile_texture(textures_root, sprite, state)
            if tex:
                if tex.size != (TILE_SIZE_PX, TILE_SIZE_PX):
                    tex = tex.resize((TILE_SIZE_PX, TILE_SIZE_PX), Image.Resampling.NEAREST)
                texture_cache[tile_name] = tex
        
        print(f"DEBUG: Loaded {len(texture_cache)} textures, rendering chunks...")
        
        # Step 3: Render chunks using texture cache
        for chunk in grid_chunks:
            cx = chunk["x"]
            cy = chunk["y"]
            chunk_file = tiles_dir / f"chunk_{cx}_{cy}.png"
            render_chunk_png(chunk["tiles"], tilemap, texture_cache, chunk_file)
        
        # Generate full map preview
        print("DEBUG: Generating full map preview...")
        preview_path = tile_cache_dir / "preview.png"
        render_full_map_png(tilemap, grid_chunks, texture_cache, preview_path)
        
        # Generate entity layer
        if entities:
            print("DEBUG: Generating entity layer...")
            min_cx = min(c["x"] for c in grid_chunks)
            max_cx = max(c["x"] for c in grid_chunks)
            min_cy = min(c["y"] for c in grid_chunks)
            max_cy = max(c["y"] for c in grid_chunks)
            x_range = max_cx - min_cx
            y_range = max_cy - min_cy
            
            entity_layer_path = tile_cache_dir / "entities.png"
            render_entity_layer(entities, selected["name"], min_cx, min_cy, x_range, y_range, entity_layer_path, scale=64)
        
        # Pre-cache entity icons
        print("DEBUG: Pre-caching entity icons...")
        unique_protos = set()
        for ent in entities:
            proto = ent.get("proto", "")
            print(f"DEBUG: Found entity proto: {proto}")
            if proto:
                unique_protos.add(proto)
        for proto in unique_protos:
            get_entity_icon(instance_name, proto, 32)
        
        # Save metadata
        with open(meta_path, "w") as f:
            json.dump({
                "file_mtime": file_path.stat().st_mtime,
                "chunks": [{"x": c["x"], "y": c["y"]} for c in grid_chunks]
            }, f)
        print("DEBUG: Tile generation complete")
    else:
        print("DEBUG: Skipping tile generation (cached)")
        # Still ensure entity layer exists
        if entities and not (tile_cache_dir / "entities.png").exists():
            print("DEBUG: Generating missing entity layer...")
            min_cx = min(c["x"] for c in grid_chunks)
            max_cx = max(c["x"] for c in grid_chunks)
            min_cy = min(c["y"] for c in grid_chunks)
            max_cy = max(c["y"] for c in grid_chunks)
            x_range = max_cx - min_cx
            y_range = max_cy - min_cy
            entity_layer_path = tile_cache_dir / "entities.png"
            render_entity_layer(entities, selected["name"], min_cx, min_cy, x_range, y_range, entity_layer_path, scale=64)
        
        # Pre-cache entity icons if needed
        unique_protos = set()
        for ent in entities:
            proto = ent.get("proto", "")
            if proto:
                unique_protos.add(proto)
        for proto in unique_protos:
            icon_path = Path("static") / "entity_cache" / selected["name"] / f"{proto}.png"
            if not icon_path.exists():
                get_entity_icon(selected["name"], proto, 32)
    
    return render_template(
        "map_view.html",
        rel_file=rel_file,
        raw_text=raw_text,
        parse_ok=not parse_error,
        parse_error=parse_error,
        tilemap=tilemap,
        grid_chunks=grid_chunks,
        entities=entities,
        entity_types=entity_types,
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


@map_bp.route("/api/entity-layer/<cache_key>")
def get_entity_layer(cache_key):
    """Serve entity layer image"""
    entity_path = Path("static") / "map_cache" / cache_key / "entities.png"
    if not entity_path.exists():
        abort(404)
    return send_file(entity_path, mimetype='image/png')

@map_bp.route("/api/entity-icon/<instance_name>/<proto>")
def get_entity_icon_api(instance_name, proto):
    """Serve cached entity icon"""
    icon_path = Path("static") / "entity_cache" / instance_name / f"{proto}.png"
    if not icon_path.exists():
        abort(404)
    return send_file(icon_path, mimetype='image/png')
