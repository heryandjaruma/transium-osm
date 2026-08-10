#!/usr/bin/env python3

# To Run
# python scripts/extract_stops_from_pdf.py \
#   "Transit Map.pdf" \
#   --route-ref K1B \
#   --operator "Trans Metro Dewata" \
#   --direction-map "Sentral Parkir Kuta=1,Terminal Pesiapan=0" \
#   --alias "Pintu Puspem Bandung=Pintu Puspem Badung" \
#   --alias "RSU Manuba=RSU Manuaba" \
#   --out routes_and_stops/k1b_bus_stops.gpkg
#
# Extracts bus stop coordinates for one route out of the Trans Bali Transit
# Map PDF. Every stop on the map is drawn as a small circular marker with a
# hyperlink annotation pointing at its Google Maps location, next to a text
# label with the stop's name. This script locates each stop's text label,
# finds the nearest map-marker hyperlink, resolves the shortened Google Maps
# URL to recover the precise lat/lng, and writes a `bus_stops` GeoPackage
# layer using the schema in routes_stops_generation.md.

import argparse
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
import pymupdf
from shapely.geometry import Point

STOP_FIELDS = [
    "stop_id",
    "name",
    "name:en",
    "route_ref",
    "public_transport",
    "bus",
    "operator",
    "direction",
    "shelter",
    "bench",
    "lit",
    "bin",
    "amenity",
    "highway",
    "access",
    "kerb",
]

