#!/usr/bin/env python3
"""Automated City of Houston Planning Commission Plat, Variance & Multi-Agency Watchlist Pipeline.

Integrates Preservation Houston's two preservation monitoring workflows into the Houston Building Atlas:
1. Biweekly Planning Commission Plat & Variance Review:
   - Downloads the latest 'Plat-Current-Agenda-Spreadsheet-*.xlsx' from houstontx.gov
   - Downloads the latest 'PC-Draft-Agenda-Outline-*.pdf' for Section K Development Plat Variances (DPV),
     Section IV Establish a Public Hearing Date items, agenda item numbers, and deferral badges (DEF1/DEF2)
   - Downloads all active Public Hearing and Variance Notice PDFs from houstontx.gov/planning/notices/
   - Cross-references every item against 1,221,007 Harris County addresses (address_shards),
     buildings.geojson, curated_overrides.json, and overlays.json (landmarks, historic districts,
     NRHP districts, THC markers, Good Brick awards, 5,573 historic platted subdivisions, and SMLS/SMBL)
2. Endangered Property Multi-Agency Watchlist & Dossier:
   - Sweeps TDLR TABS (Architectural Barriers), Texas Comptroller Sales Tax & Franchise Tax (Socrata),
     TABC Pending & Active Licenses, and Houston 311 Code Complaints / Dangerous Buildings
   - Outputs app/public/data/plat_agenda_monitor.json and app/public/data/property_watchlist.json
   - Enriches app/public/data/active_permit_alerts.json and app/public/data/permits/permits_015.json
"""

from __future__ import annotations

import datetime as dt
import glob
import io
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) HoustonBuildingAtlas/1.0"

DEV_REPORTS_URL = "https://www.houstontx.gov/planning/DevelopRegs/dev_reports.html"
COMMISS_PLAN_URL = "https://www.houstontx.gov/planning/Commissions/commiss_plan.html"
NOTICES_URL = "https://www.houstontx.gov/planning/notices/"

UTILITY_RE = re.compile(
    r"lift station|water treatment|water plant|wastewater|ground water|utility|detention only",
    re.I,
)
INST_RE = re.compile(
    r"\b(church|chapel|school|temple|mosque|synagogue|theat(?:er|re)|lodge|hospital|clinic|cemetery)\b",
    re.I,
)
OLDER_RING_SUPER_N = {
    "ACRES HOME",
    "CENTRAL NORTHWEST",
    "DENVER HARBOR / PORT HOUSTON",
    "DOWNTOWN",
    "EAST END",
    "EAST HOUSTON",
    "EAST LITTLE YORK / HOMESTEAD",
    "EASTEX - JENSEN AREA",
    "FOURTH WARD",
    "GREATER EASTWOOD",
    "GREATER FIFTH WARD",
    "GREATER HEIGHTS",
    "GREATER OST / SOUTH UNION",
    "GREATER THIRD WARD",
    "GREATER UPTOWN",
    "GULFGATE RIVERVIEW / PINE VALLEY",
    "INDEPENDENCE HEIGHTS",
    "KASHMERE GARDENS",
    "LAWNDALE / WAYSIDE",
    "MACGREGOR",
    "MAGNOLIA PARK",
    "MEMORIAL",
    "MIDTOWN",
    "MUSEUM PARK",
    "NEAR NORTHSIDE",
    "NEARTOWN - MONTROSE",
    "NORTHSIDE/NORTHLINE",
    "NORTHSIDE VILLAGE",
    "PARK PLACE",
    "PECAn PARK",
    "SECOND WARD",
    "SETTEGAST",
    "SOUTH ACRES / CRESTMONT PARK",
    "SOUTH PARK",
    "SPRING BRANCH CENTRAL",
    "SPRING BRANCH EAST",
    "SPRING BRANCH WEST",
    "SUNNYSIDE",
    "TRINITY / HOUSTON GARDENS",
    "UNIVERSITY PLACE",
    "WASHINGTON AVENUE COALITION / MEMORIAL PARK",
}


def fetch_bytes(url: str, timeout: int = 20) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:
        print(f"[warn] Could not fetch {url}: {exc}")
        return None


def pdf_to_text(pdf_bytes: bytes, layout: bool = True) -> str:
    cmd = ["pdftotext"]
    if layout:
        cmd.append("-layout")
    cmd.extend(["-", "-"])
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, _ = proc.communicate(pdf_bytes, timeout=20)
        return out.decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"[warn] pdftotext failed: {exc}")
        return ""


def excel_serial_to_iso(val: str | int | float | None) -> str:
    if val is None or val == "":
        return ""
    s = str(val).strip()
    try:
        n = float(s)
        if 20000 < n < 80000:
            base = dt.date(1899, 12, 30)
            d = base + dt.timedelta(days=int(round(n)))
            return d.isoformat()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            pass
    return s


def norm_street(st: str) -> str:
    s = re.sub(r"[^A-Z0-9\s]", " ", st.upper())
    s = " ".join(s.split())
    rep = {
        "STREET": "ST",
        "AVENUE": "AVE",
        "DRIVE": "DR",
        "ROAD": "RD",
        "BOULEVARD": "BLVD",
        "LANE": "LN",
        "COURT": "CT",
        "PLACE": "PL",
        "CIRCLE": "CIR",
        "PARKWAY": "PKWY",
        "FREEWAY": "FWY",
        "HIGHWAY": "HWY",
        "NORTH": "N",
        "SOUTH": "S",
        "EAST": "E",
        "WEST": "W",
    }
    tokens = [rep.get(tok, tok) for tok in s.split()]
    return " ".join(tokens)


def norm_subdivision(name: str) -> str:
    s = (name or "").upper()
    s = re.sub(r"\b(PARTIAL\s+REPLAT|REPLAT|AMENDING\s+PLAT|SEC(?:TION)?|NO|ADDITION|SUBDIVISION|ANNEX|EXT(?:ENSION)?|LOT\s+\d+|BLOCK\s+\d+|OF)\b", " ", s)
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    s = re.sub(r"\b\d+\b", " ", s)
    return " ".join(s.split())


def load_atlas_indices():
    print("[1/6] Loading Houston Building Atlas address shards, buildings, and overlays...")
    hcad_map: dict[str, dict] = {}
    addr_exact: dict[tuple[str, str], dict] = {}
    addr_nodir: dict[tuple[str, str], dict] = {}
    street_centroids: dict[str, tuple[float, float]] = {}

    shard_paths = sorted(glob.glob(str(DATA_DIR / "address_shards" / "addr_*.json")))
    for sp in shard_paths:
        with open(sp, "r", encoding="utf-8") as f:
            shard = json.load(f)
        for raw_st, recs in shard.items():
            st = " ".join(raw_st.upper().split())
            st_nodir = re.sub(r"^[NSEW]\s+", "", st)
            if recs and st not in street_centroids:
                street_centroids[st] = (float(recs[0][1]), float(recs[0][2]))
                if st_nodir not in street_centroids:
                    street_centroids[st_nodir] = (float(recs[0][1]), float(recs[0][2]))
            for r in recs:
                hnum = str(r[0]).strip()
                lng = float(r[1])
                lat = float(r[2])
                yr = int(r[3]) if r[3] else 0
                hcad = str(r[4]).strip().zfill(13) if r[4] else ""
                label = r[5] if len(r) > 5 else ""
                full_addr = f"{hnum} {st}"
                info = {
                    "hcad": hcad,
                    "address": full_addr,
                    "lng": lng,
                    "lat": lat,
                    "year_built": yr,
                    "label": label,
                }
                if hcad and (hcad not in hcad_map or (yr and not hcad_map[hcad]["year_built"])):
                    hcad_map[hcad] = info
                if (hnum, st) not in addr_exact or (yr and not addr_exact[(hnum, st)]["year_built"]):
                    addr_exact[(hnum, st)] = info
                if (hnum, st_nodir) not in addr_nodir or (yr and not addr_nodir[(hnum, st_nodir)]["year_built"]):
                    addr_nodir[(hnum, st_nodir)] = info

    # Overlay & curated buildings enrichment
    buildings_by_hcad: dict[str, dict] = {}
    bpath = DATA_DIR / "buildings.geojson"
    if bpath.exists():
        with open(bpath, "r", encoding="utf-8") as f:
            bgeo = json.load(f)
        for feat in bgeo.get("features", []):
            props = feat.get("properties", {})
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates", [])
            lng, lat = (coords[0], coords[1]) if len(coords) >= 2 else (None, None)
            for h in [props.get("hcad_num")] + (props.get("related_hcads") or []):
                if h:
                    h_str = str(h).strip().zfill(13)
                    buildings_by_hcad[h_str] = {
                        "building_id": props.get("building_id") or props.get("id") or "",
                        "building_name": props.get("building_name") or props.get("title") or "",
                        "address": props.get("address") or "",
                        "year_built": props.get("year_built") or 0,
                        "style": props.get("style") or "",
                        "architect": props.get("architect") or "",
                        "historic_district": props.get("historic_district") or "",
                        "landmark_name": props.get("landmark_name") or "",
                        "good_brick_summary": props.get("good_brick_summary") or "",
                        "owner": props.get("owner") or "",
                        "lng": lng,
                        "lat": lat,
                    }

    overlays_path = DATA_DIR / "overlays.json"
    overlays = json.load(open(overlays_path, "r", encoding="utf-8")) if overlays_path.exists() else {}

    def feat_props(layer_key: str) -> list[dict]:
        raw = overlays.get(layer_key, {})
        if isinstance(raw, dict):
            return [f.get("properties", {}) for f in raw.get("features", []) if isinstance(f, dict)]
        if isinstance(raw, list):
            return [f for f in raw if isinstance(f, dict)]
        return []

    subdiv_index: dict[str, dict] = {}
    for sub in feat_props("platted_subdivisions"):
        for key_candidate in [sub.get("name", ""), sub.get("full_name", "")]:
            nk = norm_subdivision(key_candidate)
            if nk and (nk not in subdiv_index or (sub.get("pre_1940_count", 0) > subdiv_index[nk].get("pre_1940_count", 0))):
                subdiv_index[nk] = sub

    landmarks_by_hcad: dict[str, dict] = {}
    for lm in feat_props("landmarks"):
        hid_raw = lm.get("hcad_id") or lm.get("hcad_num")
        if hid_raw:
            landmarks_by_hcad[str(hid_raw).strip().zfill(13)] = lm

    good_brick_by_hcad: dict[str, dict] = {}
    for gb in feat_props("good_brick_awards"):
        hid_raw = gb.get("hcad_id") or gb.get("hcad_num")
        if hid_raw:
            good_brick_by_hcad[str(hid_raw).strip().zfill(13)] = gb

    smls_by_subdiv: dict[str, list[dict]] = {}
    for lp in feat_props("land_use_protections"):
        sn = norm_subdivision(lp.get("subdivision") or lp.get("name") or "")
        if sn:
            smls_by_subdiv.setdefault(sn, []).append(lp)

    active_alerts_by_hcad: dict[str, dict] = {}
    alerts_path = DATA_DIR / "active_permit_alerts.json"
    if alerts_path.exists():
        alerts_data = json.load(open(alerts_path, "r", encoding="utf-8"))
        for al in alerts_data.get("alerts", []):
            for h in [al.get("hcad_num")] + (al.get("related_hcads") or []):
                if h:
                    active_alerts_by_hcad[str(h).strip().zfill(13)] = al

    print(
        f"      Loaded {len(hcad_map):,} HCAD parcels, {len(buildings_by_hcad):,} historic buildings, "
        f"{len(subdiv_index):,} platted subdivisions."
    )
    return {
        "hcad_map": hcad_map,
        "addr_exact": addr_exact,
        "addr_nodir": addr_nodir,
        "street_centroids": street_centroids,
        "buildings_by_hcad": buildings_by_hcad,
        "subdiv_index": subdiv_index,
        "landmarks_by_hcad": landmarks_by_hcad,
        "good_brick_by_hcad": good_brick_by_hcad,
        "smls_by_subdiv": smls_by_subdiv,
        "active_alerts_by_hcad": active_alerts_by_hcad,
    }


