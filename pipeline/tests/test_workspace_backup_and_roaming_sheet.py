"""Unit tests for Google Workspace public Approved_Edits backup, authenticated
pipeline_token monitoring pull/fallback, and Apps Script roaming/RBAC invariants.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from atlas_pipeline import backup_workspace_state, fetch_plat_and_watchlist
from atlas_pipeline.backup_workspace_state import (
    compile_workspace_backup_payload,
    run_live_backup,
)
from atlas_pipeline.schema import PLAT_SYNC_HEADERS
from atlas_pipeline.setup_admin_sheet import APPS_SCRIPT_CODE, build_instructions_rows


def test_apps_script_roaming_and_instructions_hygiene_invariants() -> None:
    """APPS_SCRIPT_CODE has zero literal double quotes, zero forbidden dashes, and supports roaming + Phase 1 RBAC."""
    assert '"' not in APPS_SCRIPT_CODE
    assert "\u2013" not in APPS_SCRIPT_CODE
    assert "\u2014" not in APPS_SCRIPT_CODE
    assert "supportsRoamingSync: true" in APPS_SCRIPT_CODE
    assert "supportsPhase1Monitoring: true" in APPS_SCRIPT_CODE
    assert "get_user_state" in APPS_SCRIPT_CODE
    assert "put_user_state" in APPS_SCRIPT_CODE
    assert "pull_monitoring_state" in APPS_SCRIPT_CODE
    assert "PLAT_SYNC_CSV_V1" in APPS_SCRIPT_CODE
    assert "_Monitoring_Roles" in APPS_SCRIPT_CODE
    assert "_Staff_User_State" in APPS_SCRIPT_CODE

    instr_dump = json.dumps(build_instructions_rows(), ensure_ascii=False)
    assert "\u2013" not in instr_dump
    assert "\u2014" not in instr_dump


def test_compile_workspace_backup_payload_scrubs_pii_dashes_large_photos_and_preserves_existing_on_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """compile_workspace_backup_payload produces deterministic, dash-free, PII-free JSON and run_live_backup guards outages."""
    plat_buf = io.StringIO()
    pw = csv.DictWriter(plat_buf, fieldnames=PLAT_SYNC_HEADERS)
    pw.writeheader()
    pw.writerow(
        {
            "status": "Approved",
            "hcad_num": "PLAT_REV:2026-11-12:2026-1810",
            "address": "Montrose Replat\u2014West",
            "year_built": "1924",
            "original_hcad_year": "1924",
            "historic_district": "",
            "contributing": "Tier 1",
            "source_type": "PLAT_MONITOR_REVIEW",
            "source_citation": json.dumps(
                {
                    "cycleKey": "2026-11-12",
                    "appNo": "2026-1810",
                    "reviewer": "advocate@example.org",
                    "updatedAt": "2026-11-22T01:00:00Z",
                    "review": {
                        "tier": "1",
                        "why": "Pre-1930 bungalow\u2013verify setback",
                        "reviewer": "advocate@example.org",
                        "updated": "2026-11-22T01:00:00Z",
                    },
                }
            ),
            "source_url": "",
            "verified_by": "Preservation Houston (advocate@example.org)",
            "updated_at": "2026-11-22T01:00:00Z",
        }
    )
    pw.writerow(
        {
            "status": "Approved",
            "hcad_num": "WATCH_PROP:prop-kirby-mansion",
            "address": "2000 Smith St.",
            "year_built": "1894",
            "original_hcad_year": "1894",
            "historic_district": "",
            "contributing": "Tier 1",
            "source_type": "PROPERTY_WATCHLIST_UPDATE",
            "source_citation": json.dumps(
                {
                    "id": "prop-kirby-mansion",
                    "updatedAt": "2026-11-22T01:05:00Z",
                    "updatedBy": "Sample Staffer / Houston Building Atlas",
                    "reportedBy": "Sample Staffer",
                    "checklist": [
                        {
                            "id": "chk-01",
                            "checks": [
                                {
                                    "date": "2026-10-26",
                                    "result": "Checked Oct 26, 2026 by Sample Staffer - No new filings found.",
                                }
                            ],
                        }
                    ],
                    "photoDataUrl": "data:image/jpeg;base64," + ("A" * 5000),
                }
            ),
            "source_url": "",
            "verified_by": "Preservation Houston (advocate@example.org)",
            "updated_at": "2026-11-22T01:05:00Z",
        }
    )

    app_buf = io.StringIO()
    aw = csv.DictWriter(
        app_buf,
        fieldnames=[
            "status",
            "hcad_num",
            "address",
            "year_built",
            "original_hcad_year",
            "historic_district",
            "contributing",
            "source_type",
            "source_citation",
            "source_url",
            "verified_by",
            "building_name",
            "alt_names",
            "bld_style",
            "architect",
            "geometry_geojson",
        ],
    )
    aw.writeheader()
    aw.writerow(
        {
            "status": "Approved",
            "hcad_num": "0621100000014",
            "address": "1127 KEY ST",
            "year_built": "1925",
            "original_hcad_year": "1930",
            "historic_district": "Norhill",
            "contributing": "Contributing",
            "source_type": "Other Primary Archival Record",
            "source_citation": "CR001 BLA Card\u2014Permit #8111 (Confirmed via email from advocate@example.org; Checked Nov 20, 2026 by Sample Staffer - verified)",
            "source_url": "",
            "verified_by": "advocate@example.org",
            "building_name": "",
            "alt_names": "",
            "bld_style": "Craftsman",
            "architect": "",
            "geometry_geojson": "",
        }
    )

    payload = compile_workspace_backup_payload(
        plat_sync_csv=plat_buf.getvalue(),
        approved_edits_csv=app_buf.getvalue(),
    )
    assert payload["schemaVersion"] == "2.0"
    assert payload["counts"] == {"approvedOverrides": 1}
    assert "platReviews" not in payload
    assert "watchProperties" not in payload

    serialized = json.dumps(payload, ensure_ascii=False)
    assert "\u2013" not in serialized
    assert "\u2014" not in serialized
    assert "advocate@example.org" not in serialized, "Staff email PII must never leak into workspace_cloud_backup.json"
    assert "Sample Staffer" not in serialized, "Individual staff names must be normalized to Preservation Staff in public Git backups"
    assert "prop-kirby-mansion" not in serialized, "Private Watchlist dossiers must never enter public workspace_cloud_backup.json"

    # Verify run_live_backup writes Approved_Edits-only payload, triggers decoupled Drive backup when token set, and scrubs legacy sections on outage
    out_file = tmp_path / "workspace_cloud_backup.json"
    drive_triggered: list[str] = []
    monkeypatch.setenv("ATLAS_PIPELINE_SYNC_TOKEN", "test-pipe-token-2026")
    monkeypatch.setattr(
        backup_workspace_state,
        "trigger_drive_vault_backup",
        lambda webhook_url="", pipeline_token="", timeout=45: (
            drive_triggered.append(webhook_url) or {"ok": True, "target": "drive_folder"}
        ),
    )
    monkeypatch.setattr(
        backup_workspace_state,
        "_fetch_text",
        lambda _url, timeout=45: app_buf.getvalue(),
    )
    written = run_live_backup(out_file)
    assert written["counts"] == {"approvedOverrides": 1}
    assert "platReviews" not in written
    assert "watchProperties" not in written
    assert len(drive_triggered) == 1
    saved_text = out_file.read_text(encoding="utf-8")

    def _failing_fetch(_url: str, timeout: int = 45) -> str:
        raise OSError("Simulated network outage")

    monkeypatch.setattr(backup_workspace_state, "_fetch_text", _failing_fetch)
    outage_res = run_live_backup(out_file)
    assert outage_res == {}
    assert out_file.read_text(encoding="utf-8") == saved_text

    # Legacy file containing pre-Phase-1 platReviews/watchProperties MUST be scrubbed even during an Approved_Edits outage!
    out_file.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "platReviews": {"PLAT_REV:2026-11-12:2026-1810": {"tier": "1"}},
                "watchProperties": {"WATCH_PROP:prop-kirby-mansion": {"id": "prop-kirby-mansion"}},
                "approvedOverrides": payload["approvedOverrides"],
            }
        ),
        encoding="utf-8",
    )
    scrubbed_outage = run_live_backup(out_file)
    assert scrubbed_outage["counts"] == {"approvedOverrides": 1}
    assert "platReviews" not in scrubbed_outage
    assert "watchProperties" not in scrubbed_outage
    on_disk_scrubbed = json.loads(out_file.read_text(encoding="utf-8"))
    assert "platReviews" not in on_disk_scrubbed
    assert "watchProperties" not in on_disk_scrubbed


def test_pull_cloud_sheet_sync_rows_authenticated_pipeline_token_and_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pull_cloud_sheet_sync_rows uses authenticated ATLAS_PIPELINE_SYNC_TOKEN POST first and falls back cleanly without ?feed=plat_sync."""
    calls: list[dict] = []

    def _fake_pull_via_token(*, webhook_url: str = "", pipeline_token: str = "", timeout: int = 45) -> dict:
        calls.append({"webhook_url": webhook_url, "pipeline_token": pipeline_token})
        return {
            "platReviews": {("2026-11-12", "2026-1810"): {"tier": "1", "tierConfirmed": True}},
            "watchProperties": {},
            "parcels": [],
            "watchedEntities": [],
        }

    monkeypatch.setenv("ATLAS_PIPELINE_SYNC_TOKEN", "pipe-secret-2026")
    monkeypatch.setattr(
        fetch_plat_and_watchlist,
        "pull_workspace_monitoring_via_pipeline_token",
        _fake_pull_via_token,
    )
    res1 = fetch_plat_and_watchlist.pull_cloud_sheet_sync_rows(backup_json_path=None)
    assert len(calls) == 1
    assert res1["platReviews"][("2026-11-12", "2026-1810")]["tier"] == "1"

    # Without ATLAS_PIPELINE_SYNC_TOKEN and without private sections in backup_json_path, returns clean empty dicts (never hits ?feed=plat_sync!)
    monkeypatch.delenv("ATLAS_PIPELINE_SYNC_TOKEN", raising=False)
    monkeypatch.setattr(
        fetch_plat_and_watchlist,
        "fetch_bytes",
        lambda url, timeout=15: (_ for _ in ()).throw(
            AssertionError(f"Must never call unauthenticated CSV feed {url}")
        ),
    )
    res_empty = fetch_plat_and_watchlist.pull_cloud_sheet_sync_rows(backup_json_path=None)
    assert res_empty == {"platReviews": {}, "watchProperties": {}}

    # Local hermetic test fixture JSON still hydrates cleanly when explicitly supplied
    tier3_backup = tmp_path / "hermetic_fixture_backup.json"
    tier3_backup.write_text(
        json.dumps(
            {
                "platReviews": {
                    "PLAT_REV:2026-12-03:2026-1524": {
                        "cycleKey": "2026-12-03",
                        "review": {"tier": "2", "tierConfirmed": True},
                    }
                },
                "watchProperties": {
                    "WATCH_PROP:prop-tier3-test": {
                        "id": "prop-tier3-test",
                        "address": "4705 Liberty Rd",
                        "tier": 2,
                        "tierConfirmed": True,
                        "searchTerms": {"entities": ["Liberty LLC"]},
                        "driveLinks": ["https://drive.google.com/example"],
                        "checklist": [{"id": "chk-01", "status": "In Progress", "checks": []}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    res3 = fetch_plat_and_watchlist.pull_cloud_sheet_sync_rows(backup_json_path=tier3_backup)
    assert res3["platReviews"][("2026-12-03", "2026-1524")]["tier"] == "2"
    wp3 = res3["watchProperties"]["prop-tier3-test"]
    assert wp3["_hasExplicitSearchTerms"] is True
    assert wp3["_hasExplicitDriveLinks"] is True
    assert wp3["_hasExplicitChecklist"] is True
