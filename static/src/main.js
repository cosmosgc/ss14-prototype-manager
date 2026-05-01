import Map from 'ol/Map';
import View from 'ol/View';
import VectorLayer from 'ol/layer/Vector';
import VectorSource from 'ol/source/Vector';
import Feature from 'ol/Feature';
import Point from 'ol/geom/Point';
import Polygon from 'ol/geom/Polygon';
import Style from 'ol/style/Style';
import Fill from 'ol/style/Fill';
import Stroke from 'ol/style/Stroke';
import Text from 'ol/style/Text';
import CircleStyle from 'ol/style/Circle';
import 'ol/ol.css';

// Read map data from template
const mapDataTag = document.getElementById('map-data');
if (!mapDataTag) {
  console.error('No map data found');
  throw new Error('Missing map data');
}

const mapData = JSON.parse(mapDataTag.textContent);
const { tilemap = {}, gridChunks = [], entities = [] } = mapData;
const CHUNK_SIZE = 16;

console.log('Map data loaded:', { 
  tilemapKeys: Object.keys(tilemap).length, 
  gridChunks: gridChunks.length, 
  entities: entities.length 
});

// --- Tile Layer ---
const tileSource = new VectorSource();
let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
let hasData = false;

gridChunks.forEach(chunk => {
  const chunkX = chunk.chunk_x;
  const chunkY = chunk.chunk_y;
  const tiles = chunk.tiles || [];

  // Update extent
  const left = chunkX * CHUNK_SIZE;
  const bottom = chunkY * CHUNK_SIZE;
  minX = Math.min(minX, left);
  minY = Math.min(minY, bottom);
  maxX = Math.max(maxX, left + CHUNK_SIZE);
  maxY = Math.max(maxY, bottom + CHUNK_SIZE);
  hasData = true;

  for (let tileY = 0; tileY < CHUNK_SIZE; tileY++) {
    for (let tileX = 0; tileX < CHUNK_SIZE; tileX++) {
      const tileId = tiles[tileY]?.[tileX] || 0;
      const tileName = tilemap[tileId] || `Tile ${tileId}`;
      const worldX = chunkX * CHUNK_SIZE + tileX;
      const worldY = chunkY * CHUNK_SIZE + tileY;

      // Create 1x1 tile rectangle
      const rect = new Polygon([[
        [worldX, worldY],
        [worldX + 1, worldY],
        [worldX + 1, worldY + 1],
        [worldX, worldY + 1],
        [worldX, worldY]
      ]]);

      const feature = new Feature({ geometry: rect });
      feature.set('tileName', tileName);
      feature.set('tileId', tileId);

      // Style by tile type
      let fillColor = 'rgba(200, 200, 200, 0.5)';
      if (tileName === 'Space') fillColor = 'rgba(0, 0, 20, 0.8)';
      else if (tileName === 'Plating') fillColor = 'rgba(150, 150, 150, 0.7)';
      else if (tileName.toLowerCase().includes('wall')) fillColor = 'rgba(100, 100, 100, 0.7)';

      feature.setStyle(new Style({
        fill: new Fill({ color: fillColor }),
        stroke: new Stroke({ color: 'rgba(0,0,0,0.2)', width: 0.5 }),
        text: new Text({
          text: tileName.length > 10 ? '' : tileName,
          font: '8px sans-serif',
          fill: new Fill({ color: '#fff' }),
          stroke: new Stroke({ color: '#000', width: 0.5 })
        })
      }));

      tileSource.addFeature(feature);
    }
  }
});

// --- Entity Layer ---
const entitySource = new VectorSource();

entities.forEach(ent => {
  const x = ent.x;
  const y = ent.y;

  // Update extent
  minX = Math.min(minX, x);
  minY = Math.min(minY, y);
  maxX = Math.max(maxX, x);
  maxY = Math.max(maxY, y);
  hasData = true;

  const point = new Point([x, y]);
  const feature = new Feature({ geometry: point });
  feature.set('proto', ent.proto);
  feature.set('type', ent.proto_type);

  feature.setStyle(new Style({
    image: new CircleStyle({
      radius: 4,
      fill: new Fill({ color: 'rgba(255, 50, 50, 0.8)' }),
      stroke: new Stroke({ color: '#fff', width: 1 })
    }),
    text: new Text({
      text: ent.proto,
      offsetY: -12,
      font: '10px sans-serif',
      fill: new Fill({ color: '#000' }),
      stroke: new Stroke({ color: '#fff', width: 2 })
    })
  }));

  entitySource.addFeature(feature);
});

// --- Create Map ---
const map = new Map({
  target: 'map',
  layers: [
    new VectorLayer({ source: tileSource, zIndex: 1 }),
    new VectorLayer({ source: entitySource, zIndex: 2 })
  ],
  view: new View({
    center: [0, 0],
    zoom: 2
  })
});

// Fit view to extent if valid
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

console.log(`Map rendered: ${gridChunks.length} chunks, ${entities.length} entities`);
