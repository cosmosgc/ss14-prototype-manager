import yaml
from pathlib import Path
import base64
import zlib
import struct
import sqlite3
import json
import traceback
import sys
from datetime import datetime

# =========================
# CUSTOM YAML LOADER (IGNORE SS14 TAGS)
# =========================
class IgnoreTagsLoader(yaml.SafeLoader):
    pass


def ignore_unknown(loader, tag_suffix, node):
    # Convert everything into basic Python types
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


IgnoreTagsLoader.add_multi_constructor('!', ignore_unknown)


MAP_PATH = r"G:\Development\ss14\Andromeda-v\Resources\Maps\Test\dev_map.yml"
DB_PATH = r"G:\Development\ss14\prototype manager\data\app.db"
LOG_FILE = "ss14_debug_log.txt"

CHUNK_SIZE = 16


# =========================
# LOGGER
# =========================
class Logger:
    def __init__(self, file):
        self.terminal = sys.stdout
        self.log = open(file, "w", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)

    def flush(self):
        pass


sys.stdout = Logger(LOG_FILE)
sys.stderr = sys.stdout

print(f"==== SS14 DEBUG START {datetime.now()} ====\n")


# =========================
# TILE DECODER
# =========================
def decode_tile_data(encoded_str):
    print("\n[decode_tile_data] Starting decode...")

    try:
        cleaned = "".join(encoded_str.strip().split())
        print(f"[decode_tile_data] Cleaned length: {len(cleaned)}")

        if not cleaned:
            print("[decode_tile_data] Empty tile data")
            return [[0] * CHUNK_SIZE for _ in range(CHUNK_SIZE)]

        decoded = base64.b64decode(cleaned)
        print(f"[decode_tile_data] Base64 decoded: {len(decoded)} bytes")

        # SS14 format 7: 7 bytes per tile (256 tiles * 7 = 1792 bytes)
        # First 2 bytes = tile ID (uint16, little-endian)
        TILE_SIZE = 7

        grid = []

        for y in range(CHUNK_SIZE):
            row = []
            for x in range(CHUNK_SIZE):
                offset = (y * CHUNK_SIZE + x) * TILE_SIZE

                if offset + 2 > len(decoded):
                    row.append(0)
                else:
                    tile_id = struct.unpack('<H', decoded[offset:offset+2])[0]
                    row.append(tile_id)

            grid.append(row)

        return grid

    except Exception as e:
        print(f"[decode_tile_data] ERROR: {e}")
        return [[0] * CHUNK_SIZE for _ in range(CHUNK_SIZE)]


# =========================
# LOAD YAML
# =========================
def load_yaml(file_path):
    print(f"\n[load_yaml] Loading: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        docs = list(yaml.load_all(f, Loader=IgnoreTagsLoader))

    print(f"[load_yaml] Documents loaded: {len(docs)}")
    return docs


# =========================
# LOAD DB
# =========================
def load_db():
    print(f"\n[load_db] Connecting to DB: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# =========================
# MAIN PARSER
# =========================
def parse_map(docs):
    tilemap = {}
    grid_chunks = []
    entities = []

    proto_cache = {}
    db = load_db()

    print("\n[parse_map] Starting parse...")

    for doc_idx, doc in enumerate(docs):
        print(f"\n--- Document {doc_idx} ---")

        if not isinstance(doc, dict):
            continue

        print(f"[doc {doc_idx}] keys: {list(doc.keys())}")

        if "tilemap" in doc and not tilemap:
            print("[tilemap] Found tilemap")
            tilemap = {int(k): v for k, v in doc["tilemap"].items()}
            print(f"[tilemap] Entries: {len(tilemap)}")

        proto_groups = doc.get("entities", [])

        for group_idx, group in enumerate(proto_groups):
            if not isinstance(group, dict):
                continue

            proto_name = group.get("proto")
            print(f"\n[group {group_idx}] proto: {proto_name}")

            if proto_name == "":
                print("[MapGrid] Processing root grid")

                for ent in group.get("entities", []):
                    for comp in ent.get("components", []):
                        if comp.get("type") == "MapGrid":
                            chunks = comp.get("chunks", {})
                            print(f"[MapGrid] Total chunks: {len(chunks)}")

                            for chunk_key, chunk_data in chunks.items():
                                try:
                                    cx, cy = map(int, chunk_key.split(","))
                                except:
                                    print(f"[MapGrid] Bad chunk key: {chunk_key}")
                                    continue

                                print(f"[MapGrid] Decoding chunk ({cx},{cy})")

                                tiles = decode_tile_data(chunk_data.get("tiles", ""))

                                grid_chunks.append({
                                    "x": cx,
                                    "y": cy,
                                    "tiles": tiles
                                })

            elif proto_name:
                if proto_name not in proto_cache:
                    row = db.execute(
                        "SELECT proto_id, proto_type FROM prototype_ids WHERE proto_id = ?",
                        (proto_name,)
                    ).fetchone()

                    if row:
                        proto_cache[proto_name] = dict(row)
                    else:
                        proto_cache[proto_name] = {"proto_id": proto_name, "proto_type": "unknown"}

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
                        "proto": proto_name,
                        "x": pos_x,
                        "y": pos_y,
                        "name": name
                    })

    print("\n[parse_map] Done")
    print(f"Chunks: {len(grid_chunks)}")
    print(f"Entities: {len(entities)}")

    return tilemap, grid_chunks, entities


# =========================
# MAIN SAFE EXECUTION
# =========================
if __name__ == "__main__":
    try:
        docs = load_yaml(MAP_PATH)
        tilemap, grid_chunks, entities = parse_map(docs)

        print("\n==== SAMPLE OUTPUT ====")
        if grid_chunks:
            print("First chunk:", grid_chunks[0]["x"], grid_chunks[0]["y"])
            print("First chunk tiles sample:", grid_chunks[0]["tiles"][0][:10])
        if entities:
            print("First entity:", entities[0])

    except Exception:
        print("\n==== CRASH DETECTED ====\n")
        traceback.print_exc()

    finally:
        print("\n==== END OF SCRIPT ====")