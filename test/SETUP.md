# SS14 Map Viewer - Setup Instructions

## What's Been Done

1. **Tile Renderer** (`map_renderer.py`) - Generates PNG tiles from SS14 map data
2. **PNG Preview** - Generated full map preview at `static/map_preview.png`
3. **Flask Backend** (`app.py`) - Serves tiles and map data via API
4. **OpenLayers Frontend** (`static/index.html`) - Displays the map using OpenLayers

## Testing Instructions

### 1. Start the Flask App

Open a command prompt and run:
```
cd "G:\Development\ss14\prototype manager\test"
"G:\Development\ss14\prototype manager\.venv\Scripts\python.exe" app.py
```

### 2. Open the Map Viewer

Open your browser and navigate to:
```
http://localhost:5000
```

### 3. Test the Features

- The map should display chunk tiles from the SS14 map
- Click "Toggle Preview" to switch between tile view and full map preview
- Click "Reset View" to center the map

## API Endpoints

- `GET /api/map-bounds` - Returns map bounds (min/max X/Y coordinates)
- `GET /api/map-data` - Returns full map data as JSON
- `GET /api/tiles/<filename>` - Serves tile images
- `GET /api/tile/<cx>/<cy>` - Get specific chunk tile
- `GET /api/preview` - Serves full map preview image

## Vite + OpenLayers Setup (Optional)

To properly set up Vite with OpenLayers:

1. Install dependencies:
```
cd "G:\Development\ss14\prototype manager\test"
npm install vite ol
```

2. Create `vite.config.js`:
```javascript
export default {
  root: './static',
  build: {
    outDir: '../static/dist',
    assetsDir: 'assets'
  }
}
```

3. Build: `npx vite build`

## Files Created

- `map_renderer.py` - Tile rendering script
- `app.py` - Flask backend
- `static/index.html` - OpenLayers viewer
- `static/map_preview.png` - Full map preview
- `static/map_data.json` - Map data as JSON
- `static/tiles/tiles/*.png` - Individual chunk tiles
