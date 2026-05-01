from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
    Blueprint,
)

from routes import register_blueprints
from PIL import Image
import yaml
from pydub import AudioSegment
from mutagen.mp3 import MP3
from mutagen.oggvorbis import OggVorbis
from mutagen.id3 import ID3
import tempfile


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DB_PATH = Path(os.getenv("SQLITE_PATH", str(DATA_DIR / "app.db")))
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
DEFAULT_THUMB_SCALE = int(os.getenv("DEFAULT_THUMB_SCALE", "4"))


class IgnoreUnknownTagLoader(yaml.SafeLoader):
    pass


def _construct_unknown(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


IgnoreUnknownTagLoader.add_multi_constructor("!", _construct_unknown)


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    init_db()

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "selected_instance_name": session.get("selected_instance"),
            "instances_count": len(load_instances()),
        }

    @app.route("/")
    def index() -> str:
        instances = load_instances()
        selected_name = session.get("selected_instance")
        selected = get_instance_by_name(selected_name, instances) if selected_name else None
        return render_template("index.html", instances=instances, selected=selected)

    @app.post("/instances/add")
    def add_instance():
        name = request.form.get("name", "").strip()
        root = request.form.get("root_path", "").strip()
        if not name or not root:
            flash("Name and path are required.", "error")
            return redirect(url_for("index"))
        if not Path(root).exists():
            flash("Path does not exist.", "error")
            return redirect(url_for("index"))

        instances = load_instances()
        if any(i["name"].lower() == name.lower() for i in instances):
            flash("Instance name already exists.", "error")
            return redirect(url_for("index"))

        save_instance(name, str(Path(root)))
        session["selected_instance"] = name
        flash("Instance added.", "success")
        return redirect(url_for("index"))

    @app.post("/instances/<name>/select")
    def select_instance(name: str):
        instances = load_instances()
        if not get_instance_by_name(name, instances):
            abort(404)
        session["selected_instance"] = name
        flash(f"Selected instance: {name}", "success")
        return redirect(url_for("index"))

    @app.post("/instances/<name>/delete")
    def delete_instance(name: str):
        if not delete_instance_db(name):
            abort(404)
        if session.get("selected_instance") == name:
            session.pop("selected_instance", None)
    flash(f"Deleted instance: {name}", "success")
    return redirect(url_for("index"))

    # Register blueprints for modular routing
    from routes import register_blueprints
    register_blueprints(app)

    @app.errorhandler(Exception)
    def handle_exception(e):
        import traceback
        traceback.print_exc()
        return "Internal Server Error", 500

    return app

def load_instances() -> list[dict[str, str]]:
    with get_db() as conn:
        rows = conn.execute("SELECT name, root_path FROM instances ORDER BY name").fetchall()
    return [{"name": r["name"], "root_path": r["root_path"]} for r in rows]


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

        # 🔹 Main prototype table (now WITH type)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS prototype_ids (
            instance_name TEXT NOT NULL,
            proto_id TEXT NOT NULL,
            proto_type TEXT NOT NULL,
            rel_path TEXT NOT NULL,
            PRIMARY KEY (instance_name, proto_id)
        )
        """)

        # 🔹 Component entries (each "type: X" inside components)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS prototype_components (
            instance_name TEXT NOT NULL,
            proto_id TEXT NOT NULL,
            component_type TEXT NOT NULL,
            data TEXT, -- JSON blob for flexibility
            PRIMARY KEY (instance_name, proto_id, component_type)
        )
        """)

        # 🔹 Optional: extracted known fields (indexed)
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


def delete_instance_db(name: str) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM instances WHERE name = ?", (name,))
        conn.execute("DELETE FROM prototype_ids WHERE instance_name = ?", (name,))
        conn.execute("DELETE FROM instance_scan WHERE instance_name = ?", (name,))
        conn.execute("DELETE FROM instance_settings WHERE instance_name = ?", (name,))
        return cur.rowcount > 0


def get_instance_by_name(name: str | None, instances: list[dict[str, str]]) -> dict[str, str] | None:
    if not name:
        return None
    for instance in instances:
        if instance["name"] == name:
            return instance
    return None


def selected_instance_or_400() -> dict[str, str]:
    instances = load_instances()
    selected_name = session.get("selected_instance")
    selected = get_instance_by_name(selected_name, instances)
    if not selected:
        abort(400, "No instance selected.")
    return selected


def safe_join(base: Path, relative: str) -> Path:
    candidate = (base / relative).resolve()
    base_resolved = base.resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError:
        abort(400, "Invalid path.")
    return candidate


def list_prototype_files(proto_root: Path) -> list[str]:
    if not proto_root.exists():
        return []
    out: list[str] = []
    for file in proto_root.rglob("*"):
        if file.suffix.lower() in {".yml", ".yaml"} and file.is_file():
            out.append(file.relative_to(proto_root).as_posix())
    out.sort()
    return out


def load_yaml_documents(file_path: Path) -> list[Any]:
    text = file_path.read_text(encoding="utf-8")

    # 🔥 Fix invalid YAML: replace tabs with spaces
    text = text.replace("\t", "    ")

    docs = list(yaml.load_all(text, Loader=IgnoreUnknownTagLoader))
    return docs


def validate_yaml_text(text: str) -> tuple[bool, str | None]:
    try:
        text = text.replace("\t", "    ")
        list(yaml.load_all(text, Loader=IgnoreUnknownTagLoader))
        return True, None
    except yaml.YAMLError as exc:
        return False, str(exc)


