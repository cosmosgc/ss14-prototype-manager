from __future__ import annotations

import io
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

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
)
from PIL import Image
import yaml


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


INSTANCES_FILE = Path(os.getenv("INSTANCES_FILE", str(DATA_DIR / "instances.json")))
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
    ensure_instances_file_exists()

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

        instances.append({"name": name, "root_path": str(Path(root))})
        save_instances(instances)
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
        instances = load_instances()
        filtered = [i for i in instances if i["name"] != name]
        if len(filtered) == len(instances):
            abort(404)
        save_instances(filtered)
        if session.get("selected_instance") == name:
            session.pop("selected_instance", None)
        flash(f"Deleted instance: {name}", "success")
        return redirect(url_for("index"))

    @app.route("/prototypes")
    def prototypes():
        requested_instance = request.args.get("instance", "").strip()
        if requested_instance:
            instances = load_instances()
            match = get_instance_by_name(requested_instance, instances)
            if not match:
                abort(404, "Instance not found.")
            session["selected_instance"] = match["name"]
        selected = selected_instance_or_400()
        proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
        query = request.args.get("q", "").strip().lower()
        files = list_prototype_files(proto_root)
        if query:
            files = [f for f in files if query in f.lower()]
        file_entries = build_file_entries(proto_root, files, Path(selected["root_path"]))
        tree = build_tree(file_entries)
        return render_template("prototypes.html", tree=tree, query=query, selected=selected)

    @app.route("/prototype/view", methods=["GET", "POST"])
    def prototype_view():
        selected = selected_instance_or_400()
        rel_file = request.args.get("file", "").strip()
        if not rel_file:
            abort(400)
        proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
        file_path = safe_join(proto_root, rel_file)
        if request.method == "POST":
            new_content = request.form.get("content", "")
            ok, error = validate_yaml_text(new_content)
            if not ok:
                flash(f"YAML parse error: {error}", "error")
            else:
                file_path.write_text(new_content, encoding="utf-8")
                flash("Prototype saved.", "success")
            return redirect(url_for("prototype_view", file=rel_file))

        raw_text = file_path.read_text(encoding="utf-8")
        docs = load_yaml_documents(file_path)
        sprite_refs = collect_sprite_refs(docs)
        sprite_state_pairs = collect_sprite_state_pairs(docs)
        audio_refs = collect_audio_refs(docs)
        prototype_refs = collect_prototype_like_refs(docs)
        sprite_cards = build_sprite_cards(Path(selected["root_path"]), sprite_refs, sprite_state_pairs)
        audio_cards = build_audio_cards(Path(selected["root_path"]), audio_refs)
        prototype_ref_cards = build_prototype_ref_cards(prototype_refs)
        _, parse_error = validate_yaml_text(raw_text)

        return render_template(
            "prototype_view.html",
            rel_file=rel_file,
            docs=docs,
            raw_text=raw_text,
            parse_error=parse_error,
            sprite_cards=sprite_cards,
            audio_cards=audio_cards,
            prototype_ref_cards=prototype_ref_cards,
            selected=selected,
        )

    @app.get("/prototype/by-id")
    def prototype_by_id():
        selected = selected_instance_or_400()
        proto_id = request.args.get("id", "").strip()
        if not proto_id:
            abort(400, "Missing id.")
        proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
        index = build_prototype_index(proto_root)
        files = index["id_to_files"].get(proto_id, [])
        if not files:
            flash(f"Prototype id not found: {proto_id}", "error")
            return redirect(url_for("prototypes", q=proto_id))
        return redirect(url_for("prototype_view", file=files[0]))

    @app.get("/sprite/preview")
    def sprite_preview():
        selected = selected_instance_or_400()
        sprite = request.args.get("sprite", "").strip()
        state = request.args.get("state", "icon").strip()
        scale = int(request.args.get("scale", str(DEFAULT_THUMB_SCALE)))
        if scale < 1 or scale > 16:
            abort(400)

        textures_root = Path(selected["root_path"]) / "Resources" / "Textures"
        sprite_dir = safe_join(textures_root, sprite)
        image_path = safe_join(sprite_dir, f"{state}.png")
        if not image_path.exists():
            abort(404)

        with Image.open(image_path) as im:
            im = im.convert("RGBA")
            out = im.resize((im.width * scale, im.height * scale), Image.Resampling.NEAREST)
            buffer = io.BytesIO()
            out.save(buffer, format="PNG")
            buffer.seek(0)
            return send_file(buffer, mimetype="image/png")

    @app.get("/audio/play")
    def audio_play():
        selected = selected_instance_or_400()
        rel = request.args.get("path", "").strip()
        if not rel:
            abort(400)
        audio_root = Path(selected["root_path"]) / "Resources" / "Audio"
        file_path = safe_join(audio_root, rel)
        if not file_path.exists():
            abort(404)
        return send_file(file_path)

    @app.get("/open-explorer")
    def open_explorer():
        selected = selected_instance_or_400()
        target = request.args.get("target", "").strip().lower()
        referrer = request.args.get("back") or request.referrer or url_for("index")
        root = Path(selected["root_path"])

        path: Path | None = None
        select_file = False
        if target == "yml":
            rel_file = request.args.get("file", "").strip()
            if not rel_file:
                abort(400)
            proto_root = root / "Resources" / "Prototypes"
            path = safe_join(proto_root, rel_file)
            select_file = True
        elif target == "yml-vscode":
            rel_file = request.args.get("file", "").strip()
            if not rel_file:
                abort(400)
            proto_root = root / "Resources" / "Prototypes"
            path = safe_join(proto_root, rel_file)
        elif target == "rsi":
            sprite = request.args.get("sprite", "").strip()
            if not sprite:
                abort(400)
            textures_root = root / "Resources" / "Textures"
            path = safe_join(textures_root, sprite)
        elif target == "audio":
            rel_audio = request.args.get("path", "").strip()
            if not rel_audio:
                abort(400)
            audio_root = root / "Resources" / "Audio"
            path = safe_join(audio_root, rel_audio)
            select_file = True
        else:
            abort(400, "Invalid target.")

        if not path.exists():
            flash("Path does not exist on disk.", "error")
            return redirect(referrer)

        if os.name != "nt":
            flash("Explorer opening is currently supported only on Windows.", "error")
            return redirect(referrer)

        if target == "yml-vscode":
            vscode_target = str(path.resolve())
            code_cmd = find_vscode_cli()
            if not code_cmd:
                flash(
                    f'VS Code CLI not found. Install "code" in PATH. Target: "{vscode_target}"',
                    "error",
                )
                return redirect(referrer)
            try:
                result = subprocess.run(
                    [code_cmd, "-g", vscode_target],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                if result.returncode == 0:
                    flash(f'Opened in VS Code: "{vscode_target}"', "success")
                else:
                    details = (result.stderr or result.stdout or "No output").strip()
                    flash(f'VS Code exit code {result.returncode}: {details}', "error")
            except FileNotFoundError:
                flash(
                    f'VS Code CLI not found. Install "code" in PATH. Target: "{vscode_target}"',
                    "error",
                )
            except subprocess.TimeoutExpired:
                flash("Timed out while trying to open VS Code.", "error")
            return redirect(referrer)
        if select_file and path.is_file():
            subprocess.Popen(["explorer.exe", "/select,", str(path)])
        else:
            subprocess.Popen(["explorer.exe", str(path)])

        return redirect(referrer)

    @app.get("/api/instances")
    def api_instances():
        return jsonify(load_instances())

    @app.get("/api/current-instance")
    def api_current_instance():
        selected = selected_instance_or_400()
        return jsonify(selected)

    return app


def load_instances() -> list[dict[str, str]]:
    ensure_instances_file_exists()
    try:
        data = json.loads(INSTANCES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [i for i in data if isinstance(i, dict) and "name" in i and "root_path" in i]
    except json.JSONDecodeError:
        pass
    return []


def ensure_instances_file_exists() -> None:
    if INSTANCES_FILE.exists():
        return
    INSTANCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTANCES_FILE.write_text("[]", encoding="utf-8")


def save_instances(instances: list[dict[str, str]]) -> None:
    INSTANCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTANCES_FILE.write_text(json.dumps(instances, indent=2), encoding="utf-8")


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
    docs = list(yaml.load_all(text, Loader=IgnoreUnknownTagLoader))
    return docs


def validate_yaml_text(text: str) -> tuple[bool, str | None]:
    try:
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
    refs: list[dict[str, str]]
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for ref in refs:
        cards.append(
            {
                "key": ref["key"],
                "id": ref["id"],
            }
        )
    return cards


app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_RUN_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_RUN_PORT", "5000")),
        debug=_env_bool("FLASK_DEBUG", True),
    )
