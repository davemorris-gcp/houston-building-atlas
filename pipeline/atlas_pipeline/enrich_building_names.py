#!/usr/bin/env python3
"""
Enrich Houston Building Atlas structures with Primary Building Names (`building_name` / `landmark_name`),
Historical & Colloquial Alternate Names (`alt_names`), and Name Provenance (`name_source`).

Sources integrated:
1. City of Houston HAHC Landmark Designation Reports (`coh_landmarks_enriched.json` + `landmark_pdf_texts.json`)
2. Preservation Houston Good Brick Awards (`overlays.json` -> `good_brick_awards`)
3. Houston Architecture Info Forum (`haif_index.json` -> 5,576 threads across 3,894 HCAD parcels & 6,248 street addresses)
4. Curated Archival & Campus Overrides (`curated_overrides.json` + `buildings.geojson`)
5. Iconic Multi-Name Houston Buildings registry (original historic name vs. adaptive reuse / modern tenant name)
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

CACHE_DIR = Path("pipeline/cache")
DATA_DIR = Path("app/public/data")

# Neighborhoods or metadata notes in parentheses that should NOT be extracted as standalone building aliases
NEIGHBORHOOD_OR_META_PAREN_RE = re.compile(
    r"^(?:"
    r"18\d\d|19\d\d|20\d\d|c\.\s*\d{4}|built\s+\d{4}|"
    r"historic\b.*|demolished.*|relocated.*|moved.*|burned.*|site\b.*|"
    r"building\s+[a-z0-9]+|wing\b.*|phase\b.*|addition\b.*|rear\b.*|annex\b|"
    r"cherryhurst|waverly court|southside place|braeswood|meyerland|river oaks|"
    r"montrose|heights|houston heights|woodland heights|norhill|eastwood|"
    r"idylwood|broadacres|shadow lawn|westmoreland|courtlandt place|avondale|"
    r"sixth ward|old sixth ward|first ward|second ward|third ward|fourth ward|fifth ward|"
    r"downtown|midtown|museum district|medical center|eado|east end|near northside|"
    r"glenbrook valley|oak forest|garden oaks|spring branch|memorial|bellaire|west university|"
    r"smom|inc\.?|llc|ltd\.?"
    r")$",
    re.I,
)

# Generic structural/auxiliary labels that are NOT proper historical building names
GENERIC_AUXILIARY_NAME_RE = re.compile(
    r"(?:"
    r"carriage house|auxiliary structure|detached garage|garage apartment|outbuilding|"
    r"storage tank|process unit|industrial structure|petrochemical|refinery tank|"
    r"bus shelter|transit canopy|parking garage|loading dock|utility building|"
    r"courtyard wing|west wing|east wing|north wing|south wing"
    r")",
    re.I,
)

STREET_ADDRESS_ONLY_RE = re.compile(
    r"^\d{1,5}(?:\s*-\s*\d{1,5})?\s+(?:[NSEW]\.?\s+)?[A-Za-z0-9\.\s]+\b"
    r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Lane|Ln|Court|Ct|Place|Pl|Circle|Cir|Parkway|Pkwy|Freeway|Fwy|Highway|Hwy|Loop|Way)\.?$",
    re.I,
)

GENERIC_HAIF_PREFIX_RE = re.compile(
    r"^(?:demo|demolition|teardown|razed|new|proposed|future|question|help|unknown|mystery|"
    r"vacant|lot|building|buildings|house|homes|home|site|construction|rendering|retail|"
    r"office|warehouse|parking|garage|apartments|condos|townhomes|development|project|"
    r"redevelopment|renovation|rehab|remodel|fire|abandoned|historic|old|former|"
    r"\d+\s+story|\d+-story|\d+\s+flr)\b",
    re.I,
)

# Curated canonical multi-name Houston landmarks where historic name and modern/adaptive-reuse names are both widely used
ICONIC_MULTI_NAME_BY_HCAD: dict[str, dict] = {
    "0010810000007": {
        "primary": "Gulf Building",
        "alts": [
            "JPMorgan Chase Building",
            "Texas Commerce Bank Building",
            "National Bank of Commerce Building",
        ],
        "source": "COH Landmark Designation Report & HAIF Archive",
    },
    "0010910000015": {
        "primary": "Niels & Mellie Esperson Buildings",
        "alts": ["Niels Esperson Building (1927)", "Mellie Esperson Building (1941)"],
        "source": "COH Landmark Designation Report",
    },
    "0010420000006": {
        "primary": "The Hogg Building",
        "alts": ["Pappas Building", "Armor Building"],
        "source": "COH Landmark Designation Report",
    },
    "1210850000002": {
        "primary": "The Humble Building",
        "alts": [
            "Humble Oil & Refining Co. Building",
            "Courtyard by Marriott Downtown",
        ],
        "source": "COH Landmark Designation Report & Good Brick Award",
    },
    "0020540000001": {
        "primary": "Exxon Building",
        "alts": ["Humble Oil Building (1963)", "800 Bell"],
        "source": "Houston Architecture Forum (HAIF)",
    },
    "0011370000011": {
        "primary": "City National Bank Building",
        "alts": ["First City National Bank", "Texas American Building", "First City National Bank Annex"],
        "source": "COH Landmark Report & HAIF Archive",
    },
    "0011480000019": {
        "primary": "Julia Ideson Building",
        "alts": ["Central Houston Public Library (1926)"],
        "source": "COH Landmark Designation Report",
    },
    "0010200000001": {
        "primary": "Union National Bank Building",
        "alts": ["Hotel Icon"],
        "source": "COH Landmark Designation Report",
    },
    "1238970010001": {
        "primary": "Merchants & Manufacturers (M&M) Building",
        "alts": ["One Main Building (UH-Downtown)", "M&M Building"],
        "source": "COH Landmark Designation Report & NRHP",
    },
    "1344850010002": {
        "primary": "Last Concert Café (1949 Café Building)",
        "alts": ["Elena's Spanish Cafe", "Richey-Fant House Compound"],
        "source": "Preservation Houston Curated Archive",
    },
    "1344850010002#richey_fant_1850": {
        "primary": "Richey-Fant House",
        "alts": ["Last Concert Café (1850 Cottage)", "Benjamin Richey House"],
        "source": "COH Landmark Designation Report",
    },
    "0261330000018": {
        "primary": "Stewart House",
        "alts": ["The Marlene", "Marlene Inn B&B"],
        "source": "Preservation Houston Good Brick Award & HAIF",
    },
    "0341890010001": {
        "primary": "Butler Brothers Building",
        "alts": ["Houston Permitting Center"],
        "source": "COH Landmark Designation Report",
    },
    "0010870000001": {
        "primary": "Cheek-Neal Coffee Building",
        "alts": ["Sampson Street Lofts", "Maxwell House Coffee Plant"],
        "source": "COH Landmark Report & Good Brick Award",
    },
    "0382190000001": {
        "primary": "Link-Lee House",
        "alts": ["University of St. Thomas Administration Building"],
        "source": "COH Landmark Designation Report",
    },
    "0201650000001": {
        "primary": "Oriental Textile Mill",
        "alts": ["Heights Clock Tower", "Lawrence Street Textile Mill"],
        "source": "COH Landmark Designation Report",
    },
    "0010660000001": {
        "primary": "Jones Hall for the Performing Arts",
        "alts": ["Houston City Auditorium (1910–1963 Site)"],
        "source": "Preservation Houston & HAIF Archive",
    },
    "0010780000001": {
        "primary": "Sam Houston U.S. Post Office and Custom House",
        "alts": ["1911 Federal Building", "U.S. Custom House", "Houston Armed Forces Induction Center"],
        "source": "COH Landmark Designation Report & HAIF",
    },
}


def norm_addr(s: str | None) -> str:
    if not s:
        return ""
    s = s.upper().strip()
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    s = re.sub(
        r"\b(STREET|ST|AVENUE|AVE|BOULEVARD|BLVD|DRIVE|DR|ROAD|RD|LANE|LN|COURT|CT|PLACE|PL|CIRCLE|CIR|PARKWAY|PKWY|FREEWAY|FWY|HIGHWAY|HWY)\b",
        "",
        s,
    )
    return " ".join(s.split())


def is_valid_building_name(cand: str, addr: str = "") -> bool:
    if not cand:
        return False
    c = cand.strip(" -:,;.\"'\t\r\n")
    if len(c) < 3 or len(c) > 95:
        return False
    if c.isdigit() or c.startswith("HCAD "):
        return False
    if NEIGHBORHOOD_OR_META_PAREN_RE.match(c):
        return False
    if GENERIC_AUXILIARY_NAME_RE.search(c):
        return False
    if STREET_ADDRESS_ONLY_RE.match(c):
        return False
    if addr and norm_addr(c) == norm_addr(addr):
        return False
    return True


def split_primary_and_alts(raw_label: str, addr: str = "") -> tuple[str, list[str]]:
    """
    Splits a combined building name string into `(primary_name, alt_names)` when it contains:
      - Parenthetical aliases: `The Hogg Building (Pappas Building)` or `Magnolia Brewery (aka Magnolia Ballroom)`
      - Slash-separated dual names: `Stewart House / The Marlene`
    Preserves parentheticals that are pure neighborhood/date/campus qualifiers.
    """
    if not raw_label:
        return "", []
    s = re.sub(r"\s*-\s*DEMOLISHED\b", "", str(raw_label), flags=re.I).strip()
    if GENERIC_AUXILIARY_NAME_RE.search(s):
        return s, []

    alts: list[str] = []

    def repl_paren(m: re.Match) -> str:
        inner = m.group(1).strip()
        clean_inner = re.sub(
            r"^(?:aka|a\.k\.a\.|also known as|formerly known as|formerly|now known as|now|later)\s+",
            "",
            inner,
            flags=re.I,
        ).strip(" -:,;.")
        if not clean_inner:
            return ""
        if NEIGHBORHOOD_OR_META_PAREN_RE.match(clean_inner):
            return m.group(0)
        if STREET_ADDRESS_ONLY_RE.match(clean_inner) or ("/" in clean_inner and re.search(r"\d+\s+\w+", clean_inner)):
            return ""
        if is_valid_building_name(clean_inner, addr):
            alts.append(clean_inner)
            return ""
        return m.group(0)

    s_clean = re.sub(r"\(([^()]+)\)", repl_paren, s).strip()
    s_clean = re.sub(r"\s{2,}", " ", s_clean).strip(" -:,;")

    if " / " in s_clean:
        parts = [p.strip() for p in s_clean.split(" / ") if p.strip()]
        valid_parts = [p for p in parts if is_valid_building_name(p, addr)]
        if len(valid_parts) >= 2:
            s_clean = valid_parts[0]
            for vp in valid_parts[1:]:
                if vp.lower() not in [a.lower() for a in alts]:
                    alts.append(vp)

    # If the primary name is just "Building at 1204 Nance Street" or "411 Fannin Street" and we have a real alias, promote the alias!
    if alts and (
        re.match(r"^Building at\s+\d+", s_clean, re.I)
        or STREET_ADDRESS_ONLY_RE.match(s_clean)
    ):
        old_p = s_clean
        s_clean = alts.pop(0)
        if not re.match(r"^Building at\s+", old_p, re.I):
            alts.append(old_p)

    return s_clean, alts


def extract_aka_from_prose(text: str, primary_name: str = "", addr: str = "") -> list[str]:
    """
    Extracts explicit historical/modern aliases from COH Landmark Report prose, such as:
      "The City National Bank Building, now known as the Texas American Building, was..."
      "The John G. Logue House, also known as the Milford House..."
    """
    if not text:
        return []
    found: list[str] = []
    patterns = [
        r"(?:also known as|now known as|formerly known as|originally known as|later known as|popularly known as)\s+(?:the\s+)?([A-Z][A-Za-z0-9\s\.\-&'\u2019]{3,55}?)(?=[,.;\n\r()]|\s+(?:was|is|in|at|on|which|and|located|built|designed|constructed|has|had))",
    ]
    bad_starts = (
        "a speculative",
        "a wedding",
        "a two-story",
        "a one-story",
        "a three-story",
        "a large",
        "a historic",
        "an historic",
        "lot ",
        "lots ",
        "tract ",
        "block ",
        "sixth ward",
        "fourth ward",
        "third ward",
        "heights",
        "rice university",
    )
    for pat in patterns:
        for m in re.finditer(pat, text):
            cand = m.group(1).strip(" -:,;.\"'\t\r\n")
            cand = re.sub(r"^the\s+", "", cand, flags=re.I).strip()
            low = cand.lower()
            if any(low.startswith(b) for b in bad_starts):
                continue
            if not re.search(
                r"\b(Building|House|Home|Hotel|Hall|Tower|Center|Bank|Mill|Brewery|Ballroom|School|Church|Hospital|Museum|Terminal|Theater|Theatre|Lofts|Place|Court|Annex|Club|Bungalow|Cottage|Mansion|Villa|Apartments|Arms|Manor|Works|Company|Depot|Station|Market|Bakery|Shop|Cafe|Café)\b",
                cand,
                re.I,
            ):
                continue
            if primary_name and cand.lower() == primary_name.lower():
                continue
            if is_valid_building_name(cand, addr) and cand.lower() not in [f.lower() for f in found]:
                found.append(cand)
    return found


def extract_names_from_haif_title(title: str, addr: str = "") -> list[str]:
    """
    Extracts clean building or institution names from a HAIF thread title like:
      "Peden Iron Works At 700 N. San Jacinto St."
      "Annunciation Catholic Church At 1618 Texas Ave"
      "Marlene Inn Bb 109 Stratford St"
    """
    if not title:
        return []
    t = re.sub(r"^Houston\s+photo:\s*", "", str(title), flags=re.I).strip()
    m = re.split(r"\s+(?:\bat\b|-)\s+\d{1,5}\b", t, maxsplit=1, flags=re.I)
    if len(m) < 2:
        return []
    raw_prefix = m[0].strip(" -:,;()")
    if not raw_prefix or len(raw_prefix) < 4:
        return []
    if GENERIC_HAIF_PREFIX_RE.match(raw_prefix):
        return []
    if STREET_ADDRESS_ONLY_RE.match(raw_prefix):
        return []

    parts = re.split(r"\s*(?:/|\baka\b|\bformerly\b)\s*", raw_prefix, flags=re.I)
    out: list[str] = []
    for p in parts:
        p_clean = p.strip(" -:,;()")
        p_clean = re.sub(r"\s+\d{2,5}\s+[A-Za-z]+\s+(?:St|Ave|Blvd|Dr|Rd)\.?$", "", p_clean, flags=re.I).strip()
        if not is_valid_building_name(p_clean, addr):
            continue
        if GENERIC_HAIF_PREFIX_RE.match(p_clean):
            continue
        if p_clean.lower() not in [x.lower() for x in out]:
            out.append(p_clean)
    return out


def merge_unique_names(primary: str, candidates: list[str], addr: str = "") -> list[str]:
    """Returns deduplicated list of alternate names that are distinct from `primary` and `addr`."""
    p_low = (primary or "").strip().lower()
    p_norm = re.sub(r"^the\s+", "", p_low)
    addr_norm = norm_addr(addr)
    result: list[str] = []
    seen_norms: set[str] = {p_low, p_norm}
    for cand in candidates:
        if not cand:
            continue
        c = cand.strip(" -:,;.")
        if not is_valid_building_name(c, addr):
            continue
        c_low = c.lower()
        c_norm = re.sub(r"^the\s+", "", c_low)
        if c_low in seen_norms or c_norm in seen_norms:
            continue
        # Also skip if one is just a trivial substring of primary (like "Harris County Courthouse" vs "1910 Harris County Courthouse")
        if addr_norm and norm_addr(c) == addr_norm:
            continue
        seen_norms.add(c_low)
        seen_norms.add(c_norm)
        result.append(c)
    return result[:4]


def main() -> None:
    coh_enriched = json.loads((CACHE_DIR / "coh_landmarks_enriched.json").read_text())
    pdf_texts = json.loads((CACHE_DIR / "landmark_pdf_texts.json").read_text())
    overlays_path = DATA_DIR / "overlays.json"
    overrides_path = DATA_DIR / "curated_overrides.json"
    buildings_path = DATA_DIR / "buildings.geojson"
    haif_path = DATA_DIR / "haif_index.json"
    search_path = DATA_DIR / "search_index.json"

    overlays = json.loads(overlays_path.read_text())
    overrides_doc = json.loads(overrides_path.read_text())
    overrides = overrides_doc.get("overrides", {})
    buildings_doc = json.loads(buildings_path.read_text())
    haif_doc = json.loads(haif_path.read_text())
    search_doc = json.loads(search_path.read_text())

    pdf_text_by_url = {
        url: (rec.get("text") or "")
        for url, rec in pdf_texts.items()
        if isinstance(rec, dict) and rec.get("text")
    }

    # Track how many distinct primary/campus buildings share an HCAD parcel (ignoring #aux_ secondary garages)
    hcad_building_counts: dict[str, int] = defaultdict(int)
    addr_building_counts: dict[str, int] = defaultdict(int)
    for k, ov in overrides.items():
        if not isinstance(ov, dict) or ov.get("suppress_only"):
            continue
        if "#aux_" in str(k):
            continue
        base_h = str(ov.get("hcad_num") or k.split("#")[0] or "").strip()
        if base_h:
            hcad_building_counts[base_h] += 1
        na = norm_addr(ov.get("address"))
        if na:
            addr_building_counts[na] += 1

    by_key_names: dict[str, dict] = defaultdict(lambda: {"primaries": [], "alts": []})
    by_addr_names: dict[str, dict] = defaultdict(lambda: {"primaries": [], "alts": []})

    def register_name(
        key: str,
        addr: str,
        primary: str,
        alts: list[str],
        priority: int,
        source: str,
        allow_addr_index: bool = True,
    ) -> None:
        if not is_valid_building_name(primary, addr):
            primary = ""
        valid_alts = [a for a in alts if is_valid_building_name(a, addr)]
        if key:
            rec = by_key_names[key]
            if primary:
                rec["primaries"].append((priority, primary, source))
            rec["alts"].extend(valid_alts)
        na = norm_addr(addr)
        if allow_addr_index and na and addr_building_counts.get(na, 0) <= 1 and "#" not in str(key):
            arec = by_addr_names[na]
            if primary:
                arec["primaries"].append((priority, primary, source))
            arec["alts"].extend(valid_alts)

    # Priority 0: Iconic curated multi-name Houston buildings
    for k, info in ICONIC_MULTI_NAME_BY_HCAD.items():
        register_name(k, "", info["primary"], info["alts"], 0, info["source"], allow_addr_index=False)

    # Priority 1: COH Landmark Designation Reports (`coh_landmarks_enriched.json` + `overlays.json` landmarks)
    for enr in coh_enriched:
        raw_name = enr.get("name") or ""
        addr = enr.get("address") or ""
        hcad = str(enr.get("hcad_num") or enr.get("hcad_num_resolved") or "").strip()
        pdf_url = enr.get("report_pdf_url") or ""
        full_txt = pdf_text_by_url.get(pdf_url, "")
        summary_txt = enr.get("pdf_summary") or ""

        primary, alts = split_primary_and_alts(raw_name, addr)
        prose_akas = extract_aka_from_prose(f"{summary_txt}\n{full_txt[:3500]}", primary, addr)
        all_alts = merge_unique_names(primary, alts + prose_akas, addr)
        register_name(hcad, addr, primary, all_alts, 1, "COH Landmark Designation Report")

    lm_with_alts = 0
    for feat in overlays.get("landmarks", {}).get("features", []):
        p = feat.get("properties") or {}
        raw_name = p.get("name") or ""
        addr = p.get("address") or ""
        hcad = str(p.get("hcad_num") or "").strip()
        pdf_url = p.get("report_pdf_url") or ""
        full_txt = pdf_text_by_url.get(pdf_url, "")
        summary_txt = p.get("pdf_summary") or ""

        primary, alts = split_primary_and_alts(raw_name, addr)
        prose_akas = extract_aka_from_prose(f"{summary_txt}\n{full_txt[:3500]}", primary, addr)
        haif_alts = []
        for th in p.get("haif_threads") or []:
            haif_alts.extend(extract_names_from_haif_title(th.get("title", ""), addr))
        if hcad in ICONIC_MULTI_NAME_BY_HCAD:
            ic = ICONIC_MULTI_NAME_BY_HCAD[hcad]
            primary = ic["primary"]
            all_alts = merge_unique_names(primary, ic["alts"] + alts + prose_akas + haif_alts, addr)
        else:
            all_alts = merge_unique_names(primary, alts + prose_akas + haif_alts, addr)
        if primary:
            p["building_name"] = primary
            p["name"] = primary
        if all_alts:
            p["alt_names"] = all_alts
            lm_with_alts += 1
        p["name_source"] = "COH Landmark Designation Report"
        register_name(hcad, addr, primary, all_alts, 1, "COH Landmark Designation Report")

    # Priority 2: Preservation Houston Good Brick Awards (`overlays.json` -> `good_brick_awards`)
    gb_with_alts = 0
    for feat in overlays.get("good_brick_awards", {}).get("features", []):
        p = feat.get("properties") or {}
        raw_name = p.get("landmark_name") or p.get("name") or ""
        addr = p.get("address") or ""
        hcad = str(p.get("hcad_num") or "").strip()
        bid = str(p.get("building_id") or "").strip()
        primary, alts = split_primary_and_alts(raw_name, addr)
        haif_alts = []
        for th in p.get("haif_threads") or []:
            haif_alts.extend(extract_names_from_haif_title(th.get("title", ""), addr))
        all_alts = merge_unique_names(primary, alts + haif_alts, addr)
        if primary:
            p["building_name"] = primary
            p["landmark_name"] = primary
            p["name"] = primary
        if all_alts:
            p["alt_names"] = all_alts
            gb_with_alts += 1
        p["name_source"] = "Preservation Houston Good Brick Award"
        is_multi_campus = hcad_building_counts.get(hcad, 0) > 1
        if bid and "#" in bid:
            register_name(bid, addr, primary, all_alts, 2, "Preservation Houston Good Brick Award", allow_addr_index=False)
        elif not is_multi_campus:
            register_name(hcad, addr, primary, all_alts, 2, "Preservation Houston Good Brick Award")

    # Priority 3: Curated Overrides (`curated_overrides.json`)
    for k, ov in overrides.items():
        if not isinstance(ov, dict) or ov.get("suppress_only"):
            continue
        if "#aux_" in str(k):
            continue
        addr = ov.get("address") or ""
        raw_lm = ov.get("landmark_name") or ""
        raw_bn = ov.get("building_name") or ""
        p1, a1 = split_primary_and_alts(raw_lm, addr)
        p2, a2 = split_primary_and_alts(raw_bn, addr)
        primary = p1 or p2
        extra = ([p2] if p1 and p2 and p1.lower() != p2.lower() else []) + a1 + a2
        src = (
            "COH Landmark Designation Report"
            if ov.get("landmark_report_url") or ov.get("landmark_code")
            else "Preservation Houston Curated Archive"
        )
        if primary:
            is_sub = "#" in str(k) or hcad_building_counts.get(str(ov.get("hcad_num") or ""), 0) > 1
            register_name(k, addr, primary, extra, 2, src, allow_addr_index=not is_sub)

    # Priority 4: HAIF Forum Threads (`haif_index.json`)
    threads = haif_doc.get("threads", {})
    by_key_haif = haif_doc.get("by_key", {})
    by_addr_haif = haif_doc.get("by_addr", {})

    for key, tids in by_key_haif.items():
        base_h = key.split("#")[0]
        if "#" not in key and hcad_building_counts.get(base_h, 0) > 1:
            continue
        extracted: list[str] = []
        for tid in tids:
            rec = threads.get(str(tid))
            if not rec:
                continue
            for nm in extract_names_from_haif_title(rec.get("t", "")):
                if re.search(r"\bin\s+(?:dallas|austin|san antonio|fort worth|el paso|new orleans|chicago|atlanta)\b", nm, re.I):
                    continue
                if nm.lower() not in [x.lower() for x in extracted]:
                    extracted.append(nm)
        if extracted:
            register_name(
                key,
                "",
                extracted[0],
                extracted[1:],
                4,
                "Houston Architecture Forum (HAIF)",
                allow_addr_index=False,
            )

    for na, tids in by_addr_haif.items():
        if addr_building_counts.get(na, 0) > 1:
            continue
        extracted = []
        for tid in tids:
            rec = threads.get(str(tid))
            if not rec:
                continue
            for nm in extract_names_from_haif_title(rec.get("t", "")):
                if re.search(r"\bin\s+(?:dallas|austin|san antonio|fort worth|el paso|new orleans|chicago|atlanta)\b", nm, re.I):
                    continue
                if nm.lower() not in [x.lower() for x in extracted]:
                    extracted.append(nm)
        if extracted:
            arec = by_addr_names[na]
            arec["primaries"].append((4, extracted[0], "Houston Architecture Forum (HAIF)"))
            arec["alts"].extend(extracted[1:])

    def resolve_best_name_and_alts(key: str, hcad: str, addr: str) -> tuple[str, list[str], str]:
        if "#aux_" in str(key):
            return "", [], ""
        na = norm_addr(addr)
        is_sub_building = "#" in str(key)
        is_multi_campus_hcad = hcad_building_counts.get(hcad, 0) > 1
        primaries: list[tuple[int, str, str]] = []
        raw_alts: list[str] = []

        if key and key in by_key_names:
            primaries.extend(by_key_names[key]["primaries"])
            raw_alts.extend(by_key_names[key]["alts"])
        if not is_sub_building and not is_multi_campus_hcad:
            if hcad and hcad != key and hcad in by_key_names:
                primaries.extend(by_key_names[hcad]["primaries"])
                raw_alts.extend(by_key_names[hcad]["alts"])
            if na and addr_building_counts.get(na, 0) <= 1 and na in by_addr_names:
                primaries.extend(by_addr_names[na]["primaries"])
                raw_alts.extend(by_addr_names[na]["alts"])

        if not primaries:
            return "", [], ""

        primaries.sort(key=lambda x: x[0])
        _, best_name, best_source = primaries[0]
        other_primary_names = [nm for _, nm, _ in primaries[1:]]
        sources_set = {src for _, _, src in primaries}
        if len(sources_set) > 1 and "COH Landmark Designation Report" in sources_set and "Houston Architecture Forum (HAIF)" in sources_set:
            best_source = "COH Landmark Report & HAIF Archive"
        elif len(sources_set) > 1 and "Preservation Houston Good Brick Award" in sources_set:
            best_source = f"{best_source} & Good Brick Archive" if "Good Brick" not in best_source else best_source

        merged_alts = merge_unique_names(best_name, other_primary_names + raw_alts, addr)
        return best_name, merged_alts, best_source

    # Apply resolved `building_name`, `alt_names`, `name_source` to `curated_overrides.json`
    ov_named_count = 0
    ov_multi_name_count = 0
    for k, ov in overrides.items():
        if not isinstance(ov, dict) or ov.get("suppress_only"):
            continue
        if "#aux_" in str(k):
            ov.pop("alt_names", None)
            continue
        hcad = str(ov.get("hcad_num") or k.split("#")[0] or "").strip()
        addr = ov.get("address") or ""
        best_name, merged_alts, best_src = resolve_best_name_and_alts(k, hcad, addr)
        if best_name:
            ov["building_name"] = best_name
            ov["landmark_name"] = best_name
            ov["name_source"] = best_src
            ov_named_count += 1
        if merged_alts:
            ov["alt_names"] = merged_alts
            ov_multi_name_count += 1
        else:
            ov.pop("alt_names", None)

    # Apply resolved `building_name`, `alt_names`, `name_source` to `buildings.geojson`
    bld_named_count = 0
    bld_multi_name_count = 0
    for feat in buildings_doc.get("features", []):
        p = feat.get("properties") or {}
        fid = str(p.get("id") or p.get("building_id") or "").strip()
        if "#aux_" in fid:
            p.pop("alt_names", None)
            continue
        hcad = str(p.get("hcad_num") or "").strip()
        addr = p.get("address") or ""
        existing_lm = p.get("landmark_name") or ""
        existing_bn = p.get("building_name") or ""
        if existing_lm or existing_bn:
            p1, a1 = split_primary_and_alts(existing_lm or existing_bn, addr)
            if p1 and is_valid_building_name(p1, addr):
                register_name(fid or hcad, addr, p1, a1, 2, "Preservation Houston Curated Archive", allow_addr_index=False)

        best_name, merged_alts, best_src = resolve_best_name_and_alts(fid, hcad, addr)
        if best_name:
            p["building_name"] = best_name
            p["landmark_name"] = best_name
            p["name_source"] = best_src
            bld_named_count += 1
        if merged_alts:
            p["alt_names"] = merged_alts
            bld_multi_name_count += 1
        else:
            p.pop("alt_names", None)

    # Embed compact `names_by_key` and `names_by_addr` in `haif_index.json`
    names_by_key_compact = {}
    for k in set(list(by_key_names.keys()) + list(by_key_haif.keys())):
        if "#aux_" in str(k):
            continue
        base_hcad = k.split("#")[0]
        best_name, merged_alts, best_src = resolve_best_name_and_alts(k, base_hcad, "")
        if best_name:
            entry: dict = {"n": best_name}
            if merged_alts:
                entry["a"] = merged_alts
            if best_src:
                entry["s"] = best_src
            names_by_key_compact[k] = entry

    names_by_addr_compact = {}
    for na in by_addr_names.keys():
        if addr_building_counts.get(na, 0) > 1:
            continue
        primaries = sorted(by_addr_names[na]["primaries"], key=lambda x: x[0])
        if not primaries:
            continue
        best_name = primaries[0][1]
        best_src = primaries[0][2]
        other_primaries = [nm for _, nm, _ in primaries[1:]]
        merged_alts = merge_unique_names(best_name, other_primaries + by_addr_names[na]["alts"], "")
        entry = {"n": best_name}
        if merged_alts:
            entry["a"] = merged_alts
        if best_src:
            entry["s"] = best_src
        names_by_addr_compact[na] = entry

    haif_doc["names_by_key"] = names_by_key_compact
    haif_doc["names_by_addr"] = names_by_addr_compact

    # Enrich `search_index.json` with `building_name`, `alt_names`, and formatted sublabels
    search_named_count = 0
    search_alt_count = 0
    for item in search_doc:
        fid = str(item.get("id") or "").strip()
        if "#aux_" in fid:
            item.pop("alt_names", None)
            continue
        hcad = str(item.get("hcad_num") or "").strip()
        lbl = str(item.get("label") or "").strip()
        sub = str(item.get("sublabel") or "").strip()
        # Strip any prior "AKA: ... • " prefix so we rebuild cleanly
        sub = re.sub(r"^AKA:\s*[^•]+•\s*", "", sub).strip()

        lbl_no_addr = re.sub(r"\s*(?:—\s*\d+\s+.*|\(\d+\s+[^)]+\))$", "", lbl).strip()
        lbl_primary, lbl_alts = split_primary_and_alts(lbl_no_addr, "")
        addr_cand = lbl if re.match(r"^\d+\s+", lbl) else ""
        best_name, merged_alts, best_src = resolve_best_name_and_alts(fid, hcad, addr_cand)
        if not best_name and lbl_primary and is_valid_building_name(lbl_primary, addr_cand):
            best_name = lbl_primary
        merged_alts = merge_unique_names(best_name or lbl_primary, merged_alts + lbl_alts, addr_cand)

        if best_name:
            item["building_name"] = best_name
            item["label"] = best_name
            if addr_cand and addr_cand.lower() not in sub.lower():
                sub = f"{addr_cand} • {sub}" if sub else addr_cand
            search_named_count += 1
        if merged_alts:
            item["alt_names"] = merged_alts
            aka_str = f"AKA: {', '.join(merged_alts[:2])}"
            sub = f"{aka_str} • {sub}" if sub else aka_str
            search_alt_count += 1
        else:
            item.pop("alt_names", None)
        item["sublabel"] = sub
        if best_src:
            item["name_source"] = best_src

    overlays_path.write_text(json.dumps(overlays, separators=(",", ":")))
    overrides_path.write_text(json.dumps(overrides_doc, separators=(",", ":")))
    buildings_path.write_text(json.dumps(buildings_doc, separators=(",", ":")))
    haif_path.write_text(json.dumps(haif_doc, separators=(",", ":")))
    search_path.write_text(json.dumps(search_doc, separators=(",", ":")))

    print(f"Landmarks in overlays.json with alternate names: {lm_with_alts}")
    print(f"Good Brick Awards in overlays.json with alternate names: {gb_with_alts}")
    print(f"curated_overrides.json: {ov_named_count} named buildings ({ov_multi_name_count} with multiple/historical names)")
    print(f"buildings.geojson: {bld_named_count} named buildings ({bld_multi_name_count} with multiple/historical names)")
    print(f"haif_index.json runtime lookup: {len(names_by_key_compact)} named HCAD/building keys, {len(names_by_addr_compact)} named street addresses")
    print(f"search_index.json: {search_named_count} named entries ({search_alt_count} with searchable historical/alternate names)")


if __name__ == "__main__":
    main()
