"""Automated GitHub Backup & Workbook Deduplication for Google Workspace Cloud State.

1. Daily GitHub Backup (`--output`):
   Pulls live non-PII team operational state from the Google Workspace Apps Script
   (`?feed=plat_sync` for deduplicated `Plat_&_Watchlist_Sync` records and `Approved_Edits`
   CSV for curated map overrides), sanitizes typographic dashes and PII, sorts keys
   deterministically, and writes a version-controlled JSON snapshot to
   `pipeline/backups/workspace_cloud_backup.json`.
2. Workbook Row Partitioning (`--partition-pending-csv` / `partition_pending_submissions_rows`):
   Losslessly separates legacy append-only `Pending_Submissions` dumps into:
   - `plat_watchlist_sync_rows` (deduplicated 1-row-per-key `PLAT_REV:*` & `WATCH_PROP:*`)
   - `citydir_batch_archive_rows` (automated `City Directory Batch Auditor` pending rows)
   - `clean_pending_rows` (human community submissions & verified map overrides)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
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
    """Partition raw Pending_Submissions rows (excluding header) losslessly.

    - Deduplicates `PLAT_MONITOR_REVIEW` (`PLAT_REV:*`) and `PROPERTY_WATCHLIST_UPDATE`
      (`WATCH_PROP:*`) rows by canonical key, keeping the latest timestamped payload.
    - Separates automated `City Directory Batch Auditor` `Pending` rows into
      `citydir_batch_archive_rows` so `Pending_Submissions` holds only human submissions
      and verified overrides.
    """
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
            padded[7] or ("PLAT_MONITOR_REVIEW" if key.startswith("PLAT_REV:") else "PROPERTY_WATCHLIST_UPDATE"),
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
        # Pad out to 23 columns (A:W) while keeping S:V (indices 18..21) empty for ARRAYFORMULAs
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
                src_type or ("PLAT_MONITOR_REVIEW" if hcad_key.startswith("PLAT_REV:") else "PROPERTY_WATCHLIST_UPDATE"),
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
        for _, item in sorted(sync_by_key.items(), key=lambda kv: (kv[0].startswith("PLAT_REV:"), kv[0]))
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
                headers={"User-Agent": "HoustonBuildingAtlas-WorkspaceBackup/1.0"},
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
    if not s or s.startswith("<"):
        return False
    first_line = s.splitlines()[0].lower()
    return "status" in first_line and ("hcad_num" in first_line or "record key" in first_line)


def compile_workspace_backup_payload(
    *,
    plat_sync_csv: str,
    approved_edits_csv: str,
) -> dict[str, Any]:
    """Compile a deterministic, PII-free JSON backup from Workspace CSV feeds."""
    plat_reviews: dict[str, Any] = {}
    watch_properties: dict[str, Any] = {}
    approved_overrides: dict[str, Any] = {}

    for csv_blob in (plat_sync_csv, approved_edits_csv):
        if not csv_blob or not csv_blob.strip():
            continue
        reader = csv.DictReader(io.StringIO(csv_blob))
        for row in reader:
            norm = {_sanitize_text(k).lower(): _sanitize_text(v) for k, v in row.items() if k}
            if norm.get("status", "").lower() != "approved":
                continue
            key = norm.get("hcad_num") or norm.get("record key (cycle/property)", "")
            if not key:
                continue
            src_type = (norm.get("source_type") or norm.get("record type", "")).upper()
            cite = (
                norm.get("source_citation")
                or norm.get("json state payload (synced to plat & property monitor)", "")
            )
            if src_type == "PLAT_MONITOR_REVIEW" or key.startswith("PLAT_REV:"):
                if cite.startswith("{"):
                    try:
                        obj = _scrub_json_value(json.loads(cite))
                        prev = plat_reviews.get(key)
                        prev_ts = str((prev or {}).get("updatedAt") or "")
                        cand_ts = str(obj.get("updatedAt") or (obj.get("review") or {}).get("updated") or "")
                        if prev is None or cand_ts >= prev_ts:
                            plat_reviews[key] = obj
                    except Exception:
                        pass
            elif src_type == "PROPERTY_WATCHLIST_UPDATE" or key.startswith("WATCH_PROP:"):
                if cite.startswith("{"):
                    try:
                        obj = _scrub_json_value(json.loads(cite))
                        # Strip inline base64 photoDataUrl from git backup if huge; keep URL & metadata
                        if isinstance(obj, dict) and len(str(obj.get("photoDataUrl") or "")) > 4096:
                            obj = dict(obj)
                            obj["photoDataUrlOmittedInCompactBackup"] = True
                            obj.pop("photoDataUrl", None)
                        prev = watch_properties.get(key)
                        prev_ts = str((prev or {}).get("updatedAt") or "")
                        cand_ts = str(obj.get("updatedAt") or "")
                        if prev is None or cand_ts >= prev_ts:
                            watch_properties[key] = obj
                    except Exception:
                        pass
            else:
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
        "schemaVersion": "1.0",
        "description": "Automated GitHub backup of non-PII Google Workspace operational state (Plat Reviews, Property Watchlist dossiers, and Approved Map Overrides).",
        "counts": {
            "platReviews": len(plat_reviews),
            "watchProperties": len(watch_properties),
            "approvedOverrides": len(approved_overrides),
        },
        "platReviews": dict(sorted(plat_reviews.items())),
        "watchProperties": dict(sorted(watch_properties.items())),
        "approvedOverrides": dict(sorted(approved_overrides.items())),
    }


def run_live_backup(output_path: Path = DEFAULT_BACKUP_PATH) -> dict[str, Any]:
    cb = int(dt.datetime.now(dt.timezone.utc).timestamp())
    plat_csv = ""
    approved_csv = ""
    try:
        plat_csv = _fetch_text(f"{SHEET_WEBHOOK_URL}?feed=plat_sync&_cb={cb}")
    except Exception as exc:
        print(f"Warning: could not fetch ?feed=plat_sync ({exc})", file=sys.stderr)
    try:
        approved_csv = _fetch_text(f"{SHEET_PUBLISHED_CSV_URL}&_cb={cb}")
    except Exception as exc:
        print(f"Warning: could not fetch Approved_Edits CSV ({exc})", file=sys.stderr)

    plat_valid = _is_valid_sync_csv(plat_csv)
    approved_valid = _is_valid_sync_csv(approved_csv)
    if not plat_valid and not approved_valid:
        print("Warning: both cloud feeds returned empty/invalid responses; preserving existing backup.", file=sys.stderr)
        return {}

    payload = compile_workspace_backup_payload(
        plat_sync_csv=plat_csv if plat_valid else "",
        approved_edits_csv=approved_csv if approved_valid else "",
    )

    # Preserve existing per-section backup records on partial feed outages, HTML error pages,
    # or header-only CSVs so a transient outage never wipes previously committed sections.
    if output_path.exists():
        try:
            prev_payload = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(prev_payload, dict):
                if not payload["platReviews"] and not payload["watchProperties"]:
                    prev_plat = _scrub_json_value(prev_payload.get("platReviews") or {})
                    prev_watch = _scrub_json_value(prev_payload.get("watchProperties") or {})
                    if isinstance(prev_plat, dict) and prev_plat:
                        payload["platReviews"] = dict(sorted(prev_plat.items()))
                    if isinstance(prev_watch, dict) and prev_watch:
                        payload["watchProperties"] = dict(sorted(prev_watch.items()))
                if not payload["approvedOverrides"]:
                    prev_app = _scrub_json_value(prev_payload.get("approvedOverrides") or {})
                    if isinstance(prev_app, dict) and prev_app:
                        payload["approvedOverrides"] = dict(sorted(prev_app.items()))
                payload["counts"] = {
                    "platReviews": len(payload["platReviews"]),
                    "watchProperties": len(payload["watchProperties"]),
                    "approvedOverrides": len(payload["approvedOverrides"]),
                }
        except Exception:
            pass

    output_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if "\u2014" in serialized or "\u2013" in serialized:
        raise ValueError("Typographic dash leaked into workspace_cloud_backup.json")
    output_path.write_text(serialized, encoding="utf-8")
    print(
        f"Saved Workspace backup to {output_path} "
        f"({payload['counts']['platReviews']} plat reviews, "
        f"{payload['counts']['watchProperties']} watched properties, "
        f"{payload['counts']['approvedOverrides']} approved overrides)."
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up Google Workspace cloud sync state to Git.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_BACKUP_PATH,
        help="Target JSON backup file path.",
    )
    parser.add_argument(
        "--partition-pending-csv",
        type=Path,
        default=None,
        help="Optional local Pending_Submissions CSV to partition into clean human, plat_sync, and archive JSON sets.",
    )
    args = parser.parse_args()
    if args.partition_pending_csv is not None:
        raw_rows = list(csv.reader(args.partition_pending_csv.read_text(encoding="utf-8").splitlines()))
        part = partition_pending_submissions_rows(raw_rows[1:] if raw_rows else [])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(part, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    run_live_backup(args.output)


if __name__ == "__main__":
    main()
