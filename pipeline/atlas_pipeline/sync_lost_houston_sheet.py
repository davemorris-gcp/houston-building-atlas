"""Generate Google Sheets batch payloads for the read-only Lost_Houston_Catalog tab.

Reads the Milestone 2 Lost Houston overlay (`app/public/data/overlays.json["lost_houston"]`)
and curated archival photo index (`app/public/data/building_photos.json`), optionally merges
newly staged `lost_*` archival photographs from a `Building_Photos` CSV export (with strict
`DF-6` parcel-isolation guards), enriches each demolished structure with spatial jurisdiction
overlays (Super Neighborhood, Houston City Council District, 1937 HOLC Redlining Grade & Zone,
and COH Historic/Heritage/NRHP District boundaries), and writes JSON batch payloads compatible
with `gsheets mutate`:

  1. `lost_houston_create_raw_batch.json`  (one-time structural DDL: tab creation + Admin Hub row insert)
  2. `lost_houston_values_batch.json`      (idempotent clear + RAW text/number slices + ARRAYFORMULAs + optional Hub/SOP 14)
  3. `lost_houston_style_raw_batch.json`   (idempotent formatting, widths, BasicFilter, 7 Filter View syncs + SOP 14 styling)

Usage:
  # Repeatable re-sync on existing workbook:
  uv run python -m atlas_pipeline.sync_lost_houston_sheet --output-dir /path/to/scratch

  # Ingest newly staged lost_* photo rows from Building_Photos CSV export + re-sync sheet:
  uv run python -m atlas_pipeline.sync_lost_houston_sheet \
      --ingest-building-photos-csv /path/to/Building_Photos.csv \
      --write-site-data --include-sop14 --update-hub-row --output-dir /path/to/scratch
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from typing import Any

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from atlas_pipeline.lost_houston_expansion import build_lost_houston_photo_record
from atlas_pipeline.schema import sanitize_atlas_text, write_compact_json

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OVERLAYS_PATH = REPO_ROOT / "app" / "public" / "data" / "overlays.json"
DEFAULT_PHOTOS_PATH = REPO_ROOT / "app" / "public" / "data" / "building_photos.json"

SID_HUB = 1383259584
SID_INSTR = 1038833872
SID_PHOTOS = 1351980492
SID_LOST = 824622001
SHEET_TITLE_LOST = "Lost_Houston_Catalog"
# Live Instructions_&_Apps_Script row map: Rows 1..5 overview, Row 6 Passkey (B6),
# Row 7 divider, Rows 8..24 Sections 7..22 (SOP 1..13 + Master Docs section at Row 24).
# Verify A24:A26 before changing so --include-sop14 never overwrites Row 24 or touches Cell B6.
SOP14_TARGET_ROW = 25
ATLAS_BASE_URL = "https://davemorris-gcp.github.io/houston-building-atlas/"
# Must stay synchronized with Building_Photos!L1 in setup_admin_sheet.py so DF-6
# lost_* rows (blank Col A hcad_num) retain deep links alongside standing HCAD rows.
BUILDING_PHOTOS_OPEN_IN_ATLAS_FORMULA = (
    '={"open_in_atlas"; ARRAYFORMULA(IF(REGEXMATCH(TRIM(TO_TEXT(B2:B)), "^(?i)lost_"), '
    f'HYPERLINK("{ATLAS_BASE_URL}#lost_houston=1&bnd=lost_houston:" & LOWER(TRIM(B2:B)), '
    '"🏛️ Open Lost Site in Atlas ↗"), '
    f'IF(LEN(A2:A)=0, , HYPERLINK("{ATLAS_BASE_URL}#hcad=" & A2:A, '
    '"🗺️ Open Building in Atlas ↗"))))}'
)

MIN_SHEET_GRID_ROWS = 700
SHEET_ROW_HEADROOM = 100
FILTER_VIEW_COUNT = 7

COHORT_SPECS: dict[str, tuple[int, str]] = {
    "curated_landmark": (1, "1. Curated Survey Landmark"),
    "coh_landmark_demolished": (2, "2. COH Landmark - Demolished"),
    "nrhp_demolished": (3, "3. NRHP Property - Demolished"),
    "red_book_1915_vanished": (4, "4. 1915 Red Book - Vanished"),
    "historic_teardown_2020_2026": (5, "5. Pre-1940 Teardown (2020-2026)"),
}

HEADERS_A_TO_AC = [
    "lost_id",
    "cohort_tier",
    "name",
    "address",
    "lifespan_label",
    "year_built",
    "year_demolished",
    "lifespan_years",
    "neighborhood",
    "super_neighborhood",
    "council_district",
    "holc_1937_grade",
    "holc_1937_zone",
    "historic_district",
    "designation_or_record_id",
    "category",
    "architect_or_builder",
    "architectural_style",
    "sanborn_material",
    "stories",
    "demolition_cause",
    "replaced_by",
    "historic_significance",
    "site_hcad_nums",
    "footprint_provenance",
    "primary_source_citation",
    "primary_source_url",
    "archival_photo_url",
    "archival_photo_credit",
]
HEADERS_AD_TO_AF = ["open_in_atlas", "street_view", "hcad_parcel"]
HEADERS_AG_TO_AH = ["lat", "lon"]
ALL_HEADERS = HEADERS_A_TO_AC + HEADERS_AD_TO_AF + HEADERS_AG_TO_AH
COL_IDX: dict[str, int] = {name: idx for idx, name in enumerate(ALL_HEADERS)}


def col_letter(col_idx: int) -> str:
    """Convert a 0-based column index to Google Sheets A1 column letters (0 -> 'A', 27 -> 'AB')."""
    if col_idx < 0:
        raise ValueError(f"col_idx must be >= 0, got {col_idx}")
    out = ""
    n = col_idx + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def compute_grid_row_count(total_rows: int) -> int:
    """Compute sheet grid row capacity with headroom above populated catalog rows."""
    return max(MIN_SHEET_GRID_ROWS, total_rows + SHEET_ROW_HEADROOM)


def rgb(hex_str: str) -> dict[str, float]:
    """Convert a 6-digit hex color string into a Google Sheets API v4 float Color dict."""
    h = hex_str.lstrip("#")
    return {
        "red": round(int(h[0:2], 16) / 255.0, 4),
        "green": round(int(h[2:4], 16) / 255.0, 4),
        "blue": round(int(h[4:6], 16) / 255.0, 4),
    }


def sanitize_sheet_text(value: Any) -> str:
    """Normalize text for Google Sheets cells using canonical Atlas dash scrubbing."""
    if value is None:
        return ""
    cleaned = sanitize_atlas_text(str(value).replace("\u2014", " - ").replace("\u2013", "-"))
    return " ".join(cleaned.split())


class _SpatialOverlayIndex:
    """R-tree spatial lookup matching `build_neighborhood_scorecards` boundary semantics."""

    def __init__(self, features: list[dict[str, Any]]) -> None:
        self._geoms = []
        self._props: list[dict[str, Any]] = []
        for feat in features:
            geom_raw = feat.get("geometry")
            if not geom_raw:
                continue
            self._geoms.append(shape(geom_raw))
            self._props.append(feat.get("properties") or {})
        self._tree = STRtree(self._geoms) if self._geoms else None

    def matching_props(self, pt: Point) -> list[dict[str, Any]]:
        if self._tree is None:
            return []
        hits = sorted(int(i) for i in self._tree.query(pt, predicate="intersects"))
        return [self._props[i] for i in hits]


def format_designation_id(props: dict[str, Any]) -> str:
    """Format canonical preservation designation or archival record identifiers."""
    parts: list[str] = []
    coh_code = sanitize_sheet_text(props.get("coh_landmark_code") or props.get("coh_landmark_id"))
    if coh_code:
        parts.append(f"COH #{coh_code}")
    nrhp_ref = sanitize_sheet_text(props.get("nrhp_refnum"))
    if nrhp_ref:
        parts.append(f"NRHP #{nrhp_ref}")
    rb_id = sanitize_sheet_text(props.get("red_book_id"))
    if rb_id:
        parts.append(f"1915 Red Book: {rb_id}")
    permit_dt = sanitize_sheet_text(props.get("demo_permit_date"))
    permit_desc = sanitize_sheet_text(props.get("demo_permit_desc"))
    if permit_dt:
        parts.append(f"Demo Permit {permit_dt}" + (f" ({permit_desc})" if permit_desc else ""))
    loc_vol = sanitize_sheet_text(props.get("loc_volume_id"))
    loc_sheet = props.get("loc_sheet_num")
    if loc_vol and loc_sheet is not None and not parts:
        parts.append(f"Sanborn {loc_vol} Sh. {loc_sheet}")
    return "; ".join(parts)


def ingest_lost_houston_sheet_photos(
    overlays: dict[str, Any],
    building_photos: dict[str, Any],
    csv_path: Path,
) -> dict[str, int]:
    """Non-destructively merge `lost_*` photo rows from a `Building_Photos` CSV export.

    Mutates `building_photos["photos"]` and `overlays["lost_houston"]` feature properties
    in place atomically ONLY after all CSV rows pass schema and `lost_id` validation.
    Enforces `DF-6` isolation via `build_lost_houston_photo_record` (omitting `hcad_num`
    and `address` and setting `is_lost_houston: True`) even if an editor accidentally
    filled parcel columns in the sheet, tags records with `"curation_source":
    "building_photos_sheet"` so catalog rebuilds preserve them, and promotes real photos
    ahead of LOC Sanborn map plate fallbacks (`sanborn_sheet_image_url`).
    """
    lost_features_by_id: dict[str, dict[str, Any]] = {}
    for feat in overlays.get("lost_houston", {}).get("features", []):
        props = feat.get("properties") or {}
        lid = str(props.get("id") or "").strip().lower()
        if lid:
            lost_features_by_id[lid] = props

    existing_photos = building_photos.setdefault("photos", [])
    seen_keys: set[tuple[str, str]] = set()
    for p in existing_photos:
        bid = str(p.get("building_id") or "").strip().lower()
        img = sanitize_sheet_text(p.get("image_url"))
        if bid and img:
            seen_keys.add((bid, img))

    scanned_lost_rows = 0
    staged: list[tuple[dict[str, Any], dict[str, Any], str, str]] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = {str(h or "").strip() for h in (reader.fieldnames or [])}
        missing_cols = {"building_id", "image_url"} - headers
        if missing_cols:
            raise ValueError(
                f"Building_Photos CSV {csv_path} missing required column(s): "
                f"{sorted(missing_cols)}; verify you exported the Building_Photos tab."
            )
        for row_num, raw_row in enumerate(reader, start=2):
            raw_bid = str(raw_row.get("building_id") or "").strip()
            if not raw_bid.lower().startswith("lost_"):
                continue
            scanned_lost_rows += 1
            bid_norm = raw_bid.lower()
            if bid_norm not in lost_features_by_id:
                raise ValueError(
                    f"Building_Photos CSV row {row_num} references unknown lost_id {raw_bid!r}; "
                    f"copy exact ID from Lost_Houston_Catalog Column A."
                )
            img_url = sanitize_sheet_text(raw_row.get("image_url"))
            if not img_url.startswith(("http://", "https://")):
                raise ValueError(
                    f"Building_Photos CSV row {row_num} ({bid_norm}) requires a direct http(s) image_url."
                )
            if (bid_norm, img_url) in seen_keys:
                continue

            fprops = lost_features_by_id[bid_norm]
            raw_yr = str(raw_row.get("photo_year") or "").strip()
            photo_year = int(raw_yr) if raw_yr.isdigit() else 0
            canonical_id = str(fprops.get("id") or bid_norm)
            landmark_name = sanitize_sheet_text(raw_row.get("landmark_name")) or sanitize_sheet_text(
                fprops.get("name")
            )
            credit = sanitize_sheet_text(raw_row.get("credit") or raw_row.get("attribution"))

            rec = build_lost_houston_photo_record(
                building_id=canonical_id,
                landmark_name=landmark_name,
                image_url=img_url,
                thumb_url=sanitize_sheet_text(raw_row.get("thumb_url")),
                photo_year=photo_year,
                era_label=sanitize_sheet_text(raw_row.get("era_label")),
                caption=sanitize_sheet_text(raw_row.get("caption")),
                attribution=credit,
                source_url=sanitize_sheet_text(raw_row.get("source_url")),
            )
            rec["curation_source"] = "building_photos_sheet"
            seen_keys.add((bid_norm, img_url))
            staged.append((rec, fprops, img_url, credit))

    backfilled_features = 0
    for rec, fprops, img_url, credit in staged:
        existing_photos.append(rec)
        cur_photo = sanitize_sheet_text(fprops.get("photo_url"))
        sanborn_fallback = sanitize_sheet_text(fprops.get("sanborn_sheet_image_url"))
        if not cur_photo or (sanborn_fallback and cur_photo == sanborn_fallback):
            fprops["photo_url"] = img_url
            if credit:
                fprops["photo_credit"] = credit
            backfilled_features += 1
    added_photos = len(staged)

    return {
        "scanned_lost_rows": scanned_lost_rows,
        "added_photos": added_photos,
        "backfilled_features": backfilled_features,
    }


def build_sop14_row(metrics: dict[str, Any]) -> list[str]:
    """Build the 3-column SOP 14 Administrator Manual row for `Instructions_&_Apps_Script`."""
    total_sites = metrics["total_sites"]
    photo_sites = metrics["photo_site_count"]
    unphoto_sites = metrics["unphotographed_site_count"]
    total_photos = metrics["total_curated_photos"]
    cohorts = metrics["cohort_counts"]
    desig = metrics["designated_landmark_count"]
    holc_d = metrics["holc_counts"].get("D", 0)

    col_a = "23. SOP 14 - Lost Houston Catalog Curation & Archival Photo Backfill (Lost_Houston_Catalog Tab)"
    col_b = (
        f"The read-only 'Lost_Houston_Catalog' tab (gid={SID_LOST}) indexes all {total_sites} verified "
        f"Milestone 2 demolished structures across 5 provenance cohorts: Cohort 1 Curated Survey Landmarks "
        f"({cohorts.get(1, 0)}), Cohort 2 COH Designated Landmarks Lost ({cohorts.get(2, 0)}), Cohort 3 NRHP "
        f"Properties Lost ({cohorts.get(3, 0)}), Cohort 4 1915 Red Book Vanished Black Houston Sites "
        f"({cohorts.get(4, 0)}), and Cohort 5 2020-2026 Pre-1940 Teardowns ({cohorts.get(5, 0)}). "
        f"Currently {photo_sites} sites carry {total_photos} curated archival photographs and "
        f"{unphoto_sites} sites await archival photo backfill.\n\n"
        "1. Filtering Lost_Houston_Catalog Without Affecting Other Staff (7 Saved Filter Views):\n"
        "   • Open 'Lost_Houston_Catalog' -> Data -> Filter views:\n"
        f"     - '🏛️ 1. Designated COH & NRHP Landmarks Lost ({desig} Sites)' (includes COH #98LD74 in Cohort 1)\n"
        f"     - '🏙️ 2. Curated Surveyed Monuments ({cohorts.get(1, 0)} Sites)'\n"
        f"     - '📕 3. 1915 Red Book Vanished Black Houston Sites ({cohorts.get(4, 0)} Sites)'\n"
        f"     - '🚧 4. Recent Pre-1940 Teardowns 2020-2026 ({cohorts.get(5, 0)} Sites)'\n"
        f"     - '🟥 5. Inside 1937 HOLC Grade D Redlined Zones ({holc_d} Sites)'\n"
        f"     - '📷 6. Sites with Curated Archival Photos ({photo_sites} Sites)'\n"
        f"     - '🖼️ 7. Unphotographed Sites - Archival Photo Backfill Queue ({unphoto_sites} Sites)'\n"
        "   • Why the tab is Warning-Protected (Read-Only): Direct cell edits inside Lost_Houston_Catalog are "
        "wiped on every pipeline sync ('clear Lost_Houston_Catalog!A1:AH'). Always stage new photos in "
        "'Building_Photos' and source/geometry updates in Git.\n\n"
        "2. Working the Archival Photo Backfill Queue (HMRC, LOC HABS, Rice Woodson, Portal to Texas History):\n"
        "   • Select Filter View '🖼️ 7. Unphotographed Sites - Archival Photo Backfill Queue' (auto-sorted "
        "by Cohort Tier 1 -> 5 and earliest year_built first so unphotographed major monuments appear at the top; "
        "note that 59 Cohort 1 monuments in Filter View 6 currently show a LOC Sanborn map plate fallback "
        "from tile.loc.gov in archival_photo_url and also remain eligible for building-photo backfill).\n"
        "   • Click '🗺️ Open in Atlas ↗' (Col AD) or '🕰️ Street View ↗' (Col AE; use Google Maps Historical "
        "Street View clock back to 2007 for Cohort 5 2020-2026 teardowns!) and check Column Z/AA citations.\n"
        "   • Verify candidate scans in Houston Public Library HMRC Digital Archives, Library of Congress "
        "HABS/HAER, Rice University Woodson Research Center (Quartex IIIF), Portal to Texas History "
        "(texashistory.unt.edu), Wikimedia Commons, or HAIF (media.invisioncic.com/w329674/...).\n\n"
        "3. MANDATORY DF-6 DATA-ISOLATION RULE When Staging Lost Houston Photos in Building_Photos:\n"
        "   • Append a new row to the 'Building_Photos' tab (Cols A-K) using these STRICT rules:\n"
        "     - Col A (hcad_num): LEAVE COMPLETELY BLANK! (Never enter the modern parcel's HCAD account, or "
        "the demolished building's photo could be mistaken for the modern replacement tower on that lot.)\n"
        "     - Col B (building_id): Copy the EXACT 'lost_...' ID from Lost_Houston_Catalog Column A "
        "(e.g., 'lost_hermann_building_1917'). Col L will automatically render '🏛️ Open Lost Site in Atlas ↗'.\n"
        "     - Col C (landmark_name): Historic name of the demolished structure.\n"
        "     - Col D (address): LEAVE BLANK (enforces DF-6 address-index isolation from modern buildings on the lot).\n"
        "     - Cols E-K: Fill in photo_year (e.g. 1925), era_label ('c. 1925 Postcard View'), caption, direct "
        "permanent image_url (.jpg/.png/.webp or IIIF default.jpg), thumb_url, credit (archive + photographer), "
        "and source_url (catalog item page).\n\n"
        "4. Non-Destructive Pipeline Sync Commands (Publishing Staged Photos to Map & Refreshing Catalog):\n"
        "   • Export Building_Photos to CSV, then run from houston-building-atlas/pipeline:\n"
        "     uv run python -m atlas_pipeline.sync_lost_houston_sheet \\\n"
        "         --ingest-building-photos-csv /path/to/Building_Photos.csv \\\n"
        "         --write-site-data --include-sop14 --update-hub-row --output-dir /tmp/lost_sync\n"
        "   • Apply the regenerated batches to refresh Lost_Houston_Catalog, Hub Row 18, SOP 14, and Filter View counts:\n"
        "     gsheets mutate batch <SHEET_ID> -f /tmp/lost_sync/lost_houston_values_batch.json\n"
        "     gsheets mutate raw-batch <SHEET_ID> -f /tmp/lost_sync/lost_houston_style_raw_batch.json\n"
        "   • IMPORTANT: NEVER run standalone 'seed_building_photos.py' to sync Lost Houston photos (that legacy "
        "bootstrap script only contains the 33 initial seed photos and would overwrite HAIF, Landmark, and "
        "Lost Houston entries in building_photos.json).\n\n"
        "5. Adding Newly Researched Lost Landmarks or Correcting Footprints / Lifespans:\n"
        "   • Add curated monuments in pipeline/atlas_pipeline/lost_houston_expansion.py (or "
        "build_lost_houston_and_sanborn.py; sheet-ingested photos tagged 'curation_source: building_photos_sheet' "
        "are preserved automatically), verify against ZeroExtantCollisionGate (<=14m point & >20% "
        "polygon area guards so extant landmarks are never falsely marked demolished), rebuild scorecards "
        "(build_neighborhood_scorecards.py), re-run sync_lost_houston_sheet.py with "
        "'--ingest-building-photos-csv <Building_Photos.csv> --write-site-data --include-sop14 --update-hub-row', "
        "and run 'node tests/unit/run_all.mjs'."
    )
    col_c = (
        "Monthly / Ongoing Archival Curation\n"
        f"Tabs: Lost_Houston_Catalog (gid={SID_LOST}) & Building_Photos (gid={SID_PHOTOS})\n"
        "Script: pipeline/atlas_pipeline/sync_lost_houston_sheet.py"
    )
    return [col_a, col_b, col_c]


def build_lost_houston_catalog_rows(
    overlays: dict[str, Any],
    building_photos: dict[str, Any],
    *,
    verify_milestone2_counts: bool = False,
) -> tuple[list[list[Any]], list[list[float]], dict[str, Any]]:
    """Build sorted rows for columns A..AC and AG..AH plus validation metrics."""
    lost_features = overlays.get("lost_houston", {}).get("features", [])
    sn_idx = _SpatialOverlayIndex(overlays.get("super_neighborhoods", {}).get("features", []))
    cd_idx = _SpatialOverlayIndex(overlays.get("council_districts", {}).get("features", []))
    holc_idx = _SpatialOverlayIndex(overlays.get("holc_1937_zones", {}).get("features", []))
    hd_idx = _SpatialOverlayIndex(overlays.get("historic_districts", {}).get("features", []))
    herit_idx = _SpatialOverlayIndex(overlays.get("heritage_districts", {}).get("features", []))
    nrhp_idx = _SpatialOverlayIndex(overlays.get("nrhp_districts", {}).get("features", []))

    photos_by_id: dict[str, list[dict[str, Any]]] = {}
    for entry in building_photos.get("photos", []):
        bid = str(entry.get("building_id") or "").strip().lower()
        if bid and (bid.startswith("lost_") or entry.get("is_lost_houston")):
            photos_by_id.setdefault(bid, []).append(entry)

    sortable_records: list[tuple[tuple[Any, ...], list[Any], list[float]]] = []
    cohort_counter: Counter[int] = Counter()
    holc_counter: Counter[str] = Counter()
    photo_site_count = 0
    designated_landmark_count = 0

    for feat in lost_features:
        p = feat["properties"]
        lost_id = sanitize_sheet_text(p["id"])
        raw_cohort = sanitize_sheet_text(p.get("source_cohort"))
        if raw_cohort not in COHORT_SPECS:
            raise ValueError(f"Unknown source_cohort {raw_cohort!r} on {lost_id}")
        cohort_rank, cohort_label = COHORT_SPECS[raw_cohort]
        cohort_counter[cohort_rank] += 1

        lon = round(float(p["lon"]), 6)
        lat = round(float(p["lat"]), 6)
        geom_raw = feat.get("geometry")
        rep_pt = shape(geom_raw).representative_point() if geom_raw else Point(lon, lat)

        sn_hits = sn_idx.matching_props(rep_pt)
        super_nbhd = sanitize_sheet_text(sn_hits[0].get("name")) if sn_hits else ""

        cd_hits = cd_idx.matching_props(rep_pt)
        if cd_hits:
            cp = cd_hits[0]
            letter = sanitize_sheet_text(cp.get("district_letter") or cp.get("district_code"))
            member = sanitize_sheet_text(cp.get("council_member"))
            council_str = f"District {letter}" + (f" ({member})" if member else "")
        else:
            council_str = "Outside COH"

        holc_hits = holc_idx.matching_props(rep_pt)
        if holc_hits:
            hp = holc_hits[0]
            holc_grade = sanitize_sheet_text(hp.get("holc_grade")) or "Unzoned"
            zcode = sanitize_sheet_text(hp.get("zone_code"))
            gshort = sanitize_sheet_text(hp.get("grade_short"))
            atitle = sanitize_sheet_text(hp.get("area_title"))
            holc_zone = f"{zcode} ({gshort}): {atitle}" if gshort else f"{zcode}: {atitle}"
        else:
            holc_grade = "Unzoned"
            holc_zone = ""
        holc_counter[holc_grade] += 1

        districts_hit: list[str] = []
        for hdp in hd_idx.matching_props(rep_pt):
            nm = sanitize_sheet_text(hdp.get("name"))
            if nm and nm not in districts_hit:
                districts_hit.append(nm)
        for hrp in herit_idx.matching_props(rep_pt):
            nm = sanitize_sheet_text(hrp.get("name"))
            if nm and nm not in districts_hit:
                districts_hit.append(nm)
        for ndp in nrhp_idx.matching_props(rep_pt):
            nm = sanitize_sheet_text(ndp.get("name"))
            tag = f"NRHP: {nm}" if nm else ""
            if tag and tag not in districts_hit:
                districts_hit.append(tag)
        hist_district_str = "; ".join(districts_hit)

        desig_str = format_designation_id(p)
        if "COH #" in desig_str or "NRHP #" in desig_str:
            designated_landmark_count += 1

        name = sanitize_sheet_text(p.get("name") or p.get("building_name"))
        addr = sanitize_sheet_text(p.get("address"))
        addr_1915 = sanitize_sheet_text(p.get("historic_address_1915"))
        if addr_1915 and addr_1915.lower() not in addr.lower():
            addr = f"{addr} [1915: {addr_1915}]" if addr else f"1915: {addr_1915}"

        yb_raw = p.get("year_built")
        yd_raw = p.get("year_demolished")
        ly_raw = p.get("lifespan_years")
        year_built: int | str = int(yb_raw) if isinstance(yb_raw, (int, float)) and int(yb_raw) > 0 else ""
        year_demo: int | str = int(yd_raw) if isinstance(yd_raw, (int, float)) and int(yd_raw) > 0 else ""
        lifespan_yrs: int | str = int(ly_raw) if isinstance(ly_raw, (int, float)) and int(ly_raw) >= 0 else ""

        architect = sanitize_sheet_text(p.get("architect"))
        builder = sanitize_sheet_text(p.get("builder"))
        if builder and builder.lower() not in architect.lower():
            arch_builder = f"{architect}; Builder: {builder}" if architect else f"Builder: {builder}"
        else:
            arch_builder = architect

        stories_raw = p.get("stories")
        stories_val: int | float | str = ""
        if isinstance(stories_raw, int) and stories_raw > 0:
            stories_val = stories_raw
        elif isinstance(stories_raw, float) and stories_raw > 0:
            stories_val = int(stories_raw) if stories_raw.is_integer() else round(stories_raw, 1)

        raw_hcads = p.get("site_hcad_nums") or []
        clean_hcads: list[str] = []
        for h in raw_hcads:
            hs = str(h).strip()
            if hs and hs not in clean_hcads:
                clean_hcads.append(hs)
        hcad_single = str(p.get("hcad_num") or "").strip()
        if hcad_single and hcad_single not in clean_hcads:
            clean_hcads.append(hcad_single)
        site_hcads_str = "; ".join(clean_hcads)

        citations = p.get("citations") or []
        cite_labels = [sanitize_sheet_text(c.get("label")) for c in citations if c.get("label")]
        primary_cite = " | ".join(cite_labels) if cite_labels else sanitize_sheet_text(p.get("source"))

        primary_url = sanitize_sheet_text(
            p.get("sanborn_sheet_loc_url")
            or p.get("source_url")
            or (citations[0].get("url") if citations else "")
        )

        curated_list = photos_by_id.get(lost_id.lower(), [])
        photo_url = sanitize_sheet_text(
            p.get("photo_url") or (curated_list[0].get("image_url") if curated_list else "")
        )
        fallback_credit = (
            (curated_list[0].get("attribution") or curated_list[0].get("credit"))
            if curated_list
            else ""
        )
        photo_credit = sanitize_sheet_text(p.get("photo_credit") or fallback_credit)
        if photo_url:
            photo_site_count += 1
            if len(curated_list) > 1:
                photo_credit = f"[{len(curated_list)} curated photos] {photo_credit}".strip()

        symbol_rank = int(p.get("symbol_rank") or 4)
        sort_yb = year_built if isinstance(year_built, int) else 9999
        sort_key = (cohort_rank, symbol_rank, sort_yb, name.lower(), lost_id)

        row_a_ac: list[Any] = [
            lost_id,
            cohort_label,
            name,
            addr,
            sanitize_sheet_text(p.get("lifespan_label")),
            year_built,
            year_demo,
            lifespan_yrs,
            sanitize_sheet_text(p.get("neighborhood")),
            super_nbhd,
            council_str,
            holc_grade,
            holc_zone,
            hist_district_str,
            desig_str,
            sanitize_sheet_text(p.get("category")),
            arch_builder,
            sanitize_sheet_text(p.get("style")),
            sanitize_sheet_text(p.get("sanborn_material_label")),
            stories_val,
            sanitize_sheet_text(p.get("demolition_cause")),
            sanitize_sheet_text(p.get("replaced_by")),
            sanitize_sheet_text(p.get("historic_significance")),
            site_hcads_str,
            sanitize_sheet_text(p.get("footprint_provenance")),
            primary_cite,
            primary_url,
            photo_url,
            photo_credit,
        ]
        sortable_records.append((sort_key, row_a_ac, [lat, lon]))

    sortable_records.sort(key=lambda item: item[0])
    rows_a_ac = [HEADERS_A_TO_AC] + [r[1] for r in sortable_records]
    rows_ag_ah = [HEADERS_AG_TO_AH] + [r[2] for r in sortable_records]

    total_sites = len(sortable_records)
    metrics = {
        "total_sites": total_sites,
        "cohort_counts": dict(sorted(cohort_counter.items())),
        "designated_landmark_count": designated_landmark_count,
        "holc_counts": dict(sorted(holc_counter.items())),
        "holc_zoned_total": sum(v for k, v in holc_counter.items() if k != "Unzoned"),
        "photo_site_count": photo_site_count,
        "unphotographed_site_count": total_sites - photo_site_count,
        "total_curated_photos": sum(len(v) for v in photos_by_id.values()),
    }
    _assert_catalog_invariants(
        rows_a_ac,
        rows_ag_ah,
        metrics,
        verify_milestone2_counts=verify_milestone2_counts,
    )
    return rows_a_ac, rows_ag_ah, metrics


def _assert_catalog_invariants(
    rows_a_ac: list[list[Any]],
    rows_ag_ah: list[list[float]],
    metrics: dict[str, Any],
    *,
    verify_milestone2_counts: bool = False,
) -> None:
    if len(rows_a_ac) != len(rows_ag_ah):
        raise AssertionError("Row count mismatch between A..AC and AG..AH slices")
    if metrics["total_sites"] <= 0:
        raise AssertionError("Expected at least 1 Lost Houston site")
    if sum(metrics["cohort_counts"].values()) != metrics["total_sites"]:
        raise AssertionError("Cohort count sum does not match total_sites")
    if metrics["photo_site_count"] + metrics["unphotographed_site_count"] != metrics["total_sites"]:
        raise AssertionError("Photographed + unphotographed site counts do not sum to total_sites")

    if verify_milestone2_counts:
        if metrics["total_sites"] != 622:
            raise AssertionError(f"Expected 622 Lost Houston sites, got {metrics['total_sites']}")
        expected_cohorts = {1: 85, 2: 7, 3: 15, 4: 205, 5: 310}
        if metrics["cohort_counts"] != expected_cohorts:
            raise AssertionError(f"Cohort mismatch: {metrics['cohort_counts']} != {expected_cohorts}")
        if metrics["designated_landmark_count"] != 23:
            raise AssertionError(
                f"Expected 23 designated COH/NRHP landmarks, got {metrics['designated_landmark_count']}"
            )
        if metrics["holc_zoned_total"] != 393 or metrics["holc_counts"].get("D") != 168:
            raise AssertionError(f"HOLC spatial join mismatch: {metrics['holc_counts']}")
        if metrics["photo_site_count"] != 133:
            raise AssertionError(f"Expected 133 photographed sites, got {metrics['photo_site_count']}")

    expected_width = len(HEADERS_A_TO_AC)
    hcad_col_offset = COL_IDX["site_hcad_nums"]
    seen_ids: set[str] = set()
    for r_idx, row in enumerate(rows_a_ac[1:], start=2):
        if len(row) != expected_width:
            raise AssertionError(f"Row {r_idx} has {len(row)} columns, expected {expected_width}")
        lid = row[0]
        if not lid or lid in seen_ids:
            raise AssertionError(f"Duplicate or empty lost_id at row {r_idx}: {lid!r}")
        seen_ids.add(lid)
        for cell in row:
            if isinstance(cell, str) and ("\u2014" in cell or "\u2013" in cell):
                raise AssertionError(f"Typographic dash leaked into row {r_idx}: {cell!r}")
        hcad_cell = row[hcad_col_offset]
        if hcad_cell:
            for tok in hcad_cell.split("; "):
                if len(tok) != 13 or not tok.isdigit():
                    raise AssertionError(f"Malformed 13-digit site_hcad_nums token at row {r_idx}: {tok!r}")


def _build_sop14_format_requests() -> list[dict[str, Any]]:
    """Build idempotent `repeatCell` formatting for `Instructions_&_Apps_Script!A25:C25`."""
    r0 = SOP14_TARGET_ROW - 1
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


def build_create_raw_batch(
    include_add_sheet: bool,
    include_hub_insert: bool,
    *,
    include_sop14: bool = False,
    total_rows: int = MIN_SHEET_GRID_ROWS,
) -> dict[str, Any]:
    """Build one-time structural DDL BatchUpdateSpreadsheetRequest payload.

    WARNING: `include_add_sheet` and `include_hub_insert` are non-idempotent one-time
    provisioning operations; verify tab absence and `Admin_Hub_&_Quick_Filter!A18`
    pre-state before executing. Note that `build_style_raw_batch(..., include_sop14=True)`
    also emits the idempotent `SOP 14` formatting so routine re-syncs never depend on
    running this one-time create batch.
    """
    requests: list[dict[str, Any]] = []
    if include_add_sheet:
        requests.append(
            {
                "addSheet": {
                    "properties": {
                        "sheetId": SID_LOST,
                        "title": SHEET_TITLE_LOST,
                        "gridProperties": {
                            "rowCount": compute_grid_row_count(total_rows),
                            "columnCount": len(ALL_HEADERS),
                            "frozenRowCount": 1,
                            "frozenColumnCount": 3,
                        },
                        "tabColor": rgb("#d97706"),
                    }
                }
            }
        )
    if include_hub_insert:
        requests.append(
            {
                "insertDimension": {
                    "range": {
                        "sheetId": SID_HUB,
                        "dimension": "ROWS",
                        "startIndex": 17,
                        "endIndex": 18,
                    },
                    "inheritFromBefore": True,
                }
            }
        )
    if include_sop14:
        requests.extend(_build_sop14_format_requests())
    return {"requests": requests}


def build_values_batch(
    rows_a_ac: list[list[Any]],
    rows_ag_ah: list[list[float]],
    include_hub_row: bool,
    *,
    include_sop14: bool = False,
    metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build `gsheets mutate batch` operations for idempotent catalog sync.

    1. Clears `Lost_Houston_Catalog!A1:AH` first so stray manual edits in helper
       columns (`AD2:AF`) cannot block `ARRAYFORMULA` expansion with `#REF!`, and
       stale rows below `end_row` cannot survive shrinkage or deduplication.
    2. Writes `A1:AC` and `AG1:AH` as disjoint `RAW` slices so leading-zero 13-digit
       HCAD strings (`001...`) and `<=` lifespan strings stay literal text while numeric
       years/coordinates stay JSON numbers.
    3. Writes single-cell `USER_ENTERED` `ARRAYFORMULA` headers into `AD1:AF1`.
    4. When `include_sop14=True`, writes SOP 14 onto `Instructions_&_Apps_Script!A25:C25`
       (never touching Rows `1..24` or Passkey Cell `B6`) and upgrades `Building_Photos!L1`
       so staged `lost_*` rows with blank `Col A` receive one-click Atlas deep links.
    """
    end_row = len(rows_a_ac)
    total_sites = metrics["total_sites"] if metrics else max(0, end_row - 1)

    last_raw_col = col_letter(len(HEADERS_A_TO_AC) - 1)
    first_formula_col = col_letter(COL_IDX["open_in_atlas"])
    last_formula_col = col_letter(COL_IDX["hcad_parcel"])
    lat_col = col_letter(COL_IDX["lat"])
    lon_col = col_letter(COL_IDX["lon"])
    id_col = col_letter(COL_IDX["lost_id"])
    hcad_col = col_letter(COL_IDX["site_hcad_nums"])

    formula_headers_ad_af = [
        [
            f'={{"{HEADERS_AD_TO_AF[0]}"; ARRAYFORMULA(IF(LEN({id_col}2:{id_col})=0, , HYPERLINK("'
            + ATLAS_BASE_URL
            + f'#lat=" & {lat_col}2:{lat_col} & "&lng=" & {lon_col}2:{lon_col} '
            + f'& "&z=18&lost_houston=1&bnd=lost_houston:" & {id_col}2:{id_col}, '
            '"🗺️ Open in Atlas ↗")))}',
            f'={{"{HEADERS_AD_TO_AF[1]}"; ARRAYFORMULA(IF(LEN({id_col}2:{id_col})=0, , HYPERLINK('
            f'"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=" & {lat_col}2:{lat_col} & "," & {lon_col}2:{lon_col}, '
            '"🕰️ Street View ↗")))}',
            f'={{"{HEADERS_AD_TO_AF[2]}"; ARRAYFORMULA(IF(LEN({hcad_col}2:{hcad_col})=0, , HYPERLINK('
            f'"https://arcweb.hcad.org/parcel-viewer-v2.0/?hcad_num=" & LEFT({hcad_col}2:{hcad_col}, 13), '
            '"🏛️ HCAD Parcel ↗")))}',
        ]
    ]

    ops: list[dict[str, Any]] = [
        {
            "op": "clear",
            "range": f"{SHEET_TITLE_LOST}!A1:{lon_col}",
        },
        {
            "op": "write",
            "range": f"{SHEET_TITLE_LOST}!A1:{last_raw_col}{end_row}",
            "data": rows_a_ac,
            "value_input": "RAW",
        },
        {
            "op": "write",
            "range": f"{SHEET_TITLE_LOST}!{lat_col}1:{lon_col}{end_row}",
            "data": rows_ag_ah,
            "value_input": "RAW",
        },
        {
            "op": "write",
            "range": f"{SHEET_TITLE_LOST}!{first_formula_col}1:{last_formula_col}1",
            "data": formula_headers_ad_af,
            "value_input": "USER_ENTERED",
        },
    ]

    if include_hub_row:
        ops.append(
            {
                "op": "write",
                "range": "Admin_Hub_&_Quick_Filter!A18:I18",
                "data": [
                    [
                        "🏛️ Lost Houston Demolished Buildings Catalog (Read-Only)",
                        f"=COUNTA(IFERROR({SHEET_TITLE_LOST}!A2:A))",
                        "Read-only reference catalog",
                        (
                            f"In {SHEET_TITLE_LOST} -> Data -> Filter views "
                            f"({FILTER_VIEW_COUNT} pre-built cohort, HOLC & photo-backfill views)"
                        ),
                        f'=HYPERLINK("#gid={SID_LOST}", "Open {SHEET_TITLE_LOST} ↗")',
                        "",
                        "Lost Houston Layer (Live 3D Atlas)",
                        f'=HYPERLINK("{ATLAS_BASE_URL}#lat=29.75950&lng=-95.36320&z=15.0&lost_houston=1", "🏛️ Open Lost Houston Map View ↗")',
                        f"{total_sites} demolished landmarks, 1915 Red Book sites & 2020-2026 pre-1940 teardowns",
                    ]
                ],
                "value_input": "USER_ENTERED",
            }
        )

    if include_sop14:
        if not metrics:
            raise ValueError("metrics dict is required when include_sop14=True")
        sop_row = build_sop14_row(metrics)
        for cell in sop_row:
            if "\u2014" in cell or "\u2013" in cell:
                raise AssertionError(f"Typographic dash leaked into SOP 14 text: {cell!r}")
        ops.extend(
            [
                {
                    "op": "write",
                    "range": f"Instructions_&_Apps_Script!A{SOP14_TARGET_ROW}:C{SOP14_TARGET_ROW}",
                    "data": [sop_row],
                    "value_input": "RAW",
                },
                {
                    "op": "write",
                    "range": "Building_Photos!L1",
                    "data": [[BUILDING_PHOTOS_OPEN_IN_ATLAS_FORMULA]],
                    "value_input": "USER_ENTERED",
                },
            ]
        )
    return ops


