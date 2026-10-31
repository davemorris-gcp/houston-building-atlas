"""Synchronize approved Google Sheet edits with `curated_overrides.json` and `buildings.geojson`.

This script supports two complementary zero-cost workflows:
1. Live Runtime Sync (Zero-Rebuild): The web app fetches the published Google Sheet
   `Approved_Edits` CSV directly in the browser on page load (`curatedEdits.js`).
2. Repository Snapshot Sync (Optional CI / CLI): Staff or a scheduled GitHub Action
   can run this script to bake approved Google Sheet CSV rows (and their footprints
   from `buildings.geojson`) into `app/public/data/curated_overrides.json` and
   `app/public/data/buildings.geojson`.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atlas_pipeline.schema import (
    FIELD_SURVEY_SOURCE_TYPE,
    MIN_VALID_YEAR,
    OVERRIDE_MODE_ADDITIVE_POLYGON,
    OVERRIDE_MODE_SHARD_PRIMARY,
    OVERRIDE_MODE_SUPPRESS_VACANT,
    classify_override_footprint_mode,
    compute_decade,
    extract_embedded_geometry_json,
    is_demolished_vacant_marker,
    normalize_override_record,
    normalize_standing_override_year,
    strip_embedded_field_survey_tag,
    strip_embedded_geometry_tag,
    validate_and_normalize_override_polygon,
    write_compact_json,
)

_SKIP_SOURCE_TYPES = frozenset({
    "PLAT_MONITOR_REVIEW",
    "PROPERTY_WATCHLIST_UPDATE",
    FIELD_SURVEY_SOURCE_TYPE,
    "BUILDING_FLAG",
    "DATA_FLAG",
    "PHOTO_MISATTRIBUTION_FLAG",
    "CLAIM_DISPUTE_FLAG",
    "NARRATIVE_ERROR_FLAG",
})
_SKIP_ID_PREFIXES = ("PLAT_", "WATCH_", "FLAG_", "NARRATIVE_", "SURV_")
_GEOMETRY_ONLY_FALLBACK_SOURCES = frozenset({
    "",
    "SATELLITE / AERIAL IMAGERY",
    "AERIAL / SATELLITE IMAGERY",
    "SATELLITE / AERIAL IMAGERY VERIFICATION",
    "SATELLITE / GIS FOOTPRINT EDIT",
    "STAFF POLYGON EDITOR",
    "PRESERVATION HOUSTON STAFF POLYGON EDITOR",
    "HCAD PARCEL GIS",
    "[FOOTPRINT FIX NEEDED]",
})


def normalize_hcad(raw: Any) -> str:
    return "".join(ch for ch in str(raw or "") if ch.isalnum())


def parse_override_target(norm_row: dict[str, str]) -> tuple[str, str, bool]:
    """Parse canonical (override_key, clean_13digit_hcad_num, is_sub_building) from a CSV row."""
    raw_bld = str(norm_row.get("building_id") or "").strip()
    raw_hcad = str(
        norm_row.get("hcad_num")
        or norm_row.get("hcad_account")
        or norm_row.get("account")
        or norm_row.get("acct")
        or ""
    ).strip()
    raw_id_field = raw_bld if "#" in raw_bld else (raw_hcad if "#" in raw_hcad else (raw_bld or raw_hcad))
    if not raw_id_field or raw_id_field.startswith(_SKIP_ID_PREFIXES):
        return ("", "", False)

    hcad_part, sep, slug_part = raw_id_field.partition("#")
    raw_digits = "".join(ch for ch in hcad_part if ch.isdigit())
    if not raw_digits and raw_hcad:
        raw_digits = "".join(ch for ch in raw_hcad.partition("#")[0] if ch.isdigit())
    if not raw_digits:
        return ("", "", False)

    hcad_num = raw_digits.zfill(13) if len(raw_digits) < 13 else raw_digits
    slug = re.sub(r"[^A-Za-z0-9_#-]", "", slug_part.strip())
    if sep and slug:
        return (f"{hcad_num}#{slug}", hcad_num, True)
    return (hcad_num, hcad_num, False)


def _resolve_row_geometry(norm_row: dict[str, str]) -> dict[str, Any] | None:
    """Resolve explicit `geometry_geojson` cell first, then balanced `[GEOMETRY_GEOJSON:{...}]` tag (DF-2)."""
    raw_geom_cell = str(norm_row.get("geometry_geojson") or "").strip()
    if raw_geom_cell:
        try:
            cand_obj = json.loads(raw_geom_cell)
        except json.JSONDecodeError:
            cand_obj = None
        if cand_obj is not None:
            res = validate_and_normalize_override_polygon(cand_obj)
            if res["ok"]:
                return res["geometry"]
    return extract_embedded_geometry_json(norm_row.get("source_citation", ""))


def merge_override_record(
    prev: dict[str, Any] | None,
    incoming: dict[str, Any],
    ov_key: str = "",
) -> dict[str, Any]:
    """Fold an incoming approved edit row onto a prior override record left-to-right (DF-1, DF-2).

    Guarantees:
      - Later metadata-only rows preserve earlier custom `geometry` (and keep additive `#aux_<N>`
        disposition `keep_shard_footprints=False`).
      - Later geometry-only rows (with missing/unknown `year_built` and `suppress_only=False`)
        preserve prior standing `year_built`, `decade`, Tier 1/2 `source_type`, and architectural
        metadata without coercing `year_built` to `0`.
      - Vacant suppression rows (`suppress_only=True`) strip active `geometry` and zero out years;
        revival rows (`suppress_only=False` with standing year or geometry) clear suppression.
    """
    prior = dict(prev or {})
    key = str(ov_key or incoming.get("override_key") or incoming.get("id") or prior.get("id") or "").strip()
    is_sub = "#" in key or bool(incoming.get("is_building_override")) or bool(prior.get("is_building_override"))

    if bool(incoming.get("suppress_only")):
        merged = dict(prior)
        for k, v in incoming.items():
            if k == "override_key" or v in ("", None):
                continue
            merged[k] = v
        merged["suppress_only"] = True
        merged["year_built"] = 0
        merged["decade"] = 0
        merged["keep_shard_footprints"] = False
        merged.pop("geometry", None)
        if is_sub:
            merged["building_id"] = key
            merged["is_building_override"] = True
        return normalize_override_record(key, merged)

    inc_year = normalize_standing_override_year(incoming.get("year_built"))
    prior_year = normalize_standing_override_year(prior.get("year_built"))
    was_suppressed = bool(prior.get("suppress_only"))
    is_geom_only = inc_year == 0 and isinstance(incoming.get("geometry"), dict)

    merged = dict(prior)
    for k, v in incoming.items():
        if k in ("override_key", "year_built", "decade", "keep_shard_footprints", "replace_parcel_shards"):
            continue
        if v in ("", None):
            continue
        if k == "source_type" and is_geom_only and prior_year >= MIN_VALID_YEAR and not was_suppressed:
            prior_src = str(prior.get("source_type") or "").strip()
            inc_src_upper = str(v).strip().upper()
            if prior_src and (
                inc_src_upper in _GEOMETRY_ONLY_FALLBACK_SOURCES
                or inc_src_upper.startswith("FOOTPRINT ISSUE:")
            ):
                continue
        if k == "source_citation" and is_geom_only and prior_year >= MIN_VALID_YEAR and not was_suppressed:
            prior_cit = strip_embedded_geometry_tag(prior.get("source_citation", ""))
            inc_cit = strip_embedded_geometry_tag(str(v))
            if prior_cit and inc_cit and inc_cit not in prior_cit:
                merged["source_citation"] = f"{prior_cit} | Footprint note: {inc_cit}"
                continue
            if prior_cit and not inc_cit:
                merged["source_citation"] = prior_cit
                continue
        merged[k] = v

    merged["suppress_only"] = False
    if inc_year >= MIN_VALID_YEAR:
        merged["year_built"] = inc_year
        merged["decade"] = compute_decade(inc_year)
    elif was_suppressed:
        merged.pop("year_built", None)
        merged.pop("decade", None)
    elif prior_year >= MIN_VALID_YEAR:
        merged["year_built"] = prior_year
        merged["decade"] = compute_decade(prior_year)
    else:
        merged.pop("year_built", None)
        merged.pop("decade", None)

    has_geom = isinstance(merged.get("geometry"), dict) and merged["geometry"].get("type") in (
        "Polygon",
        "MultiPolygon",
    )
    explicit_replace = incoming.get("replace_parcel_shards")

    if is_sub:
        merged["building_id"] = key
        merged["is_building_override"] = True
        if has_geom:
            merged["keep_shard_footprints"] = False
            if isinstance(explicit_replace, bool):
                merged["replace_parcel_shards"] = explicit_replace
            else:
                merged["replace_parcel_shards"] = bool(prior.get("replace_parcel_shards", False))
        else:
            merged["keep_shard_footprints"] = True
            merged["replace_parcel_shards"] = False
    else:
        if has_geom:
            merged.pop("keep_shard_footprints", None)
            if isinstance(explicit_replace, bool):
                merged["replace_parcel_shards"] = explicit_replace
        elif merged.get("keep_shard_footprints") is False and was_suppressed:
            merged.pop("keep_shard_footprints", None)

    mode = classify_override_footprint_mode(key, merged)
    if mode == OVERRIDE_MODE_ADDITIVE_POLYGON:
        merged["keep_shard_footprints"] = False
        merged["replace_parcel_shards"] = False
    elif mode == OVERRIDE_MODE_SHARD_PRIMARY and is_sub:
        merged["keep_shard_footprints"] = True
        merged["replace_parcel_shards"] = False
    elif mode == OVERRIDE_MODE_SUPPRESS_VACANT:
        merged["keep_shard_footprints"] = False
        merged.pop("geometry", None)

    return normalize_override_record(key, merged)


def parse_approved_csv_rows(csv_text: str) -> list[dict[str, Any]]:
    """Parse CSV text and return only rows whose status is Approved/Verified."""
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    approved: list[dict[str, Any]] = []
    for row in reader:
        norm_row = {str(k or "").strip().lower().replace(" ", "_"): str(v or "").strip() for k, v in row.items()}
        status = norm_row.get("status", "approved").lower()
        if status and status not in {"approved", "verified", "live", "published", "true", "1"}:
            continue
        raw_src_type = norm_row.get("source_type", "").strip().upper()
        if raw_src_type in _SKIP_SOURCE_TYPES or raw_src_type.endswith("_FLAG"):
            continue
        raw_citation = norm_row.get("source_citation", "")
        if "[FIELD_SURVEY_JSON:" in raw_citation:
            continue

        override_key, hcad_num, is_sub = parse_override_target(norm_row)
        if not hcad_num:
            continue

        resolved_geom = _resolve_row_geometry(norm_row)
        clean_citation = strip_embedded_field_survey_tag(strip_embedded_geometry_tag(raw_citation))

        raw_yr_str = ""
        for yr_col in ("year_built", "verified_year_built", "suggested_year_built", "corrected_year"):
            cand = norm_row.get(yr_col, "")
            if cand != "":
                raw_yr_str = cand
                break

        raw_sup_str = norm_row.get("suppress_only", "").strip().lower()
        explicit_sup: bool | None = (
            True
            if raw_sup_str in {"true", "1", "yes"}
            else False
            if raw_sup_str in {"false", "0", "no"}
            else None
        )
        is_suppress_only = is_demolished_vacant_marker(
            raw_yr_str,
            footprint_issue=norm_row.get("footprint_issue", ""),
            footprint_notes=norm_row.get("footprint_notes") or norm_row.get("notes", ""),
            source_type=norm_row.get("source_type", ""),
            source_citation=clean_citation,
            suppress_only=explicit_sup,
        )

        year_built: int | None
        if is_suppress_only:
            year_built = 0
            resolved_geom = None
        else:
            parsed_yr = normalize_standing_override_year(raw_yr_str)
            if parsed_yr >= MIN_VALID_YEAR:
                year_built = parsed_yr
            elif resolved_geom is not None and (raw_yr_str == "" or raw_yr_str == "0"):
                year_built = None
            else:
                continue

        orig_raw = (
            norm_row.get("original_hcad_year")
            or norm_row.get("hcad_year")
            or norm_row.get("hcad_year_built")
            or norm_row.get("current_year_built")
            or ""
        )
        orig_year = int(orig_raw) if orig_raw.isdigit() else None

        default_src_type = (
            "Satellite / Aerial Imagery"
            if (resolved_geom is not None and year_built is None)
            else "Houston City Directory"
        )
        parsed_rec: dict[str, Any] = {
            "override_key": override_key,
            "id": override_key,
            "hcad_num": hcad_num,
            "address": norm_row.get("address", ""),
            "year_built": year_built,
            "decade": 0 if is_suppress_only else (compute_decade(year_built) if year_built else None),
            "original_hcad_year": orig_year,
            "historic_district": norm_row.get("historic_district", ""),
            "contributing": norm_row.get("contributing", ""),
            "source_type": norm_row.get("source_type") or default_src_type,
            "source_citation": clean_citation,
            "source_url": norm_row.get("source_url", ""),
            "verified_by": norm_row.get("verified_by", "Preservation Houston Archival Review"),
            "verified_date": norm_row.get("verified_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            "notes": norm_row.get("notes", ""),
            "suppress_only": bool(is_suppress_only),
        }
        if resolved_geom is not None:
            parsed_rec["geometry"] = resolved_geom
        raw_replace = norm_row.get("replace_parcel_shards", "").strip().lower()
        if raw_replace in {"true", "1", "yes"}:
            parsed_rec["replace_parcel_shards"] = True
        elif raw_replace in {"false", "0", "no"}:
            parsed_rec["replace_parcel_shards"] = False

        if norm_row.get("building_name"):
            parsed_rec["building_name"] = norm_row["building_name"]
        raw_alt_names = norm_row.get("alt_names", "")
        if raw_alt_names:
            alt_list = [
                part.strip()
                for part in raw_alt_names.replace("|", ";").split(";")
                if part.strip() and part.strip() != parsed_rec.get("building_name")
            ]
            if alt_list:
                parsed_rec["alt_names"] = alt_list
        if norm_row.get("bld_style"):
            parsed_rec["bld_style"] = norm_row["bld_style"]
        if norm_row.get("architect"):
            parsed_rec["architect"] = norm_row["architect"]
        if is_sub:
            parsed_rec["building_id"] = override_key
            parsed_rec["is_building_override"] = True
            if is_suppress_only:
                parsed_rec["keep_shard_footprints"] = False
            elif resolved_geom is not None:
                parsed_rec["keep_shard_footprints"] = False
                parsed_rec.setdefault("replace_parcel_shards", False)
            else:
                parsed_rec["keep_shard_footprints"] = True
                parsed_rec["replace_parcel_shards"] = False
        approved.append(parsed_rec)
    return approved


def fold_approved_csv_overrides(
    csv_text: str,
    base_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fold sequential `Approved_Edits` CSV rows left-to-right onto `base_overrides` (DF-2)."""
    folded: dict[str, dict[str, Any]] = {
        str(k): dict(v) for k, v in (base_overrides or {}).items() if isinstance(v, dict)
    }
    for row in parse_approved_csv_rows(csv_text):
        ov_key = str(row.get("override_key") or row["hcad_num"]).strip()
        folded[ov_key] = merge_override_record(folded.get(ov_key), row, ov_key)
    return folded


