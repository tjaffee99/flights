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
    lat: a.lat[i], lon: a.lon[i], pax: 0, routes: [],
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

function distance(x, y) {
  const s = Math.sin((y.lat - x.lat) * RAD / 2) ** 2 +
            Math.cos(x.lat * RAD) * Math.cos(y.lat * RAD) * Math.sin((y.lon - x.lon) * RAD / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(s)));
}

// the great circle from x to y as [lon, lat] points about every 120 km, in pieces split where it
// crosses the antimeridian (the globe has one world: a longitude past ±180 would leave its surface)
function arc(x, y, km) {
  const n = Math.max(2, Math.ceil(km / 120));
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
  light: { land:'#F2F0EB', water:'#D9E4EA', bnd:'#C3BCB1', label:'#8E8A84', halo:'#F2F0EB', space:'#E9E7E1',
           horizon:'#FFFFFF', route:'#2E4FA8', routeOp:.26, hot:'#E0542E', dot:'#1C1C1E', dotEdge:'#FFFFFF', text:'#1C1C1E' },
  dark:  { land:'#2A2C30', water:'#1A1C20', bnd:'#4A4D53', label:'#7C7E84', halo:'#1A1C20', space:'#0E0F11',
           horizon:'#3A4352', route:'#7FB2FF', routeOp:.26, hot:'#FF9F5A', dot:'#F2F2F7', dotEdge:'#1A1C20', text:'#F2F2F7' },
};
const pal = () => PAL[isDark() ? 'dark' : 'light'];

const OFM = 'https://tiles.openfreemap.org';
const FONT = ['Noto Sans Regular'];
const FONT_BOLD = ['Noto Sans Bold'];

// line width in pixels: the square root of passengers, so a 10M route is ~3x a 1M one, not 10x
const W = ['sqrt', ['/', ['get', 'p'], 1e6]];
const lineWidth = (k = 1, min = 0) => ['interpolate', ['exponential', 1.6], ['zoom'],
  0, ['max', 0.25 + min, ['*', 1.1 * k, W]],
  3, ['max', 0.35 + min, ['*', 2 * k, W]],
  6, ['max', 0.7 + min, ['*', 4.5 * k, W]],
  10, ['max', 1.2 + min, ['*', 10 * k, W]]];
const dotRadius = (add = 0) => ['interpolate', ['linear'], ['zoom'],
  0, ['+', 0.8 + add, ['*', 0.45, ['sqrt', ['/', ['get', 'p'], 1e6]]]],
  4, ['+', 1.6 + add, ['*', 0.9, ['sqrt', ['/', ['get', 'p'], 1e6]]]],
  8, ['+', 3 + add, ['*', 1.6, ['sqrt', ['/', ['get', 'p'], 1e6]]]]];

