#!/usr/bin/env python3
"""Daily Activity Log & 90-Day Change Ledger Builder for Plat & Property Monitor.

Generates and updates `app/public/data/monitor_activity_log.json` across all four
Preservation Houston monitoring streams using 100% Python 3.12 standard-library modules
so `.github/workflows/update_live_permits.yml` runs unattended with zero third-party packages:

  1. `watchlist`: 8-agency automated Watchlist sweeps (`chk-04`, `chk-05`, `chk-06`, `chk-09`,
     `chk-10`, `chk-13`, `chk-14`, `chk-15`), `To Do`/`Complete -> Flagged` status escalations,
     and honest daily sweep health telemetry (`8/8 agencies OK` vs degraded agency warnings).
  2. `agenda`: Biweekly Planning Commission agenda cycle postings, newly added plat applications,
     Section K Development Plat Variances (DPVs), deferral updates, and newly attached Plat or
     Public Hearing Notice PDFs.
  3. `staff`: Confirmed Tier 1/2 Plat Review decisions, Watchlist dossier additions, Tier 3
     archival/removals, manual checklist research stamps (`chk-01..chk-15`), snowball search-term
     updates, and Google Drive document links.
  4. `permits`: Citywide 45-day Historic Demolition / Sewer Disconnection / TX DSHS Asbestos
     alerts (`active_permit_alerts.json`) plus monthly HAHC COA agenda cycles, guideline redline
     drafts, new COA filings, and in-place post-hearing outcome rulings (`hahc_coa_tracker.json`).

Cross-Runtime Canonical Event ID Grammar (shared 1:1 with `app/js/activityLogHelpers.js`):
  - `wl:auto:<propId>:<rowId>:id:<agencyRecordId>` or `wl:auto:<propId>:<rowId>:txt:<fnv1a8>`
  - `wl:status:<propId>:<rowId>:<statusSlug>:<isoDate>`
  - `wl:sweep:<localDate>:degraded:<failedAgenciesSlug>`
  - `wl:manual:<propId>:<rowId>:<isoDate>:<fnv1a8>`
  - `staff:prop:<propId>:added`
  - `staff:prop:<propId>:archived`
  - `staff:prop:<propId>:edited:<isoDate>`
  - `staff:review:<cycleKey>:<appNo>:t<tier>`
  - `agenda:cycle:<cycleKey>`
  - `agenda:item:<cycleKey>:<appNo>:<kind>` (`added`, `dpv`, `notice_pdf`, `plat_pdf`, `deferral:<slug>`)
  - `permit:coh:<padded13Hcad>:<projectSlug>`
  - `permit:dshs:<padded13Hcad>:<notifSlug>`
  - `hahc:cycle:<meetingDate>`
  - `hahc:guideline:<meetingDate>:<slug>`
  - `hahc:filing:<filingId>:<outcomeSlug>`

Temporal & Idempotency Contracts:
  - Calendar day bucketing uses `zoneinfo.ZoneInfo("America/Chicago")` so evening Houston edits
    after `00:00 UTC` land on the correct local Central Time date.
  - `dailyRuns[]` counts (`eventCount`, `adverseEventCount`, `byStream`) are recomputed
    idempotently from retained deduplicated `events[]` for each Central Time date so intraday
    manual `workflow_dispatch` re-runs with zero new delta events never clobber morning counts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "app" / "public" / "data"
DEFAULT_LOG_FILENAME = "monitor_activity_log.json"

CHICAGO_TZ = ZoneInfo("America/Chicago")
RETENTION_DAYS = 90
SCHEMA_VERSION = 1

SNAPSHOT_FILENAMES: tuple[str, ...] = (
    "property_watchlist.json",
    "plat_agenda_monitor.json",
    "active_permit_alerts.json",
    "hahc_coa_tracker.json",
)

AUTOMATED_SWEEP_AGENCY_LABELS: dict[str, str] = {
    "chk-04": "TX Comptroller (Sales & Franchise Tax)",
    "chk-05": "HCAD Ownership & Classification",
    "chk-06": "TABC Liquor Licensing",
    "chk-09": "COH Planning Commission Plats & Variances",
    "chk-10": "TDLR Architectural Barriers (TABS)",
    "chk-13": "Houston 311 & Dangerous Buildings",
    "chk-14": "Houston Sold Permits",
    "chk-15": "TX DSHS Asbestos & Demolition",
}

HIGH_SEVERITY_CHECK_IDS = {"chk-10", "chk-13", "chk-14", "chk-15"}
ALLOWED_URL_PREFIXES = ("https://", "http://")


# ---------------------------------------------------------------------------
# Cross-Runtime Normalization & Canonical Event ID Primitives
# (Must remain byte-identical to app/js/activityLogHelpers.js)
# ---------------------------------------------------------------------------

def sanitize_text(val: object) -> str:
    """Normalize text and replace any U+2014 em-dash characters with standard ASCII hyphens."""
    s = str(val if val is not None else "")
    return s.replace("\u2014", " - ").replace("\u2013", "-").strip()


def collapse_upper(raw: object) -> str:
    """Collapse internal whitespace and uppercase text for cross-runtime digest parity."""
    return " ".join(sanitize_text(raw).upper().split())


def fnv1a32_hex(text: str) -> str:
    """Compute deterministic 32-bit FNV-1a digest over UTF-8 bytes as 8 lowercase hex chars."""
    h = 0x811C9DC5
    for byte in str(text or "").encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def slug_token(raw: object) -> str:
    """Normalize an identifier fragment into a compact lowercase ASCII slug (max 64 chars)."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", sanitize_text(raw).lower()).strip("-")
    return cleaned[:64] or "none"


def normalize_hcad_13(raw: object) -> str:
    """Extract first valid 10-13 digit HCAD parcel ID zero-padded to 13 digits, or fallback slug."""
    text = str(raw or "").strip()
    if not text:
        return "nohcad"
    for raw_tok in re.split(r"[,;\s|]+", text):
        tok = raw_tok.lstrip("#").split("#")[0].strip()
        if not tok or re.match(r"^[+-]?\d+(?:\.\d+)?E[+-]?\d+$", tok, re.I):
            continue
        digits = re.sub(r"[-\s]+", "", tok)
        if digits.isdigit() and 10 <= len(digits) <= 13 and not re.match(r"^0+$", digits):
            return digits.zfill(13)
    return slug_token(text)


