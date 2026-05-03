"""
Map renderer for SS14 maps - generates PNG tiles for OpenLayers
"""
import base64
import struct
import yaml
import json
from pathlib import Path
from PIL import Image, ImageDraw

CHUNK_SIZE = 16
TILE_SIZE_PX = 32  # Pixel size of each tile in the rendered image

# Default tile colors for different tile types
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


def load_map_data(map_path):
    """Load and parse SS14 map file"""
    with open(map_path, "r", encoding="utf-8") as f:
        doc = yaml.load(f, Loader=IgnoreTagsLoader)

    tilemap = {}
    grid_chunks = []
    entities = []

    # Parse tilemap
    if "tilemap" in doc:
        tilemap = {int(k): v for k, v in doc["tilemap"].items()}

    # Parse entities/grids
    for group in doc.get("entities", []):
        proto = group.get("proto")
        
        if proto == "":
            # Root grid entity
            for ent in group.get("entities", []):
                for comp in ent.get("components", []):
                    if comp.get("type") == "MapGrid":
                        chunks = comp.get("chunks", {})
                        for chunk_key, chunk_data in chunks.items():
                            cx, cy = map(int, chunk_key.split(","))
                            tiles = decode_tile_data(chunk_data.get("tiles", ""))
                            grid_chunks.append({
                                "x": cx,
                                "y": cy,
                                "tiles": tiles
                            })
        else:
            # Regular entities
            for ent in group.get("entities", []):
                pos_x, pos_y = 0, 0
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
                    "proto": proto,
                    "x": pos_x,
                    "y": pos_y,
                    "name": name
                })

    return tilemap, grid_chunks, entities


def decode_tile_data(encoded_str):
    """Decode base64 tile data (SS14 format 7: 7 bytes per tile)"""
    try:
        cleaned = "".join(encoded_str.strip().split())
        if not cleaned:
            return [[0] * CHUNK_SIZE for _ in range(CHUNK_SIZE)]

        decoded = base64.b64decode(cleaned)
        TILE_DATA_SIZE = 7

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
        print("Tile decode error: " + str(e))
        return [[0] * CHUNK_SIZE for _ in range(CHUNK_SIZE)]


def get_tile_color(tile_id, tilemap):
    """Get the color for a tile ID"""
    tile_name = tilemap.get(tile_id, "Space")
    return TILE_COLORS.get(tile_name, (255, 0, 255, 255))  # Magenta for unknown


def render_chunk_png(chunk_data, tilemap, output_path):
    """Render a single chunk as PNG"""
    tiles = chunk_data["tiles"]
    img = Image.new('RGBA', (CHUNK_SIZE * TILE_SIZE_PX, CHUNK_SIZE * TILE_SIZE_PX))
    draw = ImageDraw.Draw(img)

    for y in range(CHUNK_SIZE):
        for x in range(CHUNK_SIZE):
            tile_id = tiles[y][x]
            color = get_tile_color(tile_id, tilemap)
            # Draw filled rectangle for the tile
            x0 = x * TILE_SIZE_PX
            y0 = y * TILE_SIZE_PX
            x1 = x0 + TILE_SIZE_PX - 1
            y1 = y0 + TILE_SIZE_PX - 1
            draw.rectangle([x0, y0, x1, y1], fill=color)

    img.save(output_path)
    return output_path


def render_full_map_png(tilemap, grid_chunks, output_path, scale=4):
    """Render the entire map as a single PNG image"""
    if not grid_chunks:
        print("No chunks to render")
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

    for (cx, cy), chunk in chunk_lookup.items():
        tiles = chunk["tiles"]
        offset_x = (cx - min_cx) * CHUNK_SIZE
        offset_y = (cy - min_cy) * CHUNK_SIZE

        for y in range(CHUNK_SIZE):
            for x in range(CHUNK_SIZE):
                tile_id = tiles[y][x]
                color = get_tile_color(tile_id, tilemap)
                px = (offset_x + x) * scale
                py = (offset_y + y) * scale
                draw.rectangle([px, py, px + scale - 1, py + scale - 1], fill=color)

    img.save(output_path)
    print("Saved full map preview: " + output_path)
    return output_path


def export_map_json(tilemap, grid_chunks, entities, output_path):
    """Export map data as JSON for the web viewer"""
    # Create chunk lookup with tile data
    chunks_data = []
    for chunk in grid_chunks:
        chunks_data.append({
            "x": chunk["x"],
            "y": chunk["y"],
            "tiles": chunk["tiles"]
        })

    map_data = {
        "tilemap": tilemap,
        "chunks": chunks_data,
        "entities": entities,
        "chunkSize": CHUNK_SIZE,
        "tileSizePx": TILE_SIZE_PX
    }

    with open(output_path, "w") as f:
        json.dump(map_data, f)

    print("Saved map JSON: " + output_path)
    return output_path


def generate_tiles_directory(tilemap, grid_chunks, output_dir):
    """Generate PNG tiles in a directory structure for OpenLayers"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Generate individual chunk tiles
    tiles_dir = output_path / "tiles"
    tiles_dir.mkdir(exist_ok=True)

    for chunk in grid_chunks:
        cx, cy = chunk["x"], chunk["y"]
        filename = tiles_dir / f"chunk_{cx}_{cy}.png"
        render_chunk_png(chunk, tilemap, filename)

    print("Generated " + str(len(grid_chunks)) + " tile images in " + str(tiles_dir))
    return output_path


if __name__ == "__main__":
    # Test rendering
    MAP_PATH = r"G:\Development\ss14\Andromeda-v\Resources\Maps\Test\dev_map.yml"
    
    print("Loading map...")
    tilemap, grid_chunks, entities = load_map_data(MAP_PATH)
    
    print("Tilemap entries: " + str(len(tilemap)))
    print("Grid chunks: " + str(len(grid_chunks)))
    print("Entities: " + str(len(entities)))
    
    # Generate tiles
    print("\nGenerating tiles...")
    generate_tiles_directory(tilemap, grid_chunks, "static/tiles")
    
    # Generate full map preview
    print("\nGenerating full map preview...")
    render_full_map_png(tilemap, grid_chunks, "static/map_preview.png", scale=4)
    
    # Export JSON
    print("\nExporting map JSON...")
    export_map_json(tilemap, grid_chunks, entities, "static/map_data.json")
    
    print("\nDone!")
