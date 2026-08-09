# Terminology

## `gpkg`

GeoPackage. In QGIS, use the same file to keep adding features like `land`. `roads`.

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
osmium tags-filter data/bali.osm.pbf w/natural=coastline -o features/bali-coastline.osm.pbf
```

## Get Roads

```shell
osmium tags-filter data/bali.osm.pbf w/highway -o data/bali-roads.osm.pbf
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
  bali_map.gpkg \
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
  bali_map.gpkg \
  temps/k5b_17530739.gpkg \
  bus_stops \
  -nln bus_stops
```


### Filter For Specific Route

```sql
"route_ref" LIKE '%K1B%'
```