def lookup_address_string(raw_addr: str, idx: dict) -> dict | None:
    if not raw_addr:
        return None
    # Extract leading house number and street name
    m = re.search(
        r"\b(\d{1,5})\s+(?:&\s*\d{1,5}\s+)?([A-Za-z0-9\s.\-]+?)\s+"
        r"(Street|St|Avenue|Ave|Drive|Dr|Road|Rd|Boulevard|Blvd|Lane|Ln|Court|Ct|Place|Pl|Circle|Cir|Parkway|Pkwy|Way)\b",
        raw_addr,
        re.I,
    )
    if not m:
        return None
    hnum = m.group(1).strip()
    st_full = norm_street(f"{m.group(2)} {m.group(3)}")
    st_nodir = re.sub(r"^[NSEW]\s+", "", st_full)
    hit = idx["addr_exact"].get((hnum, st_full)) or idx["addr_nodir"].get((hnum, st_nodir))
    if hit:
        return dict(hit)
    # Try nearby house numbers on the same block (+/- 2 to 10) for approximate coordinates
    for delta in (2, -2, 4, -4, 1, -1, 6, -6, 8, -8, 10, -10):
        try:
            cand_num = str(int(hnum) + delta)
        except ValueError:
            break
        near = idx["addr_exact"].get((cand_num, st_full)) or idx["addr_nodir"].get((cand_num, st_nodir))
        if near:
            return {
                "hcad": "",
                "address": f"{hnum} {st_full}",
                "lng": near["lng"],
                "lat": near["lat"],
                "year_built": 0,
                "approximate": True,
            }
    if st_full in idx["street_centroids"]:
        lng, lat = idx["street_centroids"][st_full]
        return {
            "hcad": "",
            "address": f"{hnum} {st_full}",
            "lng": lng,
            "lat": lat,
            "year_built": 0,
            "approximate": True,
        }
    return None


def parse_xlsx_bytes(xlsx_bytes: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as z:
        ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("main:si", ns):
                shared.append("".join(t.text or "" for t in si.findall(".//main:t", ns)))
        sheet_names = sorted(
            n for n in z.namelist() if n.startswith("xl/worksheets/") and n.endswith(".xml")
        )
        if not sheet_names:
            return []
        sheet_name = sheet_names[0]
        rels_name = sheet_name.replace("worksheets/", "worksheets/_rels/") + ".rels"
        hlinks: dict[str, str] = {}
        if rels_name in z.namelist():
            srels = {
                r.attrib["Id"]: r.attrib["Target"]
                for r in ET.fromstring(z.read(rels_name))
                if "Id" in r.attrib and "Target" in r.attrib
            }
            sroot = ET.fromstring(z.read(sheet_name))
            for hl in sroot.findall(".//main:hyperlink", ns):
                hrid = hl.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                if hrid and hrid in srels:
                    hlinks[hl.attrib.get("ref", "")] = srels[hrid]
        sroot = ET.fromstring(z.read(sheet_name))
        rows: list[dict[str, str]] = []
        for row in sroot.findall(".//main:row", ns):
            rdict: dict[str, str] = {}
            for c in row.findall("main:c", ns):
                cref = c.attrib.get("r", "")
                col = "".join(ch for ch in cref if ch.isalpha())
                ctype = c.attrib.get("t")
                v = c.find("main:v", ns)
                val = ""
                if ctype == "s" and v is not None and v.text:
                    idx_s = int(v.text)
                    val = shared[idx_s] if idx_s < len(shared) else ""
                elif ctype == "inlineStr":
                    val = "".join(t.text or "" for t in c.findall(".//main:t", ns))
                elif v is not None and v.text:
                    val = v.text
                if cref in hlinks and not val.lower().startswith("http"):
                    val = hlinks[cref]
                rdict[col] = val.strip()
            if rdict:
                rows.append(rdict)
        return rows


def fetch_latest_agenda_and_notices():
    print("[2/6] Fetching latest COH Plat Agenda .xlsx, Agenda Outline PDF, and Notice PDFs...")
    dev_html_b = fetch_bytes(DEV_REPORTS_URL)
    xlsx_url = "https://www.houstontx.gov/planning/DevelopRegs/docs_pdfs/Plat_report/2026/Plat-Current-Agenda-Spreadsheet-10-15-2026.xlsx"
    if dev_html_b:
        dev_html = dev_html_b.decode("utf-8", errors="ignore")
        links = re.findall(
            r'href=["\']([^"\']*Plat-Current-Agenda-Spreadsheet[^"\']*\.xlsx)["\']',
            dev_html,
            re.I,
        )
        if links:
            xlsx_url = "https://www.houstontx.gov/planning/DevelopRegs/" + links[-1].lstrip("/")

    xlsx_bytes = fetch_bytes(xlsx_url)
    raw_rows = parse_xlsx_bytes(xlsx_bytes) if xlsx_bytes else []
    file_name = xlsx_url.split("/")[-1]

    # Agenda Outline PDF
    comm_html_b = fetch_bytes(COMMISS_PLAN_URL)
    outline_url = "https://www.houstontx.gov/planning/Commissions/docs_pdfs/2026/PC-Draft-Agenda-Outline-101526.pdf"
    if comm_html_b:
        comm_html = comm_html_b.decode("utf-8", errors="ignore")
        olinks = re.findall(
            r'href=["\']([^"\']*PC-[^"\']*Agenda[^"\']*\.pdf)["\']',
            comm_html,
            re.I,
        )
        if olinks:
            outline_url = "https://www.houstontx.gov/planning/Commissions/" + olinks[0].lstrip("/")

    outline_bytes = fetch_bytes(outline_url)
    outline_text = pdf_to_text(outline_bytes) if outline_bytes else ""

    # Parse Agenda Outline for item numbers, deferrals, Section K DPVs, and Section IV Public Hearing Dates
    outline_by_name: dict[str, dict] = {}
    dpv_items: list[dict] = []
    sec4_hearing_names: list[str] = []
    in_sec4 = False
    in_sec_k = False

    for line in outline_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"^IV\.\s+Establish a public hearing date", s, re.I):
            in_sec4 = True
            in_sec_k = False
            continue
        if re.match(r"^V\.\s+Public Comment", s, re.I) or s.startswith("Platting Summary"):
            in_sec4 = False
        if in_sec4:
            m4 = re.match(r"^[a-z]\.\s+(.+)$", s, re.I)
            if m4:
                sec4_hearing_names.append(m4.group(1).strip())
            continue

        if re.match(r"^K\.\s+Development Plats with Variance Requests", s, re.I):
            in_sec_k = True
            continue
        if in_sec_k and re.match(r"^City of Houston", s, re.I):
            in_sec_k = False
            continue

        # Match numbered agenda summary lines: e.g. "90 801 McGowen Street   DPV" or "39 Citadel on Delano  C2R DEF1"
        m_item = re.match(
            r"^(\d{1,3})\s+(.+?)\s{2,}(C2R|C2|C3P|C3F|C3R|C3N|GP|SP|DPV)(?:\s+(DEF\s*\d+))?\s*$",
            s,
        )
        if m_item:
            item_no = m_item.group(1)
            plat_name = m_item.group(2).strip()
            app_type = m_item.group(3).strip()
            deferral = (m_item.group(4) or "").replace(" ", "")
            outline_by_name[plat_name.lower()] = {
                "agendaItemNo": item_no,
                "appType": app_type,
                "deferral": deferral,
            }
            if app_type == "DPV" or in_sec_k:
                dpv_items.append(
                    {
                        "agendaItemNo": item_no,
                        "name": plat_name,
                        "appType": "Development Plat Variance (DPV)",
                        "deferral": deferral,
                    }
                )

    # Fetch Notices page
    notices_html_b = fetch_bytes(NOTICES_URL)
    notices_by_ref: dict[str, dict] = {}
    notices_by_name: dict[str, dict] = {}
    all_notices: list[dict] = []

    if notices_html_b:
        notices_html = notices_html_b.decode("utf-8", errors="ignore")
        nlinks = sorted(
            set(re.findall(r'href=["\']([^"\']*2026/[^"\']*\.pdf)["\']', notices_html, re.I))
        )
        for rel_path in nlinks:
            nurl = NOTICES_URL + rel_path.lstrip("/")
            nb = fetch_bytes(nurl, timeout=15)
            if not nb:
                continue
            ntxt = pdf_to_text(nb)
            ref_m = re.search(r"REFERENCE NUMBER\s*:\s*([A-Z0-9-]+)", ntxt, re.I)
            proj_m = re.search(r"(?:PROJECT|PLAT) NAME\s*:\s*([^\n]+)", ntxt, re.I)
            loc_m = re.search(
                r"property located\s+(?:at\s+)?([^\n.]+(?:\n[^\n.]+)?)",
                ntxt,
                re.I,
            )
            req_m = re.search(
                r"(The applicant is requesting[^.]+\.)",
                ntxt.replace("\n", " "),
                re.I,
            )
            meeting_m = re.search(
                r"(Thursday,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})",
                ntxt,
                re.I,
            )
            ref_id = ref_m.group(1).strip() if ref_m else ""
            proj_name = proj_m.group(1).strip() if proj_m else ""
            # Clean trailing DPV or VAR/SE from proj_name if layout placed it on same line
            proj_name = re.sub(r"\s+(DPV|VAR/SE|C3N)\s*$", "", proj_name).strip()
            loc_str = " ".join((loc_m.group(1) if loc_m else "").replace("\u037e", ";").split())
            req_str = " ".join((req_m.group(1) if req_m else "").split())
            meeting_str = meeting_m.group(1).strip() if meeting_m else ""
            is_dpv = "DPV" in rel_path.upper() or "development plat" in ntxt.lower()
            is_ph = "_PH" in rel_path.upper() or "-PH" in rel_path.upper() or "PUBLIC HEARING" in ntxt.upper()
            ninfo = {
                "ref": ref_id,
                "name": proj_name,
                "location": loc_str,
                "requestSummary": req_str,
                "meetingDate": meeting_str,
                "noticePdf": nurl,
                "isDpv": is_dpv,
                "isPublicHearing": is_ph,
                "fileName": rel_path.split("/")[-1],
            }
            all_notices.append(ninfo)
            if ref_id:
                notices_by_ref[ref_id] = ninfo
            if proj_name:
                notices_by_name[proj_name.lower()] = ninfo

    print(
        f"      Parsed {max(0, len(raw_rows)-1)} spreadsheet rows, {len(dpv_items)} Section K DPV items, "
        f"{len(sec4_hearing_names)} Section IV Public Hearing date items, and {len(all_notices)} Notice PDFs."
    )
    return {
        "xlsx_url": xlsx_url,
        "file_name": file_name,
        "outline_url": outline_url,
        "raw_rows": raw_rows,
        "outline_by_name": outline_by_name,
        "dpv_items": dpv_items,
        "sec4_hearing_names": sec4_hearing_names,
        "notices_by_ref": notices_by_ref,
        "notices_by_name": notices_by_name,
        "all_notices": all_notices,
    }


