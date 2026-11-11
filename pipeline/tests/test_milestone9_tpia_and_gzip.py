"""Milestone 9 unit, behavioral, and integration tests for RFC 1952 .json.gz shards and TPIA ingestion."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile

import pytest

from atlas_pipeline import (
    cli,
    fetch_hahc_tracker,
    fetch_live_permits,
    fetch_plat_and_watchlist,
    ingest_tpia_clerk_and_hcad,
)
from atlas_pipeline.build_ownership_and_history import (
    enrich_permit_shards_with_ownership_and_deeds,
)
from atlas_pipeline.ingest_tpia_clerk_and_hcad import (
    classify_clerk_instrument_type,
    extract_hcad_legal_crosswalk_keys,
    ingest_hcad_tpia_tables,
    normalize_clerk_tpia_instrument,
    run_tpia_ingestion,
)
from atlas_pipeline.schema import (
    ENRICHED_PERMIT_SHARD_KEYS,
    TPIA_PERMIT_SHARD_KEYS,
    compress_deterministic_gzip_json,
    iter_json_or_gzip_shard_keys,
    read_json_or_gzip_path,
    read_json_or_gzip_shard,
    sanitize_json_structure_dashes,
    sum_json_or_gzip_shard_bytes,
    update_pmtiles_manifest_v3,
    write_compact_json,
    write_deterministic_gzip_json_shard,
)


def _snapshot_tree_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


def _assert_rfc1952_deterministic_header(gz_bytes: bytes) -> None:
    assert len(gz_bytes) >= 10
    assert gz_bytes[0] == 0x1F
    assert gz_bytes[1] == 0x8B
    assert gz_bytes[2] == 0x08
    # Bit 3 (0x08) is FNAME; deterministic shards must never embed OS filenames or non-zero mtime
    assert (gz_bytes[3] & 0x08) == 0
    mtime = struct.unpack("<I", gz_bytes[4:8])[0]
    assert mtime == 0


def test_schema_deterministic_gzip_helpers_and_tpia_key_contracts(tmp_path: Path) -> None:
    assert ENRICHED_PERMIT_SHARD_KEYS == ("d", "ot", "pid", "m", "co", "flip")
    assert TPIA_PERMIT_SHARD_KEYS == ("ml", "dt_lender")

    payload = {
        "0620820000016": {"yr": 1926, "d": "19940615:P812345::D:JONES MARY:SMITH ROBERT"},
        "0010010000001": {"yr": 1912},
    }
    gz1 = compress_deterministic_gzip_json(payload)
    gz2 = compress_deterministic_gzip_json(dict(reversed(list(payload.items()))))
    assert gz1 == gz2
    _assert_rfc1952_deterministic_header(gz1)
    decompressed = gzip.decompress(gz1).decode("utf-8")
    assert "\n" not in decompressed
    assert json.loads(decompressed) == payload

    # Forbidden Unicode dash guard in compress_deterministic_gzip_json + recursive sanitizer
    with pytest.raises(ValueError, match="Forbidden"):
        compress_deterministic_gzip_json({"001": {"note": "bad\u2014dash"}})
    with pytest.raises(ValueError, match="Forbidden"):
        compress_deterministic_gzip_json({"001": {"note": "bad\u2013dash"}})

    dirty_struct = {
        "KEY\u2014ONE": [
            "Alpha\u2013Beta",
            ("Tuple\u2014Item", {"nested": "1920\u20131930"}),
        ]
    }
    cleaned_struct = sanitize_json_structure_dashes(dirty_struct)
    cleaned_json = json.dumps(cleaned_struct)
    assert "\u2014" not in cleaned_json
    assert "\u2013" not in cleaned_json
    assert compress_deterministic_gzip_json(cleaned_struct)

    # Atomic shard write + legacy .json unlink + transparent fallback
    sdir = tmp_path / "permits"
    sdir.mkdir(parents=True)
    legacy_062 = sdir / "permits_062.json"
    legacy_062.write_text('{"legacy":1}', encoding="utf-8")
    written_bytes = write_deterministic_gzip_json_shard(
        sdir / "permits_062.json",
        dirty_struct,
        unlink_legacy_json=True,
        sanitize_dashes=True,
    )
    assert written_bytes > 10
    assert not legacy_062.exists()
    assert (sdir / "permits_062.json.gz").exists()
    _assert_rfc1952_deterministic_header((sdir / "permits_062.json.gz").read_bytes())
    assert read_json_or_gzip_shard(sdir, "permits_062") == cleaned_struct
    assert read_json_or_gzip_shard(sdir, "permits_062.json") == cleaned_struct
    assert read_json_or_gzip_shard(sdir, "permits_062.json.gz") == cleaned_struct

    # Plain .json fallback when .json.gz does not exist yet
    legacy_077 = sdir / "permits_077.json"
    legacy_077.write_text('{"0770010000001":{"yr":1940}}', encoding="utf-8")
    assert read_json_or_gzip_shard(sdir, "permits_077") == {"0770010000001": {"yr": 1940}}
    assert read_json_or_gzip_shard(sdir, "permits_999") == {}
    assert read_json_or_gzip_path(sdir / "does_not_exist.json.gz") == {}

    bad_arr_path = tmp_path / "array.json"
    bad_arr_path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="Expected JSON object"):
        read_json_or_gzip_path(bad_arr_path)

    # When both .json and .json.gz exist, iter_json_or_gzip_shard_keys prefers .json.gz
    (sdir / "permits_062.json").write_text('{"stale":1}', encoding="utf-8")
    discovered = iter_json_or_gzip_shard_keys(sdir, "permits_", r"\d{3}")
    assert discovered == [
        ("062", sdir / "permits_062.json.gz"),
        ("077", sdir / "permits_077.json"),
    ]
    expected_bytes = sum(p.stat().st_size for p in sdir.iterdir() if p.is_file())
    assert sum_json_or_gzip_shard_bytes(sdir, "permits_", r"\d{3}") == expected_bytes

    # Manifest v3 prefix arrays + span
    mpath = tmp_path / "pmtiles_manifest.json"
    manifest = update_pmtiles_manifest_v3(
        mpath,
        total_buildings=1500,
        building_shards=["houston_buildings_nw_e.pmtiles"],
        data_tax_year=2026,
        cache_bust="20261106b",
        owner_history_prefixes=["062", "001"],
        owner_history_span=[1988, 2026],
        owner_history_accounts=42,
        permit_shard_prefixes=["077", "062"],
        address_shard_prefixes=["76", "11"],
    )
    assert manifest["permit_shard_prefixes"] == ["062", "077"]
    assert manifest["address_shard_prefixes"] == ["11", "76"]
    assert manifest["owner_history_span"] == [1988, 2026]


def test_standalone_cron_scripts_gzip_shard_roundtrips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_repo = tmp_path / "repo"
    fake_data = fake_repo / "app" / "public" / "data"
    fake_permits = fake_data / "permits"
    fake_addr = fake_data / "address_shards"
    fake_permits.mkdir(parents=True)
    fake_addr.mkdir(parents=True)

    # 1. fetch_hahc_tracker.ParcelGeocoder reads addr_<2-digit>.json.gz
    write_deterministic_gzip_json_shard(
        fake_addr / "addr_11.json.gz",
        {
            "WALLING ST": [
                ["1119", -95.387, 29.80082, 1926, "0620820000016", "1119 Walling St"],
            ]
        },
    )
    monkeypatch.setattr(fetch_hahc_tracker, "DATA_DIR", fake_data)
    geocoder = fetch_hahc_tracker.ParcelGeocoder(overlays={}, search_index=[])
    resolved = geocoder.resolve("1119 Walling St", district_hint="Norhill")
    assert resolved is not None
    assert resolved["hcad_num"] == "0620820000016"
    assert resolved["year_built"] == 1926
    assert abs(resolved["lng"] - (-95.387)) < 1e-5

    # 2. fetch_live_permits.main() reads .json.gz / legacy .json, writes deterministic .json.gz, unlinks .json
    bldg_geojson = fake_data / "buildings.geojson"
    write_compact_json(
        bldg_geojson,
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [-95.3871, 29.8007],
                                [-95.3869, 29.8007],
                                [-95.3869, 29.8009],
                                [-95.3871, 29.8009],
                                [-95.3871, 29.8007],
                            ]
                        ],
                    },
                    "properties": {
                        "id": "0620820000016",
                        "hcad_num": "0620820000016",
                        "address": "1119 WALLING ST",
                        "year_built": 1926,
                        "historic_district": "Norhill",
                    },
                }
            ],
        },
    )
    search_idx_json = fake_data / "search_index.json"
    write_compact_json(search_idx_json, [])
    alerts_json = fake_data / "active_permit_alerts.json"

    # Seed legacy uncompressed permits_062.json with TPIA fields to ensure live permit merge upgrades to .json.gz cleanly
    legacy_p062 = fake_permits / "permits_062.json"
    legacy_p062.write_text(
        json.dumps(
            {
                "0620820000016": {
                    "yr": 1926,
                    "d": "19940615:P812345::D:JONES MARY:SMITH ROBERT",
                    "ml": "19260512~VP:642/118~RUSSELL BROWN CO~JONES MARY~14500",
                    "p": [],
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(fetch_live_permits, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(fetch_live_permits, "BUILDINGS_GEOJSON", bldg_geojson)
    monkeypatch.setattr(fetch_live_permits, "SEARCH_INDEX_JSON", search_idx_json)
    monkeypatch.setattr(fetch_live_permits, "PERMITS_DIR", fake_permits)
    monkeypatch.setattr(fetch_live_permits, "ALERTS_OUTPUT_JSON", alerts_json)
    monkeypatch.setattr(fetch_live_permits, "SCORECARDS_JSON", fake_data / "neighborhood_scorecards.json")
    monkeypatch.setattr(
        fetch_live_permits,
        "fetch_coh_permits_parallel",
        lambda lookback_days=45, *, return_status=False: (
            {
                "26099111": {
                    "project_no": "26099111",
                    "date": "2026-10-20",
                    "permit_type": "SD",
                    "permit_desc": "DEMO RES\u2014MAIN",
                    "use_desc": "DEMO SINGLE FAMILY\u2013HOUSE",
                    "raw_address": "1119 WALLING ST",
                    "norm_address": fetch_live_permits.normalize_address("1119 WALLING ST"),
                    "owner_occupant": "SMITH ROBERT",
                    "valuation": "$12,000",
                }
            },
            False,
            "",
        ),
    )
    monkeypatch.setattr(
        fetch_live_permits,
        "fetch_coh_permit_detail",
        lambda project_no, permit_type="": {
            "date": "2026-10-20",
            "permit_type": "SD",
            "use_desc": "DEMO SINGLE FAMILY\u2014HOUSE",
            "fcc_group": "Demo",
            "buyer": "ACME WRECKING LLC",
            "job_address": "1119 WALLING ST",
        },
    )
    monkeypatch.setattr(
        fetch_live_permits,
        "fetch_recent_weekly_xlsx_permits",
        lambda max_files=4, *, return_status=False: ([], False, ""),
    )
    monkeypatch.setattr(
        fetch_live_permits,
        "fetch_dshs_houston_notifications",
        lambda s, e, b, *, return_status=False: ([], False, ""),
    )
    monkeypatch.setattr(
        fetch_live_permits,
        "build_annual_demolition_benchmarks",
        lambda existing=None: {"updated_through": "2026-10-20"},
    )
    monkeypatch.setattr(sys, "argv", ["fetch_live_permits.py", "--lookback-days", "7", "--weekly-xlsx-count", "0"])

    fetch_live_permits.main()

    gz_p062 = fake_permits / "permits_062.json.gz"
    assert gz_p062.exists()
    assert not legacy_p062.exists()
    raw_gz_062 = gz_p062.read_bytes()
    _assert_rfc1952_deterministic_header(raw_gz_062)
    unzipped_txt = gzip.decompress(raw_gz_062).decode("utf-8")
    assert "\u2014" not in unzipped_txt
    assert "\u2013" not in unzipped_txt
    merged_062 = json.loads(unzipped_txt)
    assert merged_062["0620820000016"]["ml"].startswith("19260512~VP:642/118")
    assert merged_062["0620820000016"]["p"][0]["id"] == "26099111"

    # 3. fetch_plat_and_watchlist load_atlas_indices & sweep_watched_property_agencies read .json.gz shards
    write_deterministic_gzip_json_shard(
        fake_permits / "permits_062.json.gz",
        {
            **merged_062,
            "0620820000016": {
                **merged_062["0620820000016"],
                "permits": [
                    {
                        "project_no": "26099111",
                        "date": "2026-10-20",
                        "permit_type": "SD",
                        "permit_desc": "BUILDING DEMO",
                        "use_desc": "DEMO SINGLE FAMILY",
                        "job_address": "1119 WALLING ST",
                    }
                ],
            },
        },
    )
    monkeypatch.setattr(fetch_plat_and_watchlist, "DATA_DIR", fake_data)

    def _stub_fetch_json(url: str, **_kwargs: object) -> tuple[object, str, str]:
        if "MapServer" in url or "FeatureServer" in url:
            return {"features": []}, "ok", ""
        return [], "ok", ""

    monkeypatch.setattr(fetch_plat_and_watchlist, "_fetch_json_with_status", _stub_fetch_json)
    atlas_idx = fetch_plat_and_watchlist.load_atlas_indices()
    assert "0620820000016" in atlas_idx["hcad_map"]
    assert ("1119", "WALLING ST") in atlas_idx["addr_exact"]

    sweep_res = fetch_plat_and_watchlist.sweep_watched_property_agencies(
        prop={
            "id": "wp-walling",
            "title": "1119 Walling St",
            "address": "1119 WALLING ST",
            "hcadAccount": "0620820000016",
            "entities": ["SMITH ROBERT"],
        },
        plat_data={"items": []},
        idx=atlas_idx,
        sweep_date_iso="2026-10-25",
    )
    assert "26099111" in sweep_res["harvestedProjectNumbers"]
    assert sweep_res["agencyHealth"]["chk-14"]["status"] == "ok"


def test_enrich_permit_shards_confidential_scrubs_tpia_keys_without_attribute_error(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    data_dir = app_dir / "public" / "data"
    cache_dir = tmp_path / "cache"
    permits_dir = data_dir / "permits"
    permits_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)

    write_compact_json(
        data_dir / "buildings.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-95.38, 29.80]},
                    "properties": {"id": "0071000000001", "hcad_num": "0071000000001", "year_built": 1925},
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-95.38, 29.80]},
                    "properties": {"id": "0072000000002", "hcad_num": "0072000000002", "year_built": 1928},
                },
            ],
        },
    )

    write_deterministic_gzip_json_shard(
        permits_dir / "permits_007.json.gz",
        {
            "0071000000001": {
                "yr": 1925,
                "ml": "19250401~VP:500/12~ACME BUILDERS~DOE JOHN~9500",
                "dt_lender": "20180601~R2018-44556~TEXAS BANK",
            },
            "0072000000002": {
                "yr": 1928,
                "d": "20150101:20150001111::D:A:B",
                "ml": "19280301~VP:610/88~BUILDER CO~SECRET OWNER~12000",
                "dt_lender": "20150101~20150001112~LENDER NA",
            },
            "0073000000003": {
                "yr": 1930,
                "conf": 1,
                "ml": "19300101~VP:700/10~STALE BUILDER~STALE~8000",
                "dt_lender": "20100101~20100002222~STALE BANK",
            },
        },
    )
    # Seed 4 additional shards (>4 total) so ThreadPoolExecutor multi-shard compression path also runs
    for extra_pfx in ("008", "009", "010", "011"):
        write_deterministic_gzip_json_shard(
            permits_dir / f"permits_{extra_pfx}.json.gz",
            {f"{extra_pfx}1000000001": {"yr": 1940}},
        )

    acct_zip = cache_dir / "Real_acct_owner.zip"
    with zipfile.ZipFile(acct_zip, "w") as zf:
        zf.writestr(
            "real_acct.txt",
            "\n".join(
                [
                    "acct\tyr\tmailto\tsite_addr_1\tsite_addr_2\tsite_addr_3\tstate_class\tstr_num\tstr\tstr_sfx\tlgl_1\tlgl_2\tmail_addr_1\tmail_city\tmail_state\tmail_zip",
                    "0071000000001\t2026\tHEIGHTS HOLDINGS LLC\t101 MAIN ST\tHOUSTON\t77002\tA1\t101\tMAIN\tST\tLT 1 BLK 1\tHEIGHTS\t500 CORP WAY\tHOUSTON\tTX\t77002",
                    "0072000000002\t2026\tCONFIDENTIAL OWNER\t103 MAIN ST\tHOUSTON\t77002\tA1\t103\tMAIN\tST\tLT 2 BLK 1\tHEIGHTS\t103 MAIN ST\tHOUSTON\tTX\t77002",
                ]
            )
            + "\n",
        )
        zf.writestr(
            "owners.txt",
            "\n".join(
                [
                    "acct\tln_num\tname\taka",
                    "0071000000001\t1\tHEIGHTS HOLDINGS LLC\t",
                    "0072000000002\t1\tCONFIDENTIAL OWNER\t",
                ]
            )
            + "\n",
        )
        zf.writestr(
            "deeds.txt",
            "\n".join(
                [
                    "acct\tdos\tclerk_yr\tclerk_id\tdeed_id",
                    "0071000000001\t06/01/2018\t2018\tRP-2018-44555\t1",
                    "0072000000002\t01/01/2015\t2015\t20150001111\t1",
                ]
            )
            + "\n",
        )
        zf.writestr("permits.txt", "acct\tid\tdscr\tpermit_type\tpermit_tp_descr\tissue_date\n")

    stats = enrich_permit_shards_with_ownership_and_deeds(
        real_acct_zip=acct_zip,
        permits_dir=permits_dir,
        data_dir=data_dir,
        real_bldg_zip=cache_dir / "Real_building_land.zip",
        app_dir=app_dir,
        tax_year=2026,
    )
    assert stats["enriched_existing"] >= 2

    enriched_p007 = read_json_or_gzip_path(permits_dir / "permits_007.json.gz")
    # Public LLC preserves TPIA ml & dt_lender alongside refreshed ownership + deeds
    assert enriched_p007["0071000000001"]["ot"] == "local_small"
    assert enriched_p007["0071000000001"]["d"] == "20180601:R2018-44555"
    assert enriched_p007["0071000000001"]["ml"] == "19250401~VP:500/12~ACME BUILDERS~DOE JOHN~9500"
    assert enriched_p007["0071000000001"]["dt_lender"] == "20180601~R2018-44556~TEXAS BANK"

    # Confidential account in Real_acct_owner.zip strips d, ml, and dt_lender without AttributeError
    assert enriched_p007["0072000000002"] == {"yr": 1928, "conf": 1}
    # Fail-closed orphaned confidential account also strips stale ml and dt_lender
    assert enriched_p007["0073000000003"] == {"yr": 1930, "conf": 1}


def test_tpia_hcad_pre2005_and_clerk_multi_format_crosswalk_dry_run_vs_apply(
    tmp_path: Path,
) -> None:
    # Unit checks on instrument normalization, type classification, and legal key extraction
    assert normalize_clerk_tpia_instrument(file_no="RP-2021-004567") == "R2021-4567"
    assert normalize_clerk_tpia_instrument(file_no="P202281", rec_year="1994") == "P202281"
    assert normalize_clerk_tpia_instrument(film_code="123 45 6789") == "FC:123-45-6789"
    assert normalize_clerk_tpia_instrument(vol="00642", page="0118") == "VP:642/118"
    assert classify_clerk_instrument_type("RELEASE OF LIEN") == ("", "D")
    assert classify_clerk_instrument_type("REL") == ("", "D")
    assert classify_clerk_instrument_type("MECHANIC'S LIEN CONTRACT") == ("MECHANICS_LIEN", "M")
    assert classify_clerk_instrument_type("DEED OF TRUST") == ("DEED_OF_TRUST", "T")
    assert classify_clerk_instrument_type("PROBATE") == ("CONVEYANCE", "O")
    assert len(extract_hcad_legal_crosswalk_keys(["LT 14 & 15 BLK 12", "NORTH NORHILL SEC 2 R/P"])) == 2

    app_dir = tmp_path / "app"
    data_dir = app_dir / "public" / "data"
    permits_dir = data_dir / "permits"
    oh_dir = data_dir / "owner_history"
    cache_dir = tmp_path / "cache"
    permits_dir.mkdir(parents=True)
    oh_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)

    # Seed baseline 2005-2026 Atlas shards + manifest
    write_deterministic_gzip_json_shard(
        permits_dir / "permits_062.json.gz",
        {
            "0620820000016": {
                "yr": 1926,
                "d": "20190415:R2019-149789",
            },
            "0620820000017": {"yr": 2004},
            "0620820000018": {"yr": 1998},
            "0620990000001": {"yr": 2016},
            "0620990000002": {"yr": 2016},
            "0620990000003": {"yr": 2016},
            "0620990000004": {"yr": 2016},
            "0620990000005": {"yr": 2016},
            "0620770000001": {
                "yr": 1930,
                "d": "20100501:20100111222",
                "ml": "19300501~VP:800/10~OLD BUILDER~OLD OWNER~4000",
            },
        },
    )
    write_deterministic_gzip_json_shard(
        oh_dir / "owners_062.json.gz",
        {
            "0620820000016": "2019-2026~20190415~R2019-149789~MORRIS DAVID|2005-2018~19940615~~SMITH ROBERT",
            "0620770000001": "2005-2026~20100501~20100111222~PUBLIC PRIOR NAME",
        },
    )
    update_pmtiles_manifest_v3(
        data_dir / "pmtiles_manifest.json",
        total_buildings=9,
        building_shards=["houston_buildings_nw_e.pmtiles"],
        data_tax_year=2026,
        cache_bust="20261106b",
        owner_history_prefixes=["062"],
        owner_history_span=[2005, 2026],
        owner_history_accounts=2,
        permit_shard_prefixes=["062"],
        address_shard_prefixes=["11"],
    )

    # Build Real_acct_owner.zip with LOT, UNIT, RES, >4 ambiguous townhome lots, and confidential aka
    acct_zip = cache_dir / "Real_acct_owner.zip"
    with zipfile.ZipFile(acct_zip, "w") as zf:
        zf.writestr(
            "real_acct.txt",
            "\n".join(
                [
                    "acct\tmailto\tlgl_1\tlgl_2\tlgl_3\tlgl_4",
                    "0620820000016\tMORRIS DAVID\tLT 14 & 15 BLK 12\tNORTH NORHILL SEC 2 R/P\t\t",
                    "0620820000017\tLOFT BUYER LLC\tUNIT 0204 BLDG B\tMONTROSE LOFTS CONDO\t\t",
                    "0620820000018\tBAYOU HOLDINGS LP\tRES A BLK 3\tBAYOU COMMERCIAL PARK\t\t",
                    "0620990000001\tOWNER 1\tLT 1 BLK 99\tOVERFLOW VILLAS\t\t",
                    "0620990000002\tOWNER 2\tLT 1 BLK 99\tOVERFLOW VILLAS\t\t",
                    "0620990000003\tOWNER 3\tLT 1 BLK 99\tOVERFLOW VILLAS\t\t",
                    "0620990000004\tOWNER 4\tLT 1 BLK 99\tOVERFLOW VILLAS\t\t",
                    "0620990000005\tOWNER 5\tLT 1 BLK 99\tOVERFLOW VILLAS\t\t",
                    "0620770000001\tJANE CITIZEN\tLT 9 BLK 4\tNORTH NORHILL SEC 2\t\t",
                ]
            )
            + "\n",
        )
        zf.writestr(
            "owners.txt",
            "\n".join(
                [
                    "acct\tln_num\tname\taka",
                    "0620820000016\t1\tMORRIS DAVID\t",
                    # Confidential token appears ONLY in `aka` column -> exercises aka confidentiality guard!
                    "0620770000001\t1\tJANE CITIZEN\tPROTECTED OWNER",
                ]
            )
            + "\n",
        )

    # HCAD pre-2005 TPIA directory (.tsv + .csv.gz)
    hcad_tpia_dir = tmp_path / "hcad_tpia"
    hcad_tpia_dir.mkdir()
    snap_rows = ["acct\ttax_yr\towner_name\tnew_own_dt\tconf"]
    for yr in range(1988, 1994):
        snap_rows.append(f"0620820000016\t{yr}\tJONES MARY\t1988-01-02\t0")
    for yr in range(1994, 2005):
        snap_rows.append(f"0620820000016\t{yr}\tSMITH ROBERT\t1994-06-15\t0")
    (hcad_tpia_dir / "hcad_snapshots_1988_2004.tsv").write_text("\n".join(snap_rows) + "\n", encoding="utf-8")

    deed_csv = (
        "acct,dos,clerk_yr,file_no,grantor,grantee\n"
        "0620820000016,06/15/1994,1994,P812345,JONES MARY,SMITH ROBERT\n"
    )
    (hcad_tpia_dir / "hcad_legacy_sales.csv.gz").write_bytes(gzip.compress(deed_csv.encode("utf-8")))

    # County Clerk TPIA directory: Option A relational (.psv + cp1252 .tsv + .csv.gz) + Option B flat .zip
    clerk_tpia_dir = tmp_path / "clerk_tpia"
    clerk_tpia_dir.mkdir()

    # Relational Header (.txt pipe-delimited)
    (clerk_tpia_dir / "clerk_headers.txt").write_text(
        "\n".join(
            [
                "doc_id|file_no|film_code|vol|pg|file_date|inst_type|amount",
                "DOC1|RP-2019-149789||||2019-04-15|W/D|",
                "DOC2||123-45-6789|||1978-03-10|PROBATE|",
                "DOC3|||0642|0118|1926-05-12|M/L|$14,500.00",
                "DOC4|RP-2020-999999||||2020-01-10|RELEASE OF LIEN|",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # Relational Parties (.tsv encoded in cp1252 with 0x96 en-dash byte to verify fallback + dash scrubbing)
    parties_cp1252 = (
        b"doc_id\tparty_type\tparty_name\n"
        b"DOC1\tGRANTOR\tSMITH ROBERT\n"
        b"DOC1\tGRANTEE\tMORRIS DAVID\n"
        b"DOC2\tDIRECT\tDOE JANE\n"
        b"DOC2\tREVERSE\tJONES MARY\n"
        b"DOC3\tOWNER\tJONES MARY\n"
        b"DOC3\tCONTRACTOR\tRUSSELL\x96BROWN CO\n"
        b"DOC4\tDIRECT\tBANK NA\n"
    )
    (clerk_tpia_dir / "clerk_parties.tsv").write_bytes(parties_cp1252)

    # Relational Legal (.csv.gz; note DOC2 omits section to verify section-agnostic fallback!)
    legal_csv = (
        "doc_id,subdivision,section,block,lot\n"
        "DOC2,NORTH NORHILL ADDITION,,12,14\n"
        "DOC3,NORTH NORHILL,2,12,15\n"
    )
    (clerk_tpia_dir / "clerk_legal.csv.gz").write_bytes(gzip.compress(legal_csv.encode("utf-8")))

    # Option B flat export inside .zip (covers D/T on UNIT, REPLAT on RES, >4-parcel overflow cap, and confidential suppression)
    with zipfile.ZipFile(clerk_tpia_dir / "clerk_flat_batch.zip", "w") as zf_c:
        zf_c.writestr(
            "flat_clerk.csv",
            "\n".join(
                [
                    "file_no,file_date,inst_type,grantor,grantee,subdivision,block,lot,unit,reserve,amount",
                    "RP-2019-149790,2019-04-15,D/T,LOFT BUYER LLC,FIRST NATIONAL BANK,MONTROSE LOFTS,,,204,,250000",
                    "20150333444,2015-08-01,REPLAT,BAYOU DEV LP,BAYOU HOLDINGS LP,BAYOU COMMERCIAL PARK,3,,,A,",
                    "RP-2022-777777,2022-03-01,W/D,BUILDER LLC,BUYER,OVERFLOW VILLAS,99,1,,,",
                    "RP-2023-888888,2023-06-01,W/D,SELLER ONE,JANE CITIZEN,NORTH NORHILL,4,9,,,",
                    "RP-2024-111111,2024-02-01,W/D,CONFIDENTIAL OWNER,NEW BUYER,NORTH NORHILL,12,14,,,",
                ]
            )
            + "\n",
        )

    # 1. --dry-run via CLI must leave every byte on disk untouched
    before_hashes = _snapshot_tree_hashes(app_dir)
    cli.main(
        [
            "--mode",
            "ingest-tpia",
            "--dry-run",
            "--output-dir",
            str(data_dir),
            "--cache-dir",
            str(cache_dir),
            "--clerk-tpia-dir",
            str(clerk_tpia_dir),
            "--hcad-tpia-dir",
            str(hcad_tpia_dir),
            "--tax-year",
            "2026",
        ]
    )
    assert _snapshot_tree_hashes(app_dir) == before_hashes

    # 2. --apply via CLI promotes deterministic .json.gz shards and updates manifest
    cli.main(
        [
            "--mode",
            "ingest-tpia",
            "--apply",
            "--output-dir",
            str(data_dir),
            "--cache-dir",
            str(cache_dir),
            "--clerk-tpia-dir",
            str(clerk_tpia_dir),
            "--hcad-tpia-dir",
            str(hcad_tpia_dir),
            "--tax-year",
            "2026",
        ]
    )
    after_apply_hashes = _snapshot_tree_hashes(app_dir)
    assert after_apply_hashes != before_hashes

    # Verify RFC 1952 headers + zero dashes
    p062_bytes = (permits_dir / "permits_062.json.gz").read_bytes()
    oh062_bytes = (oh_dir / "owners_062.json.gz").read_bytes()
    _assert_rfc1952_deterministic_header(p062_bytes)
    _assert_rfc1952_deterministic_header(oh062_bytes)
    for raw_b in (p062_bytes, oh062_bytes):
        txt = gzip.decompress(raw_b).decode("utf-8")
        assert "\u2014" not in txt
        assert "\u2013" not in txt

    manifest = json.loads((data_dir / "pmtiles_manifest.json").read_text(encoding="utf-8"))
    assert manifest["owner_history_span"] == [1988, 2026]
    assert manifest["owner_history_accounts"] == 1

    oh062 = read_json_or_gzip_path(oh_dir / "owners_062.json.gz")
    assert "0620770000001" not in oh062
    assert (
        oh062["0620820000016"]
        == "2019-2026~20190415~R2019-149789~MORRIS DAVID|1994-2018~19940615~P812345~SMITH ROBERT|1988-1993~<=1988~~JONES MARY"
    )

    p062 = read_json_or_gzip_path(permits_dir / "permits_062.json.gz")
    rec_16 = p062["0620820000016"]
    assert "20190415:R2019-149789::D:SMITH ROBERT:MORRIS DAVID" in rec_16["d"]
    assert "19940615:P812345::D:JONES MARY:SMITH ROBERT" in rec_16["d"]
    assert "19780310:FC:123-45-6789::O:DOE JANE:JONES MARY" in rec_16["d"]
    #Confidential party filing RP-2024-111111 must NEVER attach to 0620820000016
    assert "111111" not in rec_16["d"]
    assert rec_16["ml"] == "19260512~VP:642/118~RUSSELL-BROWN CO~JONES MARY~14500"

    # UNIT D/T lender + RES replat conveyance
    assert p062["0620820000017"]["dt_lender"] == "20190415~R2019-149790~FIRST NATIONAL BANK"
    assert p062["0620820000018"]["d"] == "20150801:20150333444::S:BAYOU DEV LP:BAYOU HOLDINGS LP"

    # Ambiguous >4-parcel legal key rejected cleanly across all 5 overflow parcels
    for k in range(1, 6):
        assert p062[f"062099000000{k}"] == {"yr": 2016}

    # Confidential aka parcel scrubbed fail-closed
    assert p062["0620770000001"] == {"yr": 1930, "conf": 1}

    # 3. Re-running --apply is byte-for-byte idempotent
    second_stats = run_tpia_ingestion(
        output_dir=data_dir,
        cache_dir=cache_dir,
        app_dir=app_dir,
        clerk_tpia_dir=clerk_tpia_dir,
        hcad_tpia_dir=hcad_tpia_dir,
        apply=True,
        active_tax_year=2026,
    )
    assert second_stats["updated_owner_history_accounts"] == 0
    assert second_stats["updated_permit_accounts"] == 0
    assert _snapshot_tree_hashes(app_dir) == after_apply_hashes


def test_tpia_bundle_size_ceiling_guards_and_active_tax_year_propagation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Verify active_tax_year propagation in ingest_hcad_tpia_tables (regression for hardcoded ACTIVE_TAX_YEAR)
    hcad_dir = tmp_path / "hcad_2025"
    hcad_dir.mkdir()
    (hcad_dir / "snaps.csv").write_text(
        "acct,tax_yr,owner_name,conf\n0620550000001,2025,PUBLIC NAME,1\n",
        encoding="utf-8",
    )
    res_2025 = ingest_hcad_tpia_tables(hcad_dir, max_year=2026, active_tax_year=2025)
    assert "0620550000001" in res_2025["confidential_hcads"]

    # 2. Verify HARD_MAX_APP_BYTES pre-write abort leaves disk untouched
    app_dir = tmp_path / "app"
    data_dir = app_dir / "public" / "data"
    permits_dir = data_dir / "permits"
    permits_dir.mkdir(parents=True)
    write_deterministic_gzip_json_shard(
        permits_dir / "permits_062.json.gz",
        {"0620820000016": {"yr": 1926}},
    )
    clerk_dir = tmp_path / "clerk"
    clerk_dir.mkdir()
    (clerk_dir / "flat.csv").write_text(
        "acct,file_no,file_date,inst_type,grantor,grantee\n"
        "0620820000016,RP-2020-12345,2020-05-01,W/D,SELLER A,BUYER B\n",
        encoding="utf-8",
    )
    before = _snapshot_tree_hashes(app_dir)
    monkeypatch.setattr(ingest_tpia_clerk_and_hcad, "HARD_MAX_APP_BYTES", 10)
    with pytest.raises(RuntimeError, match="HARD_MAX_APP_BYTES"):
        run_tpia_ingestion(
            output_dir=data_dir,
            cache_dir=tmp_path / "cache",
            app_dir=app_dir,
            clerk_tpia_dir=clerk_dir,
            apply=True,
        )
    assert _snapshot_tree_hashes(app_dir) == before

    # 3. Missing both --clerk-tpia-dir and --hcad-tpia-dir raises clear ValueError
    with pytest.raises(ValueError, match="Provide at least one existing TPIA directory"):
        run_tpia_ingestion(
            output_dir=data_dir,
            cache_dir=tmp_path / "cache",
            app_dir=app_dir,
            clerk_tpia_dir=tmp_path / "nonexistent_clerk",
            hcad_tpia_dir=tmp_path / "nonexistent_hcad",
            apply=False,
        )


def test_tpia_review_regressions_conf_history_option_a_aliases_and_bare_python3_cron(
    tmp_path: Path,
) -> None:
    import subprocess

    app_dir = tmp_path / "app"
    data_dir = app_dir / "public" / "data"
    permits_dir = data_dir / "permits"
    oh_dir = data_dir / "owner_history"
    cache_dir = tmp_path / "cache"
    permits_dir.mkdir(parents=True)
    oh_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)

    # 0620820000016: currently non-confidential buyer (2015-2026), had historical confidential owner in 2003
    # 0620820000019: active snapshot has aka='CONFIDENTIAL' while mailto='PUBLIC DISPLAY'
    # 0620820000020: standard public parcel for Option A REL non-promotion + Bound Deed Book vol/page gate
    write_deterministic_gzip_json_shard(
        permits_dir / "permits_062.json.gz",
        {
            "0620820000016": {
                "yr": 1926,
                "d": "20150601:R2015-20001",
                "ml": "19990401~VP:900/12~OLD CONTRACTOR~PRIOR OFFICER~8000",
                "dt_lender": "19990401~T999888~CREDIT UNION",
            },
            "0620820000019": {"yr": 1935, "d": "20200101:R2020-11111"},
            "0620820000020": {"yr": 1922},
        },
    )
    write_deterministic_gzip_json_shard(
        oh_dir / "owners_062.json.gz",
        {
            "0620820000016": "2015-2026~20150601~R2015-20001~PUBLIC BUYER",
            "0620820000019": "2020-2026~20200101~R2020-11111~PUBLIC DISPLAY",
        },
    )

    hcad_dir = tmp_path / "hcad_reg"
    hcad_dir.mkdir()
    (hcad_dir / "snapshots.csv").write_text(
        "\n".join(
            [
                "acct,yr,mailto,aka,new_own_dt,conf",
                "0620820000016,2003,PRIOR OFFICER,,1998-05-01,1",
                "0620820000019,2026,PUBLIC DISPLAY,CONFIDENTIAL,2020-01-01,0",
                # Non-target retired/vacant account absent from target_hcads must NOT enter static shards
                "0620820000099,1999,VACANT LOT OWNER,,1995-01-01,0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    clerk_dir = tmp_path / "clerk_reg"
    clerk_dir.mkdir()
    # Option A Header uses doc_id='1001' + file_no='RP-2020-004567' + inst_type='REL',
    # while Party & Legal rows join ONLY via unpadded file_no='RP-2020-4567' (no doc_id!).
    (clerk_dir / "header.txt").write_text(
        "\n".join(
            [
                "doc_id|file_no|file_date|inst_type",
                "1001|RP-2020-004567|2020-05-14|REL",
                "1002|RP-2015-020001|2015-06-01|W/D",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (clerk_dir / "party.txt").write_text(
        "\n".join(
            [
                "file_no|party_type|party_name",
                "RP-2020-4567|DIRECT|FIRST BANK NA",
                "RP-2020-4567|REVERSE|PUBLIC OWNER",
                "RP-2015-20001|GRANTOR|PRIOR OFFICER",
                "RP-2015-20001|GRANTEE|PUBLIC BUYER",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (clerk_dir / "legal.txt").write_text(
        "\n".join(
            [
                "file_no|acct",
                "RP-2020-4567|0620820000020",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    # Standalone pre-1961 Bound Deed Book table using ONLY vol + page columns (exercises header gate)
    (clerk_dir / "deed_books_1922.csv").write_text(
        "acct,vol,page,file_date,inst_type,grantor,grantee\n"
        "0620820000020,0512,0044,1922-09-10,W/D,HEIGHTS LAND CO,ORIGINAL BUYER\n",
        encoding="utf-8",
    )

    # Exercise --hcad-dir and --clerk-dir CLI aliases + default schema.ACTIVE_TAX_YEAR (no --tax-year passed)
    cli.main(
        [
            "--mode",
            "ingest-tpia",
            "--apply",
            "--output-dir",
            str(data_dir),
            "--cache-dir",
            str(cache_dir),
            "--hcad-dir",
            str(hcad_dir),
            "--clerk-dir",
            str(clerk_dir),
        ]
    )

    oh062 = read_json_or_gzip_path(oh_dir / "owners_062.json.gz")
    p062 = read_json_or_gzip_path(permits_dir / "permits_062.json.gz")

    # (a) aka='CONFIDENTIAL' scrubbed fail-closed; non-target 0620820000099 excluded
    assert "0620820000019" not in oh062
    assert p062["0620820000019"] == {"yr": 1935, "conf": 1}
    assert "0620820000099" not in oh062
    assert "0620820000099" not in p062

    # Historical confidential tenure (2003) redacted from oh062 AND party names stripped from p062
    assert "PRIOR OFFICER" not in oh062["0620820000016"]
    assert "PRIOR OFFICER" not in json.dumps(p062["0620820000016"])
    assert p062["0620820000016"]["d"] == "20150601:R2015-20001"
    assert "ml" not in p062["0620820000016"]
    assert "dt_lender" not in p062["0620820000016"]

    # (b) Option A REL ('RP-2020-004567' / 'RP-2020-4567') merged and NOT promoted to CONVEYANCE,
    # while standalone vol/page Bound Deed Book ('VP:512/44') WAS ingested!
    assert "4567" not in json.dumps(p062["0620820000020"])
    assert p062["0620820000020"]["d"] == "19220910:VP:512/44::D:HEIGHTS LAND CO:ORIGINAL BUYER"

    # (c) Bare system python3 invocation check (mirrors .github/workflows/update_live_permits.yml without PYTHONPATH)
    repo_root = Path(__file__).resolve().parents[2]
    clean_env = {"PATH": "/usr/bin:/bin"}
    help_proc = subprocess.run(
        [sys.executable, str(repo_root / "pipeline/atlas_pipeline/fetch_live_permits.py"), "--help"],
        cwd=str(repo_root),
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_proc.returncode == 0, help_proc.stderr
    for script_name in ("fetch_live_permits.py", "fetch_plat_and_watchlist.py", "fetch_hahc_tracker.py"):
        imp_proc = subprocess.run(
            [
                "/usr/bin/python3",
                "-c",
                f"import runpy, sys; sys.path[0] = {str(repo_root / 'pipeline/atlas_pipeline')!r}; "
                f"runpy.run_path({str(repo_root / 'pipeline/atlas_pipeline' / script_name)!r}, run_name='__check__')",
            ],
            cwd=str(repo_root),
            env=clean_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert imp_proc.returncode == 0, f"{script_name} failed under bare /usr/bin/python3: {imp_proc.stderr}"


def test_hcco_data_sales_subscriber_pipe_delimited_schema(tmp_path: Path) -> None:
    """Verify exact HCCO Data Sales pipe-delimited headers (FileDate, Film Code No., NType, RelDocNo, FileNo, MapCode, MapDesc, VolumnPage, L AFFT, SEE INSTRUMENT)."""
    from atlas_pipeline.ingest_tpia_clerk_and_hcad import ingest_clerk_tpia_tables

    clerk_dir = tmp_path / "hcco_datasales"
    clerk_dir.mkdir()

    # Lookup files included by HCCO Data Sales that must not pollute instrument counts
    (clerk_dir / "RPCodes.txt").write_text("RPCodeID|RPCode|Active\n1|W/D|True\n", encoding="utf-8")
    (clerk_dir / "RPNamesTypes.txt").write_text("NType|NTypeDesc|NTypeAlias\n1|Grantor|OR\n2|Grantee|EE\n", encoding="utf-8")

    (clerk_dir / "20261029_RPI_Instruments.txt").write_text(
        "\n".join(
            [
                "File No|Document Type|FileDate|Film Code No.|No. of Pages",
                "RP-2026-429524|L AFFT|2026-10-23|RP-2026-429524|4",
                "RP-2026-430978|CORREC|2026-10-26|RP-2026-430978|3",
                "RP-2026-435651|MAP|2026-10-28|RP-2026-435651|1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (clerk_dir / "20261029_RPI_LegalDesc.txt").write_text(
        "\n".join(
            [
                "File No|SubDivision|Section|Lot|Block|Misc|Map Page|Unit|Abstract|OutLot|Tract|Reserve|Comment",
                "RP-2026-429524|FIFTY NINE THIRTY RICHMOND AVE|||1||||||||",
                "RP-2026-430978|FALL MEADOW SUBDIVISION||14|1||||||||",
                "RP-2026-435651|SMITH STREET PLAZA|||||||||||",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (clerk_dir / "20261029_RPI_Names.txt").write_text(
        "\n".join(
            [
                "File No|Name|NType|Document Type",
                "RP-2026-429524|HOUSTON FOODS INC|1|L AFFT",
                "RP-2026-429524|AARONS ELECTRICAL SERVICE INC|2|L AFFT",
                "RP-2026-430978|CARANTALI INC|1|CORREC",
                "RP-2026-430978|SEE INSTRUMENT|2|CORREC",
                "RP-2026-430978|GUTIERREZ ANTONIO|2|CORREC",
                "RP-2026-435651|SMITH STREET PLAZA|1|MAP",
                "RP-2026-435651|SMITH OBEDIENCE A0696|2|MAP",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (clerk_dir / "20261029_RPI_RelatedDocs.txt").write_text(
        "\n".join(
            [
                "File No|RelDocNo|RelDocDesc",
                "RP-2026-430978|D265185|Related Harris County Real Property File Number",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (clerk_dir / "20261029_MAP_Instruments.txt").write_text(
        "\n".join(
            [
                "FileNo|MapCode|MapDesc|FileDate|VolumnPage|NoPages",
                "RP-2026-435651|M|SMITH STREET PLAZA|2026-10-28 00:00:00|716912|2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    res = ingest_clerk_tpia_tables(clerk_dir)
    assert res["kept_target_instruments"] == 3
    by_tok = {r.clerk_token: r for r in res["instruments"]}

    lafft = by_tok["R2026-429524"]
    assert lafft.category == "MECHANICS_LIEN"
    assert lafft.dt8 == "20261023"
    assert lafft.grantors == ["HOUSTON FOODS INC"]
    assert lafft.grantees == ["AARONS ELECTRICAL SERVICE INC"]

    correc = by_tok["R2026-430978"]
    assert correc.category == "CONVEYANCE"
    assert correc.dt8 == "20261026"
    assert correc.grantors == ["CARANTALI INC"]
    assert correc.grantees == ["GUTIERREZ ANTONIO"]  # 'SEE INSTRUMENT' stripped!
    assert "FN:D265185" in correc.related_match_keys

    plat = by_tok["R2026-435651"]
    assert plat.category == "RESTRICTION_PLAT"
    assert plat.dt8 == "20261028"
    assert plat.grantors == ["SMITH STREET PLAZA"]
    assert plat.map_code == "M"
    assert plat.map_desc == "SMITH STREET PLAZA"
    assert plat.map_vol_page == "716912"
    assert plat.page_count == 2


def test_hcco_nested_zip_subdivision_plat_calibration_and_replat_regex(tmp_path: Path) -> None:
    """Verify nested .zip archives, normalize_legal_subdivision_name replat/amendment regexes, MAP_LegalDesc.txt Surevey|Abstracts, and calibrate_platted_subdivisions_from_clerk."""
    from atlas_pipeline.ingest_tpia_clerk_and_hcad import (
        calibrate_platted_subdivisions_from_clerk,
        ingest_clerk_tpia_tables,
        normalize_legal_subdivision_name,
    )

    # 1. Regex guards: 'SECOND AMENDMENT' must not parse as sec='OND'; 'PARTIAL REPLAT NO 3' strips cleanly
    assert normalize_legal_subdivision_name(
        "INFINITY OFFICE PARK AT FALL CREEK CONDOMINIUMS DECLARATION OF CONDOMINIUM SECOND AMENDMENT"
    ) == ("INFINITY OFFICE PARK AT FALL CREEK", "")
    assert normalize_legal_subdivision_name("HIGHLAND VILLAGE PARTIAL REPLAT NO 3") == ("HIGHLAND VILLAGE", "")
    assert normalize_legal_subdivision_name("RIVERSIDE TERRACE SEC 6 PARTIAL REPLAT NO 4") == (
        "RIVERSIDE TERRACE",
        "6",
    )
    assert normalize_legal_subdivision_name("CRESMERE PLACE SEC 3 PARTIAL REPLAT NO 2") == (
        "CRESMERE PLACE",
        "3",
    )

    # 2. Build an outer .zip containing inner .zip subscriber archives (RPISubscriber.zip + MAPSubscriber.zip)
    inner_rpi_path = tmp_path / "20261029_RPISubscriber.zip"
    with zipfile.ZipFile(inner_rpi_path, "w") as zf_rpi:
        zf_rpi.writestr(
            "20261029_RPI_Instruments.txt",
            "\n".join(
                [
                    "File No|Document Type|FileDate|Film Code No.|No. of Pages",
                    "RP-2026-435651|MAP|2026-10-28|RP-2026-435651|2",
                    "RP-2026-438440|MAP|2026-10-30|RP-2026-438440|2",
                    "RP-2026-499999|REL|2026-10-30|RP-2026-499999|1",
                ]
            )
            + "\n",
        )
        zf_rpi.writestr(
            "20261029_RPI_Names.txt",
            "\n".join(
                [
                    "File No|Name|NType|Document Type",
                    "RP-2026-435651|SMITH STREET PLAZA|1|MAP",
                    "RP-2026-438440|HIGHLAND VILLAGE PARTIAL REPLAT NO 3|1|MAP",
                    "RP-2026-499999|IGNORED RELEASE PARTY|1|REL",
                ]
            )
            + "\n",
        )

    inner_map_path = tmp_path / "20261029_MAPSubscriber.zip"
    with zipfile.ZipFile(inner_map_path, "w") as zf_map:
        zf_map.writestr(
            "20261029_MAP_Instruments.txt",
            "\n".join(
                [
                    "FileNo|MapCode|MapDesc|FileDate|VolumnPage|NoPages",
                    "RP-2026-435651|M|SMITH STREET PLAZA|2026-10-28 00:00:00|716912|2",
                    "RP-2026-438440|M|HIGHLAND VILLAGE PARTIAL REPLAT NO 3|2026-10-30 00:00:00|716967|2",
                ]
            )
            + "\n",
        )
        zf_map.writestr(
            "20261029_MAP_LegalDesc.txt",
            "\n".join(
                [
                    "File No|Surevey|Abstracts",
                    "RP-2026-435651|OBEDIENCE SMITH|0696",
                    "RP-2026-438440|A C REYNOLDS|A0062",
                    "RP-2026-438440|N/A|N/A",
                ]
            )
            + "\n",
        )

    outer_bundle_zip = tmp_path / "hcco_outer_bundle.zip"
    with zipfile.ZipFile(outer_bundle_zip, "w") as zf_outer:
        zf_outer.write(inner_rpi_path, arcname="20261029_RPISubscriber.zip")
        zf_outer.write(inner_map_path, arcname="20261029_MAPSubscriber.zip")

    res = ingest_clerk_tpia_tables(outer_bundle_zip)
    assert res["kept_target_instruments"] == 2
    assert res["skipped_non_target_instruments"] == 1
    by_tok = {r.clerk_token: r for r in res["instruments"]}
    assert by_tok["R2026-435651"].surveys == ["OBEDIENCE SMITH"]
    assert by_tok["R2026-435651"].abstracts == ["696"]
    assert by_tok["R2026-438440"].surveys == ["A C REYNOLDS"]
    assert by_tok["R2026-438440"].abstracts == ["62"]

    # 3. Calibrate GeoJSON FeatureCollection in overlays_doc["platted_subdivisions"]
    overlays_doc = {
        "platted_subdivisions": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "name": "Smith Street Plaza",
                        "full_name": "SMITH STREET PLAZA",
                        "recnum": "716912",
                        "estimated_plat_year": 0,
                        "plat_year_is_exact": False,
                    },
                },
                {
                    "type": "Feature",
                    "properties": {
                        "name": "Highland Village",
                        "full_name": "HIGHLAND VILLAGE",
                        "estimated_plat_year": 1948,
                        "plat_year_is_exact": False,
                        "earliest_year": 1948,
                        "pre_1965_count": 140,
                    },
                },
            ],
        }
    }
    subd_stats = calibrate_platted_subdivisions_from_clerk(overlays_doc, res["instruments"])
    assert subd_stats == {
        "calibrated_subdivisions": 1,
        "subdivision_replats_linked": 1,
        "subdivision_surveys_enriched": 2,
    }

    smith_props = overlays_doc["platted_subdivisions"]["features"][0]["properties"]
    assert smith_props["estimated_plat_year"] == 2026
    assert smith_props["plat_year_is_exact"] is True
    assert smith_props["plat_file_date"] == "2026-10-28"
    assert smith_props["plat_clerk_file"] == "RP-2026-435651"
    assert smith_props["survey"] == "OBEDIENCE SMITH"
    assert smith_props["abstract"] == "A-696"

    hv_props = overlays_doc["platted_subdivisions"]["features"][1]["properties"]
    # Historic 1948 addition must NOT have its dedication year overwritten by a 2026 partial replat
    assert hv_props["estimated_plat_year"] == 1948
    assert hv_props["survey"] == "A C REYNOLDS"
    assert hv_props["abstract"] == "A-62"
    assert hv_props["clerk_replats"] == [
        {
            "file_no": "RP-2026-438440",
            "date": "2026-10-30",
            "name": "HIGHLAND VILLAGE PARTIAL REPLAT NO 3",
            "film_code": "716967",
            "pages": 2,
        }
    ]


