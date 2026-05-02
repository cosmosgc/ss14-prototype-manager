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
import CircleStyle from 'ol/style/Circle';
import { transformExtent } from 'ol/proj';
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
const CHUNK_SIZE = 16;
const TILE_SIZE_PX = 32;

console.log('Map data loaded:', {
  tilemapKeys: Object.keys(tilemap).length,
  gridChunks: gridChunks.length,
  entities: entities.length,
  cacheKey: cacheKey
});

// Calculate map bounds in SS14 coordinates (Y-up)
let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
let hasData = false;

gridChunks.forEach(chunk => {
  const cx = chunk.x !== undefined ? chunk.x : (chunk.chunk_x || 0);
  const cy = chunk.y !== undefined ? chunk.y : (chunk.chunk_y || 0);
  const left = cx * CHUNK_SIZE;
  const bottom = cy * CHUNK_SIZE;
  minX = Math.min(minX, left);
  minY = Math.min(minY, bottom);
  maxX = Math.max(maxX, left + CHUNK_SIZE);
  maxY = Math.max(maxY, bottom + CHUNK_SIZE);
  hasData = true;
});

// Store SS14 bounds for Y-flipping
const ss14_minY = minY;
const ss14_maxY = maxY;

// Flip entity Y coordinates for OpenLayers (Y-down)
const flippedEntities = entities.map(ent => ({
  ...ent,
  flippedY: ss14_minY + ss14_maxY - ent.y
}));

console.log(`SS14 bounds: minX=${minX}, minY=${minY}, maxX=${maxX}, maxY=${maxY}`);

// Create layers array
const layers = [];

// Add chunk image layers instead of preview
if (cacheKey && hasData && gridChunks.length > 0) {
  const baseUrl = '/maps/api/tiles/' + cacheKey;
  
  // Get Y range for flipping
  const allCy = gridChunks.map(c => c.y !== undefined ? c.y : (c.chunk_y || 0));
  const maxCy = Math.max(...allCy);
  
  gridChunks.forEach(chunk => {
    const cx = chunk.x !== undefined ? chunk.x : (chunk.chunk_x || 0);
    const cy = chunk.y !== undefined ? chunk.y : (chunk.chunk_y || 0);
    
    // Flip Y: image has y=0 at TOP, but SS14/OL expects y=0 at BOTTOM
    // So we flip: new_y = max_y - old_y
    const flippedCy = maxCy - cy;
    
    const left = cx * CHUNK_SIZE;
    const bottom = flippedCy * CHUNK_SIZE;
    
    const chunkExtent = [left, bottom, left + CHUNK_SIZE, bottom + CHUNK_SIZE];
    
    const tileUrl = `${baseUrl}/chunk_${cx}_${cy}.png`;
    
    const chunkLayer = new ImageLayer({
      source: new Static({
        url: tileUrl,
        imageExtent: chunkExtent,
        imageSize: [CHUNK_SIZE * TILE_SIZE_PX, CHUNK_SIZE * TILE_SIZE_PX]
      }),
      zIndex: 1
    });
    
    layers.push(chunkLayer);
  });
} else {
  console.warn('Cannot add chunk layers - missing cacheKey or no chunks');
}

// Entity layer (with flipped Y)
const entitySource = new VectorSource();

flippedEntities.forEach(ent => {
  const x = ent.x || 0;
  const y = ent.flippedY || 0;
  const proto = ent.proto || 'unknown';
  const entName = ent.name || proto;

  const point = new Point([x, y]);
  const feature = new Feature({ geometry: point });
  feature.set('proto', proto);
  feature.set('type', ent.proto_type);
  feature.set('name', entName);
  feature.set('uid', ent.uid);

  let color = 'rgba(255, 50, 50, 0.8)';
  const protoLower = proto.toLowerCase();
  if (protoLower.includes('wall')) color = 'rgba(100, 100, 100, 0.8)';
  else if (protoLower.includes('door') || protoLower.includes('airlock')) color = 'rgba(0, 150, 200, 0.8)';
  else if (protoLower.includes('cable')) color = 'rgba(255, 200, 0, 0.8)';
  else if (protoLower.includes('apc') || protoLower.includes('power')) color = 'rgba(200, 200, 0, 0.8)';
  else if (protoLower.includes('thruster')) color = 'rgba(255, 100, 0, 0.8)';
  else if (protoLower.includes('seat') || protoLower.includes('chair')) color = 'rgba(150, 100, 50, 0.8)';
  else if (protoLower.includes('light')) color = 'rgba(255, 255, 100, 0.8)';

  feature.setStyle(new Style({
    image: new CircleStyle({
      radius: 4,
      fill: new Fill({ color: color }),
      stroke: new Stroke({ color: '#fff', width: 1 })
    }),
    text: new Text({
      text: entName.length > 12 ? entName.substring(0, 12) : entName,
      offsetY: -12,
      font: '9px sans-serif',
      fill: new Fill({ color: '#000' }),
      stroke: new Stroke({ color: '#fff', width: 2 })
    })
  }));

  entitySource.addFeature(feature);
});

layers.push(new VectorLayer({ source: entitySource, zIndex: 3 }));

// Create map
const map = new Map({
  target: 'map',
  layers: layers,
  view: new View({
    center: [(minX + maxX) / 2, (minY + maxY) / 2],
    zoom: 2
  })
});

// Fit to extent if valid
if (hasData && isFinite(minX) && isFinite(minY) && isFinite(maxX) && isFinite(maxY)) {
  try {
    map.getView().fit([minX, minY, maxX, maxY], { padding: [50, 50, 50, 50] });
    console.log(`Map fitted to extent: [${minX}, ${minY}, ${maxX}, ${maxY}]`);
  } catch (e) {
    console.warn('Fit failed, using default view:', e);
  }
} else {
  console.warn('No valid map data to fit. Using default view.');
}

// Update map size after render
setTimeout(() => {
  map.updateSize();
  console.log('Map size updated');
}, 100);

// Handle window resize
window.addEventListener('resize', () => {
  map.updateSize();
});

console.log(`Map rendered: ${gridChunks.length} chunks, ${entities.length} entities`);
