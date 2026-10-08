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

import html
import json
import re
import urllib.parse
from collections import defaultdict
from pathlib import Path

CACHE_DIR = Path("pipeline/cache")
DATA_DIR = Path("app/public/data")

# Fused words caused by Invision Community stripping "/" from HAIF thread titles when generating URL slugs
FUSED_SLASH_REPLACEMENTS: list[tuple[str, str]] = [
    (r"\bApartmentsphysicians\s+Surgeons\b", "Apartments / Physicians & Surgeons"),
    (r"\bApartmentsphysicians\b", "Apartments / Physicians"),
    (r"\bApartmentshotel\b", "Apartments / Hotel"),
    (r"\bStoreimperial\b", "Store / Imperial"),
    (r"\bSupplyhugos\b", "Supply / Hugo's"),
    (r"\bMansionbayou\s+Bendmuseum\b", "Mansion / Bayou Bend Museum"),
    (r"\bMansionbayou\b", "Mansion / Bayou"),
    (r"\bBendmuseum\b", "Bend Museum"),
    (r"\bMansionclayton\b", "Mansion / Clayton"),
    (r"\bSchoolnorthside\b", "School / Northside"),
    (r"\bSchoollantrip\b", "School / Lantrip"),
    (r"\bScientistthe\b", "Scientist / The"),
    (r"\bBuildinggragg\b", "Building / Gragg"),
    (r"\bBuildingbering\b", "Building / Bering"),
    (r"\bBuildingschool\b", "Building / School"),
    (r"\bBuildingtexas\b", "Building / Texas"),
    (r"\bBuildingwestheimer\b", "Building / Westheimer"),
    (r"\bLukesbaylor\b", "Luke's / Baylor"),
    (r"\bCampusinnovation\b", "Campus / Innovation"),
    (r"\bHospitaluthealth\b", "Hospital / UTHealth"),
    (r"\bHospitalpark\b", "Hospital / Park"),
    (r"\bTowerneurosensory\b", "Tower / Neurosensory"),
    (r"\bTowershouston\b", "Towers / Houston"),
    (r"\bTowerspringhill\b", "Tower / SpringHill"),
    (r"\bTowertmc\b", "Tower / TMC"),
    (r"\bStationantone[’']?sdroubis\b", "Station / Antone's / Droubi's"),
    (r"\bExpressstaybridge\b", "Express / Staybridge"),
    (r"\bAndersonmonroe\b", "Anderson / Monroe"),
    (r"\bOfficewarehouse\b", "Office / Warehouse"),
    (r"\bOfficemedical\b", "Office / Medical"),
    (r"\bPickleballvolleyball\b", "Pickleball / Volleyball"),
    (r"\bMallplazamericas\b", "Mall / PlazAmericas"),
    (r"\bChachossmoothie\b", "Chacho's / Smoothie"),
    (r"\bOakwillowbend\b", "Oak / Willowbend"),
    (r"\bCompanybaylor\b", "Company / Baylor"),
    (r"\bCompanycourtyard\b", "Company / Courtyard"),
    (r"\bHoustonunited\b", "Houston / United"),
    (r"\bHoustoncentral\b", "Houston / Central"),
    (r"\bGymnasiumjeppesen\b", "Gymnasium / Jeppesen"),
    (r"\bFieldhouserobertson\b", "Fieldhouse / Robertson"),
    (r"\bAnnexresidence\b", "Annex / Residence"),
    (r"\bHolidaydaysheaven\b", "Holiday / Days / Heaven"),
    (r"\bParkwayrobinson\b", "Parkway / Robinson"),
    (r"\bCornervalentine\b", "Corner / Valentine"),
    (r"\bAssociationjackson\b", "Association / Jackson"),
    (r"\bAssociationmasterson\b", "Association / Masterson"),
    (r"\bUniversityhouston\b", "University / Houston"),
    (r"\bCenterbriarwood\b", "Center / Briarwood"),
    (r"\bCenterlegacy\b", "Center / Legacy"),
    (r"\bCenternrg\b", "Center / NRG"),
    (r"\bSanitariumthe\b", "Sanitarium / The"),
    (r"\bSanitariumdennis\b", "Sanitarium / Dennis"),
    (r"\bSanitariummemorial\b", "Sanitarium / Memorial"),
    (r"\bConstructionreach\b", "Construction / Reach"),
    (r"\bHomedepelchin\b", "Home / DePelchin"),
    (r"\bHometable\b", "Home / Table"),
    (r"\bHotelmotel\b", "Hotel / Motel"),
    (r"\bHotelstellar\b", "Hotel / Stellar"),
    (r"\bHousefox\b", "House / Fox"),
    (r"\bInnhomewood\b", "Inn / Homewood"),
    (r"\bStoregas\b", "Store / Gas"),
    (r"\bBarrestaurant\b", "Bar / Restaurant"),
]

