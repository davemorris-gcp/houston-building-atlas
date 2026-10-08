#!/usr/bin/env python3
"""
Crawl & Integrate Houston Architecture Info Forum (HAIF - houstonarchitecture.com)
into the Houston Building Atlas.

1. Indexes 29,467+ unique HAIF topic threads from Wayback CDX (`haif_cdx_topics.json`).
2. Cross-matches HAIF threads to Houston Building Atlas buildings (`curated_overrides.json`,
   `coh_landmarks_enriched.json`, `overlays.json`, `buildings.geojson`, `search_index.json`)
   via normalized street addresses in URL slugs AND distinctive landmark/building names.
3. Fetches archived HTML & JSON-LD (`DiscussionForumPosting`) for matched landmark/historic
   threads (`haif_thread_details.json`) with polite rate-limiting + retry, extracting:
   - Canonical HAIF thread URLs + Wayback snapshot URLs
   - Subforum category (e.g. Historic Houston, Houston Mod, Downtown, The Heights)
   - First-post & discussion architectural summaries
   - Extracted construction years, demolition notices, and Houston architects
   - Direct Invision CDN forum photos (`media.invisioncic.com/w329674/...`)
4. Generates an error/enrichment audit (`pipeline/cache/haif_enrichment_audit.json`) and
   enriches `curated_overrides.json`, `buildings.geojson`, `overlays.json`, `building_photos.json`,
   and `search_index.json`.
"""

import concurrent.futures
import html as html_lib
import json
import os
import re
import time
import urllib.request
from collections import defaultdict

CACHE_DIR = "pipeline/cache"
TOPICS_PATH = os.path.join(CACHE_DIR, "haif_cdx_topics.json")
DETAILS_CACHE_PATH = os.path.join(CACHE_DIR, "haif_thread_details.json")
AUDIT_OUT_PATH = os.path.join(CACHE_DIR, "haif_enrichment_audit.json")

STREET_SUFFIXES = {
    "st", "street", "ave", "avenue", "blvd", "boulevard", "rd", "road",
    "dr", "drive", "ln", "lane", "way", "pkwy", "parkway", "fwy", "freeway",
    "hwy", "highway", "ct", "court", "pl", "place", "cir", "circle", "sq", "square",
    "ter", "terrace", "trl", "trail", "loop"
}
DIRS = {
    "n": "N", "s": "S", "e": "E", "w": "W",
    "north": "N", "south": "S", "east": "E", "west": "W",
    "ne": "NE", "nw": "NW", "se": "SE", "sw": "SW"
}

KNOWN_HOUSTON_ARCHITECTS = [
    "Alfred C. Finn", "Joseph Finger", "Kenneth Franzheim", "William Ward Watkin",
    "John F. Staub", "John Staub", "Birdsall P. Briscoe", "Birdsall Briscoe",
    "Eugene T. Heiner", "Eugene Heiner", "George E. Dickey", "Sanguinet & Staats",
    "Sanguinet, Staats & Gottlieb", "Sanguinet, Staats & Hedrick", "Wyatt C. Hedrick",
    "Cram, Goodhue & Ferguson", "Ralph Adams Cram", "Mauran, Russell & Crowell",
    "Warren & Wetmore", "C. D. Hill", "CD Hill", "Cooke & Company",
    "Jonas & Tabor", "Henry F. Jonas", "Lamar Q. Cato", "John S. Chase",
    "MacKie & Kamrath", "Karl Kamrath", "Fred MacKie", "Wilson, Morris, Crain & Anderson",
    "Hermon Lloyd", "Lloyd & Morgan", "Lloyd, Morgan & Jones", "Harvin C. Moore",
    "Harvin Moore", "Cameron Fairchild", "Stayton Nunn", "Milton McGinty",
    "Maurice J. Sullivan", "Maurice Sullivan", "Irving R. Klein", "Irving Klein",
    "B. P. Briscoe", "Russell Brown", "Hiram A. Salisbury", "T. G. McHale",
    "Finger & Rustay", "Golemon & Rolfe", "Caudill Rowlett Scott",
    "Neuhaus & Taylor", "3D/International", "Skidmore, Owings & Merrill",
    "Philip Johnson", "John Burgee", "I. M. Pei", "Cesar Pelli", "Ludwig Mies van der Rohe",
    "Gunnar Birkerts", "Eero Saarinen", "Frank Lloyd Wright", "Alden B. Dow",
    "William Floyd", "Lucian Hood", "Lars Bang", "Howard Barnstone",
    "Barnstone & Aubry", "Eugene Aubry", "Wirtz, Calhoun",
    "R. D. Steele", "Reuben D. Steele", "Olle & Lorehn",
    "W. D. Van Siclen", "F. S. Glover", "H. C. Cooke", "Lewis Sterling Green",
    "Green & Briscoe", "James Ruskin Bailey", "Bailey & McGinty", "Harry D. Payne"
]


