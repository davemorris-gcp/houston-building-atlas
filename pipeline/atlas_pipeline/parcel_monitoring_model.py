"""Canonical Parcel-Centered Preservation Monitoring Model (Python 3.12+ Stdlib).

Pure, zero-network domain model backed by `contracts/monitoring_schema_contract.json` and
maintained in 1:1 behavioral parity with `app/js/parcelMonitoringModel.js`.

Why separate Significance, Protection, Threat Stage, Advocacy Status, and Outcome:
  Legacy Tiers 1..4 conflated architectural/cultural importance with immediacy of loss and
  case disposition, forcing reviewers to re-tier repeated plat filings and mixing historic
  demolitions ("lost") with vacant/non-historic parcels ("not_relevant"). Recording
  Significance once per parcel allows Attention Level ("act" | "watch" | "record") to
  recalculate automatically as filings arrive.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

CONTRACT_PATH = Path(__file__).resolve().parent / "contracts" / "monitoring_schema_contract.json"
MONITORING_SCHEMA_CONTRACT: dict[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

_TERMINAL_OUTCOMES_SET = frozenset(MONITORING_SCHEMA_CONTRACT["enums"]["terminalOutcomes"])
_VALID_ATTENTION_SET = frozenset(MONITORING_SCHEMA_CONTRACT["enums"]["attentionLevels"])
_VALID_SIGNIFICANCE_SET = frozenset(MONITORING_SCHEMA_CONTRACT["enums"]["significanceLevels"])
_VALID_REVIEW_STATUS_SET = frozenset(MONITORING_SCHEMA_CONTRACT["enums"]["reviewStatuses"])
_VALID_BASIS_SET = frozenset(MONITORING_SCHEMA_CONTRACT["enums"]["significanceBases"])
_VALID_CONTRIBUTING_SET = frozenset(MONITORING_SCHEMA_CONTRACT["enums"]["contributingStatuses"])
_VALID_INTEGRITY_SET = frozenset(MONITORING_SCHEMA_CONTRACT["enums"]["integrityGrades"])
_VALID_PROTECTION_SET = frozenset(MONITORING_SCHEMA_CONTRACT["enums"]["protectionLevels"])
_VALID_ADVOCACY_SET = frozenset(MONITORING_SCHEMA_CONTRACT["enums"]["advocacyStatuses"])
_VALID_OUTCOME_SET = frozenset(MONITORING_SCHEMA_CONTRACT["enums"]["outcomes"])

_PIPELINE_MACHINE_PARCEL_COLS = frozenset(
    MONITORING_SCHEMA_CONTRACT["columnOwnership"]["pipelineMachineOwnedParcelCols"]
)
_SCREENER_DECISION_COLS = frozenset(
    MONITORING_SCHEMA_CONTRACT["columnOwnership"]["screenerWritableDecisionCols"]
)
_STAFF_ONLY_DECISION_COLS = frozenset(
    MONITORING_SCHEMA_CONTRACT["columnOwnership"]["staffOnlyDecisionCols"]
)

_CORP_NOISE_TOKENS_RE = re.compile(
    r"\b(?:LLC|L\s*L\s*C|INC|INCORPORATED|CORP|CORPORATION|LTD|LIMITED|LP|L\s*P|LLP|CO|COMPANY|PROPERTIES|INVESTMENTS|HOLDINGS|GROUP|TRUST|ESTATE\s+OF|ET\s+AL)\b",
    re.IGNORECASE,
)
_CHICAGO_TZ = ZoneInfo("America/Chicago")


def sanitize_monitoring_text(raw_val: Any) -> str:
    if raw_val is None:
        return ""
    return (
        str(raw_val)
        .replace(" \u2014 ", " - ")
        .replace("\u2014", "-")
        .replace(" \u2013 ", " - ")
        .replace("\u2013", "-")
        .strip()
    )


def to_chicago_calendar_date(raw_ts_or_date: Any) -> str:
    """Normalize an ISO timestamp or date string into `YYYY-MM-DD` in `America/Chicago`."""
    s = sanitize_monitoring_text(raw_ts_or_date)
    if not s:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    slash_m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if slash_m:
        return f"{slash_m.group(3)}-{int(slash_m.group(1)):02d}-{int(slash_m.group(2)):02d}"
    iso_candidate = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        parsed = dt.datetime.fromisoformat(iso_candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(_CHICAGO_TZ).strftime("%Y-%m-%d")
    except ValueError:
        prefix_m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
        return prefix_m.group(1) if prefix_m else ""


def calendar_days_between(start_raw: Any, end_raw: Any) -> int | None:
    """Pure calendar-day delta (`end - start`) immune to DST hour transitions."""
    start_ymd = to_chicago_calendar_date(start_raw)
    end_ymd = to_chicago_calendar_date(end_raw)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", start_ymd) or not re.match(
        r"^\d{4}-\d{2}-\d{2}$", end_ymd
    ):
        return None
    d_start = dt.date.fromisoformat(start_ymd)
    d_end = dt.date.fromisoformat(end_ymd)
    return (d_end - d_start).days


def normalize_hcad13(raw_val: Any) -> str:
    base = re.sub(r"[\s\-]+", "", sanitize_monitoring_text(raw_val).split("#", 1)[0])
    if not re.match(r"^\d{10,13}$", base):
        return ""
    return base.zfill(13)


def parse_delimited_list(raw_val: Any) -> list[str]:
    if isinstance(raw_val, (list, tuple)):
        seen: list[str] = []
        for item in raw_val:
            c = sanitize_monitoring_text(item)
            if c and c not in seen:
                seen.append(c)
        return seen
    s = sanitize_monitoring_text(raw_val)
    if not s:
        return []
    out: list[str] = []
    for part in re.split(r"[;|,]+", s):
        c = part.strip()
        if c and c not in out:
            out.append(c)
    return out


def parse_related_hcads(raw_val: Any) -> list[str]:
    if isinstance(raw_val, (list, tuple)):
        tokens: list[Any] = []
        for item in raw_val:
            if isinstance(item, (list, tuple)):
                tokens.extend(item)
            else:
                tokens.extend(re.split(r"[;|,\s]+", sanitize_monitoring_text(item)))
    else:
        tokens = re.split(r"[;|,\s]+", sanitize_monitoring_text(raw_val))
    out: list[str] = []
    for tok in tokens:
        norm = normalize_hcad13(tok)
        if norm and norm not in out:
            out.append(norm)
    return out


def _slugify_token(raw_val: Any, fallback: str = "UNKNOWN") -> str:
    slug = re.sub(r"[^A-Z0-9]+", "_", sanitize_monitoring_text(raw_val).upper()).strip("_")[:64]
    return slug or fallback


def mint_synthetic_unparceled_id(
    *, app_no: str = "", address: str = "", name: str = ""
) -> str:
    """Mint a deterministic synthetic `parcel_id` for zero-HCAD ETJ tracts or unplatted reserves."""
    clean_app = sanitize_monitoring_text(app_no)
    if clean_app:
        return f"UNPARCELED:APP_{_slugify_token(clean_app)}"
    clean_addr = sanitize_monitoring_text(address)
    if clean_addr:
        return f"UNPARCELED:ADDR_{_slugify_token(clean_addr)}"
    return f"UNPARCELED:NAME_{_slugify_token(name, 'ITEM')}"


def _norm_addr_key(addr: Any) -> str:
    cleaned = re.sub(r"[.,#]", " ", sanitize_monitoring_text(addr).upper())
    return re.sub(r"\s+", " ", cleaned).strip()


def build_campus_parcel_index(parcels: Any) -> dict[str, dict[str, Any]]:
    """Build secondary `hcad_num -> campus_group_id` and perimeter-address indices."""
    by_parcel_id: dict[str, dict[str, Any]] = {}
    by_member_hcad: dict[str, str] = {}
    by_normalized_address: dict[str, str] = {}

    if isinstance(parcels, dict):
        items = list(parcels.values())
    elif isinstance(parcels, list):
        items = list(parcels)
    else:
        items = []

    def _sort_key(p: Any) -> tuple[int, str]:
        if not isinstance(p, dict):
            return (1, "")
        pid_raw = str(p.get("parcel_id") or p.get("campus_group_id") or "")
        return (0 if pid_raw.startswith("CAMPUS:") else 1, pid_raw)

    for p in sorted(items, key=_sort_key):
        if not isinstance(p, dict):
            continue
        pid = sanitize_monitoring_text(
            p.get("parcel_id")
            or p.get("campus_group_id")
            or normalize_hcad13(p.get("hcad_num"))
        )
        if not pid:
            continue
        by_parcel_id[pid] = p
        hcads = parse_related_hcads([p.get("hcad_num") or "", p.get("related_hcads") or ""])
        for hid in hcads:
            by_member_hcad.setdefault(hid, pid)
        for addr in [p.get("primary_address") or "", *parse_delimited_list(p.get("perimeter_addresses"))]:
            ak = _norm_addr_key(addr)
            if ak:
                by_normalized_address.setdefault(ak, pid)

    return {
        "byParcelId": by_parcel_id,
        "byMemberHcad": by_member_hcad,
        "byNormalizedAddress": by_normalized_address,
    }


def resolve_canonical_parcel_id(
    *,
    parcel_id: str = "",
    campus_group_id: str = "",
    hcad_ids: Any = None,
    app_no: str = "",
    address: str = "",
    name: str = "",
    campus_index: dict[str, dict[str, Any]] | None = None,
) -> str:
    explicit_campus = sanitize_monitoring_text(campus_group_id)
    if explicit_campus:
        return (
            explicit_campus
            if explicit_campus.startswith("CAMPUS:")
            else f"CAMPUS:{_slugify_token(explicit_campus)}"
        )
    explicit_pid = sanitize_monitoring_text(parcel_id)
    if explicit_pid and (
        explicit_pid.startswith("CAMPUS:") or explicit_pid.startswith("UNPARCELED:")
    ):
        return explicit_pid
    raw_hcad_inputs = [explicit_pid] if explicit_pid else []
    if hcad_ids is not None:
        if isinstance(hcad_ids, (list, tuple)):
            raw_hcad_inputs.extend(hcad_ids)
        else:
            raw_hcad_inputs.append(hcad_ids)
    norm_hcads = parse_related_hcads(raw_hcad_inputs)
    if campus_index and "byMemberHcad" in campus_index:
        for hid in norm_hcads:
            mapped = campus_index["byMemberHcad"].get(hid)
            if mapped:
                return mapped
    if norm_hcads:
        return norm_hcads[0]
    if campus_index and "byNormalizedAddress" in campus_index and address:
        ak = _norm_addr_key(address)
        mapped_addr = campus_index["byNormalizedAddress"].get(ak)
        if mapped_addr:
            return mapped_addr
    return mint_synthetic_unparceled_id(app_no=app_no, address=address, name=name)


def _lookup_area_record(areas_by_id: Any, area_id: str) -> dict[str, Any] | None:
    if not areas_by_id or not area_id:
        return None
    if isinstance(areas_by_id, dict):
        rec = areas_by_id.get(area_id)
        return rec if isinstance(rec, dict) else None
    if isinstance(areas_by_id, list):
        for item in areas_by_id:
            if isinstance(item, dict) and item.get("area_id") == area_id:
                return item
    return None


def resolve_effective_area_context(
    area_ids_raw: Any,
    areas_by_id: Any = None,
    default_cutoff_year: int = int(MONITORING_SCHEMA_CONTRACT["defaultAgeCutoffYear"]),
) -> dict[str, Any]:
    area_ids = parse_delimited_list(area_ids_raw)
    ranks: dict[str, int] = MONITORING_SCHEMA_CONTRACT["enums"]["priorityStatusRanks"]
    best_status = "outside"
    best_rank = 0
    max_cutoff = int(default_cutoff_year or MONITORING_SCHEMA_CONTRACT["defaultAgeCutoffYear"])
    matched_areas: list[dict[str, Any]] = []

    for aid in area_ids:
        rec = _lookup_area_record(areas_by_id, aid)
        if not rec:
            if aid.startswith("area:priority:") and best_rank < ranks["priority"]:
                best_status = "priority"
                best_rank = ranks["priority"]
            elif aid.startswith("area:candidate:") and best_rank < ranks["candidate"]:
                best_status = "candidate"
                best_rank = ranks["candidate"]
            elif best_rank < ranks["standard"]:
                best_status = "standard"
                best_rank = ranks["standard"]
            continue
        matched_areas.append(rec)
        st = sanitize_monitoring_text(rec.get("priority_status") or "standard").lower()
        r = ranks.get(st, ranks["standard"])
        if r > best_rank:
            best_rank = r
            best_status = st
        cutoff = _safe_int(rec.get("age_rule_cutoff_year"))
        dev_end = _safe_int(rec.get("dev_period_end"))
        eff_cutoff = max(cutoff, dev_end)
        if eff_cutoff > max_cutoff:
            max_cutoff = eff_cutoff

    return {
        "areaIds": area_ids,
        "matchedAreas": matched_areas,
        "inTrackedArea": best_rank > 0,
        "priorityStatus": best_status,
        "ageRuleCutoffYear": max_cutoff,
    }


def _safe_int(val: Any) -> int:
    if val is None or val == "" or isinstance(val, bool):
        return 0
    try:
        f = float(val)
        return int(math.trunc(f)) if math.isfinite(f) else 0
    except (TypeError, ValueError):
        return 0


def classify_protection_level(
    designation_summary: Any = "",
    *,
    in_historic_district: bool = False,
    is_protected_landmark: bool = False,
    is_city_landmark: bool = False,
    is_nrhp: bool = False,
) -> str:
    """Classify statutory protection under COH Code of Ordinances Ch. 33."""
    s = sanitize_monitoring_text(designation_summary).lower()
    if (
        is_protected_landmark
        or in_historic_district
        or re.search(
            r"\bprotected\s+landmark\b|\bplm\b|\bcity\s+historic\s+district\b|\bcoh\s+historic\s+district\b",
            s,
        )
        or (
            re.search(r"\bhistoric\s+district\b", s)
            and not re.search(r"\bnational\s+register\b|\bnrhp\b|\bpotential\b", s)
        )
    ):
        return "binding"
    if (
        is_city_landmark
        or is_nrhp
        or re.search(
            r"\bcity\s+landmark\b|\bnational\s+register\b|\bnrhp\b|\brecorded\s+texas\s+historic\s+landmark\b|\brthl\b|\bstate\s+antiquities\b|\bsal\b",
            s,
        )
    ):
        return "limited"
    return "none"


def compute_contributing_status(
    parcel: dict[str, Any], area_context: dict[str, Any] | None = None
) -> str:
    ov = sanitize_monitoring_text(parcel.get("contributing_override")).lower()
    if ov and ov in _VALID_CONTRIBUTING_SET:
        return ov
    outcome = sanitize_monitoring_text(parcel.get("outcome")).lower()
    if outcome in ("lost", "lost_before_review", "not_relevant"):
        return "non_contributing"
    ctx = area_context or resolve_effective_area_context(parcel.get("area_ids"))
    desig = sanitize_monitoring_text(parcel.get("designation_summary")).lower()
    in_district = bool(re.search(r"\bhistoric\s+district\b", desig))
    if not ctx["inTrackedArea"] and not in_district:
        return "outside_area"
    yr = _safe_int(parcel.get("year_built"))
    if int(MONITORING_SCHEMA_CONTRACT["minStandingYear"]) <= yr <= int(ctx["ageRuleCutoffYear"]):
        return "contributing"
    return "non_contributing"


def normalize_party_comparison_key(raw_name: Any) -> str:
    cleaned = re.sub(r"[.,'\"()/\-]+", " ", sanitize_monitoring_text(raw_name).upper())
    cleaned = _CORP_NOISE_TOKENS_RE.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def detect_parcel_signal_flags(
    *,
    owner_of_record: str = "",
    party_names: Any = None,
    land_use_class: str = "",
    event_kinds: Any = None,
) -> list[str]:
    flags: list[str] = []
    owner_key = normalize_party_comparison_key(owner_of_record)
    owner_tokens = {t for t in owner_key.split(" ") if len(t) >= 3}
    parties = parse_delimited_list(party_names)

    if owner_tokens and parties:
        has_disjoint = False
        for p in parties:
            pk = normalize_party_comparison_key(p)
            if not pk or len(pk) < 3:
                continue
            if re.search(
                r"\b(?:SURVEY|SURVEYING|ENGINEERING|ENGINEERS|PLATTING)\b", p.upper()
            ):
                continue
            p_tokens = [t for t in pk.split(" ") if len(t) >= 3]
            if not p_tokens:
                continue
            if not any(tok in owner_tokens for tok in p_tokens):
                has_disjoint = True
                break
        if has_disjoint:
            flags.append("owner_applicant_mismatch")

    lu_upper = sanitize_monitoring_text(land_use_class).upper()
    if re.match(r"^(?:A\d|B\d|RESIDENTIAL|SINGLE[-\s]*FAMILY)", lu_upper):
        kinds_joined = " ".join(parse_delimited_list(event_kinds)).lower()
        if re.search(
            r"\b(?:tabc|liquor|tdlr|commercial|unrestricted_reserve|change_of_use|comptroller|tceq)\b",
            kinds_joined,
        ):
            flags.append("commercial_on_residential")

    return flags


def evaluate_historical_baseline_event(
    *,
    filing_date: Any = "",
    parcel_added_at: Any = "",
    as_of_date_cst: Any = "",
    max_age_days: int = int(MONITORING_SCHEMA_CONTRACT["historicalBaselineMaxAgeDays"]),
) -> bool:
    filing_ymd = to_chicago_calendar_date(filing_date)
    if not filing_ymd:
        return False
    added_ymd = to_chicago_calendar_date(parcel_added_at)
    if added_ymd and filing_ymd < added_ymd:
        return True
    ref_ymd = to_chicago_calendar_date(as_of_date_cst)
    if ref_ymd:
        age_days = calendar_days_between(filing_ymd, ref_ymd)
        if age_days is not None and age_days > max_age_days:
            return True
    return False


def map_agency_or_permit_to_threat_stage(
    *,
    source_agency: str = "",
    event_kind: str = "",
    summary_text: str = "",
    adverse: bool = True,
) -> int:
    if adverse is False:
        return 0
    agency_key = sanitize_monitoring_text(source_agency).lower()
    kind_lower = sanitize_monitoring_text(event_kind).lower()
    text_lower = f"{kind_lower} {sanitize_monitoring_text(summary_text).lower()}"

    if re.search(
        r"\b(?:building\s+demolition|type\s+sd\b|dangerous\s+building\s+demolition|type\s+4d\b|active\s+teardown|demolished\s+on\s+site|stage\s*5)\b",
        text_lower,
    ):
        return 5
    if re.search(
        r"\b(?:sewer\s+disconnect|12/4p|asbestos|dshs|dangerous\s+building|dondb|311\s+dangerous|stage\s*4)\b",
        text_lower,
    ):
        return 4
    if re.search(
        r"\b(?:replat|plat|variance|dpv|tabc|liquor|tdlr|tabs|plan\s+review|ilms|abandonment|tceq|comptroller|stage\s*3)\b",
        text_lower,
    ):
        return 3
    if re.search(
        r"\b(?:deed|ownership\s+change|recent\s+sale|new\s+llc|sos|stage\s*2)\b",
        text_lower,
    ):
        return 2
    if re.search(
        r"\b(?:for\s+sale|loopnet|listing|broker\s+sign|surplus|escrow|stage\s*1)\b",
        text_lower,
    ):
        return 1
    mapped = MONITORING_SCHEMA_CONTRACT["agencyThreatStageMap"].get(agency_key)
    return int(mapped) if isinstance(mapped, int) else 0


def compute_threat_stage_from_events(
    events: list[dict[str, Any]] | None = None, parcel: dict[str, Any] | None = None
) -> int:
    p = parcel or {}
    adv = sanitize_monitoring_text(p.get("advocacy_status")).lower()
    out = sanitize_monitoring_text(p.get("outcome")).lower()
    if adv == "closed" or out in _TERMINAL_OUTCOMES_SET:
        return 6
    max_stage = 0
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        if ev.get("is_historical_baseline") is True or str(
            ev.get("is_historical_baseline")
        ).lower() == "true":
            continue
        st = _safe_int(ev.get("threat_stage"))
        if 1 <= st <= 5 and st > max_stage:
            max_stage = st
    return max_stage


def compute_attention_level(
    parcel: dict[str, Any],
    *,
    as_of_date_cst: str = "",
    next_hearing_date_cst: str = "",
    areas_by_id: Any = None,
) -> str:
    """Compute Attention Level (`act` | `watch` | `record`) matching JS `computeAttentionLevel` 1:1."""
    override = sanitize_monitoring_text(parcel.get("attention_override")).lower()
    if override and override in _VALID_ATTENTION_SET:
        return override

    advocacy_status = sanitize_monitoring_text(parcel.get("advocacy_status")).lower()
    outcome = sanitize_monitoring_text(parcel.get("outcome")).lower()

    if advocacy_status == "closed" or outcome in _TERMINAL_OUTCOMES_SET:
        return "record"
    if advocacy_status == "recording_only":
        return "record"

    stage = _safe_int(parcel.get("threat_stage"))
    has_active_stage = 1 <= stage <= 5
    active_stage = stage if has_active_stage else 0

    has_urgent_hearing = False
    if as_of_date_cst and next_hearing_date_cst:
        delta_days = calendar_days_between(as_of_date_cst, next_hearing_date_cst)
        if (
            delta_days is not None
            and 0 <= delta_days <= int(MONITORING_SCHEMA_CONTRACT["hearingUrgentWindowDays"])
        ):
            has_urgent_hearing = True
    has_active_threat = has_active_stage or has_urgent_hearing

    sig_confirmed = sanitize_monitoring_text(parcel.get("significance_level")).lower()
    sig_recommended = sanitize_monitoring_text(parcel.get("recommended_significance")).lower()
    rev_status = sanitize_monitoring_text(parcel.get("review_status")).lower()
    eff_sig = (
        sig_confirmed
        or sig_recommended
        or ("needs_research" if rev_status == "needs_research" else "")
    )

    protection = sanitize_monitoring_text(
        parcel.get("protection_level")
    ).lower() or classify_protection_level(parcel.get("designation_summary") or "")

    if eff_sig in ("high", "moderate", "needs_research"):
        if has_active_threat:
            if protection == "binding" and active_stage <= 3:
                return "watch"
            return "act"
        return "watch"

    area_ctx = resolve_effective_area_context(parcel.get("area_ids"), areas_by_id)
    contributing = sanitize_monitoring_text(
        parcel.get("contributing_status")
    ).lower() or compute_contributing_status(parcel, area_ctx)

    if (
        area_ctx["priorityStatus"] == "priority"
        and contributing == "contributing"
        and 3 <= active_stage <= 5
    ):
        return "watch"

    return "record"


def explain_attention_level(
    parcel: dict[str, Any],
    *,
    as_of_date_cst: str = "",
    next_hearing_date_cst: str = "",
    areas_by_id: Any = None,
) -> str:
    level = compute_attention_level(
        parcel,
        as_of_date_cst=as_of_date_cst,
        next_hearing_date_cst=next_hearing_date_cst,
        areas_by_id=areas_by_id,
    )
    override = sanitize_monitoring_text(parcel.get("attention_override")).lower()
    if override and override in _VALID_ATTENTION_SET:
        return f"{level.upper()} (Staff override)"
    adv = sanitize_monitoring_text(parcel.get("advocacy_status")).lower()
    outcome = sanitize_monitoring_text(parcel.get("outcome")).lower()
    if adv == "closed" or outcome in _TERMINAL_OUTCOMES_SET:
        suffix = f" ({outcome.replace('_', ' ')})" if outcome else ""
        return f"Record: Case closed{suffix}"
    if adv == "recording_only":
        return "Record: Placed in Recording Only mode"
    stage = max(0, min(6, _safe_int(parcel.get("threat_stage"))))
    active_stage = stage if 1 <= stage <= 5 else 0
    stage_label = MONITORING_SCHEMA_CONTRACT["enums"]["threatStages"].get(str(stage), "None")
    has_urgent_hearing = False
    if as_of_date_cst and next_hearing_date_cst:
        delta_days = calendar_days_between(as_of_date_cst, next_hearing_date_cst)
        has_urgent_hearing = (
            delta_days is not None
            and 0 <= delta_days <= int(MONITORING_SCHEMA_CONTRACT["hearingUrgentWindowDays"])
        )
    sig = (
        sanitize_monitoring_text(parcel.get("significance_level"))
        or sanitize_monitoring_text(parcel.get("recommended_significance"))
        or (
            "needs research"
            if sanitize_monitoring_text(parcel.get("review_status")) == "needs_research"
            else "unreviewed"
        )
    )
    prot = sanitize_monitoring_text(
        parcel.get("protection_level")
    ) or classify_protection_level(parcel.get("designation_summary") or "")

    if level == "act":
        if active_stage == 0 and has_urgent_hearing:
            return f"Act: {sig.replace('_', ' ')} significance + upcoming hearing ({next_hearing_date_cst})"
        return f"Act: {sig.replace('_', ' ')} significance + Stage {active_stage} ({stage_label})"
    if level == "watch":
        if (
            sig in ("high", "moderate", "needs research")
            and prot == "binding"
            and (active_stage >= 1 or has_urgent_hearing)
            and active_stage <= 3
        ):
            if active_stage >= 1:
                return f"Watch: Binding HAHC review moderates Stage {active_stage} ({stage_label})"
            return f"Watch: Binding HAHC review moderates upcoming hearing ({next_hearing_date_cst})"
        if sig in ("high", "moderate", "needs research"):
            return f"Watch: {sig} significance with no active threat filing yet"
        return f"Watch: Contributing parcel in Priority Area facing Stage {active_stage} ({stage_label})"
    return "Record: Logged for citywide survey and neighborhood metrics"


def migrate_legacy_tier_to_parcel_fields(
    legacy: dict[str, Any],
    *,
    is_watchlist: bool = False,
    atlas_known_high: bool = False,
    area_context: dict[str, Any] | None = None,
    default_threat_stage: int = 0,
) -> dict[str, Any]:
    tier = sanitize_monitoring_text(legacy.get("tier") or "")
    tracker_status = sanitize_monitoring_text(legacy.get("trackerStatus") or "")
    notes_text = " ".join(
        sanitize_monitoring_text(legacy.get(k) or "")
        for k in ("why", "notes", "removalReason", "followup")
    ).lower()
    yr = _safe_int(
        legacy.get("yearBuilt")
        if legacy.get("yearBuilt") is not None
        else (
            legacy.get("year_built")
            if legacy.get("year_built") is not None
            else legacy.get("oldestYearBuilt")
        )
    )
    is_tombstone = bool(legacy.get("removedFromWatchlist") is True) or tracker_status in (
        "Removed from Watchlist",
        "Removed (Tier 3)",
        "Removed (Tier 4)",
    )

    review_status = "not_reviewed"
    significance_level = ""
    advocacy_status = "monitoring"
    outcome = ""
    threat_stage = _safe_int(
        legacy.get("threatStage")
        if legacy.get("threatStage") is not None
        else default_threat_stage
    )

    needs_research_cue = bool(
        re.search(
            r"\b(?:needs\s+research|check\s+architect|story\s+check|verify\s+history|possible\s+landmark)\b",
            notes_text,
        )
    )
    demolished_cue = (
        bool(legacy.get("graduateToLost"))
        or tracker_status == "Lost"
        or bool(
            re.search(
                r"\b(?:demolished|already\s+gone|torn\s+down|razed|cleared\s+lot)\b",
                notes_text,
            )
        )
    )
    vacant_non_historic_cue = bool(
        re.search(
            r"\b(?:vacant\s+lot|never\s+historic|parking\s+lot|post[-\s]*1976|new\s+construction\s+only|irrelevant|mismatched)\b",
            notes_text,
        )
    )

    if atlas_known_high:
        review_status = "assessed"
        significance_level = "high"

    if is_tombstone:
        advocacy_status = "closed"
        outcome = (
            "lost"
            if demolished_cue
            and int(MONITORING_SCHEMA_CONTRACT["minStandingYear"]) <= yr <= 1976
            else "not_relevant"
        )
        threat_stage = 6
    elif tracker_status == "Lost" or legacy.get("graduateToLost") is True:
        if review_status == "not_reviewed":
            review_status = "assessed"
        if not significance_level and tier in ("1", "2"):
            significance_level = "high" if tier == "1" else "moderate"
        advocacy_status = "closed"
        outcome = "lost" if is_watchlist else "lost_before_review"
        threat_stage = 6
    elif tracker_status == "Saved":
        review_status = "assessed"
        significance_level = significance_level or ("moderate" if tier == "2" else "high")
        advocacy_status = "closed"
        outcome = "saved"
        threat_stage = 6
    elif tier == "1":
        review_status = (
            "needs_research" if needs_research_cue and not atlas_known_high else "assessed"
        )
        significance_level = significance_level or "high"
        advocacy_status = (
            "monitoring"
            if tracker_status in ("Monitoring", "For Sale")
            else "active_advocacy"
        )
        if threat_stage == 0:
            threat_stage = 3
    elif tier == "2":
        review_status = (
            "needs_research" if needs_research_cue and not atlas_known_high else "assessed"
        )
        significance_level = significance_level or "moderate"
        advocacy_status = "monitoring"
        if threat_stage == 0 and not is_watchlist:
            threat_stage = 3
    elif tier == "3" or tracker_status == "Archived (Tier 3)":
        review_status = "assessed"
        significance_level = significance_level or "not_significant"
        advocacy_status = (
            "closed" if tracker_status == "Archived (Tier 3)" else "recording_only"
        )
        if tracker_status == "Archived (Tier 3)":
            outcome = "no_longer_threatened"
            threat_stage = 6
    elif tier == "4" or tracker_status == "Archived (Tier 4)":
        review_status = "assessed"
        significance_level = significance_level or "not_significant"
        advocacy_status = "closed"
        cutoff = int(
            (area_context or {}).get("ageRuleCutoffYear")
            or MONITORING_SCHEMA_CONTRACT["defaultAgeCutoffYear"]
        )
        if (
            demolished_cue
            and not vacant_non_historic_cue
            and int(MONITORING_SCHEMA_CONTRACT["minStandingYear"]) <= yr <= cutoff
        ):
            outcome = "lost" if is_watchlist else "lost_before_review"
        else:
            outcome = "not_relevant"
        threat_stage = 6
    elif tracker_status == "For Sale" and threat_stage == 0:
        threat_stage = 1

    protection_level = classify_protection_level(legacy.get("designation") or "")
    partial_parcel: dict[str, Any] = {
        "year_built": yr or "",
        "designation_summary": sanitize_monitoring_text(legacy.get("designation") or ""),
        "area_ids": (area_context or {}).get("areaIds") or [],
        "review_status": review_status,
        "recommended_significance": "",
        "significance_level": significance_level,
        "protection_level": protection_level,
        "threat_stage": threat_stage,
        "advocacy_status": advocacy_status,
        "outcome": outcome,
        "attention_override": "",
    }
    contributing_status = compute_contributing_status(partial_parcel, area_context)
    partial_parcel["contributing_status"] = contributing_status
    attention_level = compute_attention_level(partial_parcel)

    return {
        "review_status": review_status,
        "recommended_significance": "",
        "significance_level": significance_level,
        "contributing_status": contributing_status,
        "protection_level": protection_level,
        "threat_stage": threat_stage,
        "advocacy_status": advocacy_status,
        "outcome": outcome,
        "attention_level": attention_level,
    }


def derive_legacy_tier_from_parcel(parcel: dict[str, Any] | None) -> str:
    if not isinstance(parcel, dict):
        return ""
    outcome = sanitize_monitoring_text(parcel.get("outcome")).lower()
    adv = sanitize_monitoring_text(parcel.get("advocacy_status")).lower()
    rev = sanitize_monitoring_text(parcel.get("review_status")).lower()
    sig = (
        sanitize_monitoring_text(parcel.get("significance_level")).lower()
        or sanitize_monitoring_text(parcel.get("recommended_significance")).lower()
    )
    att = sanitize_monitoring_text(
        parcel.get("attention_level")
    ).lower() or compute_attention_level(parcel)

    if (
        outcome in ("not_relevant", "lost", "lost_before_review")
        or (adv == "closed" and outcome not in ("saved", "no_longer_threatened"))
    ):
        return "4"
    if adv == "recording_only":
        return "3"
    if att == "act":
        return "2" if (sig == "moderate" and adv != "active_advocacy") else "1"
    if att == "watch":
        return "2"
    if rev == "assessed" or outcome in ("saved", "no_longer_threatened"):
        return "3"
    return ""


def _sanitize_payload_dashes(val: Any) -> Any:
    if isinstance(val, str):
        return sanitize_monitoring_text(val)
    if isinstance(val, list):
        return [_sanitize_payload_dashes(v) for v in val]
    if isinstance(val, dict):
        return {str(k): _sanitize_payload_dashes(v) for k, v in val.items()}
    return val


def compact_history_json_fifo(
    entries: list[dict[str, Any]] | None = None,
    max_chars: int = int(MONITORING_SCHEMA_CONTRACT["safeSheetCellCharLimit"]),
) -> str:
    kept = [
        _sanitize_payload_dashes(e)
        for e in (entries or [])
        if isinstance(e, dict)
    ]
    truncated_count = 0

    def _encode(items: list[dict[str, Any]], dropped: int) -> str:
        obj = (
            {"truncatedCount": dropped, "entries": items}
            if dropped > 0
            else {"entries": items}
        )
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

    serialized = _encode(kept, truncated_count)
    while len(serialized) > max_chars and len(kept) > 1:
        kept.pop(0)
        truncated_count += 1
        serialized = _encode(kept, truncated_count)
    if len(serialized) > max_chars and len(kept) == 1:
        last = dict(kept[0])
        if isinstance(last.get("note"), str):
            last["note"] = last["note"][: max(120, max_chars - 512)]
        kept = [last]
        serialized = _encode(kept, truncated_count)
    return serialized


def compact_watchlist_dossier_json(
    dossier_obj: dict[str, Any] | None = None,
    max_chars: int = int(MONITORING_SCHEMA_CONTRACT["safeSheetCellCharLimit"]),
) -> str:
    """Compact a `Watchlist_Dossiers.dossier_json` payload FIFO below `48,000` chars as valid JSON."""
    clean: dict[str, Any] = (
        _sanitize_payload_dashes(json.loads(json.dumps(dossier_obj)))
        if isinstance(dossier_obj, dict)
        else {}
    )
    serialized = json.dumps(clean, separators=(",", ":"), ensure_ascii=False)
    if len(serialized) <= max_chars:
        return serialized

    if clean.get("photoDataUrl"):
        clean["photoDataUrl"] = ""
        clean["photoDataUrlOmittedInCompactCell"] = True
        serialized = json.dumps(clean, separators=(",", ":"), ensure_ascii=False)
        if len(serialized) <= max_chars:
            return serialized

    if isinstance(clean.get("findingsLog"), list) and clean["findingsLog"]:
        clean["findingsLog"] = []
        serialized = json.dumps(clean, separators=(",", ":"), ensure_ascii=False)
        if len(serialized) <= max_chars:
            return serialized

    dropped_checks = _safe_int(clean.get("truncatedCheckCount"))
    while len(serialized) > max_chars and isinstance(clean.get("checklist"), list):
        target_row: dict[str, Any] | None = None
        max_len = 1
        for row in clean["checklist"]:
            if isinstance(row, dict) and isinstance(row.get("checks"), list) and len(row["checks"]) > max_len:
                max_len = len(row["checks"])
                target_row = row
        if target_row is None:
            break
        target_row["checks"].pop(0)
        dropped_checks += 1
        clean["truncatedCheckCount"] = dropped_checks
        serialized = json.dumps(clean, separators=(",", ":"), ensure_ascii=False)

    if len(serialized) > max_chars:
        for tk in ("notes", "buildingHistory", "phPastActions"):
            if len(serialized) <= max_chars:
                break
            val = clean.get(tk)
            if isinstance(val, str) and len(val) > 200:
                excess = len(serialized) - max_chars + 256
                clean[tk] = val[: max(200, len(val) - excess)]
                serialized = json.dumps(clean, separators=(",", ":"), ensure_ascii=False)

    return serialized


def normalize_parcel_record(
    raw: dict[str, Any],
    *,
    as_of_date_cst: str = "",
    areas_by_id: Any = None,
) -> dict[str, Any]:
    clean_hcad = normalize_hcad13(raw.get("hcad_num") or raw.get("hcad") or "")
    related_hcads = parse_related_hcads([clean_hcad, raw.get("related_hcads") or ""])
    parcel_id = resolve_canonical_parcel_id(
        parcel_id=str(raw.get("parcel_id") or ""),
        campus_group_id=str(raw.get("campus_group_id") or ""),
        hcad_ids=related_hcads,
        app_no=str(raw.get("latest_app_no") or raw.get("app_no") or ""),
        address=str(raw.get("primary_address") or raw.get("address") or ""),
        name=str(raw.get("building_name") or ""),
    )
    area_ids = parse_delimited_list(raw.get("area_ids"))
    area_ctx = resolve_effective_area_context(area_ids, areas_by_id)

    review_status_raw = sanitize_monitoring_text(raw.get("review_status")).lower()
    review_status = (
        review_status_raw if review_status_raw in _VALID_REVIEW_STATUS_SET else "not_reviewed"
    )

    rec_sig_raw = sanitize_monitoring_text(raw.get("recommended_significance")).lower()
    rec_sig = rec_sig_raw if rec_sig_raw in _VALID_SIGNIFICANCE_SET else ""

    conf_sig_raw = sanitize_monitoring_text(raw.get("significance_level")).lower()
    conf_sig = conf_sig_raw if conf_sig_raw in _VALID_SIGNIFICANCE_SET else ""

    bases = [
        b.lower()
        for b in parse_delimited_list(raw.get("significance_basis"))
        if b.lower() in _VALID_BASIS_SET
    ]

    contrib_ov_raw = sanitize_monitoring_text(raw.get("contributing_override")).lower()
    contrib_ov = contrib_ov_raw if contrib_ov_raw in _VALID_CONTRIBUTING_SET else ""

    integrity_raw = sanitize_monitoring_text(raw.get("integrity")).lower()
    integrity = integrity_raw if integrity_raw in _VALID_INTEGRITY_SET else ""

    desig_summary = sanitize_monitoring_text(raw.get("designation_summary") or "")
    prot_raw = sanitize_monitoring_text(raw.get("protection_level")).lower()
    protection_level = (
        prot_raw
        if prot_raw and prot_raw in _VALID_PROTECTION_SET
        else classify_protection_level(desig_summary)
    )

    adv_raw = sanitize_monitoring_text(raw.get("advocacy_status")).lower()
    advocacy_status = adv_raw if adv_raw in _VALID_ADVOCACY_SET else "monitoring"

    out_raw = sanitize_monitoring_text(raw.get("outcome")).lower()
    outcome = out_raw if out_raw in _VALID_OUTCOME_SET else ""

    att_ov_raw = sanitize_monitoring_text(raw.get("attention_override")).lower()
    attention_override = att_ov_raw if att_ov_raw in _VALID_ATTENTION_SET else ""

    threat_stage = max(0, min(6, _safe_int(raw.get("threat_stage"))))
    if advocacy_status == "closed" or outcome in _TERMINAL_OUTCOMES_SET:
        threat_stage = 6

    yr_num = _safe_int(raw.get("year_built"))
    hcad_yr_num = _safe_int(raw.get("hcad_year_built"))
    min_yr = int(MONITORING_SCHEMA_CONTRACT["minStandingYear"])
    max_yr = int(MONITORING_SCHEMA_CONTRACT["maxStandingYear"])

    normalized: dict[str, Any] = {
        "parcel_id": parcel_id,
        "hcad_num": clean_hcad or (related_hcads[0] if related_hcads else ""),
        "campus_group_id": parcel_id
        if parcel_id.startswith("CAMPUS:")
        else sanitize_monitoring_text(raw.get("campus_group_id") or ""),
        "related_hcads": "; ".join(related_hcads),
        "primary_address": sanitize_monitoring_text(
            raw.get("primary_address") or raw.get("address") or ""
        ),
        "perimeter_addresses": "; ".join(parse_delimited_list(raw.get("perimeter_addresses"))),
        "building_name": sanitize_monitoring_text(raw.get("building_name") or ""),
        "year_built": yr_num if min_yr <= yr_num <= max_yr else "",
        "hcad_year_built": hcad_yr_num if min_yr <= hcad_yr_num <= max_yr else "",
        "subdivision_name": sanitize_monitoring_text(raw.get("subdivision_name") or ""),
        "area_ids": "; ".join(area_ids),
        "owner_of_record": sanitize_monitoring_text(raw.get("owner_of_record") or ""),
        "land_use_class": sanitize_monitoring_text(raw.get("land_use_class") or ""),
        "review_status": review_status,
        "recommended_significance": rec_sig,
        "significance_level": conf_sig,
        "significance_basis": "; ".join(bases),
        "contributing_status": "",
        "contributing_override": contrib_ov,
        "integrity": integrity,
        "protection_level": protection_level,
        "designation_summary": desig_summary,
        "threat_stage": threat_stage,
        "signal_flags": "; ".join(parse_delimited_list(raw.get("signal_flags"))),
        "advocacy_status": advocacy_status,
        "outcome": outcome,
        "attention_level": "",
        "attention_override": attention_override,
        "watch_property_id": sanitize_monitoring_text(raw.get("watch_property_id") or ""),
        "added_at": sanitize_monitoring_text(raw.get("added_at") or ""),
        "updated_by": sanitize_monitoring_text(raw.get("updated_by") or ""),
        "updated_at": sanitize_monitoring_text(raw.get("updated_at") or ""),
    }

    normalized["contributing_status"] = compute_contributing_status(normalized, area_ctx)
    normalized["attention_level"] = compute_attention_level(
        normalized,
        as_of_date_cst=as_of_date_cst,
        next_hearing_date_cst=str(raw.get("next_hearing_date_cst") or ""),
        areas_by_id=areas_by_id,
    )
    return normalized


def merge_parcel_and_decision_concurrent(
    *,
    existing_parcel: dict[str, Any] | None = None,
    existing_decision: dict[str, Any] | None = None,
    patch: dict[str, Any] | None = None,
    actor_role: str = "screener",
    actor_label: str = "Preservation Staff",
    expected_updated_at: str = "",
    now_iso: str = "",
    as_of_date_cst: str = "",
    areas_by_id: Any = None,
) -> dict[str, Any]:
    """Column-partitioned row merger matching JS `mergeParcelAndDecisionConcurrent` 1:1."""
    role = sanitize_monitoring_text(actor_role).lower()
    if role not in ("pipeline", "screener", "staff"):
        return {
            "ok": False,
            "error": f"Unauthorized actorRole: {actor_role}",
            "conflict": False,
        }

    patch_obj = patch or {}
    ts = sanitize_monitoring_text(now_iso) or dt.datetime.now(dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    base_parcel = dict(existing_parcel) if isinstance(existing_parcel, dict) else {}
    base_decision = dict(existing_decision) if isinstance(existing_decision, dict) else {}

    if (
        expected_updated_at
        and base_decision.get("decided_at")
        and sanitize_monitoring_text(expected_updated_at)
        != sanitize_monitoring_text(base_decision.get("decided_at"))
        and role != "pipeline"
    ):
        return {
            "ok": False,
            "conflict": True,
            "error": "Parcel decision was updated in another session; reload or merge latest changes.",
            "currentParcel": normalize_parcel_record(
                base_parcel, as_of_date_cst=as_of_date_cst, areas_by_id=areas_by_id
            ),
            "currentDecision": base_decision,
        }

    if role == "pipeline":
        merged_raw = dict(base_parcel)
        if not existing_parcel:
            for k, v in patch_obj.items():
                if v is not None:
                    merged_raw[k] = v
            merged_raw["added_at"] = merged_raw.get("added_at") or ts
        else:
            for col in _PIPELINE_MACHINE_PARCEL_COLS:
                if col in patch_obj and patch_obj[col] is not None:
                    merged_raw[col] = patch_obj[col]
        merged_raw["updated_at"] = ts
        next_parcel = normalize_parcel_record(
            merged_raw,
            as_of_date_cst=as_of_date_cst or to_chicago_calendar_date(ts),
            areas_by_id=areas_by_id,
        )
        return {
            "ok": True,
            "conflict": False,
            "parcel": next_parcel,
            "decision": dict(base_decision) if existing_decision else None,
        }

    for staff_col in _STAFF_ONLY_DECISION_COLS:
        if staff_col in patch_obj and patch_obj[staff_col] is not None and role != "staff":
            return {
                "ok": False,
                "conflict": False,
                "forbiddenField": staff_col,
                "error": f"Role '{role}' cannot modify staff-only field '{staff_col}'.",
            }

    def _pick(key: str, default: str = "") -> str:
        if key in patch_obj and patch_obj[key] is not None:
            return sanitize_monitoring_text(patch_obj[key])
        if key in base_decision and base_decision[key] is not None:
            return sanitize_monitoring_text(base_decision[key])
        if key in base_parcel and base_parcel[key] is not None:
            return sanitize_monitoring_text(base_parcel[key])
        return default

    raw_story = patch_obj.get("story_check_json")
    if isinstance(raw_story, dict):
        story_check_str = json.dumps(
            _sanitize_payload_dashes(raw_story), separators=(",", ":"), ensure_ascii=False
        )
    else:
        story_check_str = _pick("story_check_json", "")

    next_decision: dict[str, Any] = {
        "decision_id": base_decision.get("decision_id")
        or f"DEC:{resolve_canonical_parcel_id(parcel_id=str(patch_obj.get('parcel_id') or base_parcel.get('parcel_id') or ''), campus_group_id=str(patch_obj.get('campus_group_id') or base_parcel.get('campus_group_id') or ''), hcad_ids=[patch_obj.get('hcad_num') or base_parcel.get('hcad_num') or ''], app_no=str(patch_obj.get('latest_app_no') or ''), address=str(patch_obj.get('primary_address') or base_parcel.get('primary_address') or ''))}",
        "parcel_id": str(
            base_decision.get("parcel_id")
            or base_parcel.get("parcel_id")
            or patch_obj.get("parcel_id")
            or ""
        ),
        "hcad_num": normalize_hcad13(
            patch_obj.get("hcad_num")
            or base_decision.get("hcad_num")
            or base_parcel.get("hcad_num")
            or ""
        ),
        "latest_app_no": _pick("latest_app_no", ""),
        "latest_cycle_key": _pick("latest_cycle_key", ""),
        "actor_role": role,
        "review_status": _pick("review_status", "not_reviewed"),
        "recommended_significance": _pick("recommended_significance", ""),
        "significance_level": sanitize_monitoring_text(patch_obj["significance_level"])
        if (role == "staff" and "significance_level" in patch_obj and patch_obj["significance_level"] is not None)
        else sanitize_monitoring_text(
            base_decision.get("significance_level")
            if base_decision.get("significance_level") is not None
            else (base_parcel.get("significance_level") or "")
        ),
        "significance_basis": "; ".join(parse_delimited_list(patch_obj["significance_basis"]))
        if ("significance_basis" in patch_obj and patch_obj["significance_basis"] is not None)
        else _pick("significance_basis", ""),
        "contributing_override": sanitize_monitoring_text(patch_obj["contributing_override"])
        if (role == "staff" and "contributing_override" in patch_obj and patch_obj["contributing_override"] is not None)
        else sanitize_monitoring_text(
            base_decision.get("contributing_override")
            if base_decision.get("contributing_override") is not None
            else (base_parcel.get("contributing_override") or "")
        ),
        "integrity": _pick("integrity", ""),
        "standing_status": _pick("standing_status", ""),
        "advocacy_status": sanitize_monitoring_text(patch_obj["advocacy_status"])
        if (role == "staff" and "advocacy_status" in patch_obj and patch_obj["advocacy_status"] is not None)
        else sanitize_monitoring_text(
            base_decision.get("advocacy_status")
            if base_decision.get("advocacy_status") is not None
            else (base_parcel.get("advocacy_status") or "monitoring")
        ),
        "outcome": sanitize_monitoring_text(patch_obj["outcome"])
        if (role == "staff" and "outcome" in patch_obj and patch_obj["outcome"] is not None)
        else sanitize_monitoring_text(
            base_decision.get("outcome")
            if base_decision.get("outcome") is not None
            else (base_parcel.get("outcome") or "")
        ),
        "attention_override": sanitize_monitoring_text(patch_obj["attention_override"])
        if (role == "staff" and "attention_override" in patch_obj and patch_obj["attention_override"] is not None)
        else sanitize_monitoring_text(
            base_decision.get("attention_override")
            if base_decision.get("attention_override") is not None
            else (base_parcel.get("attention_override") or "")
        ),
        "screening_notes": sanitize_monitoring_text(
            patch_obj["screening_notes"]
            if ("screening_notes" in patch_obj and patch_obj["screening_notes"] is not None)
            else (base_decision.get("screening_notes") or "")
        ),
        "story_check_json": story_check_str,
        "atlas_writeback_summary": sanitize_monitoring_text(
            patch_obj["atlas_writeback_summary"]
        )
        if (role == "staff" and "atlas_writeback_summary" in patch_obj and patch_obj["atlas_writeback_summary"] is not None)
        else sanitize_monitoring_text(base_decision.get("atlas_writeback_summary") or ""),
        "decided_by": sanitize_monitoring_text(actor_label or "Preservation Staff"),
        "decided_at": ts,
        "history_json": "",
    }

    prior_history: list[dict[str, Any]] = []
    if base_decision.get("history_json"):
        try:
            parsed_hist = json.loads(str(base_decision["history_json"]))
            if isinstance(parsed_hist, list):
                prior_history = parsed_hist
            elif isinstance(parsed_hist, dict) and isinstance(parsed_hist.get("entries"), list):
                prior_history = parsed_hist["entries"]
        except Exception:
            prior_history = []
    prior_history.append(
        {
            "at": ts,
            "role": role,
            "by": next_decision["decided_by"],
            "review_status": next_decision["review_status"],
            "recommended_significance": next_decision["recommended_significance"],
            "significance_level": next_decision["significance_level"],
            "advocacy_status": next_decision["advocacy_status"],
            "outcome": next_decision["outcome"],
            "note": next_decision["screening_notes"][:280],
        }
    )
    next_decision["history_json"] = compact_history_json_fifo(prior_history)

    non_decision_patch = {
        k: v
        for k, v in patch_obj.items()
        if v is not None
        and k not in _STAFF_ONLY_DECISION_COLS
        and k not in _SCREENER_DECISION_COLS
    }
    merged_parcel_raw: dict[str, Any] = {
        **baseParcel_clean(base_parcel),
        **non_decision_patch,
        "parcel_id": next_decision["parcel_id"]
        or base_parcel.get("parcel_id")
        or patch_obj.get("parcel_id")
        or "",
        "hcad_num": next_decision["hcad_num"] or base_parcel.get("hcad_num") or "",
        "review_status": next_decision["review_status"],
        "recommended_significance": next_decision["recommended_significance"],
        "significance_level": next_decision["significance_level"],
        "significance_basis": next_decision["significance_basis"],
        "contributing_override": next_decision["contributing_override"],
        "integrity": next_decision["integrity"],
        "advocacy_status": next_decision["advocacy_status"],
        "outcome": next_decision["outcome"],
        "attention_override": next_decision["attention_override"],
        "added_at": base_parcel.get("added_at") or ts,
        "updated_by": next_decision["decided_by"],
        "updated_at": ts,
    }
    next_parcel = normalize_parcel_record(
        merged_parcel_raw,
        as_of_date_cst=as_of_date_cst or to_chicago_calendar_date(ts),
        areas_by_id=areas_by_id,
    )
    next_decision["parcel_id"] = next_parcel["parcel_id"]
    next_decision["decision_id"] = f"DEC:{next_parcel['parcel_id']}"
    return {
        "ok": True,
        "conflict": False,
        "parcel": next_parcel,
        "decision": next_decision,
    }


def baseParcel_clean(d: dict[str, Any]) -> dict[str, Any]:
    return dict(d)
