from flask import Blueprint, abort, flash, jsonify, render_template, request, url_for, redirect
from pathlib import Path
import yaml
import json
from app import (
    selected_instance_or_400, safe_join, load_yaml_documents, validate_yaml_text,
    get_db, find_first_prototype_path_by_id, collect_proto_ids, extract_prototypes,
    custom_prototypes_root, get_instance_custom_dir, scan_instance_ids,
    find_prototype_paths_by_id, resolve_preview_for_prototype_id
)

prototype_creator_bp = Blueprint("prototype_creator", __name__, url_prefix="/prototype-creator")

# Prototype type templates organized by category
PROTOTYPE_TEMPLATES = {
    "clothing_neck": {
        "name": "Neck Clothing",
        "category": "Clothing",
        "parent": "ClothingNeckBase",
        "target_dir": "Entities/Clothing/Neck",
        "default_filename": "cloaks.yml",
        "fields": [
            {"name": "id", "label": "Prototype ID", "required": True, "placeholder": "ClothingNeckCloakExample"},
            {"name": "name", "label": "Display Name", "required": True, "placeholder": "example cloak"},
            {"name": "suffix", "label": "Suffix", "default": "Delta-V"},
            {"name": "description", "label": "Description", "required": True, "type": "textarea"},
            {"name": "sprite", "label": "Sprite RSI Path", "required": True, "placeholder": "_DV/Clothing/Neck/Cloaks/example.rsi"},
            {"name": "steal_group", "label": "Steal Group", "default": "HeadCloak"},
            {"name": "has_steal_target", "label": "Add StealTarget Component", "type": "checkbox", "default": True},
        ],
        "components_template": [
            {"type": "Sprite", "sprite": "{sprite}"},
            {"type": "Clothing", "sprite": "{sprite}"},
        ]
    },
    "plushie": {
        "name": "Plushie",
        "category": "Items",
        "parent": "BasePlushie",
        "target_dir": "Entities/Plushies",
        "default_filename": "plushies.yml",
        "fields": [
            {"name": "id", "label": "Prototype ID", "required": True, "placeholder": "ToyPlushieExample"},
            {"name": "name", "label": "Display Name", "required": True},
            {"name": "description", "label": "Description", "required": True, "type": "textarea"},
            {"name": "sprite", "label": "Sprite RSI Path", "required": True},
        ],
        "components_template": [
            {"type": "Sprite", "sprite": "{sprite}"},
            {"type": "Item", "sprite": "{sprite}"},
        ]
    }
}

