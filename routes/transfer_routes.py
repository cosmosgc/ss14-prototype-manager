from flask import Blueprint, abort, flash, jsonify, render_template, request, redirect, url_for
from pathlib import Path
import yaml
import shutil
import json

from app import (
    selected_instance_or_400, safe_join, get_db, find_first_prototype_path_by_id,
    find_prototype_paths_by_id, load_yaml_documents, collect_sprite_refs,
    collect_audio_refs, collect_prototype_like_refs, list_rsi_states,
    build_sprite_cards, build_audio_cards, build_prototype_ref_cards,
    custom_prototypes_root, get_instance_custom_dir, extract_prototypes,
    IgnoreUnknownTagLoader, resolve_preview_for_prototype_id
)

transfer_bp = Blueprint("transfer", __name__, url_prefix="/transfer")


@transfer_bp.route("/check-compatibility")
def check_compatibility():
    """Check if a prototype can be transferred to another instance"""
    from app import load_instances

    selected = selected_instance_or_400()
    proto_id = request.args.get("proto_id", "").strip()
    target_instance_name = request.args.get("target_instance", "").strip()

    if not proto_id or not target_instance_name:
        return jsonify({"compatible": False, "errors": ["Missing parameters"]})

    instances = load_instances()
    target_instance = None
    for inst in instances:
        if inst["name"] == target_instance_name:
            target_instance = inst
            break

    if not target_instance:
        return jsonify({"compatible": False, "errors": ["Target instance not found"]})

    if target_instance["name"] == selected["name"]:
        return jsonify({"compatible": False, "errors": ["Source and target instances are the same"]})

    # Check if prototype exists in source
    source_path = find_first_prototype_path_by_id(selected["name"], proto_id)
    if not source_path:
        return jsonify({"compatible": False, "errors": [f"Prototype {proto_id} not found in source instance"]})

    # Check if prototype already exists in target
    target_existing = find_first_prototype_path_by_id(target_instance["name"], proto_id)
    errors = []

    if target_existing:
        errors.append(f"Prototype ID {proto_id} already exists in target instance at {target_existing}")

    # Load the prototype document
    source_proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
    source_file = safe_join(source_proto_root, source_path)

    try:
        docs = load_yaml_documents(source_file)
    except Exception as e:
        return jsonify({"compatible": False, "errors": [f"Failed to load source YAML: {str(e)}"]})

    proto_doc = None
    for doc in docs:
        if isinstance(doc, dict) and doc.get("id") == proto_id:
            proto_doc = doc
            break

    # if not proto_doc:
    #     return jsonify({"compatible": False, "errors": [f"Prototype {proto_id} not found in file"]})

    # Check all referenced prototype IDs exist in target
    proto_refs = collect_prototype_like_refs(proto_doc)
    missing_protos = []
    for ref in proto_refs:
        target_ref_path = find_first_prototype_path_by_id(target_instance["name"], ref["id"])
        if not target_ref_path:
            missing_protos.append(ref["id"])

    if missing_protos:
        errors.append(f"Missing prototype references in target: {', '.join(missing_protos)}")

    # Check all referenced sprites (RSI) exist in target
    sprite_refs = collect_sprite_refs(proto_doc)
    missing_sprites = []
    target_textures_root = Path(target_instance["root_path"]) / "Resources" / "Textures"

    for sprite in sprite_refs:
        rsi_dir = safe_join(target_textures_root, sprite)
        if not rsi_dir or not rsi_dir.exists():
            missing_sprites.append(sprite)

    if missing_sprites:
        errors.append(f"Missing RSI files in target: {', '.join(missing_sprites[:5])}{'...' if len(missing_sprites) > 5 else ''}")

    # Check all referenced audio exists in target
    audio_refs = collect_audio_refs(proto_doc)
    missing_audio = []
    target_audio_root = Path(target_instance["root_path"]) / "Resources" / "Audio"

    for audio in audio_refs:
        audio_rel = audio.removeprefix("/Audio/")
        audio_path = safe_join(target_audio_root, audio_rel)
        if not audio_path or not audio_path.exists():
            missing_audio.append(audio)

    if missing_audio:
        errors.append(f"Missing audio files in target: {', '.join(missing_audio[:5])}{'...' if len(missing_audio) > 5 else ''}")

    return jsonify({
        "compatible": len(errors) == 0,
        "errors": errors,
        "missing_sprites": missing_sprites,
        "missing_audio": missing_audio,
        "missing_protos": missing_protos,
        "proto_doc": proto_doc,
        "source_path": source_path
    })


