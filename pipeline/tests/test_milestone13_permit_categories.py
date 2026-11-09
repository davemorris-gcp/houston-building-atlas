"""Milestone 13 unit and hermetic shard-repair tests for official HCAD permit_type categories."""

from __future__ import annotations

import gzip
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from atlas_pipeline import cli
from atlas_pipeline.build_permits_and_contractors import (
    format_repair_permit_categories_cli_summary,
    repair_permit_shard_categories,
)
from atlas_pipeline.schema import (
    PERMIT_TYPE_CATEGORY,
    VALID_PERMIT_CAT_CODES,
    _HCAD_TP_DESCR_TO_PTYPE,
    classify_hcad_permit_category_and_label,
    normalize_hcad_permit_row,
    read_json_or_gzip_path,
    write_deterministic_gzip_json_shard,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "app" / "public" / "data"


@pytest.mark.parametrize(
    ("ptype", "expected_tp", "expected_cat"),
    [
        # Official 25 HCAD permits.txt codes
        ("07", "Outbuilding / Yard Feature (OB&Y)", "aux"),
        ("08", "Manufactured / Mobile Home", "aux"),
        ("09", "New Dwelling (No Municipal Permit)", "new"),
        ("10", "New Residential Construction", "new"),
        ("11", "Additions or Remodeling", "remodel"),
        ("12", "Repairs (Foundation / General Maintenance)", "repair"),
        ("13", "Residential Demolition", "demo"),
        ("14", "Manufactured Home (Real Property)", "new"),
        ("15", "Moved / Relocated Structure", "reloc"),
        ("16", "Fire Damage Report", "repair"),
        ("17", "Storm / Disaster Repair", "repair"),
        ("20", "New Condominium Unit", "new"),
        ("21", "Condo Addition / Remodel", "remodel"),
        ("30", "New Commercial / Multi-Family Building", "new"),
        ("31", "Commercial Addition / Remodel", "remodel"),
        ("32", "Paving / Parking Lot / Slab", "aux"),
        ("33", "Commercial Demolition", "demo"),
        ("35", "Commercial Fire Damage Report", "repair"),
        ("39", "New Construction (No Municipal Permit)", "new"),
        ("40", "New Commercial / Industrial Building", "new"),
        ("41", "Repair / Addition / Remodel", "repair"),
        ("43", "Demolition", "demo"),
        ("50", "Storm / Flood Damage Appraisal Check", "repair"),
        ("60", "HCAD Property Owner Mailer Check", "other"),
        ("84", "Tax-Exempt Property Permit", "other"),
        # Legacy pre-2005 / synthetic codes
        ("01", "New Construction", "new"),
        ("02", "Demolition", "demo"),
        ("03", "Addition / Increase Sq Ft", "add"),
        ("04", "Finish Out / Completion", "remodel"),
        ("05", "Remodel / Renovation", "remodel"),
        ("06", "Mechanical / Electrical / Plumbing", "mep"),
        ("18", "Site / Drainage / Utilities", "aux"),
        ("TX", "County Tax Permit", "remodel"),
        ("W", "Water / Utility Permit", "mep"),
    ],
)
def test_permit_type_category_covers_all_25_official_and_legacy_codes(
    ptype: str,
    expected_tp: str,
    expected_cat: str,
) -> None:
    assert PERMIT_TYPE_CATEGORY[ptype] == (expected_tp, expected_cat)
    assert classify_hcad_permit_category_and_label(ptype, "", "") == (
        expected_tp,
        expected_cat,
    )
    assert expected_cat in VALID_PERMIT_CAT_CODES


def test_permit_type_category_key_completeness() -> None:
    expected_keys = {
        "01", "02", "03", "04", "05", "06",
        "07", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17", "18",
        "20", "21", "30", "31", "32", "33", "35", "39", "40", "41", "43", "50",
        "60", "84", "TX", "W",
    }
    assert set(PERMIT_TYPE_CATEGORY.keys()) == expected_keys
    for tp_label, cat_code in PERMIT_TYPE_CATEGORY.values():
        assert tp_label
        assert cat_code in VALID_PERMIT_CAT_CODES


@pytest.mark.parametrize(
    ("ptype", "pdesc", "dscr", "expected_tp", "expected_cat"),
    [
        # OMITTED IMPS / OMITTED IMPROVEMENTS refinement overrides moved-structure & remodel codes
        ("15", "Relocation / Bldg Move", "2020 OMITTED IMPS ADDED", "HCAD Omitted Improvement Assessment", "add"),
        ("15", "Relocation / Bldg Move", "2021 OMITTED IMPROVEMENTS", "HCAD Omitted Improvement Assessment", "add"),
        ("11", "Additions or Remodeling", "OMITTED IMPS REAR ROOM", "HCAD Omitted Improvement Assessment", "add"),
        # Type 07 refinements
        ("07", "Extra Features (OB&Y)", "ONE-STOP RE-ROOF RESIDENTIAL", "Re-Roof / Roof Structure", "roof"),
        ("07", "Extra Features (OB&Y)", "GUNITE SWIMMING POOL & SPA", "Swimming Pool / Spa", "aux"),
        # Type 11 refinements
        ("11", "Additions or Remodeling", "REROOF SHINGLES", "Re-Roof / Roof Structure", "roof"),
        ("11", "Additions or Remodeling", "2ND STORY ADDITION & REMODEL", "Residential Addition & Remodel", "add"),
        ("11", "Additions or Remodeling", "FRAME ADDN 400 SQ FT", "Residential Addition", "add"),
        ("11", "Additions or Remodeling", "INCREASE SQ FT REAR", "Residential Addition", "add"),
        ("11", "Additions or Remodeling", "INTERIOR NON-LOAD BEARING PARTITIONS", "Interior Partition Remodel", "remodel"),
        ("11", "Additions or Remodeling", "ONE-STOP REMODEL, SINGLE FAMILY RESIDENTIAL", "Additions or Remodeling", "remodel"),
        # Type 12 refinements
        ("12", "Repairs (Foundation / GM)", "ONE-STOP RE-ROOF", "Re-Roof / Roof Repair", "roof"),
        ("12", "Repairs (Foundation / GM)", "FOUNDATION REPAIR 18 PIERS", "Foundation Repair", "repair"),
        ("12", "Repairs (Foundation / GM)", "UNDERPIN & HOUSE LEVEL", "Foundation Repair", "repair"),
        ("12", "Repairs (Foundation / GM)", "RESIDENTIAL REPAIRS AS PER SPEC LIST", "Repairs (Foundation / General Maintenance)", "repair"),
        # Type 13 refinements
        ("13", "Residential Demolition", "INTERIOR DEMO NON-LOAD BEARING WALLS", "Interior Partition Remodel", "remodel"),
        ("13", "Residential Demolition", "DEMO SWIMMING POOL IN REAR", "Swimming Pool / Spa Demolition", "aux"),
        ("13", "Residential Demolition", "DEMO DETACHED GARAGE ONLY", "Accessory / Garage Demolition", "demo"),
        ("13", "Residential Demolition", "DEMO HOUSE AND DETACHED GARAGE", "Residential Demolition", "demo"),
        ("13", "Residential Demolition", "DEMO RES/SEWER DISC", "Residential Demo / Sewer Disconnect", "demo"),
        # Type 31 refinements
        ("31", "Addtns, Remodels, Lease Space", "COMMERCIAL LEASE SPACE BUILD-OUT", "Commercial Tenant Build-Out / Remodel", "remodel"),
        ("31", "Addtns, Remodels, Lease Space", "2-STORY WAREHOUSE ADDITION", "Commercial Addition / Remodel", "add"),
        ("31", "Addtns, Remodels, Lease Space", "EXTERIOR FACADE RENOVATION", "Commercial Addition / Remodel", "remodel"),
        # Type 33 & 41 refinements
        ("33", "Commercial Demolition", "INTERIOR SELECTIVE DEMO LEASE SPACE", "Commercial Interior Selective Demo", "remodel"),
        ("33", "Commercial Demolition", "DEMO COM/SEWER DISC", "Commercial Demo / Sewer Disconnect", "demo"),
        ("41", "Rep, Addtn, Rmdl, Fire Dam", "RESTORE AFTER FIRE DAMAGE", "Fire Damage Repair / Remodel", "repair"),
        # Echoed generic HCAD permit_tp_descr titles must preserve base permit_type labels
        ("11", "Additions or Remodeling", "Additions or Remodeling", "Additions or Remodeling", "remodel"),
        ("31", "Addtns, Remodels, Lease Space", "Addtns, Remodels, Lease Space", "Commercial Addition / Remodel", "remodel"),
        ("41", "Rep, Addtn, Rmdl, Fire Dam", "Rep, Addtn, Rmdl, Fire Dam", "Repair / Addition / Remodel", "repair"),
        # Carried-forward permit_tp_descr fallbacks when ptype is blank across all official headers
        ("", "Extra Features (OB&Y)", "GUNITE SWIMMING POOL", "Swimming Pool / Spa", "aux"),
        ("", "Tangible (PP) Mobile Home", "", "Manufactured / Mobile Home", "aux"),
        ("", "New Dwelling - no permit", "", "New Dwelling (No Municipal Permit)", "new"),
        ("", "New Dwelling - permit", "", "New Residential Construction", "new"),
        ("", "Additions or Remodeling", "ONE-STOP REMODEL", "Additions or Remodeling", "remodel"),
        ("", "Additions or Remodeling", "2ND STORY ADDITION & REMODEL", "Residential Addition & Remodel", "add"),
        ("", "Repairs (Foundation / GM)", "FOUNDATION UNDERPINNING", "Foundation Repair", "repair"),
        ("", "Residential Demolition", "DEMO RES/SEWER DISC", "Residential Demo / Sewer Disconnect", "demo"),
        ("", "RP Mobile Home w/ Land", "", "Manufactured Home (Real Property)", "new"),
        ("", "Relocation / Bldg Move", "", "Moved / Relocated Structure", "reloc"),
        ("", "Fire Reports", "", "Fire Damage Report", "repair"),
        ("", "Disaster Repair", "", "Storm / Disaster Repair", "repair"),
        ("", "New Building", "", "New Commercial / Multi-Family Building", "new"),
        ("", "Addtns, Remodels, Lease Space", "COMMERCIAL LEASE SPACE BUILD-OUT", "Commercial Tenant Build-Out / Remodel", "remodel"),
        ("", "Paving, Parking Lot, Slab", "", "Paving / Parking Lot / Slab", "aux"),
        ("", "Commercial Demolition", "DEMO COM/SEWER DISC", "Commercial Demo / Sewer Disconnect", "demo"),
        ("", "New Condominium Unit", "", "New Condominium Unit", "new"),
        ("", "Condo addt, rmdl, combine", "", "Condo Addition / Remodel", "remodel"),
        ("", "New Construction w/o Permit", "", "New Construction (No Municipal Permit)", "new"),
        ("", "Rep, Addtn, Rmdl, Fire Dam", "FIRE RESTORATION", "Fire Damage Repair / Remodel", "repair"),
        ("", "Rep, Addtn, Rmdl, Fire Dam", "Rep, Addtn, Rmdl, Fire Dam", "Repair / Addition / Remodel", "repair"),
        ("", "Demolition", "", "Demolition", "demo"),
        ("", "Disaster Review", "", "Storm / Flood Damage Appraisal Check", "repair"),
        ("", "Mailer Generated Field Check", "", "HCAD Property Owner Mailer Check", "other"),
        ("", "Exempt Property", "", "Tax-Exempt Property Permit", "other"),
        ("", "", "", "Building Permit", "other"),
    ],
)
def test_classify_hcad_permit_category_and_label_refinements(
    ptype: str,
    pdesc: str,
    dscr: str,
    expected_tp: str,
    expected_cat: str,
) -> None:
    assert classify_hcad_permit_category_and_label(ptype, pdesc, dscr) == (
        expected_tp,
        expected_cat,
    )


def test_normalize_hcad_permit_row_handles_relocations_omitted_imps_and_null_dates() -> None:
    # Genuine Type 15 relocation emits reloc_meta
    p_reloc, reloc_meta = normalize_hcad_permit_row(
        "555001",
        "MOVE SFD ONTO LOT",
        "15",
        "Relocation / Bldg Move",
        "04/15/2019",
    )
    assert p_reloc == {
        "id": "555001",
        "dt": "2019-04-15",
        "cat": "reloc",
        "tp": "Moved / Relocated Structure",
        "d": "MOVE SFD ONTO LOT",
    }
    assert reloc_meta == {
        "reason": "City of Houston Moved Structure Permit",
        "note": "2019-04-15: MOVE SFD ONTO LOT (Permit #555001)",
        "permit_id": "555001",
        "permit_date": "2019-04-15",
    }

    # Type 15 with OMITTED IMPS / OMITTED IMPROVEMENTS suppresses reloc_meta and classifies as 'add'
    p_omit, omit_meta = normalize_hcad_permit_row(
        "555002",
        "OMITTED IMPS \u2014 GARAGE APT",
        "15",
        "Relocation / Bldg Move",
        "12/30/1899",
    )
    assert omit_meta is None
    assert p_omit == {
        "id": "555002",
        "dt": "",
        "cat": "add",
        "tp": "HCAD Omitted Improvement Assessment",
        "d": "OMITTED IMPS - GARAGE APT",
    }

    p_omit_spelled, omit_spelled_meta = normalize_hcad_permit_row(
        "0",
        "2021 OMITTED IMPROVEMENTS",
        "15",
        "Relocation / Bldg Move",
        "01/01/2022",
    )
    assert omit_spelled_meta is None
    assert p_omit_spelled["cat"] == "add"
    assert p_omit_spelled["tp"] == "HCAD Omitted Improvement Assessment"

    # Duplicate dscr == pdesc preserves single clean description
    p_dup, dup_meta = normalize_hcad_permit_row(
        "555003",
        "Additions or Remodeling",
        "11",
        "Additions or Remodeling",
        "07/22/2025",
    )
    assert dup_meta is None
    assert p_dup["tp"] == "Additions or Remodeling"
    assert p_dup["cat"] == "remodel"
    assert p_dup["d"] == "Additions or Remodeling"

    # Pass-2 word-boundary & whole-house demolition guards ("SPACE" != "SPA", house+pool stays "demo")
    assert classify_hcad_permit_category_and_label(
        "07", "Extra Features (OB&Y)", "COVERED PARKING SPACE"
    ) == ("Outbuilding / Yard Feature (OB&Y)", "aux")
    assert classify_hcad_permit_category_and_label(
        "13", "Residential Demolition", "DEMO LIVING SPACE"
    ) == ("Residential Demolition", "demo")
    assert classify_hcad_permit_category_and_label(
        "13", "Residential Demolition", "DEMO HOUSE AND SWIMMING POOL"
    ) == ("Residential Demolition", "demo")
    assert classify_hcad_permit_category_and_label(
        "13", "Residential Demolition", "DEMO SFD & POOL"
    ) == ("Residential Demolition", "demo")


def test_repair_permit_shard_categories_hermetic_dry_run_vs_apply_and_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_dir = tmp_path / "cache"
    output_dir = tmp_path / "data"
    permits_dir = output_dir / "permits"
    cache_dir.mkdir(parents=True)
    permits_dir.mkdir(parents=True)

    # Create synthetic Real_acct_owner.zip -> permits.txt
    permits_tsv_lines = [
        "acct\tid\tdscr\tpermit_type\tpermit_tp_descr\tissue_date",
        "0620950000004\t2037787\tONE-STOP REMODEL, SINGLE FAMILY RESIDENTIAL\t11\tAdditions or Remodeling\t07/22/2025",
        "0620950000004\t1138473\tRESIDENTIAL REPAIRS AS PER SPEC LIST\t12\tRepairs (Foundation / GM)\t03/07/2008",
        "0620950000004\tCOH-LIVE-1\tLIVE COH FEED ROW\t12\tRepairs (Foundation / GM)\t02/01/2026",
        "0620000000002\t\tDEMO RES/SEWER DISC\t13\tResidential Demolition\t09/10/2021",
        "0620000000002\t\t\t09\tNew Dwelling - no permit\t08/01/2019",
        "0620000000003\t12345678\t2ND STORY ADDITION\t11\tAdditions or Remodeling\t05/10/2024",
        "0620000000003\t12345678\tGUNITE SWIMMING POOL\t07\tExtra Features (OB&Y)\t05/10/2024",
        "0620000000003\t0\tFIRST ZERO ID ROW\t10\tNew Dwelling - permit\t01/01/2020",
        "0620000000003\t0\tSECOND ZERO ID ROW\t12\tRepairs (Foundation / GM)\t06/15/2023",
        "0629999999999\t888888\tCONFIDENTIAL ROW\t10\tNew Dwelling - permit\t01/10/2024",
    ]
    with zipfile.ZipFile(cache_dir / "Real_acct_owner.zip", "w") as zf:
        zf.writestr("permits.txt", ("\r\n".join(permits_tsv_lines) + "\r\n").encode("latin-1"))

    manifest_path = output_dir / "pmtiles_manifest.json"
    manifest_path.write_text(
        json.dumps({"version": 3, "cache_bust": "20261109a"}),
        encoding="utf-8",
    )

    # Shard 062: contains stale categories, live p.src rows, top-level deed "d", and a conf:1 shield
    shard_062_initial = {
        "0620950000004": {
            "yr": 1920,
            "d": [{"dt": "2018-06-01", "g": "JANE DOE", "f": "RP-2018-100"}],
            "p": [
                {
                    "id": "2037787",
                    "dt": "2025-07-22",
                    "cat": "repair",
                    "tp": "Fire / Storm Damage Repair",
                    "d": "ONE-STOP REMODEL, SINGLE FAMILY RESIDENTIAL",
                },
                {
                    "id": "1138473",
                    "dt": "2008-03-07",
                    "cat": "other",
                    "tp": "Repairs (Foundation / GM)",
                    "d": "RESIDENTIAL REPAIRS AS PER SPEC LIST",
                },
                {
                    "id": "COH-LIVE-1",
                    "dt": "2026-02-01",
                    "cat": "demo",
                    "tp": "Stage 2 Structural Building Demolition Permit",
                    "d": "LIVE COH FEED ROW",
                    "src": "coh_live",
                },
                {
                    "id": "DSHS-1",
                    "dt": "2026-01-15",
                    "cat": "asbestos",
                    "tp": "Asbestos Abatement Notification",
                    "d": "RACM REMOVAL",
                    "src": "tx_dshs",
                },
            ],
        },
        "0620000000002": {
            "yr": 1940,
            "p": [
                {
                    "id": "",
                    "dt": "2021-09-10",
                    "cat": "repair",
                    "tp": "General Repair / Foundation",
                    "d": "DEMO RES/SEWER DISC",
                },
                {
                    "id": "CARRIED-1",
                    "dt": "2010-04-01",
                    "cat": "other",
                    "tp": "New Dwelling - permit",
                    "d": "CUSTOM HOME",
                },
                {
                    "id": "",
                    "dt": "2019-08-01",
                    "cat": "add",
                    "tp": "Legacy Stale Category",
                    "d": "New Dwelling - no permit",
                },
            ],
        },
        "0620000000003": {
            "yr": 1938,
            "p": [
                {
                    "id": "12345678",
                    "dt": "2024-05-10",
                    "cat": "other",
                    "tp": "Additions or Remodeling",
                    "d": "2ND STORY ADDITION",
                },
                {
                    "id": "12345678",
                    "dt": "2024-05-10",
                    "cat": "other",
                    "tp": "Extra Features (OB&Y)",
                    "d": "GUNITE SWIMMING POOL",
                },
                {
                    "id": "0",
                    "dt": "2022-03-01",
                    "cat": "other",
                    "tp": "Addtns, Remodels, Lease Space",
                    "d": "UNMATCHED ZERO ID CARRIED ROW",
                },
            ],
        },
        "0629999999999": {
            "conf": 1,
            "p": [
                {
                    "id": "888888",
                    "dt": "2024-01-10",
                    "cat": "other",
                    "tp": "Unchanged Confidential",
                    "d": "CONFIDENTIAL ROW",
                }
            ],
        },
    }
    # Shard 077: already clean (must not be rewritten)
    shard_077_clean = {
        "0770000000001": {
            "yr": 1950,
            "p": [
                {
                    "id": "COH-WK-9",
                    "dt": "2026-03-01",
                    "cat": "remodel",
                    "tp": "Residential Remodel",
                    "d": "WEEKLY FEED",
                    "src": "coh_weekly",
                }
            ],
        }
    }

    shard_062_path = permits_dir / "permits_062.json.gz"
    shard_077_path = permits_dir / "permits_077.json.gz"
    write_deterministic_gzip_json_shard(shard_062_path, shard_062_initial)
    write_deterministic_gzip_json_shard(shard_077_path, shard_077_clean)
    bytes_062_before = shard_062_path.read_bytes()

    # 1. Dry-run reports exact deltas without mutating disk shards or manifest
    dry_stats = repair_permit_shard_categories(
        cache_dir=cache_dir,
        output_dir=output_dir,
        tax_year=2026,
        apply=False,
        cache_bust="20261109z",
    )
    assert dry_stats == {
        "apply": False,
        "shards_scanned": 2,
        "shards_modified": 1,
        "accounts_updated": 3,
        "permits_reclassified": 8,
        "confidential_accounts_skipped": 1,
    }
    assert shard_062_path.read_bytes() == bytes_062_before
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["cache_bust"] == "20261109a"
    dry_summary = format_repair_permit_categories_cli_summary(dry_stats, elapsed_s=1.2)
    assert "DRY-RUN" in dry_summary

    # 2. Apply via CLI mutates dirty shard, preserves shields/p.src/deeds, and updates local manifest
    cli.main([
        "--mode",
        "repair-permit-categories",
        "--cache-dir",
        str(cache_dir),
        "--output-dir",
        str(output_dir),
        "--tax-year",
        "2026",
        "--apply",
        "--cache-bust",
        "20261109z",
    ])
    cli_out = capsys.readouterr().out
    assert "APPLIED" in cli_out
    assert "Permit rows reclassified        : 8" in cli_out

    repaired_062 = read_json_or_gzip_path(shard_062_path)
    algregg = repaired_062["0620950000004"]
    assert algregg["d"] == [{"dt": "2018-06-01", "g": "JANE DOE", "f": "RP-2018-100"}]
    assert algregg["p"][0]["tp"] == "Additions or Remodeling"
    assert algregg["p"][0]["cat"] == "remodel"
    assert algregg["p"][1]["tp"] == "Repairs (Foundation / General Maintenance)"
    assert algregg["p"][1]["cat"] == "repair"
    assert algregg["p"][2] == shard_062_initial["0620950000004"]["p"][2]
    assert algregg["p"][3] == shard_062_initial["0620950000004"]["p"][3]

    acct2 = repaired_062["0620000000002"]
    assert acct2["p"][0]["tp"] == "Residential Demo / Sewer Disconnect"
    assert acct2["p"][0]["cat"] == "demo"
    assert acct2["p"][1]["tp"] == "New Residential Construction"
    assert acct2["p"][1]["cat"] == "new"
    assert acct2["p"][2]["tp"] == "New Dwelling (No Municipal Permit)"
    assert acct2["p"][2]["cat"] == "new"

    acct3 = repaired_062["0620000000003"]
    assert acct3["p"][0]["tp"] == "Residential Addition"
    assert acct3["p"][0]["cat"] == "add"
    assert acct3["p"][1]["tp"] == "Swimming Pool / Spa"
    assert acct3["p"][1]["cat"] == "aux"
    # Placeholder id="0" on an unmatched date must not cross-wire to 2020 Type 10 ("new")
    assert acct3["p"][2]["tp"] == "Commercial Addition / Remodel"
    assert acct3["p"][2]["cat"] == "remodel"

    # Confidential shield preserved verbatim
    assert repaired_062["0629999999999"] == shard_062_initial["0629999999999"]
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["cache_bust"] == "20261109z"

    # 3. Second apply run is strictly idempotent (0 shards modified)
    idempotent_stats = repair_permit_shard_categories(
        cache_dir=cache_dir,
        output_dir=output_dir,
        tax_year=2026,
        apply=True,
    )
    assert idempotent_stats["shards_modified"] == 0
    assert idempotent_stats["permits_reclassified"] == 0

    # 4. Missing Real_acct_owner.zip raises FileNotFoundError
    with pytest.raises(FileNotFoundError, match="Real_acct_owner.zip"):
        repair_permit_shard_categories(
            cache_dir=tmp_path / "missing_cache",
            output_dir=output_dir,
        )


def test_live_permits_062_shard_0620950000004_and_152_shard_count() -> None:
    manifest = json.loads((DATA_DIR / "pmtiles_manifest.json").read_text(encoding="utf-8"))
    permits_dir = DATA_DIR / "permits"
    shard_files = sorted(permits_dir.glob("permits_*.json.gz"))
    assert len(shard_files) == len(manifest["permit_shard_prefixes"])

    with gzip.open(permits_dir / "permits_062.json.gz", "rt", encoding="utf-8") as fh:
        shard_062 = json.load(fh)

    algregg = shard_062["0620950000004"]
    by_id = {row["id"]: row for row in algregg["p"]}
    assert by_id["2037787"]["tp"] == "Additions or Remodeling"
    assert by_id["2037787"]["cat"] == "remodel"
    assert by_id["1138473"]["tp"] == "Repairs (Foundation / General Maintenance)"
    assert by_id["1138473"]["cat"] == "repair"


@pytest.mark.parametrize(
    ("ptype", "pdesc", "echoed_dscr", "expected_tp", "expected_cat"),
    [
        ("11", "Additions or Remodeling", "Additions or Remodeling", "Additions or Remodeling", "remodel"),
        ("11", "", "Additions or Remodeling", "Additions or Remodeling", "remodel"),
        ("31", "Addtns, Remodels, Lease Space", "Addtns, Remodels, Lease Space", "Commercial Addition / Remodel", "remodel"),
        ("31", "", "Addtns, Remodels, Lease Space", "Commercial Addition / Remodel", "remodel"),
        ("41", "Rep, Addtn, Rmdl, Fire Dam", "Rep, Addtn, Rmdl, Fire Dam", "Repair / Addition / Remodel", "repair"),
        ("41", "", "Rep, Addtn, Rmdl, Fire Dam", "Repair / Addition / Remodel", "repair"),
    ],
)
def test_echoed_hcad_type_descriptions_do_not_trigger_false_refinements(
    ptype: str,
    pdesc: str,
    echoed_dscr: str,
    expected_tp: str,
    expected_cat: str,
) -> None:
    tp, cat = classify_hcad_permit_category_and_label(ptype, pdesc, echoed_dscr)
    assert tp == expected_tp
    assert cat == expected_cat


def test_cross_language_valid_permit_cat_codes_and_css_parity() -> None:
    assert len(_HCAD_TP_DESCR_TO_PTYPE) == 23
    for mapped_ptype in _HCAD_TP_DESCR_TO_PTYPE.values():
        assert mapped_ptype in PERMIT_TYPE_CATEGORY

    js_module_url = (REPO_ROOT / "app" / "js" / "permitPlainEnglish.js").as_uri()
    proc = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            f"import {{ VALID_PERMIT_CAT_CODES }} from {json.dumps(js_module_url)}; "
            "process.stdout.write(JSON.stringify([...VALID_PERMIT_CAT_CODES].sort()));",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    js_codes = set(json.loads(proc.stdout))
    assert js_codes == set(VALID_PERMIT_CAT_CODES)

    css_text = (REPO_ROOT / "app" / "styles" / "index.css").read_text(encoding="utf-8")
    for code in VALID_PERMIT_CAT_CODES:
        if code == "other":
            continue
        assert f".permit-cat-badge.cat-{code}" in css_text