WORD_FIXES: list[tuple[str, object]] = [
    (r"\bMcintyre([’']?s)?\b", r"McIntyre\1"),
    (r"\bMcgovern\b", "McGovern"),
    (r"\bMcewan\b", "McEwan"),
    (r"\bMcdonald\b", "McDonald"),
    (r"\bMckinney\b", "McKinney"),
    (r"\bMcgees\b", "McGee's"),
    (r"\bMcadams\b", "McAdams"),
    (r"\bMd\s+Anderson\b", "MD Anderson"),
    (r"\bJpmorgan\b", "JPMorgan"),
    (r"\bUthealth\b", "UTHealth"),
    (r"\bAc\s+Hotel\b", "AC Hotel"),
    (r"\bNasa\b", "NASA"),
    (r"\bPlainscapital\b", "PlainsCapital"),
    (r"\bWillliam\b", "William"),
    (r"\bAmory\b", "Armory"),
    (r"\bAssocation\b", "Association"),
    (r"\bCemetary\b", "Cemetery"),
    (r"\bCafereria\b", "Cafeteria"),
    (r"\bMadings\b", "Mading's"),
    (r"\bAngelos\b", "Angelo's"),
    (r"\bByrds\b", "Byrd's"),
    (r"\bRobins\s+Nest\b", "Robin's Nest"),
    (r"\bRosemarys\s+Place\b", "Rosemary's Place"),
    (r"\bYoung\s+Womens\b", "Young Women's"),
    (r"\bTexas\s+Childrens\b", "Texas Children's"),
    (r"\bTexas\s+Womans\b", "Texas Woman's"),
    (r"\bLumbermans\b", "Lumberman's"),
    (r"\bLa\s+Colombe\s+Dor\b", "La Colombe d'Or"),
    (r"\bVilla\s+D[’']\s*Este\b", "Villa d'Este"),
    (r"\bOquinn\b", "O'Quinn"),
    (r"\bBarnabys\b", "Barnaby's"),
    (r"\bCurleys\b", "Curley's"),
    (r"\bWaylands\b", "Wayland's"),
    (r"\bWyatts\b", "Wyatt's"),
    (r"\bSt\s+Luke([’']?s)?\b", lambda m: "St. Luke's" if m.group(1) else "St. Luke"),
    (r"\bSt\s+Josephs\b", "St. Joseph's"),
    (r"\bSt\s+Martins\b", "St. Martin's"),
    (r"\bSt\s+Marys\b", "St. Mary's"),
    (r"\bSt\s+Pius\b", "St. Pius"),
    (r"\bSt\s+Rose\b", "St. Rose"),
    (r"\bSt\s+Thomas\b", "St. Thomas"),
    (r"\bSt\s+Anne\b", "St. Anne"),
    (r"\bSt\s+Elizabeth\b", "St. Elizabeth"),
    (r"\bSt\s+Germain\b", "St. Germain"),
    (r"\bSt\s+Jude\b", "St. Jude"),
    (r"\bDepelchin\b", "DePelchin"),
    (r"\bDegeorge\b", "DeGeorge"),
    (r"\bCenterpoint\b", "CenterPoint"),
    (r"\bNew\s+New\s+Wing\b", "New Wing"),
]


