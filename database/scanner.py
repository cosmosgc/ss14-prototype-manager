import json
from pathlib import Path
from typing import Any

import yaml


def load_yaml_documents(file_path: Path) -> list[Any]:
    text = file_path.read_text(encoding="utf-8")
    text = text.replace("\t", "    ")

    class IgnoreTagsLoader(yaml.SafeLoader):
        pass

    def _ignore_unknown(loader, tag_suffix, node):
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node)
        return None

    IgnoreTagsLoader.add_multi_constructor("", _ignore_unknown)

    docs = list(yaml.load_all(text, Loader=IgnoreTagsLoader))
    return docs


def scan_rsi_records(instance_name: str, root_path: str) -> int:
    """Scan and store RSI records."""
    from database import db_helper

    textures_root = Path(root_path) / "Resources" / "Textures"
    if not textures_root.exists():
        return 0

    count = 0
    with db_helper.get_conn() as conn:
        conn.execute("DELETE FROM rsi_records WHERE instance_name = ?", (instance_name,))
        conn.execute("DELETE FROM prototype_rsi WHERE instance_name = ?", (instance_name,))

        for rsi_dir in textures_root.rglob("*.rsi"):
            rel_path = rsi_dir.relative_to(textures_root).as_posix()
            rsi_name = rel_path

            meta_json = None
            meta_path = rsi_dir / "meta.json"
            if meta_path.exists():
                try:
                    meta_json = meta_path.read_text(encoding="utf-8")
                except Exception:
                    pass

            conn.execute("""
                INSERT OR REPLACE INTO rsi_records
                (instance_name, rsi_name, rel_path, meta_json)
                VALUES (?, ?, ?, ?)
            """, (instance_name, rsi_name, rel_path, meta_json))
            count += 1

    return count


def scan_prototype_records(instance_name: str, root_path: str) -> int:
    """Scan prototypes with content and link to RSI."""
    from database.helpers import get_db

    proto_root = Path(root_path) / "Resources" / "Prototypes"
    if not proto_root.exists():
        return 0

    count = 0
    seen = set()

    SKIP_FILES = {"tags.yml", "tags.yaml"}

    with get_db() as conn:
        conn.execute("DELETE FROM prototype_ids WHERE instance_name = ?", (instance_name,))
        conn.execute("DELETE FROM prototype_rsi WHERE instance_name = ?", (instance_name,))
        conn.execute("DELETE FROM prototype_components WHERE instance_name = ?", (instance_name,))
        conn.execute("DELETE FROM prototype_component_fields WHERE instance_name = ?", (instance_name,))

        for path in proto_root.rglob("*.yml"):
            rel_path = path.relative_to(proto_root).as_posix()

            if path.name.lower() in SKIP_FILES:
                continue

            try:
                docs = load_yaml_documents(path)
            except Exception as e:
                print(f"YAML error: {path} {e}")
                continue

            for doc in docs:
                stack = [doc]
                while stack:
                    current = stack.pop()

                    if isinstance(current, dict):
                        proto_id = current.get("id")
                        proto_type = current.get("type")

                        if isinstance(proto_id, str) and isinstance(proto_type, str):
                            key = (instance_name, proto_id)
                            if key not in seen:
                                seen.add(key)

                                content_json = json.dumps(current)

                                conn.execute("""
                                    INSERT OR REPLACE INTO prototype_ids
                                    (instance_name, proto_id, proto_type, rel_path, content)
                                    VALUES (?, ?, ?, ?, ?)
                                """, (instance_name, proto_id, proto_type, rel_path, content_json))
                                count += 1

                                sprites = collect_sprites_from_prototype(current)
                                for sprite in sprites:
                                    conn.execute("""
                                        INSERT OR REPLACE INTO prototype_rsi
                                        (instance_name, proto_id, rsi_name, rsi_rel_path)
                                        VALUES (?, ?, ?, ?)
                                    """, (instance_name, proto_id, sprite, sprite))

                            components = current.get("components")
                            if isinstance(components, list):
                                for comp in components:
                                    if not isinstance(comp, dict):
                                        continue
                                    comp_type = comp.get("type")
                                    if not comp_type:
                                        continue

                                    conn.execute("""
                                        INSERT OR REPLACE INTO prototype_components
                                        (instance_name, proto_id, component_type, data)
                                        VALUES (?, ?, ?, ?)
                                    """, (instance_name, proto_id, comp_type, json.dumps(comp)))

                                    for key_name, value in comp.items():
                                        if key_name == "type":
                                            continue
                                        if isinstance(value, (str, int, float, bool)):
                                            conn.execute("""
                                                INSERT OR REPLACE INTO prototype_component_fields
                                                (instance_name, proto_id, component_type, field_name, field_value)
                                                VALUES (?, ?, ?, ?, ?)
                                            """, (instance_name, proto_id, comp_type, key_name, str(value)))

                        for v in current.values():
                            stack.append(v)

                    elif isinstance(current, list):
                        stack.extend(current)

        conn.execute("""
            INSERT OR REPLACE INTO instance_scan
            (instance_name, scanned_at, id_count)
            VALUES (?, datetime('now'), ?)
        """, (instance_name, count))

    return count


def collect_sprites_from_prototype(proto: dict) -> list[str]:
    sprites = []
    stack = [proto]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            sprite = current.get("sprite")
            if isinstance(sprite, str) and sprite.endswith(".rsi"):
                sprites.append(sprite)
            for v in current.values():
                stack.append(v)
        elif isinstance(current, list):
            stack.extend(current)
    return list(set(sprites))


def load_prototype_by_id(instance_name: str, proto_id: str) -> dict | None:
    """Load a prototype document by ID."""
    from database import db_helper

    with db_helper.get_conn() as conn:
        row = conn.execute(
            "SELECT content FROM prototype_ids WHERE instance_name = ? AND proto_id = ?",
            (instance_name, proto_id),
        ).fetchone()

    if row and row["content"]:
        return json.loads(row["content"])
    return None


def get_rsi_info(instance_name: str, rsi_name: str) -> dict | None:
    """Get RSI record info."""
    from database import db_helper

    with db_helper.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM rsi_records WHERE instance_name = ? AND rsi_name = ?",
            (instance_name, rsi_name),
        ).fetchone()

    if not row:
        return None

    result = {"rsi_name": row["rsi_name"], "rel_path": row["rel_path"]}
    if row["meta_json"]:
        try:
            result["meta"] = json.loads(row["meta_json"])
        except Exception:
            pass
    return result


def scan_instance(instance_name: str, root_path: str) -> dict:
    """Full scan of an instance."""
    protos = scan_prototype_records(instance_name, root_path)
    rsis = scan_rsi_records(instance_name, root_path)
    return {"prototypes": protos, "rsis": rsis}