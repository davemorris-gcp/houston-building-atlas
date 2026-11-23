"""Automated Public Approved_Edits Git Backup & Restricted Google Drive Vault Trigger.

Phase 1 Privacy Contract:
1. Public GitHub Repository Backup (`pipeline/backups/workspace_cloud_backup.json`):
   Pulls ONLY public `Approved_Edits` CSV historical map overrides, scrubs PII and
   typographic dashes, and writes a deterministic JSON snapshot with ZERO internal
   `platReviews`, `watchProperties`, or `Staff_Only` payloads.
2. Restricted Google Drive Vault Snapshot (`ATLAS_PIPELINE_SYNC_TOKEN`):
   When `ATLAS_PIPELINE_SYNC_TOKEN` is configured (e.g. in GitHub Actions Secrets),
   triggers `pipeline_create_drive_backup` on the Google Apps Script Web App so full
   rolling daily snapshots of `Monitoring_Core` + `Atlas_Monitoring_Staff_Only` are
   stored inside the restricted Google Drive `Atlas_Monitoring_Backups` folder.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "pipeline") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "pipeline"))

from atlas_pipeline.schema import (  # noqa: E402
    DEFAULT_SHEET_PUBLISHED_CSV_URL as SHEET_PUBLISHED_CSV_URL,
    DEFAULT_SHEET_WEBHOOK_URL as SHEET_WEBHOOK_URL,
    DEFAULT_WORKSPACE_BACKUP_PATH as DEFAULT_BACKUP_PATH,
    PLAT_SYNC_HEADERS,
)


def _sanitize_text(val: Any) -> str:
    return (
        str(val if val is not None else "")
        .replace("\u2014", " - ")
        .replace("\u2013", "-")
        .strip()
    )


_EMAIL_TOKEN_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_CHECKED_BY_RE = re.compile(
    r"(\bChecked\s+[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}\s+by\s+|\bChecked\s+\d{4}-\d{2}-\d{2}\s+by\s+)([^-(\n\r]+?)(?=\s+-\s+|\s*\(|$)"
)
_PII_ATTRIBUTION_KEYS = frozenset(
    {
        "reviewer",
        "updatedby",
        "reportedby",
        "verified_by",
        "submitted_by",
        "submitter_name",
        "submitter_email",
        "admin_email",
        "by",
    }
)
_GENERIC_TEAM_LABELS = frozenset(
    {
        "preservation staff",
        "preservation advocate",
        "preservation houston",
        "preservation houston archival review",
        "houston building atlas",
        "community researcher",
        "city directory batch auditor",
        "houston city directory batch auditor (1866-1926)",
        "automated pipeline",
        "[auto-sweep]",
    }
)


def _scrub_json_value(obj: Any, *, key_hint: str = "") -> Any:
    if isinstance(obj, str):
        cleaned = obj.replace("\u2014", " - ").replace("\u2013", "-")
        if _EMAIL_TOKEN_RE.search(cleaned):
            cleaned = _EMAIL_TOKEN_RE.sub("Preservation Staff", cleaned)
        if key_hint.lower() in _PII_ATTRIBUTION_KEYS:
            norm = cleaned.strip()
            if norm and norm.lower() not in _GENERIC_TEAM_LABELS:
                return "Preservation Staff"
            return norm
        if "Checked " in cleaned and " by " in cleaned:

            def _sub_checked(m: re.Match[str]) -> str:
                who = m.group(2).strip()
                if who.lower() in _GENERIC_TEAM_LABELS:
                    return m.group(0)
                return f"{m.group(1)}Preservation Staff"

            cleaned = _CHECKED_BY_RE.sub(_sub_checked, cleaned)
        return cleaned
    if isinstance(obj, list):
        return [_scrub_json_value(x, key_hint=key_hint) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _scrub_json_value(v, key_hint=str(k)) for k, v in obj.items()}
    return obj


def _extract_sync_timestamp(raw_citation: str, fallback_date: str = "") -> str:
    s = (raw_citation or "").strip()
    if s.startswith("{"):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("review"), dict) and parsed["review"].get("updated"):
                    return str(parsed["review"]["updated"])
                for key in ("updatedAt", "updated", "savedAt"):
                    if parsed.get(key):
                        return str(parsed[key])
        except Exception:
            pass
    return _sanitize_text(fallback_date)


def partition_pending_submissions_rows(
    rows: list[list[str]],
    existing_plat_sync_rows: list[list[str]] | None = None,
) -> dict[str, list[list[str]]]:
    """Partition raw Pending_Submissions rows (excluding header) losslessly for sheet cleanup."""
    sync_by_key: dict[str, tuple[str, int, list[str]]] = {}
    seq = 0

    for ex_row in existing_plat_sync_rows or []:
        seq += 1
        padded = [_sanitize_text(c) for c in ex_row] + [""] * 12
        key = padded[1]
        if not key or key.lower() in ("hcad_num", "record key (cycle/property)"):
            continue
        cite = padded[8]
        ts = _extract_sync_timestamp(cite, padded[11] if len(ex_row) > 11 else "")
        norm_sync_row = [
            "Approved",
            key,
            padded[2],
            padded[3],
            padded[4],
            padded[5],
            padded[6],
            padded[7]
            or ("PLAT_MONITOR_REVIEW" if key.startswith("PLAT_REV:") else "PROPERTY_WATCHLIST_UPDATE"),
            cite,
            padded[9],
            padded[10],
            ts,
        ]
        sync_by_key[key] = (ts, seq, norm_sync_row)

    clean_pending_rows: list[list[str]] = []
    citydir_batch_archive_rows: list[list[str]] = []

    for raw_row in rows:
        seq += 1
        if not any(str(c or "").strip() for c in raw_row):
            continue
        padded = [_sanitize_text(c) for c in raw_row]
        while len(padded) < 23:
            padded.append("")

        status = padded[0]
        hcad_key = padded[1]
        src_type = padded[7]
        cite = padded[8]
        submitted_by = padded[11]
        submitted_at = padded[12]

        is_plat_or_watch = (
            src_type.upper() in ("PLAT_MONITOR_REVIEW", "PROPERTY_WATCHLIST_UPDATE")
            or hcad_key.startswith("PLAT_REV:")
            or hcad_key.startswith("WATCH_PROP:")
        )
        if is_plat_or_watch and hcad_key:
            ts = _extract_sync_timestamp(cite, submitted_at)
            norm_sync_row = [
                "Approved",
                hcad_key,
                padded[2],
                padded[3],
                padded[4],
                padded[5],
                padded[6],
                src_type
                or (
                    "PLAT_MONITOR_REVIEW"
                    if hcad_key.startswith("PLAT_REV:")
                    else "PROPERTY_WATCHLIST_UPDATE"
                ),
                cite,
                padded[9],
                padded[10] or submitted_by,
                ts or submitted_at,
            ]
            prev = sync_by_key.get(hcad_key)
            if prev is None or (ts, seq) >= (prev[0], prev[1]):
                sync_by_key[hcad_key] = (ts, seq, norm_sync_row)
            continue

        is_auto_citydir_pending = (
            status.lower() == "pending"
            and "batch auditor" in submitted_by.lower()
            and src_type.lower() == "houston city directory"
        )
        data_row_18_plus_w = padded[:18] + ["", "", "", "", padded[22]]
        if is_auto_citydir_pending:
            citydir_batch_archive_rows.append(data_row_18_plus_w)
        else:
            clean_pending_rows.append(data_row_18_plus_w)

    deduped_sync_rows = [
        item[2]
        for _, item in sorted(
            sync_by_key.items(), key=lambda kv: (kv[0].startswith("PLAT_REV:"), kv[0])
        )
    ]
    return {
        "plat_watchlist_sync_rows": deduped_sync_rows,
        "citydir_batch_archive_rows": citydir_batch_archive_rows,
        "clean_pending_rows": clean_pending_rows,
    }


def _fetch_text(url: str, timeout: int = 45) -> str:
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "HoustonBuildingAtlas-WorkspaceBackup/2.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            last_err = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    assert last_err is not None
    raise last_err


def _is_valid_sync_csv(text: str) -> bool:
    s = (text or "").lstrip()
    if not s or s.startswith("<") or s.startswith("{"):
        return False
    first_line = s.splitlines()[0].lower()
    return "status" in first_line and ("hcad_num" in first_line or "record key" in first_line)


def compile_workspace_backup_payload(
    *,
    approved_edits_csv: str,
    plat_sync_csv: str = "",
) -> dict[str, Any]:
    """Compile a deterministic, PII-free JSON backup strictly containing public `Approved_Edits`.

    Why `plat_sync_csv` is ignored/dropped in v2.0:
      `davemorris-gcp/houston-building-atlas` is a public GitHub repository; internal
      `PLAT_REV:*`, `WATCH_PROP:*`, `Decisions`, and `Staff_Only` records are backed up
      exclusively to the restricted Google Drive `Atlas_Monitoring_Backups` vault.
    """
    _ = plat_sync_csv
    approved_overrides: dict[str, Any] = {}

    if approved_edits_csv and approved_edits_csv.strip():
        reader = csv.DictReader(io.StringIO(approved_edits_csv))
        for row in reader:
            norm = {_sanitize_text(k).lower(): _sanitize_text(v) for k, v in row.items() if k}
            if norm.get("status", "").lower() != "approved":
                continue
            key = norm.get("hcad_num") or norm.get("record key (cycle/property)", "")
            if not key:
                continue
            src_type = (norm.get("source_type") or norm.get("record type", "")).upper()
            if (
                src_type in ("PLAT_MONITOR_REVIEW", "PROPERTY_WATCHLIST_UPDATE")
                or key.startswith("PLAT_REV:")
                or key.startswith("WATCH_PROP:")
            ):
                continue
            cite = norm.get("source_citation") or ""
            approved_overrides[key] = _scrub_json_value(
                {
                    "hcad_num": key,
                    "address": norm.get("address", ""),
                    "year_built": norm.get("year_built", ""),
                    "original_hcad_year": norm.get("original_hcad_year", ""),
                    "historic_district": norm.get("historic_district", ""),
                    "contributing": norm.get("contributing", ""),
                    "source_type": norm.get("source_type", ""),
                    "source_citation": cite,
                    "source_url": norm.get("source_url", ""),
                    "building_name": norm.get("building_name", ""),
                    "alt_names": norm.get("alt_names", ""),
                    "bld_style": norm.get("bld_style", ""),
                    "architect": norm.get("architect", ""),
                    "geometry_geojson": norm.get("geometry_geojson", ""),
                }
            )

    return {
        "schemaVersion": "2.0",
        "description": (
            "Public Approved_Edits historical map overrides backup (private monitoring and "
            "Staff-Only snapshots are stored separately in the restricted Google Drive "
            "Atlas_Monitoring_Backups vault)."
        ),
        "counts": {
            "approvedOverrides": len(approved_overrides),
        },
        "approvedOverrides": dict(sorted(approved_overrides.items())),
    }


def trigger_drive_vault_backup(
    *,
    webhook_url: str = SHEET_WEBHOOK_URL,
    pipeline_token: str = "",
    timeout: int = 45,
) -> dict[str, Any]:
    """Trigger decoupled rolling JSON snapshot creation inside restricted Google Drive."""
    tok = (pipeline_token or os.environ.get("ATLAS_PIPELINE_SYNC_TOKEN") or "").strip()
    if not tok or not webhook_url:
        return {"ok": False, "skipped": True, "reason": "ATLAS_PIPELINE_SYNC_TOKEN not set"}
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(
            {"action": "pipeline_create_drive_backup", "pipeline_token": tok}
        ).encode("utf-8"),
        headers={"Content-Type": "text/plain;charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def run_live_backup(output_path: Path = DEFAULT_BACKUP_PATH) -> dict[str, Any]:
    cb = int(dt.datetime.now(dt.timezone.utc).timestamp())
    approved_csv = ""
    try:
        approved_csv = _fetch_text(f"{SHEET_PUBLISHED_CSV_URL}&_cb={cb}")
    except Exception as exc:
        print(f"Warning: could not fetch Approved_Edits CSV ({exc})", file=sys.stderr)

    # Trigger decoupled private Google Drive snapshot when ATLAS_PIPELINE_SYNC_TOKEN is available
    try:
        drive_res = trigger_drive_vault_backup(webhook_url=SHEET_WEBHOOK_URL)
        if drive_res.get("ok"):
            print(f"Triggered restricted Google Drive monitoring backup: {drive_res}")
    except Exception as exc:
        print(f"Warning: restricted Drive vault backup trigger failed ({exc})", file=sys.stderr)

    approved_valid = _is_valid_sync_csv(approved_csv)
    if not approved_valid:
        # Even on transient Approved_Edits outage, scrub any legacy platReviews/watchProperties from existing file!
        if output_path.exists():
            try:
                prev_payload = json.loads(output_path.read_text(encoding="utf-8"))
                if isinstance(prev_payload, dict) and (
                    "platReviews" in prev_payload or "watchProperties" in prev_payload
                ):
                    prev_app = _scrub_json_value(prev_payload.get("approvedOverrides") or {})
                    cleaned_prev = {
                        "schemaVersion": "2.0",
                        "description": (
                            "Public Approved_Edits historical map overrides backup (private monitoring and "
                            "Staff-Only snapshots are stored separately in the restricted Google Drive "
                            "Atlas_Monitoring_Backups vault)."
                        ),
                        "counts": {"approvedOverrides": len(prev_app)},
                        "approvedOverrides": dict(sorted(prev_app.items()))
                        if isinstance(prev_app, dict)
                        else {},
                    }
                    output_path.write_text(
                        json.dumps(cleaned_prev, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    return cleaned_prev
            except Exception:
                pass
        print(
            "Warning: Approved_Edits CSV returned empty/invalid response; preserving existing public backup.",
            file=sys.stderr,
        )
        return {}

    payload = compile_workspace_backup_payload(approved_edits_csv=approved_csv)

    if not payload["approvedOverrides"] and output_path.exists():
        try:
            prev_payload = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(prev_payload, dict):
                prev_app = _scrub_json_value(prev_payload.get("approvedOverrides") or {})
                if isinstance(prev_app, dict) and prev_app:
                    payload["approvedOverrides"] = dict(sorted(prev_app.items()))
                    payload["counts"] = {"approvedOverrides": len(payload["approvedOverrides"])}
        except Exception:
            pass

    output_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if "\u2014" in serialized or "\u2013" in serialized:
        raise ValueError("Typographic dash leaked into workspace_cloud_backup.json")
    output_path.write_text(serialized, encoding="utf-8")
    print(
        f"Saved public Approved_Edits backup to {output_path} "
        f"({payload['counts']['approvedOverrides']} approved overrides)."
    )
    return payload


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Snapshot public Approved_Edits to Git and trigger restricted Google Drive monitoring backup."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_BACKUP_PATH,
        help="Output JSON path for public Approved_Edits backup.",
    )
    args = parser.parse_args(argv)
    run_live_backup(args.output)


if __name__ == "__main__":
    main()
