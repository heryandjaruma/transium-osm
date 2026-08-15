#!/usr/bin/env python3
#
# Convert every route/stop GeoPackage pair in routes_and_stops/ into SQL
# INSERT statements for bus_routes, bus_stops, and route_stop.
#
# Each "<base>_bus_routes.gpkg" (single LineString feature) is paired with
# a "<base>_bus_stops.gpkg" (ordered Point features) for the same
# route+direction. bus_routes.shape is the full route geometry, encoded as
# JSON text: an array of [lat, lng] pairs.
#
# To Run
#   python3 scripts/gen_transit_sql.py
#
# Output: data/sql/insert_bus_routes.sql, insert_bus_stops.sql,
# insert_route_stop.sql

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "routes_and_stops"
OUT_DIR = ROOT / "data" / "sql"

SEQUENCE_RE = re.compile(r"_(\d+)$")


def load_layer(gpkg_path, layer_name):
    result = subprocess.run(
        ["ogr2ogr", "-f", "GeoJSON", "/vsistdout/", str(gpkg_path), layer_name],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["features"]


def sql_str(value):
    if value is None:
        return "NULL"

    return "'" + str(value).replace("'", "''") + "'"


def find_pairs():
    pairs = []

    for routes_file in sorted(SRC_DIR.glob("*_bus_routes.gpkg")):
        if routes_file.stat().st_size == 0:
            continue

        base = routes_file.name[: -len("_bus_routes.gpkg")]
        stops_file = SRC_DIR / f"{base}_bus_stops.gpkg"

        if not stops_file.exists() or stops_file.stat().st_size == 0:
            print(f"warning: no matching stops file for {routes_file.name}, skipping")
            continue

        pairs.append((routes_file, stops_file))

    return pairs


def stop_sequence(stop_id, fallback):
    match = SEQUENCE_RE.search(stop_id)
    return int(match.group(1)) if match else fallback


def main():
    pairs = find_pairs()

    route_lines = []
    stop_lines = []
    route_stop_lines = []

    for routes_file, stops_file in pairs:
        route_features = load_layer(routes_file, "bus_routes")

        if len(route_features) != 1:
            raise ValueError(
                f"{routes_file.name} has {len(route_features)} route features, expected 1"
            )

        route_props = route_features[0]["properties"]
        route_id = route_props["route_id"]
        coords = route_features[0]["geometry"]["coordinates"]
        shape = json.dumps([[lat, lon] for lon, lat in coords])

        route_lines.append(
            "INSERT INTO bus_routes (id, ref, name, direction, color, shape) "
            f"VALUES ({sql_str(route_id)}, {sql_str(route_props['ref'])}, "
            f"{sql_str(route_props['name'])}, {sql_str(route_props['direction_id'])}, "
            f"{sql_str(route_props['colour'])}, {sql_str(shape)});"
        )

        stop_features = load_layer(stops_file, "bus_stops")

        for index, feature in enumerate(stop_features):
            props = feature["properties"]
            stop_id = props["stop_id"]
            lon, lat = feature["geometry"]["coordinates"]
            name = props.get("name") or props.get("name:en")

            stop_lines.append(
                "INSERT INTO bus_stops (id, name, lat, lng) "
                f"VALUES ({sql_str(stop_id)}, {sql_str(name)}, {lat}, {lon});"
            )

            sequence = stop_sequence(stop_id, index)

            route_stop_lines.append(
                "INSERT INTO route_stop (id, routeId, stopId, sequence) "
                f"VALUES ({sql_str(f'{route_id}_{sequence:02d}')}, {sql_str(route_id)}, "
                f"{sql_str(stop_id)}, {sequence});"
            )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "insert_bus_routes.sql").write_text("\n".join(route_lines) + "\n")
    (OUT_DIR / "insert_bus_stops.sql").write_text("\n".join(stop_lines) + "\n")
    (OUT_DIR / "insert_route_stop.sql").write_text("\n".join(route_stop_lines) + "\n")

    print(
        f"routes: {len(route_lines)}, stops: {len(stop_lines)}, "
        f"route_stop: {len(route_stop_lines)} -> {OUT_DIR}"
    )


if __name__ == "__main__":
    main()
