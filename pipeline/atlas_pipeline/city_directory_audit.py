"""Polite Houston City Directory (1866-1926) crawler and HCAD date quality auditor.

Queries the Houston Public Library Digital Archives ContentDM API
(https://cdm17006.contentdm.oclc.org/digital/collection/citydir/search)
with a local SQLite cache and strict rate-limiting so the library's servers
are never overwhelmed. Cross-references suspect rounded-decade HCAD dates
(e.g., 1900, 1910, 1920, 1930) against digitized Morrison & Fourmy Houston
City Directories and exports candidate corrections for the Preservation Houston
Google Sheet override database.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CONTENTDM_BASE = "https://cdm17006.contentdm.oclc.org"
COLLECTION_ALIAS = "citydir"
POLITE_USER_AGENT = (
    "PreservationHouston-Atlas-Research/1.0 "
    "(+https://preservationhouston.org; archival-date-verification)"
)

STREET_SUFFIXES = {
    "ST",
    "STREET",
    "AVE",
    "AVENUE",
    "BLVD",
    "BOULEVARD",
    "DR",
    "DRIVE",
    "RD",
    "ROAD",
    "LN",
    "LANE",
    "CT",
    "COURT",
    "PL",
    "PLACE",
    "PKWY",
    "PARKWAY",
    "CIR",
    "CIRCLE",
    "WAY",
}

DIRECTIONALS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW", "NORTH", "SOUTH", "EAST", "WEST"}


@dataclass
class DirectoryMention:
    """A verified mention of a street address on a digitized City Directory page."""

    pointer: int
    parent_pointer: int
    year: int
    volume_title: str
    page_title: str
    snippet: str
    viewer_url: str


@dataclass
class AuditFinding:
    """Result of auditing a single HCAD property against the 1866-1926 City Directories."""

    hcad_num: str
    address: str
    historic_district: str
    original_hcad_year: int
    suggested_year_built: int | None
    classification: str
    earliest_dir_year: int | None
    matched_dir_years: list[int]
    neighbor_dir_years: list[int]
    source_type: str
    source_citation: str
    source_url: str
    notes: str


def parse_street_address(raw_address: str) -> tuple[int, str, str] | None:
    """Extract (house_number, street_stem, directional_prefix) from an HCAD address.

    Examples:
        "1127 KEY ST" -> (1127, "Key", "")
        "2115 N MAIN ST" -> (2115, "Main", "N")
        "704 E 14TH ST" -> (704, "14th", "E")
    """
    cleaned = re.sub(r"\s+", " ", (raw_address or "").strip().upper())
    m = re.match(r"^(\d+)\s+(.+)$", cleaned)
    if not m:
        return None
    house_num = int(m.group(1))
    rest_tokens = [tok for tok in m.group(2).split(" ") if tok]
    if not rest_tokens:
        return None

    # Strip trailing unit/apt designators like APT A, STE 200, #1
    filtered: list[str] = []
    for tok in rest_tokens:
        if tok in {"APT", "STE", "SUITE", "UNIT", "BLDG", "FL"} or tok.startswith("#"):
            break
        filtered.append(tok)
    if not filtered:
        return None

    directional = ""
    if filtered[0] in DIRECTIONALS and len(filtered) > 1:
        directional = filtered[0]
        filtered = filtered[1:]

    if len(filtered) > 1 and filtered[-1] in STREET_SUFFIXES:
        filtered = filtered[:-1]

    street_stem = " ".join(filtered).title()
    if not street_stem:
        return None
    return house_num, street_stem, directional


def verify_address_in_ocr(ocr_text: str, house_num: int, street_stem: str) -> str | None:
    """Verify whether `house_num` and `street_stem` genuinely co-occur in page OCR text.

    In Houston City Directories, entries appear in two primary layouts:
    1. Alphabetical Resident/Business Directory:
       "Smith John (Mary) clk h 1127 Key" or "r1127 Key"
    2. Street and Avenue Guide (reverse directory by street):
       Header "KEY—From ..." followed by line items "1123 ...", "1125 ...", "1127 ..."

    Returns a short cleaned snippet if verified, or None if it appears to be a
    false positive (e.g., phone number or unrelated street).
    """
    if not ocr_text or house_num <= 0:
        return None

    normalized = re.sub(r"\s+", " ", ocr_text)
    num_str = str(house_num)
    stem_escaped = re.escape(street_stem)

    # Pattern 1: Direct adjacency like "1127 Key" or "1127 N Key" or "1127 E 14th" (allowing OCR punctuation)
    direct_pat = re.compile(
        rf"\b[rh]?\s*{num_str}[\s.,;:-]+(?:[NSEW]\.?[\s.,;:-]+)?{stem_escaped}\b",
        re.IGNORECASE,
    )
    m_direct = direct_pat.search(normalized)
    if m_direct:
        start = max(0, m_direct.start() - 45)
        end = min(len(normalized), m_direct.end() + 55)
        return normalized[start:end].strip()

    # For dummy/single-digit HCAD numbers (< 10), require direct adjacency only
    if house_num < 10:
        return None

    # Pattern 2: Reverse Street & Avenue Guide section ("KEY ST—From ..." followed by house numbers)
    header_pat = re.compile(
        rf"\b{stem_escaped}\s+(?:ST|AVE|AV|BLVD|DR|RD)?\s*(?:—|--|-)\s*From\b",
        re.IGNORECASE,
    )
    for m_hdr in header_pat.finditer(normalized):
        window = normalized[m_hdr.start() : min(len(normalized), m_hdr.end() + 1200)]
        num_pat = re.compile(rf"\b{num_str}\s+[A-Z][a-z]+", re.IGNORECASE)
        m_num = num_pat.search(window)
        if m_num:
            s_start = max(0, m_num.start() - 20)
            s_end = min(len(window), m_num.end() + 60)
            return f"[{street_stem} Street Guide] {window[s_start:s_end].strip()}"

    return None


class ContentDmCityDirectoryClient:
    """Polite, SQLite-cached client for the HPL ContentDM `citydir` collection."""

    def __init__(
        self,
        cache_db_path: Path,
        min_delay_seconds: float = 2.0,
        max_retries: int = 3,
    ) -> None:
        self.cache_db_path = Path(cache_db_path)
        self.cache_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.min_delay_seconds = max(0.5, float(min_delay_seconds))
        self.max_retries = max_retries
        self._last_request_ts = 0.0
        self.conn = sqlite3.connect(str(self.cache_db_path))
        self._init_db()
        self._volume_years: dict[int, tuple[int, str]] = {}

    def _init_db(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS volume_years (
                    parent_pointer INTEGER PRIMARY KEY,
                    year INTEGER NOT NULL,
                    title TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS search_cache (
                    query_key TEXT PRIMARY KEY,
                    response_json TEXT NOT NULL,
                    fetched_at REAL NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS item_cache (
                    pointer INTEGER PRIMARY KEY,
                    parent_pointer INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    transc TEXT NOT NULL,
                    fetched_at REAL NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_results (
                    hcad_num TEXT PRIMARY KEY,
                    finding_json TEXT NOT NULL,
                    audited_at REAL NOT NULL
                )
                """
            )

    def close(self) -> None:
        self.conn.close()

    def get_cached_finding(self, hcad_num: str) -> AuditFinding | None:
        row = self.conn.execute(
            "SELECT finding_json FROM audit_results WHERE hcad_num = ?",
            (hcad_num,),
        ).fetchone()
        if not row:
            return None
        try:
            d = json.loads(row[0])
            return AuditFinding(**d)
        except Exception:
            return None

    def save_cached_finding(self, finding: AuditFinding) -> None:
        if not finding.hcad_num:
            return
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO audit_results(hcad_num, finding_json, audited_at) VALUES (?, ?, ?)",
                (finding.hcad_num, json.dumps(asdict(finding)), time.time()),
            )

    def _polite_sleep(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_ts
        if elapsed < self.min_delay_seconds:
            time.sleep(self.min_delay_seconds - elapsed)
        self._last_request_ts = time.monotonic()

    def _get_json(self, dm_query_path: str) -> dict[str, Any]:
        """Perform a polite HTTP GET against ContentDM dmwebservices with retries."""
        url = f"{CONTENTDM_BASE}/digital/bl/dmwebservices/index.php?q={dm_query_path}"
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            self._polite_sleep()
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": POLITE_USER_AGENT,
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    return json.loads(raw)
            except (urllib.error.URLError, urllib.error.HTTPError, ConnectionResetError, TimeoutError) as exc:
                last_err = exc
                backoff = self.min_delay_seconds * (2 ** (attempt + 1))
                time.sleep(backoff)
        raise RuntimeError(f"ContentDM request failed after {self.max_retries} attempts ({url}): {last_err}")

    def ensure_volume_catalog(self) -> dict[int, tuple[int, str]]:
        """Load the 39 top-level Houston City Directory volumes (1866-1926) into SQLite."""
        if self._volume_years:
            return self._volume_years

        rows = self.conn.execute("SELECT parent_pointer, year, title FROM volume_years").fetchall()
        if len(rows) >= 35:
            self._volume_years = {int(r[0]): (int(r[1]), str(r[2])) for r in rows}
            return self._volume_years

        data = self._get_json(f"dmQuery/{COLLECTION_ALIAS}/0/title!date/nosort/100/1/1/0/0/0/json")
        records = data.get("records", [])
        with self.conn:
            for rec in records:
                ptr = int(rec.get("pointer", -1))
                title = str(rec.get("title", ""))
                date_str = str(rec.get("date", ""))
                m = re.search(r"(18\d{2}|19\d{2})", title) or re.search(r"(18\d{2}|19\d{2})", date_str)
                year = int(m.group(1)) if m else 0
                if ptr > 0 and year > 0:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO volume_years(parent_pointer, year, title) VALUES (?, ?, ?)",
                        (ptr, year, title),
                    )
                    self._volume_years[ptr] = (year, title)
        return self._volume_years

    def search_phrase(self, phrase: str, max_records: int = 50) -> list[dict[str, Any]]:
        """Run a full-text ContentDM search for an exact phrase (`^exact^and`)."""
        clean_p = phrase.strip().lower()
        exact_key = f"exact:{clean_p}:{max_records}"
        legacy_key = f"phrase:{clean_p}:{max_records}"
        row = self.conn.execute(
            "SELECT response_json FROM search_cache WHERE query_key IN (?, ?) ORDER BY query_key ASC LIMIT 1",
            (exact_key, legacy_key),
        ).fetchone()
        if row:
            return json.loads(row[0]).get("records", [])

        encoded_phrase = urllib.parse.quote(phrase.strip())
        dm_path = (
            f"dmQuery/{COLLECTION_ALIAS}/CISOSEARCHALL^{encoded_phrase}^exact^and/"
            f"title!date/nosort/{max_records}/1/0/0/0/0/json"
        )
        data = self._get_json(dm_path)
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO search_cache(query_key, response_json, fetched_at) VALUES (?, ?, ?)",
                (exact_key, json.dumps(data), time.time()),
            )
        return data.get("records", [])

    def get_page_details(self, pointer: int, known_parent: int | None = None) -> dict[str, Any]:
        """Fetch page OCR (`transc`) and resolve its parent volume year (cached in SQLite)."""
        row = self.conn.execute(
            "SELECT parent_pointer, year, title, transc FROM item_cache WHERE pointer = ?",
            (pointer,),
        ).fetchone()
        if row:
            parent_ptr, year, title, transc = int(row[0]), int(row[1]), str(row[2]), str(row[3])
            vol_catalog = self.ensure_volume_catalog()
            vol_title = vol_catalog.get(parent_ptr, (year, f"{year} Houston City Directory"))[1]
            return {
                "pointer": pointer,
                "parent_pointer": parent_ptr,
                "year": year,
                "volume_title": vol_title,
                "title": title,
                "transc": transc,
            }

        vol_catalog = self.ensure_volume_catalog()
        parent_ptr = known_parent if (known_parent and known_parent > 0) else -1
        if parent_ptr <= 0:
            parent_info = self._get_json(f"GetParent/{COLLECTION_ALIAS}/{pointer}/json")
            parent_ptr = int(parent_info.get("parent", -1))

        item_info = self._get_json(f"dmGetItemInfo/{COLLECTION_ALIAS}/{pointer}/json")
        title = str(item_info.get("title", f"Page {pointer}"))
        transc = str(item_info.get("transc", "")) if not isinstance(item_info.get("transc"), dict) else ""

        year, vol_title = vol_catalog.get(parent_ptr, (0, "Houston City Directory"))
        if year == 0:
            m = re.search(r"(18\d{2}|19\d{2})", title)
            if m:
                year = int(m.group(1))

        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO item_cache(pointer, parent_pointer, year, title, transc, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (pointer, parent_ptr, year, title, transc, time.time()),
            )
        return {
            "pointer": pointer,
            "parent_pointer": parent_ptr,
            "year": year,
            "volume_title": vol_title,
            "title": title,
            "transc": transc,
        }

    def find_address_mentions(
        self,
        house_num: int,
        street_stem: str,
        max_ocr_checks: int = 3,
    ) -> list[DirectoryMention]:
        """Search all 1866-1926 volumes for a specific street number and street stem.

        Sorts candidate search records by volume year ascending (earliest directory first)
        so we only need 1-3 `dmGetItemInfo` OCR page fetches to verify the earliest appearance,
        avoiding redundant requests across 20+ later volumes.
        """
        phrase = f"{house_num} {street_stem}"
        records = self.search_phrase(phrase, max_records=40)
        if not records:
            return []

        vol_catalog = self.ensure_volume_catalog()

        def _rec_sort_key(rec: dict[str, Any]) -> tuple[int, int]:
            p_obj = int(rec.get("parentobject", -1))
            yr = vol_catalog.get(p_obj, (9999, ""))[0]
            return (yr if yr > 0 else 9999, int(rec.get("pointer", 0)))

        sorted_records = sorted(records, key=_rec_sort_key)
        mentions: list[DirectoryMention] = []
        ocr_checks = 0

        for rec in sorted_records:
            ptr = int(rec.get("pointer", -1))
            if ptr <= 0:
                continue
            known_parent = int(rec.get("parentobject", -1))
            # Check if already cached in SQLite before counting toward network OCR check budget
            is_cached = (
                self.conn.execute("SELECT 1 FROM item_cache WHERE pointer = ?", (ptr,)).fetchone()
                is not None
            )
            if not is_cached and ocr_checks >= max_ocr_checks and mentions:
                # We already verified the earliest mention; record subsequent volume years from catalog
                yr, vol_title = vol_catalog.get(known_parent, (0, ""))
                if yr > 0:
                    mentions.append(
                        DirectoryMention(
                            pointer=ptr,
                            parent_pointer=known_parent,
                            year=yr,
                            volume_title=vol_title,
                            page_title=str(rec.get("title", f"Page {ptr}")),
                            snippet=mentions[0].snippet,
                            viewer_url=f"{CONTENTDM_BASE}/digital/collection/{COLLECTION_ALIAS}/id/{ptr}/rec/1",
                        )
                    )
                continue

            if not is_cached and ocr_checks >= max_ocr_checks and not mentions:
                break

            if not is_cached:
                ocr_checks += 1
            page = self.get_page_details(ptr, known_parent=known_parent)
            snippet = verify_address_in_ocr(page["transc"], house_num, street_stem)
            if snippet and page["year"] > 0:
                viewer_url = f"{CONTENTDM_BASE}/digital/collection/{COLLECTION_ALIAS}/id/{ptr}/rec/1"
                mentions.append(
                    DirectoryMention(
                        pointer=ptr,
                        parent_pointer=page["parent_pointer"],
                        year=page["year"],
                        volume_title=page["volume_title"],
                        page_title=page["title"],
                        snippet=snippet,
                        viewer_url=viewer_url,
                    )
                )

        mentions.sort(key=lambda m: (m.year, m.pointer))
        return mentions


