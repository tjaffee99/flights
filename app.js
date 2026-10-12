(() => {
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// ---------------------------------------------------------------- data
// data/flights.json is built by pipeline/build_routes.py: airports and routes as parallel arrays.
// Each route has passengers in each direction (ab, ba) over a year and a source: 0 is the
// model's estimate, anything else one of the official statistics listed in `sources`.
let D = null;              // the raw file
let A = null, R = null;    // airports and routes, unpacked
const RAD = Math.PI / 180;

function unpack(d) {
  const a = d.airports, n = a.code.length;
  const air = Array.from({ length: n }, (_, i) => ({
    i, code: a.code[i], name: a.name[i], city: a.city[i], town: a.town ? a.town[i] : '', cc: a.country[i],
    lat: a.lat[i], lon: a.lon[i], pax: 0, routes: [], other: false, wiki: wikiTitle(a.wiki && a.wiki[i], a.name[i]),
  }));
  const r = d.routes;
  const routes = r.a.map((ai, i) => {
    const x = air[ai], y = air[r.b[i]];
    const route = {
      i, a: ai, b: r.b[i], ab: r.ab[i], ba: r.ba[i], pax: r.ab[i] + r.ba[i], src: r.src[i],
      airlines: (r.al[i] || []).map(k => d.airlines[k]), seasonal: !!(r.s && r.s[i]),
      months: r.n ? r.n[i] : 0, year: r.y ? r.y[i] : 0,
      dom: x.cc === y.cc, km: distance(x, y),
    };
    x.pax += route.pax; y.pax += route.pax;
    x.routes.push(i); y.routes.push(i);
    return route;
  });
  for (const x of air) x.routes.sort((p, q) => routes[q].pax - routes[p].pax);
  return [air, routes];
}

// the data gives an airport's Wikipedia title only where it differs from its name; '-' means it has none
const wikiTitle = (t, name) => (t === '-' ? null : t || name);
const wikiUrl = x => x.wiki ? 'https://en.wikipedia.org/wiki/' + encodeURIComponent(x.wiki.replace(/ /g, '_')) : null;

function distance(x, y) {
  const s = Math.sin((y.lat - x.lat) * RAD / 2) ** 2 +
            Math.cos(x.lat * RAD) * Math.cos(y.lat * RAD) * Math.sin((y.lon - x.lon) * RAD / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(s)));
}

// the great circle from x to y as [lon, lat] points about every 250 km, in pieces split where it
// crosses the antimeridian (the globe has one world: a longitude past ±180 would leave its surface)
function arc(x, y, km) {
  const n = Math.max(2, Math.ceil(km / 250));
  const f1 = x.lat * RAD, l1 = x.lon * RAD, f2 = y.lat * RAD, l2 = y.lon * RAD;
  const p1 = [Math.cos(f1) * Math.cos(l1), Math.cos(f1) * Math.sin(l1), Math.sin(f1)];
  const p2 = [Math.cos(f2) * Math.cos(l2), Math.cos(f2) * Math.sin(l2), Math.sin(f2)];
  const w = km / 6371, sw = Math.sin(w);
  const parts = [[]];
  let prev = null;
  for (let k = 0; k <= n; k++) {
    const t = k / n;
    const s = sw < 1e-9 ? [1 - t, t] : [Math.sin((1 - t) * w) / sw, Math.sin(t * w) / sw];
    const v = [0, 1, 2].map(j => s[0] * p1[j] + s[1] * p2[j]);
    const pt = [Math.atan2(v[1], v[0]) / RAD, Math.atan2(v[2], Math.hypot(v[0], v[1])) / RAD];
    if (prev && Math.abs(pt[0] - prev[0]) > 180) {
      const edge = prev[0] > 0 ? 180 : -180;
      const next = pt[0] + (edge > 0 ? 360 : -360);
      const lat = prev[1] + (pt[1] - prev[1]) * (edge - prev[0]) / (next - prev[0]);
      parts[parts.length - 1].push([edge, +lat.toFixed(3)]);
      parts.push([[-edge, +lat.toFixed(3)]]);
    }
    parts[parts.length - 1].push([+pt[0].toFixed(3), +pt[1].toFixed(3)]);
    prev = pt;
  }
  return parts;
}

// ---------------------------------------------------------------- formatting
function fmtPax(n) {
  if (n >= 9.95e6) return (n / 1e6).toFixed(0) + 'M';
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 9950) return Math.round(n / 1e3) + 'K';
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
  return String(Math.round(n));
}
const fmtInt = n => Math.round(n).toLocaleString('en-US');
const fmtKm = km => `${fmtInt(km)} km · ${fmtInt(km * 0.621371)} mi`;
const country = cc => (D.countries && D.countries[cc]) || cc;
const place = x => [x.city, country(x.cc)].filter(Boolean).join(', ');
const isEst = r => r.src === 0;
const paxLabel = r => (isEst(r) ? '≈ ' : '') + fmtPax(r.pax);

// ---------------------------------------------------------------- theme + palette
function isDark() {
  const t = document.documentElement.getAttribute('data-theme');
  if (t === 'dark') return true; if (t === 'light') return false;
  return matchMedia('(prefers-color-scheme: dark)').matches;
}
const PAL = {
  light: { land:'#F2F0EB', water:'#D9E4EA', waterLine:'#C9D9E3', urban:'#EAE7E0', park:'#DDE8D3', wood:'#E1EAD8', sand:'#EEE8D8', ice:'#FBFBFD',
           airport:'#E5E3EA', apron:'#DAD7E0', runway:'#C9C4BB', road:'#FFFFFF', roadCase:'#D9D4CA', minor:'#FBFAF7', building:'#E3DFD7', buildingEdge:'#D6D1C7',
           bnd:'#B0A99E', bnd4:'#CFC9BF', label:'#6C6C72', city:'#3A3A3C', town:'#6C6C72', halo:'#F2F0EB', space:'#E9E7E1',
           horizon:'#FFFFFF', route:'#2E4FA8', routeOp:.26, faded:'#8E8A84', fadedOp:.07, hot:'#E0542E',
           dot:'#1C1C1E', dotEdge:'#FFFFFF', text:'#1C1C1E', muted:'#7E7E85' },
  dark:  { land:'#2A2C30', water:'#1A1C20', waterLine:'#22252A', urban:'#2F3135', park:'#263027', wood:'#252D26', sand:'#2E2C27', ice:'#33363C',
           airport:'#33333B', apron:'#3A3A43', runway:'#4C4F56', road:'#3D4046', roadCase:'#2C2E32', minor:'#36393E', building:'#34363B', buildingEdge:'#3C3F44',
           bnd:'#6E7177', bnd4:'#46494E', label:'#9A9CA2', city:'#E9E9EE', town:'#A1A1A8', halo:'#1A1C20', space:'#0E0F11',
           horizon:'#3A4352', route:'#7FB2FF', routeOp:.26, faded:'#8A8C92', fadedOp:.06, hot:'#FF9F5A',
           dot:'#F2F2F7', dotEdge:'#1A1C20', text:'#F2F2F7', muted:'#94969C' },
};
const pal = () => PAL[isDark() ? 'dark' : 'light'];

