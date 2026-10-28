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
    compute_decade,
    normalize_override_record,
    write_compact_json,
)

_SKIP_SOURCE_TYPES = frozenset({
    "PLAT_MONITOR_REVIEW",
    "PROPERTY_WATCHLIST_UPDATE",
    "BUILDING_FLAG",
    "DATA_FLAG",
})
_SKIP_ID_PREFIXES = ("PLAT_", "WATCH_", "FLAG_", "NARRATIVE_")


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
        if raw_src_type in _SKIP_SOURCE_TYPES:
            continue

        override_key, hcad_num, is_sub = parse_override_target(norm_row)
        if not hcad_num:
            continue
        try:
            raw_yr = (
                norm_row.get("year_built")
                or norm_row.get("verified_year_built")
                or norm_row.get("suggested_year_built")
                or norm_row.get("corrected_year")
                or 0
            )
            year_built = int(round(float(raw_yr)))
        except ValueError:
            continue
        if not (1800 <= year_built <= 2035):
            continue

        orig_raw = (
            norm_row.get("original_hcad_year")
            or norm_row.get("hcad_year")
            or norm_row.get("hcad_year_built")
            or norm_row.get("current_year_built")
            or ""
        )
        orig_year = int(orig_raw) if orig_raw.isdigit() else None

        parsed_rec: dict[str, Any] = {
            "override_key": override_key,
            "id": override_key,
            "hcad_num": hcad_num,
            "address": norm_row.get("address", ""),
            "year_built": year_built,
            "decade": compute_decade(year_built),
            "original_hcad_year": orig_year,
            "historic_district": norm_row.get("historic_district", ""),
            "contributing": norm_row.get("contributing", ""),
            "source_type": norm_row.get("source_type", "Houston City Directory"),
            "source_citation": norm_row.get("source_citation", ""),
            "source_url": norm_row.get("source_url", ""),
            "verified_by": norm_row.get("verified_by", "Preservation Houston Archival Review"),
            "verified_date": norm_row.get("verified_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            "notes": norm_row.get("notes", ""),
        }
        if norm_row.get("building_name"):
            parsed_rec["building_name"] = norm_row["building_name"]
        if norm_row.get("architect"):
            parsed_rec["architect"] = norm_row["architect"]
        if is_sub:
            parsed_rec["building_id"] = override_key
            parsed_rec["is_building_override"] = True
            parsed_rec["keep_shard_footprints"] = True
            parsed_rec["replace_parcel_shards"] = False
        approved.append(parsed_rec)
    return approved


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
        for row in parse_approved_csv_rows(csv_text):
            ov_key = str(row.get("override_key") or row["hcad_num"]).strip()
            clean_row = {k: v for k, v in row.items() if k != "override_key" and v not in ("", None)}
            prev = overrides_map.get(ov_key, {})
            merged = {**prev, **clean_row}
            overrides_map[ov_key] = merged

    # Enrich geometry and baseline properties from buildings.geojson if present
    updated_buildings_count = 0
    if buildings_path.exists():
        buildings_fc = json.loads(buildings_path.read_text(encoding="utf-8"))
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
                if not ov.get("keep_shard_footprints") and not ov.get("geometry") and feat.get("geometry"):
                    ov["geometry"] = feat["geometry"]

                # Apply override to buildings.geojson feature as well
                ov_norm = normalize_override_record(matched_key, ov)
                overrides_map[matched_key] = ov_norm
                props["original_hcad_year"] = ov_norm["original_hcad_year"]
                props["year_built"] = int(ov_norm["year_built"])
                props["decade"] = compute_decade(int(ov_norm["year_built"]))
                props["is_curated_override"] = True
                props["source_type"] = ov_norm.get("source_type", "Houston City Directory")
                props["source_citation"] = ov_norm.get("source_citation", "")
                props["source_url"] = ov_norm.get("source_url", "")
                props["verified_by"] = ov_norm.get("verified_by", "Preservation Houston Archival Review")
                updated_buildings_count += 1

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
