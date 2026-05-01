import Map from 'ol/Map';
import View from 'ol/View';
import TileLayer from 'ol/layer/Tile';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import TileImage from 'ol/source/TileImage';
import Feature from 'ol/Feature';
import Point from 'ol/geom/Point';
import Style from 'ol/style/Style';
import Fill from 'ol/style/Fill';
import Stroke from 'ol/style/Stroke';
import Text from 'ol/style/Text';
import CircleStyle from 'ol/style/Circle';
import { getCenter } from 'ol/extent';
import 'ol/ol.css';

const mapDataTag = document.getElementById('map-data');
if (!mapDataTag) {
  console.error('No map data found');
  throw new Error('Missing map data');
}

const mapData = JSON.parse(mapDataTag.textContent);
const { tilemap = {}, gridChunks = [], entities = [], cacheKey = '' } = mapData;
const CHUNK_SIZE = 16;

console.log('Map data loaded:', {
  tilemapKeys: Object.keys(tilemap).length,
  gridChunks: gridChunks.length,
  entities: entities.length,
  cacheKey: cacheKey
});

let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
let hasData = false;

// Calculate bounds from grid chunks
gridChunks.forEach(chunk => {
  const cx = chunk.x !== undefined ? chunk.x : chunk.chunk_x;
  const cy = chunk.y !== undefined ? chunk.y : chunk.chunk_y;
  const left = cx * CHUNK_SIZE;
  const bottom = cy * CHUNK_SIZE;
  minX = Math.min(minX, left);
  minY = Math.min(minY, bottom);
  maxX = Math.max(maxX, left + CHUNK_SIZE);
  maxY = Math.max(maxY, bottom + CHUNK_SIZE);
  hasData = true;
});

entities.forEach(ent => {
  minX = Math.min(minX, ent.x);
  minY = Math.min(minY, ent.y);
  maxX = Math.max(maxX, ent.x);
  maxY = Math.max(maxY, ent.y);
  hasData = true;
});

console.log(`Bounds: minX=${minX}, minY=${minY}, maxX=${maxX}, maxY=${maxY}`);

// Create tile source using cached PNG tiles
// Map chunk coordinates to tile URLs
const chunkLookup = {};
gridChunks.forEach(chunk => {
  const cx = chunk.x !== undefined ? chunk.x : chunk.chunk_x;
  const cy = chunk.y !== undefined ? chunk.y : chunk.chunk_y;
  chunkLookup[`${cx},${cy}`] = true;
});

const tileSource = new TileImage({
  tileUrlFunction: (tileCoord) => {
    const z = tileCoord[0];
    const x = tileCoord[1];
    const y = tileCoord[2];
    
    // For simplicity, assume tile coords match chunk coords at zoom 0
    // In production, you'd want a proper tile grid
    const url = `/maps/api/tiles/${cacheKey}/${x}_${y}`;
    return url;
  }
});

// Entity layer
const entitySource = new VectorSource();

entities.forEach(ent => {
  const x = ent.x;
  const y = ent.y;
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

// Create map
const map = new Map({
  target: 'map',
  layers: [
    new TileLayer({ source: tileSource, zIndex: 1 }),
    new VectorLayer({ source: entitySource, zIndex: 3 })
  ],
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

console.log(`Map rendered: ${gridChunks.length} chunk tiles, ${entities.length} entities`);
