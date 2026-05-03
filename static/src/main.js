import Map from 'ol/Map';
import View from 'ol/View';
import ImageLayer from 'ol/layer/Image';
import Static from 'ol/source/ImageStatic';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import Feature from 'ol/Feature';
import Point from 'ol/geom/Point';
import Style from 'ol/style/Style';
import Fill from 'ol/style/Fill';
import Stroke from 'ol/style/Stroke';
import Text from 'ol/style/Text';
import Icon from 'ol/style/Icon';
import CircleStyle from 'ol/style/Circle';
import { fromLonLat } from 'ol/proj';
import 'ol/ol.css';

const mapDataTag = document.getElementById('map-data');
if (!mapDataTag) {
  console.error('No map data found');
  throw new Error('Missing map data');
}

let mapData;
try {
  mapData = JSON.parse(mapDataTag.textContent);
  console.log('Map data parsed successfully:', mapData);
} catch (e) {
  console.error('Failed to parse map data:', e);
  throw e;
}

const tilemap = mapData.tilemap || {};
const gridChunks = mapData.gridChunks || [];
const entities = mapData.entities || [];
const cacheKey = mapData.cacheKey || '';
const entityTypes = mapData.entityTypes || {};
const instanceName = mapData.instanceName || '';
const CHUNK_SIZE = 16;
const TILE_SIZE_PX = 32;

console.log('Map data loaded:', {
  tilemapKeys: Object.keys(tilemap).length,
  gridChunks: gridChunks.length,
  entities: entities.length,
  cacheKey: cacheKey,
  entityTypes: Object.keys(entityTypes).length,
  instanceName: instanceName
});

// ---- Compute chunk bounds (world space) ----
let minCx = Infinity, maxCx = -Infinity;
let minCy = Infinity, maxCy = -Infinity;

gridChunks.forEach(chunk => {
  const cx = chunk.x ?? chunk.chunk_x ?? 0;
  const cy = chunk.y ?? chunk.chunk_y ?? 0;

  minCx = Math.min(minCx, cx);
  maxCx = Math.max(maxCx, cx);
  minCy = Math.min(minCy, cy);
  maxCy = Math.max(maxCy, cy);
});

const xRange = maxCx - minCx;
const yRange = maxCy - minCy;

// ---- Render-space bounds (AFTER flip) ----
const minX = 0;
const minY = 0;
const maxX = (xRange + 1) * CHUNK_SIZE;
const maxY = (yRange + 1) * CHUNK_SIZE;

console.log("Render bounds:", { minX, minY, maxX, maxY });
console.log("Chunk range: cx", minCx, "-", maxCx, "cy", minCy, "-", maxCy);

// ---- Group entities by type ----
const entitiesByType = {};
entities.forEach(ent => {
  const proto = ent.proto || 'unknown';
  const entType = entityTypes[proto] || 'other';
  
  if (!entitiesByType[entType]) {
    entitiesByType[entType] = [];
  }
  entitiesByType[entType].push(ent);
});

console.log('Entities by type:', Object.keys(entitiesByType).map(t => `${t}: ${entitiesByType[t].length}`));