def sync_curated_overrides(
    data_dir: Path,
    sheet_csv_url: str | None = None,
    local_csv_path: Path | None = None,
) -> dict[str, int]:
    """Merge approved edits into `curated_overrides.json` and `buildings.geojson`."""
    overrides_path = data_dir / "curated_overrides.json"
    buildings_path = data_dir / "buildings.geojson"

    existing_doc: dict[str, Any] = {
        "version": 1,
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "googleSheetCsvUrl": "",
        "submissionWebhookUrl": "",
        "overrides": {},
    }
    if overrides_path.exists():
        existing_doc = json.loads(overrides_path.read_text(encoding="utf-8"))

    overrides_map: dict[str, dict[str, Any]] = dict(existing_doc.get("overrides", {}))

    csv_text = ""
    if sheet_csv_url:
        existing_doc["googleSheetCsvUrl"] = sheet_csv_url
        req = urllib.request.Request(sheet_csv_url, headers={"User-Agent": "PreservationHouston-Atlas-Sync/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            csv_text = resp.read().decode("utf-8", errors="replace")
    elif local_csv_path and local_csv_path.exists():
        csv_text = local_csv_path.read_text(encoding="utf-8")

    if csv_text:
        overrides_map = fold_approved_csv_overrides(csv_text, overrides_map)

    # Enrich geometry and baseline properties from buildings.geojson if present
    updated_buildings_count = 0
    if buildings_path.exists():
        buildings_fc = json.loads(buildings_path.read_text(encoding="utf-8"))
        kept_features: list[dict[str, Any]] = []
        for feat in buildings_fc.get("features", []):
            props = feat.get("properties", {})
            feat_bid = str(props.get("building_id") or props.get("id") or "").strip()
            hcad = normalize_hcad(props.get("hcad_num"))
            matched_key = ""
            if feat_bid and feat_bid in overrides_map:
                matched_key = feat_bid
            elif hcad and hcad in overrides_map and not overrides_map[hcad].get("is_building_override"):
                matched_key = hcad

            if matched_key:
                ov = overrides_map[matched_key]
                is_sub = "#" in matched_key or bool(ov.get("is_building_override"))
                if not ov.get("original_hcad_year"):
                    ov["original_hcad_year"] = props.get("year_built")
                if not ov.get("address"):
                    ov["address"] = props.get("address", "")
                if not ov.get("id"):
                    ov["id"] = matched_key if is_sub else props.get("id", f"curated_{matched_key}")
                if not ov.get("historic_district"):
                    ov["historic_district"] = props.get("historic_district", "")
                if not ov.get("contributing"):
                    ov["contributing"] = props.get("contributing", "")
                if not ov.get("use_category"):
                    ov["use_category"] = props.get("use_category", "Residential")
                if not ov.get("preservation_status"):
                    ov["preservation_status"] = props.get("preservation_status", "Historic District Contributing")

                if ov.get("suppress_only"):
                    ov.pop("geometry", None)
                    overrides_map[matched_key] = normalize_override_record(matched_key, ov)
                    updated_buildings_count += 1
                    continue

                if not ov.get("keep_shard_footprints") and not ov.get("geometry") and feat.get("geometry"):
                    ov["geometry"] = feat["geometry"]
                elif isinstance(ov.get("geometry"), dict) and ov["geometry"].get("type") == "Polygon":
                    feat["geometry"] = ov["geometry"]

                # Apply override to buildings.geojson feature as well
                ov_norm = normalize_override_record(matched_key, ov)
                resolved_yr = normalize_standing_override_year(ov_norm.get("year_built"))
                if resolved_yr < MIN_VALID_YEAR:
                    fallback_feat_yr = normalize_standing_override_year(props.get("year_built"))
                    if fallback_feat_yr >= MIN_VALID_YEAR:
                        ov_norm["year_built"] = fallback_feat_yr
                        ov_norm["decade"] = compute_decade(fallback_feat_yr)
                        resolved_yr = fallback_feat_yr

                overrides_map[matched_key] = ov_norm
                props["original_hcad_year"] = ov_norm["original_hcad_year"]
                if resolved_yr >= MIN_VALID_YEAR:
                    props["year_built"] = int(resolved_yr)
                    props["decade"] = compute_decade(int(resolved_yr))
                props["is_curated_override"] = True
                props["source_type"] = ov_norm.get("source_type", "Houston City Directory")
                props["source_citation"] = ov_norm.get("source_citation", "")
                props["source_url"] = ov_norm.get("source_url", "")
                props["verified_by"] = ov_norm.get("verified_by", "Preservation Houston Archival Review")
                updated_buildings_count += 1

            kept_features.append(feat)

        buildings_fc["features"] = kept_features
        write_compact_json(buildings_path, buildings_fc)

    for k in list(overrides_map.keys()):
        if isinstance(overrides_map[k], dict):
            overrides_map[k] = normalize_override_record(k, overrides_map[k])

    existing_doc["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing_doc["overrides"] = overrides_map
    write_compact_json(overrides_path, existing_doc)

    return {
        "total_overrides": len(overrides_map),
        "updated_buildings_geojson": updated_buildings_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync approved Google Sheet CSV edits into curated_overrides.json and buildings.geojson."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "app" / "public" / "data",
        help="Path to app/public/data directory.",
    )
    parser.add_argument(
        "--sheet-csv-url",
        type=str,
        default=None,
        help="Optional published Google Sheet CSV URL for the Approved_Edits tab.",
    )
    parser.add_argument(
        "--csv-file",
        type=Path,
        default=None,
        help="Optional local CSV file with Approved_Edits rows.",
    )
    args = parser.parse_args()
    stats = sync_curated_overrides(
        data_dir=args.data_dir,
        sheet_csv_url=args.sheet_csv_url,
        local_csv_path=args.csv_file,
    )
    print(
        f"Synced {stats['total_overrides']} curated override(s) "
        f"({stats['updated_buildings_geojson']} matched in buildings.geojson)."
    )


if __name__ == "__main__":
    main()
