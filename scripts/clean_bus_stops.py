#!/usr/bin/env python3

# To Run
# python scripts/clean_bus_stops.py \
#   temps/k5b_17530739.gpkg \
#   --route-ref K5B \
#   --operator "Trans Sarbagita" \
#   --direction 1 \
#   --origin-name "Politeknik Negeri Bali"

import argparse
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge, unary_union


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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Clean and normalize the bus_stops layer in a GeoPackage."
    )

    parser.add_argument(
        "gpkg",
        help="Path to the GeoPackage",
    )

    parser.add_argument(
        "--route-ref",
        required=True,
        help="Route reference, e.g. K5B, TS1",
    )

    parser.add_argument(
        "--operator",
        required=True,
        help='Operator name, e.g. "Trans Sarbagita"',
    )

    parser.add_argument(
        "--direction",
        required=True,
        choices=["0", "1"],
        help="Direction ID: 0 or 1",
    )

    parser.add_argument(
        "--origin-name",
        required=True,
        help='Name of the first stop, e.g. "Politeknik Negeri Bali"',
    )

    parser.add_argument(
        "--stops-layer",
        default="bus_stops",
        help="Stops layer name (default: bus_stops)",
    )

    parser.add_argument(
        "--routes-layer",
        default="bus_routes",
        help="Routes layer name (default: bus_routes)",
    )

    parser.add_argument(
        "--route-field",
        default="ref",
        help="Field in bus_routes containing route reference (default: ref)",
    )

    return parser.parse_args()


def normalize_text(value):
    if value is None:
        return ""

    return str(value).strip().casefold()


def find_origin_stop(stops, origin_name):
    """
    Find the origin using name or name:en.
    """

    target = normalize_text(origin_name)

    matches = []

    for idx, row in stops.iterrows():
        names = []

        if "name" in stops.columns:
            names.append(row.get("name"))

        if "name:en" in stops.columns:
            names.append(row.get("name:en"))

        if any(normalize_text(name) == target for name in names):
            matches.append(idx)

    if not matches:
        raise ValueError(
            f'Could not find origin stop "{origin_name}" '
            'in either "name" or "name:en".'
        )

    if len(matches) > 1:
        print(
            f'Warning: found {len(matches)} stops named "{origin_name}". '
            "Using the first match."
        )

    return stops.loc[matches[0]]


def build_route_geometry(routes, route_ref, route_field):
    """
    Select the requested route and merge its geometry into one line.
    """

    if route_field not in routes.columns:
        raise ValueError(
            f'Route field "{route_field}" does not exist.\n'
            f"Available fields: {list(routes.columns)}"
        )

    route_mask = (
        routes[route_field]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
        == route_ref.casefold()
    )

    selected = routes[route_mask].copy()

    if selected.empty:
        raise ValueError(
            f'No route "{route_ref}" found in '
            f'bus_routes["{route_field}"].'
        )

    selected = selected[
        selected.geometry.notna()
        & ~selected.geometry.is_empty
    ]

    if selected.empty:
        raise ValueError(
            f'Route "{route_ref}" has no valid geometry.'
        )

    merged = linemerge(
        unary_union(selected.geometry.tolist())
    )

    if isinstance(merged, LineString):
        return merged

    if isinstance(merged, MultiLineString):
        raise ValueError(
            f'Route "{route_ref}" could not be merged into one continuous '
            "LineString.\n\n"
            "This usually means the route contains disconnected segments. "
            "Fix the gaps/snapping first rather than allowing the script to "
            "silently generate potentially incorrect stop order."
        )

    raise ValueError(
        f"Unexpected merged geometry type: {merged.geom_type}"
    )


def orient_route(route_line, origin_point):
    """
    Make sure distance 0 on the LineString represents the origin side.

    Shapely/GeoPackage route coordinates can be stored in either direction.
    We therefore compare the origin stop against both endpoints.
    """

    start = route_line.coords[0]
    end = route_line.coords[-1]

    start_point = type(origin_point)(start)
    end_point = type(origin_point)(end)

    distance_to_start = origin_point.distance(start_point)
    distance_to_end = origin_point.distance(end_point)

    if distance_to_start <= distance_to_end:
        return route_line

    # Reverse coordinate sequence.
    return LineString(list(route_line.coords)[::-1])


