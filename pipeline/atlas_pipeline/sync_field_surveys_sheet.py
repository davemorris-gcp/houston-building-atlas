"""Generate and validate Google Sheets sync batches for the 12th tab (`Field_Surveys`, gid=913405001)
and Milestone 3 Preservation Staff & Field Workbench upgrades.

Architecture & Lossless Dual-Persistence Contract (DF-4 / DF-5):
1. Creates and formats `Field_Surveys` (`sheetId = 913405001`) with 20 columns (`A:T`):
   - Raw observation columns `A:R` (`survey_id` .. `gps_lat_lng_acc`), written as `RAW`
     with Plain Text (`@`) number formats on `survey_id`, `hcad_num`, and
     `observed_year_or_demo_note` so leading-zero 13-digit HCAD accounts never strip.
   - Single-cell `ARRAYFORMULA` helper columns `S1:T1` (`open_in_atlas`, `inspect_hcad`).
2. Upgrades `Pending_Submissions` (`W1 = geometry_geojson`, `S1` triage formula classifying
   `FIELD_SURVEY_OBSERVATION` as `📋 Field Survey Observation`) and `Approved_Edits`
   (`P1 = geometry_geojson`, 16-column `A2` `FILTER` formula) without clobbering existing data.
3. Adds `Field_Surveys` live telemetry + `#survey=1` quick link at Row 19 of
   `Admin_Hub_&_Quick_Filter` (preserving Row 18 `Lost_Houston_Catalog` and Row 17
   `Historical_Audit_Queue`) and re-anchors Quick-Filter dropdown/result formulas cleanly.
4. Updates `Instructions_&_Apps_Script!A5:C5` (`APPS_SCRIPT_CODE` in `B5`) and writes
   `SOP 15` to `Instructions_&_Apps_Script!A26:C26`, STRICTLY preserving Cell `B6`
   (Private Staff Admin Passkey) and Rows `1..4` and `7..25` (`SOP 1..14`).
5. Supports `--ingest-pending-csv <Pending_Submissions.csv>` to recover any Walk-the-Block
   `[FIELD_SURVEY_JSON:{...}]` envelopes saved to `Pending_Submissions` prior to Apps Script
   redeployment, deduplicating deterministically by `survey_id`.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from atlas_pipeline.schema import (
    FIELD_SURVEY_CONDITION_RATINGS,
    FIELD_SURVEY_CONTRIBUTING_VALUES,
    FIELD_SURVEY_INTEGRITY_GRADES,
    FIELD_SURVEY_SOURCE_TYPE,
    extract_embedded_field_survey_json,
    sanitize_atlas_text,
    strip_embedded_field_survey_tag,
)
from atlas_pipeline.setup_admin_sheet import (
    APPROVED_EDITS_FILTER_FORMULA,
    APPROVED_EDITS_HEADERS,
    APPROVED_TOTAL_COLS,
    APPS_SCRIPT_CODE,
    ATLAS_BASE_URL,
    HUB_FILTER_INPUT_ROW,
    HUB_RESULTS_FORMULA_ROW,
    HUB_TRIAGE_DROPDOWN_VALUES,
    PENDING_TOTAL_COLS,
    PENDING_TRIAGE_FORMULA,
    SID_APPROVED,
    SID_HUB,
    SID_INSTR,
    SID_LOST,
    SID_PENDING,
    SID_SURVEYS,
    build_hub_filter_count_formula,
    build_hub_results_formula,
    rgb,
)

SHEET_TITLE_SURVEYS = "Field_Surveys"
HUB_SURVEYS_ROW = 19
SOP15_TARGET_ROW = 26

MIN_SURVEY_GRID_ROWS = 2000
SURVEY_ROW_HEADROOM = 250
SURVEY_FILTER_VIEW_COUNT = 6

HEADERS_A_TO_R = [
    "survey_id",
    "surveyed_at",
    "review_status",
    "hcad_num",
    "address",
    "district_or_corridor",
    "integrity_grade",
    "condition_rating",
    "suggested_contributing",
    "windows_status",
    "cladding_material",
    "porch_integrity",
    "atlas_year_built",
    "observed_year_or_demo_note",
    "photo_url",
    "field_notes",
    "surveyor_name",
    "gps_lat_lng_acc",
]
HEADERS_S_TO_T = ["open_in_atlas", "inspect_hcad"]
ALL_SURVEY_HEADERS = HEADERS_A_TO_R + HEADERS_S_TO_T
COL_IDX: dict[str, int] = {name: idx for idx, name in enumerate(ALL_SURVEY_HEADERS)}

SURVEY_REVIEW_STATUSES = [
    "Unreviewed",
    "Verified",
    "Promoted to Override",
    "Needs Follow-Up",
    "Duplicate",
    "Archived",
]

SURVEY_CONTRIBUTING_VALUES = list(FIELD_SURVEY_CONTRIBUTING_VALUES)


_RE_SAFE_NEGATIVE_COORD = __import__("re").compile(r"^-\d+(?:\.\d+)?(?:\s*,|$)")


def sanitize_sheet_cell(value: Any) -> str:
    """Clean strings for Google Sheets / CSV cells with zero typographic dashes and DF-11 formula protection."""
    if value is None:
        return ""
    raw_str = sanitize_atlas_text(str(value).replace("\u2014", " - ").replace("\u2013", "-"))
    if not raw_str:
        return ""
    had_ctrl_trigger = raw_str[0] in ("\t", "\r")
    cleaned = " ".join(raw_str.split())
    if not cleaned:
        return ""
    first = cleaned[0]
    if had_ctrl_trigger or first in ("=", "+", "@") or (
        first == "-" and not _RE_SAFE_NEGATIVE_COORD.match(cleaned)
    ):
        return f"'{cleaned}"
    return cleaned


def verify_live_sheet_anchors(
    *,
    tab_id_to_title: dict[int, str],
    hub_col_a: list[str],
    instr_col_a: list[str],
) -> dict[str, bool]:
    """Validate live workbook anchors before producing structural vs idempotent batches (DF-5).

    Returns a dict describing whether one-time DDL ops (`need_add_sheet`, `need_hub_insert`)
    are still needed on the workbook. Raises `ValueError` if unexpected row drift could
    overwrite unrelated telemetry rows, `SOP 14`, or the private Passkey row (`Row 6`).
    """
    if tab_id_to_title.get(SID_LOST) != "Lost_Houston_Catalog":
        raise ValueError(
            f"Expected Lost_Houston_Catalog at gid={SID_LOST}, got {tab_id_to_title.get(SID_LOST)!r}"
        )
    existing_survey_title = tab_id_to_title.get(SID_SURVEYS)
    if existing_survey_title is not None and existing_survey_title != SHEET_TITLE_SURVEYS:
        raise ValueError(
            f"Sheet ID {SID_SURVEYS} collided with unexpected tab {existing_survey_title!r}"
        )
    need_add_sheet = existing_survey_title is None

    row18_a = hub_col_a[17].strip() if len(hub_col_a) >= 18 else ""
    if "Lost Houston" not in row18_a:
        raise ValueError(
            f"Admin_Hub_&_Quick_Filter Row 18 anchor mismatch: expected Lost Houston row, got {row18_a!r}"
        )
    row19_a = hub_col_a[18].strip() if len(hub_col_a) >= 19 else ""
    if "Walk-the-Block Field Surveys" in row19_a:
        need_hub_insert = False
    elif row19_a.startswith("HOW TO USE:"):
        need_hub_insert = True
    else:
        raise ValueError(
            "Admin_Hub_&_Quick_Filter Row 19 anchor mismatch: expected 'HOW TO USE:' "
            f"(pre-upgrade) or 'Walk-the-Block Field Surveys' (upgraded), got {row19_a!r}"
        )

    row5_a = instr_col_a[4].strip() if len(instr_col_a) >= 5 else ""
    row6_a = instr_col_a[5].strip() if len(instr_col_a) >= 6 else ""
    row25_a = instr_col_a[24].strip() if len(instr_col_a) >= 25 else ""
    if "Google Apps Script Code" not in row5_a:
        raise ValueError(
            f"Instructions_&_Apps_Script Row 5 anchor mismatch: expected Apps Script row, got {row5_a!r}"
        )
    if "Passkey" not in row6_a or "B6" not in row6_a:
        raise ValueError(
            f"Instructions_&_Apps_Script Row 6 anchor mismatch (refusing to touch sheet near B6): {row6_a!r}"
        )
    if "SOP 14" not in row25_a:
        raise ValueError(
            f"Instructions_&_Apps_Script Row 25 anchor mismatch: expected SOP 14, got {row25_a!r}"
        )
    row26_a = instr_col_a[25].strip() if len(instr_col_a) >= 26 else ""
    if row26_a and "SOP 15" not in row26_a:
        raise ValueError(
            f"Instructions_&_Apps_Script Row 26 is already occupied by non-SOP-15 content: {row26_a!r}"
        )

    return {
        "need_add_sheet": need_add_sheet,
        "need_hub_insert": need_hub_insert,
    }


def _normalize_survey_hcad(raw_val: Any) -> str:
    """Normalize a 10-to-13 digit HCAD string (or `<hcad>#<slug>`) without breaking GPS IDs."""
    text = sanitize_sheet_cell(raw_val).lstrip("'")
    if not text:
        return ""
    head, sep, tail = text.partition("#")
    digits = "".join(ch for ch in head if ch.isdigit())
    if digits and head.replace("-", "").replace(" ", "").isdigit() and 10 <= len(digits) <= 13:
        padded = digits.zfill(13)
        return f"{padded}#{tail}" if sep and tail else padded
    return text


def recover_field_survey_rows_from_pending_csv(csv_text: str) -> list[list[Any]]:
    """Recover Walk-the-Block survey rows from a `Pending_Submissions` CSV export (DF-4).

    Extracts all 18 structured `Field_Surveys` columns (`A:R`) from `[FIELD_SURVEY_JSON:{...}]`
    tags or `source_type == FIELD_SURVEY_OBSERVATION` rows, deduplicating deterministically
    by `survey_id` in left-to-right file order so retried offline flushes never duplicate rows.
    """
    if not csv_text or not csv_text.strip():
        return []
    clean_csv = csv_text.lstrip("\ufeff")
    reader = csv.DictReader(clean_csv.splitlines())
    by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []

    for row_num, raw_row in enumerate(reader, start=2):
        row = {
            str(k or "").strip().lower(): (v if v is not None else "")
            for k, v in raw_row.items()
            if k is not None
        }
        src_type = sanitize_sheet_cell(row.get("source_type")).upper()
        raw_cite = str(row.get("source_citation") or "")
        env = extract_embedded_field_survey_json(raw_cite)
        if src_type != FIELD_SURVEY_SOURCE_TYPE and env is None:
            continue

        env_dict = env or {}
        hcad_val = _normalize_survey_hcad(env_dict.get("hcad_num") or row.get("hcad_num"))
        surveyed_at = sanitize_sheet_cell(
            env_dict.get("surveyed_at") or row.get("submitted_at") or row.get("date_submitted")
        )
        survey_id = sanitize_sheet_cell(env_dict.get("survey_id"))
        if not survey_id:
            fallback_seed = f"{hcad_val or 'NOHCAD'}_{surveyed_at or row_num}"
            survey_id = f"SURV_RECOVERED_{fallback_seed.replace(' ', '_')}"

        raw_atlas_yr = (
            env_dict.get("atlas_year_built")
            if env_dict.get("atlas_year_built") not in (None, "")
            else (row.get("original_hcad_year") or row.get("hcad_year_built") or "")
        )
        atlas_yr_val: int | str = ""
        try:
            yr_int = int(round(float(str(raw_atlas_yr).strip())))
            if yr_int > 0:
                atlas_yr_val = yr_int
        except (ValueError, TypeError):
            atlas_yr_val = ""

        fallback_notes = sanitize_sheet_cell(strip_embedded_field_survey_tag(raw_cite))
        parsed: dict[str, Any] = {
            "survey_id": survey_id,
            "surveyed_at": surveyed_at,
            "review_status": sanitize_sheet_cell(env_dict.get("review_status")) or "Unreviewed",
            "hcad_num": hcad_val,
            "address": sanitize_sheet_cell(env_dict.get("address") or row.get("address")),
            "district_or_corridor": sanitize_sheet_cell(
                env_dict.get("district_or_corridor") or row.get("historic_district")
            ),
            "integrity_grade": sanitize_sheet_cell(env_dict.get("integrity_grade")),
            "condition_rating": sanitize_sheet_cell(env_dict.get("condition_rating")),
            "suggested_contributing": sanitize_sheet_cell(
                env_dict.get("suggested_contributing") or row.get("contributing")
            ),
            "windows_status": sanitize_sheet_cell(env_dict.get("windows_status")),
            "cladding_material": sanitize_sheet_cell(env_dict.get("cladding_material")),
            "porch_integrity": sanitize_sheet_cell(env_dict.get("porch_integrity")),
            "atlas_year_built": atlas_yr_val,
            "observed_year_or_demo_note": sanitize_sheet_cell(
                env_dict.get("observed_year_or_demo_note")
            ),
            "photo_url": sanitize_sheet_cell(env_dict.get("photo_url") or row.get("source_url")),
            "field_notes": sanitize_sheet_cell(env_dict.get("field_notes")) or fallback_notes,
            "surveyor_name": sanitize_sheet_cell(
                env_dict.get("surveyor_name") or row.get("submitted_by") or row.get("submitter_name")
            ),
            "gps_lat_lng_acc": sanitize_sheet_cell(env_dict.get("gps_lat_lng_acc")),
        }

        if survey_id not in by_id:
            by_id[survey_id] = parsed
            ordered_ids.append(survey_id)
        else:
            cur = by_id[survey_id]
            for k, v in parsed.items():
                if v not in ("", None):
                    cur[k] = v

    rows_out: list[list[Any]] = []
    for sid in ordered_ids:
        rec = by_id[sid]
        rows_out.append([rec[col] for col in HEADERS_A_TO_R])
    return rows_out


def load_existing_field_surveys_csv(csv_text: str) -> list[list[Any]]:
    """Parse an existing `Field_Surveys` CSV export (`A:R` or `A:T`) into sanitized 18-col rows."""
    if not csv_text or not csv_text.strip():
        return []
    clean_csv = csv_text.lstrip("\ufeff")
    reader = csv.DictReader(clean_csv.splitlines())
    rows_out: list[list[Any]] = []
    for raw_row in reader:
        row = {
            str(k or "").strip().lower(): (v if v is not None else "")
            for k, v in raw_row.items()
            if k is not None
        }
        sid = sanitize_sheet_cell(row.get("survey_id"))
        if not sid:
            continue
        raw_yr = str(row.get("atlas_year_built") or "").strip()
        atlas_yr_val: int | str = ""
        if raw_yr:
            try:
                yr_int = int(round(float(raw_yr)))
                if yr_int > 0:
                    atlas_yr_val = yr_int
            except (ValueError, TypeError):
                atlas_yr_val = ""
        rec_vals: list[Any] = []
        for col in HEADERS_A_TO_R:
            if col == "survey_id":
                rec_vals.append(sid)
            elif col == "hcad_num":
                rec_vals.append(_normalize_survey_hcad(row.get("hcad_num")))
            elif col == "review_status":
                rec_vals.append(sanitize_sheet_cell(row.get("review_status")) or "Unreviewed")
            elif col == "atlas_year_built":
                rec_vals.append(atlas_yr_val)
            else:
                rec_vals.append(sanitize_sheet_cell(row.get(col)))
        rows_out.append(rec_vals)
    return rows_out


def merge_field_survey_rows(
    existing_rows: list[list[Any]],
    incoming_rows: list[list[Any]],
) -> list[list[Any]]:
    """Merge incoming `Field_Surveys` rows onto existing live rows by `survey_id`.

    Preserves row ordering of `existing_rows`, appends brand-new `survey_id` rows at the
    tail, and protects any non-`'Unreviewed'` curator `review_status` in `existing_rows`
    when an offline backfill carries default `'Unreviewed'`.
    """
    by_id: dict[str, list[Any]] = {}
    ordered_ids: list[str] = []

    for row in existing_rows:
        if len(row) != len(HEADERS_A_TO_R):
            continue
        sid = str(row[0] or "").strip()
        if not sid:
            continue
        if sid not in by_id:
            by_id[sid] = list(row)
            ordered_ids.append(sid)
        else:
            cur = by_id[sid]
            for idx, val in enumerate(row):
                if val not in ("", None):
                    cur[idx] = val

    rev_idx = COL_IDX["review_status"]
    for row in incoming_rows:
        if len(row) != len(HEADERS_A_TO_R):
            continue
        sid = str(row[0] or "").strip()
        if not sid:
            continue
        if sid not in by_id:
            by_id[sid] = list(row)
            ordered_ids.append(sid)
        else:
            cur = by_id[sid]
            existing_status = str(cur[rev_idx] or "").strip()
            for idx, val in enumerate(row):
                if idx == rev_idx:
                    inc_status = str(val or "").strip()
                    if existing_status and existing_status != "Unreviewed" and inc_status in ("", "Unreviewed"):
                        continue
                    if inc_status:
                        cur[idx] = inc_status
                elif val not in ("", None):
                    cur[idx] = val

    return [by_id[sid] for sid in ordered_ids]


def build_sop15_row() -> list[str]:
    """Build the 3-column SOP 15 Administrator Manual row for `Instructions_&_Apps_Script!A26:C26`."""
    col_a = (
        "24. SOP 15 - Staff Historian Rapid Audit, Polygon Editor & Walk-the-Block Field Surveys "
        "(Field_Surveys Tab & #audit / #survey / #polyedit)"
    )
    col_b = (
        f"Milestone 3 provides three integrated preservation workbench tools inside the live Atlas "
        f"({ATLAS_BASE_URL}) with zero bundle overhead for public visitors:\n\n"
        "1. Split-Screen Historian Rapid Audit Mode (#audit=1):\n"
        "   • Launch via Sidebar -> 'Historian Rapid Audit' or direct link #audit=1. Streams all 2,600 "
        "cross-dataset contradictions (3 Suspected Unmarked Landmark Demolitions, 2 Architect Career Window "
        "Checks, 1,722 City Directories Earlier Than HCAD, 51 Landmark vs Atlas Year Mismatches, 782 "
        "Absent Through 1926 Directory Rounding, 40 Address Street Mismatches).\n"
        "   • Filter by Category, Priority Score, Search Text, or Historic District (including "
        "'Undesignated / Outside HD'). Left pane compares the displayed claim against archival evidence "
        "with a native Zoom-18 roof preview; Right pane pairs 1877-1955 Sanborn Fire Insurance map plates "
        "(LOC + UT PCL), 1866-1926 HPL ContentDM City Directory page links and pre-1911 historic street "
        "rename chips, HCAD GIS, Street View Time Machine, and HAIF threads.\n"
        "   • Keyboard triage shortcuts (auto-suppressed while typing in inputs): J / K (Next / Previous), "
        "A (Approve override when Staff Admin unlocked, or queue Community Suggestion), D (Dismiss Locally), "
        "S (Skip), Escape (Close).\n\n"
        "2. In-Browser Staff Polygon Editor (Staff Admin Only, #polyedit=<hcad_or_id>):\n"
        "   • Unlock '🔒 Staff Admin' first, select a building or parcel at Zoom 19-20.5 over satellite "
        "imagery and live HCAD parcel lines, and click '📐 Edit / Add Footprint'.\n"
        "   • Choose target mode: (a) Reshape Existing Footprint (<13_digit_hcad> or existing #<slug>), or "
        "(b) Add Missing Secondary Structure (<13_digit_hcad>#aux_<N> for detached carriage houses, "
        "quarters, or backyard structures, which saves with keep_shard_footprints=false and "
        "replace_parcel_shards=false so sibling buildings stay visible). Review SOP 4 Case D warnings on "
        "multi-footprint commercial/industrial lots before using whole-parcel mode.\n"
        "   • Drag vertices (Alt-click / Delete removes vertex), click edge midpoints to insert corners, "
        "use Rotate slider / -90/-45/+45/+90 presets / -34.65 deg Downtown 1836 Borden grid preset (accurate "
        "cos(lat) tangent-plane math at 29.76N), set explicit Contributing Status, and Approve & Publish. "
        "Validated Polygon geometry saves to Pending_Submissions Col W -> Approved_Edits Col P (plus a "
        "lossless [GEOMETRY_GEOJSON:{...}] citation tag) and goes live immediately.\n\n"
        f"3. Mobile 'Walk-the-Block' Field Survey Mode (#survey=1 & Field_Surveys Tab gid={SID_SURVEYS}):\n"
        "   • Open #survey=1 on a phone while walking a historic district or survey corridor. Runs as an "
        "outdoor-legible 3-snap Split-Map Bottom Sheet (Peek / Half / Full) with a Daylight High-Contrast "
        "sunlight toggle, keeping the live GPS dot, accuracy halo, surveyed-house color rings, and "
        "tap-to-select building footprints visible on the upper half of the screen.\n"
        "   • Express 2-Tap Workflow: Surveyor Name and District stay pinned for your walk -> confirm house "
        "via GPS candidates or map tap -> tap 1 of 6 Integrity tiles (Intact, Minor Alterations, "
        "Substantially Altered, Vacant / Boarded, Demolished / Vacant Lot, Storm / Hazard Damaged; auto-fills condition & contributing "
        "defaults) -> optionally snap a street photo (saved offline in browser IndexedDB for batch "
        "Share / Download) or dictate voice Field Notes -> tap sticky 'Save & Next House' to advance along "
        "your walking side of the block (with 1-tap Undo toast).\n"
        "   • Offline Resilience & Triage: Every survey queues immediately in local browser storage with "
        "formula-safe RFC 4180 CSV export and syncs over CORS to Field_Surveys (deduplicated by survey_id "
        "in Col A). IMPORTANT: Raw field surveys carry source_type=FIELD_SURVEY_OBSERVATION and NEVER "
        "mutate the public map directly; review rows inside Field_Surveys using the 6 Saved Filter Views "
        "and promote verified dates/demolitions cleanly via Staff Admin Mode.\n"
        "   • Apps Script Redeploy Note: After updating B5 above into Extensions -> Apps Script -> Deploy -> "
        "Manage deployments -> Edit -> New Version, webhooks route directly to Field_Surveys and Col W. "
        "Any surveys submitted beforehand into Pending_Submissions can be backfilled losslessly with:\n"
        "     uv run python -m atlas_pipeline.sync_field_surveys_sheet \\\n"
        "         --ingest-pending-csv /path/to/Pending_Submissions.csv --output-dir /tmp/survey_sync"
    )
    col_c = (
        "Weekly / During Block Surveys\n"
        f"Tabs: Field_Surveys (gid={SID_SURVEYS}), Pending_Submissions (Col W) & Approved_Edits (Col P)\n"
        "Scripts: sync_field_surveys_sheet.py & sync_curated_edits.py"
    )
    row = [col_a, col_b, col_c]
    for cell in row:
        if "\u2014" in cell or "\u2013" in cell:
            raise AssertionError(f"Typographic dash leaked into SOP 15 text: {cell!r}")
    return row


def _build_sop15_format_requests() -> list[dict[str, Any]]:
    r0 = SOP15_TARGET_ROW - 1
    return [
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_INSTR,
                    "startRowIndex": r0,
                    "endRowIndex": r0 + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 3,
                },
                "cell": {
                    "userEnteredFormat": {
                        "verticalAlignment": "TOP",
                        "wrapStrategy": "WRAP",
                        "textFormat": {"fontSize": 10},
                    }
                },
                "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat.fontSize)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_INSTR,
                    "startRowIndex": r0,
                    "endRowIndex": r0 + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": rgb("#f8faf5"),
                        "textFormat": {
                            "bold": True,
                            "fontSize": 10,
                            "foregroundColor": rgb("#272727"),
                        },
                        "verticalAlignment": "TOP",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }
        },
    ]


def build_milestone3_sheet_batches(
    survey_data_rows: list[list[Any]] | None = None,
    *,
    include_add_sheet: bool = False,
    include_hub_insert: bool = False,
    add_filter_views: bool = False,
    write_survey_data_rows: bool = True,
) -> dict[str, Any]:
    """Build all Google Sheets JSON batch payloads for Milestone 3 workbook provisioning or sync.

    Returns a dictionary with keys:
      - `create_raw_batch`: `BatchUpdateSpreadsheetRequest` for one-time DDL (`addSheet`,
        `insertDimension` on Hub Row 19) plus safe grid capacity expansions.
      - `raw_values_batch`: `gsheets mutate batch --value-input RAW` operations list.
      - `formula_values_batch`: `gsheets mutate batch --value-input USER_ENTERED` operations list.
      - `style_raw_batch`: `BatchUpdateSpreadsheetRequest` for formatting, validations,
        BasicFilters, and 6 deterministic-ID Filter Views (`SID_SURVEYS + 1`..`SID_SURVEYS + 6`).
    """
    rows_body = list(survey_data_rows or [])
    rows_a_r = [HEADERS_A_TO_R] + rows_body if write_survey_data_rows else [HEADERS_A_TO_R]
    total_rows = len(rows_a_r)
    grid_rows = max(MIN_SURVEY_GRID_ROWS, total_rows + SURVEY_ROW_HEADROOM)
    total_cols = len(ALL_SURVEY_HEADERS)

    for r_idx, row in enumerate(rows_a_r, start=1):
        if len(row) != len(HEADERS_A_TO_R):
            raise AssertionError(f"Survey row {r_idx} has {len(row)} cols, expected {len(HEADERS_A_TO_R)}")
        for cell in row:
            if isinstance(cell, str) and ("\u2014" in cell or "\u2013" in cell):
                raise AssertionError(f"Typographic dash leaked into survey row {r_idx}: {cell!r}")

    # 1. Structural DDL raw batch
    create_reqs: list[dict[str, Any]] = []
    if include_add_sheet:
        create_reqs.append(
            {
                "addSheet": {
                    "properties": {
                        "sheetId": SID_SURVEYS,
                        "title": SHEET_TITLE_SURVEYS,
                        "index": 11,
                        "gridProperties": {
                            "rowCount": grid_rows,
                            "columnCount": total_cols,
                            "frozenRowCount": 1,
                            "frozenColumnCount": 4,
                        },
                        "tabColor": rgb("#0d9488"),
                    }
                }
            }
        )
    if include_hub_insert:
        create_reqs.append(
            {
                "insertDimension": {
                    "range": {
                        "sheetId": SID_HUB,
                        "dimension": "ROWS",
                        "startIndex": HUB_SURVEYS_ROW - 1,
                        "endIndex": HUB_SURVEYS_ROW,
                    },
                    "inheritFromBefore": True,
                }
            }
        )
    # Always guarantee Pending_Submissions (>=24 cols) and Approved_Edits (>=18 cols) grid room
    create_reqs.extend(
        [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": SID_PENDING,
                        "gridProperties": {
                            "columnCount": 25,
                            "frozenRowCount": 1,
                            "frozenColumnCount": 3,
                        },
                    },
                    "fields": "gridProperties(columnCount,frozenRowCount,frozenColumnCount)",
                }
            },
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": SID_APPROVED,
                        "gridProperties": {
                            "columnCount": 18,
                            "frozenRowCount": 1,
                            "frozenColumnCount": 3,
                        },
                    },
                    "fields": "gridProperties(columnCount,frozenRowCount,frozenColumnCount)",
                }
            },
        ]
    )

    # 2. RAW values batch (never touches Instructions_&_Apps_Script!B6!)
    sop15_row = build_sop15_row()
    if "\u2014" in APPS_SCRIPT_CODE or "\u2013" in APPS_SCRIPT_CODE:
        raise AssertionError("Typographic dash leaked into APPS_SCRIPT_CODE")

    raw_values_ops: list[dict[str, Any]] = [
        {
            "op": "write",
            "range": f"{SHEET_TITLE_SURVEYS}!A1:R{total_rows}",
            "data": rows_a_r,
        },
        {
            "op": "write",
            "range": "Pending_Submissions!W1",
            "data": [["geometry_geojson"]],
        },
        {
            "op": "write",
            "range": "Approved_Edits!A1:P1",
            "data": [APPROVED_EDITS_HEADERS],
        },
        {
            "op": "write",
            "range": "Instructions_&_Apps_Script!A5:C5",
            "data": [
                [
                    "4. Google Apps Script Code (Copy & Paste into Extensions -> Apps Script -> Deploy as Web App)",
                    APPS_SCRIPT_CODE,
                    "Extensions -> Apps Script -> Deploy -> Manage deployments (Execute as: Me, Who has access: Anyone)",
                ]
            ],
        },
        {
            "op": "write",
            "range": f"Instructions_&_Apps_Script!A{SOP15_TARGET_ROW}:C{SOP15_TARGET_ROW}",
            "data": [sop15_row],
        },
    ]

    # 3. Formula (USER_ENTERED) values batch
    r_in = HUB_FILTER_INPUT_ROW
    survey_formula_headers = [
        [
            '={"open_in_atlas"; ARRAYFORMULA(IF(LEN(A2:A)=0, , IF(LEN(D2:D)>0, '
            f'HYPERLINK("{ATLAS_BASE_URL}#hcad=" & D2:D, "🗺️ Open in Atlas ↗"), '
            f'IF(REGEXMATCH(TO_TEXT(R2:R), "^-?[0-9.]+,\\\\s*-?[0-9.]+"), '
            f'HYPERLINK("{ATLAS_BASE_URL}#lat=" & REGEXEXTRACT(TO_TEXT(R2:R), "^(-?[0-9.]+)") '
            '& "&lng=" & REGEXEXTRACT(TO_TEXT(R2:R), ",\\\\s*(-?[0-9.]+)") & "&z=19&survey=1", '
            '"📍 Open GPS Pin ↗"), ))))}',
            '={"inspect_hcad"; ARRAYFORMULA(IF(LEN(D2:D)=0, , '
            'IF(REGEXMATCH(TO_TEXT(D2:D), "^[0-9]{13}"), '
            'HYPERLINK("https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=" & LEFT(D2:D, 13), '
            '"🏛️ HCAD Parcel ↗"), )))}',
        ]
    ]
    formula_values_ops: list[dict[str, Any]] = [
        {
            "op": "write",
            "range": f"{SHEET_TITLE_SURVEYS}!S1:T1",
            "data": survey_formula_headers,
        },
        {
            "op": "write",
            "range": "Pending_Submissions!S1",
            "data": [[PENDING_TRIAGE_FORMULA]],
        },
        {
            "op": "write",
            "range": "Approved_Edits!A2",
            "data": [[APPROVED_EDITS_FILTER_FORMULA]],
        },
        {
            "op": "write",
            "range": "Admin_Hub_&_Quick_Filter!G17:I17",
            "data": [
                [
                    "Historian Split-Screen Audit Mode (Live Atlas)",
                    f'=HYPERLINK("{ATLAS_BASE_URL}#audit=1", "🔍 Open Split-Screen Historian Audit ↗")',
                    "Keyboard-driven (J/K/A/D/S) triage across all 2,600 contradictions",
                ]
            ],
        },
        {
            "op": "write",
            "range": f"Admin_Hub_&_Quick_Filter!A{HUB_SURVEYS_ROW}:I{HUB_SURVEYS_ROW}",
            "data": [
                [
                    "📋 Walk-the-Block Field Surveys Logged",
                    f"=COUNTA(IFERROR({SHEET_TITLE_SURVEYS}!A2:A))",
                    "On block surveys / weekly",
                    f"In {SHEET_TITLE_SURVEYS} -> Data -> Filter views ({SURVEY_FILTER_VIEW_COUNT} pre-built integrity & district triage views)",
                    f'=HYPERLINK("#gid={SID_SURVEYS}", "Open {SHEET_TITLE_SURVEYS} Tab ↗")',
                    "",
                    "Walk-the-Block Field Survey Mode (Mobile Map)",
                    f'=HYPERLINK("{ATLAS_BASE_URL}#survey=1", "📋 Open Walk-the-Block Survey ↗")',
                    "Outdoor mobile field survey sheet + offline sync + Staff Polygon Editor",
                ]
            ],
        },
        {
            "op": "write",
            "range": f"Admin_Hub_&_Quick_Filter!A{HUB_SURVEYS_ROW + 1}",
            "data": [
                [
                    f"HOW TO USE: Select a Triage Category (A{r_in}), Status (B{r_in}), or Historic District (C{r_in}) "
                    f"from the dropdowns below, or type any keyword in D{r_in} (e.g. street name, HCAD account, "
                    "building name, or submitter). Click '✏️ Edit Row #...' in Column A of the results to jump "
                    "straight to that exact cell in Pending_Submissions!"
                ]
            ],
        },
        {
            "op": "write",
            "range": f"Admin_Hub_&_Quick_Filter!F{r_in}",
            "data": [[build_hub_filter_count_formula(r_in)]],
        },
        {
            "op": "write",
            "range": f"Admin_Hub_&_Quick_Filter!A{HUB_RESULTS_FORMULA_ROW}",
            "data": [[build_hub_results_formula(r_in)]],
        },
    ]

    # 4. Idempotent Style, Validation, BasicFilter & Filter Views raw batch
    c_charcoal = rgb("#272727")
    c_sage = rgb("#95c959")
    c_white = rgb("#ffffff")

    def col_width_req(sheet_id: int, start_col: int, end_col: int, px: int) -> dict[str, Any]:
        return {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col,
                    "endIndex": end_col,
                },
                "properties": {"pixelSize": px},
                "fields": "pixelSize",
            }
        }

    def validation_req(col_idx: int, values: list[str]) -> dict[str, Any]:
        return {
            "setDataValidation": {
                "range": {
                    "sheetId": SID_SURVEYS,
                    "startRowIndex": 1,
                    "endRowIndex": grid_rows,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": v} for v in values],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        }

    def cond_rule_req(
        col_idx: int,
        cond_type: str,
        val: str,
        bg_hex: str,
        fg_hex: str,
        idx: int,
    ) -> dict[str, Any]:
        return {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [
                        {
                            "sheetId": SID_SURVEYS,
                            "startRowIndex": 1,
                            "endRowIndex": grid_rows,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1,
                        }
                    ],
                    "booleanRule": {
                        "condition": {
                            "type": cond_type,
                            "values": [{"userEnteredValue": val}],
                        },
                        "format": {
                            "backgroundColor": rgb(bg_hex),
                            "textFormat": {"bold": True, "foregroundColor": rgb(fg_hex)},
                        },
                    },
                },
                "index": idx,
            }
        }

    survey_filter_range = {
        "sheetId": SID_SURVEYS,
        "startRowIndex": 0,
        "endRowIndex": grid_rows,
        "startColumnIndex": 0,
        "endColumnIndex": total_cols,
    }

    def filter_view_req(offset: int, filter_body: dict[str, Any]) -> dict[str, Any]:
        body = {"filterViewId": SID_SURVEYS + offset, **filter_body}
        if include_add_sheet or add_filter_views:
            return {"addFilterView": {"filter": body}}
        return {
            "updateFilterView": {
                "filter": body,
                "fields": "title,range,criteria,sortSpecs",
            }
        }

    col_widths_px = [
        (COL_IDX["survey_id"], COL_IDX["survey_id"] + 1, 200),
        (COL_IDX["surveyed_at"], COL_IDX["surveyed_at"] + 1, 155),
        (COL_IDX["review_status"], COL_IDX["review_status"] + 1, 155),
        (COL_IDX["hcad_num"], COL_IDX["hcad_num"] + 1, 150),
        (COL_IDX["address"], COL_IDX["address"] + 1, 220),
        (COL_IDX["district_or_corridor"], COL_IDX["district_or_corridor"] + 1, 210),
        (COL_IDX["integrity_grade"], COL_IDX["integrity_grade"] + 1, 175),
        (COL_IDX["condition_rating"], COL_IDX["condition_rating"] + 1, 125),
        (COL_IDX["suggested_contributing"], COL_IDX["suggested_contributing"] + 1, 205),
        (COL_IDX["windows_status"], COL_IDX["porch_integrity"] + 1, 160),
        (COL_IDX["atlas_year_built"], COL_IDX["atlas_year_built"] + 1, 110),
        (COL_IDX["observed_year_or_demo_note"], COL_IDX["observed_year_or_demo_note"] + 1, 185),
        (COL_IDX["photo_url"], COL_IDX["photo_url"] + 1, 220),
        (COL_IDX["field_notes"], COL_IDX["field_notes"] + 1, 340),
        (COL_IDX["surveyor_name"], COL_IDX["surveyor_name"] + 1, 155),
        (COL_IDX["gps_lat_lng_acc"], COL_IDX["gps_lat_lng_acc"] + 1, 170),
        (COL_IDX["open_in_atlas"], COL_IDX["inspect_hcad"] + 1, 155),
    ]

    style_reqs: list[dict[str, Any]] = [
        # Ensure Field_Surveys grid capacity and freeze panes
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": SID_SURVEYS,
                    "gridProperties": {
                        "rowCount": grid_rows,
                        "columnCount": total_cols,
                        "frozenRowCount": 1,
                        "frozenColumnCount": 4,
                    },
                },
                "fields": "gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount)",
            }
        },
        # Header bar on Field_Surveys (A1:T1)
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_SURVEYS,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": total_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": c_charcoal,
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": c_sage,
                            "fontSize": 10,
                        },
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }
        },
        # Body font & alignment on Field_Surveys
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_SURVEYS,
                    "startRowIndex": 1,
                    "endRowIndex": grid_rows,
                    "startColumnIndex": 0,
                    "endColumnIndex": total_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "verticalAlignment": "TOP",
                        "wrapStrategy": "CLIP",
                        "textFormat": {"fontSize": 10},
                    }
                },
                "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat.fontSize)",
            }
        },
        # Wrap field_notes column
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_SURVEYS,
                    "startRowIndex": 1,
                    "endRowIndex": grid_rows,
                    "startColumnIndex": COL_IDX["field_notes"],
                    "endColumnIndex": COL_IDX["field_notes"] + 1,
                },
                "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat.wrapStrategy",
            }
        },
        # Plain Text (@) formatting on survey_id, hcad_num, and observed_year_or_demo_note
        *[
            {
                "repeatCell": {
                    "range": {
                        "sheetId": SID_SURVEYS,
                        "startRowIndex": 1,
                        "endRowIndex": grid_rows,
                        "startColumnIndex": COL_IDX[col_name],
                        "endColumnIndex": COL_IDX[col_name] + 1,
                    },
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT", "pattern": "@"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            }
            for col_name in ("survey_id", "review_status", "hcad_num", "observed_year_or_demo_note")
        ],
        # Header style & widths for newly added geometry_geojson columns on Pending_Submissions (W1) & Approved_Edits (P1)
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_PENDING,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": PENDING_TOTAL_COLS,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": c_charcoal,
                        "textFormat": {"bold": True, "foregroundColor": c_sage, "fontSize": 10},
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }
        },
        col_width_req(SID_PENDING, 22, 23, 260),
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_APPROVED,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": APPROVED_TOTAL_COLS,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": c_charcoal,
                        "textFormat": {"bold": True, "foregroundColor": c_sage, "fontSize": 10},
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }
        },
        col_width_req(SID_APPROVED, 15, 16, 260),
        # Update BasicFilters on Pending_Submissions (A:W) and Approved_Edits (A:P)
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": SID_PENDING,
                        "startRowIndex": 0,
                        "endRowIndex": 5000,
                        "startColumnIndex": 0,
                        "endColumnIndex": PENDING_TOTAL_COLS,
                    }
                }
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": SID_APPROVED,
                        "startRowIndex": 0,
                        "endRowIndex": 2500,
                        "startColumnIndex": 0,
                        "endColumnIndex": APPROVED_TOTAL_COLS,
                    }
                }
            }
        },
        # Update Admin_Hub_&_Quick_Filter Row 19 body style + Row 22 Triage dropdown validation
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_HUB,
                    "startRowIndex": HUB_SURVEYS_ROW - 1,
                    "endRowIndex": HUB_SURVEYS_ROW,
                    "startColumnIndex": 0,
                    "endColumnIndex": 12,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": c_white,
                        "textFormat": {
                            "bold": False,
                            "fontSize": 10,
                            "foregroundColor": c_charcoal,
                        },
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }
        },
        {
            "setDataValidation": {
                "range": {
                    "sheetId": SID_HUB,
                    "startRowIndex": r_in - 1,
                    "endRowIndex": r_in,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": v} for v in HUB_TRIAGE_DROPDOWN_VALUES],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        },
        # Data validations on Field_Surveys
        validation_req(COL_IDX["review_status"], SURVEY_REVIEW_STATUSES),
        validation_req(COL_IDX["integrity_grade"], list(FIELD_SURVEY_INTEGRITY_GRADES)),
        validation_req(COL_IDX["condition_rating"], list(FIELD_SURVEY_CONDITION_RATINGS)),
        validation_req(COL_IDX["suggested_contributing"], SURVEY_CONTRIBUTING_VALUES),
    ]

    for sc, ec, px in col_widths_px:
        style_reqs.append(col_width_req(SID_SURVEYS, sc, ec, px))

    if include_add_sheet:
        rev_col = COL_IDX["review_status"]
        int_col = COL_IDX["integrity_grade"]
        style_reqs.extend(
            [
                cond_rule_req(rev_col, "TEXT_EQ", "Unreviewed", "#fff2cc", "#7f6000", 0),
                cond_rule_req(rev_col, "TEXT_EQ", "Verified", "#d9ead3", "#274e13", 1),
                cond_rule_req(rev_col, "TEXT_EQ", "Promoted to Override", "#d0e0e3", "#0c343d", 2),
                cond_rule_req(rev_col, "TEXT_EQ", "Needs Follow-Up", "#fce5cd", "#783f04", 3),
                cond_rule_req(int_col, "TEXT_EQ", "Intact", "#dcfce7", "#166534", 4),
                cond_rule_req(int_col, "TEXT_EQ", "Minor Alterations", "#dbeafe", "#1e40af", 5),
                cond_rule_req(int_col, "TEXT_EQ", "Substantially Altered", "#fef9c3", "#854d0e", 6),
                cond_rule_req(int_col, "TEXT_EQ", "Vacant / Boarded", "#e2e8f0", "#334155", 7),
                cond_rule_req(int_col, "TEXT_EQ", "Demolished / Vacant Lot", "#ffedd5", "#9a3412", 8),
                cond_rule_req(int_col, "TEXT_EQ", "Storm / Hazard Damaged", "#fee2e2", "#991b1b", 9),
            ]
        )

    style_reqs.extend(
        [
            {"setBasicFilter": {"filter": {"range": survey_filter_range}}},
            filter_view_req(
                1,
                {
                    "title": "📋 1. Unreviewed Field Observations (Newest First)",
                    "range": survey_filter_range,
                    "criteria": {
                        str(COL_IDX["review_status"]): {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": "Unreviewed"}],
                            }
                        }
                    },
                    "sortSpecs": [{"dimensionIndex": COL_IDX["surveyed_at"], "sortOrder": "DESCENDING"}],
                },
            ),
            filter_view_req(
                2,
                {
                    "title": "🚨 2. Demolished or Vacant Lots Observed",
                    "range": survey_filter_range,
                    "criteria": {
                        str(COL_IDX["integrity_grade"]): {
                            "condition": {
                                "type": "CUSTOM_FORMULA",
                                "values": [
                                    {
                                        "userEnteredValue": '=REGEXMATCH($G2, "^(Demolished / Vacant Lot|Vacant / Boarded)$")'
                                    }
                                ],
                            }
                        }
                    },
                    "sortSpecs": [{"dimensionIndex": COL_IDX["surveyed_at"], "sortOrder": "DESCENDING"}],
                },
            ),
            filter_view_req(
                3,
                {
                    "title": "🏛️ 3. Intact Historic & Minor Alterations (Contributing Candidates)",
                    "range": survey_filter_range,
                    "criteria": {
                        str(COL_IDX["integrity_grade"]): {
                            "condition": {
                                "type": "CUSTOM_FORMULA",
                                "values": [
                                    {
                                        "userEnteredValue": '=REGEXMATCH($G2, "^(Intact|Minor Alterations)$")'
                                    }
                                ],
                            }
                        }
                    },
                    "sortSpecs": [
                        {"dimensionIndex": COL_IDX["district_or_corridor"], "sortOrder": "ASCENDING"},
                        {"dimensionIndex": COL_IDX["address"], "sortOrder": "ASCENDING"},
                    ],
                },
            ),
            filter_view_req(
                4,
                {
                    "title": "⚠️ 4. Critical or Poor Physical Condition",
                    "range": survey_filter_range,
                    "criteria": {
                        str(COL_IDX["condition_rating"]): {
                            "condition": {
                                "type": "CUSTOM_FORMULA",
                                "values": [{"userEnteredValue": '=REGEXMATCH($H2, "^(Poor|Critical / Emergency)$")'}],
                            }
                        }
                    },
                    "sortSpecs": [{"dimensionIndex": COL_IDX["surveyed_at"], "sortOrder": "DESCENDING"}],
                },
            ),
            filter_view_req(
                5,
                {
                    "title": "📷 5. Observations with Field Photos Attached",
                    "range": survey_filter_range,
                    "criteria": {
                        str(COL_IDX["photo_url"]): {"condition": {"type": "NOT_BLANK"}},
                    },
                    "sortSpecs": [{"dimensionIndex": COL_IDX["surveyed_at"], "sortOrder": "DESCENDING"}],
                },
            ),
            filter_view_req(
                6,
                {
                    "title": "🗺️ 6. All Blockface Observations by District & Street",
                    "range": survey_filter_range,
                    "criteria": {},
                    "sortSpecs": [
                        {"dimensionIndex": COL_IDX["district_or_corridor"], "sortOrder": "ASCENDING"},
                        {"dimensionIndex": COL_IDX["address"], "sortOrder": "ASCENDING"},
                    ],
                },
            ),
            *_build_sop15_format_requests(),
        ]
    )

    return {
        "create_raw_batch": {"requests": create_reqs},
        "raw_values_batch": raw_values_ops,
        "formula_values_batch": formula_values_ops,
        "style_raw_batch": {"requests": style_reqs},
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build Google Sheets sync batches for Field_Surveys.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--existing-surveys-csv",
        type=Path,
        default=None,
        help="Existing Field_Surveys CSV export so recovered rows merge without clobbering live sheet rows",
    )
    parser.add_argument(
        "--ingest-pending-csv",
        type=Path,
        default=None,
        help="Optional Pending_Submissions CSV export to recover [FIELD_SURVEY_JSON:{...}] rows",
    )
    parser.add_argument(
        "--allow-unmerged-overwrite",
        action="store_true",
        help="Explicitly allow writing recovered survey rows without --existing-surveys-csv on a clean tab",
    )
    parser.add_argument(
        "--update-hub-row",
        action="store_true",
        help="Refresh Admin_Hub_&_Quick_Filter Row 19 telemetry, headers, formulas, and SOP 15 without touching Field_Surveys!A2:R",
    )
    parser.add_argument(
        "--include-add-sheet",
        action="store_true",
        help="ONE-TIME ONLY: emit addSheet, conditional formatting rules, and addFilterView requests",
    )
    parser.add_argument(
        "--include-hub-insert",
        action="store_true",
        help="ONE-TIME ONLY: insert Row 19 on Admin_Hub_&_Quick_Filter for Field_Surveys telemetry",
    )
    parser.add_argument(
        "--add-filter-views",
        action="store_true",
        help="Create the 6 Field_Surveys filter views via addFilterView instead of updateFilterView",
    )
    args = parser.parse_args(argv)

    if (
        args.ingest_pending_csv is not None
        and args.existing_surveys_csv is None
        and not args.include_add_sheet
        and not args.allow_unmerged_overwrite
    ):
        parser.error(
            "--ingest-pending-csv requires --existing-surveys-csv <Field_Surveys.csv> "
            "(or --include-add-sheet / --allow-unmerged-overwrite on an empty tab) "
            "so live webhook observations in Field_Surveys!A2:R are never truncated."
        )

    existing_rows: list[list[Any]] = []
    if args.existing_surveys_csv:
        existing_rows = load_existing_field_surveys_csv(
            args.existing_surveys_csv.read_text(encoding="utf-8-sig")
        )
        print(f"Loaded {len(existing_rows)} existing Field_Surveys row(s) from {args.existing_surveys_csv}")

    recovered_rows: list[list[Any]] = []
    if args.ingest_pending_csv:
        recovered_rows = recover_field_survey_rows_from_pending_csv(
            args.ingest_pending_csv.read_text(encoding="utf-8-sig")
        )
        print(f"Recovered {len(recovered_rows)} unique Field Survey row(s) from {args.ingest_pending_csv}")

    write_survey_data_rows = bool(
        args.include_add_sheet
        or args.existing_surveys_csv is not None
        or args.ingest_pending_csv is not None
    )
    merged_rows = merge_field_survey_rows(existing_rows, recovered_rows) if write_survey_data_rows else []

    batches = build_milestone3_sheet_batches(
        merged_rows,
        include_add_sheet=args.include_add_sheet,
        include_hub_insert=args.include_hub_insert,
        add_filter_views=args.add_filter_views,
        write_survey_data_rows=write_survey_data_rows,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in batches.items():
        out_path = args.output_dir / f"field_surveys_{name}.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        count = len(payload["requests"]) if isinstance(payload, dict) else len(payload)
        print(f"Wrote {out_path} ({count} items)")


if __name__ == "__main__":
    main()