def sanitize_links(raw_links: object) -> list[dict[str, str]]:
    """Retain only unique, well-formed http/https links with clean labels."""
    if not isinstance(raw_links, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw_links:
        if isinstance(entry, str):
            url = entry.strip()
            label = ""
        elif isinstance(entry, dict):
            url = str(entry.get("url") or "").strip()
            label = sanitize_text(entry.get("label") or "")
        else:
            continue
        if not url or any(ord(c) < 32 or ord(c) == 127 for c in url):
            continue
        if not url.lower().startswith(ALLOWED_URL_PREFIXES):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({"label": label or "Open Record", "url": url})
    return out


def to_chicago_date_iso(ts_or_date: str, fallback_iso: str = "") -> str:
    """Resolve an ISO timestamp or YYYY-MM-DD date string to an America/Chicago calendar date."""
    raw = str(ts_or_date or "").strip() or str(fallback_iso or "").strip()
    if not raw:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(CHICAGO_TZ).date().isoformat()
    except ValueError:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
        return m.group(1) if m else ""


def parse_iso_date_prefix(val: object) -> str:
    raw = str(val or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else ""


def _seed_ts_clamped(candidate_day: str, local_today: str, now_iso: str) -> str:
    """Anchor historical seed records to noon UTC on `candidate_day`, clamped to `<= now_iso` so upcoming meeting dockets never synthesize future sweep days."""
    if not candidate_day or (local_today and candidate_day > local_today):
        return now_iso
    candidate_ts = f"{candidate_day}T12:00:00Z"
    if candidate_day == local_today and "T" in now_iso and candidate_ts > now_iso:
        return now_iso
    return candidate_ts


def is_auto_sweep_check(chk: dict) -> bool:
    if not isinstance(chk, dict):
        return False
    if str(chk.get("source") or "").strip() == "auto-sweep":
        return True
    if chk.get("automated") is True:
        return True
    return str(chk.get("result") or "").strip().upper().startswith("[AUTO-SWEEP]")


def is_plat_seed_check(chk: dict) -> bool:
    if not isinstance(chk, dict):
        return False
    return str(chk.get("source") or "").strip() == "plat-seed"


def is_adverse_auto_sweep_check(chk: dict) -> bool:
    if not is_auto_sweep_check(chk):
        return False
    if isinstance(chk.get("adverse"), bool):
        return chk["adverse"]
    text = str(chk.get("result") or "")
    if re.search(
        r"\b(?:no new|0 active|no matching|none found|confirmed match to current owner)\b",
        text,
        re.I,
    ):
        return False
    return True


def build_auto_check_event_id(prop_id: str, row_id: str, chk: dict) -> str:
    pid = str(prop_id or "").strip()
    rid = str(row_id or "").strip().lower()
    agency_rec_id = str((chk or {}).get("agencyRecordId") or "").strip()
    if agency_rec_id:
        return f"wl:auto:{pid}:{rid}:id:{agency_rec_id}"
    return f"wl:auto:{pid}:{rid}:txt:{fnv1a32_hex(collapse_upper((chk or {}).get('result')))}"


def build_manual_check_event_id(prop_id: str, row_id: str, chk: dict) -> str:
    pid = str(prop_id or "").strip()
    rid = str(row_id or "").strip().lower()
    d_iso = parse_iso_date_prefix((chk or {}).get("date")) or "nodate"
    digest = fnv1a32_hex(collapse_upper((chk or {}).get("result")))
    return f"wl:manual:{pid}:{rid}:{d_iso}:{digest}"


def build_watch_status_event_id(prop_id: str, row_id: str, status: str, date_iso: str) -> str:
    pid = str(prop_id or "").strip()
    rid = str(row_id or "").strip().lower()
    return f"wl:status:{pid}:{rid}:{slug_token(status)}:{date_iso or 'nodate'}"


def build_sweep_degraded_event_id(local_date: str, failed_agencies: object = ()) -> str:
    clean_date = parse_iso_date_prefix(local_date) or str(local_date or "").strip() or "nodate"
    items = failed_agencies if isinstance(failed_agencies, (list, tuple, set)) else []
    sorted_ids = sorted({str(x or "").strip().lower() for x in items if str(x or "").strip()})
    return f"wl:sweep:{clean_date}:degraded:{'-'.join(sorted_ids) or 'unknown'}"


def build_staff_prop_event_id(prop_id: str, action: str, date_iso: str = "") -> str:
    pid = str(prop_id or "").strip()
    act = slug_token(action)
    if act == "edited":
        return f"staff:prop:{pid}:edited:{date_iso or 'nodate'}"
    return f"staff:prop:{pid}:{act}"


def build_staff_review_event_id(cycle_key: str, app_no: str, tier: str) -> str:
    return f"staff:review:{str(cycle_key or '').strip()}:{str(app_no or '').strip()}:t{str(tier or '0').strip() or '0'}"


def build_agenda_cycle_event_id(cycle_key: str) -> str:
    return f"agenda:cycle:{str(cycle_key or '').strip()}"


def build_agenda_item_event_id(cycle_key: str, app_no: str, kind: str) -> str:
    return f"agenda:item:{str(cycle_key or '').strip()}:{str(app_no or '').strip()}:{kind}"


def build_permit_coh_event_id(hcad_raw: object, project_no: object, fallback_desc: str = "", date_iso: str = "") -> str:
    key = slug_token(project_no) if str(project_no or "").strip() else slug_token(f"{fallback_desc}-{date_iso}")
    return f"permit:coh:{normalize_hcad_13(hcad_raw)}:{key}"


def build_permit_dshs_event_id(hcad_raw: object, notif_no: object, fallback_addr: str = "", date_iso: str = "") -> str:
    key = slug_token(notif_no) if str(notif_no or "").strip() else slug_token(f"{fallback_addr}-{date_iso}")
    return f"permit:dshs:{normalize_hcad_13(hcad_raw)}:{key}"


def build_hahc_cycle_event_id(meeting_date: str) -> str:
    return f"hahc:cycle:{str(meeting_date or '').strip()}"


def build_hahc_guideline_event_id(meeting_date: str, title_or_url: str) -> str:
    return f"hahc:guideline:{str(meeting_date or '').strip()}:{slug_token(title_or_url)}"


def build_hahc_filing_event_id(filing_id: str, outcome: object) -> str:
    return f"hahc:filing:{str(filing_id or '').strip()}:{slug_token(outcome or 'pending_agenda')}"


# ---------------------------------------------------------------------------
# Internal Helpers & Event Factory
# ---------------------------------------------------------------------------

def _read_json_optional(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _find_checklist_row_by_id(checklist: object, check_id: str) -> dict | None:
    if not isinstance(checklist, list):
        return None
    target = str(check_id or "").strip().lower()
    for row in checklist:
        if isinstance(row, dict) and str(row.get("id") or "").strip().lower() == target:
            return row
    return None


def _make_event(
    *,
    event_id: str,
    stream: str,
    kind: str,
    severity: str,
    adverse: bool,
    date_iso: str,
    timestamp_iso: str,
    title: str,
    summary: str,
    discovered_at: str = "",
    logged_at: str = "",
    address: str = "",
    hcad: str = "",
    watch_property_id: str = "",
    check_id: str = "",
    cycle_key: str = "",
    app_no: str = "",
    occ_key: str = "",
    building_id: str = "",
    lat: float | None = None,
    lng: float | None = None,
    year_built: int | str | None = None,
    designation: str = "",
    links: list[dict[str, str]] | None = None,
    permit_meta: dict | None = None,
) -> dict:
    effective_ts = logged_at or discovered_at or timestamp_iso
    local_date = to_chicago_date_iso(effective_ts, date_iso) or date_iso
    norm_hcad = normalize_hcad_13(hcad) if str(hcad or "").strip() else ""
    if norm_hcad == "nohcad" or not norm_hcad.isdigit():
        norm_hcad = sanitize_text(hcad)
    clean_lat = float(lat) if isinstance(lat, (int, float)) else None
    clean_lng = float(lng) if isinstance(lng, (int, float)) else None
    out: dict = {
        "id": event_id,
        "stream": stream,
        "kind": kind,
        "severity": severity if severity in ("critical", "warning", "info") else "info",
        "adverse": bool(adverse),
        "date": date_iso or local_date,
        "localDate": local_date,
        "timestamp": timestamp_iso,
        "discoveredAt": discovered_at or timestamp_iso,
        "loggedAt": logged_at or "",
        "title": sanitize_text(title),
        "summary": sanitize_text(summary),
        "address": sanitize_text(address),
        "hcad": norm_hcad,
        "watchPropertyId": str(watch_property_id or "").strip(),
        "checkId": str(check_id or "").strip().lower(),
        "cycleKey": str(cycle_key or "").strip(),
        "appNo": str(app_no or "").strip(),
        "occKey": str(occ_key or "").strip(),
        "buildingId": str(building_id or "").strip(),
        "lat": clean_lat,
        "lng": clean_lng,
        "yearBuilt": year_built if year_built not in (None, "", 0, "0") else None,
        "designation": sanitize_text(designation),
        "links": sanitize_links(links or []),
    }
    if permit_meta:
        out["permitMeta"] = permit_meta
    return out


# ---------------------------------------------------------------------------
# Stream 1 & Stream 3: Watchlist Diffing
# ---------------------------------------------------------------------------

def _diff_property_watchlist(
    before_wl: dict,
    after_wl: dict,
    now_iso: str,
    *,
    seed_mode: bool = False,
    retention_floor_iso: str = "",
) -> list[dict]:
    events: list[dict] = []
    local_today = to_chicago_date_iso(now_iso)

    before_list = before_wl.get("watchedProperties") if isinstance(before_wl.get("watchedProperties"), list) else []
    after_list = after_wl.get("watchedProperties") if isinstance(after_wl.get("watchedProperties"), list) else []

    before_by_id = {
        str(p.get("id") or "").strip(): p
        for p in before_list
        if isinstance(p, dict) and str(p.get("id") or "").strip()
    }
    after_by_id = {
        str(p.get("id") or "").strip(): p
        for p in after_list
        if isinstance(p, dict) and str(p.get("id") or "").strip()
    }

    # Degraded sweep health event (Stream 1)
    sweep_health = after_wl.get("lastSweepHealth") if isinstance(after_wl.get("lastSweepHealth"), dict) else {}
    failed_agencies = [str(x).strip().lower() for x in (sweep_health.get("failedAgencies") or []) if str(x).strip()]
    if failed_agencies and not seed_mode:
        agencies_meta = sweep_health.get("agencies") if isinstance(sweep_health.get("agencies"), dict) else {}
        failed_details: list[str] = []
        for cid in failed_agencies:
            info = agencies_meta.get(cid) if isinstance(agencies_meta.get(cid), dict) else {}
            lbl = info.get("label") or AUTOMATED_SWEEP_AGENCY_LABELS.get(cid, cid)
            err = sanitize_text(info.get("error") or "query unavailable")
            failed_details.append(f"{lbl} ({err})")
        succeeded = sweep_health.get("succeededAgencies", max(0, 8 - len(failed_agencies)))
        total_ag = sweep_health.get("totalAgencies", 8)
        sweep_ts = str(sweep_health.get("timestamp") or after_wl.get("lastAutoSweepAt") or now_iso)
        sweep_day = to_chicago_date_iso(sweep_ts, local_today) or local_today
        events.append(
            _make_event(
                event_id=build_sweep_degraded_event_id(sweep_day, failed_agencies),
                stream="watchlist",
                kind="watchlist_sweep_degraded",
                severity="warning",
                adverse=False,
                date_iso=sweep_day,
                timestamp_iso=sweep_ts,
                discovered_at=sweep_ts,
                title=f"Daily 8-Agency Sweep Degraded ({succeeded}/{total_ag} agencies OK)",
                summary=f"{len(failed_agencies)} agency endpoint(s) did not complete cleanly: {'; '.join(failed_details)}. Verify affected checks manually if time-sensitive.",
            )
        )

    # Check archivals / removals on previously tracked properties
    for pid, b_prop in before_by_id.items():
        a_prop = after_by_id.get(pid)
        was_archived = str(b_prop.get("trackerStatus") or "").strip() == "Archived (Tier 3)"
        is_missing = a_prop is None
        is_now_archived = a_prop is not None and str(a_prop.get("trackerStatus") or "").strip() == "Archived (Tier 3)"
        if not was_archived and (is_missing or is_now_archived):
            ref_p = a_prop or b_prop
            prop_title = ref_p.get("propertyName") or ref_p.get("address") or pid
            ev_ts = str(ref_p.get("archivedAt") or ref_p.get("updatedAt") or now_iso)
            ev_date = to_chicago_date_iso(ev_ts, local_today) or local_today
            events.append(
                _make_event(
                    event_id=build_staff_prop_event_id(pid, "archived"),
                    stream="staff",
                    kind="watchlist_property_archived",
                    severity="info",
                    adverse=False,
                    date_iso=ev_date,
                    timestamp_iso=ev_ts if "T" in ev_ts else now_iso,
                    discovered_at=now_iso,
                    logged_at=ev_ts if "T" in ev_ts else "",
                    title=f"Archived from Active Watchlist: {prop_title}",
                    summary=(
                        f"Reclassified to Tier 3 / Archived ({ref_p.get('address') or 'Houston'})."
                        if is_now_archived
                        else f"Removed from Property Watchlist ({ref_p.get('address') or 'Houston'})."
                    ),
                    address=str(ref_p.get("address") or ""),
                    hcad=str(ref_p.get("hcadAccount") or ""),
                    watch_property_id=pid if is_now_archived else "",
                    building_id=str(ref_p.get("buildingId") or ""),
                    lat=ref_p.get("lat"),
                    lng=ref_p.get("lng"),
                    year_built=ref_p.get("yearBuilt"),
                    designation=str(ref_p.get("designation") or ""),
                )
            )

    # Inspect active/present properties in after_wl
    for pid, a_prop in after_by_id.items():
        b_prop = before_by_id.get(pid)
        prop_title = str(a_prop.get("propertyName") or a_prop.get("address") or pid).strip()
        prop_addr = str(a_prop.get("address") or "").strip()
        prop_hcad = str(a_prop.get("hcadAccount") or "").strip()
        is_archived = str(a_prop.get("trackerStatus") or "").strip() == "Archived (Tier 3)"

        if b_prop is None and not is_archived:
            kd = a_prop.get("keyDates") if isinstance(a_prop.get("keyDates"), dict) else {}
            start_iso = (
                parse_iso_date_prefix(kd.get("monitoringStarted"))
                or parse_iso_date_prefix(a_prop.get("updatedAt"))
                or parse_iso_date_prefix(a_prop.get("pcMeetingDate"))
                or local_today
            )
            if not seed_mode or not retention_floor_iso or start_iso >= retention_floor_iso:
                seed_ts = _seed_ts_clamped(start_iso, local_today, now_iso) if seed_mode else now_iso
                is_staff_authored = bool(
                    not pid.startswith("plat-")
                    or a_prop.get("humanEdited")
                    or a_prop.get("reportedBy")
                    or a_prop.get("updatedBy")
                )
                events.append(
                    _make_event(
                        event_id=build_staff_prop_event_id(pid, "added"),
                        stream="staff" if is_staff_authored else "watchlist",
                        kind="watchlist_property_added",
                        severity="warning" if str(a_prop.get("tier") or "1") == "1" else "info",
                        adverse=False,
                        date_iso=start_iso,
                        timestamp_iso=seed_ts,
                        discovered_at=seed_ts,
                        logged_at=str(a_prop.get("updatedAt") or "") if not seed_mode else "",
                        title=f"Added to Property Watchlist (Tier {a_prop.get('tier') or '1'}): {prop_title}",
                        summary=(
                            f"Tracking {prop_addr or prop_title}"
                            f"{f' (Built {a_prop.get('yearBuilt')})' if a_prop.get('yearBuilt') else ''}"
                            f"{f' - {a_prop.get('designation')}' if a_prop.get('designation') else ''}. "
                            f"{str(a_prop.get('notes') or '')[:220]}"
                        ).strip(),
                        address=prop_addr,
                        hcad=prop_hcad,
                        watch_property_id=pid,
                        building_id=str(a_prop.get("buildingId") or ""),
                        lat=a_prop.get("lat"),
                        lng=a_prop.get("lng"),
                        year_built=a_prop.get("yearBuilt"),
                        designation=str(a_prop.get("designation") or ""),
                        links=sanitize_links(a_prop.get("driveLinks") or []),
                    )
                )

        elif b_prop is not None and not is_archived:
            # Check metadata / snowball search terms / Drive link edits (Stream 3)
            changed_fields: list[str] = []
            field_labels = (
                ("notes", "dossier notes"),
                ("ownerOfRecord", "owner of record"),
                ("buyerRelatedEntities", "buyer/related entities"),
                ("lender", "lender"),
                ("designFirm", "design firm"),
                ("phPastActions", "past PH actions"),
                ("trackerStatus", "tracker status"),
                ("threatCategory", "threat category"),
                ("designation", "designation"),
                ("ilmsProjectNo", "ILMS project #"),
            )
            for key_f, label_f in field_labels:
                if sanitize_text(b_prop.get(key_f)) != sanitize_text(a_prop.get(key_f)):
                    changed_fields.append(label_f)

            b_st = b_prop.get("searchTerms") if isinstance(b_prop.get("searchTerms"), dict) else {}
            a_st = a_prop.get("searchTerms") if isinstance(a_prop.get("searchTerms"), dict) else {}
            for cat in ("entities", "dbas", "officers", "legalKeywords", "altAddresses"):
                if sorted(str(x).strip() for x in (b_st.get(cat) or [])) != sorted(
                    str(x).strip() for x in (a_st.get(cat) or [])
                ):
                    changed_fields.append("snowball search terms")
                    break
            if str(a_prop.get("searchTermsUpdatedAt") or "") != str(b_prop.get("searchTermsUpdatedAt") or ""):
                if "snowball search terms" not in changed_fields and sorted(
                    str(x).strip() for x in (b_st.get("projectNumbers") or [])
                ) != sorted(str(x).strip() for x in (a_st.get("projectNumbers") or [])):
                    changed_fields.append("snowball project numbers")

            b_links = [lk["url"] for lk in sanitize_links(b_prop.get("driveLinks") or b_prop.get("documents"))]
            a_links = [lk["url"] for lk in sanitize_links(a_prop.get("driveLinks") or a_prop.get("documents"))]
            if sorted(b_links) != sorted(a_links):
                changed_fields.append("Drive / document links")

            if changed_fields:
                upd_raw = str(a_prop.get("updatedAt") or now_iso)
                ev_day = to_chicago_date_iso(upd_raw, local_today) or local_today
                by_txt = f" by {a_prop.get('updatedBy')}" if a_prop.get("updatedBy") else ""
                events.append(
                    _make_event(
                        event_id=build_staff_prop_event_id(pid, "edited", ev_day),
                        stream="staff",
                        kind="watchlist_property_edited",
                        severity="info",
                        adverse=False,
                        date_iso=ev_day,
                        timestamp_iso=upd_raw if "T" in upd_raw else now_iso,
                        discovered_at=now_iso,
                        logged_at=upd_raw if "T" in upd_raw else "",
                        title=f"Watchlist Dossier Updated: {prop_title}",
                        summary=f"Updated {', '.join(changed_fields)}{by_txt}.",
                        address=prop_addr,
                        hcad=prop_hcad,
                        watch_property_id=pid,
                        building_id=str(a_prop.get("buildingId") or ""),
                        lat=a_prop.get("lat"),
                        lng=a_prop.get("lng"),
                        year_built=a_prop.get("yearBuilt"),
                        designation=str(a_prop.get("designation") or ""),
                        links=sanitize_links(a_prop.get("driveLinks") or []),
                    )
                )

        # Checklist comparison strictly by canonical row["id"] ("chk-01".."chk-15")
        a_checklist = a_prop.get("checklist") if isinstance(a_prop.get("checklist"), list) else []
        b_checklist = b_prop.get("checklist") if (b_prop and isinstance(b_prop.get("checklist"), list)) else []

        for a_row in a_checklist:
            if not isinstance(a_row, dict):
                continue
            row_id = str(a_row.get("id") or "").strip().lower()
            if not row_id:
                continue
            row_activity = str(a_row.get("activity") or row_id).strip()
            b_row = _find_checklist_row_by_id(b_checklist, row_id)

            before_auto_ids: set[str] = set()
            before_manual_ids: set[str] = set()
            for b_chk in (b_row.get("checks") if isinstance(b_row, dict) else None) or []:
                if not isinstance(b_chk, dict):
                    continue
                if is_auto_sweep_check(b_chk):
                    before_auto_ids.add(build_auto_check_event_id(pid, row_id, b_chk))
                elif not is_plat_seed_check(b_chk):
                    before_manual_ids.add(build_manual_check_event_id(pid, row_id, b_chk))

            has_new_adverse_auto_on_row = False
            for a_chk in a_row.get("checks") or []:
                if not isinstance(a_chk, dict):
                    continue
                res_txt = str(a_chk.get("result") or "").strip()
                if not res_txt or is_plat_seed_check(a_chk):
                    continue
                chk_date = parse_iso_date_prefix(a_chk.get("date")) or local_today

                if is_auto_sweep_check(a_chk):
                    ev_id = build_auto_check_event_id(pid, row_id, a_chk)
                    if ev_id in before_auto_ids:
                        continue
                    disc_raw = str(a_chk.get("discoveredAt") or "")
                    effective_seed_day = parse_iso_date_prefix(disc_raw) or chk_date
                    if seed_mode and retention_floor_iso and effective_seed_day < retention_floor_iso:
                        continue
                    adverse = is_adverse_auto_sweep_check(a_chk)
                    if adverse:
                        has_new_adverse_auto_on_row = True
                    # In seed mode, suppress routine non-adverse baseline confirmations to keep briefing signal crisp
                    if seed_mode and not adverse:
                        continue
                    sev = (
                        "critical"
                        if adverse and row_id in HIGH_SEVERITY_CHECK_IDS
                        else ("warning" if adverse else "info")
                    )
                    ts_val = (
                        (disc_raw if "T" in disc_raw else _seed_ts_clamped(effective_seed_day, local_today, now_iso))
                        if seed_mode
                        else now_iso
                    )
                    events.append(
                        _make_event(
                            event_id=ev_id,
                            stream="watchlist",
                            kind="watchlist_auto_finding",
                            severity=sev,
                            adverse=adverse,
                            date_iso=chk_date,
                            timestamp_iso=ts_val,
                            discovered_at=ts_val,
                            title=f"{prop_title}: {row_activity}",
                            summary=re.sub(r"^\[AUTO-SWEEP\]\s*", "", res_txt, flags=re.I),
                            address=prop_addr,
                            hcad=prop_hcad,
                            watch_property_id=pid,
                            check_id=row_id,
                            building_id=str(a_prop.get("buildingId") or ""),
                            lat=a_prop.get("lat"),
                            lng=a_prop.get("lng"),
                            year_built=a_prop.get("yearBuilt"),
                            designation=str(a_prop.get("designation") or ""),
                            links=sanitize_links(a_chk.get("links") or []),
                        )
                    )
                else:
                    ev_id = build_manual_check_event_id(pid, row_id, a_chk)
                    if ev_id in before_manual_ids:
                        continue
                    logged_raw = str(a_chk.get("loggedAt") or "")
                    effective_day = (
                        to_chicago_date_iso(logged_raw, chk_date) if logged_raw else chk_date
                    )
                    if seed_mode and retention_floor_iso and effective_day < retention_floor_iso:
                        continue
                    # Skip generic "no results" routine stamps during historical seed only; in live diffs emit all new manual notes
                    if seed_mode and re.match(
                        r"^(?:no\s+(?:new\s+)?(?:demo\s+permit|results?)|none(?:\s+found)?|old/irrelevant.*)$",
                        res_txt.strip(),
                        re.I,
                    ):
                        continue
                    is_flagged_manual = a_row.get("status") == "Flagged"
                    ts_val = (
                        logged_raw
                        if "T" in logged_raw
                        else (_seed_ts_clamped(chk_date, local_today, now_iso) if seed_mode else now_iso)
                    )
                    events.append(
                        _make_event(
                            event_id=ev_id,
                            stream="staff",
                            kind="watchlist_manual_check",
                            severity="warning" if is_flagged_manual else "info",
                            adverse=False,
                            date_iso=chk_date,
                            timestamp_iso=ts_val,
                            discovered_at=ts_val if seed_mode else now_iso,
                            logged_at=logged_raw,
                            title=f"Staff Check Logged - {prop_title}: {row_activity}",
                            summary=res_txt,
                            address=prop_addr,
                            hcad=prop_hcad,
                            watch_property_id=pid,
                            check_id=row_id,
                            building_id=str(a_prop.get("buildingId") or ""),
                            lat=a_prop.get("lat"),
                            lng=a_prop.get("lng"),
                            year_built=a_prop.get("yearBuilt"),
                            designation=str(a_prop.get("designation") or ""),
                            links=sanitize_links(a_chk.get("links") or []),
                        )
                    )

            # Row status escalation (`To Do` / `In Progress` / `Complete` -> `Flagged`)
            prev_status = str(b_row.get("status") or "To Do").strip() if isinstance(b_row, dict) else "To Do"
            curr_status = str(a_row.get("status") or "To Do").strip()
            if not seed_mode and curr_status == "Flagged" and prev_status != "Flagged":
                raw_status_changed = str(a_row.get("statusChangedAt") or "").strip()
                is_human_esc = bool(
                    not has_new_adverse_auto_on_row
                    and (a_row.get("humanStatusEdited") is True or raw_status_changed)
                )
                esc_ts = raw_status_changed or (str(a_row.get("updatedAt") or "").strip() if is_human_esc else "")
                esc_day = (
                    to_chicago_date_iso(
                        esc_ts,
                        parse_iso_date_prefix(a_row.get("lastChecked")) or local_today,
                    )
                    if is_human_esc
                    else local_today
                ) or local_today
                events.append(
                    _make_event(
                        event_id=build_watch_status_event_id(pid, row_id, curr_status, esc_day),
                        stream="staff" if is_human_esc else "watchlist",
                        kind="watchlist_status_escalation",
                        severity="critical" if row_id in HIGH_SEVERITY_CHECK_IDS else "warning",
                        adverse=True,
                        date_iso=esc_day,
                        timestamp_iso=esc_ts if (is_human_esc and "T" in esc_ts) else now_iso,
                        discovered_at=now_iso,
                        logged_at=esc_ts if (is_human_esc and "T" in esc_ts) else "",
                        title=f"Checklist Escalated ({prev_status} -> Flagged): {prop_title}",
                        summary=f"{row_activity} ({row_id.upper()}) escalated from '{prev_status}' to 'Flagged' on {prop_addr or prop_title}.",
                        address=prop_addr,
                        hcad=prop_hcad,
                        watch_property_id=pid,
                        check_id=row_id,
                        building_id=str(a_prop.get("buildingId") or ""),
                        lat=a_prop.get("lat"),
                        lng=a_prop.get("lng"),
                        year_built=a_prop.get("yearBuilt"),
                        designation=str(a_prop.get("designation") or ""),
                    )
                )

    return events


# ---------------------------------------------------------------------------
# Stream 2 & Stream 3: Planning Commission Agenda & Reviews Diffing
# ---------------------------------------------------------------------------

def _extract_plat_cycles(doc: dict) -> dict[str, dict]:
    if not isinstance(doc, dict) or not doc:
        return {}
    if isinstance(doc.get("cycles"), dict) and doc["cycles"]:
        return {str(k): v for k, v in doc["cycles"].items() if isinstance(v, dict)}
    ck = str(doc.get("cycleKey") or doc.get("meetingDate") or "").strip()
    if ck and isinstance(doc.get("items"), list):
        return {ck: doc}
    return {}


def _is_notable_new_cycle_item(it: dict, reviews: dict) -> bool:
    """Filter bulk 100+ item spreadsheets when seeding a brand-new cycle while keeping all synthetic fixtures."""
    app = str(it.get("app") or "").strip()
    section = str(it.get("section") or "")
    typ = str(it.get("type") or "")
    is_dpv = app.upper().startswith("DPV") or "K." in section or "DPV" in typ.upper()
    if is_dpv or it.get("noticePdf"):
        return True
    rev = reviews.get(app) if isinstance(reviews, dict) else None
    if isinstance(rev, dict) and rev.get("tierConfirmed") and str(rev.get("tier") or "") in ("1", "2"):
        return True
    cls = it.get("_c")
    # Minimal synthetic unit-test items without `_c` are always surfaced
    if not isinstance(cls, dict):
        return True
    grp = str(cls.get("group") or "")
    if grp in ("out", "etj") and not cls.get("overriddenFromOut"):
        return False
    if grp == "designated":
        return True
    atlas = it.get("atlas") if isinstance(it.get("atlas"), dict) else {}
    if atlas.get("hasActivePermit") or atlas.get("designations"):
        return True
    yr = int(atlas.get("earliestYearBuilt") or 0)
    if 1836 <= yr <= 1940:
        return True
    if 1941 <= yr <= 1976 and (
        "variance" in str(it.get("special") or "").lower()
        or "D." in section
        or "F." in section
        or it.get("deferral")
    ):
        return True
    return False


def _plat_item_event_fields(it: dict, cycle_key: str) -> dict:
    app = str(it.get("app") or "").strip()
    atlas = it.get("atlas") if isinstance(it.get("atlas"), dict) else {}
    addr = str(
        atlas.get("primaryAddress")
        or it.get("noticeLocation")
        or (atlas.get("addresses") or [""])[0]
        or ""
    ).strip()
    links: list[dict[str, str]] = []
    if it.get("pdf"):
        links.append({"label": "Plat PDF", "url": str(it["pdf"])})
    if it.get("noticePdf"):
        links.append({"label": "Public Hearing Notice PDF", "url": str(it["noticePdf"])})
    desig = ", ".join(atlas.get("designations") or []) or str(it.get("hd") or "")
    return {
        "address": addr,
        "hcad": str(it.get("hcad") or ""),
        "cycle_key": cycle_key,
        "app_no": app,
        "occ_key": str(it.get("occurrenceKey") or f"{cycle_key}::{app}"),
        "building_id": str(atlas.get("buildingId") or ""),
        "lat": atlas.get("lat"),
        "lng": atlas.get("lng"),
        "year_built": atlas.get("earliestYearBuilt"),
        "designation": desig,
        "links": links,
    }


def _diff_plat_agenda(
    before_plat: dict,
    after_plat: dict,
    now_iso: str,
    *,
    seed_mode: bool = False,
    retention_floor_iso: str = "",
) -> list[dict]:
    events: list[dict] = []
    local_today = to_chicago_date_iso(now_iso)
    b_cycles = _extract_plat_cycles(before_plat)
    a_cycles = _extract_plat_cycles(after_plat)

    for cycle_key in sorted(a_cycles.keys()):
        if seed_mode and retention_floor_iso and cycle_key < retention_floor_iso:
            continue
        a_cyc = a_cycles[cycle_key]
        b_cyc = b_cycles.get(cycle_key)
        a_items = [x for x in (a_cyc.get("items") or []) if isinstance(x, dict) and str(x.get("app") or "").strip()]
        a_reviews = a_cyc.get("reviews") if isinstance(a_cyc.get("reviews"), dict) else {}
        b_reviews = (b_cyc.get("reviews") if isinstance(b_cyc, dict) and isinstance(b_cyc.get("reviews"), dict) else {}) or {}

        seed_cyc_ts = _seed_ts_clamped(cycle_key, local_today, now_iso) if seed_mode else now_iso

        if b_cyc is None:
            summ = a_cyc.get("summary") if isinstance(a_cyc.get("summary"), dict) else {}
            tot = summ.get("totalAgendaItems", len(a_items))
            to_chk = summ.get("toCheckCount", len(a_items))
            dpv_cnt = summ.get("dpvVarianceCount", 0)
            ph_cnt = summ.get("publicHearingNoticeCount", 0)
            cyc_links: list[dict[str, str]] = []
            if a_cyc.get("sourceSpreadsheetUrl"):
                cyc_links.append({"label": "Agenda Spreadsheet (.xlsx)", "url": str(a_cyc["sourceSpreadsheetUrl"])})
            if a_cyc.get("agendaOutlineUrl"):
                cyc_links.append({"label": "PC Draft Agenda Outline (PDF)", "url": str(a_cyc["agendaOutlineUrl"])})
            events.append(
                _make_event(
                    event_id=build_agenda_cycle_event_id(cycle_key),
                    stream="agenda",
                    kind="agenda_cycle_added",
                    severity="warning" if (dpv_cnt or to_chk) else "info",
                    adverse=False,
                    date_iso=cycle_key,
                    timestamp_iso=seed_cyc_ts,
                    discovered_at=seed_cyc_ts,
                    title=f"Planning Commission Agenda Posted: {cycle_key}",
                    summary=(
                        f"Ingested {tot} agenda items ({to_chk} flagged for historic review, "
                        f"{dpv_cnt} Section K Development Plat Variance(s), {ph_cnt} Public Hearing Notice(s))."
                    ),
                    cycle_key=cycle_key,
                    links=cyc_links,
                )
            )
            for it in a_items:
                if not _is_notable_new_cycle_item(it, a_reviews):
                    continue
                app = str(it.get("app") or "").strip()
                section = str(it.get("section") or "")
                typ = str(it.get("type") or "")
                is_dpv = app.upper().startswith("DPV") or "K." in section or "DPV" in typ.upper()
                atlas = it.get("atlas") if isinstance(it.get("atlas"), dict) else {}
                yr = int(atlas.get("earliestYearBuilt") or 0)
                sub_iso = parse_iso_date_prefix(it.get("submittedIso")) or cycle_key
                ev_day = sub_iso if (seed_mode and (not retention_floor_iso or sub_iso >= retention_floor_iso)) else cycle_key
                ev_ts = _seed_ts_clamped(ev_day, local_today, now_iso) if seed_mode else now_iso
                is_crit = bool(atlas.get("hasActivePermit") or atlas.get("designations") or (1836 <= yr <= 1940 and is_dpv))
                kind_tok = "dpv" if is_dpv else ("notice_pdf" if (it.get("noticePdf") and not it.get("pdf")) else "added")
                events.append(
                    _make_event(
                        event_id=build_agenda_item_event_id(cycle_key, app, kind_tok),
                        stream="agenda",
                        kind=f"agenda_item_{kind_tok}",
                        severity="critical" if is_crit else ("warning" if (is_dpv or it.get("noticePdf") or 1836 <= yr <= 1940) else "info"),
                        adverse=is_crit,
                        date_iso=ev_day,
                        timestamp_iso=ev_ts,
                        discovered_at=ev_ts,
                        title=(
                            f"Section K DPV Filed ({cycle_key}): {it.get('name') or app}"
                            if is_dpv
                            else (
                                f"Public Hearing Notice Posted ({cycle_key}): {it.get('name') or app}"
                                if kind_tok == "notice_pdf"
                                else f"Notable Plat Filing ({cycle_key}): {it.get('name') or app} ({app})"
                            )
                        ),
                        summary=(
                            f"{typ or 'Plat'} in {section or 'Agenda'}"
                            f"{f' | Built {yr}' if yr >= 1836 else ''}"
                            f"{f' | Hearing {it.get('hearingDateIso')}' if it.get('hearingDateIso') else ''}"
                            f"{f' | {it.get('varianceSummary')}' if it.get('varianceSummary') else ''}."
                        ),
                        **_plat_item_event_fields(it, cycle_key),
                    )
                )
        else:
            b_items_by_app = {
                str(x.get("app") or "").strip(): x
                for x in (b_cyc.get("items") or [])
                if isinstance(x, dict) and str(x.get("app") or "").strip()
            }
            for it in a_items:
                app = str(it.get("app") or "").strip()
                b_it = b_items_by_app.get(app)
                section = str(it.get("section") or "")
                typ = str(it.get("type") or "")
                is_dpv = app.upper().startswith("DPV") or "K." in section or "DPV" in typ.upper()
                atlas = it.get("atlas") if isinstance(it.get("atlas"), dict) else {}
                yr = int(atlas.get("earliestYearBuilt") or 0)

                if b_it is None:
                    cls = it.get("_c")
                    if isinstance(cls, dict) and cls.get("group") in ("out", "etj") and not cls.get("overriddenFromOut"):
                        continue
                    kind_tok = "dpv" if is_dpv else "added"
                    is_crit = bool(atlas.get("hasActivePermit") or atlas.get("designations"))
                    events.append(
                        _make_event(
                            event_id=build_agenda_item_event_id(cycle_key, app, kind_tok),
                            stream="agenda",
                            kind=f"agenda_item_{kind_tok}",
                            severity="critical" if is_crit else ("warning" if (is_dpv or 1836 <= yr <= 1976) else "info"),
                            adverse=is_crit,
                            date_iso=local_today or cycle_key,
                            timestamp_iso=now_iso,
                            discovered_at=now_iso,
                            title=(
                                f"New Section K DPV Added ({cycle_key}): {it.get('name') or app}"
                                if is_dpv
                                else f"New Agenda Item Added ({cycle_key}): {it.get('name') or app} ({app})"
                            ),
                            summary=(
                                f"{typ or 'Plat'} in {section or 'Agenda'}"
                                f"{f' | Built {yr}' if yr >= 1836 else ''}"
                                f"{f' | {it.get('varianceSummary')}' if it.get('varianceSummary') else ''}."
                            ),
                            **_plat_item_event_fields(it, cycle_key),
                        )
                    )
                else:
                    if not str(b_it.get("pdf") or "").strip() and str(it.get("pdf") or "").strip():
                        events.append(
                            _make_event(
                                event_id=build_agenda_item_event_id(cycle_key, app, "plat_pdf"),
                                stream="agenda",
                                kind="agenda_item_plat_pdf",
                                severity="info",
                                adverse=False,
                                date_iso=local_today or cycle_key,
                                timestamp_iso=now_iso,
                                discovered_at=now_iso,
                                title=f"Plat Drawing PDF Posted: {it.get('name') or app} ({app})",
                                summary=f"Submittal plat PDF attached for {it.get('name') or app} on the {cycle_key} agenda.",
                                **_plat_item_event_fields(it, cycle_key),
                            )
                        )
                    if not str(b_it.get("noticePdf") or "").strip() and str(it.get("noticePdf") or "").strip():
                        events.append(
                            _make_event(
                                event_id=build_agenda_item_event_id(cycle_key, app, "notice_pdf"),
                                stream="agenda",
                                kind="agenda_item_notice_pdf",
                                severity="warning",
                                adverse=False,
                                date_iso=local_today or cycle_key,
                                timestamp_iso=now_iso,
                                discovered_at=now_iso,
                                title=f"Public Hearing Notice PDF Posted: {it.get('name') or app} ({app})",
                                summary=(
                                    f"Official Public Hearing Notice PDF posted for {it.get('name') or app}"
                                    f"{f' (Hearing {it.get('hearingDateIso')})' if it.get('hearingDateIso') else ''}."
                                ),
                                **_plat_item_event_fields(it, cycle_key),
                            )
                        )
                    b_def = str(b_it.get("deferral") or "").strip()
                    a_def = str(it.get("deferral") or "").strip()
                    if a_def and a_def != b_def:
                        events.append(
                            _make_event(
                                event_id=build_agenda_item_event_id(cycle_key, app, f"deferral:{slug_token(a_def)}"),
                                stream="agenda",
                                kind="agenda_item_deferral",
                                severity="info",
                                adverse=False,
                                date_iso=local_today or cycle_key,
                                timestamp_iso=now_iso,
                                discovered_at=now_iso,
                                title=f"Deferral Status Updated ({a_def}): {it.get('name') or app} ({app})",
                                summary=f"Item {app} marked '{a_def}' on the {cycle_key} Planning Commission agenda.",
                                **_plat_item_event_fields(it, cycle_key),
                            )
                        )

        # Staff Plat Review confirmations / tier transitions (Stream 3)
        items_lookup = {str(x.get("app") or "").strip(): x for x in a_items}
        for app_no, rev in a_reviews.items():
            if not isinstance(rev, dict) or not rev.get("tierConfirmed"):
                continue
            b_rev = b_reviews.get(app_no) if isinstance(b_reviews.get(app_no), dict) else {}
            tier_val = str(rev.get("tier") or "").strip()
            if not tier_val:
                continue
            if b_rev.get("tierConfirmed") and str(b_rev.get("tier") or "").strip() == tier_val:
                continue
            upd_raw = str(rev.get("updated") or "")
            rev_day = to_chicago_date_iso(upd_raw, cycle_key) or cycle_key
            if seed_mode and retention_floor_iso and rev_day < retention_floor_iso:
                continue
            matched_it = items_lookup.get(str(app_no).strip(), {"app": app_no, "name": app_no})
            ts_val = upd_raw if "T" in upd_raw else (_seed_ts_clamped(rev_day, local_today, now_iso) if seed_mode else now_iso)
            reviewer_txt = f" by {rev.get('reviewer')}" if rev.get("reviewer") else ""
            events.append(
                _make_event(
                    event_id=build_staff_review_event_id(cycle_key, app_no, tier_val),
                    stream="staff",
                    kind="staff_plat_review",
                    severity="warning" if tier_val == "1" else "info",
                    adverse=False,
                    date_iso=rev_day,
                    timestamp_iso=ts_val,
                    discovered_at=ts_val if seed_mode else now_iso,
                    logged_at=upd_raw if "T" in upd_raw else "",
                    title=f"Plat Review Confirmed (Tier {tier_val}): {matched_it.get('name') or app_no} ({app_no})",
                    summary=(
                        f"Confirmed Tier {tier_val}{reviewer_txt}"
                        f"{f' ({rev.get('threatCategory')})' if rev.get('threatCategory') else ''}"
                        f"{f': {rev.get('why')}' if rev.get('why') else ''}."
                    ),
                    **_plat_item_event_fields(matched_it, cycle_key),
                )
            )

    return events


# ---------------------------------------------------------------------------
# Stream 4: Citywide Historic Permits, DSHS Asbestos & HAHC COA Tracker Diffing
# ---------------------------------------------------------------------------

def _build_permit_alert_meta(alert: dict, kind: str, record: dict) -> dict:
    return {
        "alertKind": kind,
        "title": sanitize_text(alert.get("title") or alert.get("building_name") or alert.get("address") or ""),
        "address": sanitize_text(alert.get("address") or ""),
        "hcad_num": normalize_hcad_13(alert.get("hcad_num") or ""),
        "related_hcads": [
            str(x) for x in (alert.get("related_hcads") or []) if str(x or "").strip()
        ],
        "watchlist_id": str(alert.get("watchlist_id") or "").strip(),
        "building_id": str(alert.get("building_id") or "").strip(),
        "year_built": alert.get("year_built"),
        "yr_remodel": alert.get("yr_remodel"),
        "architect": sanitize_text(alert.get("architect") or ""),
        "style": sanitize_text(alert.get("style") or ""),
        "neighborhood": sanitize_text(alert.get("neighborhood") or ""),
        "historic_district": sanitize_text(alert.get("historic_district") or ""),
        "landmark_name": sanitize_text(alert.get("landmark_name") or ""),
        "good_brick_summary": sanitize_text(alert.get("good_brick_summary") or ""),
        "owner": sanitize_text(alert.get("owner") or ""),
        "lat": alert.get("lat"),
        "lng": alert.get("lng") if alert.get("lng") is not None else alert.get("lon"),
        "record": {
            k: sanitize_text(v) if isinstance(v, str) else v
            for k, v in record.items()
            if k
            in (
                "project_no",
                "date",
                "permit_type",
                "permit_type_label",
                "use_desc",
                "buyer",
                "owner_occupant",
                "is_structural_demo",
                "is_sewer_disconnect",
                "notification_number",
                "work_type",
                "filing_date",
                "start_date",
                "end_date",
                "abatement_contractor",
                "facility_owner",
                "print_action_url",
                "pdf_url",
            )
        },
    }


def _diff_permit_alerts(
    before_permits: dict,
    after_permits: dict,
    now_iso: str,
    *,
    seed_mode: bool = False,
    retention_floor_iso: str = "",
) -> list[dict]:
    events: list[dict] = []
    local_today = to_chicago_date_iso(now_iso)

    before_coh_ids: set[str] = set()
    before_dshs_ids: set[str] = set()
    for b_al in before_permits.get("alerts") or []:
        if not isinstance(b_al, dict):
            continue
        b_hcad = b_al.get("hcad_num") or ""
        for cp in b_al.get("coh_permits") or []:
            if isinstance(cp, dict):
                before_coh_ids.add(
                    build_permit_coh_event_id(
                        b_hcad,
                        cp.get("project_no"),
                        str(cp.get("use_desc") or ""),
                        str(cp.get("date") or ""),
                    )
                )
        for dn in b_al.get("dshs_notifications") or []:
            if isinstance(dn, dict):
                before_dshs_ids.add(
                    build_permit_dshs_event_id(
                        b_hcad,
                        dn.get("notification_number"),
                        str(dn.get("norm_address") or b_al.get("address") or ""),
                        str(dn.get("filing_date") or dn.get("start_date") or ""),
                    )
                )

    seen_after: set[str] = set()
    for al in after_permits.get("alerts") or []:
        if not isinstance(al, dict):
            continue
        hcad_raw = al.get("hcad_num") or ""
        title_name = sanitize_text(al.get("title") or al.get("building_name") or al.get("address") or "Historic Structure")
        addr = sanitize_text(al.get("address") or "")
        desig_parts = [
            p
            for p in (
                f"City Landmark: {al.get('landmark_name')}" if al.get("landmark_name") else "",
                f"Historic District: {al.get('historic_district')}" if al.get("historic_district") else "",
                str(al.get("good_brick_summary") or "").strip(),
            )
            if p
        ]
        desig_str = " | ".join(desig_parts)
        lng_val = al.get("lng") if al.get("lng") is not None else al.get("lon")

        for cp in al.get("coh_permits") or []:
            if not isinstance(cp, dict):
                continue
            p_date = parse_iso_date_prefix(cp.get("date")) or parse_iso_date_prefix(al.get("latest_filing_date")) or local_today
            ev_id = build_permit_coh_event_id(hcad_raw, cp.get("project_no"), str(cp.get("use_desc") or ""), p_date)
            if ev_id in before_coh_ids or ev_id in seen_after:
                continue
            if seed_mode and retention_floor_iso and p_date < retention_floor_iso:
                continue
            seen_after.add(ev_id)
            is_crit = bool(
                cp.get("is_structural_demo")
                or cp.get("is_sewer_disconnect")
                or str(al.get("severity") or "").lower() == "critical"
            )
            ts_val = _seed_ts_clamped(p_date, local_today, now_iso) if seed_mode else now_iso
            pno = str(cp.get("project_no") or "N/A").strip()
            lbl = sanitize_text(cp.get("permit_type_label") or cp.get("permit_desc") or "City Permit")
            use_d = sanitize_text(cp.get("use_desc") or "")
            buyer = sanitize_text(cp.get("buyer") or "")
            events.append(
                _make_event(
                    event_id=ev_id,
                    stream="permits",
                    kind="permit_coh_alert",
                    severity="critical" if is_crit else "warning",
                    adverse=True,
                    date_iso=p_date,
                    timestamp_iso=ts_val,
                    discovered_at=ts_val,
                    title=f"{lbl} (#{pno}): {title_name}",
                    summary=(
                        f"{addr}{f' (Built {al.get('year_built')})' if al.get('year_built') else ''}: "
                        f"{use_d or lbl} issued {p_date}"
                        f"{f' to {buyer}' if buyer else ''}"
                        f"{f' | {desig_str}' if desig_str else ''}."
                    ),
                    address=addr,
                    hcad=str(hcad_raw),
                    watch_property_id=str(al.get("watchlist_id") or ""),
                    building_id=str(al.get("building_id") or ""),
                    lat=al.get("lat"),
                    lng=lng_val,
                    year_built=al.get("year_built"),
                    designation=desig_str,
                    links=[
                        {
                            "label": "Houston Sold Permits",
                            "url": "https://cohtora.houstontx.gov/approot/soldpermits/online_permit.htm",
                        }
                    ],
                    permit_meta=_build_permit_alert_meta(al, "coh", cp),
                )
            )

        for dn in al.get("dshs_notifications") or []:
            if not isinstance(dn, dict):
                continue
            d_date = (
                parse_iso_date_prefix(dn.get("filing_date"))
                or parse_iso_date_prefix(dn.get("start_date"))
                or parse_iso_date_prefix(al.get("latest_filing_date"))
                or local_today
            )
            ev_id = build_permit_dshs_event_id(
                hcad_raw, dn.get("notification_number"), str(dn.get("norm_address") or addr), d_date
            )
            if ev_id in before_dshs_ids or ev_id in seen_after:
                continue
            if seed_mode and retention_floor_iso and d_date < retention_floor_iso:
                continue
            seen_after.add(ev_id)
            ts_val = _seed_ts_clamped(d_date, local_today, now_iso) if seed_mode else now_iso
            nno = str(dn.get("notification_number") or "N/A").strip()
            wtype = sanitize_text(dn.get("work_type") or "Asbestos/Demolition")
            window = (
                f"scheduled {dn.get('start_date')} to {dn.get('end_date')}"
                if dn.get("start_date") and dn.get("end_date")
                else ""
            )
            d_links: list[dict[str, str]] = []
            if dn.get("print_action_url"):
                d_links.append({"label": f"DSHS #{nno}", "url": str(dn["print_action_url"])})
            if dn.get("pdf_url"):
                d_links.append({"label": "DSHS Record", "url": str(dn["pdf_url"])})
            events.append(
                _make_event(
                    event_id=ev_id,
                    stream="permits",
                    kind="permit_dshs_alert",
                    severity="critical",
                    adverse=True,
                    date_iso=d_date,
                    timestamp_iso=ts_val,
                    discovered_at=ts_val,
                    title=f"TX DSHS {wtype} Filing (#{nno}): {title_name}",
                    summary=(
                        f"{addr}{f' (Built {al.get('year_built')})' if al.get('year_built') else ''}: "
                        f"DSHS Notification #{nno} filed {d_date}"
                        f"{f' ({window})' if window else ''}"
                        f"{f' by {dn.get('abatement_contractor')}' if dn.get('abatement_contractor') else ''}"
                        f"{f' | {desig_str}' if desig_str else ''}."
                    ),
                    address=addr,
                    hcad=str(hcad_raw),
                    watch_property_id=str(al.get("watchlist_id") or ""),
                    building_id=str(al.get("building_id") or ""),
                    lat=al.get("lat"),
                    lng=lng_val,
                    year_built=al.get("year_built"),
                    designation=desig_str,
                    links=d_links,
                    permit_meta=_build_permit_alert_meta(al, "dshs", dn),
                )
            )

    return events


def _format_hahc_outcome_label(outcome_raw: str) -> str:
    slug = slug_token(outcome_raw or "pending_agenda")
    mapping = {
        "pending-agenda": "Pending HAHC Agenda",
        "approved": "Approved",
        "approved-conditions": "Approved with Conditions",
        "denied-or-cor": "Denied / Certificate of Remediation",
        "denied": "Denied",
        "deferred": "Deferred",
        "withdrawn": "Withdrawn",
    }
    return mapping.get(slug, sanitize_text(outcome_raw or "Pending HAHC Agenda"))


def _diff_hahc_tracker(
    before_hahc: dict,
    after_hahc: dict,
    now_iso: str,
    *,
    retention_floor_iso: str,
    seed_mode: bool = False,
) -> list[dict]:
    events: list[dict] = []
    local_today = to_chicago_date_iso(now_iso)

    b_cc = before_hahc.get("current_cycle") if isinstance(before_hahc.get("current_cycle"), dict) else {}
    a_cc = after_hahc.get("current_cycle") if isinstance(after_hahc.get("current_cycle"), dict) else {}

    a_mdate = parse_iso_date_prefix(a_cc.get("meeting_date"))
    b_mdate = parse_iso_date_prefix(b_cc.get("meeting_date"))
    if a_mdate and a_mdate != b_mdate and (not retention_floor_iso or a_mdate >= retention_floor_iso):
        cyc_ts = _seed_ts_clamped(a_mdate, local_today, now_iso) if seed_mode else now_iso
        c_links: list[dict[str, str]] = []
        if a_cc.get("agenda_pdf_url"):
            c_links.append({"label": "HAHC Agenda PDF", "url": str(a_cc["agenda_pdf_url"])})
        if after_hahc.get("source_url"):
            c_links.append({"label": "COH HAHC Portal", "url": str(after_hahc["source_url"])})
        events.append(
            _make_event(
                event_id=build_hahc_cycle_event_id(a_mdate),
                stream="permits",
                kind="hahc_cycle_posted",
                severity="info",
                adverse=False,
                date_iso=a_mdate,
                timestamp_iso=cyc_ts,
                discovered_at=cyc_ts,
                title=f"HAHC Monthly Agenda Cycle Posted: {a_mdate}",
                summary=f"{a_cc.get('active_items_count') or 0} Certificate of Appropriateness (COA) item(s) scheduled on the {a_mdate} Houston Archaeological & Historical Commission docket.",
                links=c_links,
            )
        )

    b_guides = {
        build_hahc_guideline_event_id(
            parse_iso_date_prefix(b_cc.get("meeting_date")) or "cycle",
            str(g.get("title") or g.get("pdf_url") or ""),
        )
        for g in (b_cc.get("guideline_updates") or [])
        if isinstance(g, dict) and (g.get("title") or g.get("pdf_url"))
    }
    for g in a_cc.get("guideline_updates") or []:
        if not isinstance(g, dict):
            continue
        g_title = sanitize_text(g.get("title") or "")
        g_url = str(g.get("pdf_url") or "").strip()
        if not g_title and not g_url:
            continue
        mdate_key = a_mdate or local_today
        if retention_floor_iso and mdate_key < retention_floor_iso:
            continue
        gid = build_hahc_guideline_event_id(mdate_key, g_title or g_url)
        if gid in b_guides:
            continue
        g_ts = _seed_ts_clamped(mdate_key, local_today, now_iso) if seed_mode else now_iso
        events.append(
            _make_event(
                event_id=gid,
                stream="permits",
                kind="hahc_guideline_update",
                severity="warning",
                adverse=False,
                date_iso=mdate_key,
                timestamp_iso=g_ts,
                discovered_at=g_ts,
                title=f"HAHC Historic District Design Guideline Draft: {g_title or 'Redline Draft'}",
                summary=f"Design guideline redline/update posted with the {mdate_key} HAHC packet: {g_title or g_url}.",
                links=[{"label": g_title or "Guideline Draft PDF", "url": g_url}] if g_url else [],
            )
        )

    # Top-level filings[] keyed by (filing_id, outcome) to detect both new filings and post-hearing rulings
    before_by_id: dict[str, dict] = {}
    before_state_ids: set[str] = set()
    for bf in before_hahc.get("filings") or []:
        if not isinstance(bf, dict):
            continue
        fid = str(bf.get("filing_id") or "").strip()
        if not fid:
            continue
        before_by_id[fid] = bf
        before_state_ids.add(build_hahc_filing_event_id(fid, bf.get("outcome")))

    seen_hahc: set[str] = set()
    for af in after_hahc.get("filings") or []:
        if not isinstance(af, dict):
            continue
        fid = str(af.get("filing_id") or "").strip()
        f_date = parse_iso_date_prefix(af.get("meeting_date"))
        if not fid or not f_date:
            continue
        if retention_floor_iso and f_date < retention_floor_iso:
            continue
        outcome_raw = str(af.get("outcome") or "pending_agenda").strip()
        ev_id = build_hahc_filing_event_id(fid, outcome_raw)
        if ev_id in before_state_ids or ev_id in seen_hahc:
            continue
        seen_hahc.add(ev_id)

        prev_f = before_by_id.get(fid)
        is_ruling_transition = prev_f is not None and slug_token(prev_f.get("outcome")) != slug_token(outcome_raw)
        app_cat = str(af.get("app_category") or "").strip().lower()
        app_type = sanitize_text(af.get("app_type") or "COA Application")
        addr = sanitize_text(af.get("address") or "")
        hd = sanitize_text(af.get("historic_district") or "")
        lm = sanitize_text(af.get("landmark_name") or "")
        desig = " | ".join(p for p in (f"Historic District: {hd}" if hd else "", f"Landmark: {lm}" if lm else "") if p)
        outcome_lbl = _format_hahc_outcome_label(outcome_raw)
        out_slug = slug_token(outcome_raw)
        is_demo_reloc = app_cat == "demolition_relocation" or bool(re.search(r"demolition|relocation", app_type, re.I))
        adverse = bool(is_demo_reloc and out_slug in ("pending-agenda", "approved", "approved-conditions"))
        if adverse:
            sev = "critical"
        elif is_demo_reloc or "denied" in out_slug or "defer" in out_slug or app_cat == "designation":
            sev = "warning"
        else:
            sev = "info"

        f_links: list[dict[str, str]] = []
        if af.get("agenda_or_minutes_url"):
            f_links.append({"label": "HAHC Agenda / Action Minutes PDF", "url": str(af["agenda_or_minutes_url"])})
        if af.get("staff_report_url"):
            f_links.append({"label": "HAHC Staff Report PDF", "url": str(af["staff_report_url"])})

        ts_val = _seed_ts_clamped(f_date, local_today, now_iso) if seed_mode else now_iso
        prev_lbl = _format_hahc_outcome_label(str(prev_f.get("outcome") or "")) if is_ruling_transition else ""
        title_str = (
            f"HAHC Ruling ({prev_lbl} -> {outcome_lbl}): {addr or fid} ({app_type})"
            if is_ruling_transition
            else f"HAHC COA Docket ({outcome_lbl}): {addr or fid} ({app_type})"
        )
        detail = sanitize_text(af.get("outcome_detail") or "")
        events.append(
            _make_event(
                event_id=ev_id,
                stream="permits",
                kind="hahc_filing_outcome" if is_ruling_transition else "hahc_filing_added",
                severity=sev,
                adverse=adverse,
                date_iso=f_date,
                timestamp_iso=ts_val,
                discovered_at=ts_val,
                title=title_str,
                summary=(
                    f"HAHC Meeting {f_date}{f' Item #{af.get('item_num')}' if af.get('item_num') else ''}: "
                    f"{app_type} at {addr or 'Houston'}"
                    f"{f' ({desig})' if desig else ''} - {outcome_lbl}"
                    f"{f' ({detail[:180]})' if detail and detail.lower() != outcome_lbl.lower() else ''}."
                ),
                address=addr,
                hcad=str(af.get("hcad_num") or ""),
                lat=af.get("lat"),
                lng=af.get("lng"),
                year_built=af.get("year_built"),
                designation=desig,
                links=f_links,
            )
        )

    return events


# ---------------------------------------------------------------------------
# Public Diff, Ledger Merge, Retention Pruning & Daily Run Rollup
# ---------------------------------------------------------------------------

def compute_snapshot_diff_events(
    before_snapshots: dict[str, dict],
    after_snapshots: dict[str, dict],
    now_iso: str,
    *,
    retention_days: int = RETENTION_DAYS,
    seed_mode: bool = False,
) -> list[dict]:
    """Compute deduplicated canonical events between two 4-file snapshot bundles."""
    today_str = to_chicago_date_iso(now_iso) or dt.datetime.now(CHICAGO_TZ).date().isoformat()
    today_d = dt.date.fromisoformat(today_str)
    floor_iso = (today_d - dt.timedelta(days=max(1, retention_days) - 1)).isoformat()

    raw_events: list[dict] = []
    raw_events.extend(
        _diff_property_watchlist(
            before_snapshots.get("property_watchlist.json") or {},
            after_snapshots.get("property_watchlist.json") or {},
            now_iso,
            seed_mode=seed_mode,
            retention_floor_iso=floor_iso,
        )
    )
    raw_events.extend(
        _diff_plat_agenda(
            before_snapshots.get("plat_agenda_monitor.json") or {},
            after_snapshots.get("plat_agenda_monitor.json") or {},
            now_iso,
            seed_mode=seed_mode,
            retention_floor_iso=floor_iso,
        )
    )
    raw_events.extend(
        _diff_permit_alerts(
            before_snapshots.get("active_permit_alerts.json") or {},
            after_snapshots.get("active_permit_alerts.json") or {},
            now_iso,
            seed_mode=seed_mode,
            retention_floor_iso=floor_iso,
        )
    )
    raw_events.extend(
        _diff_hahc_tracker(
            before_snapshots.get("hahc_coa_tracker.json") or {},
            after_snapshots.get("hahc_coa_tracker.json") or {},
            now_iso,
            retention_floor_iso=floor_iso,
            seed_mode=seed_mode,
        )
    )

    by_id: dict[str, dict] = {}
    for ev in raw_events:
        if ev["localDate"] >= floor_iso:
            by_id[ev["id"]] = ev
    return list(by_id.values())


def _sort_events_newest_first(events: list[dict]) -> list[dict]:
    sev_rank = {"critical": 0, "warning": 1, "info": 2}
    return sorted(
        events,
        key=lambda e: (
            e.get("localDate") or "",
            e.get("timestamp") or "",
            -sev_rank.get(str(e.get("severity") or "info"), 2),
            e.get("id") or "",
        ),
        reverse=True,
    )


def merge_and_prune_ledger(
    existing_log: dict | None,
    delta_events: list[dict],
    after_watchlist: dict | None,
    now_iso: str,
    *,
    retention_days: int = RETENTION_DAYS,
    seed_mode: bool = False,
) -> dict:
    """Merge delta events into an existing ledger idempotently and prune to `retention_days`."""
    today_str = to_chicago_date_iso(now_iso) or dt.datetime.now(CHICAGO_TZ).date().isoformat()
    today_d = dt.date.fromisoformat(today_str)
    floor_d = today_d - dt.timedelta(days=max(1, retention_days) - 1)
    floor_iso = floor_d.isoformat()

    events_by_id: dict[str, dict] = {}
    for prev_ev in ((existing_log or {}).get("events") or []):
        if not isinstance(prev_ev, dict) or not prev_ev.get("id"):
            continue
        ld = str(prev_ev.get("localDate") or to_chicago_date_iso(prev_ev.get("discoveredAt") or prev_ev.get("timestamp") or "", prev_ev.get("date") or ""))
        if not seed_mode and ld == today_str and prev_ev.get("kind") == "watchlist_sweep_degraded":
            continue
        if ld >= floor_iso:
            cp = dict(prev_ev)
            cp["localDate"] = ld
            events_by_id[cp["id"]] = cp

    for new_ev in delta_events:
        if not isinstance(new_ev, dict) or not new_ev.get("id"):
            continue
        ld = str(new_ev.get("localDate") or to_chicago_date_iso(new_ev.get("discoveredAt") or new_ev.get("timestamp") or "", new_ev.get("date") or ""))
        if ld < floor_iso:
            continue
        cp = dict(new_ev)
        cp["localDate"] = ld
        if cp["id"] not in events_by_id:
            events_by_id[cp["id"]] = cp

    retained_events = _sort_events_newest_first(list(events_by_id.values()))

    runs_by_date: dict[str, dict] = {}
    for prev_run in ((existing_log or {}).get("dailyRuns") or []):
        if not isinstance(prev_run, dict):
            continue
        rd = parse_iso_date_prefix(prev_run.get("date"))
        if rd and floor_iso <= rd <= today_str:
            runs_by_date[rd] = dict(prev_run)

    # Extract latest sweep health from after_watchlist for today's run record
    wl_doc = after_watchlist if isinstance(after_watchlist, dict) else {}
    sweep_health = wl_doc.get("lastSweepHealth") if isinstance(wl_doc.get("lastSweepHealth"), dict) else {}
    failed_today = sorted(
        str(x).strip().lower() for x in (sweep_health.get("failedAgencies") or []) if str(x).strip()
    )
    total_ag = int(sweep_health.get("totalAgencies") or len(AUTOMATED_SWEEP_AGENCY_LABELS))
    succ_ag = int(
        sweep_health["succeededAgencies"]
        if "succeededAgencies" in sweep_health and sweep_health.get("succeededAgencies") is not None
        else max(0, total_ag - len(failed_today))
    )
    props_swept = int(
        sweep_health.get("propertiesSwept")
        if sweep_health.get("propertiesSwept") is not None
        else len([p for p in (wl_doc.get("watchedProperties") or []) if isinstance(p, dict) and p.get("trackerStatus") != "Archived (Tier 3)"])
    )

    prev_today = runs_by_date.get(today_str)
    runs_by_date[today_str] = {
        "date": today_str,
        "lastRunAt": now_iso,
        "runCount": int((prev_today or {}).get("runCount") or 0) + 1,
        "sweepsExecuted": bool(sweep_health.get("sweepsExecuted", True)),
        "propertiesSwept": props_swept,
        "totalAgencies": total_ag,
        "succeededAgencies": succ_ag,
        "failedAgencies": failed_today,
        "allOk": len(failed_today) == 0,
        "eventCount": 0,
        "adverseEventCount": 0,
        "byStream": {"watchlist": 0, "agenda": 0, "staff": 0, "permits": 0},
    }

    # In seed mode, ensure continuous calendar dates from earliest retained event through today_str
    # so the Left Pane Daily Run Ledger renders clean quiet-day ranges alongside event dates.
    if seed_mode and retained_events:
        earliest_ev_iso = min(ev["localDate"] for ev in retained_events)
        cursor_d = max(floor_d, dt.date.fromisoformat(earliest_ev_iso))
        while cursor_d < today_d:
            d_iso = cursor_d.isoformat()
            if d_iso not in runs_by_date:
                runs_by_date[d_iso] = {
                    "date": d_iso,
                    "lastRunAt": f"{d_iso}T12:00:00Z",
                    "runCount": 1,
                    "sweepsExecuted": True,
                    "propertiesSwept": props_swept,
                    "totalAgencies": total_ag,
                    "succeededAgencies": total_ag,
                    "failedAgencies": [],
                    "allOk": True,
                    "seededHistory": True,
                    "eventCount": 0,
                    "adverseEventCount": 0,
                    "byStream": {"watchlist": 0, "agenda": 0, "staff": 0, "permits": 0},
                }
            cursor_d += dt.timedelta(days=1)

    # Also ensure every date carrying at least one retained event appears in dailyRuns
    for ev in retained_events:
        ld = ev["localDate"]
        if ld not in runs_by_date:
            runs_by_date[ld] = {
                "date": ld,
                "lastRunAt": ev.get("discoveredAt") or ev.get("timestamp") or f"{ld}T12:00:00Z",
                "runCount": 1,
                "sweepsExecuted": True,
                "propertiesSwept": props_swept,
                "totalAgencies": total_ag,
                "succeededAgencies": total_ag,
                "failedAgencies": [],
                "allOk": True,
                "eventCount": 0,
                "adverseEventCount": 0,
                "byStream": {"watchlist": 0, "agenda": 0, "staff": 0, "permits": 0},
            }

    # Idempotently recompute per-day counts directly from retained deduplicated events
    events_by_day: dict[str, list[dict]] = {}
    for ev in retained_events:
        events_by_day.setdefault(ev["localDate"], []).append(ev)

    for d_iso, run_rec in runs_by_date.items():
        day_evs = [
            e for e in events_by_day.get(d_iso, []) if e.get("kind") != "watchlist_sweep_degraded"
        ]
        run_rec["eventCount"] = len(day_evs)
        run_rec["adverseEventCount"] = sum(
            1 for e in day_evs if e.get("adverse") or e.get("severity") == "critical"
        )
        run_rec["byStream"] = {
            s_key: sum(1 for e in day_evs if e.get("stream") == s_key)
            for s_key in ("watchlist", "agenda", "staff", "permits")
        }

    ordered_runs = sorted(runs_by_date.values(), key=lambda r: r["date"], reverse=True)
    substantive_events = [e for e in retained_events if e.get("kind") != "watchlist_sweep_degraded"]
    return {
        "version": SCHEMA_VERSION,
        "generatedAt": now_iso,
        "lastSweepAt": str(wl_doc.get("lastAutoSweepAt") or now_iso),
        "timezone": "America/Chicago",
        "retentionDays": retention_days,
        "retentionFloorDate": floor_iso,
        "lastSweepHealth": sweep_health
        or {
            "timestamp": now_iso,
            "sweepsExecuted": True,
            "propertiesSwept": props_swept,
            "totalAgencies": total_ag,
            "succeededAgencies": succ_ag,
            "failedAgencies": failed_today,
            "allOk": len(failed_today) == 0,
        },
        "summary": {
            "totalEvents": len(substantive_events),
            "criticalCount": sum(1 for e in substantive_events if e.get("severity") == "critical"),
            "warningCount": sum(1 for e in substantive_events if e.get("severity") == "warning"),
            "infoCount": sum(1 for e in substantive_events if e.get("severity") == "info"),
            "byStream": {
                s_key: sum(1 for e in substantive_events if e.get("stream") == s_key)
                for s_key in ("watchlist", "agenda", "staff", "permits")
            },
            "operationalNoticeCount": len(retained_events) - len(substantive_events),
            "daysTracked": len(ordered_runs),
        },
        "dailyRuns": ordered_runs,
        "events": retained_events,
    }


# ---------------------------------------------------------------------------
# CLI Entry Points (--save-before, --update-log, --seed-initial)
# ---------------------------------------------------------------------------

def save_before_snapshots(data_dir: Path, before_dir: Path) -> None:
    before_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for fname in SNAPSHOT_FILENAMES:
        src = data_dir / fname
        if src.exists():
            shutil.copy2(src, before_dir / fname)
            copied += 1
    print(f"[activity-log] Saved {copied}/{len(SNAPSHOT_FILENAMES)} baseline snapshots to {before_dir}")


def load_snapshot_bundle(directory: Path | None) -> dict[str, dict]:
    if directory is None or not directory.exists():
        return {fname: {} for fname in SNAPSHOT_FILENAMES}
    return {fname: _read_json_optional(directory / fname) for fname in SNAPSHOT_FILENAMES}


def run_update_or_seed(
    *,
    data_dir: Path,
    before_dir: Path | None,
    log_path: Path,
    now_iso: str,
    retention_days: int = RETENTION_DAYS,
    seed_initial: bool = False,
) -> dict:
    after_bundle = load_snapshot_bundle(data_dir)
    before_bundle = {fname: {} for fname in SNAPSHOT_FILENAMES} if seed_initial else load_snapshot_bundle(before_dir)
    existing_log = None if seed_initial else _read_json_optional(log_path)

    delta_events = compute_snapshot_diff_events(
        before_bundle,
        after_bundle,
        now_iso,
        retention_days=retention_days,
        seed_mode=seed_initial,
    )
    merged = merge_and_prune_ledger(
        existing_log,
        delta_events,
        after_bundle.get("property_watchlist.json"),
        now_iso,
        retention_days=retention_days,
        seed_mode=seed_initial,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload_txt = json.dumps(merged, indent=2)
    assert "\u2014" not in payload_txt, "Generated monitor_activity_log.json must never contain U+2014"
    log_path.write_text(payload_txt + "\n", encoding="utf-8")
    return merged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or update monitor_activity_log.json")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--save-before", type=Path, help="Directory to store pre-run JSON snapshots")
    mode.add_argument("--update-log", action="store_true", help="Diff --before-dir vs --data-dir and update log")
    mode.add_argument("--seed-initial", action="store_true", help="Seed initial 90-day ledger from --data-dir")

    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--before-dir", type=Path, default=None)
    parser.add_argument("--log-path", type=Path, default=None)
    parser.add_argument("--now-iso", type=str, default="")
    parser.add_argument("--retention-days", type=int, default=RETENTION_DAYS)

    args = parser.parse_args(argv)
    data_dir = args.data_dir.resolve()
    log_path = (args.log_path or (data_dir / DEFAULT_LOG_FILENAME)).resolve()
    now_iso = args.now_iso or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.save_before:
        save_before_snapshots(data_dir, args.save_before.resolve())
        return 0

    merged = run_update_or_seed(
        data_dir=data_dir,
        before_dir=args.before_dir.resolve() if args.before_dir else None,
        log_path=log_path,
        now_iso=now_iso,
        retention_days=args.retention_days,
        seed_initial=bool(args.seed_initial),
    )
    today_run = merged["dailyRuns"][0] if merged["dailyRuns"] else {}
    print(
        f"[activity-log] Wrote {log_path} "
        f"({merged['summary']['totalEvents']} retained events across {merged['summary']['daysTracked']} days; "
        f"today={today_run.get('eventCount', 0)} event(s), "
        f"agencies={today_run.get('succeededAgencies', 8)}/{today_run.get('totalAgencies', 8)} OK)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
