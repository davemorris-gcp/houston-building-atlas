"""Phase 1 Non-Destructive Monitoring Migration Compiler & Workspace Provisioner.

Compiles legacy `PLAT_REV:*` (`281` cycle-keyed Plat Reviews) and `WATCH_PROP:*` (`97`
Watchlist records) alongside Atlas historical overlays into normalized 1-row-per-key
`Areas`, `Parcels`, `Decisions`, `Events`, `Watchlist_Dossiers`, and isolated
`Atlas_Monitoring_Staff_Only` records with 100% pre-write conservation accounting.

Legacy `Plat_&_Watchlist_Sync` CSV deserialization is isolated strictly inside this
one-time/idempotent migration module so runtime production modules carry zero dead
`plat_sync` shims.
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
import urllib.request
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
try:
    from .parcel_monitoring_model import (
        MONITORING_SCHEMA_CONTRACT,
        build_campus_parcel_index,
        classify_protection_level,
        compact_history_json_fifo,
        compact_watchlist_dossier_json,
        compute_attention_level,
        compute_contributing_status,
        compute_threat_stage_from_events,
        derive_legacy_tier_from_parcel,
        detect_parcel_signal_flags,
        evaluate_historical_baseline_event,
        map_agency_or_permit_to_threat_stage,
        migrate_legacy_tier_to_parcel_fields,
        mint_synthetic_unparceled_id,
        normalize_hcad13,
        normalize_parcel_record,
        parse_delimited_list,
        parse_related_hcads,
        resolve_canonical_parcel_id,
        resolve_effective_area_context,
        sanitize_monitoring_text,
        to_chicago_calendar_date,
    )
except ImportError:
    if str(REPO_ROOT / "pipeline") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "pipeline"))
    from atlas_pipeline.parcel_monitoring_model import (
        MONITORING_SCHEMA_CONTRACT,
        build_campus_parcel_index,
        classify_protection_level,
        compact_history_json_fifo,
        compact_watchlist_dossier_json,
        compute_attention_level,
        compute_contributing_status,
        compute_threat_stage_from_events,
        derive_legacy_tier_from_parcel,
        detect_parcel_signal_flags,
        evaluate_historical_baseline_event,
        map_agency_or_permit_to_threat_stage,
        migrate_legacy_tier_to_parcel_fields,
        mint_synthetic_unparceled_id,
        normalize_hcad13,
        normalize_parcel_record,
        parse_delimited_list,
        parse_related_hcads,
        resolve_canonical_parcel_id,
        resolve_effective_area_context,
        sanitize_monitoring_text,
        to_chicago_calendar_date,
    )

BOGUS_DPV_SUBDIV_SEED_RE = re.compile(r"^(?:plat-)?DPV-20\d{2}-\d{3,5}$", re.IGNORECASE)
RETRACTED_ROUNDED_CYCLE_KEY = "2026-10-16"
WATCHLIST_TOMBSTONE_STATUSES = frozenset(
    {"Removed (Tier 3)", "Removed (Tier 4)", "Removed from Watchlist"}
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_BOILERPLATE_WHY_RE = re.compile(
    r"^(?:atlas matched hcad parcel|in a city historic district|outside city limits|first-time plat|street dedication plat|general plan|utility site)",
    re.IGNORECASE,
)


def anonymize_role_label(raw_actor: Any, default_label: str = "Preservation Staff") -> str:
    """Enforce role-only attribution with zero personal staff or collaborator names."""
    s = sanitize_monitoring_text(raw_actor)
    if not s:
        return default_label
    lower = s.lower()
    if "automated" in lower or "auto-sweep" in lower or "pipeline" in lower:
        return "Automated Pipeline"
    if "screener" in lower or "fellow" in lower or "intern" in lower:
        return "Screener"
    if "advocate" in lower:
        return "Preservation Advocate"
    if "community" in lower or "tirz" in lower or "civic" in lower:
        return "Community Partner"
    if _EMAIL_RE.search(s):
        return default_label
    if lower in (
        "preservation staff",
        "preservation advocate",
        "preservation houston",
        "preservation houston archival review",
        "screener",
        "automated pipeline",
    ):
        return s
    return default_label


def scrub_dossier_pii_and_dashes(obj: Any, *, key_hint: str = "") -> Any:
    if isinstance(obj, str):
        cleaned = sanitize_monitoring_text(obj)
        if _EMAIL_RE.search(cleaned):
            cleaned = _EMAIL_RE.sub("Preservation Staff", cleaned)
        if key_hint.lower() in {
            "reviewer",
            "updatedby",
            "reportedby",
            "verified_by",
            "submitted_by",
            "submitter_name",
            "by",
        }:
            return anonymize_role_label(cleaned)
        return cleaned
    if isinstance(obj, list):
        return [scrub_dossier_pii_and_dashes(x, key_hint=key_hint) for x in obj]
    if isinstance(obj, dict):
        return {
            str(k): scrub_dossier_pii_and_dashes(v, key_hint=str(k))
            for k, v in obj.items()
        }
    return obj


def _has_human_dossier_contributions_py(raw_prop: dict[str, Any]) -> bool:
    if raw_prop.get("humanEdited") is True:
        return True
    for txt_key in ("notes", "buildingHistory", "phPastActions"):
        val = sanitize_monitoring_text(raw_prop.get(txt_key) or "")
        if val and not _BOILERPLATE_WHY_RE.match(val):
            return True
    if raw_prop.get("driveLinks") or raw_prop.get("historyLinks") or raw_prop.get("photoUrl"):
        return True
    for row in raw_prop.get("checklist") or []:
        if not isinstance(row, dict):
            continue
        if row.get("humanStatusEdited") is True:
            return True
        for chk in row.get("checks") or []:
            if isinstance(chk, dict):
                src = str(chk.get("source") or "")
                res = str(chk.get("result") or "")
                if "[AUTO-SWEEP]" not in src and "[AUTO-SWEEP]" not in res and res.strip():
                    return True
    return False


def is_legacy_unconfirmed_plat_seed_py(raw_prop: Any) -> bool:
    """1:1 Python mirror of `isLegacyUnconfirmedPlatSeed` excluding machine seed artifacts."""
    if not isinstance(raw_prop, dict):
        return True
    pid = sanitize_monitoring_text(raw_prop.get("id") or "")
    app_ref = sanitize_monitoring_text(
        raw_prop.get("appNo")
        or raw_prop.get("sourcePlatApp")
        or (pid[5:] if pid.startswith("plat-") else "")
    )
    if BOGUS_DPV_SUBDIV_SEED_RE.match(pid) or BOGUS_DPV_SUBDIV_SEED_RE.match(app_ref):
        return True
    tracker_st = sanitize_monitoring_text(raw_prop.get("trackerStatus") or "")
    if raw_prop.get("removedFromWatchlist") is True or tracker_st in WATCHLIST_TOMBSTONE_STATUSES:
        return False
    if not pid.startswith("plat-") and not app_ref:
        return False
    if _has_human_dossier_contributions_py(raw_prop):
        return False
    src_cycle = sanitize_monitoring_text(
        raw_prop.get("sourceCycleKey") or raw_prop.get("cycleKey") or ""
    )
    if src_cycle == RETRACTED_ROUNDED_CYCLE_KEY:
        return True
    if raw_prop.get("tierConfirmed") is not True:
        return True
    updated_by_lower = sanitize_monitoring_text(raw_prop.get("updatedBy") or "").lower()
    if "houston building atlas" in updated_by_lower and raw_prop.get("humanEdited") is not True:
        return True
    addr_upper = re.sub(r"\s+", " ", sanitize_monitoring_text(raw_prop.get("address") or "").upper())
    if app_ref.upper() == "DPV-26073533" and (
        not raw_prop.get("updatedAt") or "2603 MILAM" in addr_upper
    ):
        return True
    if app_ref.upper() == "2026-1585" and (
        not raw_prop.get("updatedAt") or "1226 W CLAY" in addr_upper
    ):
        return True
    return False


def is_legacy_unconfirmed_plat_review_py(cycle_key: str, app_no: str, rev_obj: Any) -> bool:
    if not isinstance(rev_obj, dict):
        return True
    if BOGUS_DPV_SUBDIV_SEED_RE.match(app_no):
        return True
    has_human_fields = bool(rev_obj.get("humanEditedFields")) or any(
        sanitize_monitoring_text(rev_obj.get(k) or "")
        and not _BOILERPLATE_WHY_RE.match(sanitize_monitoring_text(rev_obj.get(k) or ""))
        for k in ("why", "sources", "followup")
    )
    if (
        cycle_key == RETRACTED_ROUNDED_CYCLE_KEY
        and not has_human_fields
        and not rev_obj.get("humanEdited")
        and not rev_obj.get("tierConfirmed")
    ):
        return True
    return False


def parse_legacy_plat_sync_csv(csv_text: str) -> dict[str, dict[str, Any]]:
    """Isolated legacy `Plat_&_Watchlist_Sync` CSV deserializer used strictly for migration."""
    plat_reviews: dict[str, Any] = {}
    watch_properties: dict[str, Any] = {}
    if not csv_text or not csv_text.strip():
        return {"platReviews": plat_reviews, "watchProperties": watch_properties}

    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        norm = {
            sanitize_monitoring_text(k).lower(): sanitize_monitoring_text(v)
            for k, v in row.items()
            if k
        }
        if norm.get("status", "").lower() not in ("", "approved"):
            continue
        key = norm.get("hcad_num") or norm.get("record key (cycle/property)", "")
        cite = norm.get("source_citation") or norm.get(
            "json state payload (synced to plat & property monitor)", ""
        )
        src_type = (norm.get("source_type") or norm.get("record type", "")).upper()
        if not key or not cite.startswith("{"):
            continue
        try:
            parsed = json.loads(cite)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        if src_type == "PLAT_MONITOR_REVIEW" or key.startswith("PLAT_REV:"):
            prev = plat_reviews.get(key)
            prev_ts = str((prev or {}).get("updatedAt") or "")
            cand_ts = str(
                parsed.get("updatedAt") or (parsed.get("review") or {}).get("updated") or ""
            )
            if prev is None or cand_ts >= prev_ts:
                plat_reviews[key] = parsed
        elif src_type == "PROPERTY_WATCHLIST_UPDATE" or key.startswith("WATCH_PROP:"):
            prev_w = watch_properties.get(key)
            prev_wts = str((prev_w or {}).get("updatedAt") or "")
            cand_wts = str(parsed.get("updatedAt") or "")
            if prev_w is None or cand_wts >= prev_wts:
                watch_properties[key] = parsed

    return {"platReviews": plat_reviews, "watchProperties": watch_properties}


def _slug_area(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", sanitize_monitoring_text(name).lower()).strip("_")[:48]


def seed_priority_and_candidate_areas(
    *,
    overlays_data: dict[str, Any] | None = None,
    scorecards_data: dict[str, Any] | None = None,
    hpo_surveys_data: dict[str, Any] | None = None,
    updated_at: str = "",
) -> list[dict[str, Any]]:
    """Deterministically seed the 5 Priority Areas and Candidate Areas from Atlas overlays."""
    ts = updated_at or "2026-11-23T00:00:00Z"
    by_id: dict[str, dict[str, Any]] = {}

    for seed in MONITORING_SCHEMA_CONTRACT["priorityAreaSeeds"]:
        aid = str(seed["area_id"])
        by_id[aid] = {
            "area_id": aid,
            "name": str(seed["name"]),
            "area_type": str(seed["area_type"]),
            "priority_status": "priority",
            "age_rule_cutoff_year": int(seed["age_rule_cutoff_year"]),
            "dev_period_start": "",
            "dev_period_end": "",
            "dev_period_confidence": "",
            "assigned_screeners": "",
            "local_partners": "",
            "notes": "Initial Phase 1 Priority Area where setting and block intactness drive advocacy.",
            "updated_at": ts,
        }

    ov = overlays_data or {}
    local_hd_names = {
        _slug_area(str((f.get("properties") or {}).get("name") or ""))
        for f in (ov.get("historic_districts") or {}).get("features") or []
        if isinstance(f, dict)
    }

    # Candidate source 1: National Register historic districts without local COH Historic District designation
    for feat in (ov.get("nrhp_districts") or {}).get("features") or []:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        nm = sanitize_monitoring_text(props.get("name") or props.get("resname") or "")
        slug = _slug_area(nm)
        if not slug or slug in local_hd_names:
            continue
        aid = f"area:candidate:nrhp:{slug}"
        if aid not in by_id:
            by_id[aid] = {
                "area_id": aid,
                "name": nm,
                "area_type": "neighborhood",
                "priority_status": "candidate",
                "age_rule_cutoff_year": 1976,
                "dev_period_start": props.get("period_start") or "",
                "dev_period_end": props.get("period_end") or "",
                "dev_period_confidence": "high" if props.get("period_end") else "",
                "assigned_screeners": "",
                "local_partners": "",
                "notes": "Candidate Area: National Register Historic District without binding local COH designation.",
                "updated_at": ts,
            }

    # Candidate source 2: HPO district surveys / COH 2019 potential historic districts
    hpo_districts = (hpo_surveys_data or {}).get("districts") or {}
    if isinstance(hpo_districts, dict):
        for d_key, d_meta in sorted(hpo_districts.items()):
            if not isinstance(d_meta, dict):
                continue
            nm = sanitize_monitoring_text(d_meta.get("name") or d_key)
            slug = _slug_area(nm)
            if not slug or slug in local_hd_names:
                continue
            aid = f"area:candidate:hpo_survey:{slug}"
            if aid not in by_id:
                by_id[aid] = {
                    "area_id": aid,
                    "name": nm,
                    "area_type": "historic_subdivision",
                    "priority_status": "candidate",
                    "age_rule_cutoff_year": 1976,
                    "dev_period_start": "",
                    "dev_period_end": "",
                    "dev_period_confidence": "",
                    "assigned_screeners": "",
                    "local_partners": "",
                    "notes": "Candidate Area: HPO architectural survey / potential historic district.",
                    "updated_at": ts,
                }

    # Candidate source 3: 1915 Red Book concentrations (>= 3 documented historic sites)
    red_book_counts: dict[str, tuple[str, int]] = {}
    for feat in (ov.get("red_book_1915") or {}).get("features") or []:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or {}
        ward_or_nb = sanitize_monitoring_text(
            props.get("neighborhood") or props.get("ward") or props.get("area") or ""
        )
        slug = _slug_area(ward_or_nb)
        if not slug:
            continue
        prev_nm, cnt = red_book_counts.get(slug, (ward_or_nb, 0))
        red_book_counts[slug] = (prev_nm, cnt + 1)

    priority_slugs = {_slug_area(s["name"]) for s in MONITORING_SCHEMA_CONTRACT["priorityAreaSeeds"]}
    for slug, (label, cnt) in sorted(red_book_counts.items()):
        if cnt >= 3 and slug not in priority_slugs:
            aid = f"area:candidate:redbook1915:{slug}"
            if aid not in by_id:
                by_id[aid] = {
                    "area_id": aid,
                    "name": label,
                    "area_type": "neighborhood",
                    "priority_status": "candidate",
                    "age_rule_cutoff_year": 1976,
                    "dev_period_start": 1866,
                    "dev_period_end": 1930,
                    "dev_period_confidence": "moderate",
                    "assigned_screeners": "",
                    "local_partners": "",
                    "notes": f"Candidate Area: Concentration of {cnt} documented 1915 Red Book sites.",
                    "updated_at": ts,
                }

    # Candidate source 4: Scorecards neighborhoods with high pre-1940 count and minimal local protection
    sc_records = (scorecards_data or {}).get("boundaries") or (scorecards_data or {}).get("items") or []
    if isinstance(sc_records, list):
        for rec in sc_records:
            if not isinstance(rec, dict):
                continue
            pre1940 = int(rec.get("pre1940Count") or rec.get("pre_1940_count") or 0)
            prot_pct = float(rec.get("protectedSharePct") or rec.get("protected_pct") or 0.0)
            nm = sanitize_monitoring_text(rec.get("name") or "")
            slug = _slug_area(nm)
            if pre1940 >= 250 and prot_pct < 15.0 and slug and slug not in priority_slugs and slug not in local_hd_names:
                aid = f"area:candidate:pre1940:{slug}"
                if aid not in by_id:
                    by_id[aid] = {
                        "area_id": aid,
                        "name": nm,
                        "area_type": "neighborhood",
                        "priority_status": "candidate",
                        "age_rule_cutoff_year": 1976,
                        "dev_period_start": "",
                        "dev_period_end": "",
                        "dev_period_confidence": "",
                        "assigned_screeners": "",
                        "local_partners": "",
                        "notes": f"Candidate Area: {pre1940} pre-1940 structures with low statutory protection.",
                        "updated_at": ts,
                    }

    return list(by_id.values())


def collect_atlas_known_high_parcels(
    *,
    curated_overrides: dict[str, Any] | None = None,
    overlays_data: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Index HCAD parcels already recognized by the Atlas as Landmarks, NRHP, Good Brick, or 1915 Red Book."""
    known: dict[str, dict[str, Any]] = {}
    ov = overlays_data or {}

    def _register(
        raw_hcad: Any,
        label: str,
        *,
        address: str = "",
        name: str = "",
        year_built: Any = "",
    ) -> None:
        hid = normalize_hcad13(raw_hcad)
        if not hid:
            return
        entry = known.setdefault(
            hid,
            {
                "hcad_num": hid,
                "designations": [],
                "primary_address": sanitize_monitoring_text(address),
                "building_name": sanitize_monitoring_text(name),
                "year_built": year_built if year_built else "",
            },
        )
        if label and label not in entry["designations"]:
            entry["designations"].append(label)
        if address and not entry["primary_address"]:
            entry["primary_address"] = sanitize_monitoring_text(address)
        if name and not entry["building_name"]:
            entry["building_name"] = sanitize_monitoring_text(name)
        if year_built and not entry["year_built"]:
            entry["year_built"] = year_built

    layer_labels = [
        ("landmarks", "City Landmark"),
        ("nrhp_properties", "National Register (individual)"),
        ("good_brick_awards", "Good Brick Award"),
        ("red_book_1915", "1915 Red Book Site"),
    ]
    for layer_key, default_label in layer_labels:
        for feat in (ov.get(layer_key) or {}).get("features") or []:
            if not isinstance(feat, dict):
                continue
            p = feat.get("properties") or {}
            lbl = default_label
            if layer_key == "landmarks" and (
                p.get("is_protected")
                or "protected" in str(p.get("designation_type") or p.get("landmark_type") or "").lower()
            ):
                lbl = "Protected Landmark"
            _register(
                p.get("hcad_num") or p.get("hcad"),
                lbl,
                address=str(p.get("address") or ""),
                name=str(p.get("name") or p.get("landmark_name") or p.get("property_name") or ""),
                year_built=p.get("year_built") or p.get("building_year_built") or "",
            )

    ov_map = (
        curated_overrides.get("overrides")
        if isinstance(curated_overrides, dict) and isinstance(curated_overrides.get("overrides"), dict)
        else (curated_overrides if isinstance(curated_overrides, dict) else {})
    )
    for k, rec in (ov_map or {}).items():
        if not isinstance(rec, dict) or rec.get("suppress_only"):
            continue
        hid = normalize_hcad13(rec.get("hcad_num") or str(k).split("#", 1)[0])
        if not hid:
            continue
        contrib = str(rec.get("contributing") or "")
        lm_name = str(rec.get("landmark_name") or rec.get("building_name") or "")
        if "Protected Landmark" in contrib:
            _register(
                hid,
                "Protected Landmark",
                address=str(rec.get("address") or ""),
                name=lm_name,
                year_built=rec.get("year_built") or "",
            )
        elif "Landmark" in contrib or rec.get("landmark_name"):
            _register(
                hid,
                "City Landmark",
                address=str(rec.get("address") or ""),
                name=lm_name,
                year_built=rec.get("year_built") or "",
            )

    return known


