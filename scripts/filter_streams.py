#!/usr/bin/env python3

# To Run
# python scripts/filter_streams.py \
#   streams.osm.pbf \
#   --min-length 50 \
#   --out temps/streams_filtered.gpkg
#
# Then merge into the main map, following docs.md's ogr2ogr convention:
# ogr2ogr -f GPKG -update -append bali_map.gpkg temps/streams_filtered.gpkg streams -nln streams

import argparse

import geopandas as gpd

# waterway=drain is dense, minor drainage ditching -- excluded by default since
# it clutters a simple map without adding much geographic character. Pass
# --types stream,river,canal,drain to include it back in.
DEFAULT_TYPES = ["stream", "river", "canal"]

# Bali sits in UTM zone 50S; used only to get an accurate length in m.
LENGTH_CRS = "EPSG:32750"


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Filter streams.osm.pbf down to waterways significant enough to "
            "matter on a simple map. Points (waterfalls, weirs, dams) and "
            "relation artifacts are dropped unconditionally by only reading "
            "the lines layer."
        )
    )

    parser.add_argument(
        "pbf",
        nargs="?",
        default="streams.osm.pbf",
        help="Path to the source .osm.pbf (default: streams.osm.pbf)",
    )

    parser.add_argument(
        "--out",
        default="temps/streams_filtered.gpkg",
        help="Output GeoPackage path (default: temps/streams_filtered.gpkg)",
    )

    parser.add_argument(
        "--layer",
        default="streams",
        help="Output layer name (default: streams)",
    )

    parser.add_argument(
        "--types",
        default=",".join(DEFAULT_TYPES),
        help=f"Comma-separated waterway types to keep (default: {','.join(DEFAULT_TYPES)})",
    )

    parser.add_argument(
        "--min-length",
        type=float,
        default=50.0,
        help="Minimum line length in meters to keep (default: 50)",
    )

    parser.add_argument(
        "--simplify",
        type=float,
        default=0.0,
        help=(
            "Optional Douglas-Peucker simplification tolerance in degrees "
            "(e.g. 0.00005) for softer, more playful curves. 0 disables (default)."
        ),
    )

    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Print keep/drop counts for the given filters without writing output",
    )

    return parser.parse_args()


def main():
    args = parse_args()
    types = {t.strip() for t in args.types.split(",") if t.strip()}

    gdf = gpd.read_file(args.pbf, layer="lines")
    total = len(gdf)

    type_mask = gdf["waterway"].isin(types)
    off_type_dropped = int((~type_mask).sum())
    streams = gdf[type_mask].copy()

    streams["len_m"] = streams.to_crs(LENGTH_CRS).length
    short_mask = streams["len_m"] < args.min_length
    short_dropped = int(short_mask.sum())
    kept = streams[~short_mask].copy()

    print(f"lines read:               {total}")
    print(f"dropped, waterway not in {sorted(types)}: {off_type_dropped}")
    print(f"dropped, length < {args.min_length:g} m: {short_dropped}")
    print(f"kept:                     {len(kept)}")

    if args.stats_only:
        return

    if args.simplify > 0:
        kept["geometry"] = kept["geometry"].simplify(args.simplify, preserve_topology=True)

    keep_cols = [c for c in ["osm_id", "name", "waterway", "len_m", "geometry"] if c in kept.columns]
    kept = kept[keep_cols]

    kept.to_file(args.out, layer=args.layer, driver="GPKG")
    print(f"wrote {len(kept)} features to {args.out} (layer: {args.layer})")


if __name__ == "__main__":
    main()
