from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from pathlib import Path

import app as main_app

# Import required functions from main app
list_prototype_files = main_app.list_prototype_files
safe_join = main_app.safe_join
validate_yaml_text = main_app.validate_yaml_text
get_instance_custom_dir = main_app.get_instance_custom_dir
custom_prototypes_root = main_app.custom_prototypes_root
load_yaml_documents = main_app.load_yaml_documents
extract_cargo_products = main_app.extract_cargo_products
default_cargo_form_data = main_app.default_cargo_form_data
parse_cargo_form_request = main_app.parse_cargo_form_request
validate_crate_parent_compatibility = main_app.validate_crate_parent_compatibility
render_cargo_yaml = main_app.render_cargo_yaml
render_crate_yaml = main_app.render_crate_yaml
collect_proto_ids = main_app.collect_proto_ids
first_cargo_product = main_app.first_cargo_product

custom_bp = Blueprint(
    "custom",
    __name__,
    url_prefix="/custom"
)

@custom_bp.route("/files")
def custom_files():
    selected = _get_selected_instance_or_400()
    custom_dir = get_instance_custom_dir(selected["name"])
    if not custom_dir:
        flash("Set a custom directory in Options first.", "error")
        return redirect(url_for("custom_files"))
    root = custom_prototypes_root(selected, custom_dir)
    files = list_prototype_files(root) if root.exists() else []
    return render_template("custom_files.html", selected=selected, custom_dir=custom_dir, files=files)


