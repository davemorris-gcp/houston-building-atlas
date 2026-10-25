#!/usr/bin/env python3
"""Ingest City of Houston HAHC (Houston Archaeological & Historical Commission)
monthly agenda packets, parcel COA staff reports, and 2024-2026 official action
minutes into `app/public/data/hahc_coa_tracker.json`, `overlays.json`, and
`search_index.json`.

Uses only Python 3 standard library + `pdftotext` (`poppler-utils`) and committed
`app/public/data/` files so it can run both locally and in GitHub Actions CI.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "app" / "public" / "data"
CACHE_DIR = ROOT / "pipeline" / "cache" / "hahc_pdfs"
HAHC_PAGE_URL = "https://www.houstontx.gov/planning/Commissions/HAHC.html"
USER_AGENT = "Mozilla/5.0 (compatible; PreservationHoustonAtlas/2.0)"

STREET_SUFFIX_MAP = {
    "STREET": "ST",
    "STRET": "ST",
    "ST": "ST",
    "AVENUE": "AVE",
    "AVE": "AVE",
    "DRIVE": "DR",
    "DR": "DR",
    "BOULEVARD": "BLVD",
    "BLVD": "BLVD",
    "ROAD": "RD",
    "RD": "RD",
    "LANE": "LN",
    "LN": "LN",
    "COURT": "CT",
    "CT": "CT",
    "PLACE": "PL",
    "PL": "PL",
    "CIRCLE": "CIR",
    "CIR": "CIR",
    "PARKWAY": "PKWY",
    "PKWY": "PKWY",
    "TERRACE": "TER",
    "WAY": "WAY",
}

CANONICAL_DISTRICTS = [
    "Audubon Place",
    "Avondale East",
    "Avondale West",
    "Boulevard Oaks",
    "Broadacres",
    "Courtlandt Place",
    "First Montrose Commons",
    "Freedmen's Town",
    "Freeland",
    "Germantown",
    "Glenbrook Valley",
    "Highland Village",
    "Houston Heights East",
    "Houston Heights South",
    "Houston Heights West",
    "Main Street Market Square",
    "Norhill",
    "Old Sixth Ward",
    "Shadow Lawn",
    "Starkweather",
    "West Eleventh Place",
    "Westmoreland",
    "Woodland Heights",
]


def clean_ascii_punct(text: str) -> str:
    if not text:
        return ""
    s = (
        str(text)
        .replace("\u2014", " - ")
        .replace("\u2013", " - ")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u00bd", "1/2")
    )
    return re.sub(r"\s+", " ", s).strip()


def encode_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    path = urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/:@")
    query = urllib.parse.quote(urllib.parse.unquote(parsed.query), safe="=&")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, ""))


def fetch_bytes(url: str, timeout: int = 25) -> bytes:
    enc = encode_url(url)
    if CACHE_DIR.parent.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(enc.encode("utf-8")).hexdigest()[:16] + "_" + Path(urllib.parse.unquote(enc)).name
        key = re.sub(r"[^A-Za-z0-9._-]+", "_", key)
        cp = CACHE_DIR / key
        if cp.exists() and cp.stat().st_size > 200 and not enc.endswith(".html"):
            return cp.read_bytes()
    else:
        cp = None

    req = urllib.request.Request(enc, headers={"User-Agent": USER_AGENT})
    data = urllib.request.urlopen(req, timeout=timeout).read()
    if cp is not None and not enc.endswith(".html") and len(data) > 200:
        cp.write_bytes(data)
    return data


def pdf_bytes_to_layout_text(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tf:
        tf.write(data)
        tf.flush()
        return subprocess.check_output(["pdftotext", "-layout", tf.name, "-"], text=True)


def parse_meeting_date_from_url_or_text(url: str, text: str) -> str:
    fname = urllib.parse.unquote(url.split("/")[-1])
    # Patterns: 10_08_2026, 09.10.2026, 05.09.24, hahc_100826
    m = re.search(r"(\d{1,2})[._-](\d{1,2})[._-](20\d{2})", fname)
    if m:
        mm, dd, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            return f"{yyyy:04d}-{mm:02d}-{dd:02d}"
    m = re.search(r"(\d{1,2})[._-](\d{1,2})[._-](\d{2})(?!\d)", fname)
    if m:
        mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            return f"{2000 + yy:04d}-{mm:02d}-{dd:02d}"
    m = re.search(r"hahc_(\d{2})(\d{2})(\d{2})", url, re.I)
    if m:
        mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            return f"{2000 + yy:04d}-{mm:02d}-{dd:02d}"
    months = {
        "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4, "MAY": 5, "JUNE": 6,
        "JULY": 7, "AUGUST": 8, "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
    }
    head = text[:2500].upper()
    m = re.search(
        r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+(\d{1,2}),?\s+(20\d{2})",
        head,
    )
    if m:
        return f"{int(m.group(3)):04d}-{months[m.group(1)]:02d}-{int(m.group(2)):02d}"
    m = re.search(
        r"(\d{1,2})\s+(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+(20\d{2})",
        head,
    )
    if m:
        return f"{int(m.group(3)):04d}-{months[m.group(2)]:02d}-{int(m.group(1)):02d}"
    return ""


def normalize_district_name(raw_dist: str) -> tuple[str, str]:
    """Return (canonical_historic_district_or_empty, landmark_label_or_empty)."""
    cleaned = clean_ascii_punct(raw_dist)
    if not cleaned:
        return "", ""
    upper = cleaned.upper()
    for cd in CANONICAL_DISTRICTS:
        if cd.upper() in upper:
            return cd, ""
    if "HEIGHTS SOUTH" in upper:
        return "Houston Heights South", ""
    if "HEIGHTS EAST" in upper:
        return "Houston Heights East", ""
    if "HEIGHTS WEST" in upper:
        return "Houston Heights West", ""
    if "SIXTH WARD" in upper or "6TH WARD" in upper:
        return "Old Sixth Ward", ""
    if "MARKET SQUARE" in upper:
        return "Main Street Market Square", ""
    if "FREEDMEN" in upper:
        return "Freedmen's Town", ""
    if "MONTROSE COMMONS" in upper:
        return "First Montrose Commons", ""
    if "11TH PLACE" in upper or "ELEVENTH PLACE" in upper:
        return "West Eleventh Place", ""
    lm = re.sub(r"^(LANDMARK|PLM|LM)\s*:\s*", "", cleaned, flags=re.I).strip()
    return "", lm


def classify_app_category(app_type: str, is_designation: bool = False) -> str:
    if is_designation:
        return "designation"
    u = app_type.upper()
    if "DESIGNATION" in u or "PROTECTED LANDMARK" in u or "LANDMARK" in u and "ALTERATION" not in u:
        return "designation"
    if "DEMOLITION" in u or "RELOCATION" in u:
        return "demolition_relocation"
    if "NEW CONSTRUCTION" in u or "INFILL" in u:
        return "new_construction"
    if "REMEDIATION" in u or "COR" in re.findall(r"\b[A-Z]+\b", u):
        return "remediation_cor"
    return "addition_alteration"


def classify_outcome(raw_rec: str, is_active_agenda: bool) -> tuple[str, str]:
    cleaned = clean_ascii_punct(raw_rec)
    if is_active_agenda and not cleaned:
        return "pending_agenda", "Scheduled for HAHC Public Hearing"
    u = cleaned.upper()
    if not u:
        return ("pending_agenda", "Scheduled for HAHC Public Hearing") if is_active_agenda else ("approved", "Approved")
    if "DENIAL" in u or "DENIED" in u or "ISSUANCE OF COR" in u or "DENIAL/COR" in u or "DENIAL. COR" in u:
        return "denied_or_cor", cleaned
    if "DEFER" in u or "WITHDRAW" in u or "NO ACTION" in u:
        return "deferred", cleaned
    if "COND" in u or "PARTIAL" in u:
        return "approved_conditions", cleaned
    if "APPROV" in u or "FORWARD" in u or "ACCEPT" in u:
        return "approved", cleaned
    if is_active_agenda:
        return "pending_agenda", cleaned
    return "approved", cleaned


def parse_coa_tables_from_text(
    text: str,
    meeting_date: str,
    pdf_url: str,
    is_active_agenda: bool,
    staff_report_lookup: dict[str, str],
) -> list[dict]:
    items: list[dict] = []
    lines = text.splitlines()

    # 1. Extract standalone Landmark / Protected Landmark Designation public hearing blocks
    designation_pat = re.compile(
        r"LANDMARK\s+DESIGNATION\s+APPLICATION\s+FOR\s+([^,\n]+),\s*(\d+[^\n,]+)",
        re.I,
    )
    for m in designation_pat.finditer(text):
        lm_name = clean_ascii_punct(m.group(1))
        addr = clean_ascii_punct(m.group(2))
        addr = re.sub(r"\s+HOUSTON.*$", "", addr, flags=re.I).strip(" .")
        snippet = clean_ascii_punct(text[m.end() : m.end() + 650])
        is_plm = "PROTECTED LANDMARK" in snippet.upper()
        outcome = "pending_agenda" if is_active_agenda else "approved"
        if "DENI" in snippet.upper():
            outcome = "denied_or_cor"
        elif "DEFER" in snippet.upper():
            outcome = "deferred"
        detail = (
            "Scheduled for HAHC Public Hearing (Landmark Designation)"
            if is_active_agenda
            else (
                "Approved forwarding to City Council for Protected Landmark (PLM) designation"
                if is_plm
                else "Approved forwarding to City Council for Landmark designation"
            )
        )
        items.append(
            {
                "meeting_date": meeting_date,
                "item_num": "PH",
                "address": addr,
                "app_type": "Protected Landmark (PLM) Designation" if is_plm else "Landmark (LM) Designation",
                "app_category": "designation",
                "historic_district": "",
                "landmark_name": lm_name,
                "outcome": outcome,
                "outcome_detail": detail,
                "is_active_agenda": is_active_agenda,
                "agenda_or_minutes_url": encode_url(pdf_url),
                "staff_report_url": "",
            }
        )

    # 2. Parse tabular COA sections using 2+-space token spans assigned to column centers
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        win_hdr = " ".join(lines[max(0, i - 1) : min(n, i + 2)])
        if (
            "address" in line.lower()
            and "application type" in line.lower()
            and any(k in win_hdr.lower() for k in ("historic", "district", "landmark"))
        ):
            # Compute header column centers across lines[i-1 .. i+1]
            def find_span_bounds(keyword_patterns: list[str], min_x: int = 0) -> tuple[int, int] | None:
                for hline in (lines[i], lines[max(0, i - 1)], lines[min(n - 1, i + 1)]):
                    for pat in keyword_patterns:
                        m = re.search(pat, hline, re.I)
                        if m and m.start() >= min_x:
                            return (m.start(), m.end())
                return None

            b_num = find_span_bounds([r"#", r"\bNo\.?\b"], 0) or (2, 5)
            b_addr = find_span_bounds([r"\bAddress\b"], b_num[0] + 1) or (12, 22)
            b_type = find_span_bounds([r"Application\s+Type"], b_addr[1] + 2) or (36, 54)
            b_dist = find_span_bounds(
                [r"Historic\s+District(?:\s+or)?", r"Historic", r"District/PLM/LM", r"\bLandmark\b"],
                b_type[1] + 2,
            ) or (64, 82)
            b_rec = find_span_bounds(
                [r"Staff\s+Recommendation", r"\bStaff\b", r"Recommendation"],
                b_dist[0] + 10,
            )

            col_defs: list[tuple[str, float, float]] = [
                ("num", float(b_num[0]), (b_num[0] + b_num[1]) / 2.0),
                ("addr", float(b_addr[0]), (b_addr[0] + b_addr[1]) / 2.0),
                ("type", float(b_type[0]), (b_type[0] + b_type[1]) / 2.0),
                ("dist", float(b_dist[0]), (b_dist[0] + b_dist[1]) / 2.0),
            ]
            if b_rec and b_rec[0] > b_dist[0] + 8:
                col_defs.append(("rec", float(b_rec[0]), (b_rec[0] + b_rec[1]) / 2.0))

            # Advance past multi-line header rows
            i += 1
            while i < n and (
                not lines[i].strip()
                or (
                    any(w in lines[i] for w in ("District", "Landmark", "Recommendation"))
                    and not re.search(r"^\s*(\d{1,2}|\*)\s{2,}\d", lines[i])
                    and not re.search(r"\b(Alteration|New Construction|Demolition|Relocation)\b", lines[i], re.I)
                )
            ):
                i += 1

            table_rows_raw: list[tuple[str, str, str, str, str]] = []
            while i < n:
                raw = lines[i].replace("\x0c", "")
                stripped = raw.strip()
                if not stripped:
                    look = i + 1
                    while look < min(n, i + 8) and not lines[look].strip():
                        look += 1
                    if look < n:
                        nxt = lines[look].strip()
                        if (
                            nxt.startswith("Staff recommendation:")
                            or nxt.startswith("Staff Recommendation:")
                            or nxt.startswith("Commission action:")
                            or re.match(r"^[A-G]\.\s+[A-Z0-9]", nxt)
                            or nxt.startswith("PUBLIC COMMENTS")
                            or nxt.startswith("ADJOURNMENT")
                        ):
                            break
                    i += 1
                    continue

                if (
                    stripped.startswith("Staff recommendation:")
                    or stripped.startswith("Staff Recommendation:")
                    or stripped.startswith("Commission action:")
                    or stripped.startswith("Motion:")
                    or stripped.startswith("Speaker(s):")
                    or re.match(r"^[A-G]\.\s*(CONSIDERATION|PUBLIC HEARING|DIRECTOR|ADJOURN|\d+\.)", stripped, re.I)
                    or stripped.startswith("PUBLIC COMMENTS")
                    or stripped.startswith("ADJOURNMENT")
                ):
                    break

                if (
                    stripped.startswith("Docusign Envelope ID:")
                    or re.match(r"^HAHC\s+\d+\s+of\s+\d+$", stripped)
                    or ("address" in raw.lower() and "application type" in raw.lower())
                    or (
                        any(w in stripped.lower() for w in ("district/plm/lm", "recommendation", "or landmark"))
                        and len(stripped.split()) <= 4
                    )
                ):
                    i += 1
                    continue

                buckets_line = {"num": [], "addr": [], "type": [], "dist": [], "rec": []}
                spans = list(re.finditer(r"\S+(?:\s\S+)*", raw))
                for sp_idx, m_sp in enumerate(spans):
                    txt_sp = m_sp.group(0).strip()
                    sx, ex = m_sp.start(), m_sp.end()
                    mx = (sx + ex) / 2.0
                    if sp_idx == 0 and re.match(r"^(\d{1,2}|\*)\.?$", txt_sp) and sx < col_defs[1][2]:
                        buckets_line["num"].append(txt_sp.rstrip("."))
                        continue
                    if sp_idx == 0 and sx < col_defs[1][2]:
                        m_comb = re.match(r"^(\d{1,2}|\*)\.?\s+(\d{2,5}\b.*)$", txt_sp)
                        if m_comb:
                            buckets_line["num"].append(m_comb.group(1))
                            buckets_line["addr"].append(m_comb.group(2))
                            continue
                    # Pick column minimizing blend of left-edge distance and center distance (excluding 'num')
                    best_col = min(
                        col_defs[1:],
                        key=lambda cd: min(abs(sx - cd[1]), abs(mx - cd[2])),
                    )[0]
                    buckets_line[best_col].append(txt_sp)

                table_rows_raw.append(
                    (
                        " ".join(buckets_line["num"]).strip(),
                        " ".join(buckets_line["addr"]).strip(),
                        " ".join(buckets_line["type"]).strip(),
                        " ".join(buckets_line["dist"]).strip(),
                        " ".join(buckets_line["rec"]).strip(),
                    )
                )
                i += 1

            anchor_indices = [
                idx
                for idx, r in enumerate(table_rows_raw)
                if re.match(r"^(\d{1,2}|\*)$", r[0]) and re.search(r"\d", r[1])
            ]
            if not anchor_indices:
                continue

            anchor_set = set(anchor_indices)
            for pos_a, a in enumerate(anchor_indices):
                prev_a = anchor_indices[pos_a - 1] if pos_a > 0 else -999
                next_a = anchor_indices[pos_a + 1] if pos_a + 1 < len(anchor_indices) else 999999
                assigned_indices = []
                for idx_r in range(max(0, a - 2), min(len(table_rows_raw), a + 3)):
                    if idx_r == a:
                        assigned_indices.append(idx_r)
                    elif idx_r not in anchor_set:
                        # Assign continuation line to closest anchor
                        d_curr = abs(idx_r - a)
                        d_prev = abs(idx_r - prev_a)
                        d_next = abs(idx_r - next_a)
                        if d_curr < d_prev and d_curr <= d_next:
                            assigned_indices.append(idx_r)

                item_num = table_rows_raw[a][0]
                addr_str = clean_ascii_punct(" ".join(table_rows_raw[k][1] for k in assigned_indices if table_rows_raw[k][1]))
                type_str = clean_ascii_punct(" ".join(table_rows_raw[k][2] for k in assigned_indices if table_rows_raw[k][2]))
                dist_str = clean_ascii_punct(" ".join(table_rows_raw[k][3] for k in assigned_indices if table_rows_raw[k][3]))
                rec_str = clean_ascii_punct(" ".join(table_rows_raw[k][4] for k in assigned_indices if table_rows_raw[k][4]))

                addr_str = addr_str.rstrip(".,;")
                if not re.match(r"^\d+\s*\S+", addr_str):
                    continue

                hd_name, lm_name = normalize_district_name(dist_str)
                app_cat = classify_app_category(type_str)
                outcome, outcome_detail = classify_outcome(rec_str, is_active_agenda)

                sr_url = ""
                if is_active_agenda:
                    sr_url = match_staff_report_url(item_num, addr_str, staff_report_lookup)

                items.append(
                    {
                        "meeting_date": meeting_date,
                        "item_num": item_num,
                        "address": addr_str,
                        "app_type": type_str or "Certificate of Appropriateness",
                        "app_category": app_cat,
                        "historic_district": hd_name,
                        "landmark_name": lm_name,
                        "outcome": outcome,
                        "outcome_detail": outcome_detail,
                        "is_active_agenda": is_active_agenda,
                        "agenda_or_minutes_url": encode_url(pdf_url),
                        "staff_report_url": sr_url,
                    }
                )
        else:
            i += 1

    return items


def match_staff_report_url(item_num: str, addr_str: str, staff_report_lookup: dict[str, str]) -> str:
    if item_num.isdigit():
        code = f"A{int(item_num):02d}"
        if code in staff_report_lookup:
            return staff_report_lookup[code]
    m = re.match(r"^(\d+)", addr_str.strip())
    if m:
        hnum = m.group(1)
        for k, u in staff_report_lookup.items():
            if f"_{hnum}_" in u or f"_{hnum}-" in u:
                return u
    return ""


def normalize_addr_tokens(address: str) -> tuple[str, list[str]]:
    u = clean_ascii_punct(address).upper()
    u = re.sub(r"\b1\s*/\s*2\b", "", u)
    u = re.sub(r"[^A-Z0-9\s]", " ", u)
    parts = u.split()
    if not parts:
        return "", []
    m = re.match(r"^(\d+)[A-Z]?$", parts[0])
    if not m:
        return "", []
    hnum = m.group(1)
    rem = parts[1:]
    if rem and rem[-1] in STREET_SUFFIX_MAP:
        rem[-1] = STREET_SUFFIX_MAP[rem[-1]]
    return hnum, rem


def point_in_ring(lng: float, lat: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def point_in_geom(lng: float, lat: float, geom: dict) -> bool:
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    if gtype == "Polygon" and coords:
        return point_in_ring(lng, lat, coords[0])
    if gtype == "MultiPolygon":
        return any(poly and point_in_ring(lng, lat, poly[0]) for poly in coords)
    return False


class ParcelGeocoder:
    def __init__(self, overlays: dict, search_index: list[dict]) -> None:
        self._shard_cache: dict[str, dict] = {}
        self.district_polys: list[tuple[str, dict, tuple[float, float]]] = []
        for feat in overlays.get("historic_districts", {}).get("features", []):
            props = feat.get("properties", {})
            geom = feat.get("geometry") or {}
            dname = clean_ascii_punct(
                props.get("district_name") or props.get("NAME") or props.get("name") or ""
            )
            dname_canon, _ = normalize_district_name(dname)
            label = dname_canon or dname
            # Compute simple centroid fallback
            ring = (
                geom.get("coordinates", [[]])[0]
                if geom.get("type") == "Polygon"
                else (geom.get("coordinates", [[[]]])[0][0] if geom.get("type") == "MultiPolygon" else [])
            )
            if ring:
                cx = sum(p[0] for p in ring) / len(ring)
                cy = sum(p[1] for p in ring) / len(ring)
                self.district_polys.append((label, geom, (cx, cy)))

        self.addr_fast: dict[str, dict] = {}
        for row in search_index:
            addr = (row.get("address") or "").strip()
            lon = row.get("lon") or row.get("lng")
            lat = row.get("lat")
            if addr and lon and lat:
                hnum, toks = normalize_addr_tokens(addr)
                if hnum and toks:
                    key = f"{hnum} {' '.join(toks)}"
                    if key not in self.addr_fast or row.get("hcad_num"):
                        self.addr_fast[key] = {
                            "hcad_num": str(row.get("hcad_num") or ""),
                            "lng": float(lon),
                            "lat": float(lat),
                            "year_built": int(row.get("year_built") or 0),
                        }

    def _load_shard(self, shard_key: str) -> dict:
        if shard_key not in self._shard_cache:
            sp = DATA_DIR / "address_shards" / f"addr_{shard_key}.json"
            self._shard_cache[shard_key] = json.loads(sp.read_text()) if sp.exists() else {}
        return self._shard_cache[shard_key]

    def resolve(self, address: str, district_hint: str = "") -> dict | None:
        hnum, toks = normalize_addr_tokens(address)
        if not hnum or not toks:
            return None
        exact_key = f"{hnum} {' '.join(toks)}"
        if exact_key in self.addr_fast:
            res = dict(self.addr_fast[exact_key])
            res["matched_district"] = self._district_at(res["lng"], res["lat"]) or district_hint
            return res

        shard_key = hnum[:2].zfill(2) if len(hnum) >= 2 else f"0{hnum}"
        shard = self._load_shard(shard_key)
        core_toks = [t for t in toks if t not in set(STREET_SUFFIX_MAP.values())]
        target_street = " ".join(toks)
        best = None
        best_score = -1

        for st_name, rows in shard.items():
            st_upper = st_name.upper().strip()
            if core_toks and not all(t in st_upper.split() or t in st_upper for t in core_toks):
                continue
            for r in rows:
                if str(r[0]).upper() == hnum:
                    lng, lat = float(r[1]), float(r[2])
                    yr = int(r[3] or 0)
                    hcad = str(r[4] or "")
                    score = 10 if st_upper == target_street else (6 if st_upper.split()[:len(core_toks)] == core_toks else 3)
                    dist_at = self._district_at(lng, lat)
                    if district_hint and dist_at and dist_at.upper() == district_hint.upper():
                        score += 20
                    if hcad:
                        score += 2
                    if score > best_score:
                        best_score = score
                        best = {
                            "hcad_num": hcad,
                            "lng": lng,
                            "lat": lat,
                            "year_built": yr,
                            "matched_district": dist_at or district_hint,
                        }
        return best

    def _district_at(self, lng: float, lat: float) -> str:
        for dname, geom, _ in self.district_polys:
            if point_in_geom(lng, lat, geom):
                return dname
        return ""


def verify_urls_head(urls: list[str]) -> dict[str, int]:
    results: dict[str, int] = {}
    for u in urls:
        enc = encode_url(u)
        try:
            req = urllib.request.Request(enc, method="HEAD", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                results[enc] = resp.status
        except urllib.error.HTTPError as e:
            if e.code == 405:
                req = urllib.request.Request(enc, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-64"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    results[enc] = resp.status
            else:
                results[enc] = e.code
        except Exception:
            results[enc] = 0
    return results


def main() -> None:
    print("Fetching City of Houston HAHC page:", HAHC_PAGE_URL)
    html = fetch_bytes(HAHC_PAGE_URL).decode("utf-8", "ignore")
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.I)

    pdf_urls: list[str] = []
    for h in hrefs:
        if ".pdf" in h.lower():
            full = urllib.parse.urljoin(HAHC_PAGE_URL, h.strip())
            enc = encode_url(full)
            if enc not in pdf_urls:
                pdf_urls.append(enc)

    # Identify active agenda folder (e.g., hahc_100826/)
    active_packet_urls = [u for u in pdf_urls if "/hahc_" in u.lower()]
    active_agenda_pdfs = [
        u for u in active_packet_urls if "agenda" in u.lower()
    ]
    staff_report_lookup: dict[str, str] = {}
    guideline_updates: list[dict] = []
    for u in active_packet_urls:
        fname = urllib.parse.unquote(u.split("/")[-1])
        m = re.match(r"^(A\d{2})[_-]", fname, re.I)
        if m:
            staff_report_lookup[m.group(1).upper()] = u
        elif "redline" in fname.lower() or "guideline" in fname.lower():
            guideline_updates.append(
                {
                    "title": clean_ascii_punct(fname.replace(".pdf", "").replace("_", " ")),
                    "pdf_url": u,
                }
            )

    # Identify 2024, 2025, 2026 minutes PDFs (excluding Ethics Training)
    minutes_pdfs: list[str] = []
    for u in pdf_urls:
        ul = u.lower()
        if "ethics" in ul or "parkingmap" in ul:
            continue
        if ("/hahc_minutes/2026/" in ul or "/hahc_minutes/2025/" in ul or "/hahc_minutes/2024/" in ul or "minutes" in ul and "/hahc_" in ul):
            minutes_pdfs.append(u)

    # Deduplicate same meeting date (prefer ACTION over DRAFT if both exist)
    by_date_pdf: dict[str, tuple[int, str, str]] = {}
    for u in minutes_pdfs:
        try:
            txt = pdf_bytes_to_layout_text(fetch_bytes(u))
        except Exception as exc:
            print("  Skipping unreadable PDF:", u, exc)
            continue
        mdate = parse_meeting_date_from_url_or_text(u, txt)
        if not mdate or not mdate.startswith(("2024-", "2025-", "2026-")):
            continue
        prio = 2 if "action" in u.lower() else 1
        if mdate not in by_date_pdf or prio > by_date_pdf[mdate][0]:
            by_date_pdf[mdate] = (prio, u, txt)

    all_filings: list[dict] = []
    current_cycle_date = ""
    current_agenda_url = active_agenda_pdfs[0] if active_agenda_pdfs else ""
    if current_agenda_url:
        ag_txt = pdf_bytes_to_layout_text(fetch_bytes(current_agenda_url))
        current_cycle_date = parse_meeting_date_from_url_or_text(current_agenda_url, ag_txt)
        ag_items = parse_coa_tables_from_text(
            ag_txt,
            current_cycle_date,
            current_agenda_url,
            is_active_agenda=True,
            staff_report_lookup=staff_report_lookup,
        )
        print(f"Active HAHC Agenda ({current_cycle_date}): {len(ag_items)} items parsed")
        all_filings.extend(ag_items)

    for mdate in sorted(by_date_pdf.keys(), reverse=True):
        if mdate == current_cycle_date:
            continue
        _, u, txt = by_date_pdf[mdate]
        m_items = parse_coa_tables_from_text(
            txt,
            mdate,
            u,
            is_active_agenda=False,
            staff_report_lookup={},
        )
        print(f"  Minutes {mdate}: {len(m_items)} items")
        all_filings.extend(m_items)

    print(f"Total raw HAHC items parsed across {len(by_date_pdf) + (1 if current_cycle_date else 0)} meetings: {len(all_filings)}")

    overlays = json.loads((DATA_DIR / "overlays.json").read_text())
    search_index = json.loads((DATA_DIR / "search_index.json").read_text())
    geocoder = ParcelGeocoder(overlays, search_index)

    geocoded_filings: list[dict] = []
    unmatched_addrs: list[str] = []
    for idx, item in enumerate(all_filings):
        geo = geocoder.resolve(item["address"], item["historic_district"])
        if not geo:
            unmatched_addrs.append(f"{item['address']} ({item['historic_district'] or item['landmark_name']})")
            continue
        hd = item["historic_district"] or geo["matched_district"] or ""
        filing_id = f"hahc_{item['meeting_date'].replace('-', '')}_{idx:04d}"
        rec = {
            **item,
            "filing_id": filing_id,
            "historic_district": hd,
            "hcad_num": geo["hcad_num"],
            "lng": round(geo["lng"], 6),
            "lat": round(geo["lat"], 6),
            "year_built": geo["year_built"],
        }
        geocoded_filings.append(rec)

    print(f"Geocoded {len(geocoded_filings)} / {len(all_filings)} filings ({len(unmatched_addrs)} unmatched)")
    if unmatched_addrs:
        print("Sample unmatched:", unmatched_addrs[:12])

    # Sort chronologically newest first
    geocoded_filings.sort(
        key=lambda r: (1 if r["is_active_agenda"] else 0, r["meeting_date"], r["address"]),
        reverse=True,
    )

    # Verify HTTP 200 on all distinct PDF URLs referenced
    distinct_urls = sorted(
        {
            u
            for r in geocoded_filings
            for u in (r.get("agenda_or_minutes_url", ""), r.get("staff_report_url", ""))
            if u
        }
        | ({current_agenda_url} if current_agenda_url else set())
        | {g["pdf_url"] for g in guideline_updates}
    )
    print(f"Verifying HTTP status on {len(distinct_urls)} unique HAHC PDF URLs...")
    url_status = verify_urls_head(distinct_urls)
    bad_urls = {u: st for u, st in url_status.items() if st not in (200, 206)}
    if bad_urls:
        print("WARNING - non-200 URLs detected:", bad_urls)
    else:
        print(f"All {len(distinct_urls)} unique HAHC PDF URLs returned HTTP 200!")

    # Group by parcel key (`hcad_num` if 13 digits else rounded `lng,lat`) for map pins
    by_parcel: dict[str, list[dict]] = defaultdict(list)
    for r in geocoded_filings:
        pkey = r["hcad_num"] if len(r["hcad_num"]) == 13 else f"{r['lng']:.5f}_{r['lat']:.5f}"
        by_parcel[pkey].append(r)

    features: list[dict] = []
    for pkey, flist in by_parcel.items():
        top = flist[0]
        has_active = any(f["is_active_agenda"] for f in flist)
        has_demo = any(f["app_category"] == "demolition_relocation" for f in flist)
        has_desig = any(f["app_category"] == "designation" for f in flist)
        has_denied = any(f["outcome"] == "denied_or_cor" for f in flist)
        primary_cat = (
            "active_agenda"
            if has_active
            else (
                "demolition_relocation"
                if has_demo
                else ("designation" if has_desig else top["app_category"])
            )
        )
        slug = re.sub(r"[^a-z0-9]+", "_", f"{pkey}_{top['address'].lower()}").strip("_")
        compact_filings = [
            {
                "meeting_date": f["meeting_date"],
                "item_num": f["item_num"],
                "app_type": f["app_type"],
                "app_category": f["app_category"],
                "outcome": f["outcome"],
                "outcome_detail": f["outcome_detail"],
                "is_active_agenda": f["is_active_agenda"],
                "agenda_or_minutes_url": f["agenda_or_minutes_url"],
                "staff_report_url": f["staff_report_url"],
            }
            for f in flist[:8]
        ]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [top["lng"], top["lat"]]},
                "properties": {
                    "id": f"hahc_{slug}",
                    "hcad_num": top["hcad_num"],
                    "address": top["address"],
                    "historic_district": top["historic_district"],
                    "landmark_name": top["landmark_name"],
                    "year_built": top["year_built"],
                    "filings_count": len(flist),
                    "has_active_agenda": has_active,
                    "has_demolition_relocation": has_demo,
                    "has_designation": has_desig,
                    "has_denied_or_cor": has_denied,
                    "primary_category": primary_cat,
                    "latest_meeting_date": top["meeting_date"],
                    "latest_app_type": top["app_type"],
                    "latest_outcome": top["outcome"],
                    "latest_outcome_detail": top["outcome_detail"],
                    "latest_pdf_url": top["staff_report_url"] or top["agenda_or_minutes_url"],
                    "staff_report_url": top["staff_report_url"],
                    "agenda_or_minutes_url": top["agenda_or_minutes_url"],
                    "filings_json": json.dumps(compact_filings, separators=(",", ":")),
                },
            }
        )

    # Build per-Historic-District precedent summaries
    district_precedents: dict[str, dict] = {}
    by_hd: dict[str, list[dict]] = defaultdict(list)
    for r in geocoded_filings:
        if r["historic_district"]:
            by_hd[r["historic_district"]].append(r)

    for hd_name, hlist in sorted(by_hd.items()):
        c_out = Counter(f["outcome"] for f in hlist)
        c_cat = Counter(f["app_category"] for f in hlist)
        district_precedents[hd_name] = {
            "historic_district": hd_name,
            "total_filings": len(hlist),
            "unique_parcels": len({f["hcad_num"] or f["address"] for f in hlist}),
            "active_agenda_count": sum(1 for f in hlist if f["is_active_agenda"]),
            "approved_count": c_out["approved"],
            "approved_conditions_count": c_out["approved_conditions"],
            "denied_or_cor_count": c_out["denied_or_cor"],
            "deferred_count": c_out["deferred"],
            "addition_alteration_count": c_cat["addition_alteration"],
            "new_construction_count": c_cat["new_construction"],
            "demolition_relocation_count": c_cat["demolition_relocation"],
            "recent_examples": [
                {
                    "hcad_num": f["hcad_num"],
                    "address": f["address"],
                    "year_built": f["year_built"],
                    "meeting_date": f["meeting_date"],
                    "app_type": f["app_type"],
                    "app_category": f["app_category"],
                    "outcome": f["outcome"],
                    "outcome_detail": f["outcome_detail"],
                    "is_active_agenda": f["is_active_agenda"],
                    "pdf_url": f["staff_report_url"] or f["agenda_or_minutes_url"],
                    "lng": f["lng"],
                    "lat": f["lat"],
                }
                for f in hlist[:10]
            ],
        }

    cat_counts = Counter(f["app_category"] for f in geocoded_filings)
    out_counts = Counter(f["outcome"] for f in geocoded_filings)
    tracker_payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_url": HAHC_PAGE_URL,
        "current_cycle": {
            "meeting_date": current_cycle_date,
            "agenda_pdf_url": current_agenda_url,
            "active_items_count": sum(1 for f in geocoded_filings if f["is_active_agenda"]),
            "guideline_updates": guideline_updates,
        },
        "summary": {
            "meetings_parsed": len(by_date_pdf) + (1 if current_cycle_date else 0),
            "total_filings": len(geocoded_filings),
            "unique_parcels": len(features),
            "active_agenda_parcels": sum(1 for feat in features if feat["properties"]["has_active_agenda"]),
            "demolition_relocation_parcels": sum(1 for feat in features if feat["properties"]["has_demolition_relocation"]),
            "designation_parcels": sum(1 for feat in features if feat["properties"]["has_designation"]),
            "addition_alteration_filings": cat_counts["addition_alteration"],
            "new_construction_filings": cat_counts["new_construction"],
            "demolition_relocation_filings": cat_counts["demolition_relocation"],
            "designation_filings": cat_counts["designation"],
            "approved_filings": out_counts["approved"],
            "approved_conditions_filings": out_counts["approved_conditions"],
            "denied_or_cor_filings": out_counts["denied_or_cor"],
            "deferred_filings": out_counts["deferred"],
        },
        "district_precedents": district_precedents,
        "filings": geocoded_filings,
    }

    (DATA_DIR / "hahc_coa_tracker.json").write_text(
        json.dumps(tracker_payload, indent=2)
    )

    overlays["hahc_coa_filings"] = {
        "type": "FeatureCollection",
        "features": features,
    }
    (DATA_DIR / "overlays.json").write_text(
        json.dumps(overlays, separators=(",", ":"))
    )

    # Update search_index.json with HAHC COA parcel entries (replacing any prior hahc_coa entries)
    filtered_si = [row for row in search_index if row.get("kind") != "hahc_coa"]
    for feat in features:
        p = feat["properties"]
        lon, lat = feat["geometry"]["coordinates"]
        label_prefix = "HAHC Agenda" if p["has_active_agenda"] else "HAHC COA"
        dist_tag = p["historic_district"] or p["landmark_name"] or "Houston"
        filtered_si.append(
            {
                "kind": "hahc_coa",
                "id": p["id"],
                "hcad_num": p["hcad_num"],
                "name": f"{label_prefix}: {p['address']} ({p['latest_app_type']})",
                "address": p["address"],
                "district": dist_tag,
                "year_built": p["year_built"],
                "lon": lon,
                "lat": lat,
                "keywords": f"HAHC COA Certificate of Appropriateness {p['latest_app_type']} {dist_tag} {p['latest_outcome']}",
            }
        )
    (DATA_DIR / "search_index.json").write_text(
        json.dumps(filtered_si, separators=(",", ":"))
    )

    print("Saved hahc_coa_tracker.json, overlays.json['hahc_coa_filings'], and search_index.json:")
    print(json.dumps(tracker_payload["summary"], indent=2))


if __name__ == "__main__":
    main()