@transfer_bp.route("/transfer", methods=["POST"])
def transfer_prototype():
    from app import load_instances

    selected = selected_instance_or_400()
    proto_id = request.form.get("proto_id", "").strip()
    rel_file = request.form.get("rel_file", "").strip()  # 🔥 NEW
    target_instance_name = request.form.get("target_instance", "").strip()

    if not proto_id or not target_instance_name or not rel_file:
        flash("Missing parameters.", "error")
        return redirect(request.referrer or url_for("prototype.prototypes"))

    # 🔹 Find target instance
    instances = load_instances()
    target_instance = next((i for i in instances if i["name"] == target_instance_name), None)

    if not target_instance:
        flash("Target instance not found.", "error")
        return redirect(request.referrer or url_for("prototype.prototypes"))

    # 🔹 Use rel_file directly (NO MORE GUESSING)
    source_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
    source_file = safe_join(source_root, rel_file)

    if not source_file or not source_file.exists():
        flash("Source file not found.", "error")
        return redirect(request.referrer or url_for("prototype.prototypes"))

    # 🔥 Read RAW text
    try:
        text = source_file.read_text(encoding="utf-8")
    except Exception as e:
        flash(f"Failed to read source file: {e}", "error")
        return redirect(request.referrer or url_for("prototype.prototypes"))

    # 🔹 Extract ONLY this prototype block
    proto_block = extract_single_prototype_block(text, proto_id)

    if not proto_block:
        flash(f"Could not extract prototype {proto_id}", "error")
        return redirect(request.referrer or url_for("prototype.prototypes"))

    # 🔹 Prepare target file (same relative path)
    target_root = Path(target_instance["root_path"]) / "Resources" / "Prototypes"
    target_file = safe_join(target_root, rel_file)
    target_file.parent.mkdir(parents=True, exist_ok=True)

    existing_text = ""
    if target_file.exists():
        existing_text = target_file.read_text(encoding="utf-8")

        # Prevent duplicate
        import re
        if re.search(rf"\bid:\s*{re.escape(proto_id)}\b", existing_text):
            flash(f"{proto_id} already exists in target file.", "error")
            return redirect(request.referrer or url_for("prototype.prototypes"))

    # 🔹 Append safely
    new_content = existing_text.strip()
    if new_content:
        new_content += "\n\n" + proto_block + "\n"
    else:
        new_content = proto_block + "\n"

    try:
        target_file.write_text(new_content, encoding="utf-8", newline="\n")
    except Exception as e:
        flash(f"Failed to write file: {e}", "error")
        return redirect(request.referrer or url_for("prototype.prototypes"))

    # 🔹 Parse for asset extraction (ONLY for copying assets)
    docs = load_yaml_documents(source_file)

    proto_doc = None
    for doc in docs:
        for proto in extract_prototypes(doc):
            if proto.get("id") == proto_id:
                proto_doc = proto
                break
        if proto_doc:
            break

    # 🔹 Copy RSI
    sprite_refs = collect_sprite_refs(proto_doc or {})
    source_textures = Path(selected["root_path"]) / "Resources" / "Textures"
    target_textures = Path(target_instance["root_path"]) / "Resources" / "Textures"

    copied_sprites = []
    for sprite in sprite_refs:
        src = safe_join(source_textures, sprite)
        dst = safe_join(target_textures, sprite)

        if src and src.exists() and src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            copied_sprites.append(sprite)

    # 🔹 Copy audio (same logic style)
    audio_refs = collect_audio_refs(proto_doc or {})
    source_audio = Path(selected["root_path"]) / "Resources" / "Audio"
    target_audio = Path(target_instance["root_path"]) / "Resources" / "Audio"

    copied_audio = []
    for audio in audio_refs:
        rel = audio.removeprefix("/Audio/")
        src = safe_join(source_audio, rel)
        dst = safe_join(target_audio, rel)

        if src and src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_audio.append(audio)

    flash(
        f"Transferred {proto_id} → {target_instance_name}. "
        f"{len(copied_sprites)} RSI, {len(copied_audio)} audio copied.",
        "success"
    )

    return redirect(request.referrer or url_for("prototype.prototypes"))
    
@transfer_bp.route("/bulk-check")
def bulk_check():
    """Check compatibility for all prototypes in a file"""
    from app import load_instances

    selected = selected_instance_or_400()
    rel_file = request.args.get("file", "").strip()
    target_instance_name = request.args.get("target_instance", "").strip()

    if not rel_file or not target_instance_name:
        return jsonify({"compatible": False, "errors": ["Missing parameters"]})

    instances = load_instances()
    target_instance = None
    for inst in instances:
        if inst["name"] == target_instance_name:
            target_instance = inst
            break

    if not target_instance:
        return jsonify({"compatible": False, "errors": ["Target instance not found"]})

    proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
    file_path = safe_join(proto_root, rel_file)

    try:
        docs = load_yaml_documents(file_path)
    except Exception as e:
        return jsonify({"compatible": False, "errors": [f"Failed to load file: {str(e)}"]})

    results = []
    for doc in docs:
        if isinstance(doc, dict) and "id" in doc and "type" in doc:
            proto_id = doc.get("id")
            if not proto_id:
                continue

            # Check if exists in target
            target_existing = find_first_prototype_path_by_id(target_instance["name"], proto_id)
            errors = []

            if target_existing:
                errors.append(f"Already exists at {target_existing}")

            # Check refs
            proto_refs = collect_prototype_like_refs(doc)
            for ref in proto_refs:
                target_ref_path = find_first_prototype_path_by_id(target_instance["name"], ref["id"])
                if not target_ref_path:
                    errors.append(f"Missing ref: {ref['id']}")

            sprite_refs = collect_sprite_refs(doc)
            for sprite in sprite_refs:
                target_textures_root = Path(target_instance["root_path"]) / "Resources" / "Textures"
                rsi_dir = safe_join(target_textures_root, sprite)
                if not rsi_dir or not rsi_dir.exists():
                    errors.append(f"Missing RSI: {sprite}")

            results.append({
                "id": proto_id,
                "type": doc.get("type"),
                "compatible": len(errors) == 0,
                "errors": errors
            })

    return jsonify({"prototypes": results})
    
def extract_single_prototype_block(text: str, proto_id: str) -> str | None:
    lines = text.splitlines()

    blocks = []
    current_block = []

    for line in lines:
        # New block starts at "- " at column 0
        if line.startswith("- "):
            if current_block:
                blocks.append("\n".join(current_block))
                current_block = []
        current_block.append(line)

    if current_block:
        blocks.append("\n".join(current_block))

    # Find correct block
    for block in blocks:
        if f"id: {proto_id}" in block:
            return block.strip()

    return None