def collect_sprite_refs(node: Any) -> list[str]:
    refs: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key == "sprite" and isinstance(value, str) and value.endswith(".rsi"):
                    refs.append(value)
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return sorted(set(refs))


def collect_audio_refs(node: Any) -> list[str]:
    refs: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key == "path" and isinstance(value, str) and value.startswith("/Audio/"):
                    refs.append(value)
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return sorted(set(refs))


def collect_sprite_state_pairs(node: Any) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            sprite = current.get("sprite")
            state = current.get("state")
            if isinstance(sprite, str) and sprite.endswith(".rsi") and isinstance(state, str):
                pairs.append({"sprite": sprite, "state": state})
            for value in current.values():
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    unique = {(p["sprite"], p["state"]) for p in pairs}
    return [{"sprite": s, "state": st} for s, st in sorted(unique)]


def find_first_sprite_state_from_docs(docs: list[Any]) -> tuple[str, str]:
    stack = [docs]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            sprite = current.get("sprite")
            state = current.get("state")
            if isinstance(sprite, str) and sprite.endswith(".rsi"):
                return sprite, state if isinstance(state, str) and state else "icon"
            sprites = current.get("sprites")
            if isinstance(sprites, list):
                for item in sprites:
                    if isinstance(item, dict):
                        s = item.get("sprite")
                        st = item.get("state")
                        if isinstance(s, str) and s.endswith(".rsi"):
                            return s, st if isinstance(st, str) and st else "icon"
            for v in current.values():
                stack.append(v)
        elif isinstance(current, list):
            stack.extend(current)
    return "", "icon"


def resolve_preview_for_prototype_id(instance: dict[str, str], proto_id: str) -> tuple[str, str]:
    rel = find_first_prototype_path_by_id(instance["name"], proto_id)
    if not rel:
        return "", "icon"
    proto_root = Path(instance["root_path"]) / "Resources" / "Prototypes"
    try:
        docs = load_yaml_documents(safe_join(proto_root, rel))
    except Exception:
        return "", "icon"
    entity = find_entity_node_by_id(docs, proto_id)
    if not entity:
        return find_first_sprite_state_from_docs(docs)
    return resolve_entity_sprite_state(instance, entity, set(), 0)


def resolve_preview_for_row(instance: dict[str, str], rel_path: str, proto_id: str) -> tuple[str, str]:
    proto_root = Path(instance["root_path"]) / "Resources" / "Prototypes"
    try:
        docs = load_yaml_documents(safe_join(proto_root, rel_path))
    except Exception:
        return resolve_preview_for_prototype_id(instance, proto_id)
    entity = find_entity_node_by_id(docs, proto_id)
    if entity:
        return resolve_entity_sprite_state(instance, entity, set(), 0)
    return resolve_preview_for_prototype_id(instance, proto_id)


def find_entity_node_by_id(node: Any, proto_id: str) -> dict[str, Any] | None:
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if current.get("type") == "entity" and current.get("id") == proto_id:
                return current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return None


def resolve_entity_sprite_state(
    instance: dict[str, str], entity: dict[str, Any], visited: set[str], depth: int
) -> tuple[str, str]:
    if depth > 30:
        return "", "icon"
    local_sprite, local_state = extract_sprite_from_entity(entity)
    forced_state = local_state if local_state and local_state != "icon" else ""
    if local_sprite:
        return adjust_state_to_existing(instance, local_sprite, local_state or "icon")

    parent_val = entity.get("parent")
    parent_ids: list[str] = []
    if isinstance(parent_val, str):
        parent_ids = [parent_val]
    elif isinstance(parent_val, list):
        parent_ids = [x for x in parent_val if isinstance(x, str)]

    for parent_id in parent_ids:
        if parent_id in visited:
            continue
        visited.add(parent_id)
        parent_entity = load_entity_by_id(instance, parent_id)
        if not parent_entity:
            continue
        parent_sprite, parent_state = resolve_entity_sprite_state(instance, parent_entity, visited, depth + 1)
        if parent_sprite:
            # If current entity overrides only state, apply it to inherited parent sprite.
            if forced_state:
                return adjust_state_to_existing(instance, parent_sprite, forced_state)
            return parent_sprite, parent_state

    return "", local_state or "icon"


def extract_sprite_from_entity(entity: dict[str, Any]) -> tuple[str, str]:
    state = "icon"
    components = entity.get("components")
    if isinstance(components, list):
        # Prefer explicit Icon component on the entity itself for UI preview.
        for comp in components:
            if isinstance(comp, dict) and comp.get("type") == "Icon":
                if isinstance(comp.get("state"), str):
                    state = comp["state"]
                if isinstance(comp.get("sprite"), str) and comp["sprite"].endswith(".rsi"):
                    return comp["sprite"], state

        for comp in components:
            if isinstance(comp, dict) and comp.get("type") == "Sprite":
                if isinstance(comp.get("state"), str):
                    state = comp["state"]
                if isinstance(comp.get("sprite"), str) and comp["sprite"].endswith(".rsi"):
                    return comp["sprite"], state
                sprites = comp.get("sprites")
                if isinstance(sprites, list):
                    for s in sprites:
                        if isinstance(s, dict) and isinstance(s.get("sprite"), str):
                            return s["sprite"], s.get("state", state) if isinstance(s.get("state"), str) else state
    # Fallback generic keys
    sprite = entity.get("sprite")
    if isinstance(sprite, str) and sprite.endswith(".rsi"):
        if isinstance(entity.get("state"), str):
            state = entity["state"]
        return sprite, state
    sprites = entity.get("sprites")
    if isinstance(sprites, list):
        for s in sprites:
            if isinstance(s, dict) and isinstance(s.get("sprite"), str):
                return s["sprite"], s.get("state", state) if isinstance(s.get("state"), str) else state
    return "", state


