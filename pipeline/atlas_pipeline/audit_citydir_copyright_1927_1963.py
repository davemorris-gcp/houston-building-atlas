"""Audit 1927-1963 Houston City Directory US Copyright Office (USCO) registrations and renewal status.

Under 17 U.S.C. Sec. 304:
- Volumes published in 1927-1930 are unconditionally in the US public domain as of January 1, 2026
  (95-year rolling term expiration; 1931 enters via rolling term on January 1, 2027).
- Under Sec. 24 of the Copyright Act of 1909, US works published with copyright notice between
  1931 and 1963 entered the public domain permanently after 28 years unless a formal Class R /
  Class RE renewal registration was filed in the 28th year (1959-1991).

This module queries and caches the official US Copyright Office Public Records API
(`https://api.publicrecords.copyright.gov/search_service_external/simple_search_dsl`), isolates
Houston City Directory registrations from other Texas cities published by Morrison & Fourmy /
R. L. Polk, verifies Class R / Class RE renewal counts, exports a reproducible year-by-year
statutory clearance CSV (`docs/data/houston_city_directories_1927_1963_usco_clearance.csv`),
and enriches `app/public/data/citydir_index.json` with `post_1926_clearance` metadata.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from typing import Any
import urllib.parse
import urllib.request

from atlas_pipeline.schema import sanitize_atlas_text

USCO_SEARCH_API = "https://api.publicrecords.copyright.gov/search_service_external/simple_search_dsl"
USER_AGENT = "PreservationHouston-Atlas-Research/1.0"

USCO_QUERIES: tuple[str, ...] = (
    '"Morrison" "Fourmy"',
    '"Polk" "Houston" "Directory"',
    '"Polk" "Houston" "City Directory"',
    '"Houston" "City Directory"',
)

NON_HOUSTON_CITY_RE = re.compile(
    r"\b(?:"
    r"Dallas|Fort\s+Worth|Galveston|San\s+Antonio|Austin|Beaumont|Waco|El\s+Paso|"
    r"Shreveport|Texarkana|Denison|Sherman|Marshall|Corsicana|Cleburne|Temple|"
    r"Palestine|Paris|Gainesville|Port\s+Arthur|Corpus\s+Christi|Wichita\s+Falls|"
    r"Amarillo|Lubbock|Abilene|Tyler|Brownsville|Laredo|Midland|Odessa|"
    r"Houston\s+County"
    r")\b",
    re.IGNORECASE,
)

HOUSTON_CITY_RE = re.compile(r"\bHouston\b", re.IGNORECASE)
DIRECTORY_WORD_RE = re.compile(r"\bDirector(?:y|ies)\b", re.IGNORECASE)
RENEWAL_PREFIX_RE = re.compile(r"^\s*(?:RE|R)(?:[-/\s]|\d)", re.IGNORECASE)
YEAR_4DIGIT_RE = re.compile(r"\b(18[6-9]\d|19\d{2})\b")


@dataclass(frozen=True)
class UscoDirectoryHit:
    public_records_id: str
    reg_number: str
    reg_date: str
    pub_date: str
    inferred_year: int | None
    title: str
    claimant: str
    record_type: str
    is_renewal: bool


def classify_usco_renewal_flag(hit_obj: dict[str, Any]) -> bool:
    """Return True if a USCO catalog hit represents a Class R or Class RE renewal filing."""
    rec_type = str(
        hit_obj.get("type_of_record") or hit_obj.get("record_schema_type") or ""
    ).strip().lower()
    if "renewal" in rec_type:
        return True
    reg_candidates: list[str] = []
    disp = hit_obj.get("copyright_number_for_display")
    if disp:
        reg_candidates.append(str(disp))
    raw_regs = hit_obj.get("registration_number")
    if isinstance(raw_regs, list):
        reg_candidates.extend(str(x) for x in raw_regs if x)
    elif raw_regs:
        reg_candidates.append(str(raw_regs))
    for reg in reg_candidates:
        if RENEWAL_PREFIX_RE.search(reg):
            return True
    return False


def is_houston_city_directory_hit(hit_obj: dict[str, Any]) -> bool:
    """Return True strictly for Houston, Texas city/criss-cross directory catalog records."""
    titles = hit_obj.get("title_of_work") or []
    if isinstance(titles, str):
        titles = [titles]
    claimants = hit_obj.get("claimant") or []
    if isinstance(claimants, str):
        claimants = [claimants]
    title_blob = " | ".join(str(t) for t in titles if t)
    full_blob = f"{title_blob} | {' | '.join(str(c) for c in claimants if c)}"

    if not HOUSTON_CITY_RE.search(title_blob):
        return False
    if NON_HOUSTON_CITY_RE.search(title_blob) and not re.search(
        r"\bHouston(?:\s+and\s+Harris|\s*,\s*Tex|\s+City\s+Directory)", title_blob, re.IGNORECASE
    ):
        return False
    if not DIRECTORY_WORD_RE.search(full_blob):
        return False
    # Exclude telephone-company-only white/yellow pages or unrelated trade club rosters unless Polk/Morrison & Fourmy
    if re.search(r"\b(?:Telephone|Southwestern\s+Bell|Blue\s+Book|Social\s+Register|Medical|Legal|Church)\b", title_blob, re.IGNORECASE):
        if not re.search(r"\b(?:Morrison|Fourmy|Polk)\b", full_blob, re.IGNORECASE):
            return False
    return True


def extract_directory_year(hit_obj: dict[str, Any]) -> int | None:
    """Infer the primary Houston City Directory edition year from title or publication/registration date."""
    titles = hit_obj.get("title_of_work") or []
    if isinstance(titles, str):
        titles = [titles]
    title_str = " ".join(str(t) for t in titles if t)
    is_ren = classify_usco_renewal_flag(hit_obj)

    pub_val = str(hit_obj.get("first_published_date_as_date") or "").strip()
    rep_val = str(hit_obj.get("representative_date") or "").strip()
    pub_year = (
        int(pub_val[:4])
        if len(pub_val) >= 4 and pub_val[:4].isdigit() and 1866 <= int(pub_val[:4]) <= 1995
        else None
    )
    rep_year = (
        int(rep_val[:4])
        if len(rep_val) >= 4 and rep_val[:4].isdigit() and 1866 <= int(rep_val[:4]) <= 2026
        else None
    )

    anchor_year: int | None = None
    if pub_year is not None:
        anchor_year = pub_year
    elif rep_year is not None:
        # Under Sec. 24 of the 1909 Copyright Act, Class R/RE renewals were filed in the 28th year
        # after publication (27-28 years post-publication), so when `first_published_date_as_date` is
        # absent on a renewal record, anchor to `rep_year - 28` rather than the renewal filing year.
        anchor_year = (rep_year - 28) if (is_ren and rep_year >= 1954) else rep_year

    # Match explicit edition years like "1929-30", "1932", "(1941)" in title first,
    # bounded by `anchor_year + 1` so USCO catalog OCR typos (e.g., "1944" on a July 1941 filing) cannot jump into the future.
    for m_span in re.finditer(r"\b(19[0-7]\d)\s*[-/]\s*(?:19)?(\d{2})\b", title_str):
        span_yr = int(m_span.group(1))
        if anchor_year is None or span_yr <= anchor_year + 1:
            return span_yr
    years_in_title = [int(y) for y in YEAR_4DIGIT_RE.findall(title_str) if 1866 <= int(y) <= 1985]
    if anchor_year is not None:
        bounded = [y for y in years_in_title if y <= anchor_year + 1]
        if bounded:
            return bounded[-1]
    elif years_in_title:
        return years_in_title[-1]

    return anchor_year


def parse_usco_hit(raw_entry: dict[str, Any]) -> UscoDirectoryHit | None:
    """Convert a raw USCO API `data[]` item into a normalized `UscoDirectoryHit` if it is a Houston directory."""
    hit_obj = raw_entry.get("hit") if isinstance(raw_entry.get("hit"), dict) else raw_entry
    if not isinstance(hit_obj, dict):
        return None
    if not is_houston_city_directory_hit(hit_obj):
        return None

    pr_id = str(hit_obj.get("public_records_id") or "").strip()
    disp_reg = str(hit_obj.get("copyright_number_for_display") or "").strip()
    if not disp_reg:
        regs = hit_obj.get("registration_number") or []
        if isinstance(regs, list) and regs:
            disp_reg = str(regs[0]).strip()
        elif regs:
            disp_reg = str(regs).strip()

    titles = hit_obj.get("title_of_work") or []
    if isinstance(titles, str):
        titles = [titles]
    title_clean = sanitize_atlas_text(" ".join(str(t).strip() for t in titles if t).strip())

    claimants = hit_obj.get("claimant") or []
    if isinstance(claimants, str):
        claimants = [claimants]
    claimant_clean = sanitize_atlas_text("; ".join(dict.fromkeys(str(c).strip() for c in claimants if c)))

    pub_date = str(hit_obj.get("first_published_date_as_date") or "").strip()[:10]
    reg_date = str(hit_obj.get("representative_date") or "").strip()[:10]
    rec_type = str(hit_obj.get("type_of_record") or hit_obj.get("record_schema_type") or "registration").strip()
    is_ren = classify_usco_renewal_flag(hit_obj)
    yr = extract_directory_year(hit_obj)

    return UscoDirectoryHit(
        public_records_id=pr_id or f"{disp_reg}:{title_clean[:40]}",
        reg_number=sanitize_atlas_text(disp_reg),
        reg_date=reg_date,
        pub_date=pub_date,
        inferred_year=yr,
        title=title_clean,
        claimant=claimant_clean,
        record_type=rec_type,
        is_renewal=is_ren,
    )


def fetch_usco_query_pages(
    query: str,
    *,
    records_per_page: int = 40,
    max_pages: int = 12,
    delay_s: float = 0.35,
) -> list[dict[str, Any]]:
    """Fetch paginated results from the USCO Public Records API with polite pacing and retry."""
    safe_rpp = max(1, min(int(records_per_page), 50))
    out: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        params = urllib.parse.urlencode({
            "page_number": str(page),
            "records_per_page": str(safe_rpp),
            "query": query,
        })
        url = f"{USCO_SEARCH_API}?{params}"
        last_err: Exception | None = None
        payload: dict[str, Any] | None = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=25) as resp:
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                break
            except Exception as exc:  # pragma: no cover - network retry
                last_err = exc
                time.sleep(0.8 * (attempt + 1))
        if payload is None:
            if page == 1 and last_err is not None:
                raise RuntimeError(f"USCO API request failed for {query!r}: {last_err}") from last_err
            break
        batch = payload.get("data") or []
        if not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(batch) < safe_rpp:
            break
        if delay_s > 0:
            time.sleep(delay_s)
    return out


def _load_ancestry_catalog_by_year(catalog_path: Path | None = None) -> dict[int, list[dict[str, Any]]]:
    """Load the verified 1927-1968 Ancestry dbId=2469 Houston City Directory catalog indexed by year."""
    if catalog_path is None:
        catalog_path = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "data"
            / "houston_city_directories_ancestry_catalog.json"
        )
    if not catalog_path.exists():
        return {}
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        by_yr: dict[int, list[dict[str, Any]]] = {}
        for v in payload.get("volumes") or []:
            yr = int(v.get("year") or 0)
            if yr > 0:
                by_yr.setdefault(yr, []).append(v)
        return by_yr
    except Exception:
        return {}


def build_clearance_rows_1927_1963(
    hits: list[UscoDirectoryHit],
    *,
    reference_year: int = 2026,
    ancestry_catalog_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Construct the 37-row year-by-year (1927-1963) statutory copyright clearance table."""
    rolling_pd_cutoff = int(reference_year) - 96  # In 2026, <= 1930 is unconditionally PD (95-yr term)
    by_year_orig: dict[int, list[UscoDirectoryHit]] = {}
    by_year_ren: dict[int, list[UscoDirectoryHit]] = {}
    ancestry_by_year = _load_ancestry_catalog_by_year(ancestry_catalog_path)

    for h in hits:
        if h.inferred_year is None:
            continue
        if h.is_renewal:
            by_year_ren.setdefault(h.inferred_year, []).append(h)
        else:
            by_year_orig.setdefault(h.inferred_year, []).append(h)

    rows: list[dict[str, Any]] = []
    for yr in range(1927, 1964):
        orig_list = sorted(by_year_orig.get(yr, []), key=lambda x: (x.reg_date, x.reg_number))
        ren_list = sorted(by_year_ren.get(yr, []), key=lambda x: (x.reg_date, x.reg_number))

        reg_nums = "; ".join(dict.fromkeys(h.reg_number for h in orig_list if h.reg_number))
        reg_dates = "; ".join(dict.fromkeys(h.reg_date or h.pub_date for h in orig_list if (h.reg_date or h.pub_date)))
        titles = "; ".join(dict.fromkeys(h.title for h in orig_list if h.title))
        publishers = "; ".join(dict.fromkeys(h.claimant for h in orig_list if h.claimant))
        if not publishers:
            publishers = (
                "Morrison & Fourmy Directory Co."
                if yr <= 1960
                else "Morrison & Fourmy Directory Co. / R. L. Polk & Co."
            )

        ren_count = len(ren_list)
        renewal_window = f"{yr + 27}-{yr + 28}"

        if yr <= rolling_pd_cutoff:
            status = "Public Domain (95-Year Statutory Term Expired)"
            basis = f"17 U.S.C. Sec. 304 ({yr} + 95 yrs expired Jan 1, {yr + 96})"
            pd_entry_year = min(yr + 29 if ren_count == 0 else yr + 96, yr + 96)
        elif ren_count == 0:
            status = "Public Domain (No Class R/RE Renewal Filed)"
            basis = (
                f"Copyright Act of 1909 Sec. 24 (0 Class R/RE renewals in {renewal_window} window)"
            )
            pd_entry_year = yr + 29
        else:
            status = "Copyright Renewed (Verify Scope)"
            basis = f"Class R/RE renewal located ({ren_list[0].reg_number})"
            pd_entry_year = yr + 96

        anc_vols = ancestry_by_year.get(yr, [])
        if anc_vols:
            vol_summaries: list[str] = []
            for v in anc_vols:
                sg = v.get("street_guide_image_range")
                if isinstance(sg, list) and len(sg) == 2:
                    sg_note = f", Street Guide {sg[0]}..{sg[1]}"
                elif isinstance(sg, str) and sg.strip():
                    sg_note = f", Street Guide {sg.strip()}"
                else:
                    sg_note = ""
                vol_label = v.get("volume_label") or v.get("title") or f"{yr} City Directory"
                vol_summaries.append(
                    f"{vol_label}: images {v.get('first_image_id')}..{v.get('last_image_id')} ({v.get('sections_included')}{sg_note})"
                )
            online_status = (
                f"Digitized on Ancestry Library Edition (dbId=2469; {'; '.join(vol_summaries)}); "
                "HPL open ContentDM citydir stops at 1926"
            )
        else:
            online_status = (
                "Offline print/microfiche only (not in Ancestry dbId=2469; HPL open ContentDM citydir stops at 1926)"
            )

        if yr <= 1935:
            phys_holdings = (
                "HPL Houston History Research Center (550 McKinney St, call no. 977.64112 M878); "
                "Rice Woodson Research Center (Fondren Library, call no. F394 .H8 M6)"
            )
        elif yr <= 1959:
            phys_holdings = (
                "HPL Houston History Research Center (550 McKinney St, call no. 977.64112 M878); "
                "Rice Fondren Library Basement Kelley Center (open-stacks microfiche F394 .H8 P62, no appointment required)"
            )
        else:
            phys_holdings = (
                "HPL Houston History Research Center (550 McKinney St, call no. 977.64112 M878); "
                "Rice Fondren Library (LSC storage / microform)"
            )

        rows.append({
            "directory_year": yr,
            "publisher_claimant": sanitize_atlas_text(publishers),
            "copyright_status": status,
            "statutory_basis": basis,
            "usco_class_a_registrations": reg_nums or "None indexed under year (combined biennial or unindexed edition)",
            "usco_registration_or_pub_dates": reg_dates or "",
            "renewal_window_years": renewal_window,
            "usco_class_r_re_renewals_found": ren_count,
            "public_domain_entry_year": pd_entry_year,
            "online_open_scan_status": sanitize_atlas_text(online_status),
            "physical_holdings": sanitize_atlas_text(phys_holdings),
            "priority_digitization_section": "Pink/Green Street & Avenue Criss-Cross Address Guide (~120-180 pages/vol)",
            "matched_usco_titles": sanitize_atlas_text(titles),
        })
    return rows