@custom_bp.post("/files/create")
def custom_file_create():
    selected = _get_selected_instance_or_400()
    custom_dir = get_instance_custom_dir(selected["name"])
    if not custom_dir:
        return redirect(url_for("custom_files"))
    rel_path = request.form.get("rel_path", "").strip().replace("\\", "/")
    if not rel_path:
        flash("Path is required.", "error")
        return redirect(url_for("custom_files"))
    root = custom_prototypes_root(selected, custom_dir)
    file_path = safe_join(root, rel_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if file_path.exists():
        flash("File already exists.", "error")
        return redirect(url_for("custom_files"))
    with file_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("")
    flash("File created.", "success")
    return redirect(url_for("custom_file_edit", file=rel_path))


@custom_bp.route("/files/edit", methods=["GET", "POST"])
def custom_file_edit():
    selected = _get_selected_instance_or_400()
    custom_dir = get_instance_custom_dir(selected["name"])
    if not custom_dir:
        return redirect(url_for("custom_files"))
    rel_path = request.args.get("file", "").strip().replace("\\", "/")
    if not rel_path:
        abort(400)
    root = custom_prototypes_root(selected, custom_dir)
    file_path = safe_join(root, rel_path)

    if request.method == "POST":
        content = request.form.get("content", "")
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("w", encoding="utf-8", newline="\n") as f:
            f.write(normalized)
        flash("Custom file saved.", "success")
        return redirect(url_for("custom_file_edit", file=rel_path))
    content = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
    parse_ok, parse_error = validate_yaml_text(content) if content.strip() else (True, None)
    return render_template(
        "custom_file_edit.html",
        selected=selected,
        custom_dir=custom_dir,
        rel_path=rel_path,
        content=content,
        parse_ok=parse_ok,
        parse_error=parse_error,
    )


@custom_bp.post("/files/delete")
def custom_file_delete():
    selected = _get_selected_instance_or_400()
    custom_dir = get_instance_custom_dir(selected["name"])
    if not custom_dir:
        return redirect(url_for("custom_files"))
    rel_path = request.form.get("rel_path", "").strip().replace("\\", "/")
    if not rel_path:
        abort(400)
    root = custom_prototypes_root(selected, custom_dir)
    file_path = safe_join(root, rel_path)
    if file_path.exists() and file_path.is_file():
        file_path.unlink()
        flash("File deleted.", "success")
    else:
        flash("File not found.", "error")
    return redirect(url_for("custom_files"))


@custom_bp.get("/cargo")
def custom_cargo_catalog():
    selected = _get_selected_instance_or_400()
    custom_dir = get_instance_custom_dir(selected["name"])
    if not custom_dir:
        flash("Set a custom directory in Options first.", "error")
        return redirect(url_for("instance.options"))
    root = custom_prototypes_root(selected, custom_dir)
    cargo_root = root / "Catalog" / "Cargo"
    files = list_prototype_files(cargo_root) if cargo_root.exists() else []
    items = []
    for rel in files:
        fp = safe_join(cargo_root, rel)
        try:
            docs = load_yaml_documents(fp)
        except Exception:
            continue
        for item in extract_cargo_products(docs):
            item["source_file"] = f"Catalog/Cargo/{rel}"
            item["source_file_only"] = rel
            item["crate_file"] = find_crate_file_by_entity_id(root / "Catalog" / "Fills" / "Crates", item.get("product", ""))
            items.append(item)
    return render_template(
        "custom_cargo_catalog.html",
        selected=selected,
        custom_dir=custom_dir,
        items=sorted(items, key=lambda x: x.get("id", "").lower()),
    )


@custom_bp.route("/cargo/form", methods=["GET", "POST"])
def custom_cargo_form():
    selected = _get_selected_instance_or_400()
    custom_dir = get_instance_custom_dir(selected["name"])
    if not custom_dir:
        flash("Set a custom directory in Options first.", "error")
        return redirect(url_for("instance.options"))
    root = custom_prototypes_root(selected, custom_dir)
    cargo_file_rel = request.args.get("file", "").strip().replace("\\", "/")

    form_data = default_cargo_form_data()
    if cargo_file_rel:
        preload = load_cargo_form_data(root, cargo_file_rel)
        if preload:
            form_data.update(preload)

    if request.method == "POST":
        form_data = parse_cargo_form_request(request)
        compatible, reason = validate_crate_parent_compatibility(selected, form_data["crate_parent"])
        if not compatible:
            flash(f"Invalid crate parent: {reason}", "error")
            return render_template(
                "custom_cargo_form.html",
                selected=selected,
                custom_dir=custom_dir,
                form_data=form_data,
                cargo_yaml_preview=render_cargo_yaml(form_data),
                crate_yaml_preview=render_crate_yaml(form_data),
            )
        cargo_yaml = render_cargo_yaml(form_data)
        crate_yaml = render_crate_yaml(form_data)
        if request.form.get("mode") == "preview":
            return render_template(
                "custom_cargo_form.html",
                selected=selected,
                custom_dir=custom_dir,
                form_data=form_data,
                cargo_yaml_preview=cargo_yaml,
                crate_yaml_preview=crate_yaml,
            )
        cargo_target = safe_join(root / "Catalog" / "Cargo", form_data["cargo_file"])
        crate_target = safe_join(root / "Catalog" / "Fills" / "Crates", form_data["crate_file"])
        cargo_target.parent.mkdir(parents=True, exist_ok=True)
        crate_target.parent.mkdir(parents=True, exist_ok=True)
        with cargo_target.open("w", encoding="utf-8", newline="\n") as f:
            f.write(cargo_yaml)
        with crate_target.open("w", encoding="utf-8", newline="\n") as f:
            f.write(crate_yaml)
        flash("Cargo and crate YAML files saved.", "success")
        return redirect(url_for("custom.custom_cargo_catalog"))

    return render_template(
        "custom_cargo_form.html",
        selected=selected,
        custom_dir=custom_dir,
        form_data=form_data,
        cargo_yaml_preview=render_cargo_yaml(form_data),
        crate_yaml_preview=render_crate_yaml(form_data),
    )


def _get_selected_instance_or_400():
    from app import session, load_instances, get_instance_by_name
    instances = load_instances()
    selected_name = session.get("selected_instance")
    selected = get_instance_by_name(selected_name, instances)
    if not selected:
        abort(400, "No instance selected.")
    return selected


def find_crate_file_by_entity_id(crate_root: Path, entity_id: str):
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


def load_cargo_form_data(root: Path, cargo_file_rel: str):
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


def load_crate_data(root: Path, crate_file_rel: str):
    fp = safe_join(root / "Catalog" / "Fills" / "Crates", crate_file_rel)
    docs = load_yaml_documents(fp)
    stack = [docs]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if current.get("type") == "entity":
                entity_items = []
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