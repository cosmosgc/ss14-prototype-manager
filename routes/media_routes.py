from flask import Blueprint, render_template, request, redirect, url_for
from pathlib import Path

import app as main_app

media_bp = Blueprint("media", __name__)


@media_bp.get("/radio")
def radio():
    selected = main_app.selected_instance_or_400()
    jukebox_dirs, all_tracks = main_app.load_jukebox_data(Path(selected["root_path"]))
    return render_template(
        "radio.html",
        selected=selected,
        jukebox_dirs=jukebox_dirs,
        all_tracks=all_tracks,
    )


@media_bp.route("/jukebox")
def jukebox_manager():
    selected = main_app.selected_instance_or_400()
    custom_dir = main_app.get_instance_custom_dir(selected["name"]) or ""

    root = Path(selected["root_path"])

    audio_root = root / "Resources" / "Audio" / custom_dir / "Jukebox"
    catalog_path = root / "Resources" / "Prototypes" / custom_dir / "Catalog" / "Jukebox" / "Standard.yml"
    attr_path = audio_root / "attributions.yml"

    jukebox_dirs, all_tracks = main_app.load_jukebox_data_custom(root, custom_dir)

    return render_template(
        "jukebox.html",
        selected=selected,
        audio_root=audio_root,
        catalog_path=catalog_path,
        attr_path=attr_path,
        jukebox_dirs=jukebox_dirs,
        tracks=all_tracks
    )


@media_bp.post("/jukebox/add")
def jukebox_add():
    selected = main_app.selected_instance_or_400()
    custom_dir = main_app.get_instance_custom_dir(selected["name"]) or ""

    root = Path(selected["root_path"])

    audio_root = root / "Resources" / "Audio" / custom_dir / "Jukebox"
    catalog_path = root / "Resources" / "Prototypes" / custom_dir / "Catalog" / "Jukebox" / "Standard.yml"

    audio_root.mkdir(parents=True, exist_ok=True)

    yaml_data = main_app.load_yaml_file(catalog_path)

    files = request.files.getlist("files")

    for file in files:
        filename = file.filename
        if not filename:
            continue

        ext = filename.lower().split(".")[-1]
        base_name = main_app.Path(filename).stem
        output_filename = f"{base_name}.ogg"
        output_path = audio_root / output_filename

        # --- save or convert ---
        if ext == "ogg":
            file.save(output_path)
        else:
            try:
                with main_app.tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
                    file.save(tmp.name)
                    tmp_path = main_app.Path(tmp.name)

                audio = main_app.AudioSegment.from_file(tmp_path)
                audio = audio.set_channels(1)
                audio.export(output_path, format="ogg")

                if ext == "mp3":
                    main_app.copy_metadata(tmp_path, output_path)

                tmp_path.unlink(missing_ok=True)

            except Exception as e:
                print(f"Failed to convert {filename}: {e}")
                continue

        # --- add to YAML ---
        entry = main_app.build_jukebox_entry(output_filename, custom_dir)

        # avoid duplicates
        if not any(e.get("id") == entry["id"] for e in yaml_data):
            yaml_data.append(entry)

    main_app.save_yaml_file(catalog_path, yaml_data)

    return redirect(url_for("media.jukebox_manager"))


@media_bp.post("/jukebox/remove")
def jukebox_remove():
    selected = main_app.selected_instance_or_400()
    custom_dir = main_app.get_instance_custom_dir(selected["name"]) or ""

    root = Path(selected["root_path"])

    audio_root = root / "Resources" / "Audio" / custom_dir / "Jukebox"
    catalog_path = root / "Resources" / "Prototypes" / custom_dir / "Catalog" / "Jukebox" / "Standard.yml"

    filename = request.form.get("filename")
    if not filename:
        return redirect(url_for("media.jukebox_manager"))

    # --- remove audio file ---
    file_path = audio_root / filename
    if file_path.exists():
        file_path.unlink()

    # --- remove from YAML ---
    yaml_data = main_app.load_yaml_file(catalog_path)

    def matches(entry):
        path = entry.get("path", {}).get("path", "")
        return filename in path

    yaml_data = [e for e in yaml_data if not matches(e)]

    main_app.save_yaml_file(catalog_path, yaml_data)

    return redirect(url_for("media.jukebox_manager"))