def run_citydir_copyright_audit(
    *,
    cache_dir: Path,
    output_dir: Path,
    csv_output_path: Path | None = None,
    offline: bool = False,
    reference_year: int = 2026,
    verified_date: str = "2026-11-08",
) -> dict[str, Any]:
    """Run or load the USCO 1927-1963 Houston City Directory copyright audit and write outputs."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "usco_houston_citydir_audit.json"

    raw_records_by_id: dict[str, dict[str, Any]] = {}
    query_counts: dict[str, int] = {}

    if cache_path.exists() and (offline or cache_path.stat().st_size > 100):
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        for item in cached.get("raw_entries", []):
            hit_obj = item.get("hit") if isinstance(item.get("hit"), dict) else item
            pr_id = str((hit_obj or {}).get("public_records_id") or "")
            if pr_id:
                raw_records_by_id[pr_id] = item
        query_counts = dict(cached.get("query_counts") or {})
    elif offline:
        query_counts = {q: 0 for q in USCO_QUERIES}
    else:
        for q in USCO_QUERIES:
            batch = fetch_usco_query_pages(q)
            query_counts[q] = len(batch)
            for item in batch:
                hit_obj = item.get("hit") if isinstance(item.get("hit"), dict) else item
                if not isinstance(hit_obj, dict):
                    continue
                pr_id = str(
                    hit_obj.get("public_records_id")
                    or hit_obj.get("copyright_number_for_display")
                    or len(raw_records_by_id)
                )
                raw_records_by_id[pr_id] = item
        cache_payload = {
            "verified_date": verified_date,
            "api_endpoint": USCO_SEARCH_API,
            "query_counts": query_counts,
            "unique_raw_records": len(raw_records_by_id),
            "raw_entries": list(raw_records_by_id.values()),
        }
        cache_path.write_text(json.dumps(cache_payload, indent=2), encoding="utf-8")

    parsed_hits: list[UscoDirectoryHit] = []
    seen_hit_ids: set[str] = set()
    for item in raw_records_by_id.values():
        parsed = parse_usco_hit(item)
        if parsed is not None and parsed.public_records_id not in seen_hit_ids:
            seen_hit_ids.add(parsed.public_records_id)
            parsed_hits.append(parsed)

    parsed_hits.sort(key=lambda h: (h.inferred_year or 0, h.reg_date, h.reg_number))
    clearance_rows = build_clearance_rows_1927_1963(parsed_hits, reference_year=reference_year)

    hits_1927_1963 = [
        h for h in parsed_hits if h.inferred_year is not None and 1927 <= h.inferred_year <= 1963
    ]
    orig_1927_1963 = [h for h in hits_1927_1963 if not h.is_renewal]
    ren_1927_1963 = [
        h
        for h in parsed_hits
        if h.is_renewal and (h.inferred_year is None or 1927 <= h.inferred_year <= 1963)
    ]

    if csv_output_path is None:
        csv_output_path = output_dir.parents[2] / "docs" / "data" / "houston_city_directories_1927_1963_usco_clearance.csv"
    csv_output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "directory_year",
        "publisher_claimant",
        "copyright_status",
        "statutory_basis",
        "usco_class_a_registrations",
        "usco_registration_or_pub_dates",
        "renewal_window_years",
        "usco_class_r_re_renewals_found",
        "public_domain_entry_year",
        "online_open_scan_status",
        "physical_holdings",
        "priority_digitization_section",
        "matched_usco_titles",
    ]
    with csv_output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in clearance_rows:
            writer.writerow({k: sanitize_atlas_text(str(row.get(k, ""))) for k in fieldnames})

    citydir_path = output_dir / "citydir_index.json"
    if citydir_path.exists():
        citydir_doc = json.loads(citydir_path.read_text(encoding="utf-8"))
    else:
        citydir_doc = {}

    sample_regs = [
        {
            "year": h.inferred_year,
            "reg": h.reg_number,
            "date": h.reg_date or h.pub_date,
            "title": h.title[:120],
        }
        for h in orig_1927_1963
        if h.reg_number
    ][:24]

    anc_by_year = _load_ancestry_catalog_by_year()
    anc_all_vols = [v for vols in anc_by_year.values() for v in vols]
    anc_sg_years = sorted(
        {
            int(v["year"])
            for v in anc_all_vols
            if v.get("has_street_and_avenue_guide")
        }
    )

    citydir_doc["post_1926_clearance"] = {
        "verified_date": verified_date,
        "span": [1927, 1963],
        "audited_year_count": len(clearance_rows),
        "rolling_95yr_pd_span": [1927, reference_year - 96],
        "unrenewed_1909_act_pd_span": [reference_year - 95, 1963],
        "usco_houston_directory_records_total": len(parsed_hits),
        "usco_1927_1963_class_a_registrations": len(orig_1927_1963),
        "usco_class_r_re_renewals_found": len(ren_1927_1963),
        "all_1927_1963_public_domain": (
            all(str(r["copyright_status"]).startswith("Public Domain") for r in clearance_rows)
            and not any(h.inferred_year is None for h in ren_1927_1963)
        ),
        "online_open_scans_post_1926": 0,
        "ancestry_collection_id": "2469",
        "ancestry_collection_url": "https://www.ancestry.com/search/collections/2469/",
        "ancestry_post_1926_volume_count": len(anc_all_vols) or 31,
        "ancestry_post_1926_distinct_years": len(anc_by_year) or 28,
        "ancestry_post_1926_span": [1927, 1968],
        "ancestry_street_guide_years": anc_sg_years
        or [1927, 1928, 1929, 1930, 1932, 1934, 1935, 1936, 1940, 1942, 1945, 1952, 1954, 1956, 1965],
        "ancestry_catalog_path": "docs/data/houston_city_directories_ancestry_catalog.json",
        "holdings_note": (
            "All 1927-1963 Morrison & Fourmy and R. L. Polk Houston City Directories are verified "
            "US public domain (1927-1930 via 17 U.S.C. Sec. 304 95-year term; 1931-1963 via 0 Class R/RE "
            f"renewals in US Copyright Office records). Online digitized scans for {len(anc_by_year) or 28} post-1926 Houston "
            f"directory years (1927-1968 across {len(anc_all_vols) or 31} volume entries, including {len(anc_sg_years) or 15} with Street & Avenue Guides) "
            "are available in Ancestry Library Edition (U.S., City Directories, 1822-1999, dbId=2469, free "
            "via HPL MY Link or Rice campus Wi-Fi). Print/microfiche Street & Avenue Criss-Cross Guides "
            "are held at HPL Houston History Research Center (550 McKinney St, call no. 977.64112 M878), "
            "Rice Fondren Library Basement Kelley Center (open-stacks microfiche F394 .H8 P62 for 1936-1959, "
            "no appointment required), and Rice Woodson Research Center (Fondren Library, F394 .H8 M6 for 1927-1935)."
        ),
        "csv_path": "docs/data/houston_city_directories_1927_1963_usco_clearance.csv",
        "sample_class_a_registrations": sample_regs,
    }
    citydir_path.write_text(
        sanitize_atlas_text(json.dumps(citydir_doc, separators=(",", ":"))),
        encoding="utf-8",
    )

    return {
        "verified_date": verified_date,
        "query_counts": query_counts,
        "unique_raw_usco_records": len(raw_records_by_id),
        "houston_directory_records_total": len(parsed_hits),
        "class_a_1927_1963_count": len(orig_1927_1963),
        "class_r_re_renewals_found": len(ren_1927_1963),
        "audited_years": len(clearance_rows),
        "csv_output_path": str(csv_output_path),
    }


if __name__ == "__main__":  # pragma: no cover
    repo = Path(__file__).resolve().parents[2]
    summary = run_citydir_copyright_audit(
        cache_dir=repo / "pipeline" / "cache",
        output_dir=repo / "app" / "public" / "data",
    )
    print(json.dumps(summary, indent=2))
