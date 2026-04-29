# SS14 Prototype Manager

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Flask" src="https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white">
  <img alt="Tailwind" src="https://img.shields.io/badge/TailwindCSS-4.x-06B6D4?logo=tailwindcss&logoColor=white">
  <img alt="Alpine" src="https://img.shields.io/badge/Alpine.js-3.x-8BC0D0?logo=alpinedotjs&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-Local%20Project-6B7280">
</p>

<p align="center">
  Prototype + RSI + Audio workflow for <b>Space Station 14</b>, built for fast iteration.
</p>

---

## ✨ Overview

`SS14 Prototype Manager` is a local Flask app focused on editing and validating SS14 prototype YAML while giving direct visual/audio context from linked assets:

- `Resources/Prototypes` (`.yml`, `.yaml`)
- `Resources/Textures` (`.rsi` folders + `meta.json` + PNG states)
- `Resources/Audio` (`.ogg` and other referenced files)

It supports **multiple SS14 instances**, so you can switch projects without changing code.

---

## 🚀 Features

- Multi-instance management (SQLite)
- Collapsible prototype tree view
- Hover sprite preview in tree
- Prototype YAML editor with:
  - line numbers
  - indent/outdent helpers
  - tab indentation behavior
  - YAML parse error feedback
- RSI inspector:
  - state thumbnails
  - modal gallery with keyboard navigation (`←`, `→`, `Esc`)
- Audio inspector:
  - file existence checks
  - inline playback
- Explorer shortcuts:
  - open YML
  - open RSI folder
  - open audio file
- Prototype-link detection:
  - finds likely ID references across multiple key styles
  - search and on-demand resolve actions

---

## 🧱 Tech Stack

| Layer | Tooling |
|---|---|
| Backend | Flask |
| YAML | PyYAML (SS14 tag-tolerant loader) |
| Imaging | Pillow |
| Templates | Jinja2 |
| Frontend Reactivity | Alpine.js |
| Styling | Tailwind CSS + Flowbite |

---

## 📂 Project Structure

```text
prototype manager/
├─ app.py
├─ templates/
│  ├─ base.html
│  ├─ index.html
│  ├─ prototypes.html
│  └─ prototype_view.html
├─ static/
│  ├─ src/input.css
│  └─ dist/output.css
├─ data/app.db
├─ requirements.txt
├─ package.json
├─ .env
├─ install_dependencies.bat
├─ build_tailwind.bat
└─ start_app.bat
```

---

## ⚙️ Quick Start

### 1) Install dependencies

```bat
install_dependencies.bat
```

### 2) Build/watch Tailwind CSS

```bat
build_tailwind.bat
```

### 3) Start Flask app

```bat
start_app.bat
```

### 4) Open in browser

```text
http://127.0.0.1:5000
```

For LAN access, set in `.env`:

```env
FLASK_RUN_HOST=0.0.0.0
```

---

## 🧩 Configuration

Environment values (`.env`):

```env
FLASK_APP=app.py
FLASK_DEBUG=true
FLASK_RUN_HOST=0.0.0.0
FLASK_RUN_PORT=5000
SECRET_KEY=change-this-secret
SQLITE_PATH=./data/app.db
DEFAULT_THUMB_SCALE=4
```

---

## 🛠️ Workflow Notes

1. Add one or more SS14 roots (example: `G:\Development\ss14\Andromeda-v`)
2. Select instance
3. Browse prototypes in tree view
4. Open a file, edit YAML, save
5. Validate linked RSI/audio quickly in the right panel

---

## 📸 Screenshots

> Add your screenshots here:

- `docs/screenshots/tree-view.png`
- `docs/screenshots/prototype-editor.png`
- `docs/screenshots/rsi-modal-gallery.png`

---

## 🗺️ Roadmap Ideas

- Persistent prototype ID index cache for instant resolve
- Safer structured editing for common component blocks
- Batch validation reports (missing RSI/audio/prototype IDs)
- Create/delete prototypes and RSI states from UI

---

## 🤝 Contributing

Local utility project; feel free to fork and adapt to your SS14 content pipeline.

If you open changes, keep commits focused by feature area (editor, parser, assets, UI).

---

## 📄 License

No explicit OSS license is currently defined for this repository.
