"""Milestone 10 hermetic unit tests for TARO 2.0 + HPL ContentDM archival harvest and 1927-1963 USCO copyright clearance."""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import pytest

from atlas_pipeline import cli
from atlas_pipeline.audit_citydir_copyright_1927_1963 import (
    build_clearance_rows_1927_1963,
    classify_usco_renewal_flag,
    extract_directory_year,
    is_houston_city_directory_hit,
    parse_usco_hit,
    run_citydir_copyright_audit,
)
from atlas_pipeline.harvest_taro_and_hpl_archives import (
    REPO_SHORT_LABELS,
    _clean_architect_label,
    build_hpl_cdm_urls,
    extract_candidate_addresses,
    infer_architect_from_text,
    is_architect_within_career_window,
    is_houston_architectural_finding_aid,
    is_non_harris_county_location,
    load_confidential_hcad_set,
    normalize_street_address_keys,
    parse_hpl_cdm_record,
    parse_taro_ead_xml,
    run_archive_harvest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


SAMPLE_TARO_EAD_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ead xmlns="urn:isbn:1-931666-22-9">
  <archdesc level="collection">
    <did>
      <unittitle>William T. Cannady Architectural Papers</unittitle>
      <origination><persname>Cannady, William T., FAIA</persname></origination>
    </did>
    <dsc type="combined">
      <c01 level="series">
        <did><unittitle>Project Files</unittitle></did>
        <c02 level="file">
          <did>
            <container type="box">12</container>
            <container type="folder">4</container>
            <unitid>74-101</unitid>
            <unittitle> Lovett Square Townhomes, 310 Lovett Blvd, Houston, TX </unittitle>
            <unitdate>1978</unitdate>
          </did>
          <scopecontent><p>Architectural elevations and site plans for Lovett Boulevard project.</p></scopecontent>
        </c02>
        <c02 level="file">
          <did>
            <container type="box">14</container>
            <container type="folder">2</container>
            <unittitle>Beach House at 1200 Seawall Blvd, Galveston, Tex.</unittitle>
            <unitdate>1980</unitdate>
          </did>
        </c02>
        <c02 level="file">
          <did>
            <container type="box">15</container>
            <container type="folder">9</container>
            <unittitle>Undated Sketch for Historic Downtown Theater</unittitle>
          </did>
        </c02>
      </c01>
      <c01 level="series">
        <did><unittitle>Printed Material and Clippings</unittitle></did>
        <c02 level="file">
          <did>
            <container type="box">40</container>
            <container type="folder">1</container>
            <unittitle>Architectural Design, Volume XLIII, Jan. 1972, with article on One Shell Plaza</unittitle>
            <unitdate>1972</unitdate>
          </did>
        </c02>
      </c01>
    </dsc>
  </archdesc>
</ead>
"""


def test_taro_ead_xml_and_hpl_cdm_parsing_and_iiif_fallbacks() -> None:
    items = parse_taro_ead_xml(
        SAMPLE_TARO_EAD_XML,
        repo_code="ricewrc",
        ead_filename="00694.xml",
    )
    # Galveston folder must be filtered out; 3 Houston/generic folders remain
    assert len(items) == 3
    by_title = {it.title: it for it in items}

    lovett = by_title["Lovett Square Townhomes, 310 Lovett Blvd, Houston, TX"]
    assert lovett.collection_key == "taro:ricewrc/00694"
    assert lovett.url == "https://txarchives.org/ricewrc/finding_aids/00694.xml"
    assert lovett.year == 1978
    assert lovett.architect == "William T. Cannady"
    assert "Job/ID 74-101" in lovett.container_or_ref
    assert "Box 12, Folder 4" in lovett.container_or_ref

    # Magazine/periodical folder in Printed Material series must NOT inherit William T. Cannady as architect
    mag = by_title["Architectural Design, Volume XLIII, Jan. 1972, with article on One Shell Plaza"]
    assert mag.year == 1972
    assert mag.architect == ""

    # Malformed XML fails safely to empty list
    assert parse_taro_ead_xml(b"<broken><xml>", repo_code="ricewrc", ead_filename="bad.xml") == []

    # Preserve abbreviation trailing periods in architect labels
    assert _clean_architect_label("Donald Barthelme, Sr.") == "Donald Barthelme, Sr."
    assert _clean_architect_label("I.S. Brockstein and assoc., archs.") == "I.S. Brockstein and assoc."
    assert _clean_architect_label("Maurice J. Sullivan.") == "Maurice J. Sullivan"
    assert infer_architect_from_text("1947 / Donald Barthelme, Sr., architect") == "Donald Barthelme, Sr."

    # HPL ContentDM URL builder: IIIF on p17006coll41/175/199/231 vs singleitem fallback on p17006coll220/238
    for open_coll in ("p17006coll41", "p17006coll175", "p17006coll199", "p17006coll231"):
        src_u, img_u, th_u = build_hpl_cdm_urls(open_coll, 34, iiif_supported=True, has_jp2=True)
        assert src_u == f"https://cdm17006.contentdm.oclc.org/digital/collection/{open_coll}/id/34"
        assert img_u == f"https://cdm17006.contentdm.oclc.org/digital/api/singleitem/image/{open_coll}/34/default.jpg"
        assert th_u == f"https://cdm17006.contentdm.oclc.org/digital/iiif/{open_coll}/34/full/960,/0/default.jpg"

    for non_iiif_coll in ("p17006coll220", "p17006coll238"):
        src_u, img_u, th_u = build_hpl_cdm_urls(non_iiif_coll, 115, iiif_supported=False, has_jp2=True)
        assert src_u == f"https://cdm17006.contentdm.oclc.org/digital/collection/{non_iiif_coll}/id/115"
        assert img_u == f"https://cdm17006.contentdm.oclc.org/digital/api/singleitem/image/{non_iiif_coll}/115/default.jpg"
        assert th_u == img_u
        assert "/digital/iiif/" not in th_u

    assert build_hpl_cdm_urls("p17006coll41", 9, iiif_supported=True, has_jp2=False) == (
        "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll41/id/9",
        "",
        "",
    )

    # HPL record parser checks
    rec_220 = parse_hpl_cdm_record(
        "p17006coll220",
        "HPL Houston Architectural Survey Photographs (RG D 0038)",
        "",
        False,
        {
            "dmrecord": "76",
            "title": "Ritz Theater building",
            "captio": "Ritz Theater building, located at 911 Preston Street in Houston, Texas.",
            "identi": "RGD0038-017-4-001",
            "find": "77.jp2",
            "type": "image",
            "date": "2019",  # digitization year must be ignored
        },
    )
    assert rec_220 is not None
    assert rec_220.year is None
    assert rec_220.is_visual_photo_or_rendering is True
    assert "/digital/iiif/" not in rec_220.thumb_url

    rec_clipping = parse_hpl_cdm_record(
        "p17006coll238",
        "HPL Architectural Vertical Files",
        "",
        False,
        {
            "dmrecord": "12",
            "title": "News clipping on Gulf Building",
            "descri": "812 Main Street, Houston, TX, 1929",
            "find": "13.jp2",
            "type": "text",
        },
    )
    assert rec_clipping is not None
    assert rec_clipping.is_visual_photo_or_rendering is False

    # Out-of-county HPL item rejected
    assert (
        parse_hpl_cdm_record(
            "p17006coll199",
            "HPL Howard Barnstone Papers",
            "",
            True,
            {
                "dmrecord": "99",
                "title": "Ashton Villa",
                "place": "Galveston, Texas",
                "find": "100.jp2",
            },
        )
        is None
    )


def test_municipality_filtering_and_career_windows() -> None:
    # Out-of-county / out-of-state municipalities rejected
    assert is_non_harris_county_location(" Courthouse in Galveston, Tex.") is True
    assert is_non_harris_county_location("Residence at 400 Swiss Ave, Dallas, Texas") is True
    assert is_non_harris_county_location("Imperial Sugar Refinery, Sugar Land, TX") is True
    assert is_non_harris_county_location("Office Tower (N.Y.), Houston Firm") is True
    assert is_non_harris_county_location("Alamo Plaza, San Antonio, Texas") is True

    # Harris County municipalities accepted
    assert is_non_harris_county_location("310 Lovett Blvd, Houston, TX") is False
    assert is_non_harris_county_location("5100 Bellaire Blvd, Bellaire, Texas") is False
    assert is_non_harris_county_location("Goose Creek School, Baytown, Tex.") is False
    assert is_non_harris_county_location("Pasadena State Bank, Pasadena, TX") is False
    assert is_non_harris_county_location("3600 University Blvd, West University Place, TX") is False

    # Finding-aid filter
    assert is_houston_architectural_finding_aid(
        {
            "repository": "ricewrc",
            "title": "William Ward Watkin Architectural Papers",
            "geographic_areas": ["Houston (Tex.)"],
            "subject_topics": ["Architecture"],
        }
    ) is True
    assert is_houston_architectural_finding_aid(
        {
            "repository": "utcah",
            "title": "Dallas Cattle Ranching Oral History Interview Collection",
            "geographic_areas": ["Dallas (Tex.)"],
            "subject_topics": ["Ranching"],
        }
    ) is False

    # Address extraction
    addrs = extract_candidate_addresses("Mid-Main Properties, 3500 Main St, Houston, TX 77002 and 1400 Block Main St")
    assert len(addrs) == 1
    assert addrs[0][1] == "3500 MAIN"

    # Career windows
    assert is_architect_within_career_window("Alfred C. Finn", 1929) is True
    assert is_architect_within_career_window("Alfred C. Finn", 1995) is False
    assert is_architect_within_career_window("William Ward Watkin", 1940) is True
    assert is_architect_within_career_window("William Ward Watkin", 1978) is False
    assert is_architect_within_career_window("William T. Cannady", 1978) is True
    assert is_architect_within_career_window("William T. Cannady", 1935) is False


def test_harvest_archives_offline_e2e_cli_and_confidential_hcad_guards(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    output_dir = tmp_path / "data"
    taro_dir = cache_dir / "taro_ead_cache"
    ndjson_dir = cache_dir / "aligned_ndjson"
    permits_dir = output_dir / "permits"
    taro_dir.mkdir(parents=True)
    ndjson_dir.mkdir(parents=True)
    permits_dir.mkdir(parents=True)

    # 1. Confidential HCAD shards (both .json.gz and dual-mode .json)
    with gzip.open(permits_dir / "permits_060.json.gz", "wt", encoding="utf-8") as gz:
        json.dump(
            {
                "0609990000001": {"yr": 1935, "conf": 1},
                "0601110000002": {"yr": 1938, "conf": 0},
            },
            gz,
        )
    (permits_dir / "permits_061.json").write_text(
        json.dumps({"0619990000003": {"yr": 1940, "conf": 1}}),
        encoding="utf-8",
    )
    conf_loaded = load_confidential_hcad_set(output_dir)
    assert "0609990000001" in conf_loaded
    assert "0619990000003" in conf_loaded
    assert "0601110000002" not in conf_loaded

    # 2. TARO cached catalog + XML (includes dated Lovett match, undated theater folder, >10y delta folder, and confidential address)
    ead_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<ead>
  <archdesc level="collection">
    <did>
      <unittitle>William Ward Watkin Architectural Papers</unittitle>
    </did>
    <dsc>
      <c01 level="file">
        <did>
          <container type="box">1</container>
          <container type="folder">1</container>
          <unittitle>Central Sanctuary, 4100 Montrose Blvd, Houston, TX</unittitle>
          <unitdate>1940</unitdate>
        </did>
      </c01>
      <c01 level="file">
        <did>
          <container type="box">1</container>
          <container type="folder">2</container>
          <unittitle>Historic Ritz Theater</unittitle>
        </did>
      </c01>
      <c01 level="file">
        <did>
          <container type="box">1</container>
          <container type="folder">3</container>
          <unittitle>Early Sketch for 500 Lovett Blvd, Houston, TX</unittitle>
          <unitdate>1915</unitdate>
        </did>
      </c01>
      <c01 level="file">
        <did>
          <container type="box">2</container>
          <container type="folder">1</container>
          <unittitle>Confidential Judge Residence, 999 Secret Way, Houston, TX</unittitle>
          <unitdate>1935</unitdate>
        </did>
      </c01>
    </dsc>
  </archdesc>
</ead>
"""
    (taro_dir / "ricewrc__00034.xml").write_bytes(ead_xml)
    (taro_dir / "search_catalog.json").write_text(
        json.dumps(
            [
                {
                    "repository": "ricewrc",
                    "repository_name": "Woodson Research Center",
                    "filename": "00034.xml",
                    "title": "William Ward Watkin Architectural Papers",
                    "display_site": "https://txarchives.org/ricewrc/finding_aids/00034.xml",
                    "xml": "https://txarchives.org/admin/ricewrc/00034.xml",
                }
            ]
        ),
        encoding="utf-8",
    )

    # 3. HPL cached items (includes duplicate record, undated p17006coll220 photo at 2010 replacement teardown,
    #    valid p17006coll41 photo with architect within <=10y, and confidential parcel photo attempt)
    hpl_cache = {
        "p17006coll220": [
            {
                "dmrecord": "10",
                "title": "Demolished 1920s Bungalow",
                "captio": "Located at 3401 Sleepy Hollow Court in Houston, Texas.",
                "identi": "RGD0038-010",
                "find": "10.jp2",
                "type": "image",
            },
            # Duplicate of dmrecord 10 -> must be deduplicated
            {
                "dmrecord": "10",
                "title": "Demolished 1920s Bungalow",
                "captio": "Located at 3401 Sleepy Hollow Court in Houston, Texas.",
                "identi": "RGD0038-010",
                "find": "10.jp2",
                "type": "image",
            },
            {
                "dmrecord": "20",
                "title": "Confidential House Photo",
                "captio": "Located at 999 Secret Way in Houston, Texas.",
                "identi": "RGD0038-020",
                "find": "20.jp2",
                "type": "image",
            },
            {
                "dmrecord": "30",
                "title": "Old Demolished Bank at 700 Main Street",
                "captio": "Located at 700 Main Street in Houston, Texas.",
                "identi": "RGD0038-030",
                "find": "30.jp2",
                "type": "image",
            },
        ],
        "p17006coll41": [
            {
                "dmrecord": str(rec_id),
                "title": f"Mirabeau B. Lamar Senior High School View {rec_id}",
                "captio": "3325 Westheimer Rd. 1937 / Kenneth Franzheim, architect.",
                "identi": f"MSS0097-0{rec_id}",
                "find": f"{rec_id}.jp2",
                "type": "image",
            }
            for rec_id in (81, 82, 83, 84, 85)
        ],
    }
    (cache_dir / "hpl_arch_collections_cache.json").write_text(json.dumps(hpl_cache), encoding="utf-8")

    # 4. Bundle inputs
    overrides_payload = {
        "overrides": {
            "0261640000027": {
                "hcad_num": "0261640000027",
                "address": "4100 MONTROSE BLVD",
                "building_name": "Central Sanctuary",
                "year_built": 1940,
                "architect": "",
            },
            "0261640000027#aux_1": {
                "hcad_num": "0261640000027",
                "address": "4100 MONTROSE BLVD",
                "building_name": "Carriage House Auxiliary",
                "year_built": 1941,
                "architect": "",
            },
            "0010330000012": {
                "hcad_num": "0010330000012",
                "address": "911 PRESTON ST",
                "landmark_name": "Historic Ritz Theater",
                "year_built": 1926,
                "architect": "",
            },
            "0500010000001": {
                "hcad_num": "0500010000001",
                "address": "500 LOVETT BLVD",
                "building_name": "Midcentury Lovett House",
                "year_built": 1948,  # |1915 - 1948| = 33 > 10y delta -> must NOT backfill Watkin
                "architect": "",
            },
            "1181090010001": {
                "hcad_num": "1181090010001",
                "address": "3325 WESTHEIMER RD",
                "building_name": "Mirabeau B. Lamar Senior High School",
                "year_built": 1937,
                "architect": "",
            },
            "bld_conf_override": {
                "hcad_num": "0609990000001",
                "address": "999 SECRET WAY",
                "building_name": "Confidential Judge Residence",
                "year_built": 1935,
                "architect": "",
            },
        }
    }
    (output_dir / "curated_overrides.json").write_text(json.dumps(overrides_payload), encoding="utf-8")

    buildings_payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": "bld_lamar",
                    "hcad_num": "1181090010001",
                    "address": "3325 WESTHEIMER RD",
                    "building_name": "Mirabeau B. Lamar Senior High School",
                    "year_built": 1937,
                    "architect": "",
                },
                "geometry": {"type": "Point", "coordinates": [-95.42, 29.74]},
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "0601420000003",
                    "hcad_num": "0601420000003",
                    "address": "3401 SLEEPY HOLLOW CT",
                    "year_built": 2010,  # Post-1981 replacement teardown -> must reject undated 1980 HPL survey photo!
                    "architect": "",
                },
                "geometry": {"type": "Point", "coordinates": [-95.44, 29.75]},
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "0010700000001",
                    "hcad_num": "0010700000001",
                    "address": "700 MAIN ST",
                    "year_built": 2015,  # Modern tower at site of Lost Houston bank
                    "architect": "",
                },
                "geometry": {"type": "Point", "coordinates": [-95.36, 29.76]},
            },
            {
                "type": "Feature",
                "properties": {
                    "id": "0619990000003",
                    "hcad_num": "0619990000003",
                    "address": "999 SECRET WAY",
                    "year_built": 1940,
                    "architect": "",
                },
                "geometry": {"type": "Point", "coordinates": [-95.45, 29.75]},
            },
        ],
    }
    (output_dir / "buildings.geojson").write_text(json.dumps(buildings_payload), encoding="utf-8")

    overlays_payload = {
        "lost_houston": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "id": "lost_old_bank_700_main",
                        "name": "Old Demolished Bank",
                        "address": "700 MAIN ST",
                        "year_built": 1910,
                    },
                    "geometry": {"type": "Point", "coordinates": [-95.36, 29.76]},
                }
            ],
        }
    }
    (output_dir / "overlays.json").write_text(json.dumps(overlays_payload), encoding="utf-8")

    search_idx_payload = [
        {
            "id": "1181090010001",
            "hcad_num": "1181090010001",
            "label": "Mirabeau B. Lamar Senior High School",
            "address": "3325 WESTHEIMER RD",
            "year_built": 1937,
            "architect": "",
        },
        {
            "id": "0261640000027#aux_1",
            "hcad_num": "0261640000027",
            "label": "Carriage House Auxiliary",
            "address": "4100 MONTROSE BLVD",
            "year_built": 1941,
            "architect": "",
        },
    ]
    (output_dir / "search_index.json").write_text(json.dumps(search_idx_payload), encoding="utf-8")
    (output_dir / "building_photos.json").write_text('{"photos":[]}', encoding="utf-8")

    # Dry-run first: must NOT create archival_index.json
    dry_stats = run_archive_harvest(cache_dir=cache_dir, output_dir=output_dir, apply=False, offline=True)
    assert dry_stats["apply"] is False
    assert not (output_dir / "archival_index.json").exists()

    # Apply via CLI --mode harvest-archives --offline --apply
    cli.main(
        [
            "--mode",
            "harvest-archives",
            "--offline",
            "--apply",
            "--cache-dir",
            str(cache_dir),
            "--output-dir",
            str(output_dir),
        ]
    )

    arch_raw = (output_dir / "archival_index.json").read_text(encoding="utf-8")
    assert "\n" not in arch_raw
    arch = json.loads(arch_raw)

    # Sequential zero-orphan keys a1..aN
    item_keys = list(arch["items"].keys())
    assert item_keys == [f"a{i}" for i in range(1, len(item_keys) + 1)]
    referenced = {aid for arr in arch["by_key"].values() for aid in arr} | {
        aid for arr in arch["by_addr"].values() for aid in arr
    }
    assert referenced == set(item_keys)

    # Confidential HCADs and secret address MUST NEVER appear anywhere
    assert "0609990000001" not in arch["by_key"]
    assert "0619990000003" not in arch["by_key"]
    assert "bld_conf_override" not in arch["by_key"]
    assert "999 SECRET" not in arch["by_addr"]
    for it in arch["items"].values():
        assert "Secret Way" not in it["t"]
        assert "Confidential" not in it["t"]

    # 2010 teardown at 3401 Sleepy Hollow Ct rejected; 700 Main St routed ONLY to lost_old_bank_700_main (not by_addr!)
    assert "0601420000003" not in arch["by_key"]
    assert "3401 SLEEPY HOLLOW" not in arch["by_addr"]
    assert "lost_old_bank_700_main" in arch["by_key"]
    assert "0010700000001" not in arch["by_key"]
    assert "700 MAIN" not in arch["by_addr"]

    # Single-line building_photos.json serialization & 3-photo-per-parcel cap
    photos_raw = (output_dir / "building_photos.json").read_text(encoding="utf-8")
    assert "\n" not in photos_raw
    photos = json.loads(photos_raw)["photos"]
    photo_blds = {p["building_id"] for p in photos}
    assert "1181090010001" in photo_blds
    assert sum(1 for p in photos if p["building_id"] == "1181090010001") == 3
    assert "lost_old_bank_700_main" in photo_blds
    assert "0601420000003" not in photo_blds
    assert "0609990000001" not in photo_blds

    # Second apply=True run must be strictly idempotent (photos_added == 0 even when >3 candidates exist)
    summary_second = run_archive_harvest(
        cache_dir=cache_dir,
        output_dir=output_dir,
        offline=True,
        apply=True,
    )
    assert summary_second["photos_added_to_building_photos"] == 0
    assert (output_dir / "building_photos.json").read_text(encoding="utf-8") == photos_raw

    # Architect backfill guards:
    # - 0261640000027 (1940 vs 1940) -> William Ward Watkin
    # - 0261640000027#aux_1 -> remains "" (#aux_1 isolated)
    # - 0010330000012 (undated Ritz folder) -> remains ""
    # - 0500010000001 (|1915 - 1948| = 33 > 10) -> remains ""
    # - 1181090010001 (1937 vs 1937) -> Kenneth Franzheim
    updated_ov = json.loads((output_dir / "curated_overrides.json").read_text(encoding="utf-8"))["overrides"]
    assert updated_ov["0261640000027"]["architect"] == "William Ward Watkin"
    assert updated_ov["0261640000027#aux_1"]["architect"] == ""
    assert updated_ov["0010330000012"]["architect"] == ""
    assert updated_ov["0500010000001"]["architect"] == ""
    assert updated_ov["1181090010001"]["architect"] == "Kenneth Franzheim"
    assert updated_ov["bld_conf_override"]["architect"] == ""

    updated_sx = {e["id"]: e for e in json.loads((output_dir / "search_index.json").read_text(encoding="utf-8"))}
    assert updated_sx["1181090010001"]["architect"] == "Kenneth Franzheim"
    assert updated_sx["0261640000027#aux_1"]["architect"] == ""