const OFM = 'https://tiles.openfreemap.org';
const FONT = ['Noto Sans Regular'];
const FONT_BOLD = ['Noto Sans Bold'];
const FONT_ITALIC = ['Noto Sans Italic'];
const EMPTY = { type: 'FeatureCollection', features: [] };

// line width in pixels: the square root of passengers, so a 10M route is ~3x a 1M one, not 10x.
// Past zoom 6 lines get narrower again and (below) fainter, so the streets and the airport show through.
const W = ['sqrt', ['/', ['get', 'p'], 1e6]];
const lineWidth = (k = 1, min = 0, add = 0) => ['interpolate', ['exponential', 1.6], ['zoom'],
  0, ['+', add, ['min', 10, ['max', 0.25 + min, ['*', 1.1 * k, W]]]],
  3, ['+', add, ['min', 16, ['max', 0.35 + min, ['*', 2 * k, W]]]],
  6, ['+', add, ['min', 20, ['max', 0.7 + min, ['*', 3.6 * k, W]]]],
  10, ['+', add * 0.6, ['min', 12, ['max', 0.7 + min * 0.6, ['*', 2.2 * k, W]]]],
  14, ['+', add * 0.4, ['min', 8, ['max', 0.6 + min * 0.4, ['*', 1.4 * k, W]]]]];
const routeOpacity = op => ['interpolate', ['linear'], ['zoom'], 5, op, 9, op * 0.55, 13, op * 0.35];
const focusOpacity = op => ['interpolate', ['linear'], ['zoom'], 7, op, 11, op * 0.6, 14, op * 0.4];
const dotRadius = (add = 0) => ['interpolate', ['linear'], ['zoom'],
  0, ['+', 0.8 + add, ['*', 0.45, ['sqrt', ['/', ['get', 'p'], 1e6]]]],
  4, ['+', 1.6 + add, ['*', 0.9, ['sqrt', ['/', ['get', 'p'], 1e6]]]],
  8, ['+', 3 + add, ['*', 1.6, ['sqrt', ['/', ['get', 'p'], 1e6]]]]];
// an airport's code, and from zoom 9 its name under it
const airportText = (font) => ['step', ['zoom'], ['get', 'code'],
  9, ['format', ['get', 'code'], {}, '\n', {}, ['get', 'name'], { 'font-scale': 0.82, 'text-font': ['literal', font] }]];

const cls = (...c) => ['match', ['get', 'class'], c, true, false];
const NAME = ['coalesce', ['get', 'name:en'], ['get', 'name_en'], ['get', 'name']];
const omt = { source: 'omt' };

