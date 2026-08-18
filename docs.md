# Terminology

## `gpkg`

GeoPackage. In QGIS, use the same file to keep adding features like `roads`, `vegetation`.

Two gpkg files: `bali_basemap.gpkg` (roads, vegetation, kecamatan, kelurahan, water, streams, land) and
`bali_transit.gpkg` (bus_routes, bus_stops).

Think if `osm.pbf` data as source of truth. Instead of modifying that file directly,
the process should be filter then export data as you need to another `osm.pbf` or
`gpkg`.

# OSM Processing Docs

## Extract Region

```shell
osmium extract -p regions/bali.geojson raw/nusa-tenggara-260806.osm.pbf -o data/bali.osm.pbf
```

## Get Coastline (Shape of Bali)

```shell
osmium tags-filter data/bali.osm.pbf w/natural=coastline -o raw/bali-coastline.osm.pbf
```

## Get Administrative Area

```shell
osmium tags-filter data/bali.osm.pbf r/admin_level=7 -o data/kelurahan_admin7.osm.pbf # kelurahan
osmium tags-filter data/bali.osm.pbf r/admin_level=6 -o data/kecamatan_admin6.osm.pbf # kecamatan
```

Append into `bali_basemap.gpkg` as `kecamatan`/`kelurahan` layers (osm_id, name, admin_level only):

```shell
ogr2ogr -f GPKG -update -append bali_basemap.gpkg data/kecamatan_admin6.osm.pbf \
  -sql "SELECT osm_id, name, admin_level FROM multipolygons WHERE boundary='administrative' AND admin_level='6'" \
  -nln kecamatan -nlt PROMOTE_TO_MULTI

ogr2ogr -f GPKG -update -append bali_basemap.gpkg data/kelurahan_admin7.osm.pbf \
  -sql "SELECT osm_id, name, admin_level FROM multipolygons WHERE boundary='administrative' AND admin_level='7'" \
  -nln kelurahan -nlt PROMOTE_TO_MULTI
```

## Get Land

