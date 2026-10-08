"""Crawl, match, download, and parse City of Houston Landmark Designation Report PDFs."""

from __future__ import annotations

import concurrent.futures
import csv
import json
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

CACHE_DIR = Path("pipeline/cache")
PDF_TEXT_CACHE_PATH = CACHE_DIR / "landmark_pdf_texts.json"


def normalize_coh_url(u: str) -> str:
    u = u.strip()
    u = re.sub(r"^https?://(www\.)?houstontx\.gov(:80)?/", "https://www.houstontx.gov/", u, flags=re.I)
    return u


def collect_candidate_urls() -> tuple[list[str], list[str], dict[str, str], list[dict]]:
    """Return (all_pdf_urls, all_photo_urls, url_to_wayback_ts, mydata_rows)."""
    cdx_path = CACHE_DIR / "wayback_historicpres_cdx.json"
    mydata_path = CACHE_DIR / "coh_landmarks_web_mydata.json"

    cdx_rows = json.loads(cdx_path.read_text())[1:] if cdx_path.exists() else []
    mydata = json.loads(mydata_path.read_text()) if mydata_path.exists() else []

    url_to_ts: dict[str, str] = {}
    pdf_set: set[str] = set()
    photo_set: set[str] = set()

    for orig, mime, status, ts in cdx_rows:
        norm = normalize_coh_url(orig)
        low = norm.lower()
        if status == "200":
            url_to_ts[norm] = ts
            if low.endswith(".pdf") and ("/landmarks/" in low or "/docs_pdfs/" in low):
                pdf_set.add(norm)
            elif low.endswith((".jpg", ".jpeg", ".png")) and "/landmarks/" in low:
                photo_set.add(norm)

    for row in mydata:
        for k in ("LMlink", "PLMlink", "Aslink"):
            v = str(row.get(k) or "").strip()
            if not v:
                continue
            if v.startswith("landmarks") and not v.startswith("landmarks/"):
                v = "landmarks/" + v[len("landmarks"):]
            v = v.replace(".pdf.pdf", ".pdf")
            full = "https://www.houstontx.gov/planning/HistoricPres/" + v.lstrip("/")
            if full.lower().endswith(".pdf"):
                pdf_set.add(full)
        for i in range(1, 5):
            pv = str(row.get(f"Photos{i}_link") or "").strip()
            if pv:
                if pv.startswith("landmarks") and not pv.startswith("landmarks/"):
                    pv = "landmarks/" + pv[len("landmarks"):]
                full_p = "https://www.houstontx.gov/planning/HistoricPres/" + pv.lstrip("/")
                photo_set.add(full_p)

    return sorted(pdf_set), sorted(photo_set), url_to_ts, mydata


def normalize_lm_code(code: str) -> list[str]:
    """Return canonical variants of a Landmark / Protected Landmark code (e.g. 01L93 <-> 01L093)."""
    c = str(code or "").strip().upper()
    if not c or c == "-":
        return []
    variants = [c]
    m = re.match(r"^([0-9]{2})(L|PL|AS|LD)([0-9]+)([A-Z]?)$", c)
    if m:
        yr, kind, num, suf = m.groups()
        variants.append(f"{yr}{kind}{int(num):02d}{suf}")
        variants.append(f"{yr}{kind}{int(num):03d}{suf}")
        variants.append(f"{yr}{kind}{int(num)}{suf}")
    return list(dict.fromkeys(variants))