def extract_slug_addresses(slug: str):
    tokens = slug.lower().split("-")
    results = []
    for i, tok in enumerate(tokens):
        if tok.isdigit() and 2 <= len(tok) <= 5 and int(tok) > 0:
            for j in range(i + 2, min(len(tokens), i + 6)):
                if tokens[j] in STREET_SUFFIXES:
                    mid = tokens[i + 1 : j]
                    if not mid:
                        continue
                    if mid[0] in DIRS and len(mid) >= 2:
                        street_core = " ".join(mid[1:]).upper()
                        norm_with_dir = f"{tok} {DIRS[mid[0]]} {street_core}"
                        norm_no_dir = f"{tok} {street_core}"
                        results.append((norm_with_dir, norm_no_dir, "-".join(tokens[i : j + 1])))
                    else:
                        street_core = " ".join(mid).upper()
                        norm = f"{tok} {street_core}"
                        results.append((norm, norm, "-".join(tokens[i : j + 1])))
                    break
    return results


def norm_addr(a: str) -> str:
    if not a:
        return ""
    a = re.sub(r"[^A-Z0-9\s]", " ", a.upper())
    a = re.sub(r"\b(?:STE|SUITE|APT|UNIT|BLDG|FL|FLOOR)\b.*$", "", a)
    parts = a.split()
    if not parts or not parts[0].isdigit() or int(parts[0]) == 0:
        return ""
    while len(parts) > 2 and (
        parts[-1].isdigit()
        or parts[-1] in {
            "HOUSTON", "TX", "TEXAS", "BAYTOWN", "PASADENA", "BELLAIRE",
            "ST", "STREET", "AVE", "AVENUE", "BLVD", "BOULEVARD", "RD", "ROAD",
            "DR", "DRIVE", "LN", "LANE", "WAY", "PKWY", "PARKWAY", "FWY", "FREEWAY",
            "HWY", "HIGHWAY", "CT", "COURT", "PL", "PLACE", "CIR", "CIRCLE",
            "SQ", "SQUARE", "TER", "TERRACE", "TRL", "TRAIL", "LOOP",
            "N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"
        }
    ):
        parts.pop()
    if len(parts) < 2:
        return ""
    if len(parts) >= 3 and parts[1] in DIRS:
        parts[1] = DIRS[parts[1]]
    return " ".join(parts)


def norm_addr_nodir(a: str) -> str:
    na = norm_addr(a)
    parts = na.split()
    if len(parts) >= 3 and parts[1] in {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}:
        return f"{parts[0]} {' '.join(parts[2:])}"
    return na


GENERIC_NAME_STOPWORDS = {
    "the", "and", "of", "in", "at", "on", "for", "to", "by", "with", "from",
    "houston", "texas", "historic", "district", "house", "home", "building",
    "buildings", "center", "centre", "park", "parks", "school", "elementary",
    "high", "middle", "church", "baptist", "methodist", "catholic", "episcopal",
    "presbyterian", "lutheran", "temple", "first", "second", "third", "fourth",
    "fifth", "sixth", "ward", "north", "south", "east", "west", "central",
    "downtown", "midtown", "montrose", "heights", "museum", "river", "oaks",
    "memorial", "medical", "street", "avenue", "boulevard", "road", "drive",
    "place", "court", "square", "tower", "towers", "plaza", "apartments",
    "lofts", "hotel", "motel", "inn", "bank", "national", "state", "city",
    "county", "harris", "company", "co", "corp", "inc", "hall", "station",
    "terminal", "depot", "warehouse", "plant", "complex", "annex", "addition",
    "residence", "cottage", "mansion", "estate", "commercial", "office",
    "retail", "shopping", "village", "club", "theatre", "theater",
    "library", "hospital", "clinic", "college", "university", "academy"
}


