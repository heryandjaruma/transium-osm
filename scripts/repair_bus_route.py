#!/usr/bin/env python3

# python3 scripts/repair_bus_route.py \
#   temps/k5b_17530739.gpkg \
#   --route-ref K5B \
#   --max-gap 5 \
#   --dry-run

import argparse
import math
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, unary_union


DEFAULT_WORKING_CRS = "EPSG:32750"  # WGS 84 / UTM zone 50S, appropriate for Bali


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose and repair small gaps between bus route LineString "
            "segments inside a GeoPackage."
        )
    )

    parser.add_argument(
        "gpkg",
        help="Path to input GeoPackage",
    )

    parser.add_argument(
        "--route-ref",
        required=True,
        help="Route ref to repair, e.g. K5B",
    )

    parser.add_argument(
        "--routes-layer",
        default="bus_routes",
        help="Input routes layer (default: bus_routes)",
    )

    parser.add_argument(
        "--route-field",
        default="ref",
        help="Field containing route reference (default: ref)",
    )

    parser.add_argument(
        "--output-layer",
        default="bus_routes_repaired",
        help="Output layer (default: bus_routes_repaired)",
    )

    parser.add_argument(
        "--working-crs",
        default=DEFAULT_WORKING_CRS,
        help=(
            "Metric CRS used for gap measurement "
            f"(default: {DEFAULT_WORKING_CRS})"
        ),
    )

    parser.add_argument(
        "--max-gap",
        type=float,
        default=5.0,
        help="Maximum endpoint gap to auto-repair, in metres (default: 5)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only diagnose gaps; do not write repaired route",
    )

    return parser.parse_args()


def normalize(value):
    if value is None:
        return ""

    return str(value).strip().casefold()


def extract_lines(geometry):
    """
    Convert arbitrary line-based geometry into individual LineStrings.
    """
    if geometry is None or geometry.is_empty:
        return []

    if isinstance(geometry, LineString):
        return [geometry]

    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)

    # GeometryCollection can happen after some GIS operations.
    if hasattr(geometry, "geoms"):
        lines = []

        for part in geometry.geoms:
            lines.extend(extract_lines(part))

        return lines

    return []


def get_segments(routes):
    segments = []

    for _, row in routes.iterrows():
        segments.extend(extract_lines(row.geometry))

    return segments


def endpoint(line, side):
    if side == "start":
        return Point(line.coords[0])

    return Point(line.coords[-1])


def endpoint_pairs(line_a, line_b):
    """
    Return all four possible endpoint distances between two LineStrings.
    """
    combinations = []

    for side_a in ("start", "end"):
        for side_b in ("start", "end"):
            point_a = endpoint(line_a, side_a)
            point_b = endpoint(line_b, side_b)

            combinations.append(
                {
                    "side_a": side_a,
                    "side_b": side_b,
                    "point_a": point_a,
                    "point_b": point_b,
                    "distance": point_a.distance(point_b),
                }
            )

    return combinations


def nearest_endpoint_pair(line_a, line_b):
    return min(
        endpoint_pairs(line_a, line_b),
        key=lambda item: item["distance"],
    )


def replace_endpoint(line, side, point):
    """
    Replace one endpoint of a LineString.
    """
    coords = list(line.coords)
    replacement = (point.x, point.y)

    if side == "start":
        coords[0] = replacement
    else:
        coords[-1] = replacement

    return LineString(coords)


def midpoint(point_a, point_b):
    return Point(
        (point_a.x + point_b.x) / 2,
        (point_a.y + point_b.y) / 2,
    )


def count_components(lines):
    """
    Merge lines and determine how many disconnected line components remain.
    """
    if not lines:
        return 0, None

    geometry = linemerge(unary_union(lines))

    if isinstance(geometry, LineString):
        return 1, geometry

    if isinstance(geometry, MultiLineString):
        return len(geometry.geoms), geometry

    return None, geometry


def get_components(lines):
    """
    Return connected route components as individual LineStrings.
    """
    if not lines:
        return []

    merged = linemerge(unary_union(lines))

    if isinstance(merged, LineString):
        return [merged]

    if isinstance(merged, MultiLineString):
        return list(merged.geoms)

    return extract_lines(merged)


def find_nearest_components(components):
    """
    Find nearest pair of endpoints between disconnected components.
    """
    best = None

    for i in range(len(components)):
        for j in range(i + 1, len(components)):
            pair = nearest_endpoint_pair(
                components[i],
                components[j],
            )

            candidate = {
                "component_a": i,
                "component_b": j,
                **pair,
            }

            if best is None or candidate["distance"] < best["distance"]:
                best = candidate

    return best


def print_gap(gap, number):
    print()
    print(f"Gap {number}:")
    print(
        f"  component {gap['component_a']} "
        f"({gap['side_a']})"
    )
    print(
        f"  component {gap['component_b']} "
        f"({gap['side_b']})"
    )
    print(f"  distance: {gap['distance']:.3f} m")
    print(
        "  endpoint A: "
        f"{gap['point_a'].x:.3f}, "
        f"{gap['point_a'].y:.3f}"
    )
    print(
        "  endpoint B: "
        f"{gap['point_b'].x:.3f}, "
        f"{gap['point_b'].y:.3f}"
    )


