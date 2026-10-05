"""Unit tests for the Houston City Directory auditor and Google Sheet sync pipeline."""

from __future__ import annotations

import json
import time
from pathlib import Path

from atlas_pipeline.city_directory_audit import (
    ContentDmCityDirectoryClient,
    audit_suspect_property,
    export_findings_to_csv,
    parse_street_address,
    verify_address_in_ocr,
)
from atlas_pipeline.sync_curated_edits import parse_approved_csv_rows, sync_curated_overrides


def test_parse_street_address() -> None:
    assert parse_street_address("1127 KEY ST") == (1127, "Key", "")
    assert parse_street_address("2115 N MAIN ST APT B") == (2115, "Main", "N")
    assert parse_street_address("704 E 14TH ST") == (704, "14Th", "E")
    assert parse_street_address("0 UNKNOWN AVE") == (0, "Unknown", "")
    assert parse_street_address("MAIN ST") is None


def test_verify_address_in_ocr() -> None:
    # Direct alphabetical resident directory entry
    ocr_alpha = "Morris David (Sarah) archt h 1127 Key Tel Capitol 4120"
    snippet = verify_address_in_ocr(ocr_alpha, 1127, "Key")
    assert snippet is not None
    assert "1127 Key" in snippet

    # Reverse Street and Avenue Guide entry
    ocr_guide = "KEY ST—From 4200 N Main east to Studewood. 1123 Smith J 1125 Jones R 1127 Morris D"
    snippet_guide = verify_address_in_ocr(ocr_guide, 1127, "Key")
    assert snippet_guide is not None
    assert "1127" in snippet_guide

    # Unrelated phone number or different street should return None
    ocr_false = "Keystone Bldg 1120 Texas Ave Tel Preston 1127"
    assert verify_address_in_ocr(ocr_false, 1127, "Key") is None


def test_audit_suspect_property_with_sqlite_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "test_citydir.sqlite"
    client = ContentDmCityDirectoryClient(cache_db_path=db_path, min_delay_seconds=0.5)
    try:
        # Seed SQLite cache so no network calls occur
        with client.conn:
            for idx, yr in enumerate(range(1886, 1927), start=100):
                client.conn.execute(
                    "INSERT INTO volume_years(parent_pointer, year, title) VALUES (?, ?, ?)",
                    (idx, yr, f"{yr} Houston City Directory"),
                )

            # Seed 1127 Key St as having 0 hits through 1926, while neighbor 1125 Key St appears in 1922 & 1926
            client.conn.execute(
                "INSERT INTO search_cache(query_key, response_json, fetched_at) VALUES (?, ?, ?)",
                ("phrase:1127 key:40", json.dumps({"records": []}), time.time()),
            )
            client.conn.execute(
                "INSERT INTO search_cache(query_key, response_json, fetched_at) VALUES (?, ?, ?)",
                ("phrase:1123 key:40", json.dumps({"records": []}), time.time()),
            )
            client.conn.execute(
                "INSERT INTO search_cache(query_key, response_json, fetched_at) VALUES (?, ?, ?)",
                (
                    "phrase:1125 key:40",
                    json.dumps(
                        {
                            "records": [
                                {"pointer": 5001, "parentobject": 136},
                                {"pointer": 5002, "parentobject": 140},
                            ]
                        }
                    ),
                    time.time(),
                ),
            )
            client.conn.execute(
                "INSERT INTO item_cache(pointer, parent_pointer, year, title, transc, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
                (5001, 136, 1922, "Page 810", "Wilson Geo h 1125 Key", time.time()),
            )
            client.conn.execute(
                "INSERT INTO item_cache(pointer, parent_pointer, year, title, transc, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
                (5002, 140, 1926, "Page 940", "Wilson Geo h 1125 Key", time.time()),
            )

            # Seed an earlier-than-HCAD property (e.g. 1502 Heights Blvd listed in 1905 while HCAD says 1920)
            client.conn.execute(
                "INSERT INTO search_cache(query_key, response_json, fetched_at) VALUES (?, ?, ?)",
                (
                    "phrase:1502 heights:40",
                    json.dumps({"records": [{"pointer": 6001, "parentobject": 119}]}),
                    time.time(),
                ),
            )
            client.conn.execute(
                "INSERT INTO item_cache(pointer, parent_pointer, year, title, transc, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
                (6001, 119, 1905, "Page 312", "Carter Wm J h 1502 Heights Blvd", time.time()),
            )

        finding_key = audit_suspect_property(
            client,
            {
                "hcad_num": "0621100000014",
                "address": "1127 KEY ST",
                "historic_district": "Norhill Historic District",
                "year_built": 1920,
            },
            check_neighbors=True,
        )
        assert finding_key.classification == "ABSENT_THROUGH_1926_PREMATURE_HCAD_1920"
        assert finding_key.suggested_year_built == 1928
        assert finding_key.neighbor_dir_years == [1922, 1926]

        finding_heights = audit_suspect_property(
            client,
            {
                "hcad_num": "0201520000001",
                "address": "1502 HEIGHTS BLVD",
                "historic_district": "Houston Heights Historic District South",
                "year_built": 1920,
            },
            check_neighbors=False,
        )
        assert finding_heights.classification == "EARLIER_THAN_HCAD"
        assert finding_heights.suggested_year_built == 1905
        assert finding_heights.earliest_dir_year == 1905

        out_csv = tmp_path / "candidates.csv"
        export_findings_to_csv([finding_key, finding_heights], out_csv)
        assert out_csv.exists()
        csv_content = out_csv.read_text(encoding="utf-8")
        assert "0621100000014" in csv_content
        assert "0201520000001" in csv_content
    finally:
        client.close()


def test_sync_curated_edits_merges_only_approved_rows(tmp_path: Path) -> None:
    sample_csv = "\n".join(
        [
            "status,hcad_num,address,year_built,original_hcad_year,source_type,source_citation",
            "Approved,0621100000014,1127 KEY ST,1928,1920,Houston City Directory,1928 City Directory",
            "Pending,0621100000099,999 KEY ST,1915,1920,User Submission,Unverified",
        ]
    )
    rows = parse_approved_csv_rows(sample_csv)
    assert len(rows) == 1
    assert rows[0]["hcad_num"] == "0621100000014"
    assert rows[0]["year_built"] == 1928

    csv_file = tmp_path / "approved.csv"
    csv_file.write_text(sample_csv, encoding="utf-8")
    buildings_file = tmp_path / "buildings.geojson"
    buildings_file.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "id": "bld_002168",
                            "hcad_num": "0621100000014",
                            "address": "1127 KEY ST",
                            "year_built": 1920,
                            "decade": "1920s",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[-95.377, 29.792], [-95.376, 29.792], [-95.376, 29.793], [-95.377, 29.792]]],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    stats = sync_curated_overrides(data_dir=tmp_path, local_csv_path=csv_file)
    assert stats["total_overrides"] == 1
    assert stats["updated_buildings_geojson"] == 1

    updated_blds = json.loads(buildings_file.read_text(encoding="utf-8"))
    props = updated_blds["features"][0]["properties"]
    assert props["year_built"] == 1928
    assert props["original_hcad_year"] == 1920
    assert props["is_curated_override"] is True