def adjust_state_to_existing(instance: dict[str, str], sprite: str, preferred_state: str) -> tuple[str, str]:
    textures_root = Path(instance["root_path"]) / "Resources" / "Textures"
    rsi_dir = safe_join_or_none(textures_root, sprite)
    if not rsi_dir or not rsi_dir.exists():
        return sprite, preferred_state
    available = list_rsi_states(rsi_dir)
    if not available:
        return sprite, preferred_state
    if preferred_state in available:
        return sprite, preferred_state
    if "icon" in available:
        return sprite, "icon"
    return sprite, available[0]


def list_rsi_states(rsi_dir: Path) -> list[str]:
    meta_path = rsi_dir / "meta.json"
    states: list[str] = []
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            states = [s.get("name") for s in meta.get("states", []) if isinstance(s, dict) and s.get("name")]
        except Exception:
            states = []
    if not states:
        states = [p.stem for p in sorted(rsi_dir.glob("*.png"))]
    return sorted(set(states))


def load_entity_by_id(instance: dict[str, str], proto_id: str) -> dict[str, Any] | None:
    rel = find_first_prototype_path_by_id(instance["name"], proto_id)
    if not rel:
        return None
    proto_root = Path(instance["root_path"]) / "Resources" / "Prototypes"
    try:
        docs = load_yaml_documents(safe_join(proto_root, rel))
    except Exception:
        return None
    return find_entity_node_by_id(docs, proto_id)


def validate_crate_parent_compatibility(instance: dict[str, str], parent_id: str) -> tuple[bool, str]:
    if not parent_id:
        return False, "crate_parent is empty."
    allowed = {"CrateBaseSecure", "CrateGeneric", "CrateBaseWeldable"}
    if parent_id in allowed:
        return True, "ok"
    entity = load_entity_by_id(instance, parent_id)
    if not entity:
        return False, f'"{parent_id}" was not found in scanned prototype IDs.'
    if is_entity_descended_from(instance, entity, allowed, set(), 0):
        return True, "ok"
    return False, f'"{parent_id}" is not compatible. Expected parent chain to include one of: {", ".join(sorted(allowed))}.'


def is_entity_descended_from(
    instance: dict[str, str], entity: dict[str, Any], allowed: set[str], visited: set[str], depth: int
) -> bool:
    if depth > 40:
        return False
    parent_val = entity.get("parent")
    parent_ids: list[str] = []
    if isinstance(parent_val, str):
        parent_ids = [parent_val]
    elif isinstance(parent_val, list):
        parent_ids = [x for x in parent_val if isinstance(x, str)]
    for parent_id in parent_ids:
        if parent_id in allowed:
            return True
        if parent_id in visited:
            continue
        visited.add(parent_id)
        parent_entity = load_entity_by_id(instance, parent_id)
        if parent_entity and is_entity_descended_from(instance, parent_entity, allowed, visited, depth + 1):
            return True
    return False


def collect_prototype_like_refs(node: Any) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if is_prototype_key(key):
                    for candidate in extract_candidate_values(value):
                        if looks_like_proto_id(candidate):
                            refs.append({"key": key, "id": candidate})
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    unique = {(r["key"], r["id"]) for r in refs}
    return [{"key": k, "id": pid} for k, pid in sorted(unique)]


def build_sprite_cards(
    instance_root: Path, sprites: list[str], sprite_state_pairs: list[dict[str, str]]
) -> list[dict[str, Any]]:
    textures_root = instance_root / "Resources" / "Textures"
    state_by_sprite = {x["sprite"]: x["state"] for x in sprite_state_pairs}
    cards: list[dict[str, Any]] = []
    for sprite in sprites:
        rsi_dir = safe_join_or_none(textures_root, sprite)
        if rsi_dir is None:
            cards.append(
                {
                    "sprite": sprite,
                    "exists": False,
                    "meta_exists": False,
                    "states": [],
                    "png_states": [],
                    "preferred_state": state_by_sprite.get(sprite),
                    "preview_state": None,
                }
            )
            continue
        meta_path = rsi_dir / "meta.json"
        states: list[str] = []
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                states = [s.get("name") for s in meta.get("states", []) if isinstance(s, dict) and s.get("name")]
            except json.JSONDecodeError:
                states = []
        png_states: list[str] = []
        if rsi_dir.exists():
            for png in sorted(rsi_dir.glob("*.png")):
                png_states.append(png.stem)
        all_states = sorted(set(states + png_states))
        preferred_state = state_by_sprite.get(sprite)
        if preferred_state and preferred_state not in all_states:
            all_states.append(preferred_state)
        cards.append(
            {
                "sprite": sprite,
                "exists": rsi_dir.exists(),
                "meta_exists": meta_path.exists(),
                "states": all_states,
                "png_states": sorted(set(png_states)),
                "preferred_state": preferred_state,
                "preview_state": preferred_state or ("icon" if "icon" in all_states else (all_states[0] if all_states else None)),
            }
        )
    return cards


