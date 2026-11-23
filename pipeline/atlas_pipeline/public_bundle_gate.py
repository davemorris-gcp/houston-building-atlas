"""Public Bundle Sanitizer & Zero-Leak Allowlist Gate (`PublicBundleGate`).

Enforces the Phase 1 three-tier confidentiality contract across all tracked public
artifacts in `app/public/data/`, `app/llms*.txt`, `app/public/ai-companion/*`, and
`pipeline/backups/workspace_cloud_backup.json`:

1. Raw public-record municipal/state filings (`active_permit_alerts.json`, public
   Planning Commission/PlatTracker items in `plat_agenda_monitor.json`, and public
   `permits` + `agenda` events in `monitor_activity_log.json`) remain browsable.
2. Zero internal Preservation Houston `Significance` ratings, `PLAT_REV:*` reviews,
   `Watchlist_Dossiers`, screener notes, `staff`/`watchlist` activity events, or
   `Staff_Only` strategy/entity records ever enter tracked public files.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
from pathlib import Path
import re
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
try:
    from .parcel_monitoring_model import (
        compute_public_plat_suggested_tier,
        sanitize_monitoring_text,
    )
except ImportError:
    if str(REPO_ROOT / "pipeline") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "pipeline"))
    from atlas_pipeline.parcel_monitoring_model import (
        compute_public_plat_suggested_tier,
        sanitize_monitoring_text,
    )

_PRIVATE_PLAT_ITEM_KEYS = frozenset(
    {
        "review",
        "_review",
        "overrideTier",
        "significance",
        "significanceLevel",
        "recommendedSignificance",
        "screeningNotes",
        "staffOnly",
        "watchPropertyId",
    }
)

_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_ALLOWED_PUBLIC_ARCHIVE_EMAILS = frozenset(
    {
        "archives@hctx.net",
        "historicpreservation@houstontx.gov",
    }
)


def _compute_public_only_suggested_tier(item: dict[str, Any], atlas_obj: dict[str, Any]) -> str:
    """Recompute item `atlas.suggestedTier` strictly from public Atlas facts without human `overrideTier`."""
    oldest_yr = int(atlas_obj.get("earliestYearBuilt") or atlas_obj.get("oldestYearBuilt") or 0)
    desigs = [d for d in (atlas_obj.get("designations") or []) if d]
    confirmed_vacant = bool(atlas_obj.get("confirmedVacant")) and oldest_yr == 0 and not desigs
    c_meta = item.get("_c") if isinstance(item.get("_c"), dict) else {}
    return compute_public_plat_suggested_tier(
        oldest_yr=oldest_yr,
        designations=desigs,
        flags=c_meta.get("flags") or (),
        super_n=str(item.get("superN") or ""),
        is_confirmed_vacant=confirmed_vacant,
    )


def _sanitize_single_plat_item(raw_item: dict[str, Any]) -> dict[str, Any]:
    it = {
        k: copy.deepcopy(v)
        for k, v in raw_item.items()
        if k not in _PRIVATE_PLAT_ITEM_KEYS
    }
    if isinstance(it.get("_c"), dict):
        c_clean = dict(it["_c"])
        # Remove review-driven screen-out overrides and watched-developer flags from public item JSON
        if c_clean.get("overriddenFromOut"):
            c_clean["group"] = "out"
            c_clean["overriddenFromOut"] = False
            c_clean["reason"] = str(c_clean.get("originalReason") or c_clean.get("reason") or "")
        if isinstance(c_clean.get("flags"), list):
            c_clean["flags"] = [f for f in c_clean["flags"] if f != "dev"]
        it["_c"] = c_clean

    if isinstance(it.get("atlas"), dict):
        at_clean = {
            k: v
            for k, v in it["atlas"].items()
            if k not in _PRIVATE_PLAT_ITEM_KEYS
        }
        at_clean["suggestedTier"] = _compute_public_only_suggested_tier(it, at_clean)
        it["atlas"] = at_clean
    return _scrub_dashes_deep(it)


def _recompute_public_cycle_summary(
    items: list[dict[str, Any]], base_summary: dict[str, Any] | None = None
) -> dict[str, Any]:
    s = dict(base_summary or {})
    on_agenda = [it for it in items if it.get("onMeetingAgenda") is not False]
    s["totalAgendaItems"] = len(on_agenda)
    s["toCheckCount"] = sum(
        1 for it in on_agenda if ((it.get("_c") or {}).get("group") == "check")
    )
    s["designatedCount"] = sum(
        1 for it in on_agenda if ((it.get("_c") or {}).get("group") == "designated")
    )
    s["etjCount"] = sum(
        1 for it in on_agenda if ((it.get("_c") or {}).get("group") == "etj")
    )
    s["screenedOutCount"] = sum(
        1 for it in on_agenda if ((it.get("_c") or {}).get("group") == "out")
    )
    for priv_count_key in ("tier1Count", "tier2Count", "tier3Count", "tier4Count", "reviewedCount"):
        s.pop(priv_count_key, None)
    return s


def _scrub_dashes_deep(val: Any) -> Any:
    if isinstance(val, str):
        return sanitize_monitoring_text(val) if ("\u2014" in val or "\u2013" in val) else val
    if isinstance(val, list):
        return [_scrub_dashes_deep(x) for x in val]
    if isinstance(val, dict):
        return {str(k): _scrub_dashes_deep(v) for k, v in val.items()}
    return val


def sanitize_public_plat_agenda_dataset(plat_doc: dict[str, Any]) -> dict[str, Any]:
    """Strip all internal reviews and item-level `overrideTier` hints from `plat_agenda_monitor.json`."""
    out = copy.deepcopy(plat_doc or {})
    out["reviews"] = {}
    clean_top_items = [
        _sanitize_single_plat_item(it)
        for it in (out.get("items") or [])
        if isinstance(it, dict)
    ]
    out["items"] = clean_top_items
    out["summary"] = _recompute_public_cycle_summary(
        clean_top_items, out.get("summary") if isinstance(out.get("summary"), dict) else None
    )

    clean_cycles: dict[str, Any] = {}
    for ck, cyc in (out.get("cycles") or {}).items():
        if not isinstance(cyc, dict):
            continue
        cyc_copy = dict(cyc)
        cyc_copy["reviews"] = {}
        c_items = [
            _sanitize_single_plat_item(it)
            for it in (cyc_copy.get("items") or [])
            if isinstance(it, dict)
        ]
        cyc_copy["items"] = c_items
        cyc_copy["summary"] = _recompute_public_cycle_summary(
            c_items, cyc_copy.get("summary") if isinstance(cyc_copy.get("summary"), dict) else None
        )
        clean_cycles[str(ck)] = cyc_copy
    out["cycles"] = clean_cycles
    return _scrub_dashes_deep(out)


def sanitize_sweep_health_for_public(raw_health: Any, fallback_ts: str = "") -> dict[str, Any]:
    h = raw_health if isinstance(raw_health, dict) else {}
    total_ag = int(h.get("totalAgencies") or 8)
    failed = [str(x) for x in (h.get("failedAgencies") or []) if x]
    succ = int(
        h["succeededAgencies"]
        if "succeededAgencies" in h and h.get("succeededAgencies") is not None
        else max(0, total_ag - len(failed))
    )
    clean_agencies: dict[str, Any] = {}
    for ag_id, ag_meta in (h.get("agencies") or {}).items():
        if not isinstance(ag_meta, dict):
            continue
        clean_agencies[str(ag_id)] = {
            "status": sanitize_monitoring_text(ag_meta.get("status") or "ok"),
            "ok": bool(ag_meta.get("ok", True)),
            "label": sanitize_monitoring_text(ag_meta.get("label") or ag_id),
            "error": sanitize_monitoring_text(ag_meta.get("error") or ""),
        }
    return {
        "timestamp": sanitize_monitoring_text(h.get("timestamp") or fallback_ts),
        "sweepsExecuted": bool(h.get("sweepsExecuted", True)),
        "totalAgencies": total_ag,
        "succeededAgencies": succ,
        "failedAgencies": failed,
        "skippedAgencies": [str(x) for x in (h.get("skippedAgencies") or []) if x],
        "allOk": len(failed) == 0,
        "agencies": clean_agencies,
    }


def build_public_watchlist_telemetry_stub(
    watchlist_doc: dict[str, Any] | None = None,
    *,
    now_iso: str = "",
) -> dict[str, Any]:
    """Build the public-safe `property_watchlist.json` sweep health envelope with zero dossiers."""
    wl = watchlist_doc or {}
    ts = sanitize_monitoring_text(
        wl.get("updatedAt")
        or wl.get("lastAutoSweepAt")
        or now_iso
        or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    last_sweep = sanitize_monitoring_text(wl.get("lastAutoSweepAt") or ts)
    return {
        "schemaVersion": "2.0-public-stub",
        "requiresAuth": True,
        "privacyNotice": (
            "Internal Significance assessments, Watchlist investigative dossiers, and "
            "Staff-Only strategy notes require authenticated Preservation Houston Staff "
            "or Screener sign-in."
        ),
        "updatedAt": ts,
        "lastAutoSweepAt": last_sweep,
        "lastSweepHealth": sanitize_sweep_health_for_public(wl.get("lastSweepHealth"), last_sweep),
        "autoSweepSchedule": sanitize_monitoring_text(
            wl.get("autoSweepSchedule") or "Daily at 7:00 AM CT"
        ),
        "automatedAgencyCount": int(wl.get("automatedAgencyCount") or 8),
        "primaryProperty": None,
        "watchedProperties": [],
    }


def sanitize_public_monitor_activity_log(act_doc: dict[str, Any]) -> dict[str, Any]:
    """Filter `monitor_activity_log.json` strictly to public `permits` & `agenda` streams."""
    doc = copy.deepcopy(act_doc or {})
    raw_events = doc.get("events") or []
    public_events: list[dict[str, Any]] = []

    for ev in raw_events:
        if not isinstance(ev, dict):
            continue
        stream = str(ev.get("stream") or "").strip().lower()
        kind = str(ev.get("kind") or "").strip()
        if kind == "watchlist_sweep_degraded":
            clean_notice = {
                k: v
                for k, v in ev.items()
                if k not in ("watchPropertyId", "hcad", "address", "propertyName")
            }
            public_events.append(_scrub_dashes_deep(clean_notice))
            continue
        if stream not in ("permits", "agenda"):
            continue
        ev_clean = {
            k: v
            for k, v in ev.items()
            if k not in ("watchPropertyId", "reviewer", "staffOnly")
        }
        public_events.append(_scrub_dashes_deep(ev_clean))

    events_by_day: dict[str, list[dict[str, Any]]] = {}
    for ev in public_events:
        ld = str(ev.get("localDate") or "")
        if ld:
            events_by_day.setdefault(ld, []).append(ev)

    clean_runs: list[dict[str, Any]] = []
    for run in doc.get("dailyRuns") or []:
        if not isinstance(run, dict):
            continue
        r_copy = dict(run)
        r_copy.pop("propertiesSwept", None)
        d_iso = str(r_copy.get("date") or "")
        day_evs = [
            e
            for e in events_by_day.get(d_iso, [])
            if e.get("kind") != "watchlist_sweep_degraded"
        ]
        r_copy["eventCount"] = len(day_evs)
        r_copy["adverseEventCount"] = sum(
            1 for e in day_evs if e.get("adverse") or e.get("severity") == "critical"
        )
        r_copy["byStream"] = {
            "watchlist": 0,
            "agenda": sum(1 for e in day_evs if e.get("stream") == "agenda"),
            "staff": 0,
            "permits": sum(1 for e in day_evs if e.get("stream") == "permits"),
        }
        clean_runs.append(r_copy)

    substantive = [e for e in public_events if e.get("kind") != "watchlist_sweep_degraded"]
    doc["lastSweepHealth"] = sanitize_sweep_health_for_public(
        doc.get("lastSweepHealth"), str(doc.get("lastSweepAt") or doc.get("generatedAt") or "")
    )
    doc["publicOnly"] = True
    doc["privacyContract"] = "phase1_public_streams_only"
    doc["dailyRuns"] = clean_runs
    doc["events"] = public_events
    doc["summary"] = {
        "totalEvents": len(substantive),
        "criticalCount": sum(1 for e in substantive if e.get("severity") == "critical"),
        "warningCount": sum(1 for e in substantive if e.get("severity") == "warning"),
        "infoCount": sum(1 for e in substantive if e.get("severity") == "info"),
        "byStream": {
            "watchlist": 0,
            "agenda": sum(1 for e in substantive if e.get("stream") == "agenda"),
            "staff": 0,
            "permits": sum(1 for e in substantive if e.get("stream") == "permits"),
        },
        "operationalNoticeCount": len(public_events) - len(substantive),
        "daysTracked": len(clean_runs),
    }
    return _scrub_dashes_deep(doc)


def validate_public_bundle_zero_leaks(repo_root: Path = REPO_ROOT) -> list[str]:
    """Return a list of leak/hygiene violations across public data, backups, and LLM files."""
    errors: list[str] = []
    data_dir = repo_root / "app" / "public" / "data"

    wl_path = data_dir / "property_watchlist.json"
    if wl_path.exists():
        wl = json.loads(wl_path.read_text(encoding="utf-8"))
        if wl.get("watchedProperties") != []:
            errors.append(
                f"property_watchlist.json leaked {len(wl.get('watchedProperties') or [])} watchedProperties!"
            )
        if wl.get("primaryProperty") is not None:
            errors.append("property_watchlist.json leaked non-null primaryProperty!")

    plat_path = data_dir / "plat_agenda_monitor.json"
    if plat_path.exists():
        plat = json.loads(plat_path.read_text(encoding="utf-8"))
        if plat.get("reviews"):
            errors.append("plat_agenda_monitor.json leaked top-level reviews!")
        item_groups: list[tuple[str, list[Any]]] = [("top-level", plat.get("items") or [])]
        for ck, cyc in (plat.get("cycles") or {}).items():
            if isinstance(cyc, dict) and cyc.get("reviews"):
                errors.append(f"plat_agenda_monitor.json cycle {ck} leaked reviews!")
            item_groups.append((f"cycle {ck}", (cyc or {}).get("items") or []))
        for scope_lbl, item_list in item_groups:
            for it in item_list:
                if isinstance(it, dict):
                    for pk in _PRIVATE_PLAT_ITEM_KEYS:
                        if pk in it or (isinstance(it.get("atlas"), dict) and pk in it["atlas"]):
                            errors.append(
                                f"plat_agenda_monitor.json {scope_lbl} item {it.get('app')} leaked private key {pk}!"
                            )
                    c_meta = it.get("_c") if isinstance(it.get("_c"), dict) else {}
                    if "dev" in (c_meta.get("flags") or []) or c_meta.get("overriddenFromOut"):
                        errors.append(
                            f"plat_agenda_monitor.json {scope_lbl} item {it.get('app')} leaked private _c flags/override!"
                        )

    act_path = data_dir / "monitor_activity_log.json"
    if act_path.exists():
        act = json.loads(act_path.read_text(encoding="utf-8"))
        for ev in act.get("events") or []:
            if not isinstance(ev, dict):
                continue
            if ev.get("kind") == "watchlist_sweep_degraded":
                continue
            if ev.get("stream") in ("staff", "watchlist"):
                errors.append(
                    f"monitor_activity_log.json leaked private stream '{ev.get('stream')}' event {ev.get('id')}!"
                )
            for priv_ev_key in ("watchPropertyId", "reviewer", "staffOnly"):
                if ev.get(priv_ev_key):
                    errors.append(
                        f"monitor_activity_log.json event {ev.get('id')} leaked private field '{priv_ev_key}'!"
                    )

    bkp_path = repo_root / "pipeline" / "backups" / "workspace_cloud_backup.json"
    if bkp_path.exists():
        bkp = json.loads(bkp_path.read_text(encoding="utf-8"))
        for forbidden_sec in ("platReviews", "watchProperties", "staffOnly", "parcels", "decisions"):
            if bkp.get(forbidden_sec):
                errors.append(
                    f"workspace_cloud_backup.json leaked private section '{forbidden_sec}'!"
                )
        for ov_k in (bkp.get("approvedOverrides") or {}).keys():
            if str(ov_k).startswith(("PLAT_REV:", "WATCH_PROP:")):
                errors.append(
                    f"workspace_cloud_backup.json approvedOverrides leaked monitoring key '{ov_k}'!"
                )

    text_scan_paths: list[Path] = [repo_root / "app" / "llms.txt", repo_root / "app" / "llms-full.txt"]
    ai_comp_dir = repo_root / "app" / "public" / "ai-companion"
    if ai_comp_dir.exists():
        for cand_p in sorted(ai_comp_dir.rglob("*")):
            if cand_p.is_file() and cand_p.suffix in (".md", ".txt", ".py", ".mjs", ".json"):
                text_scan_paths.append(cand_p)

    for lp in text_scan_paths:
        if lp.exists():
            rel_lbl = str(lp.relative_to(repo_root))
            txt = lp.read_text(encoding="utf-8")
            if "## Section 1: Active Endangered Property Watchlist" in txt:
                errors.append(f"{rel_lbl} still contains private Watchlist Section 1 table!")
            if re.search(r"plat-monitor\.html#tab=watchlist&watch=[A-Za-z0-9]", txt):
                errors.append(f"{rel_lbl} leaked private Watchlist dossier deep links!")
            if "\u2014" in txt or "\u2013" in txt:
                errors.append(f"{rel_lbl} contains forbidden typographic dashes!")
            if lp.suffix in (".md", ".txt"):
                for em_m in _EMAIL_PATTERN.findall(txt):
                    if em_m.lower() not in _ALLOWED_PUBLIC_ARCHIVE_EMAILS:
                        errors.append(f"{rel_lbl} leaked non-public email token: {em_m}")

    for check_p in (wl_path, plat_path, act_path, bkp_path):
        if check_p.exists():
            raw_txt = check_p.read_text(encoding="utf-8")
            if "\u2014" in raw_txt or "\u2013" in raw_txt:
                errors.append(f"{check_p.name} contains forbidden typographic dashes!")

    return errors


def assert_public_bundle_zero_leaks(repo_root: Path = REPO_ROOT) -> None:
    errs = validate_public_bundle_zero_leaks(repo_root)
    if errs:
        raise AssertionError("PublicBundleGate failed:\n  - " + "\n  - ".join(errs))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Validate or sanitize public monitoring bundles.")
    parser.add_argument("--verify-repo", action="store_true", help="Assert zero private leaks in tracked public files.")
    parser.add_argument("--sanitize-in-place", action="store_true", help="Sanitize public JSON files in app/public/data/.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    root = args.repo_root.resolve()
    data_dir = root / "app" / "public" / "data"

    if args.sanitize_in_place:
        plat_p = data_dir / "plat_agenda_monitor.json"
        if plat_p.exists():
            clean_plat = sanitize_public_plat_agenda_dataset(
                json.loads(plat_p.read_text(encoding="utf-8"))
            )
            plat_p.write_text(json.dumps(clean_plat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        wl_p = data_dir / "property_watchlist.json"
        if wl_p.exists():
            clean_wl = build_public_watchlist_telemetry_stub(
                json.loads(wl_p.read_text(encoding="utf-8"))
            )
            wl_p.write_text(json.dumps(clean_wl, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        act_p = data_dir / "monitor_activity_log.json"
        if act_p.exists():
            clean_act = sanitize_public_monitor_activity_log(
                json.loads(act_p.read_text(encoding="utf-8"))
            )
            act_p.write_text(json.dumps(clean_act, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.verify_repo:
        assert_public_bundle_zero_leaks(root)
        print("PublicBundleGate PASSED: zero private monitoring leaks in tracked public files.")


if __name__ == "__main__":
    main()
