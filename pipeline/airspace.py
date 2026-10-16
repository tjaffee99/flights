"""The way a flight goes around airspace its airline avoids: Russia for European and American
airlines, Pakistan for Indian ones, conflict zones (see overrides.json's 'airspace').

Countries come from Natural Earth's 1:50m boundaries (public domain), drawn onto a half-degree grid:
a cell is closed if any part of it touches an avoided country, which leaves a margin of up to about
25 km. A route whose great circle stays clear flies it. One that doesn't takes the shortest way
across open cells (Dijkstra, with knight's moves so it isn't held to eight directions), then pulled
straight: from each point, on to the farthest point it can see. What's left is a few great-circle
legs, like the waypoints of a real flight plan.

    python3 airspace.py LHR NRT RU     # try one: the way from Heathrow to Narita around Russia
"""

import json
import math
import sys
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = HERE / 'cache'
BORDERS = CACHE / 'naturalearth' / 'ne_50m_admin_0_countries.geojson'
BORDERS_URL = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson'

STEP = 0.5                      # degrees a grid cell
ROWS, COLS = int(180 / STEP), int(360 / STEP)
FINE = 5                        # each cell is drawn as FINE x FINE pixels: closed if any is in the country
EARTH = 6371.0
NEAR_END = 80                   # km of a flight's start and end that may cross a margin (Vilnius is 30 km from Belarus)


def borders():
    if not BORDERS.exists():
        BORDERS.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(BORDERS_URL, headers={'User-Agent': 'FlightAtlasBuilder/0.1'})
        with urllib.request.urlopen(request, timeout=300) as response:
            BORDERS.write_bytes(response.read())
    shapes = {}
    for feature in json.loads(BORDERS.read_text())['features']:
        p = feature['properties']
        code = p.get('ISO_A2_EH') if p.get('ISO_A2_EH') not in (None, '-99') else p.get('ISO_A2')
        geometry = feature['geometry']
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        shapes.setdefault(code, []).extend(polygon[0] for polygon in polygons)   # outer rings
    return shapes


def unit(lat, lon):
    lat, lon = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1)


def km(a, b):
    """Great-circle km between (lat, lon) points."""
    return EARTH * math.acos(max(-1.0, min(1.0, float(np.dot(unit(*a), unit(*b))))))


def eastward(points):
    """How far a path runs east through the jet streams, in km: each stretch's eastward km weighted by
    how strong the westerlies usually are at its latitude (most at 30-50 degrees, north or south).
    Negative going west. points: (lat, lon), the ends and any waypoints."""
    total = 0.0
    for p, q in zip(points, points[1:]):
        d = km(p, q)
        if d < 1:
            continue
        n = max(2, int(d / 100))
        t = np.linspace(0, 1, n + 1)
        u, v = unit(*p), unit(*q)
        omega = math.acos(max(-1.0, min(1.0, float(np.dot(u, v)))))
        pts = (np.sin((1 - t) * omega)[:, None] * u + np.sin(t * omega)[:, None] * v) / math.sin(omega)
        lat = np.arcsin(np.clip(pts[:, 2], -1, 1))
        lon = np.arctan2(pts[:, 1], pts[:, 0])
        dlon = (np.diff(lon) + math.pi) % (2 * math.pi) - math.pi
        mid = (lat[1:] + lat[:-1]) / 2
        weight = np.exp(-((np.degrees(np.abs(mid)) - 40) / 15) ** 2)
        total += float(np.sum(EARTH * dlon * np.cos(mid) * weight))
    return total


