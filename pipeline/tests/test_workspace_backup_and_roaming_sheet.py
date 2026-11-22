"""Unit tests for Google Workspace cloud state backup, Pending_Submissions partitioning,
?feed=plat_sync primary/fallback ingestion, and Apps Script roaming invariants.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from atlas_pipeline import backup_workspace_state, fetch_plat_and_watchlist
from atlas_pipeline.backup_workspace_state import (
    PLAT_SYNC_HEADERS,
    compile_workspace_backup_payload,
    partition_pending_submissions_rows,
    run_live_backup,
)
from atlas_pipeline.setup_admin_sheet import APPS_SCRIPT_CODE, build_instructions_rows


def test_apps_script_roaming_and_instructions_hygiene_invariants() -> None:
    """APPS_SCRIPT_CODE has zero literal double quotes, zero forbidden dashes, and supports roaming + plat_sync."""
    assert '"' not in APPS_SCRIPT_CODE
    assert "\u2013" not in APPS_SCRIPT_CODE
    assert "\u2014" not in APPS_SCRIPT_CODE
    assert "supportsRoamingSync = true" in APPS_SCRIPT_CODE
    assert "get_user_state" in APPS_SCRIPT_CODE
    assert "put_user_state" in APPS_SCRIPT_CODE
    assert "feed === 'plat_sync'" in APPS_SCRIPT_CODE
    assert "Plat_&_Watchlist_Sync" in APPS_SCRIPT_CODE
    assert "_Staff_User_State" in APPS_SCRIPT_CODE

    instr_dump = json.dumps(build_instructions_rows(), ensure_ascii=False)
    assert "\u2013" not in instr_dump
    assert "\u2014" not in instr_dump


def test_partition_pending_submissions_rows_lossless_dedup_and_archive() -> None:
    """Losslessly separates PLAT_REV:*/WATCH_PROP:* (deduped by latest timestamp), automated CityDir pending rows, and human rows."""
    existing_sync = [
        PLAT_SYNC_HEADERS,
        [
            "Approved",
            "PLAT_REV:2026-11-12:2026-1810",
            "Montrose Replat",
            "1924",
            "1924",
            "",
            "Tier 2",
            "PLAT_MONITOR_REVIEW",
            json.dumps({"cycleKey": "2026-11-12", "appNo": "2026-1810", "review": {"tier": "2", "updated": "2026-11-20T10:00:00Z"}}),
            "",
            "Preservation Staff",
            "2026-11-20T10:00:00Z",
        ],
    ]

    def mk_pending_row(
        status: str,
        hcad_key: str,
        addr: str,
        src_type: str,
        cite: str,
        submitted_by: str,
        submitted_at: str,
        geom_w: str = "",
    ) -> list[str]:
        row = [
            status,
            hcad_key,
            addr,
            "1925",
            "1930",
            "Norhill",
            "Contributing",
            src_type,
            cite,
            "",
            "",
            submitted_by,
            submitted_at,
            "Note",
            "Bldg",
            "",
            "Craftsman",
            "",
            "=FORMULA_S",
            "=FORMULA_T",
            "=FORMULA_U",
            "=FORMULA_V",
            geom_w,
        ]
        assert len(row) == 23
        return row

    raw_pending_rows = [
        # Newer PLAT_REV update (must beat existing_sync 2026-11-20T10:00:00Z AND survive an older out-of-order row below)
        mk_pending_row(
            "Approved",
            "PLAT_REV:2026-11-12:2026-1810",
            "Montrose Replat",
            "PLAT_MONITOR_REVIEW",
            json.dumps({"cycleKey": "2026-11-12", "appNo": "2026-1810", "review": {"tier": "1", "updated": "2026-11-22T08:00:00Z"}}),
            "Preservation Advocate",
            "2026-11-22T08:00:00Z",
        ),
        mk_pending_row(
            "Approved",
            "PLAT_REV:2026-11-12:2026-1810",
            "Montrose Replat",
            "PLAT_MONITOR_REVIEW",
            json.dumps({"cycleKey": "2026-11-12", "appNo": "2026-1810", "review": {"tier": "3", "updated": "2026-11-21T08:00:00Z"}}),
            "Preservation Advocate",
            "2026-11-21T08:00:00Z",
        ),
        # Duplicate WATCH_PROP rows -> newest updatedAt wins
        mk_pending_row(
            "Approved",
            "WATCH_PROP:prop-kirby-mansion",
            "2000 Smith St.",
            "PROPERTY_WATCHLIST_UPDATE",
            json.dumps({"id": "prop-kirby-mansion", "tier": "2", "updatedAt": "2026-11-19T12:00:00Z"}),
            "Preservation Advocate",
            "2026-11-19T12:00:00Z",
        ),
        mk_pending_row(
            "Approved",
            "WATCH_PROP:prop-kirby-mansion",
            "2000 Smith St.",
            "PROPERTY_WATCHLIST_UPDATE",
            json.dumps({"id": "prop-kirby-mansion", "tier": "1", "updatedAt": "2026-11-22T09:15:00Z"}),
            "Preservation Advocate",
            "2026-11-22T09:15:00Z",
        ),
        # Automated City Directory Batch Auditor Pending row -> archived to _CityDir_Batch_Archive
        mk_pending_row(
            "Pending",
            "0621100000099",
            "1135 KEY ST",
            "Houston City Directory",
            "1926 City Directory listing\u2014verified",
            "City Directory Batch Auditor",
            "2026-11-01",
        ),
        # Approved City Directory row -> MUST remain in clean_pending_rows so Approved_Edits formula still sees it!
        mk_pending_row(
            "Approved",
            "0621100000014",
            "1127 KEY ST",
            "Houston City Directory",
            "1926 City Directory listing",
            "City Directory Batch Auditor",
            "2026-11-01",
            geom_w='{"type":"Polygon","coordinates":[[[-95.38,29.79],[-95.379,29.79],[-95.379,29.791],[-95.38,29.79]]]}',
        ),
        # Human community Pending submission -> MUST remain in clean_pending_rows
        mk_pending_row(
            "Pending",
            "0610870390001",
            "1506 HEIGHTS BLVD",
            "Sanborn Fire Insurance Map",
            "1924 Sanborn Vol 6\u2013Sheet 612",
            "Community Researcher",
            "2026-11-18",
        ),
        # Blank row ignored cleanly
        ["", "", ""],
    ]

    res = partition_pending_submissions_rows(raw_pending_rows, existing_plat_sync_rows=existing_sync)

    sync_rows = res["plat_watchlist_sync_rows"]
    assert len(sync_rows) == 2
    # Deterministic order: WATCH_PROP:* before PLAT_REV:*
    assert sync_rows[0][1] == "WATCH_PROP:prop-kirby-mansion"
    assert json.loads(sync_rows[0][8])["tier"] == "1"
    assert sync_rows[1][1] == "PLAT_REV:2026-11-12:2026-1810"
    assert json.loads(sync_rows[1][8])["review"]["tier"] == "1"

    archive_rows = res["citydir_batch_archive_rows"]
    assert len(archive_rows) == 1
    assert archive_rows[0][1] == "0621100000099"
    assert archive_rows[0][18:22] == ["", "", "", ""]
    assert "\u2014" not in archive_rows[0][8]

    clean_rows = res["clean_pending_rows"]
    assert [r[1] for r in clean_rows] == ["0621100000014", "0610870390001"]
    assert clean_rows[0][18:22] == ["", "", "", ""]
    assert clean_rows[0][22].startswith('{"type":"Polygon"')
    assert "\u2013" not in clean_rows[1][8]


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
    assert payload["counts"] == {"platReviews": 1, "watchProperties": 1, "approvedOverrides": 1}
    watch_entry = payload["watchProperties"]["WATCH_PROP:prop-kirby-mansion"]
    assert watch_entry.get("photoDataUrlOmittedInCompactBackup") is True
    assert "photoDataUrl" not in watch_entry

    serialized = json.dumps(payload, ensure_ascii=False)
    assert "\u2013" not in serialized
    assert "\u2014" not in serialized
    assert "advocate@example.org" not in serialized, "Staff email PII must never leak into workspace_cloud_backup.json"
    assert "Sample Staffer" not in serialized, "Individual staff names must be normalized to Preservation Staff in public Git backups"

    # Verify run_live_backup writes valid file on success and preserves existing file when feeds return empty
    out_file = tmp_path / "workspace_cloud_backup.json"
    monkeypatch.setattr(
        backup_workspace_state,
        "_fetch_text",
        lambda url, timeout=20: plat_buf.getvalue() if "feed=plat_sync" in url else app_buf.getvalue(),
    )
    written = run_live_backup(out_file)
    assert written["counts"]["platReviews"] == 1
    saved_text = out_file.read_text(encoding="utf-8")

    def _failing_fetch(_url: str, timeout: int = 20) -> str:
        raise OSError("Simulated network outage")

    monkeypatch.setattr(backup_workspace_state, "_fetch_text", _failing_fetch)
    outage_res = run_live_backup(out_file)
    assert outage_res == {}
    assert out_file.read_text(encoding="utf-8") == saved_text

    # Partial outage (including HTTP 200 HTML error page or header-only CSV): must preserve existing sections!
    def _partial_outage_fetch(url: str, timeout: int = 45) -> str:
        if "feed=plat_sync" in url:
            return "<!DOCTYPE html><html><body>Apps Script quota exceeded</body></html>"
        return app_buf.getvalue()

    monkeypatch.setattr(backup_workspace_state, "_fetch_text", _partial_outage_fetch)
    partial_res = run_live_backup(out_file)
    assert partial_res["counts"] == {"platReviews": 1, "watchProperties": 1, "approvedOverrides": 1}
    assert "WATCH_PROP:prop-kirby-mansion" in partial_res["watchProperties"]
    assert "PLAT_REV:2026-11-12:2026-1810" in partial_res["platReviews"]


def test_pull_cloud_sheet_sync_rows_prefers_plat_sync_feed_and_falls_back_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pull_cloud_sheet_sync_rows queries Tier 1 (?feed=plat_sync) -> Tier 2 (Approved_Edits CSV) -> Tier 3 (workspace_cloud_backup.json)."""
    primary_csv = (
        "status,hcad_num,source_type,source_citation,verified_by\n"
        'Approved,PLAT_REV:2026-11-12:2026-1810,PLAT_MONITOR_REVIEW,"{""cycleKey"":""2026-11-12"",""appNo"":""2026-1810"",""review"":{""tier"":""2"",""updated"":""2026-11-20T00:00:00Z""}}",Preservation Staff\n'
        'Approved,PLAT_REV:2026-11-12:2026-1810,PLAT_MONITOR_REVIEW,"{""cycleKey"":""2026-11-12"",""appNo"":""2026-1810"",""review"":{""tier"":""1"",""updated"":""2026-11-22T00:00:00Z""}}",Preservation Staff\n'
    )
    calls: list[str] = []

    def _fake_fetch_primary(url: str, timeout: int = 15) -> bytes:
        calls.append(url)
        if "feed=plat_sync" in url:
            return primary_csv.encode("utf-8")
        raise AssertionError("Should not hit fallback CSV when ?feed=plat_sync returned valid records")

    monkeypatch.setattr(fetch_plat_and_watchlist, "fetch_bytes", _fake_fetch_primary)
    res1 = fetch_plat_and_watchlist.pull_cloud_sheet_sync_rows(backup_json_path=None)
    assert len(calls) == 1
    assert "feed=plat_sync" in calls[0]
    assert res1["platReviews"][("2026-11-12", "2026-1810")]["tier"] == "1"

    # Tier 2: ?feed=plat_sync returns empty header stub -> falls back to SHEET_PUBLISHED_CSV_URL
    calls.clear()

    def _fake_fetch_fallback(url: str, timeout: int = 15) -> bytes:
        calls.append(url)
        if "feed=plat_sync" in url:
            return b"status,hcad_num\n"
        return primary_csv.encode("utf-8")

    monkeypatch.setattr(fetch_plat_and_watchlist, "fetch_bytes", _fake_fetch_fallback)
    res2 = fetch_plat_and_watchlist.pull_cloud_sheet_sync_rows(backup_json_path=None)
    assert len(calls) == 2
    assert "feed=plat_sync" in calls[0]
    assert "output=csv" in calls[1]
    assert res2["platReviews"][("2026-11-12", "2026-1810")]["tier"] == "1"

    # Tier 3: Both remote feeds return empty -> falls back to local workspace_cloud_backup.json
    tier3_backup = tmp_path / "workspace_cloud_backup.json"
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
    monkeypatch.setattr(fetch_plat_and_watchlist, "fetch_bytes", lambda _u, timeout=15: b"")
    res3 = fetch_plat_and_watchlist.pull_cloud_sheet_sync_rows(backup_json_path=tier3_backup)
    assert res3["platReviews"][("2026-12-03", "2026-1524")]["tier"] == "2"
    wp3 = res3["watchProperties"]["prop-tier3-test"]
    assert wp3["_hasExplicitSearchTerms"] is True
    assert wp3["_hasExplicitDriveLinks"] is True
    assert wp3["_hasExplicitChecklist"] is True