def audit_suspect_property(
    client: ContentDmCityDirectoryClient,
    property_record: dict[str, Any],
    check_neighbors: bool = True,
) -> AuditFinding:
    """Audit a single HCAD property record against the 1866-1926 Houston City Directories."""
    hcad_num = str(property_record.get("hcad_num", "")).strip()
    address = str(property_record.get("address", "")).strip()
    district = str(property_record.get("historic_district", "")).strip()
    hcad_year = int(
        property_record.get("original_hcad_year")
        or property_record.get("year_built")
        or 0
    )

    parsed = parse_street_address(address)
    if not parsed:
        return AuditFinding(
            hcad_num=hcad_num,
            address=address,
            historic_district=district,
            original_hcad_year=hcad_year,
            suggested_year_built=None,
            classification="UNPARSEABLE_ADDRESS",
            earliest_dir_year=None,
            matched_dir_years=[],
            neighbor_dir_years=[],
            source_type="Houston City Directory",
            source_citation="",
            source_url="",
            notes="Address has no street number.",
        )

    house_num, street_stem, _ = parsed
    mentions = client.find_address_mentions(house_num, street_stem)
    matched_years = sorted({m.year for m in mentions if m.year > 0})
    earliest_year = matched_years[0] if matched_years else None

    # Case 1A: Property has NO year_built in HCAD (hcad_year == 0) and is found in City Directories
    if earliest_year is not None and hcad_year == 0:
        first_mention = mentions[0]
        years_preview = ", ".join(str(y) for y in matched_years[:6])
        citation = (
            f'{first_mention.volume_title} ({first_mention.page_title}): '
            f'"{first_mention.snippet}" — verified in {years_preview} directories '
            f'(unrecorded / Year Built = 0 in HCAD)'
        )
        return AuditFinding(
            hcad_num=hcad_num,
            address=address,
            historic_district=district,
            original_hcad_year=0,
            suggested_year_built=earliest_year,
            classification="UNDATED_IN_HCAD_FOUND_IN_DIRECTORY",
            earliest_dir_year=earliest_year,
            matched_dir_years=matched_years,
            neighbor_dir_years=[],
            source_type="Houston City Directory",
            source_citation=citation,
            source_url=first_mention.viewer_url,
            notes=(
                f"Unrecorded in HCAD (Year Built = 0), but appears in {earliest_year} City Directory. "
                f"Verified across directory years: {matched_years}."
            ),
        )

    # Case 1B: Address appears in City Directories earlier than HCAD's year_built
    if earliest_year is not None and earliest_year < hcad_year - 1:
        first_mention = mentions[0]
        years_preview = ", ".join(str(y) for y in matched_years[:6])
        citation = (
            f'{first_mention.volume_title} ({first_mention.page_title}): '
            f'"{first_mention.snippet}" — verified in {years_preview} directories '
            f'({hcad_year - earliest_year} yrs earlier than HCAD {hcad_year})'
        )
        return AuditFinding(
            hcad_num=hcad_num,
            address=address,
            historic_district=district,
            original_hcad_year=hcad_year,
            suggested_year_built=earliest_year,
            classification="EARLIER_THAN_HCAD",
            earliest_dir_year=earliest_year,
            matched_dir_years=matched_years,
            neighbor_dir_years=[],
            source_type="Houston City Directory",
            source_citation=citation,
            source_url=first_mention.viewer_url,
            notes=(
                f"Appears in {earliest_year} City Directory ({hcad_year - earliest_year} yrs "
                f"earlier than HCAD {hcad_year}). Verified across directory years: {matched_years}."
            ),
        )

    # Case 2: Address appears right around or after HCAD's year_built
    if earliest_year is not None:
        first_mention = mentions[0]
        citation = (
            f'{first_mention.volume_title} ({first_mention.page_title}): '
            f'"{first_mention.snippet}"'
        )
        return AuditFinding(
            hcad_num=hcad_num,
            address=address,
            historic_district=district,
            original_hcad_year=hcad_year,
            suggested_year_built=earliest_year if abs(earliest_year - hcad_year) > 1 else hcad_year,
            classification="CONFIRMED_IN_DIRECTORY",
            earliest_dir_year=earliest_year,
            matched_dir_years=matched_years,
            neighbor_dir_years=[],
            source_type="Houston City Directory",
            source_citation=citation,
            source_url=first_mention.viewer_url,
            notes=f"First appears in {earliest_year} directory (HCAD lists {hcad_year}).",
        )

    # Case 3: Address is completely absent through 1926, yet HCAD claims <= 1924 (e.g. 1920 placeholder)
    neighbor_years: set[int] = set()
    neighbor_example: DirectoryMention | None = None
    if check_neighbors and 1890 <= hcad_year <= 1924:
        for delta in (-4, -2, 2, 4):
            adj_num = house_num + delta
            if adj_num <= 0:
                continue
            adj_mentions = client.find_address_mentions(adj_num, street_stem)
            for m in adj_mentions:
                if m.year > 0:
                    neighbor_years.add(m.year)
                    if neighbor_example is None:
                        neighbor_example = m
            if len(neighbor_years) >= 2:
                break

    sorted_neighbor_years = sorted(neighbor_years)
    if 1915 <= hcad_year <= 1924 and (sorted_neighbor_years or district):
        if sorted_neighbor_years:
            citation = (
                f"Absent through 1926 Houston City Directory while adjacent lots on {street_stem} "
                f"appear in {sorted_neighbor_years} (HCAD defaults to rounded {hcad_year})"
            )
            notes = (
                f"Block active in {sorted_neighbor_years} directories, but {address} is unimproved "
                f"through 1926. HCAD {hcad_year} is a rounded decade estimate; likely built c. 1927-1929."
            )
        else:
            citation = (
                f"Unimproved / absent through 1926 Houston City Directory ({district}); "
                f"HCAD defaults to rounded {hcad_year} placeholder for post-1926 construction"
            )
            notes = (
                f"Absent from all 1866-1926 digitized Houston City Directories despite HCAD listing "
                f"{hcad_year}. Indicates post-1926 construction (c. 1927-1929; verify in 1927/1928 directory)."
            )
        return AuditFinding(
            hcad_num=hcad_num,
            address=address,
            historic_district=district,
            original_hcad_year=hcad_year,
            suggested_year_built=1928,
            classification="ABSENT_THROUGH_1926_PREMATURE_HCAD_1920",
            earliest_dir_year=None,
            matched_dir_years=[],
            neighbor_dir_years=sorted_neighbor_years,
            source_type="Houston City Directory",
            source_citation=citation,
            source_url=(
                neighbor_example.viewer_url
                if neighbor_example
                else f"{CONTENTDM_BASE}/digital/collection/{COLLECTION_ALIAS}/search/searchterm/{urllib.parse.quote(f'{house_num} {street_stem}')}"
            ),
            notes=notes,
        )

    return AuditFinding(
        hcad_num=hcad_num,
        address=address,
        historic_district=district,
        original_hcad_year=hcad_year,
        suggested_year_built=None,
        classification="NO_PRE_1926_EVIDENCE",
        earliest_dir_year=None,
        matched_dir_years=[],
        neighbor_dir_years=sorted_neighbor_years,
        source_type="Houston City Directory",
        source_citation="",
        source_url="",
        notes="No mentions found in 1866-1926 digitized directories.",
    )


