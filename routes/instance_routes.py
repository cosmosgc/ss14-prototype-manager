from flask import Blueprint, flash, redirect, render_template, request, url_for

import app as main_app

instance_bp = Blueprint("instance", __name__)


@instance_bp.route("/")
def index():
    instances = main_app.load_instances()
    selected_name = main_app.session.get("selected_instance")
    selected = main_app.get_instance_by_name(selected_name, instances) if selected_name else None
    return render_template("index.html", instances=instances, selected=selected)


@instance_bp.post("/instances/add")
def add_instance():
    name = request.form.get("name", "").strip()
    root = request.form.get("root_path", "").strip()
    if not name or not root:
        flash("Name and path are required.", "error")
        return redirect(url_for("instance.index"))
    if not main_app.Path(root).exists():
        flash("Path does not exist.", "error")
        return redirect(url_for("instance.index"))

    instances = main_app.load_instances()
    if any(i["name"].lower() == name.lower() for i in instances):
        flash("Instance name already exists.", "error")
        return redirect(url_for("instance.index"))

    main_app.save_instance(name, str(main_app.Path(root)))
    main_app.session["selected_instance"] = name
    flash("Instance added.", "success")
    return redirect(url_for("instance.index"))


@instance_bp.post("/instances/<name>/select")
def select_instance(name: str):
    instances = main_app.load_instances()
    if not main_app.get_instance_by_name(name, instances):
        main_app.abort(404)
    main_app.session["selected_instance"] = name
    flash(f"Selected instance: {name}", "success")
    return redirect(url_for("instance.index"))


@instance_bp.post("/instances/<name>/delete")
def delete_instance(name: str):
    if not main_app.delete_instance_db(name):
        main_app.abort(404)
    if main_app.session.get("selected_instance") == name:
        main_app.session.pop("selected_instance", None)
    flash(f"Deleted instance: {name}", "success")
    return redirect(url_for("instance.index"))


@instance_bp.route("/options", methods=["GET", "POST"])
def options():
    selected = main_app.selected_instance_or_400()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "save_custom_dir":
            custom_dir = request.form.get("custom_dir", "").strip().strip("/\\")
            main_app.set_instance_custom_dir(selected["name"], custom_dir)
            flash("Custom directory saved.", "success")

        elif action == "scan_ids":
            count = main_app.scan_instance_ids(
                selected["name"],
                selected["root_path"]
            )
            flash(f"Scanned and saved {count} prototype entries.", "success")

        return redirect(url_for("instance.options"))

    stats = main_app.get_instance_stats(selected["name"])
    custom_dir = main_app.get_instance_custom_dir(selected["name"])

    return render_template(
        "options.html",
        selected=selected,
        stats=stats,
        custom_dir=custom_dir
    )


@instance_bp.get("/id-search")
def id_search():
    selected = main_app.selected_instance_or_400()
    query = request.args.get("q", "").strip()
    rows = main_app.search_ids(selected["name"], query) if query else []
    return render_template("id_search.html", selected=selected, query=query, rows=rows)