class Airspace:
    def __init__(self, countries, regions=None):
        """countries: the codes any rule avoids; regions: more shapes to avoid, name -> [[lon, lat], ...]."""
        from PIL import Image, ImageDraw
        shapes = borders()
        for name, ring in (regions or {}).items():
            shapes[name] = [ring]
        self.masks = {}
        for code in countries:
            image = Image.new('1', (COLS * FINE, ROWS * FINE), 0)
            draw = ImageDraw.Draw(image)
            for ring in shapes.get(code, []):
                draw.polygon([((lon + 180) / STEP * FINE, (90 - lat) / STEP * FINE) for lon, lat in ring], fill=1, outline=1)
            fine = np.array(image, dtype=bool)[::-1]          # row 0 = the south
            self.masks[code] = fine.reshape(ROWS, FINE, COLS, FINE).any(axis=(1, 3))
        lat = -90 + (np.arange(ROWS) + 0.5) * STEP
        lon = -180 + (np.arange(COLS) + 0.5) * STEP
        self.lat, self.lon = np.meshgrid(lat, lon, indexing='ij')
        self.xyz = unit(self.lat, self.lon).reshape(-1, 3)
        self._edges = None
        self._graphs = {}

    def closed(self, avoid):
        mask = np.zeros((ROWS, COLS), dtype=bool)
        for code in avoid:
            mask |= self.masks.get(code, False)
        return mask

    def cell(self, lat, lon):
        r = np.clip(((np.asarray(lat) + 90) / STEP).astype(int), 0, ROWS - 1)
        c = ((np.asarray(lon) + 180) / STEP).astype(int) % COLS
        return r, c

    def clear(self, p, q, mask, ends=(False, False)):
        """Whether the great circle from p to q stays out of closed cells (but for NEAR_END km of an
        end that's an airport)."""
        d = km(p, q)
        if d < 1:
            return True
        n = max(2, int(d / 15))
        t = np.linspace(0, 1, n)
        u, v = unit(*p), unit(*q)
        omega = math.acos(max(-1.0, min(1.0, float(np.dot(u, v)))))
        pts = (np.sin((1 - t) * omega)[:, None] * u + np.sin(t * omega)[:, None] * v) / math.sin(omega)
        lat = np.degrees(np.arcsin(np.clip(pts[:, 2], -1, 1)))
        lon = np.degrees(np.arctan2(pts[:, 1], pts[:, 0]))
        keep = np.ones(n, dtype=bool)
        if ends[0]:
            keep &= t * d > NEAR_END
        if ends[1]:
            keep &= (1 - t) * d > NEAR_END
        r, c = self.cell(lat[keep], lon[keep])
        return not mask[r, c].any()

    def edges(self):
        """Every grid move, to the 8 neighbours and by the 8 knight's moves: (from, to, km, kind, the two
        cells it passes beside). A straight move needs only its end open; a diagonal one of the two
        corners it cuts; a knight's move both cells it jumps past."""
        if self._edges is None:
            r, c = np.meshgrid(np.arange(ROWS), np.arange(COLS), indexing='ij')
            r, c = r.ravel(), c.ravel()
            parts = []
            for dr, dc in [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1),
                           (1, 2), (1, -2), (-1, 2), (-1, -2), (2, 1), (2, -1), (-2, 1), (-2, -1)]:
                ok = (r + dr >= 0) & (r + dr < ROWS)
                ra, ca = r[ok], c[ok]
                cellid = lambda rr, cc: rr * COLS + cc % COLS
                b = cellid(ra + dr, ca + dc)
                if dr == 0 or dc == 0:
                    kind, v1, v2 = 0, b, b
                elif abs(dr) == abs(dc):
                    kind, v1, v2 = 1, cellid(ra + dr, ca), cellid(ra, ca + dc)
                elif abs(dr) == 2:
                    kind, v1, v2 = 2, cellid(ra + np.sign(dr), ca), cellid(ra + np.sign(dr), ca + dc)
                else:
                    kind, v1, v2 = 2, cellid(ra, ca + np.sign(dc)), cellid(ra + dr, ca + np.sign(dc))
                parts.append((cellid(ra, ca), b, np.full(len(b), kind), v1, v2))
            src, dst, kind, v1, v2 = (np.concatenate(x) for x in zip(*parts))
            dot = np.clip(np.einsum('ij,ij->i', self.xyz[src], self.xyz[dst]), -1, 1)
            self._edges = (src, dst, EARTH * np.arccos(dot), kind, v1, v2)
        return self._edges

    def graph(self, avoid):
        from scipy.sparse import csr_matrix
        key = frozenset(avoid)
        if key not in self._graphs:
            mask = self.closed(avoid).ravel()
            src, dst, w, kind, v1, v2 = self.edges()
            beside = np.where(kind == 2, ~mask[v1] & ~mask[v2], np.where(kind == 1, ~mask[v1] | ~mask[v2], True))
            ok = ~mask[src] & ~mask[dst] & beside
            n = ROWS * COLS
            self._graphs[key] = (csr_matrix((w[ok], (src[ok], dst[ok])), shape=(n, n)), mask.reshape(ROWS, COLS))
        return self._graphs[key]

    def inside(self, name, lat, lon):
        """Whether a point is in (or in the margin of) a country or region."""
        r, c = self.cell(lat, lon)
        return bool(self.masks[name][r, c]) if name in self.masks else False

    def nearest_open(self, lat, lon, mask):
        r0, c0 = self.cell(lat, lon)
        r0, c0 = int(r0), int(c0)
        for ring in range(0, 12):
            best = None
            for dr in range(-ring, ring + 1):
                for dc in range(-ring, ring + 1):
                    if max(abs(dr), abs(dc)) != ring:
                        continue
                    r, c = r0 + dr, (c0 + dc) % COLS
                    if 0 <= r < ROWS and not mask[r, c]:
                        d = km((lat, lon), (self.lat[r, c], self.lon[r, c]))
                        if best is None or d < best[0]:
                            best = (d, r * COLS + c)
            if best:
                return best[1]
        return None

    def ways(self, origin, targets, avoid):
        """The way from origin to each target (all (lat, lon)) around the avoided countries:
        {target index: (km, [waypoints between the ends])}, or None for a target the great circle
        reaches directly (or that can't be reached at all)."""
        from scipy.sparse.csgraph import dijkstra
        g, mask = self.graph(avoid)
        out = {}
        pending = [k for k, t in enumerate(targets) if not self.clear(origin, t, mask, (True, True))]
        if not pending:
            return out
        start = self.nearest_open(*origin, mask)
        if start is None:
            return out
        _, before = dijkstra(g, indices=start, return_predecessors=True)
        for k in pending:
            target = targets[k]
            end = self.nearest_open(*target, mask)
            if end is None or (end != start and before[end] < 0):
                continue
            cells = [end]
            while cells[-1] != start:
                cells.append(before[cells[-1]])
            pts = [origin] + [(float(self.lat.flat[i]), float(self.lon.flat[i])) for i in reversed(cells)] + [target]
            way = self.pull(pts, mask)
            out[k] = (sum(km(p, q) for p, q in zip(way, way[1:])), way[1:-1])
        return out

    def pull(self, pts, mask):
        """Straighten a path of points: from each kept point, on to the farthest it can see."""
        way, i, n = [pts[0]], 0, len(pts)
        while i < n - 1:
            j = n - 1
            while j > i + 1 and not self.clear(pts[i], pts[j], mask, (i == 0, j == n - 1)):
                j = max(i + 1, j - max(1, (j - i) // 8)) if j - i > 16 else j - 1
            way.append(pts[j])
            i = j
        return way


if __name__ == '__main__':
    import csv
    a, b, *avoid = sys.argv[1:]
    regions = json.loads((HERE / 'overrides.json').read_text())['airspace']['regions']
    where = {}
    with open(CACHE / 'airports.csv', newline='') as f:
        for row in csv.DictReader(f):
            if row['iata_code'] in (a, b) and row['type'] != 'closed':
                where[row['iata_code']] = (float(row['latitude_deg']), float(row['longitude_deg']))
    space = Airspace(avoid, regions)
    direct = km(where[a], where[b])
    found = space.ways(where[a], [where[b]], avoid).get(0)
    if not found:
        print(f'{a}-{b}: {direct:,.0f} km, the great circle is clear of {", ".join(avoid)}')
    else:
        print(f'{a}-{b}: {found[0]:,.0f} km around {", ".join(avoid)} (great circle {direct:,.0f} km, +{100 * (found[0] / direct - 1):.0f}%)')
        print('  via ' + ', '.join(f'{lat:.1f},{lon:.1f}' for lat, lon in found[1]))
