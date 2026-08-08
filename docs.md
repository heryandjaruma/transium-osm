# OSM Processing Docs

## Extract Region

```shell
osmium extract -p regions/bali.geojson raw/nusa-tenggara-260806.osm.pbf -o data/bali.osm.pbf
```

## Get Coastline (Shape of Bali)

```shell
osmium tags-filter data/bali.osm.pbf w/natural=coastline -o features/bali-coastline.osm.pbf
```