// the base map: muted at globe scale; streets, airport grounds and buildings appear as you zoom in
function baseLayers(p) {
  // road widths by class, plus `edge` pixels for the outline (one zoom curve per expression)
  const roadW = (edge = 0) => ['interpolate', ['exponential', 1.5], ['zoom'],
    5, ['match', ['get', 'class'], 'motorway', 0.7, 'trunk', 0.5, 0.3],
    10, ['+', edge * 0.6, ['match', ['get', 'class'], 'motorway', 2.4, 'trunk', 2, 'primary', 1.6, 'secondary', 1.2, 'tertiary', 1, 0.6]],
    16, ['+', edge, ['match', ['get', 'class'], 'motorway', 14, 'trunk', 12, 'primary', 11, 'secondary', 10, 'tertiary', 9, 'minor', 7, 4]]];
  const roads = ['all', ['==', ['geometry-type'], 'LineString'], ['!=', ['get', 'brunnel'], 'tunnel'],
    ['match', ['get', 'class'], ['motorway', 'trunk'], true, 'primary', ['>=', ['zoom'], 7], 'secondary', ['>=', ['zoom'], 9],
      'tertiary', ['>=', ['zoom'], 10], 'minor', ['>=', ['zoom'], 12], 'service', ['>=', ['zoom'], 14], false]];
  return [
    { id: 'land', type: 'background', paint: { 'background-color': p.land } },
    { ...omt, id: 'landcover', type: 'fill', 'source-layer': 'landcover', minzoom: 5, filter: cls('wood', 'forest', 'grass', 'wetland', 'sand', 'ice', 'glacier'),
      paint: { 'fill-color': ['match', ['get', 'class'], ['wood', 'forest'], p.wood, 'sand', p.sand, ['ice', 'glacier'], p.ice, p.park],
               'fill-opacity': ['interpolate', ['linear'], ['zoom'], 5, 0, 7, 0.6, 12, 0.9] } },
    { ...omt, id: 'landuse', type: 'fill', 'source-layer': 'landuse', minzoom: 6, filter: cls('residential', 'suburb', 'neighbourhood', 'commercial', 'industrial', 'retail'),
      paint: { 'fill-color': p.urban, 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 6, 0, 8, 1] } },
    { ...omt, id: 'park', type: 'fill', 'source-layer': 'park', minzoom: 6,
      paint: { 'fill-color': p.park, 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 6, 0, 9, 0.8] } },
    { ...omt, id: 'water', type: 'fill', 'source-layer': 'water', filter: ['!=', ['get', 'brunnel'], 'tunnel'], paint: { 'fill-color': p.water } },
    { ...omt, id: 'waterway', type: 'line', 'source-layer': 'waterway', minzoom: 8, filter: ['any', cls('river', 'canal'), ['>=', ['zoom'], 13]],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': p.waterLine, 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.5, 14, 3, 18, 10] } },
    // airport grounds, aprons and runways
    { ...omt, id: 'aeroway-area', type: 'fill', 'source-layer': 'aeroway', minzoom: 9, filter: ['==', ['geometry-type'], 'Polygon'],
      paint: { 'fill-color': ['match', ['get', 'class'], ['apron', 'runway', 'taxiway'], p.apron, p.airport],
               'fill-opacity': ['interpolate', ['linear'], ['zoom'], 9, 0, 10, 1] } },
    { ...omt, id: 'aeroway-line', type: 'line', 'source-layer': 'aeroway', minzoom: 10,
      filter: ['all', ['==', ['geometry-type'], 'LineString'], cls('runway', 'taxiway')],
      paint: { 'line-color': p.runway, 'line-width': ['interpolate', ['exponential', 1.5], ['zoom'],
        10, ['match', ['get', 'class'], 'runway', 1.2, 0.3], 16, ['match', ['get', 'class'], 'runway', 30, 8]] } },
    { ...omt, id: 'building', type: 'fill', 'source-layer': 'building', minzoom: 13,
      paint: { 'fill-color': p.building, 'fill-outline-color': p.buildingEdge, 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 13, 0, 14, 1] } },
    { ...omt, id: 'road-case', type: 'line', 'source-layer': 'transportation', minzoom: 9, filter: roads,
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': p.roadCase, 'line-width': roadW(2.5) } },
    { ...omt, id: 'road', type: 'line', 'source-layer': 'transportation', minzoom: 5, filter: roads,
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': ['match', ['get', 'class'], ['minor', 'service'], p.minor, p.road], 'line-width': roadW(),
               'line-opacity': ['interpolate', ['linear'], ['zoom'], 5, 0.6, 8, 1] } },
    { ...omt, id: 'rail', type: 'line', 'source-layer': 'transportation', minzoom: 11,
      filter: ['all', cls('rail'), ['!=', ['get', 'brunnel'], 'tunnel']],
      paint: { 'line-color': p.runway, 'line-width': ['interpolate', ['linear'], ['zoom'], 11, 0.6, 16, 2] } },
    { ...omt, id: 'bnd4', type: 'line', 'source-layer': 'boundary', minzoom: 4,
      filter: ['all', ['==', ['get', 'admin_level'], 4], ['!=', ['get', 'maritime'], 1]],
      paint: { 'line-color': p.bnd4, 'line-width': ['interpolate', ['linear'], ['zoom'], 4, 0.5, 10, 1.2], 'line-dasharray': [3, 2] } },
  ];
}