Land polygons come from closed `natural=coastline` ways in the multipolygons
layer (GDAL's OSM driver auto-closes them into polygons). Find the way by
name or id, then filter to just its geometry so the schema matches the
existing `land` layer (geometry only, no attributes):

```shell
ogr2ogr -f GPKG temps/island_full.gpkg data/bali.osm.pbf \
  -sql "SELECT * FROM multipolygons WHERE osm_way_id = '<way_id>'" \
  -nln land -nlt POLYGON

ogr2ogr -f GPKG temps/island.gpkg temps/island_full.gpkg land \
  -select "" -nln land -nlt POLYGON
```

Then append into `bali_basemap.gpkg` following the ogr2ogr convention:

```shell
ogr2ogr -f GPKG -update -append bali_basemap.gpkg temps/island.gpkg land -nln land
```

Use `-nlt POLYGON` (not `PROMOTE_TO_MULTI`) since the existing `land` layer
is typed `Polygon` -- appending a `MultiPolygon` still works but throws a
GeoPackage-spec warning.

## Get Roads

```shell
osmium tags-filter data/bali.osm.pbf w/highway -o raw/bali-roads.osm.pbf
```

## DuckDB

Get all bus routes.

```sql
SELECT
    kind,
    id,
    tags['name'] AS name,
    tags['ref'] AS ref,
    tags['route'] AS route
FROM 'data/bali.osm.pbf'
WHERE tags['route'] = 'bus'
LIMIT 100;
```

```
┌──────────┬──────────┬─────────────────────────────────────────────────────────────┬─────────┬─────────┐
│   kind   │    id    │                            name                             │   ref   │  route  │
│ varchar  │  int64   │                           varchar                           │ varchar │ varchar │
├──────────┼──────────┼─────────────────────────────────────────────────────────────┼─────────┼─────────┤
│ relation │  3276905 │ Shuttle Bus Dreamland Beach                                 │ NULL    │ bus     │
│ relation │  8216954 │ Minibus Route Orange                                        │ NULL    │ bus     │
│ relation │  9233872 │ MFUS Line 1 Bus Route                                       │ 1       │ bus     │
│ relation │ 15412461 │ Koridor 1 Terminal Pesiapan (Tabanan) → Sentral Parkir Kuta │ K1B     │ bus     │
│ relation │ 15412634 │ Koridor 1 Sentral Parkir Kuta → Terminal Pesiapan (Tabanan) │ K1B     │ bus     │
│ relation │ 15417092 │ Koridor 2 Bandara Ngurah Rai → Terminal Ubung               │ K2B     │ bus     │
│ relation │ 15417285 │ Koridor 2 Terminal Ubung → Bandara Ngurah Rai               │ K2B     │ bus     │
│ relation │ 15421166 │ Koridor 3 Matahari Terbit → Terminal Ubung                  │ K3B     │ bus     │
│ relation │ 15421213 │ Koridor 3 Terminal Ubung → Matahari Terbit                  │ K3B     │ bus     │
│ relation │ 15421343 │ Koridor 4 GOR Ngurah Rai → Monkey Forest                    │ K4B     │ bus     │
│ relation │ 15421491 │ Koridor 5 Sentral Parkir Kuta → Politeknik Negeri Bali      │ K5B     │ bus     │
│ relation │ 15425938 │ Koridor 1 Gor Ngurah Rai → Garuda Wisnu Kencana (GWK)       │ TS1     │ bus     │
│ relation │ 17530739 │ Koridor 5 Politeknik Negeri Bali → Sentral Parkir Kuta      │ K5B     │ bus     │
│ relation │ 17538157 │ Koridor 4 Monkey Forest → GOR Ngurah Rai                    │ K4B     │ bus     │
└──────────┴──────────┴─────────────────────────────────────────────────────────────┴─────────┴─────────┘
  14 rows                                                                                     5 columns
```

### Extract PBF for A Specific Id

```shell
osmium getid data/bali.osm.pbf r15412461 -r -o features/K1B_15412461.osm.pbf
```

### Bus Stops Filter

```sql
"highway" = 'bus_stop'
OR "public_transport" = 'platform'
OR "public_transport" = 'stop_position'
```

### Use ogr2ogr

Append route from temps to the main gpkg.

```shell
ogr2ogr \
  -f GPKG \
  -update \
  -append \
  bali_transit.gpkg \
  temps/k5b_17530739.gpkg \
  bus_routes \
  -nln bus_routes
```

Append stops from temps to the main gpkg.

```shell
ogr2ogr \
  -f GPKG \
  -update \
  -append \
  bali_transit.gpkg \
  temps/k5b_17530739.gpkg \
  bus_stops \
  -nln bus_stops
```


### Filter For Specific Route

```sql
"route_ref" LIKE '%K1B%'
```

# PMTiles

PMTiles is the expected output that can be consumed by MapLibre to render.

There are 2 main steps:
1. From gpkg to basemap individual feature geojson
2. From geojson to one pmtiles with geojson layers


## `gpkg` to individual `geojson`

### `bali_basemap.gpkg`

```shell
ogr2ogr -f GeoJSON build/basemap/land.geojson \
  bali_basemap.gpkg land -t_srs EPSG:4326

ogr2ogr -f GeoJSON build/basemap/kecamatan.geojson \
  bali_basemap.gpkg kecamatan -t_srs EPSG:4326

ogr2ogr -f GeoJSON build/basemap/kelurahan.geojson \
  bali_basemap.gpkg kelurahan -t_srs EPSG:4326

ogr2ogr -f GeoJSON build/basemap/water.geojson \
  bali_basemap.gpkg water -t_srs EPSG:4326

ogr2ogr -f GeoJSON build/basemap/streams.geojson \
  bali_basemap.gpkg streams -t_srs EPSG:4326

ogr2ogr -f GeoJSON build/basemap/vegetation.geojson \
  bali_basemap.gpkg vegetation -t_srs EPSG:4326

ogr2ogr -f GeoJSON build/basemap/roads.geojson \
  bali_basemap.gpkg roads -t_srs EPSG:4326
```

### `bali_transit.gpkg`

```shell
ogr2ogr -f GeoJSON build/transit/bus_routes.geojson \
  bali_transit.gpkg bus_routes -t_srs EPSG:4326

ogr2ogr -f GeoJSON build/transit/bus_stops.geojson \
  bali_transit.gpkg bus_stops -t_srs EPSG:4326
```


## `geojson` to one `pmtiles`

### `bali_basemap.gpkg`

```shell
tippecanoe \
  -o build/tiles/bali-basemap.pmtiles \
  -Z7 \
  -z16 \
  --force \
  --drop-densest-as-needed \
  --extend-zooms-if-still-dropping \
  -L land:build/basemap/land.geojson \
  -L kecamatan:build/basemap/kecamatan.geojson \
  -L kelurahan:build/basemap/kelurahan.geojson \
  -L water:build/basemap/water.geojson \
  -L streams:build/basemap/streams.geojson \
  -L vegetation:build/basemap/vegetation.geojson \
  -L roads:build/basemap/roads.geojson
```

### `bali_transit.gpkg`

```shell
tippecanoe \
  -o build/tiles/bali-transit.pmtiles \
  -Z8 \
  -z17 \
  --force \
  -r1 \
  -L bus_routes:build/transit/bus_routes.geojson \
  -L bus_stops:build/transit/bus_stops.geojson
```