def build_style_raw_batch(
    total_rows: int,
    metrics: dict[str, Any] | None = None,
    *,
    initial_setup: bool = False,
    add_filter_view_offsets: set[int] | None = None,
    include_sop14: bool = False,
) -> dict[str, Any]:
    """Build styling, filter, and protection BatchUpdateSpreadsheetRequest payload.

    When `initial_setup=False` (default recurring sync), emits only idempotent operations:
    cell/column styling, grid row capacity growth, `setBasicFilter`, and `updateFilterView`
    referencing deterministic `filterViewId`s (`SID_LOST + 1`..`SID_LOST + 7`).
    Pass `add_filter_view_offsets={7}` when migrating an existing tab that lacks a newly
    introduced filter view so that offset creates via `addFilterView` while existing views
    use `updateFilterView`. When `include_sop14=True`, also emits idempotent `repeatCell`
    styling for `Instructions_&_Apps_Script!A25:C25`.
    When `initial_setup=True`, also adds conditional format rules, creates all 7 Filter
    Views via `addFilterView`, and attaches the `warningOnly` protected range.
    """
    extra_add_offsets = set(add_filter_view_offsets or ())
    invalid_offsets = sorted(o for o in extra_add_offsets if not (1 <= o <= FILTER_VIEW_COUNT))
    if invalid_offsets:
        raise ValueError(
            f"add_filter_view_offsets must be within 1..{FILTER_VIEW_COUNT}, got {invalid_offsets}"
        )

    c_charcoal = rgb("#272727")
    c_sage = rgb("#95c959")
    total_cols = len(ALL_HEADERS)
    grid_rows = compute_grid_row_count(total_rows)

    m = metrics or {}
    cohorts = m.get("cohort_counts") or {}
    desig_count = m.get("designated_landmark_count", cohorts.get(2, 0) + cohorts.get(3, 0))
    c1_count = cohorts.get(1, 0)
    c4_count = cohorts.get(4, 0)
    c5_count = cohorts.get(5, 0)
    holc_d_count = (m.get("holc_counts") or {}).get("D", 0)
    photo_count = m.get("photo_site_count", 0)
    unphoto_count = m.get("unphotographed_site_count", max(0, total_rows - 1 - photo_count))

    def col_width_req(start_col: int, end_col: int, px: int) -> dict[str, Any]:
        return {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": SID_LOST,
                    "dimension": "COLUMNS",
                    "startIndex": start_col,
                    "endIndex": end_col,
                },
                "properties": {"pixelSize": px},
                "fields": "pixelSize",
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
                            "sheetId": SID_LOST,
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

    def filter_view_req(offset: int, filter_body: dict[str, Any]) -> dict[str, Any]:
        body = {"filterViewId": SID_LOST + offset, **filter_body}
        if initial_setup or offset in extra_add_offsets:
            return {"addFilterView": {"filter": body}}
        return {
            "updateFilterView": {
                "filter": body,
                "fields": "title,range,criteria,sortSpecs",
            }
        }

    col_widths_px = [
        (COL_IDX["lost_id"], COL_IDX["lost_id"] + 1, 210),
        (COL_IDX["cohort_tier"], COL_IDX["cohort_tier"] + 1, 235),
        (COL_IDX["name"], COL_IDX["name"] + 1, 270),
        (COL_IDX["address"], COL_IDX["address"] + 1, 240),
        (COL_IDX["lifespan_label"], COL_IDX["lifespan_label"] + 1, 130),
        (COL_IDX["year_built"], COL_IDX["lifespan_years"] + 1, 95),
        (COL_IDX["neighborhood"], COL_IDX["super_neighborhood"] + 1, 155),
        (COL_IDX["council_district"], COL_IDX["council_district"] + 1, 200),
        (COL_IDX["holc_1937_grade"], COL_IDX["holc_1937_grade"] + 1, 110),
        (COL_IDX["holc_1937_zone"], COL_IDX["holc_1937_zone"] + 1, 245),
        (COL_IDX["historic_district"], COL_IDX["historic_district"] + 1, 220),
        (COL_IDX["designation_or_record_id"], COL_IDX["designation_or_record_id"] + 1, 220),
        (COL_IDX["category"], COL_IDX["category"] + 1, 190),
        (COL_IDX["architect_or_builder"], COL_IDX["architectural_style"] + 1, 215),
        (COL_IDX["sanborn_material"], COL_IDX["sanborn_material"] + 1, 210),
        (COL_IDX["stories"], COL_IDX["stories"] + 1, 75),
        (COL_IDX["demolition_cause"], COL_IDX["replaced_by"] + 1, 275),
        (COL_IDX["historic_significance"], COL_IDX["historic_significance"] + 1, 420),
        (COL_IDX["site_hcad_nums"], COL_IDX["site_hcad_nums"] + 1, 155),
        (COL_IDX["footprint_provenance"], COL_IDX["footprint_provenance"] + 1, 185),
        (COL_IDX["primary_source_citation"], COL_IDX["primary_source_citation"] + 1, 320),
        (COL_IDX["primary_source_url"], COL_IDX["archival_photo_url"] + 1, 230),
        (COL_IDX["archival_photo_credit"], COL_IDX["archival_photo_credit"] + 1, 245),
        (COL_IDX["open_in_atlas"], COL_IDX["hcad_parcel"] + 1, 150),
        (COL_IDX["lat"], COL_IDX["lon"] + 1, 95),
    ]

    filter_range = {
        "sheetId": SID_LOST,
        "startRowIndex": 0,
        "endRowIndex": total_rows,
        "startColumnIndex": 0,
        "endColumnIndex": total_cols,
    }

    desig_col_letter = col_letter(COL_IDX["designation_or_record_id"])
    cohort_col = COL_IDX["cohort_tier"]
    holc_grade_col = COL_IDX["holc_1937_grade"]

    requests: list[dict[str, Any]] = [
        # 0. Ensure grid row/column capacity tracks catalog growth on re-syncs
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": SID_LOST,
                    "gridProperties": {
                        "rowCount": grid_rows,
                        "columnCount": total_cols,
                        "frozenRowCount": 1,
                        "frozenColumnCount": 3,
                    },
                },
                "fields": "gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount)",
            }
        },
        # 1. Header styling (#272727 charcoal bg, #95c959 sage bold text)
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_LOST,
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
        # 2. Body formatting: clean font size 10, TOP vertical alignment, CLIP by default
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_LOST,
                    "startRowIndex": 1,
                    "endRowIndex": total_rows,
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
        # 3. Enable WRAP on key narrative columns (name..address and demolition_cause..historic_significance)
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_LOST,
                    "startRowIndex": 1,
                    "endRowIndex": total_rows,
                    "startColumnIndex": COL_IDX["name"],
                    "endColumnIndex": COL_IDX["address"] + 1,
                },
                "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat.wrapStrategy",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_LOST,
                    "startRowIndex": 1,
                    "endRowIndex": total_rows,
                    "startColumnIndex": COL_IDX["demolition_cause"],
                    "endColumnIndex": COL_IDX["historic_significance"] + 1,
                },
                "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat.wrapStrategy",
            }
        },
        # 4. Enforce Plain Text (@) format on lifespan_label & site_hcad_nums across full grid
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_LOST,
                    "startRowIndex": 1,
                    "endRowIndex": grid_rows,
                    "startColumnIndex": COL_IDX["lifespan_label"],
                    "endColumnIndex": COL_IDX["lifespan_label"] + 1,
                },
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT", "pattern": "@"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": SID_LOST,
                    "startRowIndex": 1,
                    "endRowIndex": grid_rows,
                    "startColumnIndex": COL_IDX["site_hcad_nums"],
                    "endColumnIndex": COL_IDX["site_hcad_nums"] + 1,
                },
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT", "pattern": "@"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        },
    ]

    for sc, ec, px in col_widths_px:
        requests.append(col_width_req(sc, ec, px))

    if initial_setup:
        requests.extend(
            [
                cond_rule_req(cohort_col, "TEXT_EQ", COHORT_SPECS["curated_landmark"][1], "#fef3c7", "#92400e", 0),
                cond_rule_req(cohort_col, "TEXT_EQ", COHORT_SPECS["coh_landmark_demolished"][1], "#fee2e2", "#991b1b", 1),
                cond_rule_req(cohort_col, "TEXT_EQ", COHORT_SPECS["nrhp_demolished"][1], "#fce7f3", "#9d174d", 2),
                cond_rule_req(cohort_col, "TEXT_EQ", COHORT_SPECS["red_book_1915_vanished"][1], "#ffedd5", "#9a3412", 3),
                cond_rule_req(cohort_col, "TEXT_EQ", COHORT_SPECS["historic_teardown_2020_2026"][1], "#e2e8f0", "#334155", 4),
                cond_rule_req(holc_grade_col, "TEXT_EQ", "A", "#dcfce7", "#166534", 5),
                cond_rule_req(holc_grade_col, "TEXT_EQ", "B", "#dbeafe", "#1e40af", 6),
                cond_rule_req(holc_grade_col, "TEXT_EQ", "C", "#fef9c3", "#854d0e", 7),
                cond_rule_req(holc_grade_col, "TEXT_EQ", "D", "#fee2e2", "#991b1b", 8),
            ]
        )

    requests.extend(
        [
            {"setBasicFilter": {"filter": {"range": filter_range}}},
            # Filter View 1 filters designation_or_record_id so demolished landmarks retained in
            # Cohort 1 via hierarchical deduplication (such as COH #98LD74) are never omitted.
            filter_view_req(
                1,
                {
                    "title": f"🏛️ 1. Designated COH & NRHP Landmarks Lost ({desig_count} Sites)",
                    "range": filter_range,
                    "criteria": {
                        str(COL_IDX["designation_or_record_id"]): {
                            "condition": {
                                "type": "CUSTOM_FORMULA",
                                "values": [
                                    {
                                        "userEnteredValue": (
                                            f'=REGEXMATCH(${desig_col_letter}2, "(^|; )(COH|NRHP) #")'
                                        )
                                    }
                                ],
                            }
                        }
                    },
                    "sortSpecs": [{"dimensionIndex": COL_IDX["year_demolished"], "sortOrder": "DESCENDING"}],
                },
            ),
            filter_view_req(
                2,
                {
                    "title": f"🏙️ 2. Curated Surveyed Monuments ({c1_count} Sites)",
                    "range": filter_range,
                    "criteria": {
                        str(cohort_col): {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": COHORT_SPECS["curated_landmark"][1]}],
                            }
                        }
                    },
                    "sortSpecs": [{"dimensionIndex": COL_IDX["year_built"], "sortOrder": "ASCENDING"}],
                },
            ),
            filter_view_req(
                3,
                {
                    "title": f"📕 3. 1915 Red Book Vanished Black Houston Sites ({c4_count} Sites)",
                    "range": filter_range,
                    "criteria": {
                        str(cohort_col): {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": COHORT_SPECS["red_book_1915_vanished"][1]}],
                            }
                        }
                    },
                    "sortSpecs": [
                        {"dimensionIndex": COL_IDX["neighborhood"], "sortOrder": "ASCENDING"},
                        {"dimensionIndex": COL_IDX["name"], "sortOrder": "ASCENDING"},
                    ],
                },
            ),
            filter_view_req(
                4,
                {
                    "title": f"🚧 4. Recent Pre-1940 Teardowns 2020-2026 ({c5_count} Sites)",
                    "range": filter_range,
                    "criteria": {
                        str(cohort_col): {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": COHORT_SPECS["historic_teardown_2020_2026"][1]}],
                            }
                        }
                    },
                    "sortSpecs": [
                        {"dimensionIndex": COL_IDX["year_demolished"], "sortOrder": "DESCENDING"},
                        {"dimensionIndex": COL_IDX["year_built"], "sortOrder": "ASCENDING"},
                    ],
                },
            ),
            filter_view_req(
                5,
                {
                    "title": f"🟥 5. Inside 1937 HOLC Grade D Redlined Zones ({holc_d_count} Sites)",
                    "range": filter_range,
                    "criteria": {
                        str(holc_grade_col): {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": "D"}],
                            }
                        }
                    },
                    "sortSpecs": [
                        {"dimensionIndex": COL_IDX["holc_1937_zone"], "sortOrder": "ASCENDING"},
                        {"dimensionIndex": COL_IDX["year_built"], "sortOrder": "ASCENDING"},
                    ],
                },
            ),
            filter_view_req(
                6,
                {
                    "title": f"📷 6. Sites with Curated Archival Photos ({photo_count} Sites)",
                    "range": filter_range,
                    "criteria": {
                        str(COL_IDX["archival_photo_url"]): {"condition": {"type": "NOT_BLANK"}}
                    },
                    "sortSpecs": [
                        {"dimensionIndex": cohort_col, "sortOrder": "ASCENDING"},
                        {"dimensionIndex": COL_IDX["year_built"], "sortOrder": "ASCENDING"},
                    ],
                },
            ),
            filter_view_req(
                7,
                {
                    "title": f"🖼️ 7. Unphotographed Sites - Archival Photo Backfill Queue ({unphoto_count} Sites)",
                    "range": filter_range,
                    "criteria": {
                        str(COL_IDX["archival_photo_url"]): {"condition": {"type": "BLANK"}}
                    },
                    "sortSpecs": [
                        {"dimensionIndex": cohort_col, "sortOrder": "ASCENDING"},
                        {"dimensionIndex": COL_IDX["year_built"], "sortOrder": "ASCENDING"},
                    ],
                },
            ),
        ]
    )

    if include_sop14:
        requests.extend(_build_sop14_format_requests())

    if initial_setup:
        requests.append(
            {
                "addProtectedRange": {
                    "protectedRange": {
                        "range": {"sheetId": SID_LOST},
                        "description": (
                            "Read-only reference catalog generated by "
                            "pipeline/atlas_pipeline/sync_lost_houston_sheet.py. "
                            "Edits made directly in this tab will be overwritten on next pipeline sync."
                        ),
                        "warningOnly": True,
                    }
                }
            }
        )

    return {"requests": requests}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build Google Sheets sync batches for Lost_Houston_Catalog.")
    parser.add_argument("--overlays", type=Path, default=DEFAULT_OVERLAYS_PATH)
    parser.add_argument("--photos", type=Path, default=DEFAULT_PHOTOS_PATH)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--ingest-building-photos-csv",
        type=Path,
        default=None,
        help="Optional Building_Photos CSV export; non-destructively merges lost_* rows with DF-6 isolation",
    )
    parser.add_argument(
        "--write-site-data",
        action="store_true",
        help="Write merged building_photos.json and overlays.json back to disk when --ingest-building-photos-csv adds rows",
    )
    parser.add_argument(
        "--include-add-sheet",
        action="store_true",
        help="ONE-TIME ONLY: emit addSheet, conditional format rules, addFilterView, and addProtectedRange",
    )
    parser.add_argument(
        "--include-hub-insert",
        action="store_true",
        help="ONE-TIME ONLY: insert row 18 on Admin_Hub_&_Quick_Filter and populate Lost Houston telemetry",
    )
    parser.add_argument(
        "--update-hub-row",
        action="store_true",
        help="Idempotently rewrite Admin_Hub_&_Quick_Filter!A18:I18 without inserting a new row",
    )
    parser.add_argument(
        "--add-filter-view-offsets",
        type=int,
        nargs="*",
        default=[],
        help="Filter view offsets (1..7) to create via addFilterView when upgrading an existing tab",
    )
    parser.add_argument(
        "--include-sop14",
        action="store_true",
        help="Write SOP 14 manual row to Instructions_&_Apps_Script!A25:C25 and upgrade Building_Photos!L1",
    )
    parser.add_argument(
        "--verify-milestone2-counts",
        action="store_true",
        help="Assert exact Milestone-2 baseline counts (622 sites, 393 HOLC, 133 photographed)",
    )
    args = parser.parse_args(argv)
    if args.write_site_data and not args.ingest_building_photos_csv:
        parser.error("--write-site-data requires --ingest-building-photos-csv")

    overlays = json.loads(args.overlays.read_text(encoding="utf-8"))
    building_photos = json.loads(args.photos.read_text(encoding="utf-8"))

    if args.ingest_building_photos_csv:
        ingest_stats = ingest_lost_houston_sheet_photos(
            overlays,
            building_photos,
            args.ingest_building_photos_csv,
        )
        print("Ingested Building_Photos CSV:", json.dumps(ingest_stats))
        if args.write_site_data and (
            ingest_stats["added_photos"] > 0 or ingest_stats["backfilled_features"] > 0
        ):
            write_compact_json(args.photos, building_photos)
            write_compact_json(args.overlays, overlays)
            print(f"Updated {args.photos} and {args.overlays}")

    rows_a_ac, rows_ag_ah, metrics = build_lost_houston_catalog_rows(
        overlays,
        building_photos,
        verify_milestone2_counts=args.verify_milestone2_counts,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)

    create_path = args.output_dir / "lost_houston_create_raw_batch.json"
    values_path = args.output_dir / "lost_houston_values_batch.json"
    style_path = args.output_dir / "lost_houston_style_raw_batch.json"

    create_payload = build_create_raw_batch(
        include_add_sheet=args.include_add_sheet,
        include_hub_insert=args.include_hub_insert,
        include_sop14=args.include_sop14,
        total_rows=len(rows_a_ac),
    )
    values_payload = build_values_batch(
        rows_a_ac=rows_a_ac,
        rows_ag_ah=rows_ag_ah,
        include_hub_row=args.include_hub_insert or args.update_hub_row,
        include_sop14=args.include_sop14,
        metrics=metrics,
    )
    style_payload = build_style_raw_batch(
        total_rows=len(rows_a_ac),
        metrics=metrics,
        initial_setup=args.include_add_sheet,
        add_filter_view_offsets=set(args.add_filter_view_offsets),
        include_sop14=args.include_sop14,
    )

    create_path.write_text(json.dumps(create_payload, indent=2), encoding="utf-8")
    values_path.write_text(json.dumps(values_payload, indent=2), encoding="utf-8")
    style_path.write_text(json.dumps(style_payload, indent=2), encoding="utf-8")

    print("Lost_Houston_Catalog payload summary:")
    print(json.dumps(metrics, indent=2))
    print(f"Wrote {create_path} ({len(create_payload['requests'])} requests)")
    print(f"Wrote {values_path} ({len(values_payload)} ops)")
    print(f"Wrote {style_path} ({len(style_payload['requests'])} requests)")


if __name__ == "__main__":
    main()
