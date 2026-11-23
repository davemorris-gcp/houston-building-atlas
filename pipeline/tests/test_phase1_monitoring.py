from __future__ import annotations

import http.server
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import typing
from typing import Any

import pytest

from atlas_pipeline import backup_workspace_state as bws
from atlas_pipeline import build_ai_companion as bac
from atlas_pipeline import fetch_live_permits as flp
from atlas_pipeline import fetch_plat_and_watchlist as fpw
from atlas_pipeline import migrate_phase1_monitoring as mpm
from atlas_pipeline import parcel_monitoring_model as pmm
from atlas_pipeline import public_bundle_gate as pbg
from atlas_pipeline import setup_admin_sheet as sas

REPO_ROOT = Path(__file__).resolve().parents[2]
VECTORS_PATH = REPO_ROOT / "tests" / "fixtures" / "phase1_monitoring_parity_vectors.json"


def test_ut1_ut2_ut3_tst1_shared_json_vectors_and_live_js_python_byte_parity() -> None:
    """UT-1, UT-2, UT-3 & TST-1: Verify 100% parity between Python and Node JS models on shared JSON vectors."""
    suite = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
    areas_by_id = suite["areasById"]

    py_results: dict[str, Any] = {
        "attention": {},
        "normalizedParcels": {},
        "chicagoDates": {},
        "historical": {},
        "campusLookups": [],
        "signalCases": {},
        "legacy": {},
    }

    for vec in suite["attentionVectors"]:
        att = pmm.compute_attention_level(
            vec["parcel"],
            as_of_date_cst=vec["asOfDateCst"],
            next_hearing_date_cst=vec["nextHearingDateCst"],
            areas_by_id=areas_by_id,
        )
        assert att == vec["expectedAttention"], f"Vector {vec['id']} failed in Python: {att}"
        expl = pmm.explain_attention_level(
            vec["parcel"],
            as_of_date_cst=vec["asOfDateCst"],
            next_hearing_date_cst=vec["nextHearingDateCst"],
            areas_by_id=areas_by_id,
        )
        assert "\u2014" not in expl and "\u2013" not in expl
        py_results["attention"][vec["id"]] = att
        py_results["normalizedParcels"][vec["id"]] = pmm.normalize_parcel_record(
            vec["parcel"],
            as_of_date_cst=vec["asOfDateCst"],
            areas_by_id=areas_by_id,
        )

    for dv in suite["chicagoDateVectors"]:
        ymd = pmm.to_chicago_calendar_date(dv["input"])
        assert ymd == dv["expectedChicagoYmd"]
        py_results["chicagoDates"][dv["input"]] = ymd

    for hv in suite["historicalBaselineVectors"]:
        is_hist = pmm.evaluate_historical_baseline_event(
            filing_date=hv["filingDate"],
            parcel_added_at=hv["parcelAddedAt"],
            as_of_date_cst=hv["asOfDateCst"],
        )
        assert is_hist is hv["expectedHistorical"]
        py_results["historical"][hv["id"]] = is_hist

    campus_idx = pmm.build_campus_parcel_index(suite["campusAndSignalVectors"]["parcels"])
    for lk in suite["campusAndSignalVectors"]["lookups"]:
        q = lk["query"]
        resolved = pmm.resolve_canonical_parcel_id(
            hcad_ids=q.get("hcadIds") or [],
            app_no=q.get("appNo") or "",
            address=q.get("address") or "",
            campus_index=campus_idx,
        )
        assert resolved == lk["expectedParcelId"]
        py_results["campusLookups"].append(resolved)

    for sc in suite["campusAndSignalVectors"]["signalCases"]:
        flags = pmm.detect_parcel_signal_flags(
            owner_of_record=sc["ownerOfRecord"],
            party_names=sc["partyNames"],
            land_use_class=sc["landUseClass"],
            event_kinds=sc["eventKinds"],
        )
        assert flags == sc["expectedFlags"]
        py_results["signalCases"][sc["id"]] = flags

    for lv in suite["legacyCompatibilityVectors"]:
        mig = pmm.migrate_legacy_tier_to_parcel_fields(
            lv["legacy"],
            is_watchlist=lv["isWatchlist"],
            atlas_known_high=lv["atlasKnownHigh"],
        )
        for field_k, expected_v in lv["expectedMigrated"].items():
            assert mig[field_k] == expected_v, f"{lv['id']}.{field_k}"
        derived = pmm.derive_legacy_tier_from_parcel(mig)
        assert derived == lv["expectedDerivedTier"]
        py_results["legacy"][lv["id"]] = {"migrated": mig, "derived": derived}

    # Execute Node subprocess against the identical JSON vector file and assert byte-for-byte equality
    node_script = """
import fs from 'node:fs';
import {
  buildCampusParcelIndex,
  computeAttentionLevel,
  deriveLegacyTierFromParcel,
  detectParcelSignalFlags,
  evaluateHistoricalBaselineEvent,
  migrateLegacyTierToParcelFields,
  normalizeParcelRecord,
  resolveCanonicalParcelId,
  toChicagoCalendarDate,
} from './app/js/parcelMonitoringModel.js';

const suite = JSON.parse(fs.readFileSync('./tests/fixtures/phase1_monitoring_parity_vectors.json', 'utf8'));
const out = {
  attention: {},
  normalizedParcels: {},
  chicagoDates: {},
  historical: {},
  campusLookups: [],
  signalCases: {},
  legacy: {},
};
for (const vec of suite.attentionVectors) {
  out.attention[vec.id] = computeAttentionLevel(vec.parcel, {
    asOfDateCst: vec.asOfDateCst,
    nextHearingDateCst: vec.nextHearingDateCst,
    areasById: suite.areasById,
  });
  out.normalizedParcels[vec.id] = normalizeParcelRecord(vec.parcel, {
    asOfDateCst: vec.asOfDateCst,
    areasById: suite.areasById,
  });
}
for (const dv of suite.chicagoDateVectors) {
  out.chicagoDates[dv.input] = toChicagoCalendarDate(dv.input);
}
for (const hv of suite.historicalBaselineVectors) {
  out.historical[hv.id] = evaluateHistoricalBaselineEvent({
    filingDate: hv.filingDate,
    parcelAddedAt: hv.parcelAddedAt,
    asOfDateCst: hv.asOfDateCst,
  });
}
const cIdx = buildCampusParcelIndex(suite.campusAndSignalVectors.parcels);
for (const lk of suite.campusAndSignalVectors.lookups) {
  out.campusLookups.push(resolveCanonicalParcelId(lk.query, cIdx));
}
for (const sc of suite.campusAndSignalVectors.signalCases) {
  out.signalCases[sc.id] = detectParcelSignalFlags(sc);
}
for (const lv of suite.legacyCompatibilityVectors) {
  const migrated = migrateLegacyTierToParcelFields(lv.legacy, {
    isWatchlist: lv.isWatchlist,
    atlasKnownHigh: lv.atlasKnownHigh,
  });
  out.legacy[lv.id] = { migrated, derived: deriveLegacyTierFromParcel(migrated) };
}
process.stdout.write(JSON.stringify(out));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    js_results = json.loads(proc.stdout)
    assert js_results == py_results


def test_ut3_migration_compiler_281_plat_reviews_and_97_watchlist_records_conservation() -> None:
    """UT-3 & TST-2: Verify lossless compilation across 281 Plat Reviews + 97 Watchlist records."""
    watch_map: dict[str, Any] = {}
    plat_map: dict[str, Any] = {}
    agenda_cycles: dict[str, Any] = {
        "2026-10-16": {"items": []},
        "2026-10-29": {"items": []},
        "2026-11-12": {"items": []},
    }

    # 1. Build 97 Watchlist records:
    #    - 1 Campus assemblage (3 member HCADs)
    #    - 1 Oversized >55 KB Watchlist dossier with phPastActions + dash character to scrub/compact
    #    - 5 Soft-delete tombstones (Removed (Tier 3) / Removed (Tier 4))
    #    - 4 Unconfirmed legacy machine plat seeds (excluded cleanly)
    #    - 86 Standard confirmed Tier 1/2 Watchlist dossiers
    watch_map["WATCH_PROP:prop-campus-assemblage"] = {
        "id": "prop-campus-assemblage",
        "propertyName": "Historic Parish Campus Block",
        "address": "1015 Holman St",
        "hcadAccount": "0152300000001; 0152300000002; 0152300000003",
        "secondaryAddresses": ["3401 Main St"],
        "tier": "1",
        "tierConfirmed": True,
        "trackerStatus": "Escalate",
        "yearBuilt": 1918,
        "designation": "City Landmark",
        "neighborhood": "Greater Third Ward",
        "notes": "Human Watchlist assessment on multi-parcel campus.",
        "phPastActions": "Preservation Staff briefed parish committee.",
        "updatedAt": "2026-11-01T12:00:00Z",
    }

    dash_char = chr(0x2014)
    watch_map["WATCH_PROP:prop-oversized-dossier"] = {
        "id": "prop-oversized-dossier",
        "propertyName": f"Magnolia Brewery Complex {dash_char} Annex",
        "address": "701 Franklin St",
        "hcadAccount": "0010010000001",
        "tier": "1",
        "tierConfirmed": True,
        "trackerStatus": "Active / High Priority",
        "yearBuilt": 1912,
        "photoDataUrl": "data:image/jpeg;base64," + ("Z" * 40000),
        "notes": "Confirmed 1912 brick industrial landmark.",
        "checklist": [
            {
                "id": "chk-14",
                "title": "Houston Sold Permits",
                "status": "Flagged",
                "checks": [
                    {
                        "date": "2026-11-10",
                        "agencyRecordId": f"p-{idx}",
                        "result": f"[AUTO-SWEEP] Sewer Disconnect #{idx} " + ("W" * 350),
                    }
                    for idx in range(160)
                ],
            }
        ],
        "updatedAt": "2026-11-10T15:00:00Z",
    }

    for t_idx in range(5):
        hid = f"0880000000{t_idx:03d}"
        watch_map[f"WATCH_PROP:prop-tombstone-{t_idx}"] = {
            "id": f"prop-tombstone-{t_idx}",
            "propertyName": f"Removed Site {t_idx}",
            "address": f"{100 + t_idx} Post Cutoff Rd",
            "hcadAccount": hid,
            "tier": "3" if t_idx < 3 else "4",
            "trackerStatus": "Removed (Tier 3)" if t_idx < 3 else "Removed (Tier 4)",
            "removedFromWatchlist": True,
            "yearBuilt": 1999,
            "updatedAt": "2026-11-05T10:00:00Z",
        }

    for s_idx in range(4):
        watch_map[f"WATCH_PROP:plat-DPV-2026-100{s_idx}"] = {
            "id": f"plat-DPV-2026-100{s_idx}",
            "sourcePlatApp": f"DPV-2026-100{s_idx}",
            "address": f"{200 + s_idx} Bogus Seed Ln",
            "tier": "2",
            "tierConfirmed": False,
        }

    for w_idx in range(86):
        hid = f"0550000000{w_idx:03d}"
        watch_map[f"WATCH_PROP:prop-watch-{w_idx:03d}"] = {
            "id": f"prop-watch-{w_idx:03d}",
            "propertyName": f"Monitored Historic Property {w_idx}",
            "address": f"{1000 + w_idx} Heights Blvd",
            "hcadAccount": hid,
            "tier": "1" if w_idx % 2 == 0 else "2",
            "tierConfirmed": True,
            "trackerStatus": "Monitor",
            "yearBuilt": 1925,
            "neighborhood": "Sunnyside" if w_idx < 10 else "Montrose",
            "notes": f"Confirmed human Watchlist note #{w_idx}.",
            "updatedAt": "2026-11-08T12:00:00Z",
        }

    assert len(watch_map) == 97

    # 2. Build 281 Plat Reviews:
    #    - 2 cross-cycle reviews on member HCAD 0152300000003 of CAMPUS:HISTORIC_PARISH_CAMPUS_BLOCK
    #    - 2 cross-cycle reviews on a single non-Watchlist parcel 0621100000014 (Tier 2 -> Tier 1 upgrade)
    #    - 1 boilerplate Tier 3 pre-screen on watched parcel 0550000000000 (Watchlist Tier 1 must win!)
    #    - 8 zero-HCAD ETJ/ROW unplatted items -> UNPARCELED:* keys
    #    - 6 unconfirmed machine seeds (3 bogus 4-digit DPV-2026-* + 3 unconfirmed retracted 2026-10-16)
    #    - 262 distinct human-reviewed single-parcel plat items
    plat_map["PLAT_REV:2026-10-29:2026-1501"] = {
        "cycleKey": "2026-10-29",
        "appNo": "2026-1501",
        "review": {
            "hcad": "0152300000003",
            "address": "3401 Main St",
            "tier": "2",
            "tierConfirmed": True,
            "why": "Cycle 1 annex lot replat note.",
            "updated": "2026-10-29T14:00:00Z",
        },
    }
    plat_map["PLAT_REV:2026-11-12:2026-1601"] = {
        "cycleKey": "2026-11-12",
        "appNo": "2026-1601",
        "review": {
            "hcad": "0152300000003",
            "address": "3401 Main St",
            "tier": "1",
            "tierConfirmed": True,
            "why": "Cycle 2 variance escalation on campus corner.",
            "updated": "2026-11-12T14:00:00Z",
        },
    }

    plat_map["PLAT_REV:2026-10-29:2026-1701"] = {
        "cycleKey": "2026-10-29",
        "appNo": "2026-1701",
        "review": {
            "hcad": "0621100000014",
            "address": "1127 Key St",
            "yearBuilt": 1925,
            "tier": "2",
            "tierConfirmed": True,
            "why": "Initial Norhill bungalow screening note.",
            "updated": "2026-10-29T15:00:00Z",
        },
    }
    plat_map["PLAT_REV:2026-11-12:2026-1801"] = {
        "cycleKey": "2026-11-12",
        "appNo": "2026-1801",
        "review": {
            "hcad": "0621100000014",
            "address": "1127 Key St",
            "yearBuilt": 1925,
            "tier": "1",
            "tierConfirmed": True,
            "why": "Follow-up public hearing variance note.",
            "updated": "2026-11-12T15:00:00Z",
        },
    }

    plat_map["PLAT_REV:2026-11-12:2026-1850"] = {
        "cycleKey": "2026-11-12",
        "appNo": "2026-1850",
        "review": {
            "hcad": "0550000000000",
            "address": "1000 Heights Blvd",
            "tier": "3",
            "tierConfirmed": True,
            "why": "Later cycle low-priority note that must not downgrade Watchlist Tier 1.",
            "updated": "2026-11-12T16:00:00Z",
        },
    }

    for z_idx in range(8):
        app_no = f"2026-90{z_idx:02d}"
        agenda_cycles["2026-11-12"]["items"].append(
            {
                "app": app_no,
                "name": f"FM 529 Rural Cemetery Reserve {z_idx}",
                "type": "C2",
                "inCity": False,
            }
        )
        plat_map[f"PLAT_REV:2026-11-12:{app_no}"] = {
            "cycleKey": "2026-11-12",
            "appNo": app_no,
            "review": {
                "hcad": "",
                "address": "",
                "tier": "2",
                "tierConfirmed": True,
                "why": f"ETJ rural reserve check #{z_idx}.",
                "updated": "2026-11-12T16:30:00Z",
            },
        }

    for b_idx in range(3):
        plat_map[f"PLAT_REV:2026-10-29:DPV-2026-200{b_idx}"] = {
            "cycleKey": "2026-10-29",
            "appNo": f"DPV-2026-200{b_idx}",
            "review": {"tier": "2", "tierConfirmed": False},
        }
    for r_idx in range(3):
        plat_map[f"PLAT_REV:2026-10-16:2026-300{r_idx}"] = {
            "cycleKey": "2026-10-16",
            "appNo": f"2026-300{r_idx}",
            "review": {"tier": "2", "tierConfirmed": False, "humanEdited": False},
        }

    for p_idx in range(262):
        app_no = f"2026-{4000 + p_idx}"
        hid = f"0330000000{p_idx:03d}"
        plat_map[f"PLAT_REV:2026-11-12:{app_no}"] = {
            "cycleKey": "2026-11-12",
            "appNo": app_no,
            "review": {
                "hcad": hid,
                "address": f"{3000 + p_idx} Westheimer Rd",
                "yearBuilt": 1930 if p_idx < 100 else 1998,
                "tier": "2" if p_idx < 100 else "4",
                "tierConfirmed": True,
                "why": (
                    f"Intact historic storefront #{p_idx}"
                    if p_idx < 100
                    else "Post-1976 strip center; irrelevant."
                ),
                "updated": "2026-11-12T17:00:00Z",
            },
        }

    assert len(plat_map) == 281

    bundle, report = mpm.compile_migration_bundle(
        legacy_plat_reviews=plat_map,
        legacy_watch_properties=watch_map,
        plat_agenda_doc={"cycles": agenda_cycles},
        as_of_date_cst="2026-11-23",
        now_iso="2026-11-23T12:00:00Z",
    )

    # Conservation assertions across all 281 Plat Reviews and 97 Watchlist records
    assert report["inputWatchPropertyCount"] == 97
    assert report["excludedWatchMachineSeedCount"] == 4
    assert report["migratedWatchTombstoneCount"] == 5
    assert report["migratedActiveOrArchivedWatchCount"] == 88
    assert report["inputPlatReviewCount"] == 281
    assert report["excludedPlatMachineSeedCount"] == 6
    assert report["migratedPlatReviewCount"] == 275
    assert report["syntheticUnparceledCount"] == 8
    assert report["campusAssemblageCount"] == 1
    assert report["outputStaffStrategyCount"] == 1

    parcels_by_id = {p["parcel_id"]: p for p in bundle["parcels"]}
    decisions_by_id = {d["parcel_id"]: d for d in bundle["decisions"]}
    dossiers_by_id = {w["watch_property_id"]: w for w in bundle["watchlistDossiers"]}

    # Member HCAD 0152300000003 rolled up into CAMPUS:HISTORIC_PARISH_CAMPUS_BLOCK without duplicate row
    assert "0152300000003" not in parcels_by_id
    campus_p = parcels_by_id["CAMPUS:HISTORIC_PARISH_CAMPUS_BLOCK"]
    assert campus_p["significance_level"] == "high"
    campus_dec = decisions_by_id["CAMPUS:HISTORIC_PARISH_CAMPUS_BLOCK"]
    assert "Human Watchlist assessment" in campus_dec["screening_notes"]
    assert "Cycle 1 annex lot replat note" in campus_dec["screening_notes"]
    assert "Cycle 2 variance escalation" in campus_dec["screening_notes"]

    # Multi-cycle Plat Review upgrade (Tier 2 -> Tier 1) on 0621100000014 preserves both notes
    key_p = parcels_by_id["0621100000014"]
    assert key_p["significance_level"] == "high"
    assert "Initial Norhill bungalow" in decisions_by_id["0621100000014"]["screening_notes"]
    assert "Follow-up public hearing variance" in decisions_by_id["0621100000014"]["screening_notes"]

    # Human Watchlist Tier 1 takes precedence over later cycle Tier 3 review on 0550000000000
    assert parcels_by_id["0550000000000"]["significance_level"] == "high"
    assert "Later cycle low-priority note" in decisions_by_id["0550000000000"]["screening_notes"]

    # Tombstones remain closed & suppressed
    tomb_p = parcels_by_id["0880000000000"]
    assert tomb_p["advocacy_status"] == "closed"
    assert tomb_p["outcome"] == "not_relevant"
    assert tomb_p["attention_level"] == "record"

    # Oversized >55 KB dossier compacted cleanly <= 48,000 chars as valid JSON with zero dashes
    compacted_str = dossiers_by_id["prop-oversized-dossier"]["dossier_json"]
    assert len(compacted_str) <= 48000
    assert "\u2014" not in compacted_str and "\u2013" not in compacted_str
    compacted_obj = json.loads(compacted_str)
    assert compacted_obj["photoDataUrl"] == ""
    assert compacted_obj["truncatedCheckCount"] > 0

    # Idempotency check: second compilation produces identical bundle & report
    bundle2, report2 = mpm.compile_migration_bundle(
        legacy_plat_reviews=plat_map,
        legacy_watch_properties=watch_map,
        plat_agenda_doc={"cycles": agenda_cycles},
        as_of_date_cst="2026-11-23",
        now_iso="2026-11-23T12:00:00Z",
    )
    assert bundle2 == bundle
    assert report2 == report

    # Also verify legacy CSV parser isolated in migrate_phase1_monitoring.py
    csv_sample = (
        'status,hcad_num,source_type,source_citation\n'
        'Approved,PLAT_REV:2026-11-12:2026-5555,PLAT_MONITOR_REVIEW,"{""tier"":""1"",""why"":""Saved from CSV""}"\n'
    )
    parsed_csv = mpm.parse_legacy_plat_sync_csv(csv_sample)
    assert "PLAT_REV:2026-11-12:2026-5555" in parsed_csv["platReviews"]


def test_ut4_priority_and_candidate_area_seeding_and_designated_parcel_protection() -> None:
    """UT-4: Verify deterministic 5 Priority Areas + Candidate Areas seeding and protection levels."""
    overlays = {
        "historic_districts": {
            "features": [
                {"properties": {"name": "Houston Heights Historic District East", "dev_period_end": 1930}}
            ]
        },
        "landmarks": {
            "features": [
                {
                    "properties": {
                        "hcad_num": "0152300000010",
                        "name": "Julia Ideson Building",
                        "is_protected": True,
                        "address": "500 McKinney St",
                        "year_built": 1926,
                    }
                }
            ]
        },
        "nrhp_properties": {
            "features": [
                {
                    "properties": {
                        "hcad_num": "0420090000055",
                        "name": "Eldorado Ballroom",
                        "address": "2310 Elgin St",
                        "year_built": 1939,
                    }
                }
            ]
        },
        "good_brick_awards": {
            "features": [
                {
                    "properties": {
                        "hcad_num": "0621100000014",
                        "name": "Key Street Bungalow",
                        "address": "1127 Key St",
                        "year_built": 1925,
                    }
                }
            ]
        },
        "red_book_1915": {
            "features": [
                {
                    "properties": {
                        "hcad_num": "0451110000012",
                        "name": "1915 Red Book Residence",
                        "address": "910 Lovett Blvd",
                        "year_built": 1910,
                    }
                }
            ]
        },
    }
    hpo_surveys = {
        "districts": [
            {
                "name": "Glenbrook Valley",
                "period_of_significance_start": 1953,
                "period_of_significance_end": 1962,
            }
        ]
    }
    areas = mpm.seed_priority_and_candidate_areas(
        overlays_data=overlays,
        hpo_surveys_data=hpo_surveys,
        updated_at="2026-11-23T00:00:00Z",
    )
    area_ids = {a["area_id"] for a in areas}
    for expected_priority in (
        "area:priority:sunnyside",
        "area:priority:acres_homes",
        "area:priority:fifth_ward",
        "area:priority:independence_heights",
        "area:priority:third_ward",
    ):
        assert expected_priority in area_ids

    known = mpm.collect_atlas_known_high_parcels(
        overlays_data=overlays,
        curated_overrides={
            "0770010000099": {
                "hcad_num": "0770010000099",
                "contributing": "City Landmark",
                "building_name": "Curated Landmark",
                "year_built": 1920,
            }
        },
    )
    assert "0152300000010" in known
    assert "0420090000055" in known
    assert "0621100000014" in known
    assert "0770010000099" in known
    assert pmm.classify_protection_level("Protected Landmark") == "binding"
    assert pmm.classify_protection_level("Houston Heights Historic District East") == "binding"
    assert pmm.classify_protection_level("City Landmark") == "limited"
    assert pmm.classify_protection_level("National Register") == "limited"
    assert pmm.classify_protection_level("Good Brick Award") == "none"


def test_ut5_public_bundle_gate_positive_and_negative_leak_assertions(tmp_path: Path) -> None:
    """UT-5: Verify public artifact sanitizers and PublicBundleGate catch all leak vectors."""
    # Live repository must pass with zero violations
    assert pbg.validate_public_bundle_zero_leaks(REPO_ROOT) == []

    # Construct a synthetic repo tree with planted leaks and prove validate_public_bundle_zero_leaks flags every one
    data_dir = tmp_path / "app" / "public" / "data"
    ai_dir = tmp_path / "app" / "public" / "ai-companion"
    bk_dir = tmp_path / "pipeline" / "backups"
    data_dir.mkdir(parents=True)
    ai_dir.mkdir(parents=True)
    bk_dir.mkdir(parents=True)

    dirty_plat = {
        "cycleKey": "2026-11-12",
        "reviews": {"2026-1001": {"tier": "1", "why": "SECRET_STAFF_REVIEW"}},
        "items": [
            {
                "app": "2026-1001",
                "name": "Test Subdiv",
                "atlas": {"suggestedTier": "1", "overrideTier": "1", "earliestYearBuilt": 1925},
                "_c": {"group": "check", "overriddenFromOut": True, "flags": ["pre1976", "dev"]},
            }
        ],
        "cycles": {},
    }
    (data_dir / "plat_agenda_monitor.json").write_text(json.dumps(dirty_plat), encoding="utf-8")
    (data_dir / "property_watchlist.json").write_text(
        json.dumps({"watchedProperties": [{"id": "prop-secret", "notes": "CONFIDENTIAL_DOSSIER"}]}),
        encoding="utf-8",
    )
    (data_dir / "monitor_activity_log.json").write_text(
        json.dumps(
            {
                "events": [{"id": "staff:1", "stream": "staff", "title": "Secret"}],
                "summary": {"byStream": {"watchlist": 2, "staff": 1, "agenda": 0, "permits": 0}},
            }
        ),
        encoding="utf-8",
    )
    (bk_dir / "workspace_cloud_backup.json").write_text(
        json.dumps({"platReviews": {"x": 1}, "watchProperties": {"y": 2}, "approvedOverrides": {}}),
        encoding="utf-8",
    )
    (tmp_path / "app" / "llms-full.txt").write_text(
        "## Active Investigative Property Watchlist\n| Secret Property |",
        encoding="utf-8",
    )

    violations = pbg.validate_public_bundle_zero_leaks(tmp_path)
    assert len(violations) >= 5

    # Now sanitize every artifact using Phase 1 sanitizers and prove violations drop to 0!
    clean_plat = pbg.sanitize_public_plat_agenda_dataset(dirty_plat)
    assert clean_plat["reviews"] == {}
    assert "overrideTier" not in clean_plat["items"][0]["atlas"]
    assert "dev" not in clean_plat["items"][0]["_c"]["flags"]
    (data_dir / "plat_agenda_monitor.json").write_text(json.dumps(clean_plat), encoding="utf-8")
    (data_dir / "property_watchlist.json").write_text(
        json.dumps(pbg.build_public_watchlist_telemetry_stub({"lastAutoSweepAt": "2026-11-23T12:00:00Z"})),
        encoding="utf-8",
    )
    (data_dir / "monitor_activity_log.json").write_text(
        json.dumps(
            pbg.sanitize_public_monitor_activity_log(
                json.loads((data_dir / "monitor_activity_log.json").read_text(encoding="utf-8"))
            )
        ),
        encoding="utf-8",
    )
    (bk_dir / "workspace_cloud_backup.json").write_text(
        json.dumps(bws.compile_workspace_backup_payload(approved_edits_csv="status,hcad_num\n")),
        encoding="utf-8",
    )
    (tmp_path / "app" / "llms-full.txt").write_text(
        "# Houston Building Atlas Public Reference\nPublic permit and agenda feeds only.\n",
        encoding="utf-8",
    )
    assert pbg.validate_public_bundle_zero_leaks(tmp_path) == []

    # Verify build_ai_companion.build_llms_full_txt_content omits private Watchlist dossiers even if passed dirty input
    llms_md = bac.build_llms_full_txt_content(
        base_url="https://davemorris-gcp.github.io/houston-building-atlas/",
        data_dir=REPO_ROOT / "app" / "public" / "data",
    )
    assert "CONFIDENTIAL_DOSSIER" not in llms_md
    assert "\u2014" not in llms_md and "\u2013" not in llms_md


def test_ut6_workbook_write_range_and_cell_compaction_guards() -> None:
    """UT-6 & SA-2: Verify assert_safe_workbook_write_ranges blocks B6 passkey and Pending_Submissions!S:V."""
    # Safe split-range writes (A:R and W) pass
    sas.assert_safe_workbook_write_ranges(
        [
            {"range": "Pending_Submissions!A2:R2", "values": [["Approved"]]},
            {"range": "Pending_Submissions!W2", "values": [[""]]},
            {"range": "Parcels!A1:AF10", "values": [[]]},
        ]
    )

    # Overlapping Pending_Submissions!S:V ARRAYFORMULA columns must raise ValueError
    with pytest.raises(ValueError, match="Pending_Submissions"):
        sas.assert_safe_workbook_write_ranges(
            [{"range": "Pending_Submissions!A2:W2", "values": [["bad contiguous write"]]}]
        )

    with pytest.raises(ValueError, match="Pending_Submissions"):
        sas.assert_safe_workbook_write_ranges(
            [{"range": "Pending_Submissions!S2:V2", "values": [["formula clobber"]]}]
        )

    # Overwriting live Instructions_&_Apps_Script!B6 passkey without allow_baseline_b6 must raise ValueError
    with pytest.raises(ValueError, match="B6"):
        sas.assert_safe_workbook_write_ranges(
            [{"range": "Instructions_&_Apps_Script!A1:D10", "values": [["clobbers B6"]]}]
        )


def test_it3_daily_sweep_pipeline_end_to_end_with_watched_entities_and_stdlib_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IT-3: Verify daily sweep pipeline against mocked Apps Script endpoints & stdlib-only Python."""
    received_rpc_calls: list[dict[str, Any]] = []

    class MockAppsScriptHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _fmt: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            csv_body = (
                "status,hcad_num,address,year_built,original_hcad_year,historic_district,"
                "contributing,source_type,source_citation,source_url,building_name,alt_names,"
                "bld_style,architect,geometry_geojson\n"
                "Approved,0621100000014,1127 KEY ST,1925,1930,Norhill,Contributing,"
                "Houston City Directory,1925 Directory,,Key Bungalow,,,Builder,\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.end_headers()
            self.wfile.write(csv_body.encode("utf-8"))

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            received_rpc_calls.append(payload)
            if payload.get("pipeline_token") != "it3-secret-pipeline-token":
                resp = {"ok": False, "authorized": False, "error": "Invalid pipeline token"}
            elif payload.get("action") == "pipeline_pull_monitoring":
                resp = {
                    "ok": True,
                    "authorized": True,
                    "role": "pipeline",
                    "parcels": [
                        {
                            "parcel_id": "0420090000001",
                            "hcad_num": "0420090000001",
                            "primary_address": "2802 Wheeler Ave",
                            "year_built": 1928,
                            "review_status": "assessed",
                            "significance_level": "high",
                            "advocacy_status": "active_advocacy",
                            "threat_stage": 3,
                            "added_at": "2026-10-15T00:00:00Z",
                        }
                    ],
                    "decisions": [
                        {
                            "decision_id": "DEC:0420090000001",
                            "parcel_id": "0420090000001",
                            "hcad_num": "0420090000001",
                            "latest_cycle_key": "2026-11-12",
                            "latest_app_no": "2026-1001",
                            "review_status": "assessed",
                            "significance_level": "high",
                            "screening_notes": "PRIVATE_HUMAN_SCREENING_NOTE",
                            "decided_at": "2026-11-12T14:00:00Z",
                        }
                    ],
                    "watchlistDossiers": [
                        {
                            "watch_property_id": "prop-wheeler",
                            "parcel_id": "0420090000001",
                            "dossier_json": json.dumps(
                                {
                                    "id": "prop-wheeler",
                                    "parcelId": "0420090000001",
                                    "propertyName": "Wheeler Duplex",
                                    "address": "2802 Wheeler Ave",
                                    "hcadAccount": "0420090000001",
                                    "tier": "1",
                                    "tierConfirmed": True,
                                    "ownerOfRecord": "WHEELER TRUST",
                                    "developer": "Bayou Teardown Builders LLC",
                                    "keyDates": {"monitoringStarted": "2026-10-15"},
                                    "notes": "PRIVATE_WATCHLIST_DOSSIER_NOTE",
                                    "checklist": [
                                        {
                                            "id": "chk-10",
                                            "title": "TDLR TABS",
                                            "status": "To Do",
                                            "checks": [
                                                {
                                                    "date": "2026-01-10",
                                                    "agencyRecordId": "tabs-old-1",
                                                    "result": "[AUTO-SWEEP] TDLR TABS old remodel",
                                                }
                                            ],
                                        },
                                        {
                                            "id": "chk-14",
                                            "title": "Houston Sold Permits",
                                            "status": "Flagged",
                                            "checks": [
                                                {
                                                    "date": "2026-11-20",
                                                    "discoveredAt": "2026-11-23T12:00:00Z",
                                                    "agencyRecordId": "coh-new-1",
                                                    "result": "[AUTO-SWEEP] Sewer Disconnect #26099111",
                                                }
                                            ],
                                        },
                                    ],
                                }
                            ),
                        }
                    ],
                    "watchedEntitiesForMatching": [
                        {
                            "entity_id": "ENT:1",
                            "entity_name": "Bayou Teardown Builders LLC",
                            "normalized_key": "BAYOU TEARDOWN BUILDERS",
                            "entity_role": "developer",
                            "active": "true",
                        },
                        {
                            "entity_id": "ENT:2",
                            "entity_name": "Inactive Legacy Firm LLC",
                            "normalized_key": "INACTIVE LEGACY FIRM",
                            "entity_role": "developer",
                            "active": "false",
                        },
                    ],
                }
            elif payload.get("action") == "pipeline_push_sweep_results":
                resp = {"ok": True, "authorized": True, "actorType": "pipeline"}
            elif payload.get("action") == "pipeline_create_drive_backup":
                resp = {
                    "ok": True,
                    "target": "drive_folder",
                    "fileName": "atlas_monitoring_backup_2026-11-23.json",
                    "prunedCount": 0,
                }
            else:
                resp = {"ok": False, "error": f"Unexpected action: {payload.get('action')}"}

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode("utf-8"))

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockAppsScriptHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    mock_url = f"http://127.0.0.1:{port}/exec"

    try:
        monkeypatch.setenv("ATLAS_PIPELINE_SYNC_TOKEN", "it3-secret-pipeline-token")
        monkeypatch.setattr(fpw, "SHEET_WEBHOOK_URL", mock_url)
        monkeypatch.setattr(bws, "SHEET_WEBHOOK_URL", mock_url)
        monkeypatch.setattr(bws, "SHEET_PUBLISHED_CSV_URL", f"{mock_url}?gid=1876369840")

        # 1. Demolished catalog in fetch_live_permits works cleanly when public property_watchlist.json has watchedProperties: []
        stub_wl_obj = pbg.build_public_watchlist_telemetry_stub({"lastAutoSweepAt": "2026-11-23T12:00:00Z"})
        demo_cat = flp.build_general_demolished_catalog(
            overrides_raw={
                "0990010000001": {
                    "hcad_num": "0990010000001",
                    "address": "500 VACANT ST",
                    "year_built": 0,
                    "suppress_only": True,
                },
                "0621100000014": {
                    "hcad_num": "0621100000014",
                    "address": "1127 KEY ST",
                    "year_built": 1925,
                    "suppress_only": False,
                },
            },
            overlays_raw={
                "lost_houston": {
                    "features": [
                        {
                            "properties": {
                                "id": "lost_kirby_mansion_1894",
                                "hcad_num": "0152300000001",
                                "name": "John Henry Kirby Mansion",
                                "year_demolished": 2026,
                            }
                        }
                    ]
                }
            },
            watchlist_raw=stub_wl_obj,
        )
        assert "0990010000001" in demo_cat
        assert "0152300000001" in demo_cat
        assert "0621100000014" not in demo_cat, "Standing historic overrides must stay visible"

        # 2. Pull monitoring state via pipeline_token + match Watched_People_And_Firms + push sweep results
        pulled = fpw.pull_cloud_sheet_sync_rows()
        assert ("2026-11-12", "2026-1001") in pulled["platReviews"]
        assert "prop-wheeler" in pulled["watchProperties"]
        assert len(pulled["watchedEntities"]) == 2

        sample_plat_dataset = {
            "cycleKey": "2026-11-12",
            "reviews": {"2026-1001": pulled["platReviews"][("2026-11-12", "2026-1001")]},
            "items": [
                {
                    "app": "2026-2222",
                    "name": "Montrose Townhomes Replat",
                    "type": "C3N",
                    "hcad": "0451110000099",
                    "developer": "Bayou Teardown Builders, L.L.C.",
                    "applicant": "Acme Platting",
                    "landUse": "Single-Family Residential",
                    "atlas": {"primaryAddress": "1410 Westheimer Rd", "owner": "JONES ESTATE"},
                    "_c": {"group": "check", "flags": ["pre1976"]},
                },
                {
                    "app": "2026-3333",
                    "name": "Inactive Entity Plat",
                    "type": "C2",
                    "hcad": "0451110000100",
                    "developer": "Inactive Legacy Firm LLC",
                    "applicant": "Acme Platting",
                    "atlas": {"primaryAddress": "1500 Westheimer Rd", "owner": "INACTIVE LEGACY FIRM LLC"},
                    "_c": {"group": "check", "flags": []},
                },
            ],
            "cycles": {},
        }
        watchlist_for_push = {
            "lastAutoSweepAt": "2026-11-23T12:05:00Z",
            "watchedProperties": list(pulled["watchProperties"].values()),
        }
        push_resp = fpw.push_sweep_results_to_workspace(
            watchlist_for_push,
            plat_dataset=sample_plat_dataset,
            watched_entities=pulled["watchedEntities"],
        )
        assert push_resp["ok"] is True

        pushed_call = next(c for c in received_rpc_calls if c.get("action") == "pipeline_push_sweep_results")
        pushed_parcels = {p["parcel_id"]: p for p in pushed_call["parcels"]}
        pushed_events = {e["event_id"]: e for e in pushed_call["events"]}

        # Active Watched_People_And_Firms matched on both Wheeler Watchlist property and plat 2026-2222, NOT on inactive entity 2026-3333!
        assert "watched_entity_match" in pushed_parcels["0420090000001"]["signal_flags"]
        assert "watched_entity_match" in pushed_parcels["0451110000099"]["signal_flags"]
        assert "0451110000100" not in pushed_parcels

        # >180d / pre-added_at TDLR event marked is_historical_baseline=True; recent Nov 20 Sewer Disconnect is active Stage 4!
        old_ev = pushed_events["EVT:chk-10:tabs-old-1:0420090000001"]
        new_ev = pushed_events["EVT:chk-14:coh-new-1:0420090000001"]
        assert old_ev["is_historical_baseline"] is True
        assert new_ev["is_historical_baseline"] is False
        assert pushed_parcels["0420090000001"]["threat_stage"] == 4

        # 3. Write sanitized public files + run stdlib-only (-S) build_monitor_activity_log.py + backup_workspace_state.py + public_bundle_gate.py
        sim_repo = tmp_path / "sim_repo"
        sim_data = sim_repo / "app" / "public" / "data"
        sim_bk = sim_repo / "pipeline" / "backups"
        sim_data.mkdir(parents=True)
        sim_bk.mkdir(parents=True)
        for fname in ("active_permit_alerts.json", "hahc_coa_tracker.json"):
            shutil.copy2(REPO_ROOT / "app" / "public" / "data" / fname, sim_data / fname)

        (sim_data / "plat_agenda_monitor.json").write_text(
            json.dumps(pbg.sanitize_public_plat_agenda_dataset(sample_plat_dataset)),
            encoding="utf-8",
        )
        (sim_data / "property_watchlist.json").write_text(
            json.dumps(pbg.build_public_watchlist_telemetry_stub(watchlist_for_push)),
            encoding="utf-8",
        )

        before_dir = tmp_path / "before_snap"
        subprocess.run(
            [
                sys.executable,
                "-S",
                str(REPO_ROOT / "pipeline" / "atlas_pipeline" / "build_monitor_activity_log.py"),
                "--save-before",
                str(before_dir),
                "--data-dir",
                str(sim_data),
            ],
            check=True,
            cwd=str(REPO_ROOT),
        )
        subprocess.run(
            [
                sys.executable,
                "-S",
                str(REPO_ROOT / "pipeline" / "atlas_pipeline" / "build_monitor_activity_log.py"),
                "--update-log",
                "--before-dir",
                str(before_dir),
                "--data-dir",
                str(sim_data),
                "--log-path",
                str(sim_data / "monitor_activity_log.json"),
                "--now-iso",
                "2026-11-23T12:10:00Z",
            ],
            check=True,
            cwd=str(REPO_ROOT),
        )

        backup_out = sim_bk / "workspace_cloud_backup.json"
        bws.run_live_backup(output_path=backup_out)
        assert any(c.get("action") == "pipeline_create_drive_backup" for c in received_rpc_calls)

        # Verify public bundle zero-leak gate passes with 0 violations and zero private strings anywhere in sim_repo!
        assert pbg.validate_public_bundle_zero_leaks(sim_repo) == []
        for fpath in [*sim_data.glob("*.json"), backup_out]:
            txt = fpath.read_text(encoding="utf-8")
            assert "PRIVATE_HUMAN_SCREENING_NOTE" not in txt
            assert "PRIVATE_WATCHLIST_DOSSIER_NOTE" not in txt
            assert "Bayou Teardown Builders" not in backup_out.read_text(encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()


def test_round18_review_regressions_concurrent_merge_campus_sweep_and_bundle_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Python merge_parcel_and_decision_concurrent parity, campus sweep rollup, and ai-companion leak gate."""
    # 1. merge_parcel_and_decision_concurrent with existing_parcel={} / existing_decision={} matches JS 1:1 (issue_099)
    pipe_res = pmm.merge_parcel_and_decision_concurrent(
        existing_parcel={},
        existing_decision={},
        patch={"significance_level": "high", "threat_stage": 4},
        actor_role="pipeline",
        now_iso="2026-11-23T12:00:00Z",
    )
    assert pipe_res["ok"] is True
    assert pipe_res["parcel"]["significance_level"] == ""
    assert pipe_res["parcel"]["threat_stage"] == 4
    assert pipe_res["decision"] == {}

    staff_res = pmm.merge_parcel_and_decision_concurrent(
        existing_parcel=None,
        existing_decision=None,
        patch={
            "parcel_id": "0420090000001",
            "review_status": "assessed",
            "significance_level": "high",
            "story_check_json": [{"step": "year_source", "checked": True}],
        },
        actor_role="staff",
        actor_label="Preservation Staff",
        now_iso="2026-11-23T12:05:00Z",
    )
    assert staff_res["ok"] is True
    assert staff_res["parcel"]["significance_level"] == "high"
    assert json.loads(staff_res["decision"]["story_check_json"])[0]["step"] == "year_source"

    screener_denied = pmm.merge_parcel_and_decision_concurrent(
        existing_parcel=staff_res["parcel"],
        existing_decision=staff_res["decision"],
        patch={"significance_level": "not_significant"},
        actor_role="screener",
    )
    assert screener_denied["ok"] is False
    assert screener_denied["forbiddenField"] == "significance_level"

    # Tombstone custom areaContext.ageRuleCutoffYear parity (issue_100)
    tomb_custom = pmm.migrate_legacy_tier_to_parcel_fields(
        {"removedFromWatchlist": True, "why": "Demolished on site", "yearBuilt": 1980},
        is_watchlist=True,
        area_context={"areaIds": ["area:custom"], "ageRuleCutoffYear": 1985},
    )
    assert tomb_custom["outcome"] == "lost"

    # 2. push_sweep_results_to_workspace resolves secondary member HCAD onto existing CAMPUS:<slug> parcel (issue_098)
    captured_pushes: list[dict[str, Any]] = []

    def fake_urlopen(req: object, timeout: float = 30.0) -> object:
        body = json.loads(req.data.decode("utf-8"))  # type: ignore[attr-defined]
        captured_pushes.append(body)

        class _Resp:
            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *_a: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"ok": true, "authorized": true, "actorType": "pipeline"}'

        return _Resp()

    monkeypatch.setenv("ATLAS_PIPELINE_SYNC_TOKEN", "r18-token")
    monkeypatch.setattr(fpw.urllib.request, "urlopen", fake_urlopen)
    assert "designations" in typing.get_type_hints(pmm.compute_public_plat_suggested_tier)
    fpw.push_sweep_results_to_workspace(
        {
            "lastAutoSweepAt": "2026-11-23T12:00:00Z",
            "watchedProperties": [
                {
                    "id": "prop-sixth-ward",
                    "parcelId": "0010010000001",
                    "hcadAccount": "0010010000001",
                    "address": "1800 Kane St",
                    "designation": "Old Sixth Ward",
                    "checklist": [
                        {
                            "id": "chk-10",
                            "title": "TDLR TABS Commercial Remodel",
                            "status": "Flagged",
                            "checks": [
                                {
                                    "date": "2026-11-20",
                                    "discoveredAt": "2026-11-23T12:00:00Z",
                                    "agencyRecordId": "tabs-r19-1",
                                    "result": "[AUTO-SWEEP] TDLR Commercial Remodel",
                                }
                            ],
                        }
                    ],
                },
                {
                    "id": "prop-12digit-baseline",
                    "parcelId": "0420090000055",
                    "hcadAccount": "0420090000055",
                    "address": "1912 Lubbock St",
                    "checklist": [
                        {
                            "id": "chk-03",
                            "title": "Harris County Clerk Deeds",
                            "status": "Complete",
                            "checks": [
                                {
                                    "date": "2024-01-10",
                                    "historicalBaseline": True,
                                    "agencyRecordId": "deed-hist-1",
                                    "result": "[AUTO-SWEEP] Historical baseline deed",
                                }
                            ],
                        }
                    ],
                },
            ],
        },
        plat_dataset={
            "cycleKey": "2026-11-12",
            "items": [
                {
                    "app": "2026-7777",
                    "name": "Parish Annex Replat",
                    "type": "C2R",
                    "hcad": "0420010000002",
                    "atlas": {"primaryAddress": "1202 Annex St", "owner": "PARISH BOARD"},
                    "_c": {"group": "check", "flags": ["pre1976"]},
                },
                {
                    "app": "2026-7788",
                    "name": "Lubbock Commercial Replat",
                    "type": "Commercial Replat",
                    "hcad": "0420090000055",
                    "developer": "ACME TEARDOWN VENTURES LLC",
                    "applicant": "FAST PLATTING INC",
                    "atlas": {"primaryAddress": "1912 Lubbock St", "owner": "SMITH FAMILY"},
                    "_c": {"group": "check", "flags": ["pre1976"]},
                },
            ],
            "cycles": {},
        },
        existing_parcels=[
            {
                "parcel_id": "CAMPUS:PARISH_ASSEMBLAGE",
                "campus_group_id": "PARISH_ASSEMBLAGE",
                "hcad_num": "0420010000001",
                "related_hcads": "0420010000001; 0420010000002",
                "protection_level": "binding",
            },
            {
                "parcel_id": "0010010000001",
                "hcad_num": "0010010000001",
                "owner_of_record": "SMITH FAMILY",
                "land_use_class": "A1",
                "designation_summary": "Old Sixth Ward",
                "protection_level": "binding",
                "threat_stage": 3,
            },
            {
                "parcel_id": "420090000055",
                "hcad_num": "420090000055",
                "owner_of_record": "SMITH FAMILY",
                "land_use_class": "A1",
                "designation_summary": "Protected Landmark",
                "protection_level": "binding",
                "threat_stage": 4,
            },
        ],
    )
    assert len(captured_pushes) == 1
    pushed_by_id = {p["parcel_id"]: p for p in captured_pushes[0]["parcels"]}
    assert "CAMPUS:PARISH_ASSEMBLAGE" in pushed_by_id
    assert pushed_by_id["CAMPUS:PARISH_ASSEMBLAGE"]["protection_level"] == "binding"
    assert pushed_by_id["0010010000001"]["protection_level"] == "binding"
    assert pushed_by_id["0010010000001"]["owner_of_record"] == "SMITH FAMILY"
    assert "commercial_on_residential" in pushed_by_id["0010010000001"]["signal_flags"]
    assert pushed_by_id["0420090000055"]["protection_level"] == "binding"
    assert pushed_by_id["0420090000055"]["owner_of_record"] == "SMITH FAMILY"
    assert pushed_by_id["0420090000055"]["threat_stage"] == 4
    assert "owner_applicant_mismatch" in pushed_by_id["0420090000055"]["signal_flags"]
    assert "commercial_on_residential" in pushed_by_id["0420090000055"]["signal_flags"]

    # 3. validate_public_bundle_zero_leaks catches Watchlist headings, query-string deep links, summary counters, ai-companion JSON emails, and degraded-event leaks independently
    ai_dir = tmp_path / "app" / "public" / "ai-companion"
    data_dir = tmp_path / "app" / "public" / "data"
    ai_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    (ai_dir / "guide.md").write_text(
        "## Active Investigative Property Watchlist\n"
        "See https://davemorris-gcp.github.io/houston-building-atlas/plat-monitor.html?mode=watchlist&watch=prop-1\n",
        encoding="utf-8",
    )
    (ai_dir / "manifest.json").write_text(
        json.dumps({"contact": "screener@preservation.example.org"}),
        encoding="utf-8",
    )
    (data_dir / "plat_agenda_monitor.json").write_text(
        json.dumps(
            {
                "summary": {"tier1Count": 2},
                "cycles": {"2026-11-12": {"summary": {"reviewedCount": 5}}},
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "monitor_activity_log.json").write_text(
        json.dumps(
            {
                "dailyRuns": [{"date": "2026-11-23", "propertiesSwept": 80}],
                "events": [
                    {
                        "id": "agenda:1",
                        "stream": "agenda",
                        "reviewer": "Preservation Staff",
                        "watchPropertyId": "prop-1",
                    },
                    {
                        "id": "deg:1",
                        "kind": "watchlist_sweep_degraded",
                        "address": "500 Secret St",
                    },
                ],
                "summary": {"byStream": {"agenda": 1, "permits": 0, "watchlist": 0, "staff": 0}},
            }
        ),
        encoding="utf-8",
    )
    leaks = pbg.validate_public_bundle_zero_leaks(tmp_path)
    assert any("guide.md still contains private Watchlist" in err for err in leaks)
    assert any("guide.md leaked private Watchlist dossier deep links" in err for err in leaks)
    assert any("manifest.json leaked non-public email" in err for err in leaks)
    assert any("top-level summary leaked private key 'tier1Count'" in err for err in leaks)
    assert any("cycle 2026-11-12 summary leaked private key 'reviewedCount'" in err for err in leaks)
    assert any("dailyRuns 2026-11-23 leaked propertiesSwept" in err for err in leaks)
    assert any("degraded notice deg:1 leaked 'address'" in err for err in leaks)
    assert any("monitor_activity_log.json event" in err for err in leaks)