def ensure_field(df, field):
    """
    Add an empty field if it doesn't already exist.
    """

    if field not in df.columns:
        df[field] = None


def clean_stops(
    stops,
    route_line,
    route_ref,
    operator,
    direction,
    origin_name,
):
    stops = stops.copy()

    # Remove empty/null geometries before doing route projection.
    invalid = stops.geometry.isna() | stops.geometry.is_empty

    if invalid.any():
        bad_count = int(invalid.sum())

        print(
            f"Warning: removing {bad_count} stop(s) "
            "with missing/empty geometry."
        )

        stops = stops[~invalid].copy()

    # Find origin before stripping fields.
    origin = find_origin_stop(stops, origin_name)

    route_line = orient_route(
        route_line,
        origin.geometry,
    )

    #
    # Calculate where each stop sits along the route.
    #
    # project() returns distance from the beginning of the LineString.
    #
    stops["_route_distance"] = stops.geometry.apply(
        route_line.project
    )

    stops = (
        stops
        .sort_values("_route_distance")
        .reset_index(drop=True)
    )

    # Make sure every requested output column exists.
    for field in STOP_FIELDS:
        ensure_field(stops, field)

    #
    # Apply standardized values.
    #
    stops["route_ref"] = route_ref
    stops["operator"] = operator
    stops["direction"] = direction

    stops["public_transport"] = "platform"
    stops["bus"] = "yes"
    stops["highway"] = "bus_stop"

    #
    # Generate:
    #
    # STOP_K5B_1_00
    # STOP_K5B_1_01
    # ...
    #
    stops["stop_id"] = [
        f"STOP_{route_ref}_{direction}_{i:02d}"
        for i in range(len(stops))
    ]

    #
    # Strip every unwanted attribute.
    #
    # geometry has to remain even though it's not an ordinary attribute.
    #
    stops = stops[
        STOP_FIELDS + ["geometry"]
    ].copy()

    return stops


def main():
    args = parse_args()

    gpkg = Path(args.gpkg)

    if not gpkg.exists():
        raise FileNotFoundError(
            f"GeoPackage does not exist: {gpkg}"
        )

    print(f"Reading: {gpkg}")

    stops = gpd.read_file(
        gpkg,
        layer=args.stops_layer,
    )

    routes = gpd.read_file(
        gpkg,
        layer=args.routes_layer,
    )

    print(f"Stops found: {len(stops)}")
    print(f"Route: {args.route_ref}")
    print(f"Operator: {args.operator}")
    print(f"Direction: {args.direction}")
    print(f"Origin: {args.origin_name}")

    #
    # Route and stops must use the same CRS for distance/projection.
    #
    if stops.crs is None:
        raise ValueError(
            "bus_stops has no CRS assigned."
        )

    if routes.crs is None:
        raise ValueError(
            "bus_routes has no CRS assigned."
        )

    if routes.crs != stops.crs:
        print(
            f"Reprojecting bus_routes from "
            f"{routes.crs} -> {stops.crs}"
        )

        routes = routes.to_crs(stops.crs)

    route_line = build_route_geometry(
        routes,
        args.route_ref,
        args.route_field,
    )

    cleaned = clean_stops(
        stops=stops,
        route_line=route_line,
        route_ref=args.route_ref,
        operator=args.operator,
        direction=args.direction,
        origin_name=args.origin_name,
    )

    #
    # BACKUP FIRST
    #
    backup = gpkg.with_name(
        f"{gpkg.stem}.backup{gpkg.suffix}"
    )

    if not backup.exists():
        import shutil

        shutil.copy2(gpkg, backup)

        print(f"Backup created: {backup}")
    else:
        print(f"Backup already exists: {backup}")

    #
    # Replace ONLY bus_stops.
    #
    # bus_routes and every other GPKG layer remain untouched.
    #
    import pyogrio

    pyogrio.write_dataframe(
        cleaned,
        gpkg,
        layer=args.stops_layer,
        driver="GPKG",
        append=False,
    )

    print()
    print("Done.")
    print(f"Updated layer: {args.stops_layer}")
    print(f"Number of stops: {len(cleaned)}")

    print()
    print("Generated stop IDs:")

    for stop_id in cleaned["stop_id"]:
        print(f"  {stop_id}")


if __name__ == "__main__":
    main()