def clean_name_or_title(raw: str | None) -> str:
    """
    Decodes URL percent-escapes (%e2%80%99 -> ’, %e2%80%8b -> stripped), HTML entities,
    invisible zero-width characters, fused HAIF slash-slug tokens, and restores proper casing.
    """
    if not raw:
        return ""
    s = str(raw)
    for _ in range(2):
        if "%" in s:
            try:
                s = urllib.parse.unquote(s)
            except Exception:
                break
    if "&" in s:
        s = html.unescape(s)
    s = re.sub(r"[\u200b-\u200f\ufeff]", "", s)
    s = re.sub(r"[\t\r\n\u00a0]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    s = re.sub(r"\b([A-Z][a-z]+)\s+mansion\b", r"\1 Mansion", s)
    for pat, repl in FUSED_SLASH_REPLACEMENTS:
        s = re.sub(pat, repl, s, flags=re.I)
    for pat, repl in WORD_FIXES:
        s = re.sub(pat, repl, s)
    # Strip trailing HAIF discussion topic suffixes ("Proposed 15 Story High Rise", "Conversion To Retail Garage")
    s = re.sub(
        r"\s+(?:Proposed\s+\d+\s+Story\s+High\s+Rise|Proposed\s+Office\s+Tower|Conversion\s+To\s+[A-Za-z\s]+)$",
        "",
        s,
        flags=re.I,
    ).strip()
    return s


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

# Generic structural/auxiliary labels or forum discussion topics that are NOT proper historical building names
GENERIC_AUXILIARY_NAME_RE = re.compile(
    r"(?:"
    r"carriage house|auxiliary structure|detached garage|garage apartment|outbuilding|"
    r"storage tank|process unit|industrial structure|petrochemical|refinery tank|"
    r"bus shelter|transit canopy|parking garage|loading dock|utility building|"
    r"courtyard wing|west wing|east wing|north wing|south wing|"
    r"\bacres for sale\b|\bskyscraper history\b|\buniversity history\b|\bdemo house\b|"
    r"\bin dallas\b|\bin austin\b|\bin fort worth\b|\bin san antonio\b"
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
    "0010020000016": {
        "primary": "Desel-Boettcher Warehouse",
        "alts": ["McIntyre's Restaurant", "Spaghetti Warehouse"],
        "source": "COH Landmark Designation Report & HAIF Archive",
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
    c = clean_name_or_title(cand).strip(" -:,;.\"'\t\r\n")
    if len(c) < 3 or len(c) > 95:
        return False
    if c.isdigit() or c.startswith("HCAD "):
        return False
    if c.lower() in {"north", "south", "east", "west", "spring", "central", "downtown", "pavilion", "mathematics", "mixed use development"}:
        return False
    if NEIGHBORHOOD_OR_META_PAREN_RE.match(c):
        return False
    if GENERIC_AUXILIARY_NAME_RE.search(c):
        return False
    if STREET_ADDRESS_ONLY_RE.match(c):
        return False
    if addr and norm_addr(c) == norm_addr(addr):
        return False
    # If candidate is "House at <other address>" or "Building at <address>", only allow if it matches this building's address
    m_at = re.match(r"^(?:the\s+)?(?:house|building|duplex|cottage|home)\s+at\s+(\d+\s+.*)$", c, re.I)
    if m_at:
        if not addr or norm_addr(m_at.group(1)) != norm_addr(addr):
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
    s = clean_name_or_title(raw_label)
    s = re.sub(r"\s*-\s*DEMOLISHED\b", "", s, flags=re.I).strip()
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
        clean_inner = clean_name_or_title(clean_inner)
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
        parts = [clean_name_or_title(p).strip() for p in s_clean.split(" / ") if p.strip()]
        valid_parts = [p for p in parts if is_valid_building_name(p, addr)]
        if len(valid_parts) >= 2:
            s_clean = valid_parts[0]
            for vp in valid_parts[1:]:
                if vp.lower() not in [a.lower() for a in alts]:
                    alts.append(vp)
        elif len(valid_parts) == 1:
            s_clean = valid_parts[0]

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
    t = clean_name_or_title(title)
    t = re.sub(r"^Houston\s+photo:\s*", "", t, flags=re.I).strip()
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
        p_clean = clean_name_or_title(p).strip(" -:,;()")
        p_clean = re.sub(r"\s+\d{2,5}\s+[A-Za-z]+\s+(?:St|Ave|Blvd|Dr|Rd)\.?$", "", p_clean, flags=re.I).strip()
        if not is_valid_building_name(p_clean, addr):
            continue
        if GENERIC_HAIF_PREFIX_RE.match(p_clean):
            continue
        if p_clean.lower() not in [x.lower() for x in out]:
            out.append(p_clean)
    return out


def _alnum_key(s: str) -> str:
    s_low = re.sub(r"^the\s+", "", (s or "").strip().lower())
    s_low = s_low.replace("&", " and ").replace("’", "").replace("'", "")
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", s_low).split())


def merge_unique_names(primary: str, candidates: list[str], addr: str = "") -> list[str]:
    """Returns deduplicated list of alternate names that are distinct from `primary` and `addr`."""
    p_clean = clean_name_or_title(primary)
    p_low = p_clean.strip().lower()
    p_norm = re.sub(r"^the\s+", "", p_low)
    p_alnum = _alnum_key(p_clean)
    addr_norm = norm_addr(addr)
    result: list[str] = []
    seen_norms: set[str] = {p_low, p_norm, p_alnum}
    for cand in candidates:
        if not cand:
            continue
        c = clean_name_or_title(cand).strip(" -:,;.")
        if not is_valid_building_name(c, addr):
            continue
        c_low = c.lower()
        c_norm = re.sub(r"^the\s+", "", c_low)
        c_alnum = _alnum_key(c)
        if c_low in seen_norms or c_norm in seen_norms or (c_alnum and c_alnum in seen_norms):
            continue
        if addr_norm and norm_addr(c) == addr_norm:
            continue
        seen_norms.add(c_low)
        seen_norms.add(c_norm)
        if c_alnum:
            seen_norms.add(c_alnum)
        result.append(c)
    return result[:4]


def _clean_haif_thread_list(threads_list: list | None) -> None:
    if not isinstance(threads_list, list):
        return
    for th in threads_list:
        if isinstance(th, dict) and th.get("title"):
            th["title"] = clean_name_or_title(th["title"])


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

    # First: sanitize all HAIF thread titles in haif_index.json and pipeline/cache/haif_thread_details.json
    for tid, rec in (haif_doc.get("threads") or {}).items():
        if isinstance(rec, dict) and rec.get("t"):
            rec["t"] = clean_name_or_title(rec["t"])

    details_cache_path = CACHE_DIR / "haif_thread_details.json"
    if details_cache_path.exists():
        details_doc = json.loads(details_cache_path.read_text())
        details_changed = False
        for tid, rec in details_doc.items():
            if isinstance(rec, dict) and rec.get("title"):
                new_t = clean_name_or_title(rec["title"])
                if new_t != rec["title"]:
                    rec["title"] = new_t
                    details_changed = True
        if details_changed:
            details_cache_path.write_text(json.dumps(details_doc, indent=2))

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
        primary_c = clean_name_or_title(primary) if primary else ""
        if not is_valid_building_name(primary_c, addr):
            primary_c = ""
        valid_alts = []
        for a in alts:
            ac = clean_name_or_title(a)
            if is_valid_building_name(ac, addr):
                valid_alts.append(ac)
        if key:
            rec = by_key_names[key]
            if primary_c:
                rec["primaries"].append((priority, primary_c, source))
            rec["alts"].extend(valid_alts)
        na = norm_addr(addr)
        if allow_addr_index and na and addr_building_counts.get(na, 0) <= 1 and "#" not in str(key):
            arec = by_addr_names[na]
            if primary_c:
                arec["primaries"].append((priority, primary_c, source))
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
        _clean_haif_thread_list(p.get("haif_threads"))
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
        else:
            p.pop("alt_names", None)
        p["name_source"] = "COH Landmark Designation Report"
        register_name(hcad, addr, primary, all_alts, 1, "COH Landmark Designation Report")

    # Priority 2: Preservation Houston Good Brick Awards (`overlays.json` -> `good_brick_awards`)
    gb_with_alts = 0
    for feat in overlays.get("good_brick_awards", {}).get("features", []):
        p = feat.get("properties") or {}
        _clean_haif_thread_list(p.get("haif_threads"))
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
        else:
            p.pop("alt_names", None)
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
        _clean_haif_thread_list(ov.get("haif_threads"))
        if "#aux_" in str(k):
            ov.pop("alt_names", None)
            if ov.get("building_name"):
                p_aux, _ = split_primary_and_alts(ov["building_name"], ov.get("address") or "")
                ov["building_name"] = p_aux
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

    def addr_core_key(addr: str) -> str:
        s = clean_name_or_title(addr or "").upper().strip()
        s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
        s = re.sub(r"[.,#]", " ", s)
        s = re.sub(
            r"\b(ST|AVE|BLVD|DR|RD|LN|CT|PL|WAY|PKWY|FWY|CIR|TRL)\s+\d+[A-Z]?\b.*$",
            r"\1",
            s,
        )
        s = re.sub(
            r"\b(STREET|AVENUE|BOULEVARD|DRIVE|ROAD|LANE|COURT|PLACE|PARKWAY|FREEWAY)\b",
            "",
            s,
        )
        s = re.sub(r"\b(ST|AVE|BLVD|DR|RD|LN|CT|PL|WAY|PKWY|FWY|CIR|TRL)\b", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def split_slash_addresses(raw_addr: str) -> list[str]:
        s = clean_name_or_title(raw_addr or "").strip()
        s = re.sub(r"\s*\([^)]*\)\s*", "", s).strip()
        if not s or s.startswith("0 "):
            return []
        parts = [p.strip() for p in re.split(r"\s+/\s+", s) if p.strip()]
        out = []
        for p in parts:
            if re.match(r"^\d+\s+[A-Za-z0-9]", p) and not p.startswith("0 "):
                out.append(p)
        return out

    # Collect all distinct street addresses per HCAD parcel (and building ID) across all sources
    hcad_raw_addrs: dict[str, list[str]] = defaultdict(list)
    key_raw_addrs: dict[str, list[str]] = defaultdict(list)

    for feat in buildings_doc.get("features", []):
        p = feat.get("properties") or {}
        fid = str(p.get("id") or p.get("building_id") or "").strip()
        h = str(p.get("hcad_num") or "").strip()
        for a in split_slash_addresses(p.get("address") or ""):
            if fid:
                key_raw_addrs[fid].append(a)
            if h and hcad_building_counts.get(h, 0) <= 1:
                hcad_raw_addrs[h].append(a)

    for k, ov in overrides.items():
        if not isinstance(ov, dict) or ov.get("suppress_only") or "#aux_" in str(k):
            continue
        h = str(ov.get("hcad_num") or k.split("#")[0] or "").strip()
        for a in split_slash_addresses(ov.get("address") or ""):
            key_raw_addrs[k].append(a)
            if "#" not in str(k) and h and hcad_building_counts.get(h, 0) <= 1:
                hcad_raw_addrs[h].append(a)

    for coll in ("landmarks", "good_brick_awards"):
        for feat in overlays.get(coll, {}).get("features", []):
            p = feat.get("properties") or {}
            h = str(p.get("hcad_num") or "").strip()
            bid = str(p.get("building_id") or "").strip()
            for a in split_slash_addresses(p.get("address") or ""):
                if bid:
                    key_raw_addrs[bid].append(a)
                if h and "#" not in bid and hcad_building_counts.get(h, 0) <= 1:
                    hcad_raw_addrs[h].append(a)

    for key, tids in by_key_haif.items():
        base_h = key.split("#")[0]
        if "#" not in key and hcad_building_counts.get(base_h, 0) > 1:
            continue
        for tid in tids:
            rec = threads.get(str(tid))
            if not rec:
                continue
            t_str = clean_name_or_title(rec.get("t", ""))
            m_at = re.search(
                r"\bAt\s+(\d+\s+[A-Za-z0-9 .'-]+?\b(?:St|Ave|Blvd|Dr|Rd|Ln|Ct|Pl|Way|Pkwy|Fwy)\.?)\b",
                t_str,
                re.I,
            )
            if m_at:
                for a in split_slash_addresses(m_at.group(1)):
                    key_raw_addrs[key].append(a)
                    if "#" not in key and base_h and hcad_building_counts.get(base_h, 0) <= 1:
                        hcad_raw_addrs[base_h].append(a)

    def resolve_addresses_for_entity(fid: str, hcad: str, current_addr: str) -> tuple[str, list[str]]:
        cands = []
        for a in split_slash_addresses(current_addr):
            cands.append(a)
        if fid and fid in key_raw_addrs:
            cands.extend(key_raw_addrs[fid])
        if "#" not in str(fid) and hcad and hcad_building_counts.get(hcad, 0) <= 1 and hcad in hcad_raw_addrs:
            cands.extend(hcad_raw_addrs[hcad])
        by_core: dict[str, str] = {}
        for a in cands:
            ck = addr_core_key(a)
            if ck and ck not in by_core:
                by_core[ck] = a
        distinct = list(by_core.values())
        if not distinct:
            clean_cur = clean_name_or_title(current_addr or "").strip()
            return clean_cur, []
        primary_addr = distinct[0]
        alt_addrs = distinct[1:]
        return primary_addr, alt_addrs

    # Apply resolved `building_name`, `alt_names`, `name_source`, and `alt_addresses` to `curated_overrides.json`
    ov_named_count = 0
    ov_multi_name_count = 0
    for k, ov in overrides.items():
        if not isinstance(ov, dict) or ov.get("suppress_only"):
            continue
        if "#aux_" in str(k):
            ov.pop("alt_names", None)
            ov.pop("alt_addresses", None)
            continue
        hcad = str(ov.get("hcad_num") or k.split("#")[0] or "").strip()
        addr = ov.get("address") or ""
        primary_addr, alt_addrs = resolve_addresses_for_entity(k, hcad, addr)
        if primary_addr and not addr:
            ov["address"] = primary_addr
        if alt_addrs:
            ov["alt_addresses"] = alt_addrs
        else:
            ov.pop("alt_addresses", None)
        best_name, merged_alts, best_src = resolve_best_name_and_alts(k, hcad, primary_addr or addr)
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

    # Apply resolved `building_name`, `alt_names`, `name_source`, and `alt_addresses` to `buildings.geojson`
    bld_named_count = 0
    bld_multi_name_count = 0
    for feat in buildings_doc.get("features", []):
        p = feat.get("properties") or {}
        _clean_haif_thread_list(p.get("haif_threads"))
        fid = str(p.get("id") or p.get("building_id") or "").strip()
        if "#aux_" in fid:
            p.pop("alt_names", None)
            p.pop("alt_addresses", None)
            continue
        hcad = str(p.get("hcad_num") or "").strip()
        addr = p.get("address") or ""
        primary_addr, alt_addrs = resolve_addresses_for_entity(fid, hcad, addr)
        if primary_addr and not addr:
            p["address"] = primary_addr
        if alt_addrs:
            p["alt_addresses"] = alt_addrs
        else:
            p.pop("alt_addresses", None)
        existing_lm = p.get("landmark_name") or ""
        existing_bn = p.get("building_name") or ""
        if existing_lm or existing_bn:
            p1, a1 = split_primary_and_alts(existing_lm or existing_bn, primary_addr or addr)
            if p1 and is_valid_building_name(p1, primary_addr or addr):
                register_name(fid or hcad, primary_addr or addr, p1, a1, 2, "Preservation Houston Curated Archive", allow_addr_index=False)

        best_name, merged_alts, best_src = resolve_best_name_and_alts(fid, hcad, primary_addr or addr)
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

    # Also attach `alt_addresses` to `overlays.json` landmarks and good_brick_awards
    for coll in ("landmarks", "good_brick_awards"):
        for feat in overlays.get(coll, {}).get("features", []):
            p = feat.get("properties") or {}
            hcad = str(p.get("hcad_num") or "").strip()
            bid = str(p.get("building_id") or "").strip()
            addr = p.get("address") or ""
            _, alt_addrs = resolve_addresses_for_entity(bid, hcad, addr)
            if alt_addrs:
                p["alt_addresses"] = alt_addrs
            else:
                p.pop("alt_addresses", None)

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

    # Helper to compute centroid [lon, lat] from GeoJSON geometry
    def _geom_centroid(geom: dict | None) -> tuple[float, float] | None:
        if not geom or not geom.get("coordinates"):
            return None
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        if gtype == "Point" and isinstance(coords, list) and len(coords) >= 2:
            return round(float(coords[0]), 6), round(float(coords[1]), 6)
        ring = None
        if gtype == "Polygon" and coords:
            ring = coords[0]
        elif gtype == "MultiPolygon" and coords and coords[0]:
            ring = coords[0][0]
        if not ring:
            return None
        pts = [pt for pt in ring if isinstance(pt, list) and len(pt) >= 2]
        if not pts:
            return None
        lon = sum(float(pt[0]) for pt in pts) / len(pts)
        lat = sum(float(pt[1]) for pt in pts) / len(pts)
        return round(lon, 6), round(lat, 6)

    # Ensure every building in `buildings.geojson` and `curated_overrides.json` is present in `search_index.json`
    existing_si_ids: set[str] = set()
    existing_si_hcads: set[str] = set()
    for item in search_doc:
        if item.get("type") == "district":
            continue
        fid = str(item.get("id") or "").strip()
        hcad = str(item.get("hcad_num") or "").strip()
        if fid:
            existing_si_ids.add(fid)
        if hcad and "#" not in fid:
            existing_si_hcads.add(hcad)

    added_from_blds = 0
    for feat in buildings_doc.get("features", []):
        p = feat.get("properties") or {}
        fid = str(p.get("id") or p.get("building_id") or "").strip()
        if "#aux_" in fid or p.get("suppress_only"):
            continue
        hcad = str(p.get("hcad_num") or "").strip()
        if (fid and fid in existing_si_ids) or (hcad and "#" not in fid and hcad in existing_si_hcads):
            continue
        pt = _geom_centroid(feat.get("geometry"))
        if not pt:
            continue
        addr = str(p.get("address") or "").strip()
        bname = str(p.get("building_name") or p.get("landmark_name") or "").strip()
        if not addr and not bname:
            continue
        yr = int(p.get("year_built") or 0)
        dist = str(p.get("historic_district") or "").strip()
        if dist in ("Outside City District", "Outside Historic District", "None", "null"):
            dist = ""
        uc = str(p.get("use_category") or "Residential").strip()
        lu = str(p.get("landuse_desc") or "").strip()
        st = str(p.get("style") or "").strip()
        bs = str(p.get("bld_style") or "").strip()
        arch = str(p.get("architect") or "").strip()
        disp_st = st or (bs if bs not in ("Residential", "Historical / Architectural Structure") else "")
        sub_parts = [x for x in [addr if bname else "", dist or uc, disp_st, f"Built {yr}" if yr >= 1836 else ""] if x]
        new_item: dict = {
            "type": "building",
            "id": fid or f"hcad_{hcad}",
            "hcad_num": hcad,
            "label": bname or addr,
            "address": addr,
            "sublabel": " • ".join(sub_parts),
            "category": f"Built {yr}" if yr >= 1836 else (uc or "Property"),
            "year_built": yr,
            "use_category": uc,
            "lon": pt[0],
            "lat": pt[1],
            "zoom": 17.5,
        }
        if lu:
            new_item["landuse_desc"] = lu
        if st:
            new_item["style"] = st
        if bs:
            new_item["bld_style"] = bs
        if arch:
            new_item["architect"] = arch
        if dist:
            new_item["historic_district"] = dist
        search_doc.append(new_item)
        if fid:
            existing_si_ids.add(fid)
        if hcad and "#" not in fid:
            existing_si_hcads.add(hcad)
        added_from_blds += 1

    added_from_ovs = 0
    for k, ov in overrides.items():
        if not isinstance(ov, dict) or ov.get("suppress_only") or "#aux_" in str(k):
            continue
        hcad = str(ov.get("hcad_num") or k.split("#")[0] or "").strip()
        if k in existing_si_ids or ("#" not in str(k) and hcad and hcad in existing_si_hcads):
            continue
        pt = _geom_centroid(ov.get("geometry"))
        if not pt:
            continue
        addr = str(ov.get("address") or "").strip()
        bname = str(ov.get("building_name") or ov.get("landmark_name") or "").strip()
        if not addr and not bname:
            continue
        yr = int(ov.get("year_built") or 0)
        dist = str(ov.get("historic_district") or "").strip()
        if dist in ("Outside City District", "Outside Historic District", "None", "null"):
            dist = ""
        uc = str(ov.get("use_category") or "Residential").strip()
        lu = str(ov.get("landuse_desc") or "").strip()
        st = str(ov.get("style") or "").strip()
        bs = str(ov.get("bld_style") or "").strip()
        arch = str(ov.get("architect") or "").strip()
        disp_st = st or (bs if bs not in ("Residential", "Historical / Architectural Structure") else "")
        sub_parts = [x for x in [addr if bname else "", dist or uc, disp_st, f"Built {yr}" if yr >= 1836 else ""] if x]
        new_item = {
            "type": "building",
            "id": k,
            "hcad_num": hcad,
            "label": bname or addr,
            "address": addr,
            "sublabel": " • ".join(sub_parts),
            "category": f"Built {yr}" if yr >= 1836 else (uc or "Property"),
            "year_built": yr,
            "use_category": uc,
            "lon": pt[0],
            "lat": pt[1],
            "zoom": 17.5,
        }
        if lu:
            new_item["landuse_desc"] = lu
        if st:
            new_item["style"] = st
        if bs:
            new_item["bld_style"] = bs
        if arch:
            new_item["architect"] = arch
        if dist:
            new_item["historic_district"] = dist
        search_doc.append(new_item)
        existing_si_ids.add(k)
        if "#" not in str(k) and hcad:
            existing_si_hcads.add(hcad)
        added_from_ovs += 1

    # Enrich `search_index.json` with `building_name`, `alt_names`, `address`, `alt_addresses`, and formatted sublabels
    search_named_count = 0
    search_alt_count = 0
    search_multi_addr_count = 0
    for item in search_doc:
        if item.get("type") == "district":
            continue
        fid = str(item.get("id") or "").strip()
        if "#aux_" in fid:
            item.pop("alt_names", None)
            item.pop("alt_addresses", None)
            continue
        hcad = str(item.get("hcad_num") or "").strip()
        lbl = clean_name_or_title(item.get("label") or "").strip()
        sub = clean_name_or_title(item.get("sublabel") or "").strip()
        if item.get("name"):
            item["name"] = clean_name_or_title(item["name"])
        if item.get("keywords"):
            item["keywords"] = clean_name_or_title(item["keywords"])
        # Strip any prior "AKA: ... • " prefix so we rebuild cleanly
        sub = re.sub(r"^AKA:\s*[^•]+•\s*", "", sub).strip()

        lbl_no_addr = re.sub(r"\s*(?:—\s*\d+\s+.*|\(\d+\s+[^)]+\))$", "", lbl).strip()
        lbl_primary, lbl_alts = split_primary_and_alts(lbl_no_addr, "")
        addr_cand = clean_name_or_title(item.get("address") or "").strip()
        if not addr_cand and re.match(r"^\d+\s+", lbl):
            addr_cand = lbl

        primary_addr, alt_addrs = resolve_addresses_for_entity(fid, hcad, addr_cand)
        if primary_addr:
            item["address"] = primary_addr
        elif addr_cand:
            item["address"] = addr_cand
        if alt_addrs:
            item["alt_addresses"] = alt_addrs
            search_multi_addr_count += 1
        else:
            item.pop("alt_addresses", None)

        effective_addr = primary_addr or addr_cand
        best_name, merged_alts, best_src = resolve_best_name_and_alts(fid, hcad, effective_addr)
        if not best_name and lbl_primary and is_valid_building_name(lbl_primary, effective_addr):
            best_name = lbl_primary
        merged_alts = merge_unique_names(best_name or lbl_primary, merged_alts + lbl_alts, effective_addr)

        all_disp_addrs = [a for a in ([primary_addr] if primary_addr else []) + alt_addrs if a]
        combined_addr_str = " / ".join(all_disp_addrs[:2]) if all_disp_addrs else effective_addr

        if best_name:
            item["building_name"] = best_name
            item["label"] = best_name
            if combined_addr_str:
                # Replace single address in sublabel with combined address if needed, or prepend it
                sub_parts = [p.strip() for p in sub.split("•") if p.strip()]
                addr_cores = {addr_core_key(a) for a in all_disp_addrs if addr_core_key(a)}
                filtered_sub_parts = [
                    sp for sp in sub_parts if addr_core_key(sp) not in addr_cores and sp.lower() != combined_addr_str.lower()
                ]
                sub = " • ".join([combined_addr_str] + filtered_sub_parts)
            search_named_count += 1
        elif alt_addrs and combined_addr_str:
            sub_parts = [p.strip() for p in sub.split("•") if p.strip()]
            addr_cores = {addr_core_key(a) for a in all_disp_addrs if addr_core_key(a)}
            filtered_sub_parts = [
                sp for sp in sub_parts if addr_core_key(sp) not in addr_cores and sp.lower() != combined_addr_str.lower()
            ]
            sub = " • ".join([combined_addr_str] + filtered_sub_parts)

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
    print(
        f"search_index.json: {len(search_doc)} total entries (+{added_from_blds} from buildings.geojson, +{added_from_ovs} from overrides); "
        f"{search_named_count} named, {search_alt_count} with alt_names, {search_multi_addr_count} with multi-street alt_addresses"
    )


if __name__ == "__main__":
    main()