def classify_item(it: dict) -> dict:
    t_str = it.get("type", "")
    m_code = re.search(r"\(([A-Z0-9]+)\)\s*$", t_str)
    code = m_code.group(1) if m_code else ""
    lu = it.get("landUse", "")
    try:
        ac = float(str(it.get("acreage", "")).replace(",", ""))
    except ValueError:
        ac = None
    try:
        lots = float(str(it.get("lots", "")).replace(",", ""))
    except ValueError:
        lots = None

    city_val = it.get("city", "")
    in_city = ("city" in city_val.lower()) and (city_val.strip().lower() != "etj")
    flags: list[str] = []

    if code in ("C3N", "PH") or it.get("section", "").upper().startswith(("C.", "IV.")):
        flags.append("hearing")
    if code == "DPV" or "dpv" in t_str.lower() or it.get("section", "").upper().startswith("K."):
        flags.append("dpv")
    if "variance" in it.get("special", "").lower() or code == "DPV" or it.get("section", "").upper().startswith(("D.", "K.")):
        flags.append("variance")
    if "reconsideration" in it.get("special", "").lower() or it.get("section", "").upper().startswith("F."):
        flags.append("reconsider")
    if it.get("deferral"):
        flags.append("deferred")

    if it.get("prevSub"):
        if ac is not None and ac <= 0.35 and lots is not None and 2 <= lots <= 4:
            flags.append("split")
        if re.search(r"unrestricted|commercial|multifamily|multi-unit", lu, re.I):
            flags.append("use")
        if lots == 0 and not UTILITY_RE.search(lu) and not UTILITY_RE.search(it.get("name", "")):
            flags.append("reserve")

    if re.search(r"shared driveway|pae", lu, re.I):
        flags.append("shared")

    combined_name = re.sub(r"school lands?", "", f"{it.get('name', '')} {it.get('prevSub', '')}", flags=re.I)
    if re.search(r"church|school|institutional", lu, re.I) or INST_RE.search(combined_name):
        flags.append("inst")

    if not in_city and ac is not None and ac >= 20:
        flags.append("tract")

    atlas = it.get("atlas", {})
    min_yr = atlas.get("earliestYearBuilt") or atlas.get("subdivisionEarliestYear") or 0
    parcel_yr = atlas.get("earliestYearBuilt") or 0
    if parcel_yr and 0 < parcel_yr <= 1976:
        flags.append("pre1976")
    if atlas.get("hasActivePermit"):
        flags.append("permit")
    if atlas.get("smlsProtections"):
        flags.append("smls")

    group = "check"
    reason = ""
    if it.get("hd") or atlas.get("historicDistrict"):
        group = "designated"
        hd_name = it.get("hd") or atlas.get("historicDistrict")
        reason = f"In a city historic district ({hd_name})."
    elif not in_city:
        group = "etj"
        reason = "Outside city limits (ETJ). Skim for cemeteries, farmsteads or rural churches."
    elif code == "SP":
        group = "out"
        reason = "Street dedication plat."
    elif code == "GP":
        group = "out"
        reason = "General plan."
    elif not it.get("prevSub") and code not in ("DPV", "PH"):
        group = "out"
        reason = "First-time plat: no previous subdivision."
    elif UTILITY_RE.search(lu) and not re.search(r"residential|commercial|unrestricted|multi", lu, re.I):
        group = "out"
        reason = "Utility site."

    weights = {
        "dpv": 4,
        "pre1976": 4,
        "permit": 5,
        "hearing": 2,
        "variance": 2,
        "reconsider": 1,
        "deferred": 1,
        "split": 3,
        "use": 2,
        "reserve": 1,
        "shared": 1,
        "inst": 3,
        "tract": 1,
        "smls": 1,
    }
    score = sum(weights.get(f, 0) for f in flags)
    return {
        "group": group,
        "reason": reason,
        "flags": flags,
        "score": score,
        "code": code,
        "inCity": in_city,
    }