def clean_title_from_slug(slug: str) -> str:
    decoded = urllib.request.unquote(slug or "")
    decoded = re.sub(r"[\u200b-\u200f\ufeff]", "", decoded)
    decoded = re.sub(r"[\t\r\n\u00a0]+", " ", decoded)
    words = decoded.replace("-", " ").split()
    out = []
    for w in words:
        if w.lower() in {"st", "ave", "blvd", "rd", "dr", "ln", "fwy", "pkwy", "hwy", "tx"}:
            out.append(w.capitalize() if w.lower() != "tx" else "TX")
        elif w.lower() in {"n", "s", "e", "w", "ne", "nw", "se", "sw", "uh", "tsu", "isd", "ymca", "ywca"}:
            out.append(w.upper())
        else:
            out.append(w[0].upper() + w[1:] if len(w) > 1 else w.upper())
    return " ".join(out)


def fetch_wayback_thread_details(topic):
    tid = topic["tid"]
    ts = topic["ts"]
    orig = topic["orig"]
    canonical_url = f"https://www.houstonarchitecture.com/topic/{tid}-{topic['slug']}/"
    wb_url = f"https://web.archive.org/web/{ts}id_/{orig}"
    public_wb_url = f"https://web.archive.org/web/{ts}/{orig}"

    result = {
        "tid": tid,
        "slug": topic["slug"],
        "canonical_url": canonical_url,
        "wayback_url": public_wb_url,
        "ts": ts,
        "title": clean_title_from_slug(topic["slug"]),
        "subforum": "",
        "date_created": "",
        "excerpt": "",
        "years_mentioned": [],
        "architects_mentioned": [],
        "is_demolition": bool(re.search(r"\b(demo|demolish|demolition|teardown|razed)\b", topic["slug"], re.I)),
        "photos": []
    }

    raw_html = None
    for attempt in range(3):
        try:
            time.sleep(0.35 * (attempt + 1))
            req = urllib.request.Request(
                wb_url,
                headers={"User-Agent": "PreservationHouston-Atlas-Research/1.0"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw_html = resp.read().decode("utf-8", errors="ignore")
            break
        except Exception as e:
            last_err = str(e)
            time.sleep(0.8 * (attempt + 1))

    if not raw_html:
        result["fetch_error"] = last_err
        return result

    tm = re.search(r"<title>(.*?)</title>", raw_html, re.S | re.I)
    if tm:
        full_title = html_lib.unescape(tm.group(1)).strip()
        parts = [p.strip() for p in full_title.split(" - ")]
        if parts:
            result["title"] = re.sub(r"\s+", " ", parts[0])
        if len(parts) >= 2 and "HAIF" not in parts[1]:
            result["subforum"] = parts[1]

    ld_blocks = re.findall(
        r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>",
        raw_html,
        re.S | re.I
    )
    combined_text = []
    for block in ld_blocks:
        try:
            data = json.loads(block.strip())
            if isinstance(data, dict):
                if data.get("headline") or data.get("name"):
                    if not result["title"] or result["title"] == clean_title_from_slug(topic["slug"]):
                        result["title"] = html_lib.unescape(str(data.get("headline") or data.get("name"))).strip()
                if data.get("dateCreated"):
                    result["date_created"] = str(data["dateCreated"])[:10]
                if data.get("text"):
                    txt = html_lib.unescape(str(data["text"]))
                    txt = re.sub(r"\s+", " ", txt).strip()
                    combined_text.append(txt)
        except Exception:
            pass

    post_matches = re.findall(
        r'(?:data-role=["\']commentContent["\']|class=["\'][^"\']*post[-_]body[^"\']*["\'][^>]*)(.*?)</div>',
        raw_html,
        re.S | re.I
    )
    for pm in post_matches[:6]:
        clean_p = re.sub(r"<blockquote.*?</blockquote>", " ", pm, flags=re.S | re.I)
        clean_p = re.sub(r"<[^>]+>", " ", clean_p)
        clean_p = html_lib.unescape(clean_p)
        clean_p = re.sub(r"\s+", " ", clean_p).strip()
        if len(clean_p) > 35:
            combined_text.append(clean_p)

    full_corpus = " ".join(combined_text)
    if combined_text:
        excerpt = combined_text[0]
        if len(excerpt) > 340:
            excerpt = excerpt[:337].rsplit(" ", 1)[0] + "..."
        result["excerpt"] = excerpt

    yr_matches = re.findall(
        r"\b(?:built|constructed|completed|erected|opened|designed|dates?\s+to|circa|c\.)\s+(?:in\s+)?(18[3-9]\d|19\d\d|20[0-2]\d)\b",
        full_corpus,
        re.I
    )
    if not yr_matches:
        yr_matches = re.findall(r"\b(18[4-9]\d|19[0-8]\d)\b", result["title"])
    seen_yrs = []
    for y in yr_matches:
        yi = int(y)
        if 1836 <= yi <= 2025 and yi not in seen_yrs:
            seen_yrs.append(yi)
    result["years_mentioned"] = seen_yrs

    found_archs = []
    for arch in KNOWN_HOUSTON_ARCHITECTS:
        if re.search(r"\b" + re.escape(arch.strip()) + r"\b", full_corpus, re.I) or re.search(
            r"\b" + re.escape(arch.strip()) + r"\b", result["title"], re.I
        ):
            canon = arch.strip()
            if canon == "John Staub":
                canon = "John F. Staub"
            elif canon in ("Birdsall Briscoe", "B. P. Briscoe"):
                canon = "Birdsall P. Briscoe"
            elif canon == "Eugene Heiner":
                canon = "Eugene T. Heiner"
            elif canon == "Harvin Moore":
                canon = "Harvin C. Moore"
            elif canon == "Maurice Sullivan":
                canon = "Maurice J. Sullivan"
            elif canon in ("Irving Klein", "irving R. Klein"):
                canon = "Irving R. Klein"
            if canon not in found_archs:
                found_archs.append(canon)
    result["architects_mentioned"] = found_archs

    raw_imgs = re.findall(
        r"(?:https?:)?//(?:media|content)\.invisioncic\.com/w329674/[^\s\"'<>]+?\.(?:jpg|jpeg|png)",
        raw_html,
        re.I
    )
    clean_photos = []
    for img in raw_imgs:
        if img.startswith("//"):
            img = "https:" + img
        low = img.lower()
        if any(bad in low for bad in ["logo", "avatar", "profile", "emoticon", "emoji", "reaction", "badge", "icon", ".thumb."]):
            continue
        if img not in clean_photos:
            clean_photos.append(img)
    result["photos"] = clean_photos[:5]

    return result


def main():
    with open(TOPICS_PATH) as f:
        topics = json.load(f)
    print(f"Loaded {len(topics)} HAIF topics from {TOPICS_PATH}")

    slug_addr_exact = defaultdict(list)
    slug_addr_nodir = defaultdict(list)
    for t in topics:
        for norm1, norm2, _ in extract_slug_addresses(t["slug"]):
            slug_addr_exact[norm1].append(t)
            slug_addr_nodir[norm2].append(t)

    print(f"Indexed {len(slug_addr_exact)} exact street addresses from HAIF topic slugs")

    with open("app/public/data/curated_overrides.json") as f:
        overrides_doc = json.load(f)
    overrides = overrides_doc["overrides"]

    with open("pipeline/cache/coh_landmarks_enriched.json") as f:
        landmarks_raw = json.load(f)
    landmarks_list = landmarks_raw["features"] if isinstance(landmarks_raw, dict) and "features" in landmarks_raw else landmarks_raw

    with open("app/public/data/overlays.json") as f:
        overlays_doc = json.load(f)

    with open("app/public/data/buildings.geojson") as f:
        buildings_fc = json.load(f)

    with open("app/public/data/search_index.json") as f:
        search_idx_doc = json.load(f)
    search_items = search_idx_doc.get("items", search_idx_doc) if isinstance(search_idx_doc, dict) else search_idx_doc

    property_matches = defaultdict(list)

    def add_matches_for_address(prop_key, addr_str):
        if not prop_key or not addr_str:
            return
        na = norm_addr(addr_str)
        if not na:
            return
        na_nd = norm_addr_nodir(addr_str)
        hits = slug_addr_exact.get(na) or slug_addr_nodir.get(na_nd)
        if not hits:
            return
        existing_tids = {x["tid"] for x in property_matches[prop_key]}
        for h in hits:
            if h["tid"] not in existing_tids:
                property_matches[prop_key].append(h)
                existing_tids.add(h["tid"])

    for k, v in overrides.items():
        add_matches_for_address(k, v.get("address", ""))

    for feat in landmarks_list:
        p = feat.get("properties", feat)
        hcad = str(p.get("hcad_num") or p.get("HCAD_Account") or p.get("USER_HCAD_NUM") or "").strip()
        addr = p.get("address") or p.get("Matching_Address") or p.get("SITE_ADDR_1") or ""
        if hcad:
            add_matches_for_address(hcad, addr)

    for feat in overlays_doc.get("landmarks", {}).get("features", []):
        p = feat["properties"]
        hcad = str(p.get("hcad_num") or "").strip()
        addr = p.get("address") or ""
        if hcad:
            add_matches_for_address(hcad, addr)

    for feat in overlays_doc.get("good_brick_awards", {}).get("features", []):
        p = feat["properties"]
        key = p.get("building_id") or p.get("hcad_num") or ""
        if key:
            add_matches_for_address(key, p.get("address", ""))

    for feat in buildings_fc["features"]:
        p = feat["properties"]
        key = p.get("id") if "#" in str(p.get("id", "")) else (p.get("hcad_num") or p.get("id"))
        if key:
            add_matches_for_address(key, p.get("address", ""))

    for item in search_items:
        key = item.get("id") if "#" in str(item.get("id", "")) else (item.get("hcad") or item.get("hcad_num") or item.get("id"))
        addr = item.get("address") or item.get("a") or ""
        if key and addr:
            add_matches_for_address(key, addr)

    print(f"Properties matched via street address in HAIF slug: {len(property_matches)}")

    token_to_topics = defaultdict(list)
    for t in topics:
        toks = set(t["slug"].lower().split("-"))
        for tok in toks:
            if len(tok) >= 4 and not tok.isdigit() and tok not in GENERIC_NAME_STOPWORDS:
                token_to_topics[tok].append(t)

    def match_by_landmark_name(prop_key, raw_name):
        if not prop_key or not raw_name:
            return
        clean = re.sub(r"\([^)]*\)", " ", raw_name.lower())
        clean = re.sub(r"[^a-z0-9\s]", " ", clean)
        all_tokens = [w for w in clean.split() if len(w) >= 3]
        distinctive = [w for w in all_tokens if w not in GENERIC_NAME_STOPWORDS and not w.isdigit() and len(w) >= 4]
        if not distinctive:
            return
        rarest = min(distinctive, key=lambda w: len(token_to_topics.get(w, [])))
        candidates = token_to_topics.get(rarest, [])
        if not candidates or len(candidates) > 60:
            return
        existing_tids = {x["tid"] for x in property_matches.get(prop_key, [])}
        for cand in candidates:
            slug_toks = set(cand["slug"].lower().split("-"))
            if len(distinctive) >= 2:
                if all(d in slug_toks for d in distinctive[:2]):
                    phrase = "-".join(all_tokens[:2])
                    if phrase in cand["slug"].lower() or len(set(distinctive) & slug_toks) >= 2:
                        if cand["tid"] not in existing_tids:
                            property_matches[prop_key].append(cand)
                            existing_tids.add(cand["tid"])
            elif len(distinctive) == 1 and len(rarest) >= 6 and len(candidates) <= 10 and len(all_tokens) >= 2:
                phrase = "-".join(all_tokens[:2])
                if phrase in cand["slug"].lower():
                    if cand["tid"] not in existing_tids:
                        property_matches[prop_key].append(cand)
                        existing_tids.add(cand["tid"])

    for k, v in overrides.items():
        if v.get("building_name"):
            match_by_landmark_name(k, v["building_name"])

    for feat in landmarks_list:
        p = feat.get("properties", feat)
        hcad = str(p.get("hcad_num") or p.get("HCAD_Account") or p.get("USER_HCAD_NUM") or "").strip()
        name = p.get("name") or p.get("Historic_Name") or p.get("Common_Name") or p.get("PDLandMark") or ""
        if hcad and name:
            match_by_landmark_name(hcad, name)

    for feat in overlays_doc.get("landmarks", {}).get("features", []):
        p = feat["properties"]
        hcad = str(p.get("hcad_num") or "").strip()
        name = p.get("name") or ""
        if hcad and name:
            match_by_landmark_name(hcad, name)

    for feat in overlays_doc.get("good_brick_awards", {}).get("features", []):
        p = feat["properties"]
        key = p.get("building_id") or p.get("hcad_num") or ""
        name = p.get("project_name") or p.get("landmark_name") or ""
        if key and name:
            match_by_landmark_name(key, name)

    # Remove any empty keys
    property_matches = {k: v for k, v in property_matches.items() if v}
    total_matched_props = len(property_matches)
    unique_matched_tids = {}
    for k, tlist in property_matches.items():
        for t in tlist:
            unique_matched_tids[t["tid"]] = t

    print(f"Total Atlas properties matched to HAIF threads (address + landmark name): {total_matched_props}")
    print(f"Total unique HAIF threads matched to Atlas properties: {len(unique_matched_tids)}")

    # Keep only successful cached thread details (retry previous connection errors)
    thread_details = {}
    if os.path.exists(DETAILS_CACHE_PATH):
        with open(DETAILS_CACHE_PATH) as f:
            raw_cache = json.load(f)
        for k, v in raw_cache.items():
            if not v.get("fetch_error"):
                thread_details[int(k)] = v

    priority_tids = []
    for k, tlist in property_matches.items():
        for t in tlist[:2]:
            if t["tid"] not in thread_details and t["tid"] not in priority_tids:
                priority_tids.append(t["tid"])

    to_fetch = [unique_matched_tids[tid] for tid in priority_tids[:90]]
    print(f"Fetching Wayback Machine HTML & JSON-LD for {len(to_fetch)} matched HAIF threads (already cached OK={len(thread_details)})...")

    if to_fetch:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            futures = {ex.submit(fetch_wayback_thread_details, t): t["tid"] for t in to_fetch}
            done_cnt = 0
            for fut in concurrent.futures.as_completed(futures):
                tid = futures[fut]
                res = fut.result()
                thread_details[tid] = res
                done_cnt += 1
                if done_cnt % 15 == 0 or done_cnt == len(to_fetch):
                    ok_so_far = sum(1 for v in thread_details.values() if not v.get("fetch_error"))
                    print(f"  [{done_cnt}/{len(to_fetch)}] crawled (OK={ok_so_far}, latest: [{tid}] {res['title'][:50]} | subforum={res['subforum']})")

        with open(DETAILS_CACHE_PATH, "w") as f:
            json.dump({str(k): v for k, v in thread_details.items()}, f, indent=2)

    for tid, t in unique_matched_tids.items():
        if tid not in thread_details:
            thread_details[tid] = {
                "tid": tid,
                "slug": t["slug"],
                "canonical_url": f"https://www.houstonarchitecture.com/topic/{tid}-{t['slug']}/",
                "wayback_url": f"https://web.archive.org/web/{t['ts']}/{t['orig']}",
                "ts": t["ts"],
                "title": clean_title_from_slug(t["slug"]),
                "subforum": "",
                "date_created": "",
                "excerpt": "",
                "years_mentioned": [],
                "architects_mentioned": [],
                "is_demolition": bool(re.search(r"\b(demo|demolish|demolition|teardown|razed)\b", t["slug"], re.I)),
                "photos": []
            }

    # Build property metadata map from buildings.geojson + overrides + landmarks
    prop_meta = {}
    for feat in buildings_fc["features"]:
        p = feat["properties"]
        k = p.get("id") if "#" in str(p.get("id", "")) else (p.get("hcad_num") or p.get("id"))
        if k:
            prop_meta[k] = {
                "name": p.get("building_name") or p.get("landmark_name") or "",
                "address": p.get("address") or "",
                "year_built": int(p.get("year_built") or 0),
                "architect": p.get("architect") or ""
            }
    for k, v in overrides.items():
        base = prop_meta.get(k, {})
        prop_meta[k] = {
            "name": v.get("building_name") or base.get("name") or "",
            "address": v.get("address") or base.get("address") or "",
            "year_built": int(v.get("year_built") or base.get("year_built") or 0),
            "architect": v.get("architect") or base.get("architect") or ""
        }

    architect_discoveries = []
    year_discrepancies = []
    demolition_alerts = []
    photo_discoveries = []

    for prop_key, tlist in property_matches.items():
        meta = prop_meta.get(prop_key, {})
        cur_yr = int(meta.get("year_built") or 0)
        cur_arch = meta.get("architect") or ""
        cur_name = meta.get("name") or ""
        cur_addr = meta.get("address") or ""

        for t in tlist:
            det = thread_details[t["tid"]]
            if det.get("architects_mentioned") and not cur_arch:
                architect_discoveries.append({
                    "key": prop_key,
                    "name": cur_name,
                    "address": cur_addr,
                    "architects": det["architects_mentioned"],
                    "haif_title": det["title"],
                    "haif_url": det["canonical_url"],
                    "wayback_url": det["wayback_url"]
                })
            if det.get("years_mentioned"):
                for y in det["years_mentioned"]:
                    if cur_yr in {0, 1900, 1920, 1925, 1930, 1935, 1940, 1945, 1950} and y != cur_yr and (cur_yr == 0 or abs(y - cur_yr) <= 35):
                        year_discrepancies.append({
                            "key": prop_key,
                            "name": cur_name,
                            "address": cur_addr,
                            "current_year": cur_yr,
                            "haif_year": y,
                            "haif_title": det["title"],
                            "haif_url": det["canonical_url"],
                            "excerpt": det.get("excerpt", "")
                        })
            if det.get("is_demolition"):
                demolition_alerts.append({
                    "key": prop_key,
                    "name": cur_name,
                    "address": cur_addr,
                    "current_year": cur_yr,
                    "haif_title": det["title"],
                    "haif_url": det["canonical_url"],
                    "wayback_url": det["wayback_url"]
                })
            if det.get("photos"):
                photo_discoveries.append({
                    "key": prop_key,
                    "name": cur_name,
                    "address": cur_addr,
                    "photos": det["photos"],
                    "haif_title": det["title"],
                    "haif_url": det["canonical_url"],
                    "wayback_url": det["wayback_url"]
                })

    audit_summary = {
        "total_haif_topics_indexed": len(topics),
        "total_slug_street_addresses": len(slug_addr_exact),
        "total_atlas_properties_matched": total_matched_props,
        "total_unique_haif_threads_matched": len(unique_matched_tids),
        "total_deep_crawled_threads": sum(1 for v in thread_details.values() if not v.get("fetch_error") and (v.get("excerpt") or v.get("subforum") or v.get("photos"))),
        "architect_discoveries_count": len(architect_discoveries),
        "year_discrepancies_count": len(year_discrepancies),
        "demolition_alerts_count": len(demolition_alerts),
        "photo_discoveries_count": len(photo_discoveries),
        "sample_architect_discoveries": architect_discoveries[:30],
        "sample_year_discrepancies": year_discrepancies[:30],
        "sample_demolition_alerts": demolition_alerts[:30],
        "sample_photo_discoveries": photo_discoveries[:30],
        "property_to_tids": {k: [t["tid"] for t in v[:4]] for k, v in property_matches.items()}
    }

    with open(AUDIT_OUT_PATH, "w") as f:
        json.dump(audit_summary, f, indent=2)

    print("\n=== HAIF ENRICHMENT & AUDIT SUMMARY ===")
    for k in [
        "total_haif_topics_indexed",
        "total_slug_street_addresses",
        "total_atlas_properties_matched",
        "total_unique_haif_threads_matched",
        "total_deep_crawled_threads",
        "architect_discoveries_count",
        "year_discrepancies_count",
        "demolition_alerts_count",
        "photo_discoveries_count",
    ]:
        print(f"  {k}: {audit_summary[k]}")


if __name__ == "__main__":
    main()