def is_actionable_finding(item: AuditFinding) -> bool:
    """Return True if an audit finding represents a meaningful date addition or correction."""
    if item.classification in {
        "UNDATED_IN_HCAD_FOUND_IN_DIRECTORY",
        "EARLIER_THAN_HCAD",
        "ABSENT_THROUGH_1926_PREMATURE_HCAD_1920",
    }:
        return True
    if (
        item.classification == "CONFIRMED_IN_DIRECTORY"
        and item.suggested_year_built
        and item.original_hcad_year
        and abs(item.suggested_year_built - item.original_hcad_year) >= 2
    ):
        return True
    return False


def export_findings_to_csv(findings: list[AuditFinding], output_csv: Path) -> None:
    """Write audit findings to a CSV formatted for the Preservation Houston Google Sheet."""
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "status",
        "hcad_num",
        "address",
        "year_built",
        "original_hcad_year",
        "classification",
        "earliest_dir_year",
        "matched_dir_years",
        "neighbor_dir_years",
        "historic_district",
        "source_type",
        "source_citation",
        "source_url",
        "verified_by",
        "notes",
    ]
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in findings:
            actionable = is_actionable_finding(item)
            writer.writerow(
                {
                    "status": "Pending" if actionable else "Info",
                    "hcad_num": item.hcad_num,
                    "address": item.address,
                    "year_built": item.suggested_year_built or item.original_hcad_year,
                    "original_hcad_year": item.original_hcad_year,
                    "classification": item.classification,
                    "earliest_dir_year": item.earliest_dir_year or "",
                    "matched_dir_years": ";".join(str(y) for y in item.matched_dir_years),
                    "neighbor_dir_years": ";".join(str(y) for y in item.neighbor_dir_years),
                    "historic_district": item.historic_district,
                    "source_type": item.source_type,
                    "source_citation": item.source_citation,
                    "source_url": item.source_url,
                    "verified_by": "Houston City Directory Batch Auditor (1866-1926)",
                    "notes": item.notes,
                }
            )