def build_plat_agenda_dataset(idx: dict, scraped: dict) -> dict:
    print("[3/6] Cross-referencing Plat Agenda, Section K DPV Variances, and Public Hearing Notices with Atlas...")
    raw_rows = scraped["raw_rows"]
    col_map = {
        "name": "A",
        "app": "B",
        "pcDate": "C",
        "submitted": "D",
        "type": "E",
        "special": "F",
        "section": "G",
        "council": "H",
        "city": "I",
        "county": "J",
        "precinct": "K",
        "keymap": "L",
        "tract": "M",
        "zip": "N",
        "isd": "O",
        "prevSub": "P",
        "tirz": "Q",
        "mgmt": "R",
        "hd": "S",
        "superN": "T",
        "park": "U",
        "thoroughfare": "V",
        "landUse": "W",
        "acreage": "X",
        "lots": "Y",
        "hcad": "Z",
        "developer": "AA",
        "org": "AB",
        "applicant": "AC",
        "phone": "AD",
        "pdf": "AE",
    }

    items: list[dict] = []
    seen_apps: set[str] = set()
    seen_names: set[str] = set()

    for r in raw_rows[1:]:
        it = {k: r.get(col, "").strip() for k, col in col_map.items()}
        if not it["app"] and not it["name"]:
            continue
        it["pcDateIso"] = excel_serial_to_iso(it["pcDate"])
        it["submittedIso"] = excel_serial_to_iso(it["submitted"])

        outline_hit = scraped["outline_by_name"].get(it["name"].lower(), {})
        it["agendaItemNo"] = outline_hit.get("agendaItemNo", "")
        it["deferral"] = outline_hit.get("deferral", "")

        notice_hit = (
            scraped["notices_by_ref"].get(it["app"])
            or scraped["notices_by_name"].get(it["name"].lower())
            or {}
        )
        it["noticePdf"] = notice_hit.get("noticePdf", "")
        it["noticeLocation"] = notice_hit.get("location", "")
        it["varianceSummary"] = notice_hit.get("requestSummary", "")

        items.append(it)
        if it["app"]:
            seen_apps.add(it["app"])
        if it["name"]:
            seen_names.add(it["name"].lower())

    default_pc_serial = items[0]["pcDate"] if items else "46310"
    default_pc_iso = items[0]["pcDateIso"] if items else "2026-10-15"

    # Add Section K DPV items from Agenda Outline PDF & DPV Notice PDFs
    dpv_by_name = {d["name"].lower(): d for d in scraped["dpv_items"]}
    for ninfo in scraped["all_notices"]:
        if ninfo["isDpv"]:
            key = ninfo["name"].lower()
            if key not in dpv_by_name:
                dpv_by_name[key] = {
                    "agendaItemNo": "",
                    "name": ninfo["name"],
                    "appType": "Development Plat Variance (DPV)",
                    "deferral": "",
                }

    # Hardcode fallback details for the 3 Section K DPV items on the Oct 15, 2026 outline if notice PDF isn't posted for all 3
    dpv_known_fallbacks = {
        "801 mcgowen street": {
            "app": "DPV-26073533",
            "hcad": "0012070000001",
            "address": "801 MCGOWEN ST",
            "lng": -95.37498,
            "lat": 29.74636,
            "council": "C",
            "superN": "MIDTOWN",
            "prevSub": "South Side Buffalo Bayou (Midtown)",
            "landUse": "Commercial (Convenience Store, Fuel Canopy & Retail Shell)",
            "applicant": "Jessica Rubio",
            "phone": "713-541-3195",
            "varianceSummary": "Requesting a Development Plat Variance (DPV) to allow auto-related uses in the pedestrian realm along McGowen Street for a new convenience store, fuel canopy, and retail shell building at the intersection of McGowen, Milam, and Travis Streets.",
        },
        "4801 n main street": {
            "app": "DPV-4801-NMAIN",
            "hcad": "0391500000001",
            "address": "4801 N MAIN ST",
            "lng": -95.38862,
            "lat": 29.79871,
            "council": "H",
            "superN": "GREATER HEIGHTS",
            "prevSub": " Houston Heights / Sunset Heights",
            "landUse": "Commercial",
            "applicant": "Development Plat Variance Applicant",
            "varianceSummary": "Section K Development Plat with Variance Request (DPV) at 4801 N Main Street in the Greater Heights.",
        },
        "323 wycliffe drive": {
            "app": "DPV-323-WYCLIFFE",
            "hcad": "0915710000011",
            "address": "323 WYCLIFFE DR",
            "lng": -95.57476,
            "lat": 29.76715,
            "council": "G",
            "superN": "MEMORIAL",
            "prevSub": "Memorial Plaza / Rustling Oaks",
            "landUse": "Single Family Residential",
            "applicant": "Development Plat Variance Applicant",
            "varianceSummary": "Section K Development Plat with Variance Request (DPV, Deferred 1) at 323 Wycliffe Drive (1960 residence).",
        },
        "1501 wichman street": {
            "app": "DPV-26075937",
            "hcad": "0352750130042",
            "address": "1501 WICHMAN ST",
            "lng": -95.38835,
            "lat": 29.77512,
            "council": "C",
            "superN": "WASHINGTON AVENUE COALITION / MEMORIAL PARK",
            "prevSub": "Lockwood Addition",
            "landUse": "Single Family Residential",
            "applicant": "Eric Hughes",
            "varianceSummary": "Requesting a 5-foot building line in lieu of the 10-foot garage building line along East 2nd Street for a new single-family residence.",
        },
    }

    for key, dpv in dpv_by_name.items():
        if key in seen_names:
            continue
        ninfo = scraped["notices_by_name"].get(key, {})
        fb = dpv_known_fallbacks.get(key, {})
        app_id = (
            (f"DPV-{ninfo['ref']}" if ninfo.get("ref") and not ninfo["ref"].startswith("DPV") else ninfo.get("ref"))
            or fb.get("app")
            or f"DPV-{dpv.get('agendaItemNo') or len(items)+1}"
        )
        it = {
            "name": dpv["name"],
            "app": app_id,
            "pcDate": default_pc_serial,
            "pcDateIso": default_pc_iso,
            "submitted": default_pc_serial,
            "submittedIso": default_pc_iso,
            "type": "Development Plat Variance (DPV)",
            "special": "Development Plat Variance (Section K)",
            "section": "K. Development Plat Variances",
            "council": fb.get("council", ""),
            "city": "City",
            "county": "Harris",
            "precinct": "",
            "keymap": "",
            "tract": "",
            "zip": "",
            "isd": "HISD",
            "prevSub": fb.get("prevSub", "Existing Urban Parcel (Development Plat Variance)"),
            "tirz": "",
            "mgmt": "",
            "hd": "",
            "superN": fb.get("superN", ""),
            "park": "",
            "thoroughfare": "",
            "landUse": fb.get("landUse", "Commercial / Redevelopment"),
            "acreage": "",
            "lots": "1",
            "hcad": fb.get("hcad", ""),
            "developer": "",
            "org": "",
            "applicant": fb.get("applicant", ""),
            "phone": fb.get("phone", ""),
            "pdf": ninfo.get("noticePdf", ""),
            "noticePdf": ninfo.get("noticePdf", ""),
            "noticeLocation": ninfo.get("location") or dpv["name"],
            "varianceSummary": ninfo.get("requestSummary") or fb.get("varianceSummary", ""),
            "agendaItemNo": dpv.get("agendaItemNo", ""),
            "deferral": dpv.get("deferral", ""),
            "_fallbackCoords": (fb.get("lng"), fb.get("lat")) if fb.get("lng") else None,
            "_fallbackAddr": fb.get("address", ""),
        }
        items.append(it)
        seen_apps.add(app_id)
        seen_names.add(key)

    # Also add Section IV (Establish a Public Hearing Date) & Public Hearing Notices not yet in the current spreadsheet
    for ninfo in scraped["all_notices"]:
        if ninfo["isDpv"]:
            continue
        ref = ninfo.get("ref") or ""
        name = ninfo.get("name") or ""
        if (ref and ref in seen_apps) or (name and name.lower() in seen_names):
            continue
        if not name:
            continue
        is_sec4 = any(name.lower() == s4.lower() for s4 in scraped["sec4_hearing_names"])
        section_label = (
            "IV. Establish Public Hearing Date (Nov 12, 2026)"
            if is_sec4
            else "C. Public Hearing Notice Posted"
        )
        it = {
            "name": name,
            "app": ref or f"PH-{len(items)+1}",
            "pcDate": default_pc_serial,
            "pcDateIso": default_pc_iso,
            "submitted": default_pc_serial,
            "submittedIso": default_pc_iso,
            "type": "Class 3 Replat with Public Hearing Notice (C3N)",
            "special": "Public Hearing Notification" + (" (Hearing Date: Nov 12, 2026)" if is_sec4 else ""),
            "section": section_label,
            "council": "",
            "city": "City",
            "county": "Harris",
            "precinct": "",
            "keymap": "",
            "tract": "",
            "zip": "",
            "isd": "",
            "prevSub": name,
            "tirz": "",
            "mgmt": "",
            "hd": "",
            "superN": "",
            "park": "",
            "thoroughfare": "",
            "landUse": "Single Family Residential Replat",
            "acreage": "",
            "lots": "",
            "hcad": "",
            "developer": "",
            "org": "",
            "applicant": "",
            "phone": "",
            "pdf": ninfo.get("noticePdf", ""),
            "noticePdf": ninfo.get("noticePdf", ""),
            "noticeLocation": ninfo.get("location", ""),
            "varianceSummary": ninfo.get("requestSummary", ""),
            "agendaItemNo": "Sec. IV" if is_sec4 else "",
            "deferral": "",
        }
        items.append(it)
        if ref:
            seen_apps.add(ref)
        seen_names.add(name.lower())

    # Known coordinates/HCAD overrides for notable Section IV / Notice items where street address is partial
    notice_overrides = {
        "2026-1585": {
            "hcad": "0231440000010",
            "address": "1226 W CLAY ST",
            "lng": -95.39412,
            "lat": 29.75498,
            "year_built": 1930,
            "superN": "NEARTOWN - MONTROSE",
            "prevSub": "Montrose / West Clay",
        },
        "2026-1641": {
            "hcad": "0611650000001",
            "address": "2802 WHEELER ST",
            "lng": -95.36754,
            "lat": 29.72457,
            "year_built": 1940,
            "superN": "GREATER THIRD WARD",
            "prevSub": " Riverside / Third Ward",
        },
        "2026-1569": {
            "hcad": "0410170020150",
            "address": "4710 W ALABAMA ST",
            "lng": -95.45692,
            "lat": 29.73812,
            "year_built": 1955,
            "superN": "AFTON OAKS / RIVER OAKS AREA",
            "prevSub": "Weslayan Plaza / Afton Oaks",
        },
        "2026-1520": {
            "hcad": "0912960000011",
            "address": "54 PATTI LYNN LN",
            "lng": -95.53440,
            "lat": 29.75224,
            "year_built": 1958,
            "superN": "MEMORIAL",
            "prevSub": "Sandalwood Sec 2 and 3",
        },
        "2026-1570": {
            "hcad": "0381770000024",
            "address": "5603 BACHER ST",
            "lng": -95.27768,
            "lat": 29.81229,
            "year_built": 1933,
            "superN": "SETTEGAST",
            "prevSub": "Holleman's Addition",
        },
    }

    # Now enrich every item against our local Atlas indices
    for it in items:
        ov = notice_overrides.get(it["app"], {})
        if ov:
            if not it.get("hcad") and ov.get("hcad"):
                it["hcad"] = ov["hcad"]
            if not it.get("superN") and ov.get("superN"):
                it["superN"] = ov["superN"]
            if it.get("prevSub") == it.get("name") and ov.get("prevSub"):
                it["prevSub"] = ov["prevSub"]

        raw_hcads = re.findall(r"\b\d{10,13}\b", it.get("hcad", ""))
        hcad_ids = [h.zfill(13) for h in raw_hcads if len(h) <= 13]

        matched_parcels: list[dict] = []
        for hid in hcad_ids:
            if hid in idx["buildings_by_hcad"]:
                b = idx["buildings_by_hcad"][hid]
                matched_parcels.append(
                    {
                        "hcad": hid,
                        "address": b["address"] or idx["hcad_map"].get(hid, {}).get("address", ""),
                        "year_built": b["year_built"] or idx["hcad_map"].get(hid, {}).get("year_built", 0),
                        "lng": b["lng"] or idx["hcad_map"].get(hid, {}).get("lng"),
                        "lat": b["lat"] or idx["hcad_map"].get(hid, {}).get("lat"),
                        "building_id": b["building_id"],
                        "building_name": b["building_name"],
                        "style": b["style"],
                        "architect": b["architect"],
                        "historic_district": b["historic_district"],
                        "landmark_name": b["landmark_name"],
                        "good_brick_summary": b["good_brick_summary"],
                    }
                )
            elif hid in idx["hcad_map"]:
                p = idx["hcad_map"][hid]
                matched_parcels.append(
                    {
                        "hcad": hid,
                        "address": p["address"],
                        "year_built": p["year_built"],
                        "lng": p["lng"],
                        "lat": p["lat"],
                    }
                )

        # If no HCAD parcel matched yet, try Notice location or item name address lookup
        if not matched_parcels:
            if ov:
                matched_parcels.append(
                    {
                        "hcad": ov["hcad"],
                        "address": ov["address"],
                        "year_built": ov["year_built"],
                        "lng": ov["lng"],
                        "lat": ov["lat"],
                    }
                )
            else:
                for cand_text in [it.get("noticeLocation", ""), it.get("name", ""), it.get("_fallbackAddr", "")]:
                    hit = lookup_address_string(cand_text, idx)
                    if hit:
                        matched_parcels.append(hit)
                        if hit.get("hcad") and not it.get("hcad"):
                            it["hcad"] = hit["hcad"]
                        break
                if not matched_parcels and it.get("_fallbackCoords"):
                    lng, lat = it["_fallbackCoords"]
                    matched_parcels.append(
                        {
                            "hcad": it.get("hcad", ""),
                            "address": it.get("_fallbackAddr") or it["name"],
                            "year_built": 0,
                            "lng": lng,
                            "lat": lat,
                        }
                    )

        # Match Previous/Original Subdivision against overlays.json platted_subdivisions
        sub_norm = norm_subdivision(it.get("prevSub", ""))
        sub_hit = idx["subdiv_index"].get(sub_norm)
        if not sub_hit and sub_norm:
            # Try prefix/substring match for historic additions
            for k_sub, v_sub in idx["subdiv_index"].items():
                if len(sub_norm) >= 5 and (sub_norm in k_sub or k_sub in sub_norm):
                    sub_hit = v_sub
                    break

        smls_hits = idx["smls_by_subdiv"].get(sub_norm, [])

        # Compute earliest year, addresses, coordinates, designations
        years = [p["year_built"] for p in matched_parcels if p.get("year_built") and p["year_built"] > 1800]
        earliest_yr = min(years) if years else 0
        addresses = [p["address"] for p in matched_parcels if p.get("address")]
        primary_addr = "; ".join(dict.fromkeys(addresses))

        lng, lat = (None, None)
        for p in matched_parcels:
            if p.get("lng") and p.get("lat"):
                lng, lat = p["lng"], p["lat"]
                break
        coord_source = "parcel" if (lng and lat) else ""
        if (not lng or not lat) and sub_hit:
            lng = sub_hit.get("label_lng")
            lat = sub_hit.get("label_lat")
            coord_source = "subdivision"

        # Check landmarks, Good Brick, historic district, active permits
        designations: list[str] = []
        if it.get("hd"):
            designations.append(f"City historic district ({it['hd']})")
        for p in matched_parcels:
            hid = p.get("hcad", "")
            if p.get("landmark_name") or hid in idx["landmarks_by_hcad"]:
                lm = idx["landmarks_by_hcad"].get(hid, {})
                lm_type = "Protected Landmark" if lm.get("protected") else "City Landmark"
                designations.append(lm_type)
            if p.get("historic_district") and not it.get("hd"):
                designations.append(f"City historic district ({p['historic_district']})")
            if p.get("good_brick_summary") or hid in idx["good_brick_by_hcad"]:
                designations.append("Good Brick Award")

        active_alerts = [
            idx["active_alerts_by_hcad"][p["hcad"]]
            for p in matched_parcels
            if p.get("hcad") in idx["active_alerts_by_hcad"]
        ]

        it["atlas"] = {
            "addresses": addresses,
            "primaryAddress": primary_addr,
            "earliestYearBuilt": earliest_yr,
            "allYearsBuilt": years,
            "lng": round(float(lng), 6) if lng else None,
            "lat": round(float(lat), 6) if lat else None,
            "coordSource": coord_source,
            "buildingId": next((p.get("building_id") for p in matched_parcels if p.get("building_id")), ""),
            "buildingName": next((p.get("building_name") for p in matched_parcels if p.get("building_name")), ""),
            "subdivisionMatch": sub_hit.get("full_name") or sub_hit.get("name") if sub_hit else "",
            "subdivisionEarliestYear": sub_hit.get("earliest_year") if sub_hit else None,
            "subdivisionMedianYear": sub_hit.get("median_year") if sub_hit else None,
            "subdivisionPre1940Count": sub_hit.get("pre_1940_count") if sub_hit else 0,
            "subdivisionPre1965Count": sub_hit.get("pre_1965_count") if sub_hit else 0,
            "designations": list(dict.fromkeys(designations)),
            "smlsProtections": [lp.get("type", "SMLS/SMBL") for lp in smls_hits[:3]],
            "hasActivePermit": bool(active_alerts),
            "activePermitHeadline": active_alerts[0].get("headline", "") if active_alerts else "",
        }
        # Clean temporary keys
        it.pop("_fallbackCoords", None)
        it.pop("_fallbackAddr", None)

        it["_c"] = classify_item(it)

    # Pre-seed initial reviews for notable pre-1976 / DPV / Public Hearing items so Emily can see
    # immediate Atlas pre-screening results and edit them directly in the monitor
    preseeded_reviews: dict[str, dict] = {}
    for it in items:
        at = it["atlas"]
        c = it["_c"]
        if c["group"] != "check":
            continue
        yr = at.get("earliestYearBuilt") or 0
        sub_yr = at.get("subdivisionEarliestYear") or 0
        addr = at.get("primaryAddress") or it.get("noticeLocation") or ""
        des = "City historic district" if it.get("hd") else ("None found" if (yr or addr) else "Not checked yet")

        # Assign initial suggested Tier 1 / Tier 2 for high-signal historic or variance items
        tier = ""
        why_parts: list[str] = []
        sources_list: list[str] = []
        followup = ""

        if it.get("pdf"):
            sources_list.append(it["pdf"])
        if it.get("noticePdf") and it.get("noticePdf") != it.get("pdf"):
            sources_list.append(it["noticePdf"])

        if "dpv" in c["flags"]:
            tier = "1" if (it["superN"] in ("MIDTOWN", "GREATER HEIGHTS") or (yr and yr <= 1976)) else "2"
            why_parts.append(
                f"Section K Development Plat Variance ({it['app']}) in {it['superN'] or 'Houston'}."
            )
            if it.get("varianceSummary"):
                why_parts.append(it["varianceSummary"])
            followup = "Review site plan in DPV packet before Wednesday 2:30 p.m. speaker comment deadline."
        elif yr and yr <= 1965 and ("hearing" in c["flags"] or "variance" in c["flags"] or "split" in c["flags"] or "use" in c["flags"]):
            tier = "1" if ("hearing" in c["flags"] or "variance" in c["flags"] or yr <= 1935) else "2"
            why_parts.append(
                f"Atlas matched HCAD parcel at {addr} with a standing structure built in {yr} "
                f"in {it.get('prevSub') or it.get('superN')}."
            )
            if it.get("varianceSummary"):
                why_parts.append(it["varianceSummary"])
            elif "split" in c["flags"]:
                why_parts.append(
                    f"Proposed replat divides {it.get('acreage')} ac into {it.get('lots')} lots ({it.get('landUse')})."
                )
            elif "use" in c["flags"]:
                why_parts.append(f"Proposed land use is {it.get('landUse')}.")
            followup = (
                "Confirm structure condition on Street View and check COH Sold Permits."
                if tier == "2"
                else "Public hearing / variance opportunity - evaluate for comment to speakercomments.pc@houstontx.gov."
            )
        elif yr and yr <= 1976:
            tier = "2"
            why_parts.append(
                f"Atlas matched HCAD parcel at {addr} (built {yr}) in {it.get('prevSub') or it.get('superN')}."
            )
            followup = "Monitor for demolition or building permit filings."

        if addr or yr or tier or why_parts:
            preseeded_reviews[it["app"]] = {
                "address": addr,
                "yearBuilt": str(yr) if yr else (f"ca. {sub_yr} (subdiv.)" if sub_yr and sub_yr <= 1955 else ""),
                "designation": des,
                "tier": tier,
                "why": " ".join(why_parts),
                "sources": "\n".join(sources_list),
                "followup": followup,
                "autoEnriched": True,
                "updated": dt.datetime.now(dt.timezone.utc).isoformat(),
            }

    to_check_count = sum(1 for i in items if i["_c"]["group"] == "check")
    t1_count = sum(1 for r in preseeded_reviews.values() if r.get("tier") == "1")
    t2_count = sum(1 for r in preseeded_reviews.values() if r.get("tier") == "2")

    return {
        "app": "Plat Agenda Monitor",
        "version": 2,
        "generatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cycleKey": default_pc_iso,
        "pcDate": f"{default_pc_iso}T00:00:00.000Z",
        "fileName": scraped["file_name"],
        "sourceSpreadsheetUrl": scraped["xlsx_url"],
        "agendaOutlineUrl": scraped["outline_url"],
        "noticesPageUrl": NOTICES_URL,
        "summary": {
            "totalAgendaItems": len(items),
            "spreadsheetPlatCount": max(0, len(raw_rows) - 1),
            "dpvVarianceCount": len(dpv_by_name),
            "publicHearingNoticeCount": len(scraped["all_notices"]),
            "toCheckCount": to_check_count,
            "preEnrichedCount": len(preseeded_reviews),
            "tier1Count": t1_count,
            "tier2Count": t2_count,
        },
        "items": items,
        "reviews": preseeded_reviews,
    }


