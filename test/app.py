from flask import Flask, send_from_directory, jsonify
from flask_cors import CORS
import json
from pathlib import Path

app = Flask(__name__, static_folder='static')
CORS(app)

STATIC_DIR = Path(__file__).parent / 'static'
TILES_DIR = STATIC_DIR / 'tiles' / 'tiles'

@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')

@app.route('/api/map-data')
def get_map_data():
    map_data_path = STATIC_DIR / 'map_data.json'
    if not map_data_path.exists():
        return jsonify({"error": "Map data not found"}), 404
    with open(map_data_path, 'r') as f:
        data = json.load(f)
    return jsonify(data)

@app.route('/api/tiles/<path:filename>')
def get_tile(filename):
    return send_from_directory(TILES_DIR, filename)

@app.route('/api/preview')
def get_preview():
    return send_from_directory(STATIC_DIR, 'map_preview.png')

@app.route('/api/tile/<int:cx>/<int:cy>')
def get_chunk_tile(cx, cy):
    filename = 'chunk_' + str(cx) + '_' + str(cy) + '.png'
    if not (TILES_DIR / filename).exists():
        return jsonify({"error": "Tile not found"}), 404
    return send_from_directory(TILES_DIR, filename)

@app.route('/api/map-bounds')
def get_map_bounds():
    map_data_path = STATIC_DIR / 'map_data.json'
    if not map_data_path.exists():
        return jsonify({"error": "Map data not found"}), 404
    with open(map_data_path, 'r') as f:
        data = json.load(f)
    chunks = data.get('chunks', [])
    if not chunks:
        return jsonify({"error": "No chunks found"}), 404
    min_x = min(c['x'] for c in chunks)
    max_x = max(c['x'] for c in chunks)
    min_y = min(c['y'] for c in chunks)
    max_y = max(c['y'] for c in chunks)
    return jsonify({
        "minX": min_x,
        "maxX": max_x,
        "minY": min_y,
        "maxY": max_y,
        "chunkSize": data.get('chunkSize', 16),
        "tileSizePx": data.get('tileSizePx', 32)
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)
