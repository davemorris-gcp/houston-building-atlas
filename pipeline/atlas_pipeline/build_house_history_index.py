"""Build compact client-side archival lookup index for the House History Research Kit.

Exports `app/public/data/citydir_index.json` from `pipeline/cache/citydir_cache.sqlite`,
combining:
  1. All digitized Houston City Directory volumes (1866-1926) hosted in HPL ContentDM (`citydir`),
  2. Pre-audited HCAD parcel sightings (earliest directory year, matched years, exact scanned
     page pointer, and sanitized archival OCR excerpt), and
  3. Authoritative historic Houston street name aliases and pre-1915 numbering shifts so the
     Property Inspector can offer 1-click historic alias searches in City Directories and archives.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DB = REPO_ROOT / "pipeline" / "cache" / "citydir_cache.sqlite"
ANCESTRY_MATCHES_JSON = REPO_ROOT / "pipeline" / "cache" / "ancestry_houston_citydir_1927_1968_matches.json"
OUTPUT_JSON = REPO_ROOT / "app" / "public" / "data" / "citydir_index.json"

# Documented Houston historic street renamings relevant to 1866-1951 Sanborn & City Directory research.
HISTORIC_STREET_ALIASES: dict[str, list[dict[str, str]]] = {
    "EMANCIPATION": [
        {
            "former": "Dowling",
            "era": "1892-2017",
            "note": "Renamed Emancipation Ave in 2017; listed as Dowling St (and East Broadway in early 1870s-1890s) in historic directories and Sanborn maps.",
        },
        {
            "former": "East Broadway",
            "era": "Pre-1895",
            "note": "Early Third Ward maps and directories also referred to upper Dowling / Emancipation as East Broadway.",
        },
    ],
    "DOWLING": [
        {
            "former": "Emancipation",
            "era": "2017-Present",
            "note": "Renamed Emancipation Ave in 2017 (some HCAD tax parcels still carry the pre-2017 Dowling St situs).",
        },
        {
            "former": "East Broadway",
            "era": "Pre-1895",
            "note": "Early Third Ward maps and directories also referred to upper Dowling / Emancipation as East Broadway.",
        },
    ],
    "HEIGHTS": [
        {
            "former": "Boulevard",
            "era": "1892-1918",
            "note": "Listed simply as 'Boulevard' (with 3-digit Houston Heights house numbers) prior to Houston's 1918 annexation of Houston Heights.",
        }
    ],
    "JENSEN": [
        {
            "former": "Odin",
            "era": "Pre-1925",
            "note": "Lower Jensen Dr (Fifth Ward) appears as Odin Ave and Clark St in pre-1925 directories and 1907 Sanborn Vol. 1.",
        },
        {
            "former": "Clark",
            "era": "Pre-1928",
            "note": "Northward continuation of Jensen Dr was historically indexed as Clark St.",
        },
    ],
    "W DALLAS": [
        {
            "former": "San Felipe",
            "era": "Pre-1950",
            "note": "West Dallas St through Fourth Ward / Freedmen's Town was historically Old San Felipe Rd (and New San Felipe) on 1885-1925 maps.",
        }
    ],
    "DALLAS": [
        {
            "former": "San Felipe",
            "era": "Pre-1950",
            "note": "West of Downtown through Fourth Ward / Freedmen's Town, W. Dallas St was historically San Felipe Rd.",
        }
    ],
    "NAVIGATION": [
        {
            "former": "German",
            "era": "Pre-1918",
            "note": "Inner East End / Second Ward segments along Buffalo Bayou appear as German St (renamed Canal/Navigation era) and Harrisburg Rd.",
        },
        {
            "former": "Harrisburg",
            "era": "1866-1926",
            "note": "East End thoroughfare corridors frequently cross-reference Harrisburg Rd.",
        },
    ],
    "CANAL": [
        {
            "former": "German",
            "era": "Pre-1918",
            "note": "Canal St in Second Ward was listed as German St until World War I era street renamings (1918).",
        }
    ],
    "LOCKWOOD": [
        {
            "former": "mile",
            "era": "Pre-1925",
            "note": "Early Fifth Ward / East End tracts prior to Lockwood Dr grading were indexed by cross-street or railroad addition.",
        }
    ],
    "MONTROSE": [
        {
            "former": "Montrose Boulevard",
            "era": "1911-1926",
            "note": "Platted in 1911; search both 'Montrose' and 'Montrose Boul' in 1911-1926 directories.",
        }
    ],
    "ALLEN": [
        {
            "former": "Buffalo",
            "era": "Pre-1925",
            "note": "Allen Parkway corridor was historically Buffalo Bayou Drive / San Felipe outskirts.",
        }
    ],
    "MEMORIAL": [
        {
            "former": "Buffalo",
            "era": "Pre-1930",
            "note": "Memorial Dr corridor west of Downtown developed along Buffalo Dr / Washington Rd.",
        }
    ],
    "LAWNDALE": [
        {
            "former": "Telephone",
            "era": "Pre-1926",
            "note": "Early Country Club / East End parcels near Lawndale also reference Telephone Rd or Harrisburg additions.",
        }
    ],
    "ST JOSEPH": [
        {
            "former": "Calhoun",
            "era": "Pre-1960",
            "note": "St. Joseph Pkwy downtown was historically Calhoun Ave on 1885-1951 Sanborn maps.",
        }
    ],
    "I-45": [
        {
            "former": "Pierce",
            "era": "Pre-1960",
            "note": "Elevated downtown freeway right-of-way followed Pierce Ave / Heiner St.",
        }
    ],
    "CHARTRES": [
        {
            "former": "Chartres",
            "era": "1866-1926",
            "note": "Pre-1912 directories occasionally used block descriptions ('bet Congress and Preston') alongside 3- and 4-digit numbers.",
        }
    ],
}


def sanitize_text(text: str) -> str:
    """Remove em-dashes and normalize whitespace for clean UI display."""
    if not text:
        return ""
    cleaned = text.replace("\u2014", " - ").replace("\u2013", "-")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_page_pointer(source_url: str) -> int | None:
    if not source_url:
        return None
    m = re.search(r"/id/(\d+)", source_url)
    return int(m.group(1)) if m else None


def extract_concise_citation(source_citation: str) -> str:
    """Keep the directory title, page number, and quoted OCR snippet, trimming trailing boilerplate."""
    if not source_citation:
        return ""
    cleaned = sanitize_text(source_citation)
    # Split off trailing " - verified in ..." summary since matched years are stored in `y`
    parts = cleaned.split(' - verified in ')
    head = parts[0].strip()
    if len(head) > 220:
        head = head[:217].rstrip() + "..."
    return head


_OCC_ABBREV_EXPANSIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bslsldy\b", re.IGNORECASE), "Saleslady"),
    (re.compile(r"\bslsmn\b", re.IGNORECASE), "Salesman"),
    (re.compile(r"\bcarp\b", re.IGNORECASE), "Carpenter"),
    (re.compile(r"\btrav\b", re.IGNORECASE), "Commercial Traveler"),
    (re.compile(r"\bshoemkr\b", re.IGNORECASE), "Shoemaker"),
    (re.compile(r"\bblksmith\b", re.IGNORECASE), "Blacksmith"),
    (re.compile(r"\bmach\b", re.IGNORECASE), "Machinist"),
    (re.compile(r"\bclk\b", re.IGNORECASE), "Clerk"),
    (re.compile(r"\bbkpr\b", re.IGNORECASE), "Bookkeeper"),
    (re.compile(r"\blab\b", re.IGNORECASE), "Laborer"),
    (re.compile(r"\beng\b", re.IGNORECASE), "Engineer"),
    (re.compile(r"\bcond\b", re.IGNORECASE), "Conductor"),
    (re.compile(r"\bwid\b", re.IGNORECASE), "Widow"),
)


def extract_occupants_from_citydir_citation(
    source_citation: str,
    earliest_year: int,
    address: str = "",
) -> list[list]:
    """Parse a Houston City Directory OCR snippet into `[[year, name, role_or_trade], ...]` tuples (`UT-4`)."""
    if not source_citation or not earliest_year or int(earliest_year) <= 0:
        return []
    yr = int(earliest_year)
    cleaned = sanitize_text(source_citation)
    m_quote = re.search(r':\s*"([^"]+)"', cleaned)
    snippet = m_quote.group(1).strip() if m_quote else cleaned
    if not snippet:
        return []

    st_num = ""
    if address:
        m_num = re.match(r"^\s*(\d+)", str(address))
        if m_num:
            st_num = m_num.group(1)

    target_seg = snippet
    if st_num and st_num in snippet:
        idx = snippet.find(st_num)
        before = snippet[max(0, idx - 95) : idx].strip()
        if before:
            # Take text after the last sentence boundary (`r. same.`, `r. <num>`, or period)
            parts_b = re.split(r"(?:\br\.\s*same\.?|\b(?:r|h|res|bds)\.?\s+\d+[^.]*\.|\.\s+)", before)
            cand = parts_b[-1].strip(" ,;:-") if parts_b else before.strip(" ,;:-")
            if len(cand) >= 4:
                target_seg = cand

    # Match `<Surname> <Given>[, <trade/role>]`
    m_person = re.search(
        r"([A-Z][a-zA-Z'’-]{1,22}\s+(?:[A-Z][a-zA-Z.]*\s*){1,2}(?:\([cC]\))?)(?:\s*,\s*([^,0-9]{2,45}))?",
        target_seg,
    )
    if m_person:
        raw_name = re.sub(r"\s+", " ", m_person.group(1)).strip(" ,;.")
        raw_role = re.sub(r"\s+", " ", (m_person.group(2) or "")).strip(" ,;.")
        if not raw_role:
            after_name = target_seg[m_person.end(1) :].strip(" ,;.")
            raw_role = re.sub(r"\b(?:r|h|res|bds)\b.*$", "", after_name, flags=re.IGNORECASE).strip(" ,;.")
        for rx, rep in _OCC_ABBREV_EXPANSIONS:
            raw_role = rx.sub(rep, raw_role)
        role_out = raw_role[:64] if raw_role else "City Directory Resident"
        if len(raw_name) >= 3 and raw_name.lower() not in ("houston city", "same", "cor"):
            return [[yr, sanitize_text(raw_name)[:64], sanitize_text(role_out)]]

    short_excerpt = re.sub(r"\s+", " ", target_seg).strip(" ,;.")[:64]
    if len(short_excerpt) >= 4:
        return [[yr, sanitize_text(short_excerpt), "City Directory Listing"]]
    return []


def build_index(
    *,
    cache_db: Path = CACHE_DB,
    overlays_json: Path | None = None,
    overrides_json: Path | None = None,
    ancestry_matches_json: Path | None = None,
) -> dict:
    if not cache_db.exists():
        raise FileNotFoundError(f"Missing city directory SQLite cache: {cache_db}")

    ovl_path = overlays_json if overlays_json is not None else (REPO_ROOT / "app" / "public" / "data" / "overlays.json")
    ovr_path = overrides_json if overrides_json is not None else (REPO_ROOT / "app" / "public" / "data" / "curated_overrides.json")
    anc_path = (
        ancestry_matches_json
        if ancestry_matches_json is not None
        else (ANCESTRY_MATCHES_JSON if cache_db == CACHE_DB else None)
    )

    red_book_by_hcad: dict[str, list[list]] = {}
    if ovl_path.exists():
        try:
            ovl_doc = json.loads(ovl_path.read_text(encoding="utf-8"))
            rb_feats = (ovl_doc.get("red_book_1915") or {}).get("features") or []
            for feat in rb_feats:
                props = feat.get("properties") or {}
                h = re.sub(r"\D", "", str(props.get("hcad_num") or ""))
                if len(h) != 13:
                    continue
                nm = sanitize_text(str(props.get("person_name") or props.get("name") or "")).strip()
                if not nm:
                    continue
                occ = sanitize_text(
                    str(
                        props.get("occupation")
                        or props.get("business_type")
                        or props.get("category_label")
                        or "1915 Red Book Proprietor / Resident"
                    )
                ).strip()
                pg = sanitize_text(str(props.get("red_book_page") or "")).strip()
                role_lbl = f"{occ} (1915 Red Book {pg})".strip() if pg else f"{occ} (1915 Red Book)"
                tup = [1915, nm[:72], role_lbl[:80]]
                bucket = red_book_by_hcad.setdefault(h, [])
                if tup not in bucket and len(bucket) < 3:
                    bucket.append(tup)
        except Exception:
            pass

    contract_by_hcad: dict[str, list[list]] = {}
    ovr_by_hcad: dict[str, dict] = {}
    if ovr_path.exists():
        try:
            ovr_doc = json.loads(ovr_path.read_text(encoding="utf-8"))
            ovr_map = ovr_doc.get("overrides") if isinstance(ovr_doc.get("overrides"), dict) else ovr_doc
            if isinstance(ovr_map, dict):
                for k, ov in ovr_map.items():
                    if not isinstance(ov, dict):
                        continue
                    if "#" not in str(k):
                        hc_ov = re.sub(r"\D", "", str(ov.get("hcad_num") or k))
                        if len(hc_ov) == 13:
                            ovr_by_hcad[hc_ov] = ov
                    hc = ov.get("hist_contract")
                    if not isinstance(hc, dict):
                        continue
                    h = re.sub(r"\D", "", str(ov.get("hcad_num") or str(k).split("#")[0]))
                    if len(h) != 13:
                        continue
                    c_yr = int(hc.get("year") or ov.get("year_built") or 0)
                    c_own = sanitize_text(str(hc.get("owner") or "")).strip()
                    if 1836 <= c_yr <= 1965 and c_own:
                        arch = sanitize_text(str(hc.get("architect") or "")).strip()
                        role_s = f"Commissioning Owner (Architect: {arch})" if arch else "Commissioning Owner (Contract Record)"
                        tup = [c_yr, c_own[:72], role_s[:80]]
                        bucket = contract_by_hcad.setdefault(h, [])
                        if tup not in bucket and len(bucket) < 2:
                            bucket.append(tup)
        except Exception as exc:
            print(f"[build_house_history_index] Warning parsing {ovr_path}: {exc}")

    conn = sqlite3.connect(str(cache_db))
    cur = conn.cursor()

    volumes = []
    seen_years: set[int] = set()
    for parent_pointer, year, title in cur.execute(
        "SELECT parent_pointer, year, title FROM volume_years ORDER BY year ASC, parent_pointer ASC"
    ):
        clean_title = sanitize_text(title)
        # Extract year label such as "1867-68" or "1920-21" from title if present
        m_label = re.search(r"(\d{4}(?:-\d{2,4})?)$", clean_title)
        label = m_label.group(1) if m_label else str(year)
        is_primary_for_year = year not in seen_years
        seen_years.add(year)
        volumes.append(
            {
                "year": int(year),
                "label": label,
                "pointer": int(parent_pointer),
                "title": clean_title,
                "primary": is_primary_for_year,
            }
        )

    by_hcad: dict[str, dict] = {}
    absent_pre1926: list[str] = []

    for hcad_num, fjson in cur.execute(
        "SELECT hcad_num, finding_json FROM audit_results ORDER BY hcad_num ASC"
    ):
        hcad_clean = str(hcad_num or "").strip()
        if not hcad_clean:
            continue
        data = json.loads(fjson)
        earliest = data.get("earliest_dir_year")
        matched = data.get("matched_dir_years") or []
        classification = data.get("classification") or ""

        if earliest:
            entry: dict = {
                "e": int(earliest),
                "y": [int(y) for y in matched],
            }
            ptr = extract_page_pointer(data.get("source_url") or "")
            if ptr:
                entry["p"] = ptr
            cite = extract_concise_citation(data.get("source_citation") or "")
            if cite:
                entry["c"] = cite
            if classification == "EARLIER_THAN_HCAD":
                entry["f"] = "earlier"
            elif classification == "UNDATED_IN_HCAD_FOUND_IN_DIRECTORY":
                entry["f"] = "undated_hcad"
            else:
                entry["f"] = "confirmed"

            occ_rows: list[list] = []
            for occ_t in extract_occupants_from_citydir_citation(
                data.get("source_citation") or "",
                int(earliest),
                str(data.get("address") or ""),
            ):
                if occ_t not in occ_rows:
                    occ_rows.append(occ_t)
            for rb_t in red_book_by_hcad.get(hcad_clean, ()):
                if rb_t not in occ_rows:
                    occ_rows.append(rb_t)
            for hc_t in contract_by_hcad.get(hcad_clean, ()):
                if hc_t not in occ_rows:
                    occ_rows.append(hc_t)
            if occ_rows:
                occ_rows.sort(key=lambda r: (int(r[0]), str(r[1])))
                entry["r"] = occ_rows[:5]

            by_hcad[hcad_clean] = entry
        elif classification in ("ABSENT_THROUGH_1926_PREMATURE_HCAD_1920", "NO_PRE_1926_EVIDENCE"):
            absent_pre1926.append(hcad_clean)

    for extra_h in sorted(set(red_book_by_hcad.keys()) | set(contract_by_hcad.keys())):
        if extra_h in by_hcad:
            continue
        merged_occ: list[list] = []
        for rb_t in red_book_by_hcad.get(extra_h, ()):
            if rb_t not in merged_occ:
                merged_occ.append(rb_t)
        for hc_t in contract_by_hcad.get(extra_h, ()):
            if hc_t not in merged_occ:
                merged_occ.append(hc_t)
        if merged_occ:
            merged_occ.sort(key=lambda r: (int(r[0]), str(r[1])))
            ear_yr = int(merged_occ[0][0])
            by_hcad[extra_h] = {
                "e": ear_yr,
                "y": sorted({int(r[0]) for r in merged_occ if int(r[0]) <= 1926}),
                "f": "confirmed",
                "r": merged_occ[:5],
            }

    conn.close()

    post_1926_extended_count = 0
    post_1926_new_parcel_count = 0
    if anc_path is not None and anc_path.exists():
        try:
            from atlas_pipeline.harvest_taro_and_hpl_archives import load_confidential_hcad_set

            data_dir_for_conf = (
                ovr_path.parent
                if (ovr_path.parent / "permits").exists()
                else REPO_ROOT / "app" / "public" / "data"
            )
            confidential_hcads = load_confidential_hcad_set(data_dir_for_conf)
        except Exception as exc:
            print(f"[build_house_history_index] Warning loading confidential_hcads: {exc}")
            confidential_hcads = set()

        anc_doc = json.loads(anc_path.read_text(encoding="utf-8"))
        anc_by_hcad = anc_doc.get("by_hcad") if isinstance(anc_doc, dict) else {}
        if isinstance(anc_by_hcad, dict):
            for raw_hc, anc_rec in sorted(anc_by_hcad.items()):
                hc_clean = re.sub(r"\D", "", str(raw_hc or ""))
                if len(hc_clean) != 13 or hc_clean in confidential_hcads:
                    continue
                if not isinstance(anc_rec, dict):
                    continue
                sightings = anc_rec.get("sightings") or []
                if not isinstance(sightings, list) or not sightings:
                    continue

                ov_entry = ovr_by_hcad.get(hc_clean)
                if isinstance(ov_entry, dict) and (
                    ov_entry.get("suppress_only")
                    or ov_entry.get("is_demolished_vacant")
                    or int(ov_entry.get("year_built") or 0) >= 1975
                ):
                    continue

                cat = str(anc_rec.get("cat") or "")
                eff_yr = int(
                    (ov_entry or {}).get("year_built")
                    or anc_rec.get("atlas_yr")
                    or 0
                )
                if cat == "inner_core" and eff_yr > 1970 and hc_clean not in by_hcad:
                    continue
                if eff_yr > 1990 and hc_clean not in by_hcad and cat != "triage_1927":
                    continue

                def _has_specific_occ(s_obj: dict) -> bool:
                    r_str = sanitize_text(str(s_obj.get("r") or "")).strip()
                    return bool(
                        r_str
                        and r_str not in ("City Directory Resident", "Street Guide Occupant")
                    )

                sorted_sightings = sorted(
                    (s for s in sightings if isinstance(s, dict) and int(s.get("y") or 0) >= 1927),
                    key=lambda s: (
                        int(s.get("y") or 0),
                        0 if _has_specific_occ(s) else (1 if s.get("sec") == "sg" else 2),
                        str(s.get("n") or ""),
                    ),
                )
                if not sorted_sightings:
                    continue

                # Pair same-year, same-page alphabetical spouses ("City Directory Resident")
                # with the primary occupation-bearing head of household (e.g. "Thos B White (Nellie)"),
                # excluding household members identified in any year as a "Widow".
                widow_full_names: set[str] = {
                    sanitize_text(str(s.get("n") or "")).strip().upper()
                    for s in sorted_sightings
                    if "widow" in sanitize_text(str(s.get("r") or "")).lower()
                }
                alpha_head_by_page_surname: dict[tuple[int, str, str], str] = {}
                alpha_spouse_by_page_surname: dict[tuple[int, str, str], str] = {}
                for s in sorted_sightings:
                    if s.get("sec") != "alpha":
                        continue
                    y_v = int(s["y"])
                    img_v = sanitize_text(str(s.get("img") or "")).strip()
                    n_v = sanitize_text(str(s.get("n") or "")).strip()
                    if not img_v or not n_v or len(n_v.split()) < 2:
                        continue
                    if n_v.upper() in widow_full_names:
                        continue
                    surname = n_v.split()[-1].upper()
                    key_t = (y_v, img_v, surname)
                    if _has_specific_occ(s):
                        if key_t not in alpha_head_by_page_surname:
                            alpha_head_by_page_surname[key_t] = n_v
                    else:
                        r_str = sanitize_text(str(s.get("r") or "")).strip()
                        if r_str in ("City Directory Resident", "") and key_t not in alpha_spouse_by_page_surname:
                            alpha_spouse_by_page_surname[key_t] = n_v

                post_years = sorted({int(s["y"]) for s in sorted_sightings})
                first_s = sorted_sightings[0]
                first_img = sanitize_text(str(first_s.get("img") or "")).strip()

                max_tuples = (
                    6
                    if cat in ("override", "landmarks", "nrhp_properties", "good_brick_awards", "audit")
                    else 4
                )
                occ_tuples: list[list] = []
                seen_norm_names: set[str] = set()
                for s in sorted_sightings:
                    y_val = int(s["y"])
                    n_raw = sanitize_text(str(s.get("n") or "")).strip()
                    if not n_raw:
                        continue
                    r_raw = sanitize_text(str(s.get("r") or "")).strip()[:44]
                    # Skip blank-occupation row for a person identified as a widow in another year
                    if n_raw.upper() in widow_full_names and r_raw in ("City Directory Resident", ""):
                        continue
                    n_val = n_raw[:44]
                    if s.get("sec") == "alpha" and len(n_raw.split()) >= 2:
                        img_v = sanitize_text(str(s.get("img") or "")).strip()
                        surname = n_raw.split()[-1].upper()
                        key_t = (y_val, img_v, surname)
                        head_n = alpha_head_by_page_surname.get(key_t)
                        sp_n = alpha_spouse_by_page_surname.get(key_t)
                        if head_n and sp_n and head_n.upper() != sp_n.upper():
                            if n_raw.upper() == head_n.upper():
                                sp_given = " ".join(sp_n.split()[:-1]).strip()
                                if sp_given and f"({sp_given.upper()})" not in n_raw.upper():
                                    n_val = f"{head_n} ({sp_given})"[:44]
                            elif n_raw.upper() == sp_n.upper() and r_raw in ("City Directory Resident", ""):
                                # Already merged into head-of-household entry on the same page
                                continue

                    if r_raw in ("City Directory Resident", "Street Guide Occupant", ""):
                        r_val = (
                            "City Directory Resident"
                            if s.get("sec") == "alpha"
                            else "Street Guide Occupant"
                        )
                    else:
                        r_val = r_raw

                    # When a parcel has >= 5 directory years, avoid repeating the exact same
                    # occupant name across multiple years so later families remain visible
                    norm_n_key = n_val.split("(")[0].strip().upper()
                    if len(post_years) >= 5 and norm_n_key in seen_norm_names:
                        continue
                    tup = [y_val, n_val, r_val]
                    if tup not in occ_tuples and len(occ_tuples) < max_tuples:
                        occ_tuples.append(tup)
                        seen_norm_names.add(norm_n_key)

                if hc_clean in by_hcad:
                    cur = by_hcad[hc_clean]
                    cur["y"] = sorted(set(cur.get("y") or []) | set(post_years))
                    if first_img:
                        cur["ai"] = first_img
                    cur_r = list(cur.get("r") or [])
                    for tup in occ_tuples:
                        if tup not in cur_r and len(cur_r) < 6:
                            cur_r.append(tup)
                    if cur_r:
                        cur_r.sort(key=lambda r: int(r[0]))
                        cur["r"] = cur_r[:6]
                    post_1926_extended_count += 1
                else:
                    e_yr = post_years[0]
                    if eff_yr == 0:
                        f_val = "undated_hcad"
                    elif eff_yr > e_yr and cat != "override":
                        f_val = "earlier"
                    else:
                        f_val = "confirmed"
                    new_entry: dict = {
                        "e": e_yr,
                        "y": post_years,
                        "f": f_val,
                    }
                    if first_img:
                        new_entry["ai"] = first_img
                    if occ_tuples:
                        new_entry["r"] = occ_tuples
                    if cat in ("override", "landmarks", "nrhp_properties", "good_brick_awards", "audit"):
                        addr_clean = sanitize_text(str(anc_rec.get("addr") or "")).strip()
                        n_first = (
                            occ_tuples[0][1]
                            if occ_tuples
                            else sanitize_text(str(first_s.get("n") or "")).strip()
                        )
                        r_first = (
                            occ_tuples[0][2]
                            if occ_tuples
                            else sanitize_text(str(first_s.get("r") or "Resident")).strip()
                        )
                        new_entry["c"] = (
                            f"{e_yr} Houston City Directory: {n_first} ({r_first}) at {addr_clean}"
                        )[:160]
                    by_hcad[hc_clean] = new_entry
                    post_1926_new_parcel_count += 1

    payload = {
        "generated_from": "HPL ContentDM Houston City Directories (1866-1926) + Ancestry.com U.S. City Directories (1927-1968, dbId=2469)",
        "collection_url": "https://cdm17006.contentdm.oclc.org/digital/collection/citydir",
        "volume_count": len(volumes),
        "verified_hit_count": len(by_hcad),
        "post_1926_extended_parcel_count": post_1926_extended_count,
        "post_1926_new_parcel_count": post_1926_new_parcel_count,
        "audited_absent_count": len(absent_pre1926),
        "volumes": volumes,
        "historic_streets": HISTORIC_STREET_ALIASES,
        "by_hcad": by_hcad,
        "absent_through_1926": absent_pre1926,
    }
    return payload


def main() -> None:
    existing_clearance = None
    if OUTPUT_JSON.exists():
        try:
            existing_clearance = json.loads(OUTPUT_JSON.read_text(encoding="utf-8")).get(
                "post_1926_clearance"
            )
        except Exception:
            existing_clearance = None
    payload = build_index()
    if existing_clearance and "post_1926_clearance" not in payload:
        payload["post_1926_clearance"] = existing_clearance
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    if "\u2014" in raw or "\\u2014" in raw:
        raise AssertionError("Em-dash detected in generated citydir_index.json")
    OUTPUT_JSON.write_text(raw, encoding="utf-8")
    size_kb = OUTPUT_JSON.stat().st_size / 1024.0
    print(
        f"Wrote {OUTPUT_JSON} ({size_kb:.1f} KB): "
        f"{payload['volume_count']} volumes, "
        f"{payload['verified_hit_count']} verified parcel hits "
        f"(+{payload.get('post_1926_new_parcel_count', 0)} new 1927-1968 parcels, "
        f"+{payload.get('post_1926_extended_parcel_count', 0)} extended pre-1927 parcels), "
        f"{payload['audited_absent_count']} audited pre-1927 absences."
    )


if __name__ == "__main__":
    main()