def build_audio_cards(instance_root: Path, audio_paths: list[str]) -> list[dict[str, Any]]:
    audio_root = instance_root / "Resources" / "Audio"
    cards: list[dict[str, Any]] = []
    for source_path in audio_paths:
        rel = source_path.removeprefix("/Audio/")
        file_path = safe_join_or_none(audio_root, rel)
        cards.append(
            {
                "source_path": source_path,
                "relative": rel,
                "exists": bool(file_path and file_path.exists()),
            }
        )
    return cards


def build_file_entries(proto_root: Path, files: list[str], instance_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for rel_file in files:
        file_path = safe_join(proto_root, rel_file)
        text = file_path.read_text(encoding="utf-8")
        sprite = find_first_sprite_in_text(text)
        state = find_first_state_in_text(text)
        sprite_exists = False
        if sprite:
            sprite_path = safe_join_or_none(instance_root / "Resources" / "Textures", sprite)
            sprite_exists = bool(sprite_path and sprite_path.exists())
        entries.append(
            {
                "path": rel_file,
                "name": Path(rel_file).name,
                "parts": rel_file.split("/"),
                "hover_sprite": sprite,
                "hover_state": state or "icon",
                "hover_exists": bool(sprite and sprite_exists),
            }
        )
    return entries


def find_first_sprite_in_text(text: str) -> str | None:
    match = re.search(r'^\s*sprite:\s*"?([^"\n]+\.rsi)"?\s*$', text, re.MULTILINE)
    return match.group(1).strip() if match else None


def find_first_state_in_text(text: str) -> str | None:
    match = re.search(r'^\s*state:\s*"?([^"\n]+)"?\s*$', text, re.MULTILINE)
    return match.group(1).strip() if match else None


def build_tree(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    root: dict[str, Any] = {"dirs": {}, "files": []}
    for entry in entries:
        node = root
        for part in entry["parts"][:-1]:
            node = node["dirs"].setdefault(part, {"dirs": {}, "files": [], "name": part})
        node["files"].append(entry)
    return tree_node_to_list(root)


def tree_node_to_list(node: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for dirname in sorted(node["dirs"].keys()):
        subnode = node["dirs"][dirname]
        items.append(
            {
                "type": "dir",
                "name": dirname,
                "children": tree_node_to_list(subnode),
            }
        )
    for file_entry in sorted(node["files"], key=lambda x: x["name"].lower()):
        items.append({"type": "file", **file_entry})
    return items


def safe_join_or_none(base: Path, relative: str) -> Path | None:
    try:
        return safe_join(base, relative)
    except Exception:
        return None


def find_vscode_cli() -> str | None:
    local_appdata = os.getenv("LOCALAPPDATA", "")
    program_files = os.getenv("ProgramFiles", r"C:\Program Files")
    candidates = [
        "code",
        str(Path(local_appdata) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd"),
        str(Path(program_files) / "Microsoft VS Code" / "bin" / "code.cmd"),
    ]
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                return candidate
        except Exception:
            continue
    return None


def is_prototype_key(key: str) -> bool:
    key_l = key.lower()
    if key_l in {"id", "type", "name", "description", "sprite", "state", "path"}:
        return False
    if key_l.endswith("proto") or key_l.endswith("prototype"):
        return True
    if key_l.endswith("id"):
        return True
    if key_l in {
        "parent",
        "recipeunlocks",
        "head",
        "jumpsuit",
        "neck",
        "mask",
        "outerclothing",
        "shoes",
        "gloves",
        "eyes",
        "belt",
        "back",
        "idcard",
        "ears",
    }:
        return True
    return False


def extract_candidate_values(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                for v in item.values():
                    if isinstance(v, str):
                        out.append(v)
    elif isinstance(value, dict):
        for v in value.values():
            if isinstance(v, str):
                out.append(v)
    return out


def looks_like_proto_id(value: str) -> bool:
    if len(value) < 3 or len(value) > 120:
        return False
    if "/" in value or "\\" in value or "." in value:
        return False
    if value.lower() in {"true", "false", "null"}:
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", value))


def build_prototype_index(proto_root: Path) -> dict[str, dict[str, list[str]]]:
    id_to_files: dict[str, list[str]] = {}
    sprite_to_files: dict[str, list[str]] = {}
    audio_to_files: dict[str, list[str]] = {}
    files = list_prototype_files(proto_root)
    for rel_file in files:
        file_path = safe_join(proto_root, rel_file)
        try:
            docs = load_yaml_documents(file_path)
        except Exception:
            continue
        ids = collect_proto_ids(docs)
        for proto_id in ids:
            id_to_files.setdefault(proto_id, []).append(rel_file)
        for sprite in collect_sprite_refs(docs):
            sprite_to_files.setdefault(sprite, []).append(rel_file)
        for audio in collect_audio_refs(docs):
            audio_to_files.setdefault(audio, []).append(rel_file)
    for mapping in (id_to_files, sprite_to_files, audio_to_files):
        for key in mapping:
            mapping[key] = sorted(set(mapping[key]))
    return {
        "id_to_files": id_to_files,
        "sprite_to_files": sprite_to_files,
        "audio_to_files": audio_to_files,
    }


def collect_proto_ids(node: Any) -> list[str]:
    ids: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            proto_id = current.get("id")
            if isinstance(proto_id, str):
                ids.append(proto_id)
            for v in current.values():
                stack.append(v)
        elif isinstance(current, list):
            stack.extend(current)
    return sorted(set(ids))

def extract_prototypes(node):
    """Yield dicts that look like prototypes."""
    stack = [node]

    while stack:
        current = stack.pop()

        if isinstance(current, dict):
            if "id" in current and "type" in current:
                yield current

            for v in current.values():
                stack.append(v)

        elif isinstance(current, list):
            stack.extend(current)

def add_related_prototypes(
    sprite_cards: list[dict[str, Any]],
    audio_cards: list[dict[str, Any]],
    index: dict[str, dict[str, list[str]]],
    current_file: str,
) -> None:
    for card in sprite_cards:
        related = [f for f in index["sprite_to_files"].get(card["sprite"], []) if f != current_file]
        card["related_files"] = related[:20]
    for card in audio_cards:
        related = [f for f in index["audio_to_files"].get(card["source_path"], []) if f != current_file]
        card["related_files"] = related[:20]


def build_prototype_ref_cards(
    instance_name: str, refs: list[dict[str, str]]
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for ref in refs:
        direct_file = find_first_prototype_path_by_id(instance_name, ref["id"])
        cards.append(
            {
                "key": ref["key"],
                "id": ref["id"],
                "direct_file": direct_file,
            }
        )
    return cards
    


# 🔹 Loader that ignores unknown YAML tags (SS14 safe)
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


def extract_prototypes(node):
    """Recursively find all dicts with id + type."""
    stack = [node]

    while stack:
        current = stack.pop()

        if isinstance(current, dict):
            if "id" in current and "type" in current:
                yield current

            for v in current.values():
                stack.append(v)

        elif isinstance(current, list):
            stack.extend(current)


def scan_instance_ids(instance_name: str, root_path: str):
    proto_root = Path(root_path) / "Resources" / "Prototypes"
    print("Scanning:", proto_root, proto_root.exists())

    with get_db() as conn:
        conn.execute("DELETE FROM prototype_ids WHERE instance_name = ?", (instance_name,))
        conn.execute("DELETE FROM prototype_components WHERE instance_name = ?", (instance_name,))
        conn.execute("DELETE FROM prototype_component_fields WHERE instance_name = ?", (instance_name,))

        count = 0
        seen = set()  # prevent duplicates like old version

        for path in proto_root.rglob("*.yml"):
            rel_path = path.relative_to(proto_root).as_posix()

            try:
                text = path.read_text(encoding="utf-8")
                text = text.replace("\t", "    ")
                docs = list(yaml.load_all(text, Loader=IgnoreTagsLoader))
            except Exception as e:
                print("YAML error:", path, e)
                continue

            for doc in docs:
                stack = [doc]

                while stack:
                    current = stack.pop()

                    if isinstance(current, dict):
                        proto_id = current.get("id")
                        proto_type = current.get("type")

                        # 🔥 Only accept REAL prototypes
                        if isinstance(proto_id, str) and isinstance(proto_type, str):
                            # Try to get type, fallback if missing
                            proto_type = current.get("type") or "unknown"

                            key = (instance_name, proto_id)

                            if key not in seen:
                                seen.add(key)

                                conn.execute("""
                                    INSERT OR REPLACE INTO prototype_ids
                                    (instance_name, proto_id, proto_type, rel_path)
                                    VALUES (?, ?, ?, ?)
                                """, (instance_name, proto_id, proto_type, rel_path))

                                count += 1

                            # 🔹 Components (only if it's a real prototype-like object)
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
                                    """, (
                                        instance_name,
                                        proto_id,
                                        comp_type,
                                        json.dumps(comp)
                                    ))

                                    for key_name, value in comp.items():
                                        if key_name == "type":
                                            continue

                                        if isinstance(value, (str, int, float, bool)):
                                            conn.execute("""
                                                INSERT OR REPLACE INTO prototype_component_fields
                                                (instance_name, proto_id, component_type, field_name, field_value)
                                                VALUES (?, ?, ?, ?, ?)
                                            """, (
                                                instance_name,
                                                proto_id,
                                                comp_type,
                                                key_name,
                                                str(value)
                                            ))

                                        if comp_type == "CartridgeAmmo" and key_name == "proto":
                                            conn.execute("""
                                                INSERT OR REPLACE INTO prototype_component_fields
                                                (instance_name, proto_id, component_type, field_name, field_value)
                                                VALUES (?, ?, ?, 'proto_fk', ?)
                                            """, (
                                                instance_name,
                                                proto_id,
                                                comp_type,
                                                value
                                            ))

                        # continue traversal
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


def get_instance_stats(instance_name: str) -> dict:
    with get_db() as conn:
        cur = conn.cursor()

        # Total rows (includes duplicates across files)
        cur.execute("""
            SELECT COUNT(*) FROM prototype_ids
            WHERE instance_name = ?
        """, (instance_name,))
        total_rows = cur.fetchone()[0]

        # Unique IDs
        cur.execute("""
            SELECT COUNT(DISTINCT proto_id) FROM prototype_ids
            WHERE instance_name = ?
        """, (instance_name,))
        unique_ids = cur.fetchone()[0]

        # Components
        cur.execute("""
            SELECT COUNT(*) FROM prototype_components
            WHERE instance_name = ?
        """, (instance_name,))
        component_count = cur.fetchone()[0]

        # Fields
        cur.execute("""
            SELECT COUNT(*) FROM prototype_component_fields
            WHERE instance_name = ?
        """, (instance_name,))
        field_count = cur.fetchone()[0]

        # Type breakdown
        cur.execute("""
            SELECT proto_type, COUNT(*) 
            FROM prototype_ids
            WHERE instance_name = ?
            GROUP BY proto_type
            ORDER BY COUNT(*) DESC
        """, (instance_name,))
        types = cur.fetchall()

        # Last scan
        cur.execute("""
            SELECT scanned_at, id_count
            FROM instance_scan
            WHERE instance_name = ?
        """, (instance_name,))
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


def search_ids(instance_name: str, query: str) -> list[dict[str, str]]:
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


def get_instance_custom_dir(instance_name: str) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT custom_dir FROM instance_settings WHERE instance_name = ?",
            (instance_name,),
        ).fetchone()
    return row["custom_dir"] if row else ""


def set_instance_custom_dir(instance_name: str, custom_dir: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO instance_settings (instance_name, custom_dir) VALUES (?, ?) "
            "ON CONFLICT(instance_name) DO UPDATE SET custom_dir=excluded.custom_dir",
            (instance_name, custom_dir),
        )


def custom_prototypes_root(instance: dict[str, str], custom_dir: str) -> Path:
    prototypes_root = Path(instance["root_path"]) / "Resources" / "Prototypes"
    return safe_join(prototypes_root, custom_dir)


def extract_cargo_products(docs: list[Any]) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    stack = [docs]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if current.get("type") == "cargoProduct":
                icon = current.get("icon") if isinstance(current.get("icon"), dict) else {}
                products.append(
                    {
                        "id": str(current.get("id", "")),
                        "product": str(current.get("product", "")),
                        "cost": current.get("cost"),
                        "category": str(current.get("category", "")),
                        "group": str(current.get("group", "")),
                        "icon_sprite": str(icon.get("sprite", "")),
                        "icon_state": str(icon.get("state", "icon")),
                    }
                )
            for value in current.values():
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return products


def default_cargo_form_data() -> dict[str, Any]:
    return {
        "cargo_file": "cargo_new.yml",
        "cargo_id": "",
        "icon_sprite": "",
        "icon_state": "icon",
        "product_id": "",
        "cost": 1000,
        "category": "cargoproduct-category-name-emergency",
        "group": "market",
        "crate_file": "new.yml",
        "crate_parent": "CrateCommandSecure",
        "crate_id": "",
        "crate_name": "",
        "crate_description": "",
        "entity_items": [],
    }


def parse_cargo_form_request(req: Any) -> dict[str, Any]:
    raw_ids = req.form.getlist("entity_ids")
    raw_amounts = req.form.getlist("entity_amounts")
    entity_items: list[dict[str, Any]] = []
    for i, raw_id in enumerate(raw_ids):
        entity_id = raw_id.strip()
        if not entity_id:
            continue
        raw_amount = raw_amounts[i] if i < len(raw_amounts) else "1"
        try:
            amount = max(1, int(raw_amount or "1"))
        except ValueError:
            amount = 1
        entity_items.append({"id": entity_id, "amount": amount})
    product_id = req.form.get("product_id", "").strip()
    crate_id = req.form.get("crate_id", "").strip() or product_id
    return {
        "cargo_file": normalize_yaml_filename(req.form.get("cargo_file", "cargo_new.yml")),
        "cargo_id": req.form.get("cargo_id", "").strip(),
        "icon_sprite": req.form.get("icon_sprite", "").strip(),
        "icon_state": req.form.get("icon_state", "icon").strip() or "icon",
        "product_id": product_id,
        "cost": int(req.form.get("cost", "0") or 0),
        "category": req.form.get("category", "").strip(),
        "group": req.form.get("group", "").strip(),
        "crate_file": normalize_yaml_filename(req.form.get("crate_file", "new.yml")),
        "crate_parent": req.form.get("crate_parent", "CrateCommandSecure").strip(),
        "crate_id": crate_id,
        "crate_name": req.form.get("crate_name", "").strip(),
        "crate_description": req.form.get("crate_description", "").strip(),
        "entity_items": entity_items,
    }


def normalize_yaml_filename(value: str) -> str:
    clean = value.strip().replace("\\", "/").lstrip("/")
    if not clean.lower().endswith((".yml", ".yaml")):
        clean += ".yml"
    return clean


def render_cargo_yaml(data: dict[str, Any]) -> str:
    return (
        "- type: cargoProduct\n"
        f"  id: {data['cargo_id']}\n"
        "  icon:\n"
        f"    sprite: {data['icon_sprite']}\n"
        f"    state: {data['icon_state']}\n"
        f"  product: {data['product_id']}\n"
        f"  cost: {data['cost']}\n"
        f"  category: {data['category']}\n"
        f"  group: {data['group']}\n"
    )


def render_crate_yaml(data: dict[str, Any]) -> str:
    children = "".join(
        [f"        - id: {x['id']}\n          amount: {x['amount']}\n" for x in data["entity_items"]]
    )
    return (
        "- type: entity\n"
        f"  parent: {data['crate_parent']}\n"
        f"  id: {data['crate_id']}\n"
        f"  name: {data['crate_name']}\n"
        f"  description: {data['crate_description']}\n"
        "  components:\n"
        "  - type: EntityTableContainerFill\n"
        "    containers:\n"
        "      entity_storage: !type:AllSelector\n"
        "        children:\n"
        f"{children}"
    )


def load_cargo_form_data(root: Path, cargo_file_rel: str) -> dict[str, Any] | None:
    cargo_file = safe_join(root / "Catalog" / "Cargo", cargo_file_rel)
    if not cargo_file.exists():
        return None
    try:
        docs = load_yaml_documents(cargo_file)
    except Exception:
        return None
    product = first_cargo_product(docs)
    if not product:
        return None
    crate_file_rel = find_crate_file_by_entity_id(root / "Catalog" / "Fills" / "Crates", str(product.get("product", "")))
    crate_data = load_crate_data(root, crate_file_rel) if crate_file_rel else {}
    return {
        "cargo_file": cargo_file_rel,
        "cargo_id": str(product.get("id", "")),
        "icon_sprite": str((product.get("icon") or {}).get("sprite", "")),
        "icon_state": str((product.get("icon") or {}).get("state", "icon")),
        "product_id": str(product.get("product", "")),
        "cost": int(product.get("cost", 0) or 0),
        "category": str(product.get("category", "")),
        "group": str(product.get("group", "")),
        "crate_file": crate_file_rel or "new.yml",
        "crate_parent": crate_data.get("crate_parent", "CrateCommandSecure"),
        "crate_id": crate_data.get("crate_id", str(product.get("product", ""))),
        "crate_name": crate_data.get("crate_name", ""),
        "crate_description": crate_data.get("crate_description", ""),
        "entity_items": crate_data.get("entity_items", []),
    }


def first_cargo_product(docs: list[Any]) -> dict[str, Any] | None:
    stack = [docs]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if current.get("type") == "cargoProduct":
                return current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return None


def find_crate_file_by_entity_id(crate_root: Path, entity_id: str) -> str | None:
    if not entity_id or not crate_root.exists():
        return None
    for rel in list_prototype_files(crate_root):
        fp = safe_join(crate_root, rel)
        try:
            docs = load_yaml_documents(fp)
        except Exception:
            continue
        if entity_id in collect_proto_ids(docs):
            return rel
    return None


def load_crate_data(root: Path, crate_file_rel: str) -> dict[str, Any]:
    fp = safe_join(root / "Catalog" / "Fills" / "Crates", crate_file_rel)
    docs = load_yaml_documents(fp)
    stack = [docs]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if current.get("type") == "entity":
                entity_items: list[dict[str, Any]] = []
                components = current.get("components", [])
                if isinstance(components, list):
                    for comp in components:
                        if isinstance(comp, dict) and comp.get("type") == "EntityTableContainerFill":
                            containers = comp.get("containers", {})
                            storage = containers.get("entity_storage", {}) if isinstance(containers, dict) else {}
                            children = storage.get("children", []) if isinstance(storage, dict) else []
                            if isinstance(children, list):
                                for child in children:
                                    if isinstance(child, dict) and isinstance(child.get("id"), str):
                                        try:
                                            child_amount = int(child.get("amount", 1))
                                        except Exception:
                                            child_amount = 1
                                        entity_items.append({"id": child["id"], "amount": max(1, child_amount)})
                return {
                    "crate_parent": str(current.get("parent", "CrateCommandSecure")),
                    "crate_id": str(current.get("id", "")),
                    "crate_name": str(current.get("name", "")),
                    "crate_description": str(current.get("description", "")),
                    "entity_items": entity_items,
                }
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return {}

def copy_metadata(mp3_path: Path, ogg_path: Path):
    try:
        mp3_meta = MP3(mp3_path, ID3=ID3)
        ogg_meta = OggVorbis(ogg_path)

        for tag in mp3_meta:
            if tag in ["TIT2", "TPE1", "TALB"]:
                ogg_meta[tag] = mp3_meta[tag].text[0]

        ogg_meta.save()
    except Exception:
        pass

def load_jukebox_data_custom(root_path: Path, custom_dir: str) -> tuple[list[dict], list[dict]]:
    """Load jukebox data ONLY from a specific custom_dir."""
    audio_root = root_path / "Resources" / "Audio"

    if custom_dir:
        audio_root = audio_root / custom_dir

    jukebox_dirs: list[dict] = []
    all_tracks: list[dict] = []

    if not audio_root.exists():
        return jukebox_dirs, all_tracks

    for jukebox_dir in sorted(audio_root.rglob("Jukebox")):
        if not jukebox_dir.is_dir():
            continue

        attr_file = jukebox_dir / "attributions.yml"
        ogg_files = sorted([f.name for f in jukebox_dir.glob("*.ogg")])

        if not attr_file.exists() and not ogg_files:
            continue

        # --- Load attribution ---
        attributions: list[dict] = []
        if attr_file.exists():
            try:
                content = attr_file.read_text(encoding="utf-8")
                attr_data = yaml.safe_load(content) or []
                if isinstance(attr_data, list):
                    attributions = attr_data
                elif isinstance(attr_data, dict):
                    attributions = [attr_data]
            except Exception:
                pass

        # --- Index attribution by file ---
        attr_by_file: dict[str, dict] = {}
        for attr in attributions:
            if isinstance(attr, dict):
                for fname in attr.get("files", []):
                    if isinstance(fname, str):
                        attr_by_file[fname] = {
                            "license": attr.get("license", "Unknown"),
                            "copyright": attr.get("copyright", "Unknown"),
                            "source": attr.get("source", "Unknown"),
                        }

        tracks: list[dict] = []

        # Path relative to Audio root (keeps compatibility with your URLs)
        rel_path = jukebox_dir.relative_to(root_path / "Resources" / "Audio").as_posix()

        for i, ogg_file in enumerate(ogg_files):
            attr_info = attr_by_file.get(ogg_file, {})

            track = {
                "id": f"{rel_path.replace('/', '_')}_{i}",
                "filename": ogg_file,
                "title": ogg_file.removesuffix(".ogg"),
                "path": f"/{rel_path}/{ogg_file}",  # important: same format as before
                "license": attr_info.get("license", "Unknown"),
                "copyright": attr_info.get("copyright", "Unknown"),
                "source": attr_info.get("source", "Unknown"),
            }

            tracks.append(track)
            all_tracks.append(track)

        jukebox_dirs.append({
            "name": rel_path,
            "track_count": len(tracks),
            "tracks": tracks,
        })

    return jukebox_dirs, all_tracks

def load_yaml_file(path: Path):
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or []
    return []

def save_yaml_file(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def build_jukebox_entry(filename: str, custom_dir: str):
    stem = Path(filename).stem

    return {
        "type": "jukebox",
        "id": stem.replace(" ", "_"),
        "name": stem.replace("_", " "),
        "path": {
            "path": f"/Audio/{custom_dir}/Jukebox/{filename}" if custom_dir else f"/Audio/Jukebox/{filename}"
        }
    }

def load_jukebox_data(root_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load jukebox directories and their music tracks from Audio resources."""
    audio_root = root_path / "Resources" / "Audio"
    jukebox_dirs: list[dict[str, Any]] = []
    all_tracks: list[dict[str, Any]] = []

    if not audio_root.exists():
        return jukebox_dirs, all_tracks

    # Recursively find all "Jukebox" directories under Resources/Audio
    for jukebox_dir in sorted(audio_root.rglob("Jukebox")):
        if not jukebox_dir.is_dir():
            continue

        attr_file = jukebox_dir / "attributions.yml"
        ogg_files = sorted([f.name for f in jukebox_dir.glob("*.ogg")])

        if not attr_file.exists() and not ogg_files:
            continue

        # Load attribution data
        attributions: list[dict[str, Any]] = []
        if attr_file.exists():
            try:
                content = attr_file.read_text(encoding="utf-8")
                attr_data = yaml.load(content, Loader=IgnoreUnknownTagLoader) or []
                if isinstance(attr_data, list):
                    attributions = attr_data
                elif isinstance(attr_data, dict):
                    attributions = [attr_data]
            except Exception:
                attributions = []

        # Build tracks with attribution info
        tracks: list[dict[str, Any]] = []
        attr_by_file: dict[str, dict[str, Any]] = {}

        # Index attributions by file
        for attr in attributions:
            if isinstance(attr, dict):
                files = attr.get("files", [])
                if isinstance(files, list):
                    for fname in files:
                        if isinstance(fname, str):
                            attr_by_file[fname] = {
                                "license": attr.get("license", "Unknown"),
                                "copyright": attr.get("copyright", "Unknown"),
                                "source": attr.get("source", "Unknown"),
                            }

        # Create track entries with relative path for display
        rel_path = jukebox_dir.relative_to(audio_root).as_posix()
        for i, ogg_file in enumerate(ogg_files):
            attr_info = attr_by_file.get(ogg_file, {
                "license": "Unknown",
                "copyright": "Unknown",
                "source": "Unknown",
            })
            tracks.append({
                "id": f"{rel_path.replace('/', '_')}_{i}",
                "filename": ogg_file,
                "title": ogg_file.removesuffix(".ogg"),
                "path": f"/{rel_path}/{ogg_file}",
                "license": attr_info.get("license", "Unknown"),
                "copyright": attr_info.get("copyright", "Unknown"),
                "source": attr_info.get("source", "Unknown"),
            })
            all_tracks.append(tracks[-1])

        jukebox_dirs.append({
            "name": rel_path,
            "track_count": len(tracks),
            "tracks": tracks,
        })

    return jukebox_dirs, all_tracks


def build_rsi_tree_recursive(textures_root: Path, base_path: Path) -> list[dict]:
    items = []

    for item in sorted(base_path.iterdir()):
        rel_path = item.relative_to(textures_root)

        if item.is_dir():
            # 👉 Detect RSI folder
            if item.suffix == ".rsi":
                pngs = list(item.glob("*.png"))
                states = [p.stem for p in pngs]

                items.append({
                    "type": "file",  # leaf node
                    "name": item.name,
                    "path": str(rel_path).replace("\\", "/"),
                    "states": states,
                    "hover_exists": len(states) > 0
                })
            else:
                children = build_rsi_tree_recursive(textures_root, item)
                if children:
                    items.append({
                        "type": "dir",
                        "name": item.name,
                        "children": children
                    })

    return items


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    init_db()

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "selected_instance_name": session.get("selected_instance"),
            "instances_count": len(load_instances()),
        }

    # Register blueprints for modular routing
    from routes import register_blueprints
    register_blueprints(app)

    @app.errorhandler(Exception)
    def handle_exception(e):
        import traceback
        tb = traceback.format_exc()

        return render_template(
            "error.html",
            error_type=type(e).__name__,
            error_message=str(e),
            traceback=tb,
            highlight=str(e)  # we’ll use this to emphasize key parts
        ), 500

    return app


if __name__ == "__main__":
    app = create_app()
    try:
        app.run(
            host=os.getenv("FLASK_RUN_HOST", "127.0.0.1"),
            port=int(os.getenv("FLASK_RUN_PORT", "5000")),
            debug=_env_bool("FLASK_DEBUG", True),
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")