// ---- Create layer toggle controls ----
function createLayerControls() {
  const controlDiv = document.createElement('div');
  controlDiv.className = 'map-layer-controls';
  controlDiv.style.cssText = 'position:absolute; top:10px; right:10px; background:white; padding:10px; border-radius:4px; box-shadow:0 2px 6px rgba(0,0,0,0.3); z-index:1000; max-height:400px; overflow-y:auto;';
  
  // Title
  const title = document.createElement('h4');
  title.textContent = 'Layers';
  title.style.cssText = 'margin:0 0 8px 0; font-size:14px;';
  controlDiv.appendChild(title);
  
  // Master toggle
  const masterDiv = document.createElement('div');
  masterDiv.style.cssText = 'margin-bottom:8px;';
  const masterCheckbox = document.createElement('input');
  masterCheckbox.type = 'checkbox';
  masterCheckbox.checked = true;
  masterCheckbox.id = 'master-toggle';
  masterCheckbox.addEventListener('change', () => {
    const checked = masterCheckbox.checked;
    document.querySelectorAll('.layer-toggle').forEach(cb => {
      cb.checked = checked;
      cb.dispatchEvent(new Event('change'));
    });
  });
  const masterLabel = document.createElement('label');
  masterLabel.textContent = 'Show All';
  masterLabel.style.cssText = 'margin-left:4px; font-size:12px; cursor:pointer;';
  masterLabel.prepend(masterCheckbox);
  masterDiv.appendChild(masterLabel);
  controlDiv.appendChild(masterDiv);
  
  // Entity names toggle
  const namesDiv = document.createElement('div');
  namesDiv.style.cssText = 'margin-bottom:8px;';
  const namesCheckbox = document.createElement('input');
  namesCheckbox.type = 'checkbox';
  namesCheckbox.checked = false;
  namesCheckbox.id = 'names-toggle';
  namesCheckbox.addEventListener('change', () => {
    showNames = namesCheckbox.checked;
    // Force redraw all entity layers
    Object.values(entityLayers).forEach(layer => {
      layer.changed();
    });
  });
  const namesLabel = document.createElement('label');
  namesLabel.textContent = 'Show Names';
  namesLabel.style.cssText = 'margin-left:4px; font-size:12px; cursor:pointer;';
  namesLabel.prepend(namesCheckbox);
  namesDiv.appendChild(namesLabel);
  controlDiv.appendChild(namesDiv);
  
  // Separator
  const sep = document.createElement('hr');
  sep.style.cssText = 'margin:8px 0; border:none; border-top:1px solid #ccc;';
  controlDiv.appendChild(sep);
  
  // Entity type toggles
  const typeColors = {
    'walls': 'rgba(100,100,100,0.8)',
    'doors': 'rgba(0,150,200,0.8)',
    'cables': 'rgba(255,200,0,0.8)',
    'power': 'rgba(200,200,0,0.8)',
    'thrusters': 'rgba(255,100,0,0.8)',
    'furniture': 'rgba(150,100,50,0.8)',
    'lights': 'rgba(255,255,100,0.8)',
    'medical': 'rgba(255,100,100,0.8)',
    'weapons': 'rgba(255,50,50,0.8)',
    'gas': 'rgba(100,200,255,0.8)',
    'other': 'rgba(150,150,150,0.8)'
  };
  
  Object.keys(entitiesByType).sort().forEach(entType => {
    const count = entitiesByType[entType].length;
    const div = document.createElement('div');
    div.style.cssText = 'margin:4px 0;';
    
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = true;
    checkbox.className = 'layer-toggle';
    checkbox.dataset.layerType = entType;
    checkbox.id = `toggle-${entType}`;
    
    const color = typeColors[entType] || typeColors['other'];
    const colorBox = document.createElement('span');
    colorBox.style.cssText = `display:inline-block; width:12px; height:12px; background:${color}; margin-right:4px; border-radius:2px; vertical-align:middle;`;
    
    const label = document.createElement('label');
    label.textContent = `${entType} (${count})`;
    label.style.cssText = 'margin-left:4px; font-size:12px; cursor:pointer;';
    label.prepend(checkbox, colorBox);
    
    div.appendChild(label);
    controlDiv.appendChild(div);
  });
  
  return controlDiv;
}

// ---- Layers ----
const layers = [];
const entityLayers = {};

// Add chunk image layers
if (cacheKey && gridChunks.length > 0) {
  const baseUrl = '/maps/api/tiles/' + cacheKey;

  gridChunks.forEach(chunk => {
    const cx = chunk.x ?? chunk.chunk_x ?? 0;
    const cy = chunk.y ?? chunk.chunk_y ?? 0;

    // SAME math as Python: normalize first, then flip
    const cxIndex = cx - minCx;
    const cyIndex = cy - minCy;
    const cyFlipped = yRange - cyIndex;

    const left = cxIndex * CHUNK_SIZE;
    const bottom = cyFlipped * CHUNK_SIZE;

    const extent = [
      left,
      bottom,
      left + CHUNK_SIZE,
      bottom + CHUNK_SIZE
    ];

    const tileUrl = `${baseUrl}/chunk_${cx}_${cy}.png`;

    layers.push(new ImageLayer({
      source: new Static({
        url: tileUrl,
        imageExtent: extent,
        imageSize: [CHUNK_SIZE * TILE_SIZE_PX, CHUNK_SIZE * TILE_SIZE_PX]
      }),
      zIndex: 1,
      properties: { layerType: 'chunks' }
    }));
  });
} else {
  console.warn('No chunks to render');
}

// ---- Entity layers (grouped by type) ----
const typeColors = {
  'walls': 'rgba(100,100,100,0.8)',
  'doors': 'rgba(0,150,200,0.8)',
  'cables': 'rgba(255,200,0,0.8)',
  'power': 'rgba(200,200,0,0.8)',
  'thrusters': 'rgba(255,100,0,0.8)',
  'furniture': 'rgba(150,100,50,0.8)',
  'lights': 'rgba(255,255,100,0.8)',
  'medical': 'rgba(255,100,100,0.8)',
  'weapons': 'rgba(255,50,50,0.8)',
  'gas': 'rgba(100,200,255,0.8)',
  'other': 'rgba(150,150,150,0.8)'
};

