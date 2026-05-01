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
    """Transfer a prototype to another instance"""
    from app import load_instances

    selected = selected_instance_or_400()
    proto_id = request.form.get("proto_id", "").strip()
    target_instance_name = request.form.get("target_instance", "").strip()

    if not proto_id or not target_instance_name:
        flash("Missing parameters.", "error")
        return redirect(request.referrer or url_for("prototype.prototypes"))

    instances = load_instances()
    target_instance = None
    for inst in instances:
        if inst["name"] == target_instance_name:
            target_instance = inst
            break

    if not target_instance:
        flash("Target instance not found.", "error")
        return redirect(request.referrer or url_for("prototype.prototypes"))

    # Get source prototype
    source_path = find_first_prototype_path_by_id(selected["name"], proto_id)
    if not source_path:
        flash(f"Prototype {proto_id} not found in source instance.", "error")
        return redirect(request.referrer or url_for("prototype.prototypes"))

    # Load source file
    source_proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
    source_file = safe_join(source_proto_root, source_path)

    try:
        docs = list(load_yaml_documents(source_file))
    except Exception as e:
        flash(f"Failed to load source YAML: {str(e)}", "error")
        return redirect(request.referrer or url_for("prototype.prototypes"))

    # Find the prototype to transfer
    proto_doc = None
    proto_index = -1
    for i, doc in enumerate(docs):
        if isinstance(doc, dict) and doc.get("id") == proto_id:
            proto_doc = doc
            proto_index = i
            break

    if proto_doc is None:
        flash(f"Prototype {proto_id} not found in file.", "error")
        return redirect(request.referrer or url_for("prototype.prototypes"))

    # Determine target file path (same relative path)
    target_proto_root = Path(target_instance["root_path"]) / "Resources" / "Prototypes"
    target_file = safe_join(target_proto_root, source_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)

    # Check if prototype already exists in target and remove it (for update)
    existing_target_path = find_first_prototype_path_by_id(target_instance["name"], proto_id)
    if existing_target_path:
        # Remove from existing file
        try:
            existing_file = safe_join(target_proto_root, existing_target_path)
            existing_docs = list(load_yaml_documents(existing_file))
            existing_docs = [d for d in existing_docs if not (isinstance(d, dict) and d.get("id") == proto_id)]
            with existing_file.open("w", encoding="utf-8", newline="\n") as f:
                yaml.dump_all(existing_docs, f, default_flow_style=False, allow_unicode=True)
        except Exception as e:
            flash(f"Warning: Failed to remove existing prototype: {str(e)}", "error")

    # Add prototype to target file
    if target_file.exists():
        try:
            target_docs = list(load_yaml_documents(target_file))
        except Exception:
            target_docs = []
    else:
        target_docs = []

    target_docs.append(proto_doc)

    # Save to target file
    try:
        with target_file.open("w", encoding="utf-8", newline="\n") as f:
            yaml.dump_all(target_docs, f, default_flow_style=False, allow_unicode=True)
    except Exception as e:
        flash(f"Failed to save to target: {str(e)}", "error")
        return redirect(request.referrer or url_for("prototype.prototypes"))

    # Copy missing RSI files
    sprite_refs = collect_sprite_refs(proto_doc)
    source_textures_root = Path(selected["root_path"]) / "Resources" / "Textures"
    target_textures_root = Path(target_instance["root_path"]) / "Resources" / "Textures"

    copied_sprites = []
    for sprite in sprite_refs:
        source_rsi = safe_join(source_textures_root, sprite)
        target_rsi = safe_join(target_textures_root, sprite)

        if source_rsi and source_rsi.exists() and source_rsi.is_dir():
            if target_rsi.exists():
                shutil.rmtree(target_rsi)
            shutil.copytree(source_rsi, target_rsi)
            copied_sprites.append(sprite)

    # Copy missing audio files
    audio_refs = collect_audio_refs(proto_doc)
    source_audio_root = Path(selected["root_path"]) / "Resources" / "Audio"
    target_audio_root = Path(target_instance["root_path"]) / "Resources" / "Audio"

    copied_audio = []
    for audio in audio_refs:
        audio_rel = audio.removeprefix("/Audio/")
        source_audio = safe_join(source_audio_root, audio_rel)
        target_audio = safe_join(target_audio_root, audio_rel)

        if source_audio and source_audio.exists():
            if target_audio:
                target_audio.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_audio, target_audio)
                copied_audio.append(audio)

    # Re-scan target instance
    from app import scan_instance_ids
    scan_instance_ids(target_instance["name"], target_instance["root_path"])

    flash(f"Successfully transferred {proto_id} to {target_instance_name}. Copied {len(copied_sprites)} RSI(s) and {len(copied_audio)} audio file(s).", "success")
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