def test_confidential_hcad_loader_fails_closed_on_corrupt_or_malformed_shard(tmp_path: Path) -> None:
    import zlib

    out_corrupt = tmp_path / "corrupt_gz"
    (out_corrupt / "permits").mkdir(parents=True)
    (out_corrupt / "permits" / "permits_060.json.gz").write_bytes(b"\x1f\x8b\x08\x00corrupted-gzip-stream")
    with pytest.raises((OSError, EOFError, zlib.error)):
        load_confidential_hcad_set(out_corrupt)

    out_bad_type = tmp_path / "bad_json_type"
    (out_bad_type / "permits").mkdir(parents=True)
    (out_bad_type / "permits" / "permits_060.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match=r"Expected JSON object|Fail-closed TX Tax Code Sec\. 25\.025 guard"):
        load_confidential_hcad_set(out_bad_type)


def test_usco_citydir_1927_1963_copyright_clearance_unit_and_live_bundle(tmp_path: Path) -> None:
    # Renewal detection
    assert classify_usco_renewal_flag({"registration_number": ["RE0000123456"], "type_of_record": "registration"}) is True
    assert classify_usco_renewal_flag({"copyright_number_for_display": "R 456789"}) is True
    assert classify_usco_renewal_flag({"type_of_record": "renewal"}) is True
    assert classify_usco_renewal_flag({"copyright_number_for_display": "A 155265", "type_of_record": "registration"}) is False

    # Houston directory vs non-Houston / telephone filtering
    assert is_houston_city_directory_hit(
        {
            "title_of_work": ["Morrison & Fourmy's Houston (Texas) City Directory 1935"],
            "claimant": ["Morrison & Fourmy Directory Co."],
        }
    ) is True
    assert is_houston_city_directory_hit(
        {
            "title_of_work": ["Worley's Dallas (Texas) City Directory 1935"],
            "claimant": ["John F. Worley Directory Co."],
        }
    ) is False
    assert is_houston_city_directory_hit(
        {
            "title_of_work": ["Southwestern Bell Houston Telephone Directory 1950"],
            "claimant": ["Southwestern Bell Telephone Co."],
        }
    ) is False

    # 1941 vs 1944 OCR date-bounding test (USCO A 155265 filed 1941-07-14 with OCR typo "1944- VOL XLV")
    hit_1941 = {
        "public_records_id": "usco-1941",
        "copyright_number_for_display": "A 155265",
        "first_published_date_as_date": "1941-07-14",
        "representative_date": "1941-07-14",
        "title_of_work": [
            "Morrison&Fourmy's Greater Houston(Texas) City Directory 1941 - VoL XLV",
            "Morrison & Fourmy's Greater Houston (Texas) City Directory 1944- VOL XLV",
        ],
        "claimant": ["MORRISON & FOURMY DIRECTORY CO"],
        "type_of_record": "registration",
    }
    assert extract_directory_year(hit_1941) == 1941
    parsed_1941 = parse_usco_hit({"hit": hit_1941})
    assert parsed_1941 is not None
    assert parsed_1941.inferred_year == 1941
    assert parsed_1941.is_renewal is False

    # Offline round-trip on tmp_path
    cache_dir = tmp_path / "cache"
    docs_data = tmp_path / "docs_data"
    app_data = tmp_path / "app_data"
    cache_dir.mkdir()
    docs_data.mkdir()
    app_data.mkdir()

    raw_cache_payload = {
        "verified_date": "2026-11-08",
        "query_counts": {'"Morrison" "Fourmy"': 1},
        "unique_raw_records": 2,
        "raw_entries": [
            {"hit": hit_1941},
            {
                "hit": {
                    "public_records_id": "usco-dallas-1941",
                    "copyright_number_for_display": "A 155999",
                    "first_published_date_as_date": "1941-08-01",
                    "title_of_work": ["Worley's Dallas (Texas) City Directory 1941"],
                    "claimant": ["John F. Worley Directory Co."],
                    "type_of_record": "registration",
                }
            },
        ],
    }
    (cache_dir / "usco_houston_citydir_audit.json").write_text(json.dumps(raw_cache_payload), encoding="utf-8")
    (app_data / "citydir_index.json").write_text(json.dumps({"volumes": []}), encoding="utf-8")
    tmp_csv = docs_data / "houston_city_directories_1927_1963_usco_clearance.csv"

    summary = run_citydir_copyright_audit(
        cache_dir=cache_dir,
        output_dir=app_data,
        csv_output_path=tmp_csv,
        offline=True,
    )
    assert summary["audited_years"] == 37
    assert summary["class_a_1927_1963_count"] == 1
    assert summary["class_r_re_renewals_found"] == 0
    tmp_citydir = json.loads((app_data / "citydir_index.json").read_text(encoding="utf-8"))
    assert tmp_citydir["post_1926_clearance"]["all_1927_1963_public_domain"] is True

    # Counterfactual guard 1: untitled Class RE renewal (no year in title, no first_published_date_as_date)
    # anchors to representative_date - 28 (1983 - 28 = 1955), flipping both all_1927_1963_public_domain
    # and the 1955 CSV row to "Copyright Renewed (Verify Scope)".
    renewal_cache = {
        "verified_date": "2026-11-08",
        "query_counts": {'"Morrison" "Fourmy"': 2},
        "unique_raw_records": 2,
        "raw_entries": [
            {"hit": hit_1941},
            {
                "hit": {
                    "public_records_id": "usco-ren-1955",
                    "copyright_number_for_display": "RE0000999999",
                    "registration_number": ["RE0000999999"],
                    "representative_date": "1983-05-01",
                    "title_of_work": ["Morrison & Fourmy's Houston (Texas) City Directory"],
                    "claimant": ["R. L. Polk & Co."],
                    "type_of_record": "renewal",
                }
            },
        ],
    }
    (cache_dir / "usco_houston_citydir_audit.json").write_text(json.dumps(renewal_cache), encoding="utf-8")
    ren_summary = run_citydir_copyright_audit(
        cache_dir=cache_dir,
        output_dir=app_data,
        csv_output_path=tmp_csv,
        offline=True,
    )
    assert ren_summary["class_r_re_renewals_found"] == 1
    ren_citydir = json.loads((app_data / "citydir_index.json").read_text(encoding="utf-8"))
    assert ren_citydir["post_1926_clearance"]["all_1927_1963_public_domain"] is False
    with open(tmp_csv, encoding="utf-8", newline="") as f:
        ren_csv_rows = {int(r["directory_year"]): r for r in csv.DictReader(f)}
    assert int(ren_csv_rows[1955]["usco_class_r_re_renewals_found"]) == 1
    assert ren_csv_rows[1955]["copyright_status"] == "Copyright Renewed (Verify Scope)"
    assert ren_csv_rows[1983 if 1983 in ren_csv_rows else 1960]["copyright_status"].startswith("Public Domain")

    # Counterfactual guard 2: post-1963 renewal (1965 edition renewed in 1993) must NOT taint 1927-1963 clearance
    post_1963_renewal_cache = {
        "verified_date": "2026-11-08",
        "query_counts": {'"Polk" "Houston" "Directory"': 2},
        "unique_raw_records": 2,
        "raw_entries": [
            {"hit": hit_1941},
            {
                "hit": {
                    "public_records_id": "usco-ren-1965",
                    "copyright_number_for_display": "RE0000888888",
                    "registration_number": ["RE0000888888"],
                    "representative_date": "1993-05-01",
                    "title_of_work": ["Polk's Houston (Harris County, Tex.) City Directory"],
                    "claimant": ["R. L. Polk & Co."],
                    "type_of_record": "renewal",
                }
            },
        ],
    }
    (cache_dir / "usco_houston_citydir_audit.json").write_text(json.dumps(post_1963_renewal_cache), encoding="utf-8")
    post63_summary = run_citydir_copyright_audit(
        cache_dir=cache_dir,
        output_dir=app_data,
        csv_output_path=tmp_csv,
        offline=True,
    )
    assert post63_summary["class_r_re_renewals_found"] == 0
    post63_citydir = json.loads((app_data / "citydir_index.json").read_text(encoding="utf-8"))
    assert post63_citydir["post_1926_clearance"]["all_1927_1963_public_domain"] is True

    tmp_rows = build_clearance_rows_1927_1963([parsed_1941])
    assert len(tmp_rows) == 37
    assert [r["directory_year"] for r in tmp_rows] == list(range(1927, 1964))

    # Validate live repository CSV + citydir_index.json
    live_csv = REPO_ROOT / "docs" / "data" / "houston_city_directories_1927_1963_usco_clearance.csv"
    live_csv_raw = live_csv.read_text(encoding="utf-8")
    assert "None:" not in live_csv_raw
    with open(live_csv, encoding="utf-8", newline="") as f:
        live_rows = list(csv.DictReader(f))
    assert len(live_rows) == 37
    assert [int(r["directory_year"]) for r in live_rows] == list(range(1927, 1964))
    assert all(int(r["usco_class_r_re_renewals_found"]) == 0 for r in live_rows)
    row_1927 = next(r for r in live_rows if int(r["directory_year"]) == 1927)
    assert "Street Guide 4978080..4978265" in row_1927["online_open_scan_status"]
    row_1941 = next(r for r in live_rows if int(r["directory_year"]) == 1941)
    assert "A 155265" in row_1941["usco_class_a_registrations"]

    live_citydir = json.loads((REPO_ROOT / "app" / "public" / "data" / "citydir_index.json").read_text(encoding="utf-8"))
    clearance = live_citydir["post_1926_clearance"]
    assert clearance["span"] == [1927, 1963]
    assert clearance["audited_year_count"] == 37
    assert clearance["usco_class_r_re_renewals_found"] == 0
    assert clearance["all_1927_1963_public_domain"] is True
    assert clearance["ancestry_collection_id"] == "2469"
    assert clearance["ancestry_post_1926_volume_count"] == 31
    assert clearance["ancestry_post_1926_distinct_years"] == 28
    assert clearance["ancestry_post_1926_span"] == [1927, 1968]
    assert live_citydir["verified_hit_count"] >= 19000
    assert "0621100000014" in live_citydir["by_hcad"]
    assert live_citydir["by_hcad"]["0621100000014"]["e"] == 1927
    assert live_citydir["by_hcad"]["0621100000014"]["ai"] == "4978175"
    assert any(
        row[0] == 1928 and row[1] == "John F Lake (Johnie M)"
        for row in live_citydir["by_hcad"]["0621100000014"]["r"]
    )
    assert any(
        row[0] == 1932 and row[1] == "Thos B White (Nellie)"
        for row in live_citydir["by_hcad"]["0621100000014"]["r"]
    )
    assert "0261360000003" in live_citydir["by_hcad"]
    assert "0601360000010" in live_citydir["by_hcad"]
    assert "0620620000018" in live_citydir["by_hcad"]
    assert live_citydir["by_hcad"]["0620620000018"]["e"] == 1934
    assert any(
        row[0] == 1934 and row[1] == "Bertha Mirring"
        for row in live_citydir["by_hcad"]["0620620000018"]["r"]
    )
    assert any(
        row[0] == 1935 and row[1] == "Tf Banfield"
        for row in live_citydir["by_hcad"]["0620620000018"]["r"]
    )

    from atlas_pipeline.build_house_history_index import extract_post1926_occupants

    _, yrs_filtered, _, tuples_filtered = extract_post1926_occupants(
        [
            {"y": 1929, "n": "Taft", "r": "Intersects", "sec": "sg"},
            {"y": 1929, "n": "Lw Rowe", "r": "Street Guide Occupant", "sec": "sg"},
            {"y": 1940, "n": "Street", "r": "Inued", "sec": "sg"},
            {"y": 1940, "n": "Arth G Fowler", "r": "Street Guide Occupant", "sec": "sg"},
            {"y": 1954, "n": "Under", "r": "Construction", "sec": "sg"},
        ],
        max_tuples=4,
    )
    assert yrs_filtered == [1929, 1940]
    assert tuples_filtered == [
        [1929, "Lw Rowe", "Street Guide Occupant"],
        [1940, "Arth G Fowler", "Street Guide Occupant"],
    ]

    anc_catalog_path = REPO_ROOT / "docs" / "data" / "houston_city_directories_ancestry_catalog.json"
    assert anc_catalog_path.exists()
    anc_catalog = json.loads(anc_catalog_path.read_text(encoding="utf-8"))
    assert anc_catalog["ancestry_db_id"] == "2469"
    assert anc_catalog["post_1926_volume_entries"] == 31
    assert anc_catalog["post_1926_distinct_years"] == 28
    assert len(anc_catalog["volumes"]) == 31


def test_review_regressions_repo_labels_zero_addr_overlay_years_and_photo_captions(tmp_path: Path) -> None:
    # 1. REPO_SHORT_LABELS maps TARO 2.0 slugs ("utcah", "hamtmc", "tslac", "tyrrell", "utlac")
    #    and parse_taro_ead_xml honors fallback_repo_name before uppercasing unknown codes.
    assert REPO_SHORT_LABELS["utcah"] == "UT Briscoe Center"
    assert "cah" not in REPO_SHORT_LABELS
    fallback_items = parse_taro_ead_xml(
        SAMPLE_TARO_EAD_XML,
        repo_code="newarchive",
        ead_filename="00001.xml",
        fallback_repo_name="Custom Texas Archive",
    )
    assert fallback_items[0].repository == "Custom Texas Archive"

    # 2. Zero-house-number placeholder situs strings ("0 <STREET>") are rejected
    assert normalize_street_address_keys("0 AVENIDA DE LAS AMERICAS") == ("", "")

    # 3. Overlay yr=0 target inherits buildings.geojson year_built, suppress_shard_hcads redirects
    #    suppressed shard HCADs to the active override, long captions preserve intact "(Architect: ...)."
    #    suffixes, osm_city_* targets leave hcad_num="", and per-parcel 3-photo cap spans aliases.
    cache_dir = tmp_path / "cache"
    output_dir = tmp_path / "data"
    (cache_dir / "taro_ead_cache").mkdir(parents=True)
    (cache_dir / "aligned_ndjson").mkdir(parents=True)
    (output_dir / "permits").mkdir(parents=True)
    (cache_dir / "taro_ead_cache" / "search_catalog.json").write_text("[]", encoding="utf-8")

    long_desc = (
        "Perspective architectural rendering of high-rise commercial tower atrium with detailed "
        "travertine colonnade, pedestrian plaza landscaping, fountain basin, and curtain-wall "
        "mullion studies along Louisiana Street in downtown Houston, Texas; cross-referenced in "
        "archival folder MSS0468-224."
    )
    hpl_cache = {
        "p17006coll220": [
            {
                "dmrecord": "1",
                "title": "Undated Pre-1981 Cottage on Overlay Parcel",
                "captio": "Located at 100 Overlay Test Street in Houston, Texas.",
                "identi": "RGD0038-001",
                "find": "1.jp2",
                "type": "image",
            },
            {
                "dmrecord": "2",
                "title": "Knapp Showroom Building Survey Photo",
                "captio": "Knapp Showroom Building in Houston, Texas.",
                "identi": "RGD0038-002",
                "find": "2.jp2",
                "type": "image",
            },
        ],
        "p17006coll175": [
            {
                "dmrecord": str(100 + idx),
                "title": f"Downtown Atrium Tower Rendering {idx}",
                "descri": long_desc,
                "creato": "Philip Johnson",
                "captio": f"700 Louisiana St, Houston, TX. 1982. {long_desc}",
                "identi": f"MSS0468-{100 + idx}",
                "find": f"{100 + idx}.jp2",
                "type": "image",
            }
            for idx in range(1, 6)
        ],
    }
    (cache_dir / "hpl_arch_collections_cache.json").write_text(json.dumps(hpl_cache), encoding="utf-8")

    (output_dir / "curated_overrides.json").write_text(
        json.dumps(
            {
                "overrides": {
                    "0052320000001": {
                        "hcad_num": "0052320000001",
                        "address": "815 HOUSTON AVE",
                        "building_name": "Knapp Showroom Building",
                        "year_built": 1940,
                        "suppress_shard_hcads": ["1339430010001"],
                    },
                    "osm_city_54169596#bldg_tower": {
                        "address": "700 LOUISIANA ST",
                        "building_name": "Downtown Atrium Tower",
                        "year_built": 1983,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "buildings.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "id": "0990010000001",
                            "hcad_num": "0990010000001",
                            "address": "100 OVERLAY TEST ST",
                            "year_built": 2014,
                        },
                        "geometry": {"type": "Point", "coordinates": [-95.37, 29.76]},
                    },
                    {
                        "type": "Feature",
                        "properties": {
                            "id": "1339430010001",
                            "hcad_num": "1339430010001",
                            "address": "815 HOUSTON AVE",
                            "year_built": 2014,
                        },
                        "geometry": {"type": "Point", "coordinates": [-95.37, 29.76]},
                    },
                    {
                        "type": "Feature",
                        "properties": {
                            "id": "bld_700_louisiana",
                            "hcad_num": "",
                            "address": "700 LOUISIANA ST",
                            "year_built": 1983,
                        },
                        "geometry": {"type": "Point", "coordinates": [-95.36, 29.76]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "overlays.json").write_text(
        json.dumps(
            {
                "landmarks": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {
                                "hcad_num": "0990010000001",
                                "address": "100 OVERLAY TEST ST",
                                "landmark_name": "Undated Overlay Entry On Rebuilt Lot",
                                "year_built": 0,
                            },
                            "geometry": {"type": "Point", "coordinates": [-95.37, 29.76]},
                        }
                    ],
                },
                "good_brick_awards": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {
                                "building_id": "1339430010001",
                                "hcad_num": "0052320000001",
                                "address": "815 HOUSTON AVE",
                                "landmark_name": "Knapp Showroom Building",
                                "year_built": 1940,
                            },
                            "geometry": {"type": "Point", "coordinates": [-95.37, 29.76]},
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "search_index.json").write_text("[]", encoding="utf-8")
    (output_dir / "building_photos.json").write_text('{"photos":[]}', encoding="utf-8")

    stats = run_archive_harvest(
        cache_dir=cache_dir,
        output_dir=output_dir,
        apply=True,
        offline=True,
        generated_at="2026-11-08",
    )
    assert stats["apply"] is True
    arch = json.loads((output_dir / "archival_index.json").read_text(encoding="utf-8"))
    assert arch["generated_at"] == "2026-11-08"

    # yr=0 overlay inherited year_built=2014 -> undated pre-1981 survey photo rejected!
    assert "0990010000001" not in arch["by_key"]
    # Suppressed shard 1339430010001 redirected to active override 0052320000001!
    assert "1339430010001" not in arch["by_key"]
    assert "0052320000001" in arch["by_key"]

    photos = json.loads((output_dir / "building_photos.json").read_text(encoding="utf-8"))["photos"]
    tower_photos = [p for p in photos if p["address"] == "700 LOUISIANA ST"]
    # Per-parcel cap <= 3 even across osm_city_54169596#bldg_tower + bld_700_louisiana!
    assert len(tower_photos) == 3
    for tp in tower_photos:
        assert tp["hcad_num"] == ""
        assert len(tp["caption"]) <= 300
        assert tp["caption"].endswith(" (Architect: Philip Johnson).")