// Global state for toggles
let showNames = false;

Object.keys(entitiesByType).forEach(entType => {
  const typeEntities = entitiesByType[entType];
  const source = new VectorSource();
  const color = typeColors[entType] || typeColors['other'];

  typeEntities.forEach(ent => {
    const x = ent.x || 0;
    const y = ent.y || 0;

    // Same transform as chunks: normalize, flip, convert back
    const chunkX = Math.floor(x / CHUNK_SIZE);
    const chunkY = Math.floor(y / CHUNK_SIZE);

    // Handle negative modulo correctly
    const localX = ((x % CHUNK_SIZE) + CHUNK_SIZE) % CHUNK_SIZE;
    const localY = ((y % CHUNK_SIZE) + CHUNK_SIZE) % CHUNK_SIZE;

    // Apply same normalize+flip logic as chunks (no clamping)
    const cxIndex = chunkX - minCx;
    const cyIndex = chunkY - minCy;
    const cyFlipped = yRange - cyIndex;

    const flippedX = (cxIndex * CHUNK_SIZE) + localX;
    const flippedY = (cyFlipped * CHUNK_SIZE) + (CHUNK_SIZE - 1 - localY);

    const point = new Point([flippedX, flippedY]);

    const feature = new Feature({ geometry: point });
    const proto = ent.proto || 'unknown';
    feature.set('proto', proto);
    feature.set('name', ent.name || proto);
    feature.set('type', entType);

    const entName = ent.name || proto;
    
    // Try to load entity icon, fallback to circle
    const iconUrl = `/maps/api/entity-icon/${instanceName}/${encodeURIComponent(proto)}`;
    
// Dynamic scale based on resolution - icon always matches 1 tile
    feature.setStyle((feature, resolution) => {
      const iconSizePx = 64;
      const desiredMapSize = 1;
      const scale = desiredMapSize / (iconSizePx * resolution);
      
      return new Style({
        image: new Icon({
          src: iconUrl,
          scale: scale,
          anchor: [0.5, 0.5],
          crossOrigin: 'anonymous'
        }),
        text: new Text({
          text: showNames ? entName.substring(0, 12) : '',
          offsetY: -20,
          font: '10px sans-serif',
          fill: new Fill({ color: '#000' }),
          stroke: new Stroke({ color: '#fff', width: 2 })
        })
      });
    });

    source.addFeature(feature);
  });

  const layer = new VectorLayer({
    source: source,
    zIndex: 3,
    properties: { layerType: 'entity', entityType: entType }
  });

  entityLayers[entType] = layer;
  layers.push(layer);
});

// ---- Create map ----
// Wrap map in container
const mapContainer = document.createElement('div');
mapContainer.id = 'map-container';

const mapDiv = document.getElementById('map');
if (mapDiv) {
  mapDiv.parentNode.insertBefore(mapContainer, mapDiv);
  mapContainer.appendChild(mapDiv);
}

// Add toast for shortcuts info
const mapToast = document.createElement('div');
mapToast.className = 'map-toast';
mapToast.id = 'shortcuts-toast';
mapToast.textContent = 'Press ? for shortcuts';
mapToast.style.display = 'none';
mapContainer.appendChild(mapToast);

// Expose showToast globally
window.showMapToast = function(msg) {
  mapToast.textContent = msg;
  mapToast.classList.add('visible');
  setTimeout(() => mapToast.classList.remove('visible'), 2000);
};

// Create toolbar
const toolbar = document.createElement('div');
toolbar.className = 'map-toolbar';
toolbar.innerHTML = `
  <button id="btn-home" title="Go to start (Home)">Home</button>
  <button id="btn-fullscreen" title="Toggle fullscreen (F)">Fullscreen</button>
  <button id="btn-zoom-in" title="Zoom in (+)">+</button>
  <button id="btn-zoom-out" title="Zoom out (-)">−</button>
  <button id="btn-reset-zoom" title="Reset zoom (0)">1:1</button>
  <span class="divider"></span>
  <button id="btn-layers" title="Toggle layers panel">Layers</button>
  <button id="btn-help" title="Show shortcuts (?)">?</button>
`;
toolbar.style.display = 'none'; // Hidden until map loads
mapContainer.appendChild(toolbar);

const map = new Map({
  target: 'map',
  layers: layers,
  view: new View({
    center: [maxX / 2, maxY / 2],
    zoom: 2
  })
});

// Store map reference on div for keyboard handler
mapDiv._ol_map = map;

function getComputedMapBounds() {
  if (gridChunks.length === 0) return null;
  return [minX, minY, maxX, maxY];
}