// borders and place names go above the route lines, or the busiest parts of the world would hide them
function overLayers(p) {
  const halo = { 'text-halo-color': p.halo, 'text-halo-width': 1.4 };
  const place = (id, classes, minzoom, size, color, font = FONT) => ({ ...omt, id, type: 'symbol', 'source-layer': 'place', minzoom,
    filter: cls(...classes),
    layout: { 'text-field': NAME, 'text-font': font, 'text-size': size, 'text-max-width': 8, 'symbol-sort-key': ['get', 'rank'], 'text-padding': 3 },
    paint: { 'text-color': color, ...halo } });
  return [
    { ...omt, id: 'boundary', type: 'line', 'source-layer': 'boundary',
      filter: ['all', ['==', ['get', 'admin_level'], 2], ['!=', ['get', 'maritime'], 1]],
      layout: { 'line-join': 'round' },
      paint: { 'line-color': p.bnd, 'line-width': ['interpolate', ['linear'], ['zoom'], 1, 0.5, 6, 1.1, 12, 2] } },
    { ...omt, id: 'road-label', type: 'symbol', 'source-layer': 'transportation_name', minzoom: 13,
      filter: cls('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'minor'),
      layout: { 'symbol-placement': 'line', 'text-field': NAME, 'text-font': FONT, 'text-size': 11, 'text-padding': 4 },
      paint: { 'text-color': p.town, ...halo } },
    place('place-suburb', ['suburb', 'neighbourhood', 'quarter'], 12, 11, p.town),
    place('place-village', ['village'], 11, 11, p.town),
    place('place-town', ['town'], 8, ['interpolate', ['linear'], ['zoom'], 8, 11, 14, 14], p.town),
    place('place-city', ['city'], 4.5, ['interpolate', ['linear'], ['zoom'], 4.5, 11, 10, 16], p.city),
  ];
}

// Every route sits in one big source that is never filtered or restyled per feature after load:
// selecting or hovering copies the few routes involved into small sources drawn on top, so the
// map never has to rebuild the 34,000-route layer.
function flightLayers(p) {
  return [
    { id: 'routes', type: 'line', source: 'routes', layout: { 'line-sort-key': ['get', 'p'] },
      paint: { 'line-color': p.route, 'line-opacity': routeOpacity(p.routeOp), 'line-width': lineWidth() } },
    ...overLayers(p),
    // airports without airline routes: small grey dots, grey italic codes
    { id: 'others', type: 'circle', source: 'others', minzoom: 3.5,
      paint: { 'circle-color': p.muted, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 3.5, 1.2, 8, 3, 14, 5],
               'circle-opacity': 0.7, 'circle-pitch-alignment': 'map' } },
    { id: 'others-label', type: 'symbol', source: 'others', minzoom: 5,
      layout: { 'text-field': airportText(FONT_ITALIC), 'text-font': FONT_ITALIC, 'text-size': ['interpolate', ['linear'], ['zoom'], 5, 10, 9, 12],
                'text-offset': [0, 0.8], 'text-anchor': 'top', 'text-padding': 4, 'text-max-width': 9 },
      paint: { 'text-color': p.muted, 'text-halo-color': p.halo, 'text-halo-width': 1.6, 'text-opacity': 0.9 } },
    { id: 'airports', type: 'circle', source: 'airports', layout: { 'circle-sort-key': ['get', 'p'] },
      paint: { 'circle-color': p.dot, 'circle-radius': dotRadius(), 'circle-stroke-color': p.dotEdge,
               'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 0, 0.3, 5, 1],
               'circle-opacity': 0.85, 'circle-stroke-opacity': 0.85, 'circle-pitch-alignment': 'map' } },
    { id: 'airport-label', type: 'symbol', source: 'airports', minzoom: 3,
      layout: { 'text-field': airportText(FONT), 'text-font': FONT_BOLD, 'text-size': ['interpolate', ['linear'], ['zoom'], 2, 10, 7, 13],
                'text-offset': [0, 0.9], 'text-anchor': 'top', 'symbol-sort-key': ['-', ['get', 'p']], 'text-padding': 6, 'text-max-width': 9 },
      paint: { 'text-color': p.text, 'text-halo-color': p.halo, 'text-halo-width': 1.4, 'text-opacity': 0.85 } },
    // countries are placed before airport codes, so a crowd of codes can't push a country's name off the map
    { ...omt, id: 'country-label', type: 'symbol', 'source-layer': 'place', minzoom: 1.5, maxzoom: 9, filter: cls('country'),
      layout: { 'text-field': NAME, 'text-font': FONT_BOLD, 'text-transform': 'uppercase', 'text-letter-spacing': 0.08, 'text-max-width': 7,
                'text-size': ['interpolate', ['linear'], ['zoom'], 1.5, ['match', ['get', 'rank'], [1, 2], 10, 9], 6, ['match', ['get', 'rank'], [1, 2], 15, 13]],
                'symbol-sort-key': ['get', 'rank'], 'text-padding': 2 },
      paint: { 'text-color': p.label, 'text-halo-color': p.halo, 'text-halo-width': 1.6,
               'text-opacity': ['interpolate', ['linear'], ['zoom'], 7, 1, 9, 0] } },
    // the selected airport's routes, outlined so they stand clear of everything else
    { id: 'focus-halo', type: 'line', source: 'focus', layout: { 'line-cap': 'round', 'line-join': 'round', 'line-sort-key': ['get', 'p'] },
      paint: { 'line-color': p.halo, 'line-opacity': focusOpacity(0.9), 'line-width': lineWidth(2.2, 1, 2.5) } },
    { id: 'focus', type: 'line', source: 'focus', layout: { 'line-cap': 'round', 'line-join': 'round', 'line-sort-key': ['get', 'p'] },
      paint: { 'line-color': p.hot, 'line-opacity': focusOpacity(1), 'line-width': lineWidth(2.2, 1) } },
    { id: 'hover', type: 'line', source: 'hover', layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': p.text, 'line-opacity': 0.95, 'line-width': lineWidth(2.2, 1.6) } },
    { id: 'focus-dots', type: 'circle', source: 'focus-airports', filter: ['!=', ['get', 'sel'], 1],
      paint: { 'circle-color': p.hot, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 0, 2, 5, 3.5, 9, 5.5],
               'circle-stroke-color': p.dotEdge, 'circle-stroke-width': 1.2, 'circle-pitch-alignment': 'map' } },
    { id: 'focus-sel', type: 'circle', source: 'focus-airports', filter: ['==', ['get', 'sel'], 1],
      paint: { 'circle-color': p.hot, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 0, 4.5, 5, 7.5, 9, 10],
               'circle-stroke-color': p.dotEdge, 'circle-stroke-width': 2.5, 'circle-pitch-alignment': 'map' } },
    { id: 'focus-label', type: 'symbol', source: 'focus-airports',
      layout: { 'text-field': airportText(FONT), 'text-font': FONT_BOLD, 'text-size': ['case', ['==', ['get', 'sel'], 1], 14, 12],
                'text-offset': [0, 0.9], 'text-anchor': 'top', 'symbol-sort-key': ['-', ['get', 'p']], 'text-padding': 2, 'text-max-width': 9 },
      paint: { 'text-color': p.hot, 'text-halo-color': p.halo, 'text-halo-width': 1.8 } },
  ];
}
const LINE_LAYERS = ['routes', 'focus-halo', 'focus', 'hover'];