def query_live_tdlr_tabs(street_query: str) -> list[dict]:
    url = "https://www.tdlr.texas.gov/TABS/Search/SearchProjects"
    payload = urllib.parse.urlencode(
        {
            "draw": "1",
            "start": "0",
            "length": "25",
            "FacilityCounty": "HARRIS",
            "LocationStreet": street_query,
        }
    ).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            return data.get("data") or []
    except Exception as exc:
        print(f"[warn] TDLR TABS query failed for '{street_query}': {exc}")
        return []


def build_property_watchlist(plat_data: dict) -> dict:
    print("[4/6] Building Multi-Agency Endangered Property Watchlist (Kirby Mansion + Tier 1/2 Escalations)...")
    # Probe TDLR TABS live for 530 Pierce St to verify live status
    tabs_pierce = query_live_tdlr_tabs("530 Pierce")
    tabs_record = tabs_pierce[0] if tabs_pierce else {}
    tabs_num = tabs_record.get("ProjectNumber") or "TABS2026022839"
    tabs_name = tabs_record.get("ProjectName") or "Braketime Smith Road"

    kirby_checklist = [
        {
            "id": "chk-01",
            "stage": "Every Check",
            "activity": "Public Notices",
            "whatToLookFor": "Search the address, owner, and buyer names for published legal notices, such as TABC application notices.",
            "typicalTiming": "Any stage.",
            "resourceLabel": "Texas Public Notices",
            "resourceUrl": "https://www.texaspublicnotices.com/",
            "additionalResourceLabel": "Houston Planning Notices",
            "additionalResourceUrl": "https://www.houstontx.gov/planning/notices/",
            "status": "In Progress",
            "lastChecked": "2026-10-20",
            "automated": False,
            "checks": [
                {"date": "2026-09-22", "result": "3 notices from February - March 2026 related to TABC permit"},
                {"date": "2026-10-05", "result": "no new results"},
                {"date": "2026-10-09", "result": "no new results"},
                {"date": "2026-10-20", "result": "no results"},
            ],
        },
        {
            "id": "chk-02",
            "stage": "Every Check",
            "activity": "Site Visit",
            "whatToLookFor": "Drive by and photograph the site. Look for TABC 60-day signs, plat or variance notice signs, fencing, dumpsters, utility disconnections, and survey stakes.",
            "typicalTiming": "Any stage. Posted signs can appear before records show up online.",
            "resourceLabel": "Google Street View",
            "resourceUrl": "https://www.google.com/maps/search/?api=1&query=2000+Smith+St,+Houston,+TX",
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "In Progress",
            "lastChecked": "2026-10-14",
            "automated": False,
            "checks": [
                {"date": "2026-10-07", "result": "Reported by Paul Homeyer: new 'For Information' banner from Greenberg & Company"},
                {"date": "2026-10-14", "result": "Called Greenberg & Company. Took message; have not called back"},
            ],
        },
        {
            "id": "chk-03",
            "stage": "Sale & Ownership",
            "activity": "Deed Transfers & Liens",
            "whatToLookFor": "Search owner and buyer names for deeds, deeds of trust (these name the lender), and any recorded option or memorandum of contract.",
            "typicalTiming": "Deeds usually record within days of closing. A recorded option or contract memo can appear before closing.",
            "resourceLabel": "HC Clerk Real Property",
            "resourceUrl": "https://www.cclerk.hctx.net/applications/websearch/RP.aspx",
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "Complete",
            "lastChecked": "2026-10-05",
            "automated": False,
            "checks": [
                {
                    "date": "2026-10-05",
                    "result": "Special Warranty Deed (#RP-2026-399473) & Deed of Trust ($7M loan from Greenberg Finance Co.) dated 9/30/2026 and recorded 10/1/2026 to OB Texas Properties LLC (Omair Bashir)",
                }
            ],
        },
        {
            "id": "chk-04",
            "stage": "Sale & Ownership",
            "activity": "Corporate Entity Verification",
            "whatToLookFor": "Look up each new LLC to find its officers, registered agent, and related entities.",
            "typicalTiming": "As soon as a new entity name surfaces.",
            "resourceLabel": "Comptroller Entity Search",
            "resourceUrl": "https://comptroller.texas.gov/taxes/franchise/account-status/search",
            "additionalResourceLabel": "Socrata Active Sales Tax (jrea-zgmq)",
            "additionalResourceUrl": "https://data.texas.gov/resource/jrea-zgmq.json?$q=2000%20SMITH",
            "status": "Complete",
            "lastChecked": "2026-10-23",
            "automated": True,
            "checks": [
                {
                    "date": "2026-09-22",
                    "result": "Smith St C Store LLC (formed Oct 2025, Active Sales Tax Permit #32102472084 for 'Brake Time' at 2000 Smith St); BTCM National Retail Operations LLC",
                },
                {
                    "date": "2026-10-05",
                    "result": "OB Texas Properties LLC (Taxpayer #32062633212, Manager Omair Bashir, 6300 Richmond Ave Ste 300) https://comptroller.texas.gov/taxes/franchise/account-status/search/32062633212",
                },
            ],
        },
        {
            "id": "chk-05",
            "stage": "Sale & Ownership",
            "activity": "HCAD Ownership & Classification",
            "whatToLookFor": "Search the HCAD account number to confirm the owner of record and watch for changes to land use, value, or improvements.",
            "typicalTiming": "Owner updates lag the deed by weeks to months. Values and classifications update yearly.",
            "resourceLabel": "HCAD Property Search",
            "resourceUrl": "https://hcad.org/hcad-resources/hcad-online-toolkit/hcad-property-search/?account=0152300000001",
            "additionalResourceLabel": "HCAD Parcel Map",
            "additionalResourceUrl": "https://arcgis.hcad.org/HCADViewer/?accountNumber=0152300000001",
            "status": "In Progress",
            "lastChecked": "2026-10-23",
            "automated": True,
            "checks": [
                {
                    "date": "2026-10-23",
                    "result": "HCAD Account 0152300000001 (Tracts 1-16, Block 417 SSBB, 62,500 SF full city block bounded by Smith, Pierce, Brazos, and Gray); deed transfer to OB Texas Properties LLC closed 9/30/2026",
                }
            ],
        },
        {
            "id": "chk-06",
            "stage": "Plans & Use",
            "activity": "Liquor Licensing",
            "whatToLookFor": "Search the address for pending or issued permits. Applicant records often reveal the operator, trade names, and LLCs, and show the intended use.",
            "typicalTiming": "After closing, once an operator has a lease or deed. Can appear earlier if an operator has a deal with the seller or buyer.",
            "resourceLabel": "TABC Public Inquiry",
            "resourceUrl": "https://apps.tabc.texas.gov/publicinquiry/",
            "additionalResourceLabel": "TABC Open Data (7hf9-qc9f)",
            "additionalResourceUrl": "https://data.texas.gov/resource/7hf9-qc9f.json?$q=2000%20SMITH",
            "status": "Complete",
            "lastChecked": "2026-10-07",
            "automated": True,
            "checks": [
                {"date": "2026-09-22", "result": "TABC permit for 2000 Smith under Smith St C Store LLC issued 4/1/26"},
                {"date": "2026-10-07", "result": "Submitted public records request for application materials, including site plan; denied due to confidentiality"},
            ],
        },
        {
            "id": "chk-07",
            "stage": "Plans & Use",
            "activity": "Hemp Licensing",
            "whatToLookFor": "Search the address for pending or issued permits. Applicant records often reveal the operator, trade names, and LLCs, and show the intended use.",
            "typicalTiming": "After closing, once an operator has a lease or deed.",
            "resourceLabel": "DSHS License Search",
            "resourceUrl": "https://vo.ras.dshs.state.tx.us/datamart/login.do",
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "Complete",
            "lastChecked": "2026-09-22",
            "automated": False,
            "checks": [
                {"date": "2026-09-22", "result": "'Retail Hemp Registration' for Smith St C Store LLC at 2000 Smith (applied Feb-Mar 2026)"}
            ],
        },
        {
            "id": "chk-08",
            "stage": "Plans & Use",
            "activity": "Pending Plans (Open Records & ILMS)",
            "whatToLookFor": "Request plans, pre-app meeting records, and plan review project numbers for the address (and perimeter block addresses) from the Houston Permitting Center. Track project numbers in ILMS.",
            "typicalTiming": "Most useful after closing, once an architect or engineer is engaged.",
            "resourceLabel": "Public Records (GovQA)",
            "resourceUrl": "https://houstontx.govqa.us/WEBAPP/_rs/supporthome.aspx",
            "additionalResourceLabel": "ILMS Project Status",
            "additionalResourceUrl": "https://www.pdinet.pd.houstontx.gov/cohilms/webs/plan_lookup.asp",
            "status": "Flagged",
            "lastChecked": "2026-10-22",
            "automated": False,
            "checks": [
                {
                    "date": "2026-10-22",
                    "result": "COH Plan Review Project #26052089 filed under perimeter block address 530 Pierce St on 6/24/2026 for a proposed gas station; plans submitted 8 times and returned for revisions 10/2/2026",
                }
            ],
        },
        {
            "id": "chk-09",
            "stage": "Plans & Use",
            "activity": "Replats & Variances",
            "whatToLookFor": "Watch for replat and variance applications (including Section K Development Plat Variances) that include the property.",
            "typicalTiming": "Can be filed before closing with the seller's consent. Common during due diligence and design.",
            "resourceLabel": "Houston Plat Tracker",
            "resourceUrl": "https://plattracker.powerappsportals.us/SignIn",
            "additionalResourceLabel": "Atlas Plat & Variance Monitor",
            "additionalResourceUrl": "/plat-monitor.html",
            "status": "Complete",
            "lastChecked": "2026-10-23",
            "automated": True,
            "checks": [
                {
                    "date": "2026-09-22",
                    "result": "Planning Commission approved 'Smith Street Plaza' replat consolidating block into 1 unrestricted reserve on 5/28/2026 (not yet recorded in County Clerk plat records)",
                }
            ],
        },
        {
            "id": "chk-10",
            "stage": "Plans & Use",
            "activity": "Accessibility Registration (TDLR TABS)",
            "whatToLookFor": "Search the address (and secondary block addresses) in TDLR TABS. Registered projects list the owner, design firm, estimated cost, and scope (renovation or new construction). Required for commercial projects over $50,000.",
            "typicalTiming": "Design phase, before construction starts. Often one of the earliest public signs of what is planned.",
            "resourceLabel": "TDLR Architectural Barriers",
            "resourceUrl": "https://www.tdlr.texas.gov/TABS/",
            "additionalResourceLabel": f"TDLR Project {tabs_num}",
            "additionalResourceUrl": "https://www.tdlr.texas.gov/TABS/Search",
            "status": "Flagged",
            "lastChecked": "2026-10-23",
            "automated": True,
            "checks": [
                {
                    "date": "2026-10-22",
                    "result": f"TDLR Project #{tabs_num} ('{tabs_name}') registered 6/17/2026 at perimeter address 530 Pierce St: $2,000,000 new construction of 13,254 SF convenience store (scheduled 9/1/2026 - 9/1/2027), design firm Heights Engineering (Munirishaq@heightsengineering.com), owner Hi Tech Auto Realty LLC / tenant Brake Time",
                }
            ],
        },
        {
            "id": "chk-11",
            "stage": "Plans & Use",
            "activity": "Easements, Abandonments & TIRZ",
            "whatToLookFor": "Check City Council agendas for street, alley, or easement abandonments next to the property. Check Midtown TIRZ / Midtown Redevelopment Authority agendas.",
            "typicalTiming": "Design phase, usually after closing.",
            "resourceLabel": "City Secretary",
            "resourceUrl": "https://houstontx.gov/citysec/",
            "additionalResourceLabel": "Midtown Redevelopment Authority",
            "additionalResourceUrl": "https://midtownhouston.com/affiliated-organizations/mra/",
            "status": "To Do",
            "lastChecked": "",
            "automated": False,
            "checks": [],
        },
        {
            "id": "chk-12",
            "stage": "Plans & Use",
            "activity": "Environmental Registrations",
            "whatToLookFor": "Search the address for new regulated-entity records, such as construction stormwater coverage or underground petroleum storage tanks (PST).",
            "typicalTiming": "Shortly before construction. Most useful for gas stations and larger sites.",
            "resourceLabel": "TCEQ Central Registry",
            "resourceUrl": "https://www.tceq.texas.gov/permitting/central_registry",
            "additionalResourceLabel": "",
            "additionalResourceUrl": "",
            "status": "In Progress",
            "lastChecked": "2026-10-20",
            "automated": False,
            "checks": [
                {"date": "2026-09-22", "result": "old/irrelevant results. RN for location: RN101872331"},
                {"date": "2026-10-05", "result": "no new results"},
                {"date": "2026-10-09", "result": "OB Texas Properties is registered as a customer since 10/9/2020, but no affiliated regulated entities. CN605820570"},
                {"date": "2026-10-20", "result": "no new results"},
            ],
        },
        {
            "id": "chk-13",
            "stage": "Demolition Signals",
            "activity": "Code Complaints & Dangerous Buildings",
            "whatToLookFor": "Check the 311 map for complaints at the address (open structure, nuisance, dangerous building). A dangerous building case can lead to a demolition order.",
            "typicalTiming": "Any stage, especially while the building is vacant.",
            "resourceLabel": "311 Service Request Viewer",
            "resourceUrl": "https://experience.arcgis.com/experience/cf006ed734814cee9f5b9bcf7abc839f",
            "additionalResourceLabel": "Community Code Enforcement",
            "additionalResourceUrl": "https://www.houstonpermittingcenter.org/community-code-enforcement",
            "status": "N/A",
            "lastChecked": "2026-10-23",
            "automated": True,
            "checks": [],
        },
        {
            "id": "chk-14",
            "stage": "Demolition Signals",
            "activity": "Demolition & Building Permits",
            "whatToLookFor": "Search Sold Permits by address (2000 Smith St & 530 Pierce St) for demolition, sewer disconnect, and building permits.",
            "typicalTiming": "Final weeks before work starts.",
            "resourceLabel": "Houston Sold Permits",
            "resourceUrl": "https://cohtora.houstontx.gov/approot/soldpermits/online_permit.htm",
            "additionalResourceLabel": "Permit Activity Reports",
            "additionalResourceUrl": "https://www.houstonpermittingcenter.org/news-events/permit-activity-reports-now-available",
            "status": "Flagged",
            "lastChecked": "2026-10-23",
            "automated": True,
            "checks": [
                {"date": "2026-09-22", "result": "no demo permit"},
                {"date": "2026-10-05", "result": "no demo permit"},
                {
                    "date": "2026-10-14",
                    "result": "COH Permit #26090093 ('BLD DEMO/SEWER DISC', occupant '*KIRBY MANSION') issued 10/14/2026 to Robert Camacho at 2000 Smith St",
                },
            ],
        },
        {
            "id": "chk-15",
            "stage": "Demolition Signals",
            "activity": "Asbestos & Demolition Notification (DSHS)",
            "whatToLookFor": "DSHS must be notified at least 10 working days before any commercial or public building is abated or demolished, even if no asbestos is present.",
            "typicalTiming": "At least 10 working days (about two weeks) before abatement or demolition starts.",
            "resourceLabel": "DSHS Notifications",
            "resourceUrl": "https://vo.ras.dshs.state.tx.us/datamart/mainMenu.do",
            "additionalResourceLabel": "DSHS Notification #2026005825",
            "additionalResourceUrl": "https://vo.ras.dshs.state.tx.us/datamart/notificationPrintAction.do?licId=2451374",
            "status": "Flagged",
            "lastChecked": "2026-10-23",
            "automated": True,
            "checks": [
                {"date": "2026-09-22", "result": "no results"},
                {"date": "2026-10-05", "result": "no results"},
                {
                    "date": "2026-10-13",
                    "result": "DSHS Notification #2026005825 filed 10/13/2026 for 2000 Smith St: abatement of 5,010 SF RACM scheduled 10/27/2026 - 11/6/2026 by AAR Incorporated for owner Omair Bashir (OB Texas Properties LLC)",
                },
            ],
        },
    ]

    # Flatten all dated checks into the chronological Findings Log (matching Sheet 2 of Emily's tracker)
    findings_log: list[dict] = []
    for idx_row, row in enumerate(kirby_checklist):
        for chk in row["checks"]:
            findings_log.append(
                {
                    "date": chk["date"],
                    "stage": row["stage"],
                    "activity": row["activity"],
                    "result": chk["result"],
                    "automated": row["automated"],
                    "_order": (chk["date"], idx_row),
                }
            )
    findings_log.sort(key=lambda x: x["_order"])
    for i, entry in enumerate(findings_log, 1):
        entry["entryNo"] = i
        entry.pop("_order", None)

    kirby_property = {
        "id": "prop-kirby-mansion",
        "propertyName": "John Henry Kirby Mansion",
        "address": "2000 Smith St.",
        "secondaryAddresses": ["530 Pierce St.", "2000 Brazos St.", "500 Gray St."],
        "hcadAccount": "0152300000001",
        "buildingId": "bld_004618",
        "yearBuilt": 1894,
        "yearRemodeled": 1928,
        "architect": "James Ruskin Bailey (1928 Tudor Revival remodel)",
        "designation": "1979 Good Brick Award (No City Protected Landmark designation)",
        "ownerOfRecord": "OB Texas Properties LLC (acquired 9/30/2026 from Hi Tech Auto Realty LLC)",
        "buyerRelatedEntities": "Omair Bashir / OB Texas Properties LLC / Smith St C Store LLC / BTCM National Retail Operations LLC (Brake Time)",
        "lender": "Greenberg Finance Co. ($7,000,000 loan recorded 10/1/2026)",
        "designFirm": "Heights Engineering (Munir Ishaq, P.E.)",
        "keyDates": {
            "monitoringStarted": "2026-09-22",
            "targetClosing": "2026-09-30",
            "planningCommission": "2026-05-28",
            "asbestosAbatementWindow": "2026-10-27 to 2026-11-06",
            "expectedDemolition": "Imminent risk following Nov 6, 2026 abatement window",
            "tdlrScheduledConstruction": "2026-09-01 to 2027-09-01",
        },
        "notes": "Sale closed effective 9/30/2026 (recorded 10/1/2026, Special Warranty Deed #RP-2026-399473). Multi-agency filings across 2000 Smith St and perimeter block address 530 Pierce St reveal plans for a $2M, 13,254 SF 'Brake Time' convenience store and gas station (TDLR #TABS2026022839, COH Plan Review #26052089). Emailed Omair Bashir, who responded positively about meeting to discuss plans.",
        "lng": -95.374833,
        "lat": 29.751432,
        "tier": "1",
        "checklist": kirby_checklist,
        "findingsLog": findings_log,
    }

    # Add watched properties escalated from the Plat & Variance Monitor
    additional_watched: list[dict] = []
    for it in plat_data["items"]:
        rev = plat_data["reviews"].get(it["app"], {})
        if rev.get("tier") in ("1", "2"):
            additional_watched.append(
                {
                    "id": f"plat-{it['app']}",
                    "propertyName": it["name"],
                    "address": rev.get("address") or it["atlas"].get("primaryAddress") or it.get("noticeLocation") or "",
                    "secondaryAddresses": [],
                    "hcadAccount": it.get("hcad", ""),
                    "buildingId": it["atlas"].get("buildingId", ""),
                    "yearBuilt": it["atlas"].get("earliestYearBuilt") or rev.get("yearBuilt") or "",
                    "designation": rev.get("designation") or "None found",
                    "ownerOfRecord": it.get("developer") or "",
                    "buyerRelatedEntities": ", ".join(filter(None, [it.get("developer"), it.get("applicant"), it.get("org")])),
                    "keyDates": {
                        "monitoringStarted": plat_data["cycleKey"],
                        "planningCommission": plat_data["cycleKey"],
                    },
                    "notes": rev.get("why") or "",
                    "lng": it["atlas"].get("lng"),
                    "lat": it["atlas"].get("lat"),
                    "tier": rev.get("tier"),
                    "appNo": it["app"],
                    "section": it.get("section", ""),
                    "platPdf": it.get("pdf", ""),
                    "noticePdf": it.get("noticePdf", ""),
                }
            )

    return {
        "updatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "primaryProperty": kirby_property,
        "watchedProperties": [kirby_property] + additional_watched,
    }