def snap_components(components, gap):
    """
    Snap the nearest endpoints of two route components to their midpoint.
    """

    i = gap["component_a"]
    j = gap["component_b"]

    snap_point = midpoint(
        gap["point_a"],
        gap["point_b"],
    )

    line_a = replace_endpoint(
        components[i],
        gap["side_a"],
        snap_point,
    )

    line_b = replace_endpoint(
        components[j],
        gap["side_b"],
        snap_point,
    )

    updated = components.copy()
    updated[i] = line_a
    updated[j] = line_b

    # Merge again because snapping should make these two components connected.
    return get_components(updated)


def repair_components(components, max_gap):
    """
    Iteratively connect nearest disconnected components,
    but only if the gap is <= max_gap.
    """

    repairs = []
    gap_number = 1

    while len(components) > 1:
        gap = find_nearest_components(components)

        if gap is None:
            break

        print_gap(gap, gap_number)

        if gap["distance"] > max_gap:
            print()
            print(
                f"STOP: nearest remaining gap is "
                f"{gap['distance']:.3f} m, "
                f"which exceeds --max-gap {max_gap:.3f} m."
            )

            print(
                "This is probably not just a tiny digitizing/snapping error."
            )

            return components, repairs, False

        print(
            f"  -> snapping automatically "
            f"(<= {max_gap:.3f} m)"
        )

        repairs.append(
            {
                "gap": gap_number,
                "distance_m": gap["distance"],
                "component_a": gap["component_a"],
                "component_b": gap["component_b"],
            }
        )

        components = snap_components(
            components,
            gap,
        )

        gap_number += 1

    return components, repairs, True


def preserve_route_attributes(selected):
    """
    Build one representative attribute row for the repaired route.

    Since all selected features belong to the same route, use the first row's
    attributes and replace its geometry later.
    """
    row = selected.iloc[0].copy()

    return row


def main():
    args = parse_args()

    gpkg = Path(args.gpkg)

    if not gpkg.exists():
        raise FileNotFoundError(
            f"GeoPackage does not exist: {gpkg}"
        )

    print(f"Reading: {gpkg}")
    print(f"Layer: {args.routes_layer}")
    print(f"Route: {args.route_ref}")
    print(f"Maximum auto-repair gap: {args.max_gap:.3f} m")
    print(f"Working CRS: {args.working_crs}")

    routes = gpd.read_file(
        gpkg,
        layer=args.routes_layer,
    )

    if routes.crs is None:
        raise ValueError(
            f'Layer "{args.routes_layer}" does not have a CRS.'
        )

    if args.route_field not in routes.columns:
        raise ValueError(
            f'Field "{args.route_field}" does not exist.\n'
            f"Available fields: {list(routes.columns)}"
        )

    route_mask = (
        routes[args.route_field]
        .fillna("")
        .astype(str)
        .map(normalize)
        == normalize(args.route_ref)
    )

    selected = routes[route_mask].copy()

    if selected.empty:
        raise ValueError(
            f'Could not find route "{args.route_ref}" '
            f'in field "{args.route_field}".'
        )

    selected = selected[
        selected.geometry.notna()
        & ~selected.geometry.is_empty
    ].copy()

    if selected.empty:
        raise ValueError(
            f'Route "{args.route_ref}" has no valid geometry.'
        )

    source_crs = selected.crs

    working = selected.to_crs(
        args.working_crs
    )

    segments = get_segments(working)

    if not segments:
        raise ValueError(
            "No LineString geometry found in selected route."
        )

    print()
    print(f"Selected route features: {len(selected)}")
    print(f"Individual line segments: {len(segments)}")

    components = get_components(segments)

    print(f"Connected components before repair: {len(components)}")

    if len(components) == 1:
        print()
        print("Route is already continuous.")
        print("No repair is required.")

        repaired_components = components
        repairs = []
        success = True

    else:
        repaired_components, repairs, success = repair_components(
            components,
            args.max_gap,
        )

    print()
    print("Repair summary")
    print("--------------")
    print(f"Automatic snaps: {len(repairs)}")
    print(
        f"Connected components remaining: "
        f"{len(repaired_components)}"
    )

    if repairs:
        for repair in repairs:
            print(
                f"  gap {repair['gap']}: "
                f"{repair['distance_m']:.3f} m"
            )

    if args.dry_run:
        print()
        print("Dry run only. Nothing was written.")
        return

    if not success or len(repaired_components) != 1:
        print()
        print(
            "No repaired layer was written because the route "
            "still contains disconnected components."
        )

        print(
            "Fix the large gap(s) manually in QGIS, then run this script again."
        )

        raise SystemExit(2)

    repaired_geometry = repaired_components[0]

    if not isinstance(repaired_geometry, LineString):
        raise ValueError(
            "Final geometry is unexpectedly not a LineString."
        )

    #
    # Preserve attributes from the first route feature.
    #
    representative = preserve_route_attributes(
        selected
    )

    attrs = representative.drop(
        labels=["geometry"]
    ).to_dict()

    repaired = gpd.GeoDataFrame(
        [attrs],
        geometry=[repaired_geometry],
        crs=args.working_crs,
    )

    #
    # Restore original layer CRS.
    #
    repaired = repaired.to_crs(source_crs)

    print()
    print(
        f'Writing repaired route to layer "{args.output_layer}"...'
    )

    repaired.to_file(
        gpkg,
        layer=args.output_layer,
        driver="GPKG",
    )

    print()
    print("Done.")
    print(f"Input layer:  {args.routes_layer}")
    print(f"Output layer: {args.output_layer}")
    print(f"Route:        {args.route_ref}")
    print("Geometry:     LineString")


if __name__ == "__main__":
    main()