#!/usr/bin/env python3
#
# Convert raw/kelurahan_admin7.osm.pbf into SQL INSERT statements for the
# Kelurahan table, resolving each kelurahan's parent kecamatan from its
# `wikipedia` tag rather than a spatial join.
#
# OSM's Indonesian-village wikipedia tag follows the id.wikipedia.org
# article naming convention "<kelurahan>, <kecamatan>, <kabupaten>"
# (e.g. "id:Legian, Kuta, Badung"), so the middle segment already is the
# kecamatan name. Every one of the 716 kelurahan features in the source
# extract carries this tag, and its middle segment matches a name in
# raw/kecamatan_admin6.osm.pbf exactly -- verified against all 57
# kecamatan before relying on it here.
#
# To Run
#   python3 scripts/gen_kelurahan_sql.py
#
# Output: data/sql/insert_kelurahan.sql

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "raw"
OUT_DIR = ROOT / "data" / "sql"

KECAMATAN_PBF = RAW_DIR / "kecamatan_admin6.osm.pbf"
KELURAHAN_PBF = RAW_DIR / "kelurahan_admin7.osm.pbf"

WIKIPEDIA_RE = re.compile(r'"wikipedia"=>"([^"]*)"')


def load_admin_features(pbf_path, admin_level):
    result = subprocess.run(
        [
            "ogr2ogr",
            "-f",
            "GeoJSON",
            "/vsistdout/",
            str(pbf_path),
            "-sql",
            "SELECT osm_id, name, other_tags FROM multipolygons "
            f"WHERE boundary='administrative' AND admin_level='{admin_level}'",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["features"]


def sql_str(value):
    if value is None:
        return "NULL"

    return "'" + str(value).replace("'", "''") + "'"


def parent_kecamatan_name(other_tags):
    match = WIKIPEDIA_RE.search(other_tags or "")

    if not match:
        raise ValueError(f"missing wikipedia tag in other_tags: {other_tags!r}")

    # "id:Legian, Kuta, Badung" -> "Legian, Kuta, Badung" -> ["Legian", "Kuta", "Badung"]
    article = match.group(1).split(":", 1)[-1]
    parts = [p.strip() for p in article.split(",")]

    if len(parts) != 3:
        raise ValueError(f"unexpected wikipedia article format: {match.group(1)!r}")

    return parts[1]


def main():
    kecamatan_features = load_admin_features(KECAMATAN_PBF, 6)
    kecamatan_names = {f["properties"]["name"] for f in kecamatan_features}

    kelurahan_features = load_admin_features(KELURAHAN_PBF, 7)

    lines = []

    for feature in kelurahan_features:
        props = feature["properties"]
        kecamatan_name = parent_kecamatan_name(props.get("other_tags"))

        if kecamatan_name not in kecamatan_names:
            raise ValueError(
                f"{props['name']!r} (osm_id {props['osm_id']}) resolved to "
                f"kecamatan {kecamatan_name!r}, not found in kecamatan_admin6.osm.pbf"
            )

        lines.append(
            "INSERT INTO Kelurahan (id, kelurahanName, kecamatanName) "
            f"VALUES ({sql_str(props['osm_id'])}, {sql_str(props['name'])}, "
            f"{sql_str(kecamatan_name)});"
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "insert_kelurahan.sql").write_text("\n".join(lines) + "\n")
    print(f"wrote {len(lines)} rows -> {OUT_DIR / 'insert_kelurahan.sql'}")


if __name__ == "__main__":
    main()