function baseStyle(p) {
  return {
    version: 8,
    glyphs: OFM + '/fonts/{fontstack}/{range}.pbf',
    projection: { type: 'globe' },
    sky: { 'sky-color': p.space, 'horizon-color': p.horizon, 'fog-color': p.space, 'sky-horizon-blend': 0.6,
           'horizon-fog-blend': 0.6, 'fog-ground-blend': 0.9, 'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 1, 5, 1, 7, 0] },
    sources: { omt: { type: 'vector', url: OFM + '/planet' } },
    layers: baseLayers(p),
  };
}

function addFlightLayers(p) {
  map.addSource('routes', { type: 'geojson', data: routeGeo, buffer: 2, tolerance: 0.4, maxzoom: 6 });
  map.addSource('airports', { type: 'geojson', data: airportGeo, maxzoom: 10 });
  map.addSource('others', { type: 'geojson', data: EMPTY, maxzoom: 10 });
  map.addSource('focus', { type: 'geojson', data: EMPTY, maxzoom: 8 });
  map.addSource('focus-airports', { type: 'geojson', data: EMPTY, maxzoom: 10 });
  map.addSource('hover', { type: 'geojson', data: EMPTY, maxzoom: 8 });
  for (const layer of flightLayers(p)) map.addLayer(layer);
  showLines();
}

// a theme change restyles every layer in place (paint only), then reapplies the selection's fading
function applyTheme() {
  const p = pal();
  map.setSky(baseStyle(p).sky);
  for (const layer of [...baseLayers(p), ...flightLayers(p)]) {
    for (const [key, value] of Object.entries(layer.paint || {})) map.setPaintProperty(layer.id, key, value);
  }
  showSelection();
}

// ---------------------------------------------------------------- map
const phone = () => matchMedia('(max-width: 640px)').matches;
// the whole globe, sized to the screen and set beside the panel (above it on a phone)
function HOME() {
  const pad = panelPadding();
  const w = innerWidth - pad.left, h = innerHeight - pad.bottom;
  const zoom = 1.55 + Math.log2(Math.max(200, Math.min(0.86 * h, 0.92 * w)) / 460);
  return { center: [-30, 30], zoom, padding: pad };
}
let map = null, routeGeo = null, airportGeo = null;

function buildGeo() {
  routeGeo = { type: 'FeatureCollection', features: R.map(r => ({
    type: 'Feature',
    properties: { i: r.i, a: r.a, b: r.b, p: r.pax, e: isEst(r) ? 1 : 0, d: r.dom ? 1 : 0 },
    geometry: { type: 'MultiLineString', coordinates: arc(A[r.a], A[r.b], r.km) },
  })) };
  airportGeo = { type: 'FeatureCollection', features: A.filter(x => x.routes.length).map(x => point(x)) };
}
const point = (x, sel = false) => ({
  type: 'Feature',
  properties: { i: x.i, code: x.code, name: x.name, p: x.pax, sel: sel ? 1 : 0 },
  geometry: { type: 'Point', coordinates: [x.lon, x.lat] },
});
const collection = features => ({ type: 'FeatureCollection', features });

// ---------------------------------------------------------------- state
const S = {
  airport: null,   // selected airport index
  route: null,     // selected route index
  from: null,      // the airport a route was opened from, for "back"
  hover: null,     // hovered route index
  min: 0,          // overview: minimum passengers per route
  dom: true, intl: true,
  lines: (() => { try { return localStorage.getItem('flights.lines') !== 'off'; } catch (_) { return true; } })(),
  q: '',
};

function overviewFilter() {
  const f = ['all', ['>=', ['get', 'p'], S.min]];
  if (!S.dom) f.push(['==', ['get', 'd'], 0]);
  if (!S.intl) f.push(['==', ['get', 'd'], 1]);
  return f;
}

const selecting = () => S.airport !== null || S.route !== null;

function showSelection() {
  if (!map || !map.getSource('focus')) return;
  const p = pal(), on = selecting();
  const ids = S.route !== null ? [S.route] : S.airport !== null ? A[S.airport].routes : [];
  map.getSource('focus').setData(collection(ids.map(i => routeGeo.features[i])));
  const spots = new Map();
  for (const i of ids) for (const k of [R[i].a, R[i].b]) spots.set(k, A[k]);
  if (S.airport !== null) spots.set(S.airport, A[S.airport]);
  const sel = S.route === null ? S.airport : null;
  map.getSource('focus-airports').setData(collection([...spots.values()].map(x => point(x, x.i === sel))));
  // everything else fades to grey (constant paint values: no re-layout)
  map.setPaintProperty('routes', 'line-color', on ? p.faded : p.route);
  map.setPaintProperty('routes', 'line-opacity', routeOpacity(on ? p.fadedOp : p.routeOp));
  map.setPaintProperty('airports', 'circle-opacity', on ? 0.15 : 0.85);
  map.setPaintProperty('airports', 'circle-stroke-opacity', on ? 0.15 : 0.85);
  map.setPaintProperty('airport-label', 'text-opacity', on ? 0 : 0.85);
  map.setPaintProperty('others', 'circle-opacity', on ? 0.25 : 0.7);
  map.setPaintProperty('others-label', 'text-opacity', on ? 0.4 : 0.9);
}

// the route lines can be switched off to see the map underneath; the choice is remembered
function showLines() {
  for (const id of LINE_LAYERS) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', S.lines ? 'visible' : 'none');
  const b = $('#lines');
  b.setAttribute('aria-pressed', String(S.lines));
  b.setAttribute('aria-label', S.lines ? 'Hide flight paths' : 'Show flight paths');
  b.title = S.lines ? 'Hide flight paths' : 'Show flight paths';
}

// only the overview's route filters touch the big layer, and only when they change
function applyFilters() {
  if (map && map.getLayer('routes')) map.setFilter('routes', S.min || !S.dom || !S.intl ? overviewFilter() : null);
}

function setHover(i) {
  if (i === S.hover) return;
  S.hover = i;
  if (map && map.getSource('hover')) map.getSource('hover').setData(collection(i === null ? [] : [routeGeo.features[i]]));
  document.querySelectorAll('.row.hl').forEach(el => el.classList.remove('hl'));
  if (i !== null) document.querySelectorAll(`.row[data-route="${i}"]`).forEach(el => el.classList.add('hl'));
}

// ---------------------------------------------------------------- panel views
const body = $('#body');
const BACK = '<svg viewBox="0 0 9 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7.5 1.5L2 7l5.5 5.5"/></svg>';

function render() {
  if (S.q) body.innerHTML = searchView();
  else if (S.route !== null) body.innerHTML = routeView(R[S.route]);
  else if (S.airport !== null) body.innerHTML = airportView(A[S.airport]);
  else body.innerHTML = overview();
  body.scrollTop = 0;
  $('#phead').classList.remove('scrolled');
}

function routeRow(r, from, top) {
  const other = A[from === null ? r.a : (r.a === from ? r.b : r.a)];
  const title = from === null ? `${A[r.a].code} – ${A[r.b].code}` : other.city || other.name;
  const sub = from === null ? `${A[r.a].city || A[r.a].name} – ${A[r.b].city || A[r.b].name}` : `${other.name} · ${country(other.cc)}`;
  const share = Math.max(2, 100 * r.pax / top);
  return `<button class="row" data-route="${r.i}">
    <span class="bar" style="width:calc((100% - 32px) * ${(share / 100).toFixed(3)})"></span>
    ${from === null ? '' : `<span class="code">${esc(other.code)}</span>`}
    <span class="t"><b>${esc(title)}</b><span>${esc(sub)}</span></span>
    <span class="n">${paxLabel(r)}${isEst(r) ? '<small>estimate</small>' : ''}</span></button>`;
}

function airportRow(x) {
  if (x.other) return `<button class="row other" data-airport="${x.i}"><span class="code">${esc(x.code)}</span>
    <span class="t"><b>${esc(x.name)}</b><span>${esc(place(x))}</span></span>
    <span class="n"><small>no airline<br>flights</small></span></button>`;
  return `<button class="row" data-airport="${x.i}"><span class="code">${esc(x.code)}</span>
    <span class="t"><b>${esc(x.name)}</b><span>${esc(place(x))}</span></span>
    <span class="n">${fmtPax(x.pax)}<small>${x.routes.length} routes</small></span></button>`;
}

function overview() {
  const total = R.reduce((s, r) => s + r.pax, 0);
  const measured = R.reduce((s, r) => s + (isEst(r) ? 0 : r.pax), 0);
  const live = A.filter(x => x.routes.length).length;
  const topRoutes = R.slice(0, 12);   // the file lists routes busiest first
  const topAirports = A.filter(x => x.routes.length).sort((p, q) => q.pax - p.pax).slice(0, 12);
  const seg = (vals) => vals.map(([v, label]) => `<button data-min="${v}" aria-pressed="${S.min === v}">${label}</button>`).join('');
  return `<div class="intro"><h1>Flight Atlas</h1>
    <p>Every passenger air route flying today, drawn by how many people fly it in a year. Click an airport to see everywhere it flies nonstop.</p></div>
  <div class="stats"><div><b>${fmtInt(R.length)}</b><span>routes</span></div><div><b>${fmtInt(live)}</b><span>airports</span></div>
    <div><b>${(total / 1e9).toFixed(2)}B</b><span>passengers a year</span></div></div>
  <div class="opts"><span>Show routes</span><span class="seg" id="minseg">${seg([[0, 'All'], [1e5, '100K+'], [1e6, '1M+']])}</span></div>
  <div class="chips"><button class="chip" id="cdom" aria-pressed="${S.dom}">Domestic</button><button class="chip" id="cintl" aria-pressed="${S.intl}">International</button></div>
  <div class="sec">Busiest airports</div>${topAirports.map(airportRow).join('')}
  <div class="sec">Busiest routes</div>${topRoutes.map(r => routeRow(r, null, topRoutes[0].pax)).join('')}
  <div class="note small">Routes are the ones airlines fly as of ${esc(D.routes_as_of)}, from Wikipedia's airport pages.
    Passengers are counted for ${Math.round(100 * measured / total)}% of travel (${esc([...new Set(D.sources.filter(s => s.id).map(s => s.short))].join(', '))});
    the rest are estimates fitted to each airport’s annual total. <a href="${esc(D.about)}" target="_blank" rel="noopener">How this was made</a></div>`;
}

const WIKI = '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 2.5H2.5v7h7V7M7 2.5h2.5V5M9.5 2.5 5.5 6.5"/></svg>';
// an airport's name, linking to its Wikipedia article when it has one
function airportTitle(x) {
  const url = wikiUrl(x);
  return url ? `<a class="wiki" href="${esc(url)}" target="_blank" rel="noopener" title="Open on Wikipedia">${esc(x.name)}${WIKI}</a>` : esc(x.name);
}

function airportView(x) {
  if (x.other) return `<button class="back" data-back>${BACK}All routes</button>
  <div class="dhead other"><span class="code big">${esc(x.code)}</span><div><h2>${airportTitle(x)}</h2><div class="sub">${esc(place(x))}</div></div></div>
  <div class="note">No scheduled airline flights. ${esc((D.closed && D.closed[x.cc]) ||
    'Airports like this one serve private planes, air taxis, the military, or have closed or not yet opened.')}</div>`;
  const routes = x.routes.map(i => R[i]);
  const countries = new Set(routes.map(r => A[r.a === x.i ? r.b : r.a].cc));
  const est = routes.filter(isEst).reduce((s, r) => s + r.pax, 0);
  const top = routes.length ? routes[0].pax : 1;
  return `<button class="back" data-back>${BACK}All routes</button>
  <div class="dhead"><span class="code big">${esc(x.code)}</span><div><h2>${airportTitle(x)}</h2><div class="sub">${esc(place(x))}</div></div></div>
  <div class="stats"><div><b>${(est > x.pax / 2 ? '≈ ' : '') + fmtPax(x.pax)}</b><span>passengers a year</span></div>
    <div><b>${routes.length}</b><span>nonstop destinations</span></div><div><b>${countries.size}</b><span>countries</span></div></div>
  <div class="sec">Destinations by passengers</div>
  ${routes.map(r => routeRow(r, x.i, top)).join('') || '<div class="note">No current passenger routes.</div>'}
  ${est ? `<div class="note small">≈ marks estimated passengers.</div>` : ''}`;
}

// where a route's passenger count comes from, in a sentence
function sourceText(r) {
  const src = D.sources.find(s => s.id === r.src) || D.sources[0];
  if (isEst(r)) return 'Estimated from the airlines that fly it, the size of both airports and the distance, scaled to the airports’ annual totals';
  if (src.key === 'reported') return `${src.name}${r.year ? ' (' + r.year + ')' : ''}`;
  let t = `${src.name}, ${src.period}`;
  if (r.months) t += ` · scaled up to a year from ${r.months} months of flights`;
  return t;
}

function routeView(r) {
  const x = A[r.a], y = A[r.b];
  const dir = !(D.sources.find(s => s.id === r.src) || {}).directional ? '' :
    `<dt>${esc(x.code)} → ${esc(y.code)}</dt><dd>${fmtInt(r.ab)}</dd><dt>${esc(y.code)} → ${esc(x.code)}</dt><dd>${fmtInt(r.ba)}</dd>`;
  return `<button class="back" data-back>${BACK}${S.from !== null ? esc(A[S.from].code) : 'All routes'}</button>
  <div class="route-ends"><span class="code big">${esc(x.code)}</span><span class="arrow">⇄</span><span class="code big">${esc(y.code)}</span></div>
  <div class="dhead" style="padding-top:4px"><div><h2>${esc(x.city || x.name)} – ${esc(y.city || y.name)}</h2>
    <div class="sub">${esc(country(x.cc))}${x.cc === y.cc ? ' · domestic' : ' – ' + esc(country(y.cc))}</div></div></div>
  <dl class="kv"><dt>Passengers a year</dt><dd><b>${isEst(r) ? '≈ ' + fmtPax(r.pax) : fmtInt(r.pax)}</b></dd>${dir}
    <dt>Distance</dt><dd>${fmtKm(r.km)}</dd>
    <dt>Source</dt><dd>${esc(sourceText(r))}${r.seasonal ? ' · seasonal route' : ''}</dd></dl>
  ${r.airlines.length ? `<div class="sec">Airlines</div><div class="airlines">${r.airlines.map(esc).join(' · ')}</div>` : ''}
  <div class="sec">Airports</div>${airportRow(x)}${airportRow(y)}`;
}

// search: airports by code, name, city or country
let index = null;
const fold = s => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
function search(q) {
  if (!index) index = A.filter(x => x.routes.length || x.other).map(x => ({ x, code: x.code.toLowerCase(), text: fold([x.name, x.city, x.town, country(x.cc)].join(' ')) }));
  const f = fold(q).trim();
  if (!f) return [];
  const words = f.split(/\s+/);
  const hits = [];
  for (const e of index) {
    let score = 0;
    if (e.code === f) score = 100;
    else if (words.every(w => e.text.includes(w))) score = 10 + (e.text.startsWith(f) ? 5 : 0) + (fold(e.x.city).startsWith(f) ? 5 : 0);
    else if (f.length >= 2 && e.code.startsWith(f)) score = 8;
    if (score) hits.push([score + Math.log10(1 + e.x.pax) / 10 - (e.x.other ? 1 : 0), e.x]);
  }
  return hits.sort((p, q) => q[0] - p[0]).slice(0, 40).map(h => h[1]);
}
function searchView() {
  const hits = search(S.q);
  return hits.length ? `<div class="sec">Airports</div>${hits.map(airportRow).join('')}` : `<div class="note">No airports match “${esc(S.q)}”.</div>`;
}

// ---------------------------------------------------------------- navigation
function selectAirport(i, { fly = true } = {}) {
  S.airport = i; S.route = null; S.from = null; S.q = ''; $('#q').value = ''; $('#qclear').classList.remove('show');
  setHover(null); render(); showSelection(); writeHash();
  if (fly && i !== null) {
    const x = A[i];
    const zoom = Math.max(map.getZoom(), x.other ? 5.5 : phone() ? 1.2 : 1.9);
    map.easeTo({ center: [x.lon, x.lat], zoom, duration: 900, padding: panelPadding() });
  }
}
function selectRoute(i, { fly = true } = {}) {
  if (S.route === null) S.from = S.airport;
  S.route = i; S.q = '';
  setHover(null); render(); showSelection(); writeHash();
  if (fly) {
    const r = R[i], pts = arc(A[r.a], A[r.b], r.km).flat(), mid = pts[Math.floor(pts.length / 2)];
    const zoom = Math.max(1, Math.min(5.5, Math.log2(26000 / Math.max(r.km, 300)) - (phone() ? 0.8 : 0)));
    map.easeTo({ center: mid, zoom, duration: 900, padding: panelPadding() });
  }
}
function back() {
  if (S.route !== null && S.from !== null) { const a = S.from; S.route = null; S.from = null; selectAirport(a, { fly: false }); }
  else { S.route = null; S.from = null; selectAirport(null, { fly: false }); }
}
function panelPadding() {
  if (phone()) return { top: 0, bottom: Math.round(innerHeight * 0.45), left: 0, right: 0 };
  return { top: 0, bottom: 0, left: Math.min(380, innerWidth / 3), right: 0 };
}

function writeHash() {
  let h = '';
  if (S.route !== null) h = '#' + A[R[S.route].a].code + '-' + A[R[S.route].b].code;
  else if (S.airport !== null) h = '#' + A[S.airport].code;
  if (location.hash !== h) history.replaceState(null, '', h || location.pathname + location.search);
}
let hashWaits = false, othersLoaded = false;   // a link to an airport without routes waits for others.json
function readHash() {
  const m = decodeURIComponent(location.hash.slice(1)).toUpperCase().match(/^([A-Z0-9]{3})(?:-([A-Z0-9]{3}))?$/);
  if (!m) return;
  const byCode = c => A.find(x => x.code === c && (x.routes.length || x.other));
  const x = byCode(m[1]);
  if (!x) { hashWaits = !othersLoaded; return; }
  if (!m[2]) { selectAirport(x.i); return; }
  const y = byCode(m[2]);
  const r = y && x.routes.map(i => R[i]).find(r => r.a === y.i || r.b === y.i);
  if (r) { S.airport = x.i; selectRoute(r.i); } else selectAirport(x.i);
}

// ---------------------------------------------------------------- events
body.addEventListener('click', e => {
  const t = e.target.closest('button');
  if (!t) return;
  if (t.hasAttribute('data-back')) back();
  else if (t.dataset.route !== undefined) selectRoute(+t.dataset.route);
  else if (t.dataset.airport !== undefined) selectAirport(+t.dataset.airport);
  else if (t.dataset.min !== undefined) { S.min = +t.dataset.min; render(); applyFilters(); }
  else if (t.id === 'cdom' || t.id === 'cintl') {
    const k = t.id === 'cdom' ? 'dom' : 'intl';
    S[k] = !S[k];
    if (!S.dom && !S.intl) S[k === 'dom' ? 'intl' : 'dom'] = true;
    render(); applyFilters();
  }
});
body.addEventListener('mouseover', e => {
  const t = e.target.closest('.row[data-route]');
  setHover(t ? +t.dataset.route : null);
});
body.addEventListener('mouseleave', () => setHover(null));
body.addEventListener('scroll', () => $('#phead').classList.toggle('scrolled', body.scrollTop > 2));

let qTimer = null;
$('#q').addEventListener('input', e => {
  clearTimeout(qTimer);
  qTimer = setTimeout(() => { S.q = e.target.value; $('#qclear').classList.toggle('show', !!S.q); render(); }, 80);
});
$('#q').addEventListener('keydown', e => {
  if (e.key === 'Enter') { const hit = search(S.q || e.target.value)[0]; if (hit) selectAirport(hit.i); }
  if (e.key === 'Escape') { e.target.value = ''; S.q = ''; $('#qclear').classList.remove('show'); render(); }
});
$('#qclear').addEventListener('click', () => { $('#q').value = ''; S.q = ''; $('#qclear').classList.remove('show'); render(); $('#q').focus(); });
$('#grip').addEventListener('click', () => $('#panel').classList.toggle('min'));
$('#lines').addEventListener('click', () => {
  S.lines = !S.lines;
  try { localStorage.setItem('flights.lines', S.lines ? 'on' : 'off'); } catch (_) {}
  if (map) showLines();
});
$('#zin').addEventListener('click', () => map.zoomIn());
$('#zout').addEventListener('click', () => map.zoomOut());
$('#home').addEventListener('click', () => map.easeTo({ ...HOME(), duration: 900 }));
$('#proj').addEventListener('click', () => {
  const flat = map.getProjection()?.type !== 'mercator';
  map.setProjection({ type: flat ? 'mercator' : 'globe' });
  $('#proj').setAttribute('aria-label', flat ? 'Switch to a globe' : 'Switch to a flat map');
  $('#proj').title = flat ? 'Globe' : 'Flat map';
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && document.activeElement !== $('#q') && selecting()) back();
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (map && map.getLayer('routes')) applyTheme(); });

