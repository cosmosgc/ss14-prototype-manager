import sqlite3
import json
from pathlib import Path
from functools import lru_cache
from typing import Any

DB_PATH = Path(__file__).parent.parent / "data" / "app.db"


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS instances (
            name TEXT PRIMARY KEY,
            root_path TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS prototype_ids (
            instance_name TEXT NOT NULL,
            proto_id TEXT NOT NULL,
            proto_type TEXT NOT NULL,
            rel_path TEXT NOT NULL,
            content TEXT,
            PRIMARY KEY (instance_name, proto_id)
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS rsi_records (
            instance_name TEXT NOT NULL,
            rsi_name TEXT NOT NULL,
            rel_path TEXT NOT NULL,
            meta_json TEXT,
            PRIMARY KEY (instance_name, rsi_name)
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS prototype_rsi (
            instance_name TEXT NOT NULL,
            proto_id TEXT NOT NULL,
            rsi_name TEXT NOT NULL,
            rsi_rel_path TEXT,
            PRIMARY KEY (instance_name, proto_id, rsi_name)
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS prototype_components (
            instance_name TEXT NOT NULL,
            proto_id TEXT NOT NULL,
            component_type TEXT NOT NULL,
            data TEXT,
            PRIMARY KEY (instance_name, proto_id, component_type)
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS prototype_component_fields (
            instance_name TEXT NOT NULL,
            proto_id TEXT NOT NULL,
            component_type TEXT NOT NULL,
            field_name TEXT NOT NULL,
            field_value TEXT,
            PRIMARY KEY (instance_name, proto_id, component_type, field_name)
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS instance_scan (
            instance_name TEXT PRIMARY KEY,
            scanned_at TEXT NOT NULL,
            id_count INTEGER NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS instance_settings (
            instance_name TEXT PRIMARY KEY,
            custom_dir TEXT NOT NULL DEFAULT ''
        )
        """)


def save_instance(name: str, root_path: str) -> None:
    with get_db() as conn:
        conn.execute("INSERT INTO instances (name, root_path) VALUES (?, ?)", (name, root_path))


def delete_instance(name: str) -> int:
    with get_db() as conn:
        rows = 0
        for table in ["instances", "prototype_ids", "rsi_records", "prototype_rsi",
                      "prototype_components", "prototype_component_fields",
                      "instance_scan", "instance_settings"]:
            cur = conn.execute(f"DELETE FROM {table} WHERE instance_name = ?", (name,))
            rows += cur.rowcount
        return rows


@lru_cache(maxsize=1)
def load_instances() -> list[dict[str, str]]:
    with get_db() as conn:
        rows = conn.execute("SELECT name, root_path FROM instances ORDER BY name").fetchall()
    return [{"name": r["name"], "root_path": r["root_path"]} for r in rows]


def load_instance_by_name(name: str) -> dict | None:
    instances = load_instances()
    for instance in instances:
        if instance["name"] == name:
            return instance
    return None


def search_prototype_ids(instance_name: str, query: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT proto_id, rel_path FROM prototype_ids "
            "WHERE instance_name = ? AND proto_id LIKE ? ORDER BY proto_id LIMIT 200",
            (instance_name, f"%{query}%"),
        ).fetchall()
    return [{"proto_id": r["proto_id"], "rel_path": r["rel_path"]} for r in rows]


def find_prototype_paths_by_id(instance_name: str, proto_id: str) -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT rel_path FROM prototype_ids WHERE instance_name = ? AND proto_id = ? ORDER BY rel_path",
            (instance_name, proto_id),
        ).fetchall()
    return [r["rel_path"] for r in rows]


def find_first_prototype_path_by_id(instance_name: str, proto_id: str) -> str | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT rel_path FROM prototype_ids WHERE instance_name = ? AND proto_id = ? ORDER BY rel_path LIMIT 1",
            (instance_name, proto_id),
        ).fetchone()
    return row["rel_path"] if row else None


def load_prototype_content(instance_name: str, proto_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT content FROM prototype_ids WHERE instance_name = ? AND proto_id = ?",
            (instance_name, proto_id),
        ).fetchone()
    if row and row["content"]:
        return json.loads(row["content"])
    return None


def find_rsi_for_prototype(instance_name: str, proto_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT rsi_name, rsi_rel_path FROM prototype_rsi WHERE instance_name = ? AND proto_id = ?",
            (instance_name, proto_id),
        ).fetchall()
    return [{"name": r["rsi_name"], "path": r["rsi_rel_path"]} for r in rows]


def get_instance_stats(instance_name: str) -> dict:
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM prototype_ids WHERE instance_name = ?", (instance_name,))
        total_rows = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT proto_id) FROM prototype_ids WHERE instance_name = ?", (instance_name,))
        unique_ids = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM prototype_components WHERE instance_name = ?", (instance_name,))
        component_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM prototype_component_fields WHERE instance_name = ?", (instance_name,))
        field_count = cur.fetchone()[0]

        cur.execute("""
            SELECT proto_type, COUNT(*) FROM prototype_ids
            WHERE instance_name = ? GROUP BY proto_type ORDER BY COUNT(*) DESC
        """, (instance_name,))
        types = cur.fetchall()

        cur.execute("SELECT scanned_at, id_count FROM instance_scan WHERE instance_name = ?", (instance_name,))
        row = cur.fetchone()

        last_scan = row[0] if row else None
        last_scan_count = row[1] if row else 0

        return {
            "id_count": total_rows,
            "unique_ids": unique_ids,
            "component_count": component_count,
            "field_count": field_count,
            "types": types,
            "last_scan": last_scan,
            "last_scan_count": last_scan_count,
        }


def get_custom_dir(instance_name: str) -> str:
    with get_db() as conn:
        row = conn.execute("SELECT custom_dir FROM instance_settings WHERE instance_name = ?", (instance_name,)).fetchone()
    return row["custom_dir"] if row else ""


def set_custom_dir(instance_name: str, custom_dir: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO instance_settings (instance_name, custom_dir) VALUES (?, ?) "
            "ON CONFLICT(instance_name) DO UPDATE SET custom_dir=excluded.custom_dir",
            (instance_name, custom_dir),
        )