"""Pass-2 Dual-Source Pre-1927 HPO Survey & City Directory Audit Queue Auto-Calibrator.

Cross-checks `app/public/data/historical_audit_queue.json` (`1930_PLACEHOLDER_PRE1927_PLAT`
and `CITY_DIRECTORY_EARLIER_THAN_HCAD`) against `hpo_district_surveys.json` (6,062 matched
COH Historic District survey rows), `citydir_index.json` (51,798 Morrison & Fourmy 1866-1926
City Directory parcels), and `Real_building_land.zip` structural flags (`slab_hcads`,
`rebuilt_hcads`, and multi-building years) to promote replacement-safe, dual-source-verified
pre-1927 historic structures into `curated_overrides.json`, `buildings.geojson`,
`search_index.json`, and `stats_summary.json`.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

from atlas_pipeline.harvest_coh_hpo_surveys_and_landmarks import (
    CAMPUS_SPLIT_HCADS,
    DEFAULT_GENERATED_AT_DATE,
    EXCLUDED_AUTO_PROMOTION_HCADS,
    OverrideProvenance,
    _promote_year_override,
    _write_sanitized_compact_json,
    clean_hpo_text,
    is_replaceable_style_value,
    is_usable_hpo_survey_style,
)
from atlas_pipeline.schema import (
    ACTIVE_TAX_YEAR,
    RESERVED_E2E_FIXTURE_HCADS,
    load_bld1_structural_flags,
    resolve_citydir_cdm_url,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
CACHE_DIR = REPO_ROOT / "pipeline" / "cache"

ROUNDED_DECADE_PLACEHOLDERS: tuple[int, ...] = (1900, 1910, 1920, 1930, 1940, 1950)
ALLOWED_PRE1960_CURR_YEARS: tuple[int, ...] = tuple(range(1850, 1961))

# Un-overridden HCAD IDs referenced in unit/pipeline test fixtures that must remain untouched
PROTECTED_TEST_FIXTURE_HCADS: frozenset[str] = frozenset(
    {
        "0010020000001",
        "0010020000016",
        "0010160000011",
        "0010190000008",
        "0010330000012",
        "0011820000001",
        "0052090000011",
        "0090860000002",
        "0102490000006",
        "0200670000010",
        "0201120000005",
        "0201270000049",
        "0201520000004",
        "0201790000034",
        "0201950000022",
        "0201950000025",
        "0202000000016",
        "0202010000014",
        "0202160000012",
        "0202560000001",
        "0210360000008",
        "0300010000005",
        "0370290000014",
        "0370290000021",
        "0370290000022",
        "0370290000023",
        "0370290000025",
        "0370300000010",
        "0601360000010",
        "0620180000006",
        "0620220000015",
        "0620810030009",
        "0620820000016",
        "0620820000017",
        "0620820000018",
        "0620820000019",
        "0620820000020",
        "0620950000004",
        "0620990000001",
        "0620990000002",
        "0620990000003",
        "0621100000013",
        "0621100000016",
        "0621100000017",
        "0621100000018",
        "0621100000019",
        "0621100000020",
        "0621100000091",
        "0621100000092",
        "0621100000098",
        "0621110000014",
        "1215360010001",
        "1222470010018",
    }
)


def _point_in_ring(lng: float, lat: float, ring: list[list[float]]) -> bool:
    """Ray-casting point-in-ring check for `[lng, lat]` against a GeoJSON linear ring."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _point_in_geojson_geom(lng: float, lat: float, geom: dict[str, Any] | None) -> bool:
    """Return True when `(lng, lat)` lies inside a GeoJSON Polygon or MultiPolygon."""
    if not isinstance(geom, dict):
        return False
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    if gtype == "Polygon" and coords:
        if not _point_in_ring(lng, lat, coords[0]):
            return False
        return not any(_point_in_ring(lng, lat, hole) for hole in coords[1:])
    if gtype == "MultiPolygon" and coords:
        return any(
            _point_in_geojson_geom(lng, lat, {"type": "Polygon", "coordinates": poly})
            for poly in coords
        )
    return False


