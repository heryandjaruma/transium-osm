#!/usr/bin/env python3

# To Run
# python scripts/filter_vegetation.py \
#   vegetation.osm.pbf \
#   --min-area 2000 \
#   --out temps/vegetation_filtered.gpkg
#
# Then merge into the main map, following docs.md's ogr2ogr convention:
# ogr2ogr -f GPKG -update -append bali_map.gpkg temps/vegetation_filtered.gpkg vegetation -nln vegetation

import argparse

import geopandas as gpd

# Tags that read as "vegetation / green space" on a simple map. Anything not
# matching one of these (residential, industrial, cemetery, bare_rock, water,
# untagged relation artifacts, etc.) is dropped regardless of size -- it isn't
# vegetation, it's noise from the broader landuse/natural extract filter.
VEG_NATURAL = {"wood", "scrub", "grassland", "heath"}
VEG_LANDUSE = {"forest", "meadow", "orchard", "vineyard", "farmland", "grass", "allotments", "plant_nursery"}
VEG_LEISURE = {"park", "garden", "nature_reserve", "golf_course"}

# Bali sits in UTM zone 50S; used only to get an accurate area in m2.
AREA_CRS = "EPSG:32750"


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Filter vegetation.osm.pbf down to polygon vegetation big enough to "
            "matter on a simple map. Points and (multi)linestrings are dropped "
            "unconditionally by only reading the multipolygons layer."
        )
    )

    parser.add_argument(
        "pbf",
        nargs="?",
        default="vegetation.osm.pbf",
        help="Path to the source .osm.pbf (default: vegetation.osm.pbf)",
    )

    parser.add_argument(
        "--out",
        default="temps/vegetation_filtered.gpkg",
        help="Output GeoPackage path (default: temps/vegetation_filtered.gpkg)",
    )

    parser.add_argument(
        "--layer",
        default="vegetation",
        help="Output layer name (default: vegetation)",
    )

    parser.add_argument(
        "--min-area",
        type=float,
        default=2000.0,
        help="Minimum polygon area in m2 to keep (default: 2000, i.e. 0.2 ha)",
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


def is_vegetation(gdf):
    return (
        gdf["natural"].isin(VEG_NATURAL)
        | gdf["landuse"].isin(VEG_LANDUSE)
        | gdf["leisure"].isin(VEG_LEISURE)
    )


def main():
    args = parse_args()

    gdf = gpd.read_file(args.pbf, layer="multipolygons")
    total = len(gdf)

    veg_mask = is_vegetation(gdf)
    non_veg_dropped = (~veg_mask).sum()
    veg = gdf[veg_mask].copy()

    veg["area_m2"] = veg.to_crs(AREA_CRS).area
    small_mask = veg["area_m2"] < args.min_area
    small_dropped = int(small_mask.sum())
    kept = veg[~small_mask].copy()

    print(f"multipolygons read:      {total}")
    print(f"dropped, not vegetation: {non_veg_dropped}")
    print(f"dropped, area < {args.min_area:g} m2: {small_dropped}")
    print(f"kept:                    {len(kept)}")

    if args.stats_only:
        return

    if args.simplify > 0:
        kept["geometry"] = kept["geometry"].simplify(args.simplify, preserve_topology=True)

    keep_cols = [c for c in ["osm_id", "osm_way_id", "name", "natural", "landuse", "leisure", "area_m2", "geometry"] if c in kept.columns]
    kept = kept[keep_cols]

    kept.to_file(args.out, layer=args.layer, driver="GPKG")
    print(f"wrote {len(kept)} features to {args.out} (layer: {args.layer})")


if __name__ == "__main__":
    main()