// ---- Keyboard shortcuts ----
function showToast(msg) {
  const toast = document.getElementById('shortcuts-toast');
  if (toast) {
    toast.textContent = msg;
    toast.classList.add('visible');
    setTimeout(() => toast.classList.remove('visible'), 2000);
  }
}

document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
  if (!map || !map.getView()) return;
  
  const view = map.getView();
  const step = e.shiftKey ? 8 : 2;
  const center = view.getCenter();
  const res = view.getResolution();
  
  switch(e.key) {
    case 'ArrowUp':
    case 'w':
    case 'W':
      e.preventDefault();
      view.animate({ center: [center[0], center[1] + step * res] }, { duration: 100 });
      break;
    case 'ArrowDown':
    case 's':
    case 'S':
      e.preventDefault();
      view.animate({ center: [center[0], center[1] - step * res] }, { duration: 100 });
      break;
    case 'ArrowLeft':
    case 'a':
    case 'A':
      e.preventDefault();
      view.animate({ center: [center[0] - step * res, center[1]] }, { duration: 100 });
      break;
    case 'ArrowRight':
    case 'd':
    case 'D':
      e.preventDefault();
      view.animate({ center: [center[0] + step * res, center[1]] }, { duration: 100 });
      break;
    case 'Home':
      e.preventDefault();
      const bounds = getComputedMapBounds();
      if (bounds) view.fit(bounds, { duration: 300, padding: [50, 50, 50, 50] });
      break;
    case 'f':
    case 'F':
      e.preventDefault();
      document.getElementById('map-container').classList.toggle('fullscreen');
      setTimeout(() => map.updateSize(), 100);
      break;
    case '+':
    case '=':
      e.preventDefault();
      view.animate({ resolution: view.getResolution() / 1.5 }, { duration: 100 });
      break;
    case '-':
    case '_':
      e.preventDefault();
      view.animate({ resolution: view.getResolution() * 1.5 }, { duration: 100 });
      break;
    case '0':
      e.preventDefault();
      view.animate({ resolution: 1 }, { duration: 200 });
      break;
    case '?':
      e.preventDefault();
      showToast('WASD/Arrows: move | +/-: zoom | 0: reset | Home: fit | F: fullscreen');
      break;
  }
});

// Expose for external use
window.getComputedMapBounds = getComputedMapBounds;

// Button handlers
document.addEventListener('DOMContentLoaded', () => {
  document.body.addEventListener('click', (e) => {
    const id = e.target.id;
    const view = map.getView();
    
    switch(id) {
      case 'btn-home':
        const bounds = getComputedMapBounds();
        if (bounds) view.fit(bounds, { duration: 300, padding: [50, 50, 50, 50] });
        break;
      case 'btn-fullscreen':
        document.getElementById('map-container').classList.toggle('fullscreen');
        setTimeout(() => map.updateSize(), 100);
        break;
      case 'btn-zoom-in':
        view.animate({ resolution: view.getResolution() / 1.5 }, { duration: 100 });
        break;
      case 'btn-zoom-out':
        view.animate({ resolution: view.getResolution() * 1.5 }, { duration: 100 });
        break;
      case 'btn-reset-zoom':
        view.animate({ resolution: 1 }, { duration: 200 });
        break;
      case 'btn-layers':
        const controls = document.querySelector('.map-controls');
        if (controls) controls.style.display = controls.style.display === 'none' ? 'block' : 'none';
        break;
      case 'btn-help':
        showToast('WASD/Arrows: move | +/-: zoom | 0: reset | F: fullscreen | Home: fit');
        break;
    }
  });
});

// ---- Add layer controls ----
setTimeout(() => {
  const mapDiv = document.getElementById('map');
  const controls = createLayerControls();
  mapDiv.appendChild(controls);

  // Add event listeners for toggles
  document.querySelectorAll('.layer-toggle').forEach(checkbox => {
    checkbox.addEventListener('change', () => {
      const layerType = checkbox.dataset.layerType;
      const layer = entityLayers[layerType];
      if (layer) {
        layer.setVisible(checkbox.checked);
      }
    });
  });

  // Master toggle
  const masterToggle = document.getElementById('master-toggle');
  if (masterToggle) {
    masterToggle.addEventListener('change', () => {
      const checked = masterToggle.checked;
      Object.values(entityLayers).forEach(layer => {
        layer.setVisible(checked);
      });
    });
  }
}, 100);

// ---- Fit using RENDER bounds ----
map.getView().fit(
  [minX, minY, maxX, maxY],
  { padding: [50, 50, 50, 50] }
);

setTimeout(() => map.updateSize(), 200);
window.addEventListener('resize', () => map.updateSize());

console.log(`Map rendered correctly: ${gridChunks.length} chunks, ${entities.length} entities`);