def _match_area_ids_by_neighborhood_name(
    neighborhood_text: str, areas: list[dict[str, Any]]
) -> list[str]:
    norm = _slug_area(neighborhood_text)
    if not norm:
        return []
    matched: list[str] = []
    aliases = {
        "greater_fifth_ward": "fifth_ward",
        "greater_third_ward": "third_ward",
        "acres_home": "acres_homes",
    }
    canonical_norm = aliases.get(norm, norm)
    for a in areas:
        a_slug = _slug_area(a.get("name") or "")
        if a_slug and (a_slug == canonical_norm or a_slug in canonical_norm or canonical_norm in a_slug):
            matched.append(str(a["area_id"]))
    return matched


def compile_migration_bundle(
    *,
    legacy_plat_reviews: dict[str, Any] | None = None,
    legacy_watch_properties: dict[str, Any] | None = None,
    plat_agenda_doc: dict[str, Any] | None = None,
    overlays_data: dict[str, Any] | None = None,
    curated_overrides: dict[str, Any] | None = None,
    scorecards_data: dict[str, Any] | None = None,
    hpo_surveys_data: dict[str, Any] | None = None,
    as_of_date_cst: str = "2026-11-23",
    now_iso: str = "2026-11-23T00:00:00Z",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile legacy Plat Reviews & Watchlist records into normalized Phase 1 tables with 100% conservation."""
    areas = seed_priority_and_candidate_areas(
        overlays_data=overlays_data,
        scorecards_data=scorecards_data,
        hpo_surveys_data=hpo_surveys_data,
        updated_at=now_iso,
    )
    areas_by_id = {a["area_id"]: a for a in areas}
    known_high_by_hcad = collect_atlas_known_high_parcels(
        curated_overrides=curated_overrides,
        overlays_data=overlays_data,
    )

    # Index public agenda items by `(cycleKey, appNo)` and `appNo` to enrich HCADs, addresses, and owners
    agenda_item_by_occ: dict[tuple[str, str], dict[str, Any]] = {}
    agenda_item_by_app: dict[str, dict[str, Any]] = {}
    for ck, cyc in sorted(((plat_agenda_doc or {}).get("cycles") or {}).items()):
        if not isinstance(cyc, dict):
            continue
        for it in cyc.get("items") or []:
            if not isinstance(it, dict):
                continue
            app_no = sanitize_monitoring_text(it.get("app") or "")
            if app_no:
                agenda_item_by_occ[(str(ck), app_no)] = it
                agenda_item_by_app[app_no] = it

    raw_watch_map = legacy_watch_properties or {}
    raw_plat_map = legacy_plat_reviews or {}

    input_watch_count = len(raw_watch_map)
    excluded_watch_seed_count = 0
    migrated_watch_tombstone_count = 0
    migrated_active_or_archived_watch_count = 0
    campus_assemblage_count = 0

    parcels_by_id: dict[str, dict[str, Any]] = {}
    decisions_by_parcel: dict[str, dict[str, Any]] = {}
    decision_notes_by_parcel: dict[str, list[str]] = {}
    decision_hist_by_parcel: dict[str, list[dict[str, Any]]] = {}
    events_by_id: dict[str, dict[str, Any]] = {}
    watchlist_dossiers_by_id: dict[str, dict[str, Any]] = {}
    staff_strategy_by_parcel: dict[str, dict[str, Any]] = {}

    # 1. Migrate Watchlist Dossiers FIRST so multi-parcel CAMPUS:<slug> groups and human dossiers establish canonical parcel_ids
    for raw_key, raw_wp_val in sorted(raw_watch_map.items()):
        wp = scrub_dossier_pii_and_dashes(raw_wp_val) if isinstance(raw_wp_val, dict) else {}
        wid = sanitize_monitoring_text(
            wp.get("id") or (raw_key[11:] if str(raw_key).startswith("WATCH_PROP:") else raw_key)
        )
        wp["id"] = wid
        if is_legacy_unconfirmed_plat_seed_py(wp):
            excluded_watch_seed_count += 1
            continue

        tracker_status = sanitize_monitoring_text(wp.get("trackerStatus") or "")
        is_tombstone = (
            wp.get("removedFromWatchlist") is True or tracker_status in WATCHLIST_TOMBSTONE_STATUSES
        )
        if is_tombstone:
            migrated_watch_tombstone_count += 1
        else:
            migrated_active_or_archived_watch_count += 1

        hcads = parse_related_hcads(wp.get("hcadAccount") or wp.get("hcad_num") or "")
        campus_id = ""
        if len(hcads) > 1:
            slug_src = wp.get("propertyName") or wp.get("address") or wid
            campus_slug = re.sub(r"[^A-Z0-9]+", "_", sanitize_monitoring_text(slug_src).upper()).strip("_")[:48]
            campus_id = f"CAMPUS:{campus_slug or hcads[0]}"
            campus_assemblage_count += 1

        parcel_id = resolve_canonical_parcel_id(
            campus_group_id=campus_id,
            hcad_ids=hcads,
            app_no=str(wp.get("appNo") or wp.get("sourcePlatApp") or ""),
            address=str(wp.get("address") or ""),
            name=str(wp.get("propertyName") or wid),
        )
        area_ids = _match_area_ids_by_neighborhood_name(
            str(wp.get("neighborhood") or wp.get("superNeighborhoodMatch") or ""),
            areas,
        )
        area_ctx = resolve_effective_area_context(area_ids, areas_by_id)
        known_high = any(h in known_high_by_hcad for h in hcads)
        known_meta = next((known_high_by_hcad[h] for h in hcads if h in known_high_by_hcad), None)

        desig_str = sanitize_monitoring_text(wp.get("designation") or "")
        if (not desig_str or desig_str.lower() in ("none", "not checked yet")) and known_meta:
            desig_str = "; ".join(known_meta["designations"])

        added_at = sanitize_monitoring_text(
            (wp.get("keyDates") or {}).get("monitoringStarted")
            or wp.get("addedAt")
            or wp.get("updatedAt")
            or now_iso
        )

        # Extract timeline events from checklist checks
        parcel_events: list[dict[str, Any]] = []
        for chk_row in wp.get("checklist") or []:
            if not isinstance(chk_row, dict):
                continue
            chk_id = sanitize_monitoring_text(chk_row.get("id") or "")
            for idx_c, chk in enumerate(chk_row.get("checks") or []):
                if not isinstance(chk, dict):
                    continue
                res_txt = sanitize_monitoring_text(chk.get("result") or "")
                if not res_txt or "no new filings" in res_txt.lower() or "no matching" in res_txt.lower():
                    continue
                adverse = chk.get("adverse") is not False
                stage = map_agency_or_permit_to_threat_stage(
                    source_agency=chk_id,
                    event_kind=str(chk_row.get("title") or chk_id),
                    summary_text=res_txt,
                    adverse=adverse,
                )
                if stage == 0:
                    continue
                f_date = sanitize_monitoring_text(chk.get("date") or "")
                is_hist = bool(chk.get("historicalBaseline") is True) or evaluate_historical_baseline_event(
                    filing_date=f_date,
                    parcel_added_at=added_at,
                    as_of_date_cst=as_of_date_cst,
                )
                ref_tok = sanitize_monitoring_text(
                    chk.get("agencyRecordId") or f"{f_date}_{idx_c}"
                )
                ev_id = f"EVT:{chk_id}:{ref_tok}:{parcel_id}"
                links = chk.get("links") or []
                first_url = (
                    sanitize_monitoring_text(links[0].get("url"))
                    if isinstance(links, list) and links and isinstance(links[0], dict)
                    else ""
                )
                ev_obj = {
                    "event_id": ev_id,
                    "parcel_id": parcel_id,
                    "hcad_num": hcads[0] if hcads else "",
                    "threat_stage": stage,
                    "source_agency": chk_id,
                    "event_kind": sanitize_monitoring_text(chk_row.get("title") or chk_id),
                    "filing_date": to_chicago_calendar_date(f_date) or f_date,
                    "detected_at": sanitize_monitoring_text(
                        chk.get("discoveredAt") or chk.get("loggedAt") or wp.get("updatedAt") or now_iso
                    ),
                    "is_historical_baseline": is_hist,
                    "headline": res_txt[:160],
                    "summary": res_txt[:1000],
                    "party_names": sanitize_monitoring_text(wp.get("developer") or ""),
                    "mismatch_flag": "",
                    "source_url": first_url,
                    "raw_ref_id": ref_tok,
                }
                events_by_id[ev_id] = ev_obj
                parcel_events.append(ev_obj)

        active_stage = compute_threat_stage_from_events(parcel_events)
        migrated_fields = migrate_legacy_tier_to_parcel_fields(
            {
                **wp,
                "designation": desig_str,
                "threatStage": active_stage,
            },
            is_watchlist=True,
            atlas_known_high=known_high,
            area_context=area_ctx,
            default_threat_stage=active_stage,
        )
        sig_flags = detect_parcel_signal_flags(
            owner_of_record=str(wp.get("ownerOfRecord") or ""),
            party_names=[
                wp.get("developer") or "",
                wp.get("buyerRelatedEntities") or "",
            ],
            land_use_class="",
            event_kinds=[e["event_kind"] for e in parcel_events],
        )

        yr_str = str(wp.get("yearBuilt") or (known_meta or {}).get("year_built") or "")[:4]
        yr_val = int(yr_str) if yr_str.isdigit() else ""
        perim_addrs = parse_delimited_list(
            [
                *(wp.get("secondaryAddresses") or []),
                *((wp.get("searchTerms") or {}).get("altAddresses") or []),
            ]
        )

        parcel_rec = normalize_parcel_record(
            {
                "parcel_id": parcel_id,
                "hcad_num": hcads[0] if hcads else "",
                "campus_group_id": campus_id,
                "related_hcads": hcads,
                "primary_address": wp.get("address")
                or (known_meta or {}).get("primary_address")
                or "",
                "perimeter_addresses": perim_addrs,
                "building_name": wp.get("propertyName")
                or (known_meta or {}).get("building_name")
                or "",
                "year_built": yr_val,
                "hcad_year_built": yr_val,
                "subdivision_name": wp.get("subdivisionMatch") or "",
                "area_ids": area_ids,
                "owner_of_record": wp.get("ownerOfRecord") or "",
                "land_use_class": "",
                "designation_summary": desig_str,
                "signal_flags": sig_flags,
                "watch_property_id": wid,
                "added_at": added_at,
                "updated_by": "Preservation Staff",
                "updated_at": sanitize_monitoring_text(wp.get("updatedAt") or now_iso),
                **migrated_fields,
            },
            as_of_date_cst=as_of_date_cst,
            areas_by_id=areas_by_id,
        )
        parcels_by_id[parcel_id] = parcel_rec

        note_text = sanitize_monitoring_text(wp.get("notes") or "")
        if note_text:
            decision_notes_by_parcel.setdefault(parcel_id, []).append(note_text)
        decision_hist_by_parcel.setdefault(parcel_id, []).append(
            {
                "at": sanitize_monitoring_text(wp.get("updatedAt") or now_iso),
                "role": "staff",
                "by": "Preservation Staff",
                "source": f"Watchlist ({wid})",
                "review_status": parcel_rec["review_status"],
                "significance_level": parcel_rec["significance_level"],
                "advocacy_status": parcel_rec["advocacy_status"],
                "outcome": parcel_rec["outcome"],
                "note": note_text[:280],
            }
        )
        decisions_by_parcel[parcel_id] = {
            "decision_id": f"DEC:{parcel_id}",
            "parcel_id": parcel_id,
            "hcad_num": parcel_rec["hcad_num"],
            "latest_app_no": sanitize_monitoring_text(wp.get("appNo") or ""),
            "latest_cycle_key": sanitize_monitoring_text(wp.get("sourceCycleKey") or ""),
            "actor_role": "staff",
            "review_status": parcel_rec["review_status"],
            "recommended_significance": "",
            "significance_level": parcel_rec["significance_level"],
            "significance_basis": "",
            "contributing_override": "",
            "integrity": "",
            "standing_status": "demolished"
            if parcel_rec["outcome"] in ("lost", "lost_before_review")
            else "standing",
            "advocacy_status": parcel_rec["advocacy_status"],
            "outcome": parcel_rec["outcome"],
            "attention_override": "",
            "screening_notes": note_text,
            "story_check_json": "",
            "atlas_writeback_summary": "",
            "decided_by": "Preservation Staff",
            "decided_at": sanitize_monitoring_text(wp.get("updatedAt") or now_iso),
            "history_json": "",
        }

        # Preserve any PH past actions / contact strategy in isolated Staff_Parcel_Strategy
        ph_actions = sanitize_monitoring_text(wp.get("phPastActions") or "")
        if ph_actions:
            staff_strategy_by_parcel[parcel_id] = {
                "parcel_id": parcel_id,
                "hcad_num": parcel_rec["hcad_num"],
                "strategy_notes": ph_actions,
                "contact_notes": "",
                "owner_relationship_notes": "",
                "decision_matrix_json": "",
                "updated_by": "Preservation Staff",
                "updated_at": sanitize_monitoring_text(wp.get("updatedAt") or now_iso),
            }

        # Enrich Watchlist dossier with normalized parcel fields & derived legacy tier
        enriched_wp = {
            **wp,
            "parcelId": parcel_id,
            "reviewStatus": parcel_rec["review_status"],
            "significanceLevel": parcel_rec["significance_level"],
            "contributingStatus": parcel_rec["contributing_status"],
            "protectionLevel": parcel_rec["protection_level"],
            "threatStage": parcel_rec["threat_stage"],
            "advocacyStatus": parcel_rec["advocacy_status"],
            "outcome": parcel_rec["outcome"],
            "attentionLevel": parcel_rec["attention_level"],
            "derivedTier": derive_legacy_tier_from_parcel(parcel_rec) or str(wp.get("tier") or "1"),
            "updatedBy": "Preservation Staff",
        }
        dossier_json_str = compact_watchlist_dossier_json(enriched_wp)

        watchlist_dossiers_by_id[wid] = {
            "watch_property_id": wid,
            "parcel_id": parcel_id,
            "hcad_num": parcel_rec["hcad_num"],
            "primary_address": parcel_rec["primary_address"],
            "property_name": sanitize_monitoring_text(
                wp.get("propertyName") or parcel_rec["primary_address"] or wid
            ),
            "attention_level": parcel_rec["attention_level"],
            "advocacy_status": parcel_rec["advocacy_status"],
            "outcome": parcel_rec["outcome"],
            "dossier_json": dossier_json_str,
            "updated_by": "Preservation Staff",
            "updated_at": sanitize_monitoring_text(wp.get("updatedAt") or now_iso),
        }

    # Build campus index so multi-cycle Plat Reviews on any member HCAD resolve to parent CAMPUS:<slug>
    campus_index = build_campus_parcel_index(list(parcels_by_id.values()))

    # 2. Migrate 281 Plat Reviews (chronological cycle order so latest human Plat Review wins when no Watchlist dossier exists)
    input_plat_count = len(raw_plat_map)
    excluded_plat_seed_count = 0
    migrated_plat_count = 0
    synthetic_unparceled_ids: set[str] = set()

    def _plat_sort_key(kv: tuple[str, Any]) -> tuple[str, str, str]:
        raw_k, raw_v = kv
        parts = str(raw_k).split(":")
        ck = str(
            (raw_v.get("cycleKey") if isinstance(raw_v, dict) else "")
            or (parts[1] if len(parts) >= 3 else "")
        )
        upd = str(
            (raw_v.get("updatedAt") if isinstance(raw_v, dict) else "")
            or ((raw_v.get("review") or {}).get("updated") if isinstance(raw_v, dict) else "")
            or ""
        )
        return (ck, upd, str(raw_k))

    for raw_pkey, raw_pval in sorted(raw_plat_map.items(), key=_plat_sort_key):
        p_entry = scrub_dossier_pii_and_dashes(raw_pval) if isinstance(raw_pval, dict) else {}
        parts = str(raw_pkey).split(":")
        cycle_key = sanitize_monitoring_text(
            p_entry.get("cycleKey") or (parts[1] if len(parts) >= 3 else "")
        )
        app_no = sanitize_monitoring_text(
            p_entry.get("appNo")
            or p_entry.get("app")
            or (":".join(parts[2:]) if len(parts) >= 3 else raw_pkey)
        )
        rev = (
            p_entry.get("review")
            if isinstance(p_entry.get("review"), dict)
            else p_entry
        )
        if is_legacy_unconfirmed_plat_review_py(cycle_key, app_no, rev):
            excluded_plat_seed_count += 1
            continue

        migrated_plat_count += 1
        ag_item = agenda_item_by_occ.get((cycle_key, app_no)) or agenda_item_by_app.get(app_no) or {}
        ag_atlas = ag_item.get("atlas") if isinstance(ag_item.get("atlas"), dict) else {}

        hcads = parse_related_hcads(
            [
                rev.get("hcad") or "",
                ag_item.get("hcad") or "",
                *(
                    [p.get("hcad") for p in (ag_atlas.get("parcels") or []) if isinstance(p, dict)]
                ),
            ]
        )
        primary_addr = sanitize_monitoring_text(
            rev.get("address") or ag_atlas.get("primaryAddress") or ""
        )
        item_name = sanitize_monitoring_text(ag_item.get("name") or app_no)

        parcel_id = resolve_canonical_parcel_id(
            hcad_ids=hcads,
            app_no=app_no,
            address=primary_addr,
            name=item_name,
            campus_index=campus_index,
        )
        if parcel_id.startswith("UNPARCELED:"):
            synthetic_unparceled_ids.add(parcel_id)

        area_ids = _match_area_ids_by_neighborhood_name(
            str(ag_item.get("superN") or ""), areas
        )
        area_ctx = resolve_effective_area_context(area_ids, areas_by_id)
        known_high = any(h in known_high_by_hcad for h in hcads)
        known_meta = next((known_high_by_hcad[h] for h in hcads if h in known_high_by_hcad), None)

        desig_str = sanitize_monitoring_text(
            rev.get("designation")
            or "; ".join(ag_atlas.get("designations") or [])
            or (" ".join(known_meta["designations"]) if known_meta else "")
        )
        if desig_str.lower() in ("not checked yet", "none found"):
            desig_str = "; ".join(known_meta["designations"]) if known_meta else ""

        ev_id = f"EVT:chk-09:{cycle_key}_{app_no}:{parcel_id}"
        ev_date = sanitize_monitoring_text(
            ag_item.get("submittedIso") or cycle_key or as_of_date_cst
        )
        events_by_id[ev_id] = {
            "event_id": ev_id,
            "parcel_id": parcel_id,
            "hcad_num": hcads[0] if hcads else "",
            "threat_stage": 3,
            "source_agency": "chk-09",
            "event_kind": sanitize_monitoring_text(ag_item.get("type") or "Plat Application"),
            "filing_date": to_chicago_calendar_date(ev_date) or ev_date,
            "detected_at": sanitize_monitoring_text(
                p_entry.get("updatedAt") or rev.get("updated") or now_iso
            ),
            "is_historical_baseline": False,
            "headline": f"Planning Commission {app_no}: {item_name}"[:160],
            "summary": sanitize_monitoring_text(
                f"{ag_item.get('type') or ''} {ag_item.get('special') or ''}"
            )[:500],
            "party_names": sanitize_monitoring_text(
                "; ".join(
                    x
                    for x in [ag_item.get("developer") or "", ag_item.get("applicant") or ""]
                    if x
                )
            ),
            "mismatch_flag": "",
            "source_url": sanitize_monitoring_text(
                ag_item.get("pdf") or ag_item.get("noticePdf") or ""
            ),
            "raw_ref_id": app_no,
        }

        # Collect distinct non-boilerplate review notes across all cycles losslessly
        note_parts = []
        for nk in ("why", "sources", "followup"):
            nv = sanitize_monitoring_text(rev.get(nk) or "")
            if nv and not _BOILERPLATE_WHY_RE.match(nv):
                note_parts.append(nv)
        combined_cycle_note = " | ".join(note_parts)
        if combined_cycle_note:
            tagged_note = f"[{cycle_key} {app_no}] {combined_cycle_note}"
            notes_bucket = decision_notes_by_parcel.setdefault(parcel_id, [])
            if combined_cycle_note not in notes_bucket and tagged_note not in notes_bucket:
                notes_bucket.append(tagged_note)

        yr_raw = rev.get("yearBuilt") or ag_atlas.get("earliestYearBuilt") or (known_meta or {}).get("year_built") or ""
        yr_s = str(yr_raw)[:4]
        yr_num = int(yr_s) if yr_s.isdigit() else ""

        migrated_plat_fields = migrate_legacy_tier_to_parcel_fields(
            {
                "tier": rev.get("tier") or "",
                "yearBuilt": yr_num,
                "designation": desig_str,
                "why": combined_cycle_note,
                "threatStage": 3,
            },
            is_watchlist=False,
            atlas_known_high=known_high,
            area_context=area_ctx,
            default_threat_stage=3,
        )

        decision_hist_by_parcel.setdefault(parcel_id, []).append(
            {
                "at": sanitize_monitoring_text(
                    rev.get("updated") or p_entry.get("updatedAt") or now_iso
                ),
                "role": "staff",
                "by": "Preservation Staff",
                "source": f"Plat {cycle_key}::{app_no}",
                "review_status": migrated_plat_fields["review_status"],
                "significance_level": migrated_plat_fields["significance_level"],
                "advocacy_status": migrated_plat_fields["advocacy_status"],
                "outcome": migrated_plat_fields["outcome"],
                "note": combined_cycle_note[:280],
            }
        )

        existing_parcel = parcels_by_id.get(parcel_id)
        has_watchlist_precedence = bool(
            existing_parcel and existing_parcel.get("watch_property_id")
        )
        has_human_tier = sanitize_monitoring_text(rev.get("tier") or "") in ("1", "2", "3", "4")

        if not existing_parcel:
            parcels_by_id[parcel_id] = normalize_parcel_record(
                {
                    "parcel_id": parcel_id,
                    "hcad_num": hcads[0] if hcads else "",
                    "campus_group_id": parcel_id if parcel_id.startswith("CAMPUS:") else "",
                    "related_hcads": hcads,
                    "primary_address": primary_addr or item_name,
                    "perimeter_addresses": [],
                    "building_name": (known_meta or {}).get("building_name") or item_name,
                    "year_built": yr_num,
                    "hcad_year_built": yr_num,
                    "subdivision_name": sanitize_monitoring_text(ag_item.get("prevSub") or ""),
                    "area_ids": area_ids,
                    "owner_of_record": sanitize_monitoring_text(ag_atlas.get("owner") or ""),
                    "land_use_class": sanitize_monitoring_text(ag_item.get("landUse") or ""),
                    "designation_summary": desig_str,
                    "added_at": sanitize_monitoring_text(
                        rev.get("updated") or p_entry.get("updatedAt") or now_iso
                    ),
                    "updated_by": "Preservation Staff",
                    "updated_at": sanitize_monitoring_text(
                        rev.get("updated") or p_entry.get("updatedAt") or now_iso
                    ),
                    **migrated_plat_fields,
                },
                as_of_date_cst=as_of_date_cst,
                areas_by_id=areas_by_id,
            )
        elif not has_watchlist_precedence and (
            has_human_tier or existing_parcel.get("review_status") == "not_reviewed"
        ):
            # Latest human Plat Review updates non-Watchlist parcel fields while keeping Watchlist dossier precedence intact
            parcels_by_id[parcel_id] = normalize_parcel_record(
                {
                    **existing_parcel,
                    "primary_address": primary_addr or existing_parcel["primary_address"],
                    "year_built": yr_num or existing_parcel["year_built"],
                    "designation_summary": desig_str or existing_parcel["designation_summary"],
                    "updated_at": sanitize_monitoring_text(
                        rev.get("updated") or p_entry.get("updatedAt") or existing_parcel["updated_at"]
                    ),
                    **migrated_plat_fields,
                },
                as_of_date_cst=as_of_date_cst,
                areas_by_id=areas_by_id,
            )

        p_after = parcels_by_id[parcel_id]
        prev_dec = decisions_by_parcel.get(parcel_id)
        decisions_by_parcel[parcel_id] = {
            "decision_id": f"DEC:{parcel_id}",
            "parcel_id": parcel_id,
            "hcad_num": p_after["hcad_num"],
            "latest_app_no": app_no,
            "latest_cycle_key": cycle_key,
            "actor_role": "staff",
            "review_status": p_after["review_status"],
            "recommended_significance": "",
            "significance_level": p_after["significance_level"],
            "significance_basis": (prev_dec or {}).get("significance_basis") or "",
            "contributing_override": "",
            "integrity": (prev_dec or {}).get("integrity") or "",
            "standing_status": "demolished"
            if p_after["outcome"] in ("lost", "lost_before_review")
            else "standing",
            "advocacy_status": p_after["advocacy_status"],
            "outcome": p_after["outcome"],
            "attention_override": "",
            "screening_notes": "\n".join(decision_notes_by_parcel.get(parcel_id) or []),
            "story_check_json": "",
            "atlas_writeback_summary": "",
            "decided_by": "Preservation Staff",
            "decided_at": sanitize_monitoring_text(
                rev.get("updated")
                or p_entry.get("updatedAt")
                or (prev_dec or {}).get("decided_at")
                or now_iso
            ),
            "history_json": "",
        }

    # Finalize compacted history_json & uncapped screening_notes across all Decisions
    for pid, dec_row in decisions_by_parcel.items():
        notes_list = decision_notes_by_parcel.get(pid) or []
        if notes_list:
            dec_row["screening_notes"] = "\n".join(notes_list)[:47500]
        dec_row["history_json"] = compact_history_json_fifo(
            decision_hist_by_parcel.get(pid) or []
        )

    # Pre-write conservation assertions: 100% of input records must be accounted for!
    assert (
        excluded_watch_seed_count
        + migrated_watch_tombstone_count
        + migrated_active_or_archived_watch_count
        == input_watch_count
    ), "Watchlist record conservation check failed!"
    assert (
        excluded_plat_seed_count + migrated_plat_count == input_plat_count
    ), "Plat Review record conservation check failed!"

    bundle = {
        "schemaVersion": "1.0",
        "compiledAt": now_iso,
        "areas": sorted(areas, key=lambda r: str(r["area_id"])),
        "parcels": sorted(parcels_by_id.values(), key=lambda r: str(r["parcel_id"])),
        "decisions": sorted(decisions_by_parcel.values(), key=lambda r: str(r["decision_id"])),
        "events": sorted(events_by_id.values(), key=lambda r: str(r["event_id"])),
        "watchlistDossiers": sorted(
            watchlist_dossiers_by_id.values(), key=lambda r: str(r["watch_property_id"])
        ),
        "staffOnly": {
            "parcelStrategy": sorted(
                staff_strategy_by_parcel.values(), key=lambda r: str(r["parcel_id"])
            ),
            "watchedEntities": [],
        },
    }
    report = {
        "inputWatchPropertyCount": input_watch_count,
        "excludedWatchMachineSeedCount": excluded_watch_seed_count,
        "migratedWatchTombstoneCount": migrated_watch_tombstone_count,
        "migratedActiveOrArchivedWatchCount": migrated_active_or_archived_watch_count,
        "inputPlatReviewCount": input_plat_count,
        "excludedPlatMachineSeedCount": excluded_plat_seed_count,
        "migratedPlatReviewCount": migrated_plat_count,
        "syntheticUnparceledCount": len(synthetic_unparceled_ids),
        "campusAssemblageCount": campus_assemblage_count,
        "outputAreaCount": len(bundle["areas"]),
        "outputParcelCount": len(bundle["parcels"]),
        "outputDecisionCount": len(bundle["decisions"]),
        "outputEventCount": len(bundle["events"]),
        "outputWatchlistDossierCount": len(bundle["watchlistDossiers"]),
        "outputStaffStrategyCount": len(bundle["staffOnly"]["parcelStrategy"]),
    }

    serialized_check = json.dumps(bundle, ensure_ascii=False)
    if "\u2014" in serialized_check or "\u2013" in serialized_check:
        raise ValueError("Forbidden typographic dash detected in compiled migration bundle!")

    return bundle, report


def push_migration_bundle_to_workspace(
    bundle: dict[str, Any],
    *,
    webhook_url: str,
    admin_email: str,
    admin_passkey: str = "",
    google_id_token: str = "",
) -> dict[str, Any]:
    """Push a compiled Phase 1 migration bundle idempotently to Google Workspace."""
    core_payload = {
        "action": "pipeline_push_sweep_results",
        "allowFullMigrationSeed": True,
        "admin_email": admin_email,
        "admin_passkey": admin_passkey,
        "google_id_token": google_id_token,
        "areas": bundle.get("areas") or [],
        "parcels": bundle.get("parcels") or [],
        "decisions": bundle.get("decisions") or [],
        "events": bundle.get("events") or [],
        "watchlistDossiers": bundle.get("watchlistDossiers") or [],
    }
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(core_payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "text/plain;charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        core_res = json.loads(resp.read().decode("utf-8"))
    if not core_res.get("ok"):
        raise RuntimeError(f"Monitoring_Core migration push failed: {core_res}")

    staff_only = bundle.get("staffOnly") or {}
    strat_rows = staff_only.get("parcelStrategy") or []
    ent_rows = staff_only.get("watchedEntities") or []
    staff_res: dict[str, Any] = {"ok": True, "skipped": True}
    if strat_rows or ent_rows:
        st_payload = {
            "action": "upsert_staff_only",
            "admin_email": admin_email,
            "admin_passkey": admin_passkey,
            "google_id_token": google_id_token,
            "parcelStrategy": strat_rows,
            "watchedEntities": ent_rows,
        }
        st_req = urllib.request.Request(
            webhook_url,
            data=json.dumps(st_payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "text/plain;charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(st_req, timeout=60) as resp2:
            staff_res = json.loads(resp2.read().decode("utf-8"))

    return {"ok": True, "core": core_res, "staffOnly": staff_res}


def _load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Compile and optionally push Phase 1 normalized monitoring state."
    )
    parser.add_argument(
        "--legacy-backup-json",
        type=Path,
        default=REPO_ROOT / "pipeline" / "backups" / "workspace_cloud_backup.json",
        help="Path to legacy JSON backup containing platReviews and watchProperties.",
    )
    parser.add_argument(
        "--legacy-sync-csv",
        type=Path,
        default=None,
        help="Optional path to legacy Plat_&_Watchlist_Sync CSV export.",
    )
    parser.add_argument(
        "--output-bundle",
        type=Path,
        default=None,
        help="Optional output path (in scratch directory, never public git!) for compiled JSON bundle.",
    )
    parser.add_argument("--push-webhook-url", type=str, default="")
    parser.add_argument("--admin-email", type=str, default="")
    parser.add_argument("--admin-passkey", type=str, default="")
    args = parser.parse_args(argv)

    data_dir = REPO_ROOT / "app" / "public" / "data"
    legacy_doc = _load_json_if_exists(args.legacy_backup_json)
    plat_reviews = dict(legacy_doc.get("platReviews") or {})
    watch_props = dict(legacy_doc.get("watchProperties") or {})

    if args.legacy_sync_csv and args.legacy_sync_csv.exists():
        parsed_csv = parse_legacy_plat_sync_csv(
            args.legacy_sync_csv.read_text(encoding="utf-8")
        )
        plat_reviews.update(parsed_csv["platReviews"])
        watch_props.update(parsed_csv["watchProperties"])

    # Also merge any active dossiers still present in pre-scrub property_watchlist.json
    disk_wl = _load_json_if_exists(data_dir / "property_watchlist.json")
    for wp in disk_wl.get("watchedProperties") or []:
        if isinstance(wp, dict) and wp.get("id"):
            wkey = f"WATCH_PROP:{wp['id']}"
            if wkey not in watch_props:
                watch_props[wkey] = wp

    bundle, report = compile_migration_bundle(
        legacy_plat_reviews=plat_reviews,
        legacy_watch_properties=watch_props,
        plat_agenda_doc=_load_json_if_exists(data_dir / "plat_agenda_monitor.json"),
        overlays_data=_load_json_if_exists(data_dir / "overlays.json"),
        curated_overrides=_load_json_if_exists(data_dir / "curated_overrides.json"),
        scorecards_data=_load_json_if_exists(data_dir / "neighborhood_scorecards.json"),
        hpo_surveys_data=_load_json_if_exists(data_dir / "hpo_district_surveys.json"),
        as_of_date_cst=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        now_iso=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    print("Phase 1 Migration Reconciliation Report:", json.dumps(report, indent=2))
    if args.output_bundle:
        args.output_bundle.parent.mkdir(parents=True, exist_ok=True)
        args.output_bundle.write_text(
            json.dumps({"report": report, "bundle": bundle}, indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        print(f"Wrote compiled migration bundle to {args.output_bundle}")
    if args.push_webhook_url:
        push_res = push_migration_bundle_to_workspace(
            bundle,
            webhook_url=args.push_webhook_url,
            admin_email=args.admin_email,
            admin_passkey=args.admin_passkey,
        )
        print("Workspace push result:", json.dumps(push_res, indent=2))


if __name__ == "__main__":
    main()