def download_and_extract_pdfs(
    urls: list[str],
    url_to_ts: dict[str, str],
) -> dict[str, dict]:
    """Download PDFs in parallel (with Wayback fallback) and extract text via pdftotext."""
    cache: dict[str, dict] = {}
    if PDF_TEXT_CACHE_PATH.exists():
        try:
            cache = json.loads(PDF_TEXT_CACHE_PATH.read_text())
        except Exception:
            cache = {}

    to_fetch = [u for u in urls if u not in cache]
    if to_fetch:
        print(f"Downloading & parsing {len(to_fetch)} new PDFs ({len(cache)} already cached)...")

        def fetch_one(u: str) -> tuple[str, dict]:
            safe_u = u.replace(" ", "%20").replace("&", "%26") if " " in u else u
            data = b""
            final_url = u
            source = "live"
            try:
                req = urllib.request.Request(safe_u, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status == 200:
                        data = resp.read()
            except Exception:
                pass

            if len(data) < 500 or not data.startswith(b"%PDF"):
                ts = url_to_ts.get(u, "20200603")
                wb_url = f"https://web.archive.org/web/{ts}id_/{safe_u}"
                try:
                    req = urllib.request.Request(wb_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        cand = resp.read()
                        if len(cand) >= 500 and cand.startswith(b"%PDF"):
                            data = cand
                            final_url = f"https://web.archive.org/web/{ts}/{safe_u}"
                            source = "wayback"
                except Exception:
                    pass

            if len(data) < 500 or not data.startswith(b"%PDF"):
                return u, {"ok": False, "url": final_url, "source": "missing", "text": ""}

            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf") as tf:
                    tf.write(data)
                    tf.flush()
                    txt = subprocess.check_output(["pdftotext", tf.name, "-"], timeout=10).decode(
                        "utf-8", errors="ignore"
                    )
                return u, {
                    "ok": True,
                    "url": final_url,
                    "source": source,
                    "bytes": len(data),
                    "text": txt,
                }
            except Exception as exc:
                return u, {"ok": False, "url": final_url, "source": f"error:{exc}", "text": ""}

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
            for idx, (u, info) in enumerate(ex.map(fetch_one, to_fetch), 1):
                cache[u] = info
                if idx % 50 == 0 or idx == len(to_fetch):
                    print(f"  Processed {idx}/{len(to_fetch)} PDFs...")
        PDF_TEXT_CACHE_PATH.write_text(json.dumps(cache))

    return cache


STYLE_PATTERNS = [
    "Queen Anne",
    "Colonial Revival",
    "Dutch Colonial Revival",
    "Spanish Colonial Revival",
    "Mediterranean Revival",
    "Mission Revival",
    "Tudor Revival",
    "Gothic Revival",
    "Classical Revival",
    "Neoclassical",
    "Greek Revival",
    "Renaissance Revival",
    "Italian Renaissance",
    "Spanish Renaissance",
    "Second Empire",
    "Italianate",
    "Folk Victorian",
    "Eastlake",
    "Stick Style",
    "Shingle Style",
    "Richardson Romanesque",
    "Romanesque Revival",
    "Beaux-Arts",
    "Prairie School",
    "Prairie Style",
    "Craftsman",
    "Bungalow",
    "Foursquare",
    "American Foursquare",
    "Art Deco",
    "Streamline Moderne",
    "Art Moderne",
    "Moderne",
    "International Style",
    "Mid-Century Modern",
    "Contemporary",
    "Ranch Style",
    "Minimal Traditional",
    "Vernacular",
    "Shotgun",
    "Gulf Coast Cottage",
]


def parse_report_metadata(text: str) -> dict:
    """Extract construction year, architect/builder, style, header location/name, and narrative summary from PDF text."""
    if not text or len(text.strip()) < 60:
        return {}

    cleaned = re.sub(r"\s+", " ", text)
    header_chunk = cleaned[:1600]

    # Extract header fields if present
    site_name_m = re.search(
        r"LANDMARK(?:/SITE)?\s+NAME\s*:\s*(.+?)(?=OWNER\s*:|APPLICANT\s*:|LOCATION\s*:|AGENDA\s+ITEM\s*:)",
        header_chunk,
        re.I,
    )
    loc_m = re.search(
        r"LOCATION\s*:\s*(.+?)(?=HEARING\s+NOTICE|AGENDA\s+ITEM|HPO\s+FILE|DATE\s+ACCEPTED|SITE\s+INFORMATION|APPLICANT)",
        header_chunk,
        re.I,
    )
    hpo_m = re.search(r"HPO\s+FILE\s+NO\.?\s*:\s*([0-9A-Za-z_-]+)", header_chunk, re.I)

    # Isolate HISTORY AND SIGNIFICANCE section so we don't accidentally match hearing dates
    hist_start = re.search(r"HISTORY\s+AND\s+SIGNIFICANCE(?:\s+SUMMARY)?\s*:", cleaned, re.I)
    hist_body = cleaned[hist_start.start():] if hist_start else cleaned
    crit_match = re.search(r"APPROVAL\s+CRITERIA|RESTORATION\s+HISTORY|BIBLIOGRAPHY", hist_body, re.I)
    if crit_match and crit_match.start() > 150:
        hist_narrative = hist_body[:crit_match.start()]
    else:
        hist_narrative = hist_body[:3800]

    # 1. Extract construction year, prioritizing explicit building construction verbs
    year_built = None
    explicit_year_patterns = [
        r"(?:house|building|structure|home|residence|church|cottage|bungalow|mansion|edifice|complex|store|warehouse|plant|school|hospital|hotel|theatre|theater|court|apartments|duplex)\s+(?:at\s+[^,]+?\s+)?(?:was\s+)?(?:built|constructed|completed|erected|designed|remodeled)\s+(?:in\s+|circa\s+|c\.\s*|ca\.\s*|around\s+|between\s+)?(18[4-9][0-9]|19[0-7][0-9])\b",
        r"(?:was\s+|were\s+|is\s+a\s+[^.]{0,50}?)(?:built|constructed|completed|erected)\s+(?:in\s+|circa\s+|c\.\s*|ca\.\s*|around\s+|between\s+)?(18[4-9][0-9]|19[0-7][0-9])\b",
        r"\b(?:built|constructed|completed|erected)\s+(?:in\s+|circa\s+|c\.\s*|ca\.\s*|around\s+)(18[4-9][0-9]|19[0-7][0-9])\b",
        r"\b(?:circa|c\.|ca\.)\s*(18[4-9][0-9]|19[0-7][0-9])\b",
        r"Built\s*:\s*(?:c\.\s*)?(18[4-9][0-9]|19[0-7][0-9])\b",
    ]
    for pat in explicit_year_patterns:
        for m in re.finditer(pat, hist_narrative, re.I):
            yr = int(m.group(1))
            ctx_before = hist_narrative[max(0, m.start() - 60):m.start()].lower()
            if any(
                skip in ctx_before
                for skip in ("demolished", "prior to", "replaced", "burned", "omaha", "incorporated", "annexed")
            ):
                continue
            if yr in (1836, 1891, 1896, 1918, 1983, 1995, 1997) and m.start() > 260:
                continue
            year_built = yr
            break
        if year_built:
            break

    # 2. Extract architectural style
    extracted_style = ""
    for st in STYLE_PATTERNS:
        if re.search(rf"\b{re.escape(st)}\b", hist_narrative, re.I):
            extracted_style = st
            break

    # 3. Extract architect / builder
    extracted_architect = ""
    arch_patterns = [
        r"(?:designed\s+by|architect(?:s)?\s+(?:was|were|is)\s+)(?:the\s+(?:firm|architectural\s+firm)\s+of\s+|noted\s+(?:Houston\s+)?architect\s+|prominent\s+(?:Houston\s+)?architect\s+|local\s+architect\s+|architect\s+)?([A-Z][A-Za-z.\s,&'-]{3,48}?)(?:\.|,|;|\s+and\s+(?:built|constructed)|\s+in\s+1[89]|\s+for\s+|\s+who\s+|\s+as\s+)",
        r"(?:built|constructed)\s+by\s+(?:builder\s+|contractor\s+|general\s+contractor\s+)?([A-Z][A-Za-z.\s,&'-]{3,45}?)(?:\.|,|;|\s+in\s+1[89]|\s+for\s+)",
    ]
    for pat in arch_patterns:
        m = re.search(pat, hist_narrative)
        if m:
            cand = m.group(1).strip(" .,;-")
            if 4 <= len(cand) <= 50 and not any(
                bad in cand.lower()
                for bad in (
                    "city of houston",
                    "national register",
                    "omaha",
                    "current owner",
                    "property owner",
                    "south texas",
                )
            ):
                extracted_architect = cand
                break

    # 4. Extract concise 1-2 sentence summary from HISTORY AND SIGNIFICANCE
    narr_clean = re.sub(
        r"^HISTORY\s+AND\s+SIGNIFICANCE(?:\s+SUMMARY)?\s*:\s*", "", hist_narrative, flags=re.I
    )
    narr_clean = re.sub(
        r"^HISTORY\s+AND\s+SIGNIFICANCE\s*:\s*", "", narr_clean, flags=re.I
    ).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", narr_clean)
    picked = []
    for s in sentences:
        s = s.strip()
        if len(s) < 25:
            continue
        if "At the March 13, 1997 public hearing" in s or "Sec. 33-224" in s:
            break
        picked.append(s)
        if len(" ".join(picked)) >= 220 or len(picked) >= 2:
            break
    summary = " ".join(picked)[:380]

    return {
        "header_site_name": site_name_m.group(1).strip() if site_name_m else "",
        "header_location": loc_m.group(1).strip() if loc_m else "",
        "header_hpo_file": hpo_m.group(1).strip() if hpo_m else "",
        "pdf_year_built": year_built,
        "pdf_style": extracted_style,
        "pdf_architect": extracted_architect,
        "pdf_summary": summary,
    }


def score_pdf_for_landmark(
    u: str,
    pdf_info: dict,
    pdf_meta: dict,
    lm_codes: set[str],
    st_num: str,
    st_name: str,
    name: str,
) -> int:
    """Compute a verification score so off-by-one GIS codes never attach the wrong house's PDF."""
    fn = urllib.parse.unquote(u.split("/")[-1]).lower()
    txt_head = ((pdf_info or {}).get("text") or "")[:1800].lower()
    hdr_loc = (pdf_meta.get("header_location") or "").lower()
    hdr_name = (pdf_meta.get("header_site_name") or "").lower()

    st_toks = [
        t.lower()
        for t in re.split(r"[\s.-]+", st_name)
        if len(t) >= 3 and t.lower() not in ("street", "avenue", "boulevard", "drive", "lane", "road", "court", "place", "way")
    ]
    name_toks = [
        w.lower()
        for w in re.split(r"[^A-Za-z0-9]+", name)
        if len(w) >= 4
        and w.lower()
        not in (
            "house",
            "building",
            "historic",
            "street",
            "avenue",
            "boulevard",
            "drive",
            "church",
            "park",
            "the",
            "and",
            "residence",
            "home",
            "company",
            "complex",
        )
    ]

    COMMON_FIRST_NAMES = {
        "john", "william", "james", "charles", "george", "joseph", "thomas", "henry", "robert",
        "edward", "frank", "walter", "arthur", "fred", "albert", "harry", "david", "louis",
        "richard", "earl", "paul", "ralph", "ernest", "samuel", "howard", "clarence", "andrew",
        "mary", "elizabeth", "margaret", "sarah", "anna", "helen", "clara", "alice", "florence",
        "first", "second", "third", "fourth", "fifth", "sixth", "houston", "texas", "memorial",
        "baptist", "methodist", "missionary", "united", "saint", "medical", "national", "central",
    }
    distinctive_name_toks = [w for w in name_toks if w not in COMMON_FIRST_NAMES and len(w) >= 5]

    has_st_num_fn = bool(st_num and re.search(rf"(^|[^0-9]){re.escape(st_num)}([^0-9]|$)", fn))
    has_st_name_fn = bool(st_toks and any(t in fn for t in st_toks))
    has_addr_txt = bool(
        st_num
        and st_toks
        and any(
            re.search(rf"(^|[^0-9]){re.escape(st_num)}\s+(?:[nsew]\.?\s+)?{re.escape(t)}", hdr_loc or txt_head[:700])
            for t in st_toks
        )
    )
    has_dist_name_fn = bool(distinctive_name_toks and any(w in fn for w in distinctive_name_toks))
    has_dist_name_txt = bool(
        distinctive_name_toks and any(w in (hdr_name or txt_head[:700]) for w in distinctive_name_toks)
    )

    # Check if filename has a code range like 16PL135-16PL156
    expanded_fn_codes: set[str] = set()
    for rm in re.finditer(r"([0-9]{2})(L|PL)([0-9]{2,3})\s*[-–]\s*\1\2([0-9]{2,3})", fn, re.I):
        yr_s, kind_s, start_n, end_n = rm.groups()
        for n_i in range(int(start_n), int(end_n) + 1):
            expanded_fn_codes.update(w.lower() for w in normalize_lm_code(f"{yr_s}{kind_s}{n_i}"))

    has_code = False
    for c in lm_codes:
        cl = c.lower()
        if cl in fn or cl in expanded_fn_codes or cl == (pdf_meta.get("header_hpo_file") or "").lower():
            has_code = True
            break

    # Check if filename explicitly mentions a DIFFERENT street number
    fn_without_code = re.sub(
        r"^[a-z0-9_-]*?[0-9]{2}(?:l|pl|ld|as)[0-9]{1,3}(?:[-–][0-9]{2}(?:l|pl|ld|as)[0-9]{1,3})?[a-z]?[_-]?",
        "",
        fn,
    )
    other_num_m = re.search(r"\b([0-9]{3,5})\b", fn_without_code)
    conflicts_st_num = bool(st_num and other_num_m and other_num_m.group(1) != st_num)
    if conflicts_st_num and not has_addr_txt:
        return -100

    # Strict acceptance rules:
    # 1. Exact street number + street name in filename or header
    if (has_st_num_fn and has_st_name_fn) or has_addr_txt:
        return 100 + (30 if has_code else 0) + (20 if has_dist_name_fn else 0)

    # 2. Exact HPO code match (or range match) + (street number OR distinctive name)
    if has_code and (has_st_num_fn or has_dist_name_fn or has_dist_name_txt):
        return 85

    # 3. Off-by-one GIS code cases where both distinctive surname AND street number/name or multi-token name match in filename
    if has_dist_name_fn and (has_st_num_fn or has_st_name_fn or len([w for w in distinctive_name_toks if w in fn]) >= 2):
        return 75

    return 0


def main():
    coh_landmarks = json.loads((CACHE_DIR / "coh_landmarks.json").read_text())
    all_pdf_urls, all_photo_urls, url_to_ts, mydata = collect_candidate_urls()

    # Ensure all candidate /landmarks/ PDFs are downloaded & parsed in cache
    landmark_dir_pdfs = [
        u
        for u in all_pdf_urls
        if "/landmarks/" in u.lower()
        or any(k in u.lower() for k in ("landmark", "plm", "_lm", "lm_", "nomination"))
    ]
    pdf_cache = download_and_extract_pdfs(landmark_dir_pdfs, url_to_ts)

    # Pre-parse metadata for all valid PDFs
    parsed_by_url: dict[str, dict] = {}
    for u, info in pdf_cache.items():
        if info.get("ok"):
            parsed_by_url[u] = parse_report_metadata(info.get("text") or "")

    # Also index photos by code and street number
    photo_by_code: dict[str, list[str]] = {}
    for u in all_photo_urls:
        fn = urllib.parse.unquote(u.split("/")[-1])
        for m in re.finditer(r"([0-9]{2}(?:L|PL|AS|LD)[0-9]{1,3}[A-Z]?)", fn, re.I):
            for cv in normalize_lm_code(m.group(1)):
                photo_by_code.setdefault(cv, []).append(u)

    mydata_by_num: dict[str, list[dict]] = {}
    for r in mydata:
        num = str(r.get("Addnum") or "").strip()
        if num:
            mydata_by_num.setdefault(num, []).append(r)

    enriched_rows = []
    years_recovered = 0
    archs_recovered = 0
    styles_recovered = 0
    summaries_recovered = 0
    verified_pdf_count = 0

    for feat in coh_landmarks:
        p = feat.get("properties") or {}
        lm = str(p.get("USER_LM_NUM") or "").strip()
        plm = str(p.get("USER_PLM_NUM") or "").strip()
        st_num = str(p.get("USER_SITE_ST_NUM") or "").strip()
        st_name = str(p.get("USER_SITE_ST_NAME") or "").strip()
        addr = str(p.get("USER_SITE_ADDRESS") or "").strip()
        name = str(p.get("USER_SITE_NAME") or "").strip()

        lm_codes = set(normalize_lm_code(lm) + normalize_lm_code(plm))

        # Score all valid PDFs against this landmark
        scored_candidates: list[tuple[int, str]] = []
        for u, info in pdf_cache.items():
            if not info.get("ok"):
                continue
            sc = score_pdf_for_landmark(
                u, info, parsed_by_url.get(u, {}), lm_codes, st_num, st_name, name
            )
            if sc >= 30:
                scored_candidates.append((sc, u))

        scored_candidates.sort(key=lambda x: (-x[0], " " in x[1], len(x[1])))
        valid_pdfs = []
        seen_urls = set()
        for sc, u in scored_candidates:
            final_u = pdf_cache[u].get("url") or u
            if final_u not in seen_urls:
                seen_urls.add(final_u)
                valid_pdfs.append((u, final_u))

        best_meta = {}
        for orig_u, _ in valid_pdfs:
            meta = parsed_by_url.get(orig_u) or {}
            for k, v in meta.items():
                if v and not best_meta.get(k):
                    best_meta[k] = v

        # Collect photos
        matched_photos = []
        for c in lm_codes:
            matched_photos.extend(photo_by_code.get(c, []))
        if st_num and st_num in mydata_by_num:
            st_toks = [t.lower() for t in re.split(r"[\s.-]+", st_name) if len(t) >= 2]
            for r in mydata_by_num[st_num]:
                r_nam = str(r.get("Addnam") or "").lower()
                if st_toks and any(t in r_nam for t in st_toks):
                    for i in range(1, 5):
                        pv = str(r.get(f"Photos{i}_link") or "").strip()
                        if pv:
                            if pv.startswith("landmarks") and not pv.startswith("landmarks/"):
                                pv = "landmarks/" + pv[len("landmarks"):]
                            matched_photos.append(
                                "https://www.houstontx.gov/planning/HistoricPres/" + pv.lstrip("/")
                            )
        dedup_photos = list(dict.fromkeys(matched_photos))

        if valid_pdfs:
            verified_pdf_count += 1
        if not p.get("USER_YR_BUILT") and best_meta.get("pdf_year_built"):
            years_recovered += 1
        if not str(p.get("USER_ARCHITECT___BUILDER") or "").strip() and best_meta.get("pdf_architect"):
            archs_recovered += 1
        if not str(p.get("USER_STYLE") or "").strip() and best_meta.get("pdf_style"):
            styles_recovered += 1
        if best_meta.get("pdf_summary"):
            summaries_recovered += 1

        gis_yr = p.get("USER_YR_BUILT")
        pdf_yr = best_meta.get("pdf_year_built")

        enriched_rows.append({
            "objectid": p.get("OBJECTID"),
            "lm_num": lm if lm != "-" else "",
            "plm_num": plm if plm != "-" else "",
            "name": name,
            "address": addr,
            "hcad_num": str(p.get("USER_HCAD_NUM") or "").strip(),
            "gis_year_built": gis_yr or "",
            "pdf_year_built": pdf_yr or "",
            "final_year_built": gis_yr or pdf_yr or "",
            "gis_architect": str(p.get("USER_ARCHITECT___BUILDER") or "").strip(),
            "pdf_architect": best_meta.get("pdf_architect") or "",
            "final_architect": str(p.get("USER_ARCHITECT___BUILDER") or "").strip()
            or best_meta.get("pdf_architect")
            or "",
            "gis_style": str(p.get("USER_STYLE") or "").strip(),
            "pdf_style": best_meta.get("pdf_style") or "",
            "final_style": str(p.get("USER_STYLE") or "").strip()
            or best_meta.get("pdf_style")
            or "",
            "report_pdf_url": valid_pdfs[0][1] if valid_pdfs else "",
            "secondary_pdf_url": valid_pdfs[1][1] if len(valid_pdfs) > 1 else "",
            "photo_urls": dedup_photos,
            "pdf_summary": best_meta.get("pdf_summary") or "",
        })

    out_json = CACHE_DIR / "coh_landmarks_enriched.json"
    out_json.write_text(json.dumps(enriched_rows, indent=2))

    missing_csv = CACHE_DIR / "coh_landmarks_missing_pdfs_tpia.csv"
    with missing_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["HPO_LM_Number", "HPO_PLM_Number", "Landmark_Name", "Street_Address", "HCAD_Account", "Year_Built"])
        for r in enriched_rows:
            if not r["report_pdf_url"]:
                w.writerow([
                    r["lm_num"],
                    r["plm_num"],
                    r["name"],
                    r["address"],
                    r["hcad_num"],
                    r["final_year_built"],
                ])

    print("=== VERIFIED LANDMARK PDF CRAWL & ENRICHMENT SUMMARY ===")
    print(f"  Total COH Landmarks in GIS:        {len(enriched_rows)}")
    print(f"  Verified Downloadable PDF Reports: {verified_pdf_count} / {len(enriched_rows)} ({verified_pdf_count*100/len(enriched_rows):.1f}%)")
    print(f"  Missing Year Built Recovered:      +{years_recovered} landmarks (was missing in COH GIS)")
    print(f"  Missing Architect/Builder Recovered:+{archs_recovered} landmarks (was missing in COH GIS)")
    print(f"  Missing Style Recovered:           +{styles_recovered} landmarks (was missing in COH GIS)")
    print(f"  Historical Summaries Extracted:    +{summaries_recovered} landmarks")
    print(f"  Remaining Unpublished PDFs (TPIA): {len(enriched_rows) - verified_pdf_count} (saved to {missing_csv})")


if __name__ == "__main__":
    main()