def select_suspect_candidates(
    geojson_path: Path,
    district_filter: str | None = None,
    limit: int = 25,
    scope: str = "suspect",
    overlays_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Select properties for City Directory auditing.

    Scopes:
    - "suspect": rounded-decade HCAD years (1890, 1900, 1910, 1920, 1930)
    - "all_target": all properties with no year_built (year_built == 0) AND all properties
      in a City of Houston Historic District, Heritage District, or National Register (NRHP)
      Historic District, interleaved across districts so every district gets early coverage.
    """
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    suspect_years = {1890, 1900, 1910, 1920, 1930}

    # Optionally spatially enrich features with COH / Heritage / NRHP district names from overlays.json
    dist_polys: list[Any] = []
    dist_names: list[str] = []
    tree: Any = None
    if overlays_path is None:
        default_ov = geojson_path.parent / "overlays.json"
        if default_ov.exists():
            overlays_path = default_ov

    if overlays_path and overlays_path.exists():
        try:
            from shapely.geometry import shape
            from shapely.strtree import STRtree

            ov_data = json.loads(overlays_path.read_text(encoding="utf-8"))
            for key in ["historic_districts", "heritage_districts", "nrhp_districts"]:
                for f in ov_data.get(key, {}).get("features", []):
                    nm = str(f.get("properties", {}).get("name", "")).strip()
                    if nm and nm != "Outside Historic District" and f.get("geometry"):
                        dist_polys.append(shape(f["geometry"]))
                        dist_names.append(nm)
            if dist_polys:
                tree = STRtree(dist_polys)
        except Exception:
            tree = None

    seen_hcads: set[str] = set()
    candidates: list[dict[str, Any]] = []

    for feat in data.get("features", []):
        props = dict(feat.get("properties", {}))
        hcad = str(props.get("hcad_num", "")).strip()
        addr = str(props.get("address", "")).strip()
        if not hcad or hcad in seen_hcads or not addr or not re.match(r"^[1-9]\d+\s+", addr):
            continue
        seen_hcads.add(hcad)

        yr = int(props.get("year_built", 0) or 0)
        dist = str(props.get("historic_district", "")).strip()
        if dist == "Outside Historic District":
            dist = ""

        if not dist and tree is not None and feat.get("geometry"):
            try:
                from shapely.geometry import shape

                pt = shape(feat["geometry"]).centroid
                for idx in tree.query(pt, predicate="intersects"):
                    if dist_polys[idx].contains(pt):
                        dist = dist_names[idx]
                        break
            except Exception:
                pass

        props["historic_district"] = dist

        if district_filter and district_filter.lower() not in dist.lower():
            continue

        if scope == "all_target":
            if yr == 0 or bool(dist):
                candidates.append(props)
        else:
            if yr in suspect_years:
                candidates.append(props)

    if scope == "all_target":
        # Group into priority tiers and interleave across districts so every COH & NRHP
        # historic district gets immediate representation in the Google Sheet.
        from collections import defaultdict

        tier1_by_dist: dict[str, list[dict[str, Any]]] = defaultdict(list)  # In district & year == 0
        tier2_by_dist: dict[str, list[dict[str, Any]]] = defaultdict(list)  # In district & suspect decade
        tier3_undated_outside: list[dict[str, Any]] = []  # Outside district & year == 0
        tier4_other_dist: list[dict[str, Any]] = []  # Other years in district

        for p in candidates:
            yr = int(p.get("year_built", 0) or 0)
            dist = str(p.get("historic_district", "")).strip()
            if dist and yr == 0:
                tier1_by_dist[dist].append(p)
            elif dist and yr in suspect_years:
                tier2_by_dist[dist].append(p)
            elif yr == 0:
                tier3_undated_outside.append(p)
            else:
                tier4_other_dist.append(p)

        def _interleave(by_dist: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
            for lst in by_dist.values():
                lst.sort(
                    key=lambda x: (
                        0 if x.get("contributing") == "Contributing" else 1,
                        str(x.get("address", "")),
                    )
                )
            ordered_dists = sorted(by_dist.keys(), key=lambda d: (-len(by_dist[d]), d))
            out: list[dict[str, Any]] = []
            max_len = max((len(v) for v in by_dist.values()), default=0)
            for i in range(max_len):
                for d in ordered_dists:
                    if i < len(by_dist[d]):
                        out.append(by_dist[d][i])
            return out

        interleaved_t1 = _interleave(tier1_by_dist)
        interleaved_t2 = _interleave(tier2_by_dist)
        combined_priority: list[dict[str, Any]] = []
        max_t12 = max(len(interleaved_t1), len(interleaved_t2))
        for i in range(max_t12):
            if i < len(interleaved_t1):
                combined_priority.append(interleaved_t1[i])
            if i < len(interleaved_t2):
                combined_priority.append(interleaved_t2[i])

        interleaved = (
            combined_priority
            + sorted(tier3_undated_outside, key=lambda x: str(x.get("address", "")))
            + sorted(tier4_other_dist, key=lambda x: str(x.get("address", "")))
        )
        return interleaved[:limit] if limit > 0 else interleaved

    # Default "suspect" sort
    candidates.sort(
        key=lambda p: (
            0 if p.get("address") == "1127 KEY ST" else 1,
            0 if p.get("contributing") == "Contributing" else 1,
            0 if p.get("historic_district") else 1,
            str(p.get("address", "")),
        )
    )
    return candidates[:limit] if limit > 0 else candidates


def sync_findings_to_google_sheet(
    findings: list[AuditFinding],
    props_by_hcad: dict[str, dict[str, Any]],
    spreadsheet_id: str,
    gsheets_bin: str = "/google/bin/releases/gemini-agents-gsheets/gsheets",
) -> int:
    """Append newly discovered actionable findings to `Pending_Submissions` in Google Sheets."""
    import subprocess
    import tempfile

    actionable = [f for f in findings if is_actionable_finding(f) and f.suggested_year_built]
    if not actionable or not Path(gsheets_bin).exists():
        return 0

    # Read existing hcad_num values from Pending_Submissions so we never insert duplicates
    read_cmd = [
        gsheets_bin,
        "readonly",
        "read",
        spreadsheet_id,
        "Pending_Submissions!A1:B5000",
        "--json",
    ]
    proc = subprocess.run(read_cmd, capture_output=True, text=True, check=False)
    existing_hcads: set[str] = set()
    current_row_count = 1
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            rows = json.loads(proc.stdout)
            current_row_count = len(rows)
            for r in rows[1:]:
                if len(r) > 1 and r[1]:
                    existing_hcads.add(str(r[1]).strip().zfill(13))
        except Exception:
            pass

    new_rows: list[list[Any]] = []
    for item in actionable:
        hcad_padded = str(item.hcad_num).strip().zfill(13)
        if hcad_padded in existing_hcads:
            continue
        existing_hcads.add(hcad_padded)
        prop = props_by_hcad.get(item.hcad_num, {})
        contributing = str(prop.get("contributing", "") or "Contributing").strip()
        new_rows.append(
            [
                "Pending",
                hcad_padded,
                item.address,
                int(item.suggested_year_built or 0),
                int(item.original_hcad_year or 0),
                item.historic_district or "Historic Core",
                contributing,
                item.source_type,
                item.source_citation,
                item.source_url,
                "Houston City Directory Batch Auditor (1866-1926)",
                "City Directory Batch Auditor",
            ]
        )

    if not new_rows:
        return 0

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(
            [{"op": "append", "range": "Pending_Submissions!A1", "data": new_rows}],
            tmp,
        )
        tmp_path = tmp.name

    try:
        subprocess.run(
            [gsheets_bin, "mutate", "batch", spreadsheet_id, "-f", tmp_path, "--value-input", "RAW"],
            capture_output=True,
            text=True,
            check=False,
        )
        total_rows = current_row_count + len(new_rows)
        subprocess.run(
            [
                gsheets_bin,
                "mutate",
                "data-validation",
                spreadsheet_id,
                f"Pending_Submissions!A2:A{max(500, total_rows + 50)}",
                "--values",
                "Approved,Pending,Rejected",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return len(new_rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Polite Houston City Directory (1866-1926) crawler & HCAD data quality auditor."
    )
    parser.add_argument(
        "--buildings-geojson",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "app" / "public" / "data" / "buildings.geojson",
        help="Path to buildings.geojson to select properties from.",
    )
    parser.add_argument(
        "--cache-db",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "cache" / "citydir_cache.sqlite",
        help="SQLite database path for caching ContentDM search & page OCR responses.",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "cache" / "city_directory_audit_candidates.csv",
        help="Output CSV path for Google Sheet review candidates.",
    )
    parser.add_argument(
        "--district",
        type=str,
        default=None,
        help="Optional historic district substring filter (e.g. 'Norhill', 'Heights', 'Sixth Ward').",
    )
    parser.add_argument(
        "--address",
        type=str,
        default=None,
        help="Optional specific street address to audit (e.g. '1127 KEY ST').",
    )
    parser.add_argument(
        "--scope",
        type=str,
        default="suspect",
        choices=["suspect", "all_target"],
        help="Audit scope: 'suspect' (rounded decades) or 'all_target' (all undated + COH/NRHP districts).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of properties to audit in one run (0 = all matching).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.4,
        help="Polite minimum delay in seconds between live ContentDM HTTP requests (default: 1.4s).",
    )
    parser.add_argument(
        "--check-neighbors",
        action="store_true",
        help="Also query adjacent house numbers (+-2, +-4) when a 1920-dated house is absent.",
    )
    parser.add_argument(
        "--sync-sheet-id",
        type=str,
        default="",
        help="Optional Google Sheet ID to incrementally append Pending findings into Pending_Submissions.",
    )
    parser.add_argument(
        "--sync-every",
        type=int,
        default=25,
        help="Flush newly discovered Pending findings to CSV and Google Sheet every N audited properties.",
    )
    args = parser.parse_args()

    client = ContentDmCityDirectoryClient(cache_db_path=args.cache_db, min_delay_seconds=args.delay)
    try:
        vol_catalog = client.ensure_volume_catalog()
        print(f"Loaded {len(vol_catalog)} Houston City Directory volumes (1866-1926) in SQLite cache.", flush=True)

        if args.address:
            props_list: list[dict[str, Any]] = []
            if args.buildings_geojson.exists():
                data = json.loads(args.buildings_geojson.read_text(encoding="utf-8"))
                for feat in data.get("features", []):
                    p = feat.get("properties", {})
                    if str(p.get("address", "")).strip().upper() == args.address.strip().upper():
                        props_list.append(p)
                        break
            if not props_list:
                props_list = [
                    {
                        "hcad_num": "UNKNOWN",
                        "address": args.address.strip().upper(),
                        "year_built": 1920,
                        "historic_district": args.district or "",
                    }
                ]
        else:
            props_list = select_suspect_candidates(
                args.buildings_geojson,
                district_filter=args.district,
                limit=args.limit,
                scope=args.scope,
            )

        props_by_hcad = {str(p.get("hcad_num", "")).strip(): p for p in props_list}
        print(
            f"Auditing {len(props_list)} HCAD property record(s) (scope={args.scope}, delay={args.delay}s)...",
            flush=True,
        )
        findings: list[AuditFinding] = []
        total_synced = 0

        for idx, prop in enumerate(props_list, 1):
            hcad_num = str(prop.get("hcad_num", "")).strip()
            cached = client.get_cached_finding(hcad_num) if not args.check_neighbors else None
            if cached is not None:
                # Ensure historic_district is preserved if enriched
                if prop.get("historic_district") and not cached.historic_district:
                    cached.historic_district = str(prop["historic_district"])
                finding = cached
            else:
                finding = audit_suspect_property(client, prop, check_neighbors=args.check_neighbors)
                client.save_cached_finding(finding)

            findings.append(finding)
            if is_actionable_finding(finding):
                print(
                    f"  [{idx}/{len(props_list)}] ★ {finding.address} ({finding.historic_district or 'Core'}, "
                    f"HCAD {finding.original_hcad_year}) -> {finding.classification} | "
                    f"suggested={finding.suggested_year_built} | {finding.source_citation[:110]}",
                    flush=True,
                )
            elif idx % 20 == 0 or idx == len(props_list):
                print(
                    f"  [{idx}/{len(props_list)}] Audited {finding.address} -> {finding.classification}",
                    flush=True,
                )

            if args.sync_every > 0 and (idx % args.sync_every == 0 or idx == len(props_list)):
                export_findings_to_csv(findings, args.output_csv)
                if args.sync_sheet_id:
                    added = sync_findings_to_google_sheet(
                        findings,
                        props_by_hcad=props_by_hcad,
                        spreadsheet_id=args.sync_sheet_id,
                    )
                    if added > 0:
                        total_synced += added
                        print(
                            f"  --> Synced +{added} new Pending row(s) to Google Sheet (total added this run: {total_synced})",
                            flush=True,
                        )

        export_findings_to_csv(findings, args.output_csv)
        print(
            f"Completed audit of {len(findings)} properties ({sum(1 for f in findings if is_actionable_finding(f))} actionable). "
            f"Exported to {args.output_csv}.",
            flush=True,
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