// map hover and click: airports first; routes only those of the selected airport (in the overview
// there are far too many under the cursor for hovering them to mean anything)
const tip = $('#tip');
function pick(point) {
  const box = (r) => [[point.x - r, point.y - r], [point.x + r, point.y + r]];
  const layers = selecting() ? ['focus-sel', 'focus-dots', 'airports'] : ['airports'];
  if (map.getZoom() >= 3.5 && map.getLayer('others')) layers.push('others');
  const air = map.queryRenderedFeatures(box(7), { layers });
  if (air.length) {
    const rank = f => (f.layer.id.startsWith('focus') ? 1e12 : 0) + f.properties.p;
    air.sort((p, q) => rank(q) - rank(p));
    return { airport: air[0].properties.i };
  }
  if (selecting()) {
    const lines = map.queryRenderedFeatures(box(5), { layers: ['focus'] });
    if (lines.length) {
      lines.sort((p, q) => q.properties.p - p.properties.p);
      return { route: lines[0].properties.i };
    }
  }
  return {};
}
let tipKey = null;
function showTip(point, key, html) {
  if (!key) { tip.classList.remove('show'); tipKey = null; return; }
  if (key !== tipKey) { tip.innerHTML = html(); tipKey = key; }
  tip.style.left = point.x + 'px'; tip.style.top = point.y + 'px';
  tip.classList.add('show');
}
function hoverAt(point) {
  const hit = pick(point);
  map.getCanvasContainer().classList.toggle('pointer', hit.airport !== undefined || hit.route !== undefined);
  if (hit.airport !== undefined) {
    const x = A[hit.airport];
    setHover(null);
    showTip(point, 'a' + x.i, () => x.other
      ? `<b>${esc(x.code)}</b> <i>${esc(x.name)}</i><br><span>${esc(place(x))} · no airline flights</span>`
      : `<b>${esc(x.code)}</b> ${esc(x.name)}<br><span>${esc(place(x))} · ${fmtPax(x.pax)} passengers · ${x.routes.length} routes</span>`);
  } else if (hit.route !== undefined) {
    const r = R[hit.route];
    setHover(r.i);
    showTip(point, 'r' + r.i, () => `<b>${esc(A[r.a].code)} – ${esc(A[r.b].code)}</b> ${esc(A[r.a].city)} – ${esc(A[r.b].city)}<br><span>${paxLabel(r)} passengers a year${isEst(r) ? ' (estimate)' : ''}</span>`);
  } else { setHover(null); showTip(point, null); }
}

