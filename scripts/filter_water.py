#!/usr/bin/env python3

# To Run
# python scripts/filter_water.py \
#   water.osm.pbf \
#   --min-area 200 \
#   --out temps/water_filtered.gpkg
#
# Then merge into the main map, following docs.md's ogr2ogr convention:
# ogr2ogr -f GPKG -update -append bali_map.gpkg temps/water_filtered.gpkg water -nln water

import argparse

import geopandas as gpd

# Tags that read as an actual water body on a simple map. Anything else
# (scrub, shingle, wetland, residential slivers, etc. that leaked into the
# broader natural/landuse extract) is dropped regardless of size.
WATER_NATURAL = {"water"}
WATER_LANDUSE = {"reservoir", "basin"}

# Bali sits in UTM zone 50S; used only to get an accurate area in m2.
AREA_CRS = "EPSG:32750"


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Filter water.osm.pbf down to water bodies big enough to matter on "
            "a simple map. Points and (multi)linestrings are dropped "
            "unconditionally by only reading the multipolygons layer."
        )
    )

    parser.add_argument(
        "pbf",
        nargs="?",
        default="water.osm.pbf",
        help="Path to the source .osm.pbf (default: water.osm.pbf)",
    )

    parser.add_argument(
        "--out",
        default="temps/water_filtered.gpkg",
        help="Output GeoPackage path (default: temps/water_filtered.gpkg)",
    )

    parser.add_argument(
        "--layer",
        default="water",
        help="Output layer name (default: water)",
    )

    parser.add_argument(
        "--min-area",
        type=float,
        default=200.0,
        help="Minimum polygon area in m2 to keep (default: 200)",
    )

    parser.add_argument(
        "--simplify",
        type=float,
        default=0.0,
        help=(
            "Optional Douglas-Peucker simplification tolerance in degrees "
            "(e.g. 0.00005) for softer, more playful shapes. 0 disables (default)."
        ),
    )

    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Print keep/drop counts for the given --min-area without writing output",
    )

    return parser.parse_args()


def is_water(gdf):
    return gdf["natural"].isin(WATER_NATURAL) | gdf["landuse"].isin(WATER_LANDUSE)


def main():
    args = parse_args()

    gdf = gpd.read_file(args.pbf, layer="multipolygons")
    total = len(gdf)

    water_mask = is_water(gdf)
    non_water_dropped = int((~water_mask).sum())
    water = gdf[water_mask].copy()

    water["area_m2"] = water.to_crs(AREA_CRS).area
    small_mask = water["area_m2"] < args.min_area
    small_dropped = int(small_mask.sum())
    kept = water[~small_mask].copy()

    print(f"multipolygons read:      {total}")
    print(f"dropped, not water:      {non_water_dropped}")
    print(f"dropped, area < {args.min_area:g} m2: {small_dropped}")
    print(f"kept:                    {len(kept)}")

    if args.stats_only:
        return

    if args.simplify > 0:
        kept["geometry"] = kept["geometry"].simplify(args.simplify, preserve_topology=True)

    keep_cols = [c for c in ["osm_id", "osm_way_id", "name", "natural", "landuse", "area_m2", "geometry"] if c in kept.columns]
    kept = kept[keep_cols]

    kept.to_file(args.out, layer=args.layer, driver="GPKG")
    print(f"wrote {len(kept)} features to {args.out} (layer: {args.layer})")


if __name__ == "__main__":
    main()