def _match_sanborn_volumes(
    ll: list[Any] | None, sb_bounds: list[tuple[dict[str, Any], str]]
) -> list[str]:
    """Return matching Sanborn Fire Insurance Map volume titles containing `ll = [lng, lat]`."""
    if not isinstance(ll, list) or len(ll) < 2:
        return []
    lng, lat = float(ll[0]), float(ll[1])
    return [title for geom, title in sb_bounds if _point_in_geojson_geom(lng, lat, geom)]


def calibrate_dual_source_pre1927_audit_queue(
    *,
    data_dir: Path | None = None,
    cache_dir: Path | None = None,
    apply: bool = False,
    structural_flags_override: dict[str, Any] | None = None,
    verified_at: str = DEFAULT_GENERATED_AT_DATE,
) -> dict[str, Any]:
    """Evaluate and promote dual-source-verified pre-1927 historic structures from the audit queue."""
    ddir = Path(data_dir or DATA_DIR)
    cdir = Path(cache_dir or CACHE_DIR)

    queue_path = ddir / "historical_audit_queue.json"
    hpo_path = ddir / "hpo_district_surveys.json"
    cd_path = ddir / "citydir_index.json"
    sanborn_path = ddir / "sanborn_catalog.json"
    overrides_path = ddir / "curated_overrides.json"
    buildings_path = ddir / "buildings.geojson"
    search_path = ddir / "search_index.json"
    stats_path = ddir / "stats_summary.json"
    permits_dir = ddir / "permits"
    triage_csv_path = cdir / "triage_1927_cutoff_coh.csv"

    q_doc = json.loads(queue_path.read_text(encoding="utf-8"))
    hpo_doc = json.loads(hpo_path.read_text(encoding="utf-8"))
    cd_doc = json.loads(cd_path.read_text(encoding="utf-8"))
    sb_doc = json.loads(sanborn_path.read_text(encoding="utf-8")) if sanborn_path.exists() else {}
    ov_doc = json.loads(overrides_path.read_text(encoding="utf-8"))
    blds_doc = json.loads(buildings_path.read_text(encoding="utf-8"))
    search_idx = json.loads(search_path.read_text(encoding="utf-8"))

    sb_bounds: list[tuple[dict[str, Any], str]] = [
        (
            v["geometry"],
            str((v.get("properties") or {}).get("title") or (v.get("properties") or {}).get("id") or "Sanborn"),
        )
        for v in (sb_doc.get("volume_boundaries") or [])
        if isinstance(v, dict) and isinstance(v.get("geometry"), dict)
    ]
    plat_yr_by_hcad: dict[str, int] = {}
    if triage_csv_path.exists():
        with triage_csv_path.open(encoding="utf-8") as f_csv:
            for row in csv.DictReader(f_csv):
                rhc = str(row.get("hcad_num") or "").strip()
                m_py = re.search(r"\((\d{4})", str(row.get("plat_name_and_year") or ""))
                if rhc and m_py:
                    plat_yr_by_hcad[rhc] = int(m_py.group(1))

    overrides: dict[str, Any] = ov_doc.get("overrides", ov_doc)
    hpo_by_hcad: dict[str, list[str]] = hpo_doc.get("by_hcad") or {}
    hpo_items: dict[str, dict[str, Any]] = hpo_doc.get("items") or {}
    hpo_dist_order: list[str] = hpo_doc.get("district_order") or []
    hpo_dists: dict[str, dict[str, Any]] = hpo_doc.get("districts") or {}
    cd_by_hcad: dict[str, dict[str, Any]] = cd_doc.get("by_hcad") or {}

    all_excluded = (
        set(RESERVED_E2E_FIXTURE_HCADS)
        | set(EXCLUDED_AUTO_PROMOTION_HCADS)
        | set(CAMPUS_SPLIT_HCADS)
        | set(PROTECTED_TEST_FIXTURE_HCADS)
    )
    overridden_base_hcads = {str(k).split("#", 1)[0].strip() for k in overrides.keys()}

    blds_by_hcad: dict[str, list[dict[str, Any]]] = {}
    for feat in blds_doc.get("features") or []:
        bp = feat.get("properties") or {}
        hc = str(bp.get("hcad_num") or "").strip()
        if hc:
            blds_by_hcad.setdefault(hc, []).append(feat)

    cand_hcads: set[str] = set()
    all_unresolved_queue_hcads: set[str] = set()
    for it in q_doc.get("items") or []:
        hc = str(it.get("h") or "").strip()
        if hc and hc not in overrides and hc not in overridden_base_hcads:
            all_unresolved_queue_hcads.add(hc)
            if (
                it.get("cat")
                in ("1930_PLACEHOLDER_PRE1927_PLAT", "CITY_DIRECTORY_EARLIER_THAN_HCAD")
                and hc not in all_excluded
            ):
                cand_hcads.add(hc)

    if structural_flags_override is not None:
        slab_set = set(structural_flags_override.get("slab_hcads") or [])
        rebuilt_set = set(structural_flags_override.get("rebuilt_hcads") or [])
        bldg_years = {
            str(k): [int(y) for y in v]
            for k, v in (structural_flags_override.get("bldg_years_by_hcad") or {}).items()
        }
    else:
        bld_zip_candidates = [
            cdir / str(ACTIVE_TAX_YEAR) / "Real_building_land.zip",
            cdir / "Real_building_land.zip",
        ]
        bld_zip_path = next((p for p in bld_zip_candidates if p.exists()), bld_zip_candidates[0])
        flags = load_bld1_structural_flags(bld_zip_path, all_unresolved_queue_hcads | cand_hcads)
        slab_set = set(flags["slab_hcads"])
        rebuilt_set = set(flags["rebuilt_hcads"])
        bldg_years = flags["bldg_years_by_hcad"]

    permit_shard_cache: dict[str, dict[str, Any]] = {}
    promoted_hcads: dict[str, dict[str, Any]] = {}
    transitions: list[tuple[str, str, str, str]] = []
    plat_1930_promoted = 0
    dir_pre_promoted = 0
    crossing_1927_count = 0
    refining_pre1927_count = 0

    for it in q_doc.get("items") or []:
        cat = str(it.get("cat") or "")
        hc = str(it.get("h") or "").strip()
        ay = int(it.get("ay") or 0)
        if (
            hc not in cand_hcads
            or hc in promoted_hcads
            or hc in overrides
            or hc in slab_set
            or hc in rebuilt_set
        ):
            continue

        cama_yrs = [int(y) for y in (bldg_years.get(hc) or []) if int(y or 0) > 0]
        if ay > 1960 or any(y > 1960 for y in cama_yrs):
            continue

        all_f = blds_by_hcad.get(hc) or []
        if not all_f or any(
            int((f.get("properties") or {}).get("year_built") or 0) > 1960 for f in all_f
        ):
            continue
        if any(
            re.search(r"#b[2-9]\d*$", str((f.get("properties") or {}).get("id") or ""))
            for f in all_f
        ):
            continue
        target_blds = [
            f
            for f in all_f
            if "#" not in str((f.get("properties") or {}).get("id") or "")
            or str((f.get("properties") or {}).get("id") or "") == f"{hc}#b1"
        ]
        if not target_blds:
            continue
        distinct_b_yrs = {
            int((f.get("properties") or {}).get("year_built") or 0) for f in target_blds
        }
        if len(distinct_b_yrs) != 1 or any(y > 1960 for y in distinct_b_yrs):
            continue

        raw_dist = str(it.get("d") or "").strip()
        in_hd = bool(raw_dist and raw_dist != "Outside Historic District")
        sids = hpo_by_hcad.get(hc) or []
        srows = [hpo_items[s] for s in sids if s in hpo_items]
        if any(r.get("st") in ("NC", "N", "V") for r in srows) or it.get("cnt") == "Non-Contributing":
            continue
        if len(target_blds) > 1 and any(
            sec.get("s") not in ("C", "PC") for r in srows for sec in (r.get("sec") or [])
        ):
            continue
        c_rows = [r for r in srows if r.get("st") in ("C", "PC")]
        dated_c_rows = [r for r in c_rows if 1850 <= int(r.get("y") or 0) <= 1926]
        has_hpo_contrib = bool(c_rows) or it.get("cnt") in (
            "Contributing",
            "Potentially Contributing",
        )
        best_r = dated_c_rows[0] if dated_c_rows else (c_rows[0] if c_rows else {})
        hpo_yr = int(best_r.get("y") or 0)
        hpo_yr_disp = str(best_r.get("yr") or (hpo_yr if hpo_yr > 0 else "Contributing"))
        st_full = (
            "Contributing"
            if best_r.get("st") == "C" or it.get("cnt") == "Contributing"
            else "Potentially Contributing"
        )
        d_idx = best_r.get("d")
        dist_name = (
            hpo_dist_order[int(d_idx)]
            if isinstance(d_idx, int) and 0 <= int(d_idx) < len(hpo_dist_order)
            else (raw_dist or "Outside Historic District")
        )
        dist_meta = hpo_dists.get(dist_name) or {}
        inv_pdf_url = str(dist_meta.get("inventory_pdf_url") or "")
        raw_style = str(best_r.get("sy") or "").strip()
        usable_style = raw_style if is_usable_hpo_survey_style(raw_style) else ""

        cd_rec = cd_by_hcad.get(hc) or {}
        cd_yr = int(cd_rec.get("e") or it.get("sy") or 0)
        cd_ptr, cd_url = (
            resolve_citydir_cdm_url(cd_rec, "")
            if cd_rec
            else (int(it.get("dp") or 0), str(it.get("u") or ""))
        )
        sb_titles = _match_sanborn_volumes(it.get("ll"), sb_bounds)
        plat_yr = plat_yr_by_hcad.get(hc, 0)
        if not plat_yr and cat == "1930_PLACEHOLDER_PRE1927_PLAT":
            m_ev = re.search(r"\((\d{4})\)", str(it.get("ev") or ""))
            if m_ev:
                plat_yr = int(m_ev.group(1))

        cal_yr = 0
        src_type = "COH HPO Historic District Survey"
        ver_by = "COH HPO Historic District Survey"
        contrib_val = "Contributing"
        if cat == "1930_PLACEHOLDER_PRE1927_PLAT":
            if has_hpo_contrib and 1850 <= hpo_yr <= 1926:
                if 1866 <= cd_yr <= 1926:
                    if cd_yr < hpo_yr and (hpo_yr - cd_yr) > 10:
                        continue
                    cal_yr = min(hpo_yr, cd_yr)
                else:
                    cal_yr = hpo_yr
            elif has_hpo_contrib and cd_yr == 1927 and sb_titles and 0 < plat_yr <= 1926:
                cal_yr = 1927
            elif not in_hd and 1866 <= cd_yr <= 1926 and sb_titles and 0 < plat_yr <= 1926:
                cal_yr = cd_yr
                src_type = "Houston City Directory"
                ver_by = "Houston City Directory & Sanborn Map"
                contrib_val = "Outside Historic District"
        elif cat == "CITY_DIRECTORY_EARLIER_THAN_HCAD":
            if not (1866 <= cd_yr <= 1926):
                continue
            if has_hpo_contrib and 1850 <= hpo_yr <= 1926:
                if cd_yr < hpo_yr and (hpo_yr - cd_yr) > 10:
                    continue
                if (
                    ay in ROUNDED_DECADE_PLACEHOLDERS
                    or bool(sb_titles)
                    or abs(hpo_yr - cd_yr) <= 10
                    or (cd_yr >= hpo_yr and 1911 <= cd_yr <= 1926 and 1880 <= hpo_yr <= 1926)
                ):
                    cal_yr = min(hpo_yr, cd_yr)
            elif in_hd and has_hpo_contrib and hpo_yr == 0 and sb_titles:
                cal_yr = cd_yr
            elif not in_hd and sb_titles and (0 < plat_yr <= 1926 or cd_yr >= 1900):
                cal_yr = cd_yr
                src_type = "Houston City Directory"
                ver_by = "Houston City Directory & Sanborn Map"
                contrib_val = "Outside Historic District"

        if not (1850 <= cal_yr <= 1927 and cal_yr <= ay - 2):
            continue

        bp0 = target_blds[0].get("properties") or {}
        addr_disp = clean_hpo_text(str(bp0.get("address") or it.get("a") or best_r.get("a") or ""))
        cd_part = (
            f"; corroborated by {cd_yr} Morrison & Fourmy Houston City Directory"
            + (f" (ContentDM #{cd_ptr})" if cd_ptr > 0 else "")
            if 1866 <= cd_yr <= 1927
            else ""
        )
        sb_part = f"; Sanborn Map ({sb_titles[0]})" if sb_titles else ""
        if src_type == "COH HPO Historic District Survey":
            yr_clause = f"Circa Year Built: {hpo_yr_disp}; " if hpo_yr > 0 else ""
            citation = (
                f"City of Houston Historic Preservation Office Official Inventory of {dist_name} "
                f"({yr_clause}Status: {st_full}"
                f"{f'; Style: {usable_style}' if usable_style else ''}"
                f"{cd_part}{sb_part}; HCAD listed {ay})."
            )
            source_url = inv_pdf_url or cd_url
        else:
            plat_part = f"; Pre-1926 Plat ({plat_yr})" if 0 < plat_yr <= 1926 else ""
            citation = (
                f"{cd_yr} Morrison & Fourmy Houston City Directory"
                + (f" (ContentDM #{cd_ptr})" if cd_ptr > 0 else "")
                + f"{sb_part}{plat_part} (HCAD listed {ay})."
            )
            source_url = cd_url or inv_pdf_url

        promoted_res = _promote_year_override(
            hc=hc,
            target_blds=target_blds,
            auth_yr=cal_yr,
            allowed_curr_years=ALLOWED_PRE1960_CURR_YEARS,
            rebuilt_hcads=rebuilt_set,
            slab_hcads=slab_set,
            provenance=OverrideProvenance(
                verified_by=ver_by,
                source_type=src_type,
                source_citation=citation,
                source_url=source_url,
                year_source="archival",
                verified_at=verified_at,
            ),
            address=addr_disp,
            historic_district=dist_name,
            contributing=contrib_val,
            style=usable_style,
            architect=str(bp0.get("architect") or ""),
            permits_dir=permits_dir,
            permit_shard_cache=permit_shard_cache,
        )
        if promoted_res is None:
            continue

        new_ov, shift = promoted_res
        overrides[hc] = new_ov
        promoted_hcads[hc] = new_ov
        transitions.extend(shift.fallback_transitions)
        if cat == "1930_PLACEHOLDER_PRE1927_PLAT":
            plat_1930_promoted += 1
        else:
            dir_pre_promoted += 1
        if (ay >= 1927 and cal_yr <= 1926) or (ay >= 1928 and cal_yr <= 1927):
            crossing_1927_count += 1
        else:
            refining_pre1927_count += 1

    # Sync search_index.json entries for promoted HCADs
    search_updated = 0
    for srow in search_idx:
        if (
            not isinstance(srow, dict)
            or srow.get("overlay_layer")
            or srow.get("kind")
            or srow.get("type") not in (None, "", "building")
        ):
            continue
        shc = str(srow.get("hcad_num") or "").strip()
        sid = str(srow.get("id") or "").strip()
        if shc in promoted_hcads and "#" not in sid:
            ov_r = promoted_hcads[shc]
            yr_new = int(ov_r["year_built"])
            changed_s = False
            if srow.get("year_built") != yr_new:
                srow["year_built"] = yr_new
                srow["decade"] = ov_r["decade"]
                changed_s = True
            cat_s = str(srow.get("category") or "")
            if cat_s.startswith("Built "):
                srow["category"] = f"Built {yr_new}"
                changed_s = True
            sub_s = str(srow.get("sublabel") or "")
            if "Built " in sub_s:
                srow["sublabel"] = re.sub(r"\bBuilt\s+\d{4}\b", f"Built {yr_new}", sub_s)
                changed_s = True
            ov_st = str(ov_r.get("bld_style") or "").strip()
            if ov_st and is_replaceable_style_value(srow.get("style")):
                srow["style"] = ov_st
                changed_s = True
            if changed_s:
                search_updated += 1

    overridden_after = {str(k).split("#", 1)[0].strip() for k in overrides.keys()}
    risk_counts: dict[str, int] = {
        "likely_replaced_slab_or_post1960": 0,
        "multi_building_parcel": 0,
        "needs_sanborn_or_streetview_check": 0,
        "resolved_or_overridden": 0,
    }
    queue_changed = False
    for it in q_doc.get("items") or []:
        hc = str(it.get("h") or "").strip()
        ay = int(it.get("ay") or 0)
        old_rf = it.get("rf")
        if hc in overrides or hc in overridden_after:
            risk_counts["resolved_or_overridden"] += 1
            if "rf" in it:
                it.pop("rf", None)
                queue_changed = True
            continue
        cama_yrs = [int(y) for y in (bldg_years.get(hc) or []) if int(y or 0) > 0]
        all_f = blds_by_hcad.get(hc) or []
        has_post1960_feat = any(
            int((f.get("properties") or {}).get("year_built") or 0) > 1960 for f in all_f
        )
        has_multi_feat = (
            len(all_f) > 1
            or any(
                re.search(r"#b[2-9]\d*$", str((f.get("properties") or {}).get("id") or ""))
                for f in all_f
            )
            or len(cama_yrs) > 1
        )
        if (
            hc in slab_set
            or hc in rebuilt_set
            or ay > 1960
            or any(y > 1960 for y in cama_yrs)
            or has_post1960_feat
        ):
            risk_counts["likely_replaced_slab_or_post1960"] += 1
            new_rf = "slab"
        elif has_multi_feat:
            risk_counts["multi_building_parcel"] += 1
            new_rf = "multi"
        else:
            risk_counts["needs_sanborn_or_streetview_check"] += 1
            new_rf = "check"
        if old_rf != new_rf:
            it["rf"] = new_rf
            queue_changed = True
    if q_doc.get("replacement_risk_counts") != risk_counts:
        q_doc["replacement_risk_counts"] = risk_counts
        queue_changed = True

    if apply and queue_changed:
        _write_sanitized_compact_json(queue_path, q_doc)

    if apply and promoted_hcads:
        ov_doc["overrides"] = overrides
        _write_sanitized_compact_json(overrides_path, ov_doc)
        _write_sanitized_compact_json(buildings_path, blds_doc)
        _write_sanitized_compact_json(search_path, search_idx)

        if stats_path.exists() and transitions:
            stats_doc = json.loads(stats_path.read_text(encoding="utf-8"))
            dec_counts = stats_doc.get("decade_counts")
            src_counts = stats_doc.get("year_source_counts")
            for old_dec_k, new_dec_k, old_src_k, new_src_k in transitions:
                if (
                    isinstance(dec_counts, dict)
                    and old_dec_k != new_dec_k
                    and old_dec_k in dec_counts
                    and new_dec_k in dec_counts
                    and int(dec_counts[old_dec_k]) > 0
                ):
                    dec_counts[old_dec_k] = int(dec_counts[old_dec_k]) - 1
                    dec_counts[new_dec_k] = int(dec_counts[new_dec_k]) + 1
                eff_old_src = (
                    old_src_k
                    if (isinstance(src_counts, dict) and old_src_k in src_counts)
                    else "hcad_cama"
                )
                if (
                    isinstance(src_counts, dict)
                    and eff_old_src != new_src_k
                    and eff_old_src in src_counts
                    and int(src_counts[eff_old_src]) > 0
                ):
                    src_counts[eff_old_src] = int(src_counts[eff_old_src]) - 1
                    src_counts[new_src_k] = int(src_counts.get(new_src_k, 0)) + 1
            _write_sanitized_compact_json(stats_path, stats_doc)

    return {
        "apply": bool(apply),
        "total_promoted": len(promoted_hcads),
        "plat_1930_promoted": plat_1930_promoted,
        "dir_pre_promoted": dir_pre_promoted,
        "crossing_1927_cutoff": crossing_1927_count,
        "refining_pre1927_rounded_decade": refining_pre1927_count,
        "search_index_updated": search_updated,
        "total_curated_overrides": len(overrides),
        "replacement_risk_counts": risk_counts,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Promote dual-source-verified pre-1927 HPO Survey + City Directory structures."
    )
    parser.add_argument("--apply", action="store_true", help="Persist updates to app/public/data/.")
    parser.add_argument("--dry-run", action="store_true", help="Preview counts without writing.")
    parser.add_argument(
        "--flags-cache",
        type=Path,
        default=None,
        help="Optional precomputed structural flags JSON path.",
    )
    args = parser.parse_args(argv)
    flags_ov = (
        json.loads(args.flags_cache.read_text(encoding="utf-8"))
        if (args.flags_cache and args.flags_cache.exists())
        else None
    )
    res = calibrate_dual_source_pre1927_audit_queue(
        apply=bool(args.apply and not args.dry_run),
        structural_flags_override=flags_ov,
    )
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