function loadOthers() {
  fetch('data/others.json').then(r => r.json()).then(o => {
    Object.assign(D.countries, o.countries);
    D.closed = o.closed || {};
    const features = o.code.map((code, k) => {
      const x = { i: A.length, code, name: o.name[k], city: o.city[k], town: '', cc: o.country[k],
                  lat: o.lat[k], lon: o.lon[k], pax: 0, routes: [], other: true, wiki: wikiTitle(o.wiki && o.wiki[k], o.name[k]) };
      A.push(x);
      return point(x);
    });
    map.getSource('others').setData(collection(features));
    othersLoaded = true;
    index = null;
    if (S.q) render();
    if (hashWaits) readHash();
  }).catch(err => console.warn('No list of other airports', err));
}

function initMap() {
  map = new maplibregl.Map({
    container: 'map', style: baseStyle(pal()), center: HOME().center, zoom: HOME().zoom, minZoom: 0, maxZoom: 16,
    attributionControl: false, dragRotate: false, pitchWithRotate: false, renderWorldCopies: true,
  });
  map.touchZoomRotate.disableRotation();
  map.on('load', () => {
    map.jumpTo(HOME());
    addFlightLayers(pal());
    showSelection();
    readHash();
    $('#loading').classList.add('done');
    loadOthers();
  });
  // at most one lookup per frame, and none while the map is being dragged or zoomed
  let at = null, frame = 0;
  map.on('mousemove', e => {
    at = e.point;
    if (frame || !map.getLayer('routes')) return;
    frame = requestAnimationFrame(() => { frame = 0; if (!map.isMoving()) hoverAt(at); });
  });
  map.on('movestart', () => showTip(null, null));
  map.on('mouseout', () => { setHover(null); showTip(null, null); });
  map.on('click', e => {
    const hit = pick(e.point);
    showTip(null, null);
    if (hit.airport !== undefined) selectAirport(hit.airport, { fly: false });
    else if (hit.route !== undefined) selectRoute(hit.route, { fly: false });
    else if (selecting()) { S.route = null; S.from = null; selectAirport(null, { fly: false }); }
  });
}

fetch('data/flights.json')
  .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
  .then(d => {
    D = d; [A, R] = unpack(d);
    buildGeo();
    render();
    initMap();
    addEventListener('hashchange', readHash);
  })
  .catch(err => {
    console.error(err);
    $('#loading div').innerHTML = '<span>Could not load the flight data.</span>';
  });
})();