function baseStyle(p) {
  return {
    version: 8,
    glyphs: OFM + '/fonts/{fontstack}/{range}.pbf',
    projection: { type: 'globe' },
    sky: { 'sky-color': p.space, 'horizon-color': p.horizon, 'fog-color': p.space, 'sky-horizon-blend': 0.6,
           'horizon-fog-blend': 0.6, 'fog-ground-blend': 0.9, 'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 1, 5, 1, 7, 0] },
    sources: { omt: { type: 'vector', url: OFM + '/planet' } },
    layers: [
      { id: 'land', type: 'background', paint: { 'background-color': p.land } },
      { id: 'water', type: 'fill', source: 'omt', 'source-layer': 'water', filter: ['!=', ['get', 'brunnel'], 'tunnel'],
        paint: { 'fill-color': p.water } },
      { id: 'boundary', type: 'line', source: 'omt', 'source-layer': 'boundary',
        filter: ['all', ['==', ['get', 'admin_level'], 2], ['!=', ['get', 'maritime'], 1]],
        paint: { 'line-color': p.bnd, 'line-width': ['interpolate', ['linear'], ['zoom'], 1, 0.4, 6, 1] } },
      { id: 'country-label', type: 'symbol', source: 'omt', 'source-layer': 'place', minzoom: 2.5,
        filter: ['==', ['get', 'class'], 'country'],
        layout: { 'text-field': ['coalesce', ['get', 'name:en'], ['get', 'name']], 'text-font': FONT,
                  'text-size': ['interpolate', ['linear'], ['zoom'], 2, 10, 6, 13], 'text-transform': 'uppercase',
                  'text-letter-spacing': 0.08, 'text-max-width': 7 },
        paint: { 'text-color': p.label, 'text-halo-color': p.halo, 'text-halo-width': 1 } },
    ],
  };
}

function addFlightLayers(p) {
  map.addSource('routes', { type: 'geojson', data: routeGeo, promoteId: 'i', buffer: 4, tolerance: 0.3 });
  map.addSource('airports', { type: 'geojson', data: airportGeo, promoteId: 'i' });
  map.addLayer({ id: 'routes', type: 'line', source: 'routes',
    layout: { 'line-cap': 'round', 'line-join': 'round', 'line-sort-key': ['get', 'p'] },
    paint: { 'line-color': p.route, 'line-opacity': p.routeOp, 'line-width': lineWidth() } });
  // the selected airport's routes: measured solid, estimated dashed
  for (const [id, est] of [['sel', 0], ['sel-est', 1]]) {
    map.addLayer({ id, type: 'line', source: 'routes', filter: ['==', ['get', 'i'], -1],
      layout: { 'line-cap': est ? 'butt' : 'round', 'line-join': 'round', 'line-sort-key': ['get', 'p'] },
      paint: { 'line-color': p.hot, 'line-opacity': 0.9, 'line-width': lineWidth(1.6, 0.8),
               ...(est ? { 'line-dasharray': [2, 1.2] } : {}) } });
  }
  map.addLayer({ id: 'hover', type: 'line', source: 'routes', filter: ['==', ['get', 'i'], -1],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': p.text, 'line-opacity': 0.9, 'line-width': lineWidth(1.6, 1.2) } });
  map.addLayer({ id: 'airports', type: 'circle', source: 'airports',
    layout: { 'circle-sort-key': ['get', 'p'] },
    paint: { 'circle-color': p.dot, 'circle-radius': dotRadius(), 'circle-stroke-color': p.dotEdge,
             'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 0, 0.3, 5, 1],
             'circle-opacity': 0.85, 'circle-stroke-opacity': 0.85,
             'circle-pitch-alignment': 'map' } });
  map.addLayer({ id: 'airports-hot', type: 'circle', source: 'airports', filter: ['==', ['get', 'i'], -1],
    paint: { 'circle-color': p.hot, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 0, 1.8, 5, 3.2, 9, 5],
             'circle-stroke-color': p.dotEdge, 'circle-stroke-width': 1, 'circle-pitch-alignment': 'map' } });
  map.addLayer({ id: 'airport-sel', type: 'circle', source: 'airports', filter: ['==', ['get', 'i'], -1],
    paint: { 'circle-color': p.hot, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 0, 4, 5, 7, 9, 10],
             'circle-stroke-color': p.dotEdge, 'circle-stroke-width': 2, 'circle-pitch-alignment': 'map' } });
  map.addLayer({ id: 'airport-label', type: 'symbol', source: 'airports', minzoom: 3,
    layout: { 'text-field': ['get', 'code'], 'text-font': FONT_BOLD, 'text-size': ['interpolate', ['linear'], ['zoom'], 2, 10, 7, 13],
              'text-offset': [0, 0.9], 'text-anchor': 'top', 'symbol-sort-key': ['-', ['get', 'p']], 'text-padding': 6 },
    paint: { 'text-color': p.text, 'text-halo-color': p.halo, 'text-halo-width': 1.4, 'text-opacity': 0.85 } });
  map.addLayer({ id: 'airport-label-hot', type: 'symbol', source: 'airports', filter: ['==', ['get', 'i'], -1],
    layout: { 'text-field': ['get', 'code'], 'text-font': FONT_BOLD, 'text-size': 12, 'text-offset': [0, 0.9],
              'text-anchor': 'top', 'symbol-sort-key': ['-', ['get', 'p']], 'text-padding': 2 },
    paint: { 'text-color': p.hot, 'text-halo-color': p.halo, 'text-halo-width': 1.6 } });
}

function applyTheme() {
  const p = pal();
  map.setSky(baseStyle(p).sky);
  map.setPaintProperty('land', 'background-color', p.land);
  map.setPaintProperty('water', 'fill-color', p.water);
  map.setPaintProperty('boundary', 'line-color', p.bnd);
  map.setPaintProperty('country-label', 'text-color', p.label);
  map.setPaintProperty('country-label', 'text-halo-color', p.halo);
  map.setPaintProperty('routes', 'line-color', p.route);
  for (const id of ['sel', 'sel-est']) map.setPaintProperty(id, 'line-color', p.hot);
  for (const id of ['airports-hot', 'airport-sel']) {
    map.setPaintProperty(id, 'circle-color', p.hot);
    map.setPaintProperty(id, 'circle-stroke-color', p.dotEdge);
  }
  map.setPaintProperty('hover', 'line-color', p.text);
  map.setPaintProperty('airports', 'circle-color', p.dot);
  map.setPaintProperty('airports', 'circle-stroke-color', p.dotEdge);
  for (const id of ['airport-label', 'airport-label-hot']) map.setPaintProperty(id, 'text-halo-color', p.halo);
  map.setPaintProperty('airport-label', 'text-color', p.text);
  map.setPaintProperty('airport-label-hot', 'text-color', p.hot);
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
  airportGeo = { type: 'FeatureCollection', features: A.filter(x => x.routes.length).map(x => ({
    type: 'Feature',
    properties: { i: x.i, code: x.code, p: x.pax },
    geometry: { type: 'Point', coordinates: [x.lon, x.lat] },
  })) };
}

// ---------------------------------------------------------------- state
const S = {
  airport: null,   // selected airport index
  route: null,     // selected route index
  from: null,      // the airport a route was opened from, for "back"
  hover: null,     // hovered route index
  min: 0,          // overview: minimum passengers per route
  dom: true, intl: true,
  q: '',
};

function overviewFilter() {
  const f = ['all', ['>=', ['get', 'p'], S.min]];
  if (!S.dom) f.push(['==', ['get', 'd'], 0]);
  if (!S.intl) f.push(['==', ['get', 'd'], 1]);
  return f;
}

function showSelection() {
  if (!map || !map.getLayer('sel')) return;
  const p = pal();
  let ids = [];
  if (S.route !== null) ids = [S.route];
  else if (S.airport !== null) ids = A[S.airport].routes;
  const est = ids.filter(i => isEst(R[i])), meas = ids.filter(i => !isEst(R[i]));
  map.setFilter('sel', ['in', ['get', 'i'], ['literal', meas]]);
  map.setFilter('sel-est', ['in', ['get', 'i'], ['literal', est]]);
  map.setFilter('routes', ids.length ? ['all', overviewFilter(), ['!', ['in', ['get', 'i'], ['literal', ids]]]] : overviewFilter());
  map.setPaintProperty('routes', 'line-opacity', ids.length ? (S.route !== null ? 0.05 : isDark() ? 0.07 : 0.09) : p.routeOp);
  const spots = new Set();
  for (const i of ids) { spots.add(R[i].a); spots.add(R[i].b); }
  const list = [...spots];
  map.setFilter('airports-hot', ['in', ['get', 'i'], ['literal', list]]);
  map.setFilter('airport-sel', ['==', ['get', 'i'], S.route === null && S.airport !== null ? S.airport : -1]);
  map.setFilter('airport-label-hot', ['in', ['get', 'i'], ['literal', list]]);
  map.setPaintProperty('airports', 'circle-opacity', ids.length ? 0.35 : 0.85);
  map.setPaintProperty('airports', 'circle-stroke-opacity', ids.length ? 0.35 : 0.85);
  map.setLayoutProperty('airport-label', 'visibility', ids.length ? 'none' : 'visible');
  setHover(S.hover);
}

function setHover(i) {
  S.hover = i;
  if (map && map.getLayer('hover')) map.setFilter('hover', ['==', ['get', 'i'], i === null ? -1 : i]);
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
  return `<button class="row" data-airport="${x.i}"><span class="code">${esc(x.code)}</span>
    <span class="t"><b>${esc(x.name)}</b><span>${esc(place(x))}</span></span>
    <span class="n">${fmtPax(x.pax)}<small>${x.routes.length} routes</small></span></button>`;
}

function overview() {
  const total = R.reduce((s, r) => s + r.pax, 0);
  const measured = R.reduce((s, r) => s + (isEst(r) ? 0 : r.pax), 0);
  const live = A.filter(x => x.routes.length).length;
  const topRoutes = [...R].sort((p, q) => q.pax - p.pax).slice(0, 12);
  const topAirports = A.filter(x => x.routes.length).sort((p, q) => q.pax - p.pax).slice(0, 12);
  const seg = (vals) => vals.map(([v, label]) => `<button data-min="${v}" aria-pressed="${S.min === v}">${label}</button>`).join('');
  const legend = [[1e5, '100K passengers a year'], [1e6, '1 million'], [1e7, '10 million']].map(([v, label]) =>
    `<i style="height:${Math.max(1, 2 * Math.sqrt(v / 1e6)).toFixed(1)}px"></i><span>${label}</span>`).join('');
  return `<div class="intro"><h1>Flight Atlas</h1>
    <p>Every passenger air route flying today, drawn by how many people fly it in a year. Click an airport to see everywhere it flies nonstop.</p></div>
  <div class="stats"><div><b>${fmtInt(R.length)}</b><span>routes</span></div><div><b>${fmtInt(live)}</b><span>airports</span></div>
    <div><b>${(total / 1e9).toFixed(2)}B</b><span>passengers a year</span></div></div>
  <div class="opts"><span>Show routes</span><span class="seg" id="minseg">${seg([[0, 'All'], [1e5, '100K+'], [1e6, '1M+']])}</span></div>
  <div class="chips"><button class="chip" id="cdom" aria-pressed="${S.dom}">Domestic</button><button class="chip" id="cintl" aria-pressed="${S.intl}">International</button></div>
  <div class="legend">${legend}<i class="hot" style="height:2px"></i><span>Selected airport's routes</span><i class="dash"></i><span>Selected, passengers estimated</span></div>
  <div class="sec">Busiest routes</div>${topRoutes.map(r => routeRow(r, null, topRoutes[0].pax)).join('')}
  <div class="sec">Busiest airports</div>${topAirports.map(airportRow).join('')}
  <div class="note small">Routes are the ones airlines fly as of ${esc(D.routes_as_of)}, from Wikipedia's airport pages.
    Passengers are counted for ${Math.round(100 * measured / total)}% of travel (${esc([...new Set(D.sources.filter(s => s.id).map(s => s.short))].join(', '))});
    the rest are estimates fitted to each airport’s annual total. <a href="${esc(D.about)}" target="_blank" rel="noopener">How this was made</a></div>`;
}

function airportView(x) {
  const routes = x.routes.map(i => R[i]);
  const countries = new Set(routes.map(r => A[r.a === x.i ? r.b : r.a].cc));
  const est = routes.filter(isEst).reduce((s, r) => s + r.pax, 0);
  const top = routes.length ? routes[0].pax : 1;
  return `<button class="back" data-back>${BACK}All routes</button>
  <div class="dhead"><span class="code big">${esc(x.code)}</span><div><h2>${esc(x.name)}</h2><div class="sub">${esc(place(x))}</div></div></div>
  <div class="stats"><div><b>${(est > x.pax / 2 ? '≈ ' : '') + fmtPax(x.pax)}</b><span>passengers a year</span></div>
    <div><b>${routes.length}</b><span>nonstop destinations</span></div><div><b>${countries.size}</b><span>countries</span></div></div>
  <div class="sec">Destinations by passengers</div>
  ${routes.map(r => routeRow(r, x.i, top)).join('') || '<div class="note">No current passenger routes.</div>'}
  ${est ? `<div class="note small">≈ marks estimated passengers. Dashed lines on the map are estimates too.</div>` : ''}`;
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
  if (!index) index = A.filter(x => x.routes.length).map(x => ({ x, code: x.code.toLowerCase(), text: fold([x.name, x.city, x.town, country(x.cc)].join(' ')) }));
  const f = fold(q).trim();
  if (!f) return [];
  const words = f.split(/\s+/);
  const hits = [];
  for (const e of index) {
    let score = 0;
    if (e.code === f) score = 100;
    else if (words.every(w => e.text.includes(w))) score = 10 + (e.text.startsWith(f) ? 5 : 0) + (fold(e.x.city).startsWith(f) ? 5 : 0);
    else if (f.length >= 2 && e.code.startsWith(f)) score = 8;
    if (score) hits.push([score + Math.log10(1 + e.x.pax) / 10, e.x]);
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
    map.easeTo({ center: [x.lon, x.lat], zoom: Math.max(map.getZoom(), phone() ? 1.2 : 1.9), duration: 900, padding: panelPadding() });
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
function readHash() {
  const m = decodeURIComponent(location.hash.slice(1)).toUpperCase().match(/^([A-Z0-9]{3})(?:-([A-Z0-9]{3}))?$/);
  if (!m) return;
  const byCode = c => A.find(x => x.code === c && x.routes.length);
  const x = byCode(m[1]);
  if (!x) return;
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
  else if (t.dataset.min !== undefined) { S.min = +t.dataset.min; render(); showSelection(); }
  else if (t.id === 'cdom' || t.id === 'cintl') {
    const k = t.id === 'cdom' ? 'dom' : 'intl';
    S[k] = !S[k];
    if (!S.dom && !S.intl) S[k === 'dom' ? 'intl' : 'dom'] = true;
    render(); showSelection();
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
  if (e.key === 'Escape' && document.activeElement !== $('#q') && (S.airport !== null || S.route !== null)) back();
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (map && map.getLayer('routes')) applyTheme(); });

// map hover and click: airports first, then routes
const tip = $('#tip');
function pick(point) {
  const box = (r) => [[point.x - r, point.y - r], [point.x + r, point.y + r]];
  const air = map.queryRenderedFeatures(box(7), { layers: ['airport-sel', 'airports-hot', 'airports'] });
  if (air.length) {
    air.sort((p, q) => q.properties.p - p.properties.p);
    return { airport: air[0].properties.i };
  }
  const layers = S.airport !== null || S.route !== null ? ['sel', 'sel-est'] : ['routes'];
  const lines = map.queryRenderedFeatures(box(4), { layers });
  if (lines.length) {
    lines.sort((p, q) => q.properties.p - p.properties.p);
    return { route: lines[0].properties.i };
  }
  return {};
}
function showTip(point, html) {
  if (!html) { tip.classList.remove('show'); return; }
  tip.innerHTML = html;
  tip.style.left = point.x + 'px'; tip.style.top = point.y + 'px';
  tip.classList.add('show');
}

function initMap() {
  map = new maplibregl.Map({
    container: 'map', style: baseStyle(pal()), center: HOME().center, zoom: HOME().zoom, minZoom: 0, maxZoom: 10,
    attributionControl: false, dragRotate: false, pitchWithRotate: false, renderWorldCopies: true,
  });
  map.touchZoomRotate.disableRotation();
  map.on('load', () => {
    map.jumpTo(HOME());
    addFlightLayers(pal());
    showSelection();
    readHash();
    $('#loading').classList.add('done');
  });
  map.on('mousemove', e => {
    if (!map.getLayer('routes')) return;
    const hit = pick(e.point);
    map.getCanvasContainer().classList.toggle('pointer', hit.airport !== undefined || hit.route !== undefined);
    if (hit.airport !== undefined) {
      const x = A[hit.airport];
      setHover(null);
      showTip(e.point, `<b>${esc(x.code)}</b> ${esc(x.name)}<br><span>${esc(place(x))} · ${fmtPax(x.pax)} passengers · ${x.routes.length} routes</span>`);
    } else if (hit.route !== undefined) {
      const r = R[hit.route];
      setHover(r.i);
      showTip(e.point, `<b>${esc(A[r.a].code)} – ${esc(A[r.b].code)}</b> ${esc(A[r.a].city)} – ${esc(A[r.b].city)}<br><span>${paxLabel(r)} passengers a year${isEst(r) ? ' (estimate)' : ''}</span>`);
    } else { setHover(null); showTip(e.point, null); }
  });
  map.on('mouseout', () => { setHover(null); showTip(null, null); });
  map.on('click', e => {
    const hit = pick(e.point);
    showTip(null, null);
    if (hit.airport !== undefined) selectAirport(hit.airport, { fly: false });
    else if (hit.route !== undefined) selectRoute(hit.route, { fly: false });
    else if (S.airport !== null || S.route !== null) { S.route = null; S.from = null; selectAirport(null, { fly: false }); }
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
