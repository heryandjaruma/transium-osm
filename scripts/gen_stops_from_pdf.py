"""Generate a bus_stops GeoPackage for one corridor from Bus_Map_of_Denpasar_Raya.pdf.

The transit map is a vector PDF where every stop is a small dark triangle sitting on
a coloured corridor line, and every triangle carries a link annotation pointing at a
Google Maps short URL. That gives us three things per stop:

  * the corridor it belongs to      -> the triangle sits on the corridor's coloured line
  * the direction it serves         -> the triangle points the way the bus travels
  * the real world coordinate       -> resolve the maps.app.goo.gl short link

Names come from the label text placed immediately beside each triangle.

Usage:
    python scripts/gen_stops_from_pdf.py K5B
    python scripts/gen_stops_from_pdf.py K5B --no-network   # geometry/labels only, no gpkg
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "Bus_Map_of_Denpasar_Raya.pdf"
CACHE = ROOT / "temps" / "goo_gl_cache.json"
OUT_DIR = ROOT / "temps"

MAP_PAGE = 0

# Okabe-Ito palette used by the map, keyed by the corridor badge number in the legend.
CORRIDOR_COLOURS = {
    1: (0.835, 0.369, 0.0),
    2: (0.0, 0.447, 0.698),
    3: (0.086, 0.298, 0.392),
    4: (0.0, 0.62, 0.451),
    5: (0.902, 0.624, 0.0),
    6: (0.337, 0.706, 0.914),
}

MARKER_FILL = (0.141, 0.141, 0.129)  # dark triangle used for every stop marker

# Tolerances, all in PDF points.
STOP_ON_LINE_TOL = 4.0    # link rect centre -> corridor line
MARKER_TOL = 3.0          # link rect centre -> stop triangle
LABEL_TOL = 16.0          # stop -> its name label
TERMINUS_TOL = 12.0       # terminal capsules sit further off the line than platforms
GAP_BRIDGE_TOL = 30.0     # join corridor line pieces broken by crossings
WRAP_PITCH = 7.0          # line pitch below which two text lines are one wrapped label


@dataclass
class Corridor:
    ref: str
    legend_no: int
    operator: str
    origin: str          # full-match regex against stop labels
    destination: str     # full-match regex against stop labels
    origin_index: int    # the index this terminus has in the route's own sorting
    direction: str       # value for the `direction` field (the destination id)
    name_overrides: dict = field(default_factory=dict)


CORRIDORS = {
    "K5B": Corridor(
        ref="K5B",
        legend_no=5,
        operator="Trans Metro Dewata",
        origin=r"Politeknik Negeri Bali",
        destination=r"Sentral Parkir Kuta",
        origin_index=0,
        direction="1",
    ),
}


# --------------------------------------------------------------------------- geometry


def _pt(p):
    return (p.x, p.y)


def corridor_segments(page, colour):
    """Every stroked segment drawn in the corridor colour, as ((x0,y0),(x1,y1))."""
    segs = []
    for drawing in page.get_drawings():
        stroke = drawing.get("color")
        if not stroke or tuple(round(v, 3) for v in stroke) != colour:
            continue
        for item in drawing["items"]:
            if item[0] == "l":
                segs.append((_pt(item[1]), _pt(item[2])))
            elif item[0] == "c":
                segs.append((_pt(item[1]), _pt(item[4])))
    return segs


def point_seg_distance(p, a, b):
    (ax, ay), (bx, by) = a, b
    px, py = p
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    t = 0.0 if denom == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def build_graph(segments):
    """Adjacency over snapped segment endpoints, with short gaps bridged.

    The corridor line is chopped wherever another line crosses it, so the raw
    segments form many disconnected pieces. Bridging component endpoints that are
    within GAP_BRIDGE_TOL of each other stitches them back into one network.
    """
    snap = lambda p: (round(p[0], 1), round(p[1], 1))
    graph: dict[tuple, set] = {}
    for a, b in segments:
        sa, sb = snap(a), snap(b)
        graph.setdefault(sa, set()).add(sb)
        graph.setdefault(sb, set()).add(sa)

    # Connected components, so we only bridge across genuine breaks.
    comp_of, comps = {}, []
    for node in graph:
        if node in comp_of:
            continue
        stack, comp = [node], []
        while stack:
            cur = stack.pop()
            if cur in comp_of:
                continue
            comp_of[cur] = len(comps)
            comp.append(cur)
            stack.extend(n for n in graph[cur] if n not in comp_of)
        comps.append(comp)

    loose = [n for n in graph if len(graph[n]) <= 1]
    for i, a in enumerate(loose):
        for b in loose[i + 1:]:
            if comp_of[a] == comp_of[b]:
                continue
            if math.dist(a, b) <= GAP_BRIDGE_TOL:
                graph[a].add(b)
                graph[b].add(a)
    return graph


def shortest_path(graph, start, goal):
    import heapq

    dist = {start: 0.0}
    prev: dict = {}
    heap = [(0.0, start)]
    seen = set()
    while heap:
        d, node = heapq.heappop(heap)
        if node in seen:
            continue
        seen.add(node)
        if node == goal:
            break
        for nb in graph[node]:
            nd = d + math.dist(node, nb)
            if nd < dist.get(nb, math.inf):
                dist[nb] = nd
                prev[nb] = node
                heapq.heappush(heap, (nd, nb))
    if goal not in dist:
        raise RuntimeError("corridor line is not connected between the two termini")
    path, cur = [goal], goal
    while cur != start:
        cur = prev[cur]
        path.append(cur)
    return path[::-1]


def project_on_path(path, p):
    """(distance along path, perpendicular distance, unit tangent) for the closest point."""
    best = None
    travelled = 0.0
    for a, b in zip(path, path[1:]):
        seg_len = math.dist(a, b)
        if seg_len == 0:
            continue
        dx, dy = (b[0] - a[0]) / seg_len, (b[1] - a[1]) / seg_len
        t = max(0.0, min(seg_len, (p[0] - a[0]) * dx + (p[1] - a[1]) * dy))
        perp = math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))
        if best is None or perp < best[1]:
            best = (travelled + t, perp, (dx, dy))
        travelled += seg_len
    return best if best else (0.0, math.inf, (1.0, 0.0))


# ----------------------------------------------------------------------------- stops


def link_stops(page):
    """Cluster the Google Maps link annotations into one entry per stop."""
    raw = [l for l in page.get_links() if "maps.app.goo.gl" in l.get("uri", "")]
    clusters: list[dict] = []
    for link in raw:
        rect = link["from"]
        centre = ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        for c in clusters:
            if c["uri"] == link["uri"] and math.dist(c["centre"], centre) < 8:
                c["rects"].append(rect)
                xs = [r.x0 for r in c["rects"]] + [r.x1 for r in c["rects"]]
                ys = [r.y0 for r in c["rects"]] + [r.y1 for r in c["rects"]]
                c["centre"] = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
                break
        else:
            clusters.append({"uri": link["uri"], "centre": centre, "rects": [rect]})
    return clusters


def stop_markers(page):
    """Dark triangles with their pointing direction, i.e. the direction of travel."""
    markers = []
    for drawing in page.get_drawings():
        fill = drawing.get("fill")
        if not fill or tuple(round(v, 3) for v in fill) != MARKER_FILL:
            continue
        pts = [_pt(item[1]) for item in drawing["items"] if item[0] == "l"]
        pts += [_pt(item[2]) for item in drawing["items"] if item[0] == "l"]
        uniq = []
        for p in pts:
            if not any(math.dist(p, q) < 0.05 for q in uniq):
                uniq.append(p)
        if len(uniq) != 3:
            continue
        # The markers are isoceles arrows: the tip is the vertex furthest from the
        # centroid, and the bus travels tip-ward.
        centre = (sum(p[0] for p in uniq) / 3, sum(p[1] for p in uniq) / 3)
        apex = max(uniq, key=lambda p: math.dist(p, centre))
        base = [p for p in uniq if p is not apex]
        mid = ((base[0][0] + base[1][0]) / 2, (base[0][1] + base[1][1]) / 2)
        vx, vy = apex[0] - mid[0], apex[1] - mid[1]
        norm = math.hypot(vx, vy) or 1.0
        markers.append({"centre": centre, "dir": (vx / norm, vy / norm)})
    return markers


# ---------------------------------------------------------------------------- labels


BADGE = re.compile(r"^(S1|B|I1|[1-9][0-9]?)$")


def label_candidates(page):
    """Stop name labels, with wrapped lines merged back into one label.

    Name text is 5pt (6pt for termini). The 3pt parenthetical under some names is a
    platform qualifier - "(Barat)", "(Timur)" - and is part of the name; the 4pt line
    under others is a local alias and is not. Single digits and "S1" are the coloured
    corridor badges printed along the lines, not names.
    """
    runs = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if abs(line["dir"][0]) < 0.99:  # rotated text: street names, never stops
                continue
            main = [s for s in line["spans"]
                    if s["font"].startswith("IBMPlexSans") and "Italic" not in s["font"]
                    and (4.6 <= s["size"] <= 6.6
                         or (s["size"] >= 2.8 and s["text"].strip().startswith("(")))]
            if not main:
                continue
            text = "".join(s["text"] for s in main).strip()
            if not text or text in {"-", "→"} or BADGE.match(text):
                continue
            runs.append({
                "text": text,
                "bbox": [min(s["bbox"][0] for s in main), min(s["bbox"][1] for s in main),
                         max(s["bbox"][2] for s in main), max(s["bbox"][3] for s in main)],
                "last_y0": min(s["bbox"][1] for s in main),
            })

    runs.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
    merged: list[dict] = []
    for run in runs:
        host = None
        for prev in reversed(merged):
            px0, _, px1, _ = prev["bbox"]
            x0, y0, x1, y1 = run["bbox"]
            if prev["text"].endswith(")"):
                continue  # a "(Barat)"-style qualifier closes the label
            overlap = min(px1, x1) - max(px0, x0)
            width = min(px1 - px0, x1 - x0)
            if 0 < y0 - prev["last_y0"] < WRAP_PITCH and overlap > 0.5 * width:
                host = prev
                break
        if host is None:
            merged.append(dict(run))
            continue
        x0, y0, x1, y1 = run["bbox"]
        host["text"] += " " + run["text"]
        host["bbox"] = [min(host["bbox"][0], x0), min(host["bbox"][1], y0),
                        max(host["bbox"][2], x1), max(host["bbox"][3], y1)]
        host["last_y0"] = y0
    for label in merged:
        label["text"] = re.sub(r"\s+\(", " (", label["text"])
    return merged


def rect_distance(bbox, p):
    x0, y0, x1, y1 = bbox
    dx = max(x0 - p[0], 0, p[0] - x1)
    dy = max(y0 - p[1], 0, p[1] - y1)
    return math.hypot(dx, dy)


def assign_labels(stops, labels):
    """Greedy globally-nearest one-to-one matching between stops and labels.

    Where the two platforms of a stop share a single printed label (termini, and
    stops labelled only on one side of the line) the leftover platform falls back to
    the nearest label, so both sides end up with the same name.
    """
    pairs = []
    for si, stop in enumerate(stops):
        for li, label in enumerate(labels):
            d = rect_distance(label["bbox"], stop["centre"])
            if d <= LABEL_TOL:
                pairs.append((d, si, li))
    pairs.sort()
    taken_stop, taken_label, out, shared = set(), set(), {}, set()
    for d, si, li in pairs:
        if si in taken_stop or li in taken_label:
            continue
        taken_stop.add(si)
        taken_label.add(li)
        out[si] = labels[li]["text"]
    for d, si, li in pairs:
        if si in out:
            continue
        out[si] = labels[li]["text"]
        shared.add(si)
    return out, shared


# -------------------------------------------------------------------------- geocoding


def resolve_coordinates(uris, delay=0.4):
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [u for u in uris if u not in cache]
    for i, uri in enumerate(todo, 1):
        req = urllib.request.Request(uri, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                final = resp.geturl()
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"  ! {uri}: {exc}")
            continue
        m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", final)
        if not m:
            m = re.search(r"/@(-?\d+\.\d+),(-?\d+\.\d+)", final)
        if m:
            cache[uri] = [float(m.group(1)), float(m.group(2))]
        else:
            print(f"  ! no coordinate in {final[:120]}")
        print(f"  resolved {i}/{len(todo)}", end="\r")
        time.sleep(delay)
    if todo:
        print()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, indent=1))
    return cache


# ------------------------------------------------------------------------------- main


def collect(corridor: Corridor):
    doc = pymupdf.open(PDF)
    page = doc[MAP_PAGE]
    colour = CORRIDOR_COLOURS[corridor.legend_no]

    segments = corridor_segments(page, colour)
    if not segments:
        raise SystemExit(f"no line drawn in colour {colour}")

    all_stops = link_stops(page)
    names, shared = assign_labels(all_stops, label_candidates(page))
    markers = stop_markers(page)

    def entry(i, stop, marker=None):
        return {
            "uri": stop["uri"],
            "centre": stop["centre"],
            "name": names.get(i, ""),
            "shared_label": i in shared,
            "dir": marker["dir"] if marker else None,
            "line_distance": min(point_seg_distance(stop["centre"], a, b) for a, b in segments),
        }

    on_line, near_line = [], []
    for i, stop in enumerate(all_stops):
        cand = entry(i, stop)
        if cand["line_distance"] > TERMINUS_TOL:
            continue
        near_line.append(cand)
        marker = min(markers, key=lambda m: math.dist(m["centre"], stop["centre"]), default=None)
        if cand["line_distance"] > STOP_ON_LINE_TOL or marker is None:
            continue
        if math.dist(marker["centre"], stop["centre"]) > MARKER_TOL:
            continue
        on_line.append({**cand, "dir": marker["dir"]})

    def anchor(pattern, label):
        hits = [s for s in near_line if re.fullmatch(pattern, s["name"])]
        if not hits:
            raise SystemExit(f"no stop near corridor {corridor.ref} matches {label} /{pattern}/")
        return min(hits, key=lambda s: s["line_distance"])

    origin = anchor(corridor.origin, "origin")
    destination = anchor(corridor.destination, "destination")

    graph = build_graph(segments)
    nodes = list(graph)
    near = lambda p: min(nodes, key=lambda n: math.dist(n, p))
    start, goal = near(origin["centre"]), near(destination["centre"])
    # The corridor is drawn as two parallel lines, one per direction, joined only at
    # the termini. Walking origin -> destination twice, the second time with the first
    # walk's interior removed, yields one line each - both oriented the way we travel.
    lines = [shortest_path(graph, start, goal)]
    trimmed = {n for n in lines[0][1:-1]
               if math.dist(n, start) > 20 and math.dist(n, goal) > 20}
    pruned = {n: {m for m in nbrs if m not in trimmed}
              for n, nbrs in graph.items() if n not in trimmed}
    try:
        lines.append(shortest_path(pruned, start, goal))
    except (RuntimeError, KeyError):
        print("  note: could not isolate the second direction line; using one line only")

    ordered = []
    for stop in on_line:
        if stop["uri"] in {origin["uri"], destination["uri"]}:
            continue
        along, perp, _ = project_on_path(lines[0], stop["centre"])
        if perp > 12:
            continue
        # Test the marker against the tangent of the line the stop actually sits on:
        # the sister line can run at right angles to it around terminal loops.
        own = min(lines, key=lambda ln: project_on_path(ln, stop["centre"])[1])
        tangent = project_on_path(own, stop["centre"])[2]
        if stop["dir"][0] * tangent[0] + stop["dir"][1] * tangent[1] < 0.6:
            continue
        ordered.append({**stop, "along": along})
    ordered.sort(key=lambda s: s["along"])

    # The termini are marked with a single arrow (arrival) or a terminal capsule
    # rather than one arrow per direction, so they are placed by name, not by marker.
    ordered = [{**origin, "along": 0.0}] + ordered + [{**destination, "along": math.inf}]

    seen, unique = set(), []
    for stop in ordered:
        if stop["uri"] in seen:
            continue
        seen.add(stop["uri"])
        unique.append(stop)
    return unique


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corridor", choices=sorted(CORRIDORS))
    ap.add_argument("--no-network", action="store_true", help="skip link resolution and gpkg write")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    corridor = CORRIDORS[args.corridor]
    stops = collect(corridor)
    print(f"{corridor.ref}: {len(stops)} stops in direction {corridor.direction}")
    for i, s in enumerate(stops, 1):
        flag = "~" if s["shared_label"] else " "
        print(f"  {i:02d}{flag} {s['name'] or '<<UNNAMED>>':40s} {s['uri']}")
    if any(s["shared_label"] for s in stops):
        print("  (~ = name taken from a label the map prints once for both platforms)")
    if args.no_network:
        return

    coords = resolve_coordinates([s["uri"] for s in stops])
    missing = [s for s in stops if s["uri"] not in coords]
    if missing:
        print(f"warning: {len(missing)} stops could not be geocoded and are dropped")

    import geopandas as gpd
    from shapely.geometry import Point

    rows, geoms = [], []
    for i, stop in enumerate(stops, 1):
        if stop["uri"] not in coords:
            continue
        lat, lon = coords[stop["uri"]]
        name = corridor.name_overrides.get(stop["uri"], stop["name"])
        rows.append({
            "stop_id": f"STOP_{corridor.ref}_{corridor.origin_index}_{i:02d}",
            "name": name,
            "name_en": None,
            "route_ref": corridor.ref,
            "public_transport": "platform",
            "bus": "yes",
            "operator": corridor.operator,
            "direction": corridor.direction,
            "shelter": None,
            "bench": None,
            "lit": None,
            "bin": None,
            "amenity": None,
            "highway": "bus_stop",
            "access": None,
            "kerb": None,
            "source": "Bus_Map_of_Denpasar_Raya.pdf",
            "gmaps_url": stop["uri"],
        })
        geoms.append(Point(lon, lat))

    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    out = Path(args.out) if args.out else OUT_DIR / f"{corridor.ref.lower()}_{corridor.origin_index}_bus_stops.gpkg"
    gdf.to_file(out, layer="bus_stops", driver="GPKG")
    print(f"wrote {out} ({len(gdf)} features)")


if __name__ == "__main__":
    main()