MAPS_URI_RE = re.compile(r"maps\.app\.goo\.gl|google\.com/maps")
LATLNG_PRECISE_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
LATLNG_AT_RE = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")
PLACE_NAME_RE = re.compile(r"/maps/place/([^/@]+)/")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract a route's bus stop coordinates from the transit map PDF."
    )
    parser.add_argument("pdf", help="Path to the transit map PDF")
    parser.add_argument("--page", type=int, default=0, help="0-indexed PDF page to read (default: 0)")
    parser.add_argument(
        "--stops-md",
        default="routes_and_stops/routes_and_stops.md",
        help="Path to routes_and_stops.md",
    )
    parser.add_argument("--route-ref", required=True, help="Route reference, e.g. K1B")
    parser.add_argument("--operator", required=True, help='Operator name, e.g. "Trans Metro Dewata"')
    parser.add_argument(
        "--direction-map",
        required=True,
        help='Maps each "## <start stop>" section to a direction id, '
        'e.g. "Sentral Parkir Kuta=1,Terminal Pesiapan=0"',
    )
    parser.add_argument(
        "--alias",
        action="append",
        default=[],
        help='Resolve a stop-list name to a differently-spelled PDF label, '
        'e.g. "RSU Manuba=RSU Manuaba". Repeatable.',
    )
    parser.add_argument(
        "--link-override",
        action="append",
        default=[],
        help='Force a stop name to use a specific marker URL, bypassing label '
        'matching entirely. Use when two labels sit close enough on the map '
        'that automatic matching picks the wrong marker, '
        'e.g. "Simpang Gerokgak=https://maps.app.goo.gl/HSE3UpjJmVy9Semb9". Repeatable.',
    )
    parser.add_argument("--out", required=True, help="Output GeoPackage path")
    parser.add_argument("--stops-layer", default="bus_stops", help="Output layer name")
    parser.add_argument(
        "--link-distance-threshold",
        type=float,
        default=40.0,
        help="Max PDF-unit distance from a label to a candidate marker link",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# routes_and_stops.md parsing
# ---------------------------------------------------------------------------

def parse_route_sections(md_path, route_ref):
    """
    Returns {section_start_name: [stop_name, ...]} for the route's heading
    "# <ROUTE_REF> (...)".
    """
    text = Path(md_path).read_text()
    lines = text.splitlines()

    route_heading_re = re.compile(rf"^#\s+{re.escape(route_ref)}\b")
    any_heading_re = re.compile(r"^#\s+\S")
    section_heading_re = re.compile(r"^##\s+(.+)$")
    item_re = re.compile(r"^\d+\.\s*(.+)$")

    sections = {}
    in_route = False
    current_section = None

    for line in lines:
        if route_heading_re.match(line):
            in_route = True
            continue

        if any_heading_re.match(line) and not route_heading_re.match(line):
            in_route = False
            continue

        if not in_route:
            continue

        m = section_heading_re.match(line)
        if m:
            current_section = m.group(1).strip()
            sections[current_section] = []
            continue

        m = item_re.match(line)
        if m and current_section is not None:
            sections[current_section].append(m.group(1).strip())

    if not sections:
        raise ValueError(f'No "# {route_ref}" section found in {md_path}')

    return sections


# ---------------------------------------------------------------------------
# PDF label matching
# ---------------------------------------------------------------------------

def load_words(page):
    raw_words = page.get_text("words")
    return [
        {"x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3], "text": w[4], "idx": i}
        for i, w in enumerate(raw_words)
    ]


def norm_token(t):
    return re.sub(r"[^a-z0-9]", "", t.lower())


def tokens_of(name):
    parts = re.split(r"[\s/]+", name)
    return [p for p in parts if p]


def find_label_candidates(words, name):
    """
    Find every spatial run of words that spells out `name`, allowing the
    label to wrap onto a second line directly below (as the map does for
    long names).
    """
    toks = tokens_of(name)
    results = []

    def backtrack(i, cur, used, chain):
        if i == len(toks):
            results.append(list(chain))
            return
        target_norm = norm_token(toks[i])
        # A wrapped second (or third) line is usually centered/aligned under
        # the whole label seen so far, not just the immediately preceding
        # word, so use the chain's combined x-range for the "below" check.
        chain_x0 = min(cw["x0"] for cw in chain) if chain else None
        chain_x1 = max(cw["x1"] for cw in chain) if chain else None
        for w in words:
            if w["idx"] in used or norm_token(w["text"]) != target_norm:
                continue
            if cur is None:
                backtrack(i + 1, w, used | {w["idx"]}, chain + [w])
                continue
            same_line = (
                abs(w["y0"] - cur["y0"]) < 2.5
                and 0 <= w["x0"] - cur["x1"] < 10
            )
            below = 2.5 <= (w["y0"] - cur["y0"]) <= 9 and (
                abs(w["x0"] - chain_x0) < 15
                or not (w["x1"] < chain_x0 - 5 or w["x0"] > chain_x1 + 5)
            )
            if same_line or below:
                backtrack(i + 1, w, used | {w["idx"]}, chain + [w])

    backtrack(0, None, set(), [])
    return results


def bbox_of(chain):
    return pymupdf.Rect(
        min(w["x0"] for w in chain),
        min(w["y0"] for w in chain),
        max(w["x1"] for w in chain),
        max(w["y1"] for w in chain),
    )


def rect_distance(a, b):
    dx = max(a.x0 - b.x1, b.x0 - a.x1, 0)
    dy = max(a.y0 - b.y1, b.y0 - a.y1, 0)
    return (dx**2 + dy**2) ** 0.5


def nearest_links(bbox, links, limit=3):
    scored = sorted(
        ((rect_distance(bbox, l["from"]), l) for l in links),
        key=lambda t: t[0],
    )
    return scored[:limit]


# ---------------------------------------------------------------------------
# Google Maps short-link resolution
# ---------------------------------------------------------------------------

_resolve_cache = {}


def resolve_maps_url(short_url):
    if short_url in _resolve_cache:
        return _resolve_cache[short_url]

    req = urllib.request.Request(
        short_url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"}
    )
    resp = urllib.request.urlopen(req, timeout=15)
    final_url = resp.geturl()

    m = LATLNG_PRECISE_RE.search(final_url)
    if not m:
        m = LATLNG_AT_RE.search(final_url)
    lat, lng = (float(m.group(1)), float(m.group(2))) if m else (None, None)

    place_match = PLACE_NAME_RE.search(final_url)
    place_name = urllib.parse.unquote(place_match.group(1)).replace("+", " ") if place_match else None

    result = {"final_url": final_url, "lat": lat, "lng": lng, "place_name": place_name}
    _resolve_cache[short_url] = result

    time.sleep(0.15)

    return result


def name_similarity(a, b):
    """
    Cheap token-overlap similarity, case/diacritic-insensitive.

    Numbered-series stops (Gatsu Barat 1..12, Tuban 1..7, Teuku Umar 1..8, ...)
    are common on this map, and a *different* number sharing every other word
    ("Gatsu Barat 1" vs "Gatsu Barat 9") is strong evidence of the WRONG stop,
    not a fuzzy match, so it's penalized rather than scored on word overlap
    alone.
    """
    na = set(norm_token(t) for t in tokens_of(a) if norm_token(t))
    nb = set(norm_token(t) for t in tokens_of(b) if norm_token(t))
    if not na or not nb:
        return 0.0
    sim = len(na & nb) / len(na | nb)

    a_nums = {t for t in na if t.isdigit()}
    b_nums = {t for t in nb if t.isdigit()}
    if a_nums and b_nums and a_nums.isdisjoint(b_nums):
        sim *= 0.3

    return sim


# ---------------------------------------------------------------------------
# Main resolution per stop name
# ---------------------------------------------------------------------------

def resolve_stop(name, words, links, alias_map, threshold, warnings, link_override_map=None, claimed=None):
    if claimed is None:
        claimed = {}

    if link_override_map and name in link_override_map:
        uri = link_override_map[name]
        resolved = resolve_maps_url(uri)
        if resolved["lat"] is None:
            warnings.append(f'--link-override for "{name}" did not resolve to coordinates: {uri}')
            return None
        claimed[uri] = name
        return {
            "lat": resolved["lat"],
            "lng": resolved["lng"],
            "place_name": resolved["place_name"],
            "dist": 0.0,
            "sim": 1.0,
            "uri": uri,
        }

    lookup_name = alias_map.get(name, name)

    candidates = find_label_candidates(words, lookup_name)
    if not candidates:
        warnings.append(f'No PDF label found for "{name}" (looked up as "{lookup_name}")')
        return None

    scored = []
    for chain in candidates:
        bbox = bbox_of(chain)
        for dist, link in nearest_links(bbox, links, limit=4):
            if dist > threshold:
                continue
            try:
                resolved = resolve_maps_url(link["uri"])
            except Exception as exc:
                warnings.append(f'Failed to resolve link for "{name}": {exc}')
                continue
            if resolved["lat"] is None:
                continue
            sim = name_similarity(lookup_name, resolved["place_name"] or "")
            scored.append((sim, -dist, dist, resolved, link["uri"]))

    if not scored:
        warnings.append(f'No resolvable marker link found near "{name}"')
        return None

    by_sim = sorted(scored, key=lambda t: (t[0], t[1]), reverse=True)
    by_dist = sorted(scored, key=lambda t: (t[2], -t[0]))

    # A confident name match (unpenalized token overlap, or a numbered-series
    # match with the SAME number) is trusted over raw proximity. Otherwise -
    # e.g. a same-family name with a different number, or no name at all -
    # the label's own physically-nearest marker wins; sim-based ranking
    # alone would let an unrelated same-family listing several points away
    # outscore the correct marker sitting right on top of the label.
    selection_order = by_sim if by_sim[0][0] >= 0.5 else by_dist

    if name in alias_map:
        # An explicit alias means this name IS the other spelling of a
        # known stop, so intentionally reusing that stop's marker is
        # correct, not a collision.
        best = selection_order[0]
    else:
        # Two nearby-but-distinct stops can otherwise both gravitate to the
        # same marker (e.g. two adjacent numbered stops sharing one named
        # Google listing between them). Prefer a marker no other stop has
        # claimed yet; only reuse one if nothing else is available.
        best = next((c for c in selection_order if c[4] not in claimed), None)
        if best is None:
            best = selection_order[0]
            warnings.append(
                f'"{name}" could only match a marker already used by '
                f'"{claimed.get(best[4])}" (uri={best[4]}); coordinates may overlap.'
            )

    sim, _, dist, resolved, uri = best
    claimed[uri] = name

    if sim < 0.5:
        warnings.append(
            f'Low name-match confidence for "{name}": nearest resolved place is '
            f'"{resolved["place_name"]}" (uri={uri}, dist={dist:.1f}, sim={sim:.2f})'
        )

    return {
        "lat": resolved["lat"],
        "lng": resolved["lng"],
        "place_name": resolved["place_name"],
        "dist": dist,
        "sim": sim,
        "uri": uri,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    direction_map = {}
    for pair in args.direction_map.split(","):
        section, direction = pair.split("=")
        direction_map[section.strip()] = direction.strip()

    alias_map = {}
    for pair in args.alias:
        src, dst = pair.split("=", 1)
        alias_map[src.strip()] = dst.strip()

    link_override_map = {}
    for pair in args.link_override:
        src, dst = pair.split("=", 1)
        link_override_map[src.strip()] = dst.strip()

    sections = parse_route_sections(args.stops_md, args.route_ref)

    missing_sections = set(direction_map) - set(sections)
    if missing_sections:
        raise ValueError(
            f"--direction-map references sections not found in {args.stops_md}: {missing_sections}\n"
            f"Available sections: {list(sections)}"
        )

    doc = pymupdf.open(args.pdf)
    page = doc[args.page]
    words = load_words(page)
    links = [l for l in page.get_links() if "uri" in l and MAPS_URI_RE.search(l["uri"])]

    print(f"PDF page {args.page}: {len(words)} words, {len(links)} map marker links")

    resolved_cache = {}
    warnings = []
    rows = []
    claimed = {}

    for section_name, direction in direction_map.items():
        stop_names = sections[section_name]
        print(f"\nDirection {direction} ({section_name}): {len(stop_names)} stops")

        for i, name in enumerate(stop_names):
            if name not in resolved_cache:
                resolved_cache[name] = resolve_stop(
                    name, words, links, alias_map, args.link_distance_threshold, warnings,
                    link_override_map=link_override_map, claimed=claimed,
                )
            r = resolved_cache[name]

            status = "OK" if r else "MISSING"
            print(f"  [{direction}][{i:02d}] {name:45s} {status}"
                  + (f"  ~{r['place_name']}  d={r['dist']:.1f} sim={r['sim']:.2f}" if r else ""))

            if r is None:
                continue

            row = {f: None for f in STOP_FIELDS}
            row.update(
                {
                    "stop_id": f"STOP_{args.route_ref}_{direction}_{i:02d}",
                    "name": name,
                    "name:en": name,
                    "route_ref": args.route_ref,
                    "public_transport": "platform",
                    "bus": "yes",
                    "operator": args.operator,
                    "direction": direction,
                    "highway": "bus_stop",
                }
            )
            row["geometry"] = Point(r["lng"], r["lat"])
            rows.append(row)

    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print(f"  - {w}")

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, layer=args.stops_layer, driver="GPKG")

    print(f"\nWrote {len(gdf)} stops to {out_path} (layer={args.stops_layer})")


if __name__ == "__main__":
    main()
