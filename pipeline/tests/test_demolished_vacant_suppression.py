"""Hermetic unit and pipeline integration tests for Option 1 Demolished / Vacant Lot suppression."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from atlas_pipeline.schema import (
    DEMOLISHED_REPLACED_ISSUE,
    DEMOLISHED_VACANT_CITATION_TAG,
    DEMOLISHED_VACANT_ISSUE,
    is_demolished_vacant_marker,
    normalize_override_record,
)
from atlas_pipeline.setup_admin_sheet import APPS_SCRIPT_CODE
from atlas_pipeline.sync_curated_edits import (
    parse_approved_csv_rows,
    sync_curated_overrides,
)


@pytest.mark.parametrize(
    ("year_raw", "footprint_issue", "footprint_notes", "source_type", "source_citation", "suppress_only", "expected"),
    [
        # Explicit vacant / demolished year tokens (unspecified suppress_only=None)
        (0, "", "", "", "", None, True),
        ("0", "", "", "", "", None, True),
        # Explicit suppress_only=False on year 0 (e.g. non-year edit on undated building) -> False
        (0, "", "", "", "", False, False),
        ("0", "", "", "", "", False, False),
        ("Vacant", "", "", "", "", False, True),
        ("Vacant lot", "", "", "", "", False, True),
        ("vacant", "", "", "", "", False, True),
        ("Demolished", "", "", "", "", False, True),
        ("N/A", "", "", "", "", False, True),
        ("none", "", "", "", "", False, True),
        # Blank year_built with demolished/vacant issue or citation tag
        ("", DEMOLISHED_VACANT_ISSUE, "", "", "", False, True),
        (None, "", "", f"Footprint Issue: {DEMOLISHED_VACANT_ISSUE}", "", False, True),
        ("", "", "", "Houston City Directory", f"{DEMOLISHED_VACANT_CITATION_TAG} | Lot cleared", False, True),
        # Legacy combined issue when accompanied by vacant cue and blank/0 year
        ("", "Building demolished or replaced", "House demolished; lot is vacant grass", "", "", False, True),
        # Explicit suppress_only flag
        ("1928", "", "", "", "", True, True),
        # Replacement structure ("Building demolished & replaced by newer structure") with valid year -> False
        ("2019", DEMOLISHED_REPLACED_ISSUE, "Original 1925 bungalow replaced by 2019 townhome", "", "", False, False),
        (1836, DEMOLISHED_REPLACED_ISSUE, "", "", "", False, False),
        ("2026", DEMOLISHED_VACANT_ISSUE, "", "", DEMOLISHED_VACANT_CITATION_TAG, False, False),
        # Blank year with replacement structure or unrelated shape issue -> False
        ("", DEMOLISHED_REPLACED_ISSUE, "Replaced by 2021 structure", "", "", False, False),
        ("", "Wrong orientation / Rotated angle", "Rotated 30 degrees", "", "", False, False),
        # Non-numeric non-vacant year text must NOT silently coerce to year 0
        ("c. 1928", DEMOLISHED_VACANT_ISSUE, "", "", DEMOLISHED_VACANT_CITATION_TAG, False, False),
        # Out-of-range positive year (e.g. 1800) without vacant token -> False
        ("1800", DEMOLISHED_VACANT_ISSUE, "", "", "", False, False),
    ],
)
def test_is_demolished_vacant_marker_matrix(
    year_raw: object,
    footprint_issue: str,
    footprint_notes: str,
    source_type: str,
    source_citation: str,
    suppress_only: bool,
    expected: bool,
) -> None:
    """UT-2: Verify all demolished/vacant vs replacement-structure entry paths in schema.py."""
    actual = is_demolished_vacant_marker(
        year_raw,
        footprint_issue=footprint_issue,
        footprint_notes=footprint_notes,
        source_type=source_type,
        source_citation=source_citation,
        suppress_only=suppress_only,
    )
    assert actual is expected


def test_normalize_override_record_suppress_only_strips_geometry_and_zeroes_dates() -> None:
    """UT-2: normalize_override_record enforces suppress_only=True, year_built=0, decade=0, and no geometry."""
    poly = {
        "type": "Polygon",
        "coordinates": [[[-95.39, 29.79], [-95.389, 29.79], [-95.389, 29.791], [-95.39, 29.79]]],
    }
    suppressed = normalize_override_record(
        "0621100000014",
        {
            "address": "1127 KEY ST",
            "year_built": 1928,
            "decade": 1920,
            "suppress_only": True,
            "geometry": poly,
            "notes": "Demolished \u2014 vacant lot",
        },
    )
    assert suppressed["suppress_only"] is True
    assert suppressed["year_built"] == 0
    assert suppressed["decade"] == 0
    assert "geometry" not in suppressed
    assert "\u2014" not in suppressed["notes"]

    # Replacement structure record preserves explicit suppress_only=False and valid decade
    replaced = normalize_override_record(
        "0621100000014",
        {
            "address": "1127 KEY ST",
            "year_built": "2021",
            "suppress_only": False,
            "geometry": poly,
        },
    )
    assert replaced["suppress_only"] is False
    assert replaced["year_built"] == 2021
    assert replaced["decade"] == 2020
    assert replaced["geometry"] == poly


def test_parse_approved_csv_rows_entry_paths_and_replacement_structure() -> None:
    """UT-2: Verify Approved_Edits CSV parsing across 0/Vacant/Demolished, blank-year vacant, and replacement year."""
    csv_text = "\n".join([
        "status,hcad_num,address,year_built,original_hcad_year,footprint_issue,source_type,source_citation,building_name",
        "Approved,0621100000014,1127 KEY ST,0,1920,,,,",
        "Approved,0621100000015,1129 KEY ST,Vacant,1924,,,,",
        "Approved,0621100000016,1131 KEY ST,Vacant lot,1926,,,,",
        "Approved,0621100000017,1133 KEY ST,Demolished,1922,,,,",
        f"Approved,0621100000018,1135 KEY ST,,1930,{DEMOLISHED_VACANT_ISSUE},Aerial / Satellite Imagery,,",
        f"Approved,0621100000019,1137 KEY ST,,1932,,Footprint Issue: {DEMOLISHED_VACANT_ISSUE},{DEMOLISHED_VACANT_CITATION_TAG},",
        f"Approved,1223580020001#old_annex,7900 N ELDRIDGE PKWY,0,1975,{DEMOLISHED_VACANT_ISSUE},Aerial / Satellite Imagery,{DEMOLISHED_VACANT_CITATION_TAG},Demolished Annex",
        f"Approved,0621100000020,1139 KEY ST,2021,1925,{DEMOLISHED_REPLACED_ISSUE},Aerial / Satellite Imagery,Replaced by 2021 residence,New Key Residence",
        # Approved moderation flag rows carrying 0 / N/A MUST be skipped (never ingested as suppress_only overrides)
        "Approved,0420090000018,408 AVONDALE ST,0,1928,,PHOTO_MISATTRIBUTION_FLAG,[1-CLICK PHOTO FLAG],",
        "Approved,0420090000019,410 AVONDALE ST,N/A,1928,,CLAIM_DISPUTE_FLAG,[DISPUTED FIELD: architect],",
        "Approved,0420090000020,412 AVONDALE ST,0,1928,,NARRATIVE_ERROR_FLAG,[NARRATIVE FLAG],",
    ])
    rows = parse_approved_csv_rows(csv_text)
    by_key = {r["override_key"]: r for r in rows}
    assert len(by_key) == 8
    for skipped_flag_hcad in ("0420090000018", "0420090000019", "0420090000020"):
        assert skipped_flag_hcad not in by_key

    for vacant_key in (
        "0621100000014",
        "0621100000015",
        "0621100000016",
        "0621100000017",
        "0621100000018",
        "0621100000019",
        "1223580020001#old_annex",
    ):
        rec = by_key[vacant_key]
        assert rec["suppress_only"] is True, f"Expected suppress_only=True for {vacant_key}"
        assert rec["year_built"] == 0
        assert rec["decade"] == 0
        assert "geometry" not in rec

    sub_vacant = by_key["1223580020001#old_annex"]
    assert sub_vacant["is_building_override"] is True
    assert sub_vacant["building_id"] == "1223580020001#old_annex"
    assert not sub_vacant.get("keep_shard_footprints")

    replaced = by_key["0621100000020"]
    assert replaced["suppress_only"] is False
    assert replaced["year_built"] == 2021
    assert replaced["decade"] == 2020
    assert replaced["original_hcad_year"] == 1925


def test_sync_curated_overrides_overrides_existing_removes_buildings_and_revives_replacements(
    tmp_path: Path,
) -> None:
    """UT-2 & UT-3: sync_curated_overrides suppresses whole-parcel & #<slug> buildings and revives replacements."""
    poly_a = {
        "type": "Polygon",
        "coordinates": [[[-95.39, 29.79], [-95.389, 29.79], [-95.389, 29.791], [-95.39, 29.79]]],
    }
    poly_b = {
        "type": "Polygon",
        "coordinates": [[[-95.60, 29.89], [-95.599, 29.89], [-95.599, 29.891], [-95.60, 29.89]]],
    }
    overrides_path = tmp_path / "curated_overrides.json"
    buildings_path = tmp_path / "buildings.geojson"

    # Seed pre-existing curated_overrides.json with a historic building override + geometry
    overrides_path.write_text(
        json.dumps({
            "version": 1,
            "updatedAt": "2026-10-27T00:00:00Z",
            "googleSheetCsvUrl": "",
            "submissionWebhookUrl": "",
            "overrides": {
                "0621100000014": {
                    "id": "0621100000014",
                    "hcad_num": "0621100000014",
                    "address": "1127 KEY ST",
                    "year_built": 1928,
                    "decade": 1920,
                    "original_hcad_year": 1920,
                    "geometry": poly_a,
                    "contributing": "Contributing",
                },
                "1223580020001#main_school": {
                    "id": "1223580020001#main_school",
                    "building_id": "1223580020001#main_school",
                    "hcad_num": "1223580020001",
                    "is_building_override": True,
                    "keep_shard_footprints": True,
                    "replace_parcel_shards": False,
                    "address": "7900 N ELDRIDGE PKWY",
                    "building_name": "Main High School",
                    "year_built": 2002,
                    "decade": 2000,
                },
            },
        }),
        encoding="utf-8",
    )

    # Seed buildings.geojson with:
    # - 2 footprints on parcel 0621100000014 (main house + garage -> both must be removed by whole-parcel suppress)
    # - 2 sub-buildings on campus 1223580020001 (#main_school must survive, #old_portable must be removed)
    buildings_path.write_text(
        json.dumps({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": poly_a,
                    "properties": {
                        "id": "0621100000014#b1",
                        "building_id": "0621100000014#b1",
                        "hcad_num": "0621100000014",
                        "address": "1127 KEY ST",
                        "year_built": 1928,
                        "decade": 1920,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": poly_a,
                    "properties": {
                        "id": "0621100000014#b2",
                        "building_id": "0621100000014#b2",
                        "hcad_num": "0621100000014",
                        "address": "1127 KEY ST",
                        "year_built": 1928,
                        "decade": 1920,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": poly_b,
                    "properties": {
                        "id": "1223580020001#main_school",
                        "building_id": "1223580020001#main_school",
                        "hcad_num": "1223580020001",
                        "address": "7900 N ELDRIDGE PKWY",
                        "year_built": 2002,
                        "decade": 2000,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": poly_b,
                    "properties": {
                        "id": "1223580020001#old_portable",
                        "building_id": "1223580020001#old_portable",
                        "hcad_num": "1223580020001",
                        "address": "7900 N ELDRIDGE PKWY",
                        "year_built": 1985,
                        "decade": 1980,
                    },
                },
            ],
        }),
        encoding="utf-8",
    )

    csv_round_1 = tmp_path / "approved_round1.csv"
    csv_round_1.write_text(
        "\n".join([
            "status,hcad_num,address,year_built,original_hcad_year,footprint_issue,source_citation",
            f"Approved,0621100000014,1127 KEY ST,,1920,{DEMOLISHED_VACANT_ISSUE},{DEMOLISHED_VACANT_CITATION_TAG}",
            f"Approved,1223580020001#old_portable,7900 N ELDRIDGE PKWY,0,1985,{DEMOLISHED_VACANT_ISSUE},{DEMOLISHED_VACANT_CITATION_TAG}",
        ]),
        encoding="utf-8",
    )

    sync_curated_overrides(data_dir=tmp_path, local_csv_path=csv_round_1)

    synced_ov = json.loads(overrides_path.read_text(encoding="utf-8"))["overrides"]
    assert synced_ov["0621100000014"]["suppress_only"] is True
    assert synced_ov["0621100000014"]["year_built"] == 0
    assert synced_ov["0621100000014"]["decade"] == 0
    assert "geometry" not in synced_ov["0621100000014"]

    assert synced_ov["1223580020001#old_portable"]["suppress_only"] is True
    assert synced_ov["1223580020001#old_portable"]["is_building_override"] is True
    assert synced_ov["1223580020001#old_portable"]["year_built"] == 0
    assert "geometry" not in synced_ov["1223580020001#old_portable"]

    # Sibling #main_school on same 13-digit HCAD must remain untouched and visible
    assert not synced_ov["1223580020001#main_school"].get("suppress_only")
    assert synced_ov["1223580020001#main_school"]["year_built"] == 2002

    synced_blds = json.loads(buildings_path.read_text(encoding="utf-8"))["features"]
    remaining_ids = [f["properties"]["building_id"] for f in synced_blds]
    assert remaining_ids == ["1223580020001#main_school"]
    assert all(int(f["properties"].get("year_built") or 0) >= 1836 for f in synced_blds)

    # Round 2: Later approval of replacement structures (year >= 1830) clears prior suppress_only
    csv_round_2 = tmp_path / "approved_round2.csv"
    csv_round_2.write_text(
        "\n".join([
            "status,hcad_num,address,year_built,original_hcad_year,footprint_issue,source_citation",
            f"Approved,0621100000014,1127 KEY ST,2024,1920,{DEMOLISHED_REPLACED_ISSUE},New 2024 infill home",
            f"Approved,1223580020001#old_portable,7900 N ELDRIDGE PKWY,2023,1985,{DEMOLISHED_REPLACED_ISSUE},New permanent wing",
        ]),
        encoding="utf-8",
    )
    sync_curated_overrides(data_dir=tmp_path, local_csv_path=csv_round_2)

    revived_ov = json.loads(overrides_path.read_text(encoding="utf-8"))["overrides"]
    assert revived_ov["0621100000014"]["suppress_only"] is False
    assert revived_ov["0621100000014"]["year_built"] == 2024
    assert revived_ov["0621100000014"]["decade"] == 2020
    assert "keep_shard_footprints" not in revived_ov["0621100000014"]

    assert revived_ov["1223580020001#old_portable"]["suppress_only"] is False
    assert revived_ov["1223580020001#old_portable"]["year_built"] == 2023
    assert revived_ov["1223580020001#old_portable"]["decade"] == 2020
    assert revived_ov["1223580020001#old_portable"]["keep_shard_footprints"] is True


PARITY_VECTORS: list[tuple[dict[str, object], bool, object]] = [
    (
        {
            "hcad_num": "0621100000014",
            "address": "1127 KEY ST",
            "suggested_year_built": "0",
            "source_type": "Houston City Directory",
        },
        True,
        0,
    ),
    (
        {
            "hcad_num": "0621100000015",
            "address": "1129 KEY ST",
            "suggested_year_built": "0.0",
            "footprint_issue": DEMOLISHED_VACANT_ISSUE,
            "source_type": "Aerial / Satellite Imagery",
        },
        True,
        0,
    ),
    (
        {
            "hcad_num": "0621100000016",
            "address": "1131 KEY ST",
            "suggested_year_built": "",
            "footprint_issue": DEMOLISHED_VACANT_ISSUE,
            "source_type": f"Footprint Issue: {DEMOLISHED_VACANT_ISSUE}",
            "source_citation": DEMOLISHED_VACANT_CITATION_TAG,
        },
        True,
        0,
    ),
    (
        {
            "hcad_num": "0621100000017",
            "address": "1133 KEY ST",
            "suggested_year_built": "",
            "footprint_issue": "Building demolished or replaced",
            "footprint_notes": "Lot cleared and vacant",
            "source_type": "Aerial / Satellite Imagery",
        },
        True,
        0,
    ),
    (
        {
            "hcad_num": "0420090000018",
            "address": "408 AVONDALE ST",
            "suggested_year_built": "0",
            "suppress_only": False,
            "building_name": "Avondale Grocery",
            "source_type": "Houston City Directory",
        },
        False,
        "",
    ),
    (
        {
            "hcad_num": "0621100000020",
            "address": "1139 KEY ST",
            "suggested_year_built": "2019",
            "footprint_issue": DEMOLISHED_REPLACED_ISSUE,
            "source_type": "Aerial / Satellite Imagery",
        },
        False,
        2019,
    ),
]


@pytest.mark.parametrize(("payload", "expected_suppress", "expected_sheet_yr"), PARITY_VECTORS)
def test_python_and_apps_script_vacant_marker_parity(
    payload: dict[str, object],
    expected_suppress: bool,
    expected_sheet_yr: object,
) -> None:
    """Verify cross-runtime parity between schema.is_demolished_vacant_marker and APPS_SCRIPT_CODE doPost(e)."""
    actual_py = is_demolished_vacant_marker(
        payload.get("suggested_year_built"),
        footprint_issue=str(payload.get("footprint_issue") or ""),
        footprint_notes=str(payload.get("footprint_notes") or ""),
        source_type=str(payload.get("source_type") or ""),
        source_citation=str(payload.get("source_citation") or ""),
        suppress_only=payload.get("suppress_only") if isinstance(payload.get("suppress_only"), bool) else None,
    )
    assert actual_py is expected_suppress

    harness = """
const vm = require("node:vm");
const appsScriptCode = process.env.APPS_SCRIPT_CODE;
const payload = JSON.parse(process.env.TEST_PAYLOAD);
const writtenRows = [];
const mockSheet = {
  getRange() {
    return {
      getValues: () => [["hcad_num"]],
      getValue: () => "test-passkey",
      setValues(rows) { writtenRows.push(rows[0]); },
    };
  },
};
const sandbox = {
  LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
  SpreadsheetApp: {
    getActiveSpreadsheet: () => ({
      getOwner: () => ({ getEmail: () => "admin@preservationhouston.org" }),
      getEditors: () => [],
      getSheetByName: () => mockSheet,
      insertSheet: () => mockSheet,
    }),
  },
  ContentService: {
    MimeType: { JSON: "application/json", CSV: "text/csv" },
    createTextOutput(txt) { return { text: txt, setMimeType() { return this; } }; },
  },
  UrlFetchApp: { fetch: () => ({ getResponseCode: () => 400 }) },
  JSON, String, Number, Boolean, Date, RegExp, Math, isFinite, encodeURIComponent,
};
vm.createContext(sandbox);
vm.runInContext(appsScriptCode, sandbox);
sandbox.doPost({ postData: { contents: JSON.stringify(payload) } });
process.stdout.write(JSON.stringify(writtenRows[0]));
"""
    proc = subprocess.run(
        ["node", "-e", harness],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "APPS_SCRIPT_CODE": APPS_SCRIPT_CODE,
            "TEST_PAYLOAD": json.dumps(payload),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    row = json.loads(proc.stdout)
    assert row[3] == expected_sheet_yr


def test_apps_script_dopost_handles_vacant_zero_blank_year_and_quick_flags() -> None:
    """UT-2: Execute APPS_SCRIPT_CODE doPost(e) in Node VM across vacant=0, blank-year vacant, replacement, and quick flags."""
    harness = """
const vm = require("node:vm");
const appsScriptCode = process.env.APPS_SCRIPT_CODE;
const writtenRows = [];
const mockSheet = {
  getRange(a1OrRow) {
    if (a1OrRow === "B:B") {
      return { getValues: () => [["hcad_num"]] };
    }
    if (a1OrRow === "B6") {
      return { getValue: () => "test-passkey" };
    }
    return {
      setValues(rows) {
        writtenRows.push(rows[0]);
      },
    };
  },
};
const sandbox = {
  LockService: {
    getScriptLock: () => ({ waitLock() {}, releaseLock() {} }),
  },
  SpreadsheetApp: {
    getActiveSpreadsheet: () => ({
      getOwner: () => ({ getEmail: () => "admin@preservationhouston.org" }),
      getEditors: () => [],
      getSheetByName: () => mockSheet,
      insertSheet: () => mockSheet,
    }),
  },
  ContentService: {
    MimeType: { JSON: "application/json", CSV: "text/csv" },
    createTextOutput(txt) {
      return { text: txt, setMimeType() { return this; } };
    },
  },
  UrlFetchApp: { fetch: () => ({ getResponseCode: () => 400 }) },
  JSON,
  String,
  Number,
  Boolean,
  Date,
  RegExp,
  encodeURIComponent,
};
vm.createContext(sandbox);
vm.runInContext(appsScriptCode, sandbox);

function post(payload) {
  const before = writtenRows.length;
  sandbox.doPost({ postData: { contents: JSON.stringify(payload) } });
  return writtenRows[before];
}

const results = {
  explicitZero: post({
    hcad_num: "0621100000014",
    address: "1127 KEY ST",
    hcad_year_built: 1920,
    suggested_year_built: 0,
    footprint_issue: "Building demolished - lot currently vacant",
    source_type: "Footprint Issue: Building demolished - lot currently vacant",
    source_citation: "[DEMOLISHED / VACANT LOT - HIDE FOOTPRINT]",
  }),
  blankYearVacantIssue: post({
    hcad_num: "0621100000015",
    address: "1129 KEY ST",
    hcad_year_built: 1924,
    suggested_year_built: "",
    footprint_issue: "Building demolished - lot currently vacant",
    source_type: "Footprint Issue: Building demolished - lot currently vacant",
    source_citation: "[DEMOLISHED / VACANT LOT - HIDE FOOTPRINT]",
  }),
  vacantStringYear: post({
    hcad_num: "0621100000016",
    address: "1131 KEY ST",
    hcad_year_built: 1926,
    suggested_year_built: "Vacant lot",
  }),
  replacementYear: post({
    hcad_num: "0621100000017",
    address: "1133 KEY ST",
    hcad_year_built: 1922,
    suggested_year_built: 2021,
    footprint_issue: "Building demolished & replaced by newer structure",
  }),
  photoQuickFlag: post({
    hcad_num: "0621100000018",
    address: "1135 KEY ST",
    hcad_year_built: 1928,
    suggested_year_built: 0,
    source_type: "PHOTO_MISATTRIBUTION_FLAG",
    source_citation: "[1-CLICK PHOTO FLAG: Wrong Building / Misattributed Photo]",
  }),
  claimQuickFlag: post({
    hcad_num: "0621100000019",
    address: "1137 KEY ST",
    hcad_year_built: 1930,
    suggested_year_built: 0,
    source_type: "CLAIM_DISPUTE_FLAG",
    source_citation: "[DISPUTED FIELD: architect]",
  }),
};
process.stdout.write(JSON.stringify(results));
"""
    proc = subprocess.run(
        ["node", "-e", harness],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "APPS_SCRIPT_CODE": APPS_SCRIPT_CODE},
        capture_output=True,
        text=True,
        check=True,
    )
    out = json.loads(proc.stdout)
    # Column D (index 3) is year_built in Google Sheet
    assert out["explicitZero"][3] == 0
    assert out["blankYearVacantIssue"][3] == 0
    assert out["vacantStringYear"][3] == 0
    assert out["replacementYear"][3] == 2021
    # Quick-flag payloads pass suggested_year_built=0 as a placeholder and MUST write '' (not 0!)
    assert out["photoQuickFlag"][3] == ""
    assert out["claimQuickFlag"][3] == ""


def test_live_repository_buildings_geojson_and_curated_overrides_suppression_invariants(
    tmp_path: Path,
) -> None:
    """IT-1: Verify live app/public/data artifacts and end-to-end sync_curated_overrides invariants."""
    repo_root = Path(__file__).resolve().parents[2]
    data_dir = repo_root / "app" / "public" / "data"
    buildings_path = data_dir / "buildings.geojson"
    overrides_path = data_dir / "curated_overrides.json"

    assert buildings_path.exists(), f"Missing {buildings_path}"
    assert overrides_path.exists(), f"Missing {overrides_path}"

    raw_overrides_bytes = overrides_path.read_bytes()
    assert len(raw_overrides_bytes) < 8 * 1024 * 1024, "curated_overrides.json must stay < 8 MB"
    raw_overrides_text = raw_overrides_bytes.decode("utf-8").strip()
    assert "\n" not in raw_overrides_text, "curated_overrides.json must remain single-line compact JSON"

    overrides_doc = json.loads(raw_overrides_text)
    overrides_map = overrides_doc.get("overrides", {})
    assert len(overrides_map) > 100

    for key, rec in overrides_map.items():
        if rec.get("suppress_only"):
            assert int(rec.get("year_built") or 0) == 0, f"Suppressed override {key} must have year_built=0"
            assert int(rec.get("decade") or 0) == 0, f"Suppressed override {key} must have decade=0"
            assert "geometry" not in rec or rec["geometry"] is None, f"Suppressed {key} must omit geometry"
            assert not rec.get("keep_shard_footprints"), f"Suppressed {key} must not keep shard footprints"
        else:
            yr = int(rec.get("year_built") or 0)
            assert yr == 0 or (1836 <= yr <= 2027), (
                f"Non-suppressed override {key} has invalid year_built={yr}"
            )

    buildings_fc = json.loads(buildings_path.read_text(encoding="utf-8"))
    features = buildings_fc.get("features", [])
    assert len(features) > 1000
    undated_or_suppressed = [
        f.get("properties", {}).get("id") or f.get("properties", {}).get("hcad_num")
        for f in features
        if int(f.get("properties", {}).get("year_built") or 0) < 1836
        or bool(f.get("properties", {}).get("suppress_only"))
    ]
    assert undated_or_suppressed == [], (
        f"buildings.geojson must contain 0 undated (year_built < 1836) or suppress_only features, "
        f"found {len(undated_or_suppressed)}: {undated_or_suppressed[:5]}"
    )

    # End-to-end slice run of sync_curated_overrides against real records copied into tmp_path
    key_st_ov = dict(overrides_map["0621100000014"])
    cy_main_ov = dict(overrides_map["1223580020001#cy_ridge_hs_main"])
    sample_blds = [
        f
        for f in features
        if str(f.get("properties", {}).get("hcad_num") or "") == "0621100000014"
    ][:2]
    assert len(sample_blds) >= 1, "Expected 0621100000014 in live buildings.geojson"

    (tmp_path / "curated_overrides.json").write_text(
        json.dumps(
            {
                "version": 1,
                "updatedAt": "2026-10-28T00:00:00Z",
                "googleSheetCsvUrl": "",
                "submissionWebhookUrl": "",
                "overrides": {
                    "0621100000014": key_st_ov,
                    "1223580020001#cy_ridge_hs_main": cy_main_ov,
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "buildings.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": sample_blds}),
        encoding="utf-8",
    )
    csv_path = tmp_path / "approved_live_slice.csv"
    csv_path.write_text(
        "\n".join(
            [
                "status,hcad_num,address,year_built,original_hcad_year,footprint_issue,source_citation",
                f"Approved,0621100000014,1127 KEY ST,,1920,{DEMOLISHED_VACANT_ISSUE},{DEMOLISHED_VACANT_CITATION_TAG}",
                f"Approved,1223580020001#cy_ridge_hs_aux_1,7900 N ELDRIDGE PKWY,0,2002,{DEMOLISHED_VACANT_ISSUE},{DEMOLISHED_VACANT_CITATION_TAG}",
            ]
        ),
        encoding="utf-8",
    )

    sync_curated_overrides(tmp_path, local_csv_path=csv_path)

    synced_ov = json.loads((tmp_path / "curated_overrides.json").read_text(encoding="utf-8"))["overrides"]
    assert synced_ov["0621100000014"]["suppress_only"] is True
    assert synced_ov["0621100000014"]["year_built"] == 0
    assert "geometry" not in synced_ov["0621100000014"]
    assert synced_ov["1223580020001#cy_ridge_hs_aux_1"]["suppress_only"] is True
    assert synced_ov["1223580020001#cy_ridge_hs_aux_1"]["is_building_override"] is True
    assert bool(synced_ov["1223580020001#cy_ridge_hs_main"].get("suppress_only")) is False
    assert synced_ov["1223580020001#cy_ridge_hs_main"]["year_built"] == 2002

    synced_blds = json.loads((tmp_path / "buildings.geojson").read_text(encoding="utf-8"))["features"]
    assert synced_blds == [], "Suppressed 0621100000014 building features must be removed from buildings.geojson"
