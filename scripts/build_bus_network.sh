#!/usr/bin/env bash
#
# Build bali_transit.gpkg by appending every bus_routes/bus_stops gpkg
# in routes_and_stops/ into two layers, following docs.md's ogr2ogr
# append convention (the same one used to build bali_basemap.gpkg).
#
# To Run
#   scripts/build_bus_network.sh
#
# Output: bali_transit.gpkg (layers: bus_routes, bus_stops), written
# to the repo root.

set -euo pipefail

cd "$(dirname "$0")/.."

SRC_DIR="routes_and_stops"
OUT="bali_transit.gpkg"

rm -f "$OUT"

# direction_id / public_transport:version come in as Integer in some source
# files and String in others (depends on whether the field was ever blank
# when it was extracted). Force both to String so every append lands on the
# same schema regardless of order.
routes_count=0
for f in "$SRC_DIR"/*_bus_routes.gpkg; do
    ogr2ogr -f GPKG -update -append -mapFieldType Integer=String "$OUT" "$f" bus_routes -nln bus_routes
    routes_count=$((routes_count + 1))
done

stops_count=0
for f in "$SRC_DIR"/*_bus_stops.gpkg; do
    ogr2ogr -f GPKG -update -append "$OUT" "$f" bus_stops -nln bus_stops
    stops_count=$((stops_count + 1))
done

echo "appended $routes_count routes gpkg + $stops_count stops gpkg -> $OUT"
ogrinfo -so "$OUT"