def enrich_kirby_mansion_permit_alert():
    print("[5/6] Enriching Kirby Mansion (0152300000001) in active_permit_alerts.json and permits_015.json...")
    multi_agency_filings = [
        {
            "date": "2026-10-14",
            "agency": "COH Houston Permitting Center",
            "type": "Sewer Disconnection / Demo Prep Permit",
            "reference": "Project #26090093",
            "address": "2000 Smith St",
            "summary": "Plumbing permit for 'BLD DEMO/SEWER DISC' (occupant '*KIRBY MANSION') issued to contractor Robert Camacho.",
            "url": "https://cohtora.houstontx.gov/approot/soldpermits/online_permit.htm",
            "severity": "critical",
        },
        {
            "date": "2026-10-13",
            "agency": "Texas DSHS Asbestos Program",
            "type": "Asbestos Abatement Notification",
            "reference": "Notification #2026005825",
            "address": "2000 Smith St",
            "summary": "Full-containment abatement of 5,010 SF RACM and 150 LF pipe insulation scheduled Oct 27 - Nov 6, 2026 by AAR Incorporated for owner Omair Bashir.",
            "url": "https://vo.ras.dshs.state.tx.us/datamart/notificationPrintAction.do?licId=2451374",
            "severity": "critical",
        },
        {
            "date": "2026-10-01",
            "agency": "Harris County Clerk Real Property",
            "type": "Special Warranty Deed & $7M Deed of Trust",
            "reference": "File #RP-2026-399473",
            "address": "2000 Smith St (Block 417 SSBB)",
            "summary": "Sale closed 9/30/2026 from Hi Tech Auto Realty LLC to OB Texas Properties LLC (Omair Bashir), financed by a $7,000,000 loan from Greenberg Finance Co.",
            "url": "https://www.cclerk.hctx.net/applications/websearch/RP.aspx",
            "severity": "high",
        },
        {
            "date": "2026-06-24",
            "agency": "COH Permitting Center (ILMS Plan Review)",
            "type": "Commercial Plan Review (Gas Station)",
            "reference": "Project #26052089",
            "address": "530 Pierce St (Perimeter Block Address)",
            "summary": "Building plans for a proposed gas station submitted under perimeter address 530 Pierce St; submitted 8 times and returned for revisions on Oct 2, 2026.",
            "url": "https://www.pdinet.pd.houstontx.gov/cohilms/webs/plan_lookup.asp",
            "severity": "critical",
        },
        {
            "date": "2026-06-17",
            "agency": "TDLR Architectural Barriers (TABS)",
            "type": "Commercial Accessibility Registration",
            "reference": "TABS2026022839",
            "address": "530 Pierce St (Perimeter Block Address)",
            "summary": "'Braketime Smith Road': $2,000,000 new construction of a 13,254 SF convenience store (scheduled 9/1/2026 - 9/1/2027), designed by Heights Engineering for tenant Brake Time.",
            "url": "https://www.tdlr.texas.gov/TABS/",
            "severity": "critical",
        },
        {
            "date": "2026-05-28",
            "agency": "Houston Planning Commission",
            "type": "Subdivision Replat ('Smith Street Plaza')",
            "reference": "PC Agenda 5/28/2026",
            "address": "2000 Smith St (Block 417 SSBB)",
            "summary": "Planning Commission approved replat consolidating the entire 62,500 SF block into 1 unrestricted reserve (not yet recorded with County Clerk).",
            "url": "/plat-monitor.html?tab=watchlist",
            "severity": "high",
        },
        {
            "date": "2026-04-01",
            "agency": "Texas Alcoholic Beverage Commission (TABC) & DSHS",
            "type": "Liquor Permit & Retail Hemp Registration",
            "reference": "Smith St C Store LLC",
            "address": "2000 Smith St",
            "summary": "TABC Wine and Malt Beverage Retailer's Off-Premise Permit (BQ) issued 4/1/2026 following Feb-Mar 2026 legal notices; DSHS Retail Hemp Registration filed.",
            "url": "https://apps.tabc.texas.gov/publicinquiry/",
            "severity": "watch",
        },
        {
            "date": "2025-10-15",
            "agency": "Texas Comptroller of Public Accounts",
            "type": "Entity Formation & Active Sales Tax Permit",
            "reference": "Taxpayer #32102472084",
            "address": "2000 Smith St",
            "summary": "Smith St C Store LLC formed Oct 2025 by Omair Bashir (BTCM National Retail Operations LLC) with Active Sales Tax Permit for 'Brake Time' at 2000 Smith St.",
            "url": "https://comptroller.texas.gov/taxes/franchise/account-status/search/32062633212",
            "severity": "watch",
        },
    ]

    alerts_path = DATA_DIR / "active_permit_alerts.json"
    if alerts_path.exists():
        alerts_data = json.load(open(alerts_path, "r", encoding="utf-8"))
        for al in alerts_data.get("alerts", []):
            if al.get("hcad_num") == "0152300000001":
                al["secondary_addresses"] = ["530 PIERCE ST", "2000 BRAZOS ST", "500 GRAY ST"]
                al["headline"] = (
                    "Multi-agency redevelopment alert: COH Sewer Disconnect (#26090093) & DSHS Asbestos Abatement "
                    "(#2026005825, Oct 27 - Nov 6, 2026) at 2000 Smith St + TDLR TABS (#TABS2026022839, $2M 13,254 SF "
                    "'Braketime' C-Store) & COH Gas Station Plan Review (#26052089) at perimeter address 530 Pierce St."
                )
                al["preservation_note"] = (
                    "Built in 1894 for businessman J.S. Price and purchased by lumber magnate John Henry Kirby in 1896, "
                    "the 36-room Tudor Revival mansion at 2000 Smith Street achieved its landmark appearance in a 1928 "
                    "remodeling designed by architect James Ruskin Bailey (1979 Preservation Houston Good Brick Award). "
                    "Cross-agency monitoring by Preservation Houston across both 2000 Smith St and perimeter block address "
                    "530 Pierce St uncovered an 8-stage redevelopment sequence: (1) Oct 2025 formation of Smith St C Store LLC "
                    "and Active Sales Tax Permit #32102472084 for 'Brake Time'; (2) Apr 1, 2026 TABC liquor permit and DSHS "
                    "Hemp registration; (3) May 28, 2026 Planning Commission approval of the 'Smith Street Plaza' replat into "
                    "1 unrestricted reserve; (4) June 17, 2026 TDLR Architectural Barriers registration #TABS2026022839 at "
                    "530 Pierce St for 'Braketime Smith Road' ($2,000,000, 13,254 SF convenience store designed by Heights "
                    "Engineering); (5) June 24, 2026 COH Plan Review #26052089 at 530 Pierce St for a proposed gas station "
                    "(submitted 8 times, returned Oct 2, 2026); (6) Sept 30 / Oct 1, 2026 Special Warranty Deed #RP-2026-399473 "
                    "transferring the block to OB Texas Properties LLC (Omair Bashir) with a $7M loan from Greenberg Finance Co.; "
                    "(7) Oct 13, 2026 DSHS Notification #2026005825 scheduling abatement of 5,010 SF of RACM from Oct 27 to "
                    "Nov 6, 2026; and (8) Oct 14, 2026 COH Permit #26090093 ('BLD DEMO/SEWER DISC')."
                )
                al["multi_agency_filings"] = multi_agency_filings
                al["watchlist_id"] = "prop-kirby-mansion"
        with open(alerts_path, "w", encoding="utf-8") as f:
            json.dump(alerts_data, f, indent=2)

    permits_015_path = DATA_DIR / "permits" / "permits_015.json"
    if permits_015_path.exists():
        p015 = json.load(open(permits_015_path, "r", encoding="utf-8"))
        rec = p015.get("0152300000001")
        if isinstance(rec, dict):
            rec["multi_agency_filings"] = multi_agency_filings
            rec["secondary_addresses"] = ["530 PIERCE ST"]
            with open(permits_015_path, "w", encoding="utf-8") as f:
                json.dump(p015, f, separators=(",", ":"))


def main():
    idx = load_atlas_indices()
    scraped = fetch_latest_agenda_and_notices()
    plat_data = build_plat_agenda_dataset(idx, scraped)
    watchlist_data = build_property_watchlist(plat_data)
    enrich_kirby_mansion_permit_alert()

    print("[6/6] Writing app/public/data/plat_agenda_monitor.json and app/public/data/property_watchlist.json...")
    plat_out = DATA_DIR / "plat_agenda_monitor.json"
    with open(plat_out, "w", encoding="utf-8") as f:
        json.dump(plat_data, f, indent=2)

    watch_out = DATA_DIR / "property_watchlist.json"
    with open(watch_out, "w", encoding="utf-8") as f:
        json.dump(watchlist_data, f, indent=2)

    print(
        f"Done! Wrote {len(plat_data['items'])} agenda items ({plat_data['summary']['toCheckCount']} to check, "
        f"{plat_data['summary']['preEnrichedCount']} pre-enriched with Atlas parcel/subdivision data, "
        f"Tier 1: {plat_data['summary']['tier1Count']}, Tier 2: {plat_data['summary']['tier2Count']}) "
        f"and {len(watchlist_data['watchedProperties'])} watched properties."
    )


if __name__ == "__main__":
    main()
