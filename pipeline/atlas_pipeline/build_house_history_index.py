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


def build_index() -> dict:
    if not CACHE_DB.exists():
        raise FileNotFoundError(f"Missing city directory SQLite cache: {CACHE_DB}")

    conn = sqlite3.connect(str(CACHE_DB))
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
            by_hcad[hcad_clean] = entry
        elif classification in ("ABSENT_THROUGH_1926_PREMATURE_HCAD_1920", "NO_PRE_1926_EVIDENCE"):
            absent_pre1926.append(hcad_clean)

    conn.close()

    payload = {
        "generated_from": "HPL ContentDM Houston City Directories (1866-1926)",
        "collection_url": "https://cdm17006.contentdm.oclc.org/digital/collection/citydir",
        "volume_count": len(volumes),
        "verified_hit_count": len(by_hcad),
        "audited_absent_count": len(absent_pre1926),
        "volumes": volumes,
        "historic_streets": HISTORIC_STREET_ALIASES,
        "by_hcad": by_hcad,
        "absent_through_1926": absent_pre1926,
    }
    return payload


def main() -> None:
    payload = build_index()
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    if "\u2014" in raw or "\\u2014" in raw:
        raise AssertionError("Em-dash detected in generated citydir_index.json")
    OUTPUT_JSON.write_text(raw, encoding="utf-8")
    size_kb = OUTPUT_JSON.stat().st_size / 1024.0
    print(
        f"Wrote {OUTPUT_JSON} ({size_kb:.1f} KB): "
        f"{payload['volume_count']} volumes, "
        f"{payload['verified_hit_count']} verified parcel hits, "
        f"{payload['audited_absent_count']} audited pre-1927 absences."
    )


if __name__ == "__main__":
    main()