def get_prototype_categories():
    """Group templates by category for UI organization"""
    categories = {}
    for key, tmpl in PROTOTYPE_TEMPLATES.items():
        cat = tmpl["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append({"key": key, **tmpl})
    return categories

def generate_prototype_yaml(template_key, data):
    """Generate YAML document for a single prototype using template"""
    tmpl = PROTOTYPE_TEMPLATES.get(template_key)
    if not tmpl:
        return None
    
    # Build components list
    components = []
    for comp in tmpl["components_template"]:
        comp_copy = dict(comp)
        comp_copy["sprite"] = comp_copy["sprite"].format(sprite=data.get("sprite", ""))
        components.append(comp_copy)
    
    # Add StealTarget if requested
    if data.get("has_steal_target") and tmpl["key"] == "clothing_neck":
        components.append({
            "type": "StealTarget",
            "stealGroup": data.get("steal_group", "HeadCloak")
        })
    
    # Build prototype document
    proto = {
        "type": "entity",
        "parent": tmpl["parent"],
        "id": data["id"],
        "name": data["name"],
    }
    if data.get("suffix"):
        proto["suffix"] = data["suffix"]
    if data.get("description"):
        proto["description"] = data["description"]
    proto["components"] = components
    
    # Convert to YAML string
    return yaml.dump([proto], default_flow_style=False, allow_unicode=True)

def load_prototype_from_yaml(file_path, proto_id):
    """Load a single prototype document from YAML file by ID"""
    try:
        docs = load_yaml_documents(file_path)
        for doc in docs:
            if isinstance(doc, dict) and doc.get("id") == proto_id:
                return doc
    except Exception:
        pass
    return None

def update_prototype_in_yaml(file_path, proto_id, updated_doc):
    """Update an existing prototype in YAML file and save"""
    try:
        docs = list(load_yaml_documents(file_path))
        updated = False
        for i, doc in enumerate(docs):
            if isinstance(doc, dict) and doc.get("id") == proto_id:
                docs[i] = updated_doc
                updated = True
                break
        if not updated:
            return False
        
        # Write back all documents
        with file_path.open("w", encoding="utf-8", newline="\n") as f:
            yaml.dump_all(docs, f, default_flow_style=False, allow_unicode=True)
        return True
    except Exception:
        return False

def add_prototype_to_yaml(file_path, new_doc):
    """Add a new prototype document to YAML file"""
    try:
        docs = list(load_yaml_documents(file_path))
        docs.append(new_doc)
        with file_path.open("w", encoding="utf-8", newline="\n") as f:
            yaml.dump_all(docs, f, default_flow_style=False, allow_unicode=True)
        return True
    except Exception:
        return False

@prototype_creator_bp.route("/")
def index():
    """Dashboard showing prototype categories and creation options"""
    selected = selected_instance_or_400()
    categories = get_prototype_categories()
    
    # Get recent prototypes from DB for quick access
    with get_db() as conn:
        recent = conn.execute(
            "SELECT proto_id, proto_type, rel_path FROM prototype_ids "
            "WHERE instance_name = ? ORDER BY rowid DESC LIMIT 10",
            (selected["name"],)
        ).fetchall()
    
    return render_template(
        "prototype_creator/index.html",
        categories=categories,
        recent_prototypes=recent,
        selected=selected
    )

@prototype_creator_bp.route("/create", methods=["GET", "POST"])
def create_prototype():
    """Create new prototype (new YAML file or add to existing)"""
    selected = selected_instance_or_400()
    template_key = request.args.get("template", "")
    tmpl = PROTOTYPE_TEMPLATES.get(template_key)
    
    if request.method == "POST":
        # Process form data
        data = {
            "id": request.form.get("proto_id", "").strip(),
            "name": request.form.get("name", "").strip(),
            "suffix": request.form.get("suffix", "").strip(),
            "description": request.form.get("description", "").strip(),
            "sprite": request.form.get("sprite", "").strip(),
            "has_steal_target": request.form.get("has_steal_target") == "on",
            "steal_group": request.form.get("steal_group", "").strip(),
        }
        
        # Validate required fields
        if not data["id"] or not data["name"] or not data["sprite"]:
            flash("ID, Name and Sprite are required.", "error")
            return redirect(url_for("prototype_creator.create_prototype", template=template_key))
        
        # Determine target YAML file
        target_file = request.form.get("target_file", "").strip()
        new_filename = request.form.get("new_filename", "").strip()
        
        proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
        custom_dir = get_instance_custom_dir(selected["name"])
        target_dir = proto_root / custom_dir / tmpl["target_dir"] if custom_dir else proto_root / tmpl["target_dir"]
        target_dir.mkdir(parents=True, exist_ok=True)
        
        if new_filename:
            # Create new YAML file
            if not new_filename.endswith((".yml", ".yaml")):
                new_filename += ".yml"
            file_path = target_dir / new_filename
            target_file = str(file_path.relative_to(proto_root).as_posix())
        elif target_file:
            # Add to existing file
            file_path = safe_join(proto_root, target_file)
        else:
            # Default file in target dir
            file_path = target_dir / tmpl["default_filename"]
            target_file = str(file_path.relative_to(proto_root).as_posix())
        
        # Generate YAML for prototype
        yaml_str = generate_prototype_yaml(template_key, data)
        if not yaml_str:
            flash("Failed to generate prototype YAML.", "error")
            return redirect(url_for("prototype_creator.create_prototype", template=template_key))
        
        # Parse generated YAML to get document
        new_doc = yaml.safe_load(yaml_str)
        if not new_doc:
            flash("Failed to parse generated YAML.", "error")
            return redirect(url_for("prototype_creator.create_prototype", template=template_key))
        
        # Check if prototype ID already exists
        existing = find_prototype_paths_by_id(selected["name"], data["id"])
        if existing:
            flash(f"Prototype ID {data['id']} already exists.", "error")
            return redirect(url_for("prototype_creator.create_prototype", template=template_key))
        
        # Save to file
        if file_path.exists():
            # Add to existing file
            if not add_prototype_to_yaml(file_path, new_doc):
                flash("Failed to add prototype to existing file.", "error")
                return redirect(url_for("prototype_creator.create_prototype", template=template_key))
        else:
            # Create new file with single prototype
            with file_path.open("w", encoding="utf-8", newline="\n") as f:
                f.write(yaml_str)
        
        # Re-scan instance to update DB
        scan_instance_ids(selected["name"], selected["root_path"])
        
        flash(f"Prototype {data['id']} created successfully.", "success")
        return redirect(url_for("prototype_creator.edit_prototype", proto_id=data["id"]))
    
    # GET request: show form
    return render_template(
        "prototype_creator/create.html",
        template_key=template_key,
        template=tmpl,
        selected=selected
    )

@prototype_creator_bp.route("/edit/<proto_id>", methods=["GET", "POST"])
def edit_prototype(proto_id):
    """Edit individual prototype (not full YAML)"""
    selected = selected_instance_or_400()
    
    # Find which file contains this prototype
    rel_files = find_prototype_paths_by_id(selected["name"], proto_id)
    if not rel_files:
        abort(404, f"Prototype {proto_id} not found.")
    
    rel_file = rel_files[0]
    proto_root = Path(selected["root_path"]) / "Resources" / "Prototypes"
    file_path = safe_join(proto_root, rel_file)
    
    # Load the specific prototype
    proto = load_prototype_from_yaml(file_path, proto_id)
    if not proto:
        abort(404, f"Prototype {proto_id} not found in {rel_file}.")
    
    if request.method == "POST":
        # Update prototype fields from form
        proto["name"] = request.form.get("name", proto.get("name", ""))
        proto["description"] = request.form.get("description", proto.get("description", ""))
        
        # Update sprite in components
        new_sprite = request.form.get("sprite", "")
        if new_sprite:
            components = proto.get("components", [])
            for comp in components:
                if isinstance(comp, dict) and comp.get("type") in ("Sprite", "Clothing"):
                    comp["sprite"] = new_sprite
        
        # Save updated prototype
        if update_prototype_in_yaml(file_path, proto_id, proto):
            scan_instance_ids(selected["name"], selected["root_path"])
            flash(f"Prototype {proto_id} updated.", "success")
        else:
            flash("Failed to update prototype.", "error")
        
        return redirect(url_for("prototype_creator.edit_prototype", proto_id=proto_id))
    
    # GET: show edit form
    # Find template matching this prototype's parent
    template_key = None
    for key, tmpl in PROTOTYPE_TEMPLATES.items():
        if proto.get("parent") == tmpl["parent"]:
            template_key = key
            break
    
    # Get sprite preview
    sprite, state = resolve_preview_for_prototype_id(selected, proto_id)
    
    return render_template(
        "prototype_creator/edit.html",
        proto_id=proto_id,
        proto=proto,
        rel_file=rel_file,
        template_key=template_key,
        sprite=sprite,
        state=state,
        selected=selected
    )

@prototype_creator_bp.route("/rsi", methods=["GET", "POST"])
def manage_rsi():
    """Manage RSI files (create/upload) in custom dir"""
    selected = selected_instance_or_400()
    custom_dir = get_instance_custom_dir(selected["name"])
    textures_root = Path(selected["root_path"]) / "Resources" / "Textures"
    target_dir = textures_root / custom_dir if custom_dir else textures_root
    
    if request.method == "POST":
        # Handle RSI upload or creation
        rsi_path = request.form.get("rsi_path", "").strip().replace("\\", "/")
        if not rsi_path.endswith(".rsi"):
            rsi_path += ".rsi"
        
        rsi_dir = safe_join(target_dir, rsi_path)
        rsi_dir.mkdir(parents=True, exist_ok=True)
        
        # Handle uploaded files (meta.json, pngs)
        if "meta_json" in request.files:
            meta_file = request.files["meta_json"]
            if meta_file.filename:
                meta_file.save(rsi_dir / "meta.json")
        
        if "png_files" in request.files:
            png_files = request.files.getlist("png_files")
            for png in png_files:
                if png.filename.endswith(".png"):
                    png.save(rsi_dir / png.filename)
        
        flash(f"RSI saved to {rsi_path}", "success")
        return redirect(url_for("prototype_creator.manage_rsi", rsi_path=rsi_path))
    
    # List existing RSIs in custom dir
    rsis = []
    if target_dir.exists():
        for rsi in target_dir.rglob("*.rsi"):
            if rsi.is_dir():

                rel = rsi.relative_to(target_dir).as_posix()
                rsis.append(rel)
    
    return render_template(
        "prototype_creator/rsi.html",
        rsis=sorted(rsis),
        selected=selected
    )

@prototype_creator_bp.route("/api/search")
def api_search():
    """ElasticSearch-like prototype search using SQLite"""
    selected = selected_instance_or_400()
    query = request.args.get("q", "").strip()
    proto_type = request.args.get("type", "").strip()
    limit = min(int(request.args.get("limit", 50)), 200)
    
    with get_db() as conn:
        sql = """
            SELECT p.proto_id, p.proto_type, p.rel_path,
                   GROUP_CONCAT(DISTINCT c.component_type) as components
            FROM prototype_ids p
            LEFT JOIN prototype_components c ON p.instance_name = c.instance_name 
                AND p.proto_id = c.proto_id
            WHERE p.instance_name = ?
        """
        params = [selected["name"]]
        
        if query:
            sql += " AND p.proto_id LIKE ?"
            params.append(f"%{query}%")
        if proto_type:
            sql += " AND p.proto_type = ?"
            params.append(proto_type)
        
        sql += " GROUP BY p.proto_id ORDER BY p.proto_id LIMIT ?"
        params.append(limit)
        
        rows = conn.execute(sql, params).fetchall()
    
    return jsonify([{
        "id": r["proto_id"],
        "type": r["proto_type"],
        "rel_path": r["rel_path"],
        "components": r["components"].split(",") if r["components"] else []
    } for r in rows])


@prototype_creator_bp.route("/api/check-id/<proto_id>")
def api_check_id(proto_id):
    """Check if a prototype ID is unique"""
    selected = selected_instance_or_400()
    existing = find_first_prototype_path_by_id(selected["name"], proto_id)
    return jsonify({
        "exists": existing is not None,
        "path": existing
    })
