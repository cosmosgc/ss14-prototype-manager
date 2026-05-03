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
  namesCheckbox.checked = true;
  namesCheckbox.id = 'names-toggle';
  namesCheckbox.addEventListener('change', () => {
    const checked = namesCheckbox.checked;
    // Update all entity layer styles
    Object.values(entityLayers).forEach(layer => {
      const source = layer.getSource();
      source.forEachFeature(feature => {
        const style = feature.getStyle();
        if (style) {
          const textStyle = style.getText();
          if (textStyle) {
            textStyle.setText(checked ? (feature.get('name') || '') : '');
          }
        }
      });
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
    
    // Try icon first
    feature.setStyle(new Style({
      image: new Icon({
        src: iconUrl,
        scale: 0.5,
        crossOrigin: 'anonymous'
      }),
      text: new Text({
        text: entName.substring(0, 12),
        offsetY: -20,
        font: '9px sans-serif',
        fill: new Fill({ color: '#000' }),
        stroke: new Stroke({ color: '#fff', width: 2 })
      })
    }));

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
const map = new Map({
  target: 'map',
  layers: layers,
  view: new View({
    center: [maxX / 2, maxY / 2],
    zoom: 2
  })
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
