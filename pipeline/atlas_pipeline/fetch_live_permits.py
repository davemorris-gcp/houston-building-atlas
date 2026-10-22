#!/usr/bin/env python3
"""
Automated Live Permit & Asbestos Notification Ingestion Pipeline
for The Houston Building Atlas.

Fetches:
1. City of Houston Sold Permits (WebFOCUS live API at cohtora.houstontx.gov
   using concurrent date-slice fetching + project drilldown via sold_permit_d.fex,
   supplemented by weekly Sold Permits .xlsx spreadsheets on houstonpermittingcenter.org).
2. Texas Department of State Health Services (DSHS) Asbestos Abatement &
   Demolition Notifications (vo.ras.dshs.state.tx.us/datamart) including full
   3-page notification PDF extraction via pdftotext.

Cross-references all filings against historic structures in
app/public/data/buildings.geojson and app/public/data/search_index.json,
classifies risk severity (CRITICAL vs WATCH), enriches affected permit shards
(app/public/data/permits/permits_<prefix3>.json), and writes
app/public/data/active_permit_alerts.json for the Atlas UI.
"""

import argparse
import ast
import concurrent.futures
import datetime
import http.cookiejar
import io
import json
import re
import ssl
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDINGS_GEOJSON = REPO_ROOT / "app" / "public" / "data" / "buildings.geojson"
SEARCH_INDEX_JSON = REPO_ROOT / "app" / "public" / "data" / "search_index.json"
PERMITS_DIR = REPO_ROOT / "app" / "public" / "data" / "permits"
ALERTS_OUTPUT_JSON = REPO_ROOT / "app" / "public" / "data" / "active_permit_alerts.json"

COH_WFSERVLET_URL = "http://cohtora.houstontx.gov/ibi_apps/WFServlet"
HPC_SOLD_PERMITS_PAGE = "https://www.houstonpermittingcenter.org/sold-permits-search"
DSHS_BASE_URL = "https://vo.ras.dshs.state.tx.us/datamart"


def make_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


SSL_CTX = make_ssl_context()


def normalize_address(addr: str) -> str:
    """Normalize a Houston street address for matching against HCAD situs addresses."""
    if not addr:
        return ""
    s = str(addr).upper().strip()
    s = re.sub(r"\bHOUSTON\s+HARRIS\s+TX.*$", "", s)
    s = re.sub(r"\bHOUSTON\s*,?\s*TX.*$", "", s)
    s = re.sub(r"\bHOUSTON\b.*$", "", s)
    s = re.sub(r"\s+\d{5}(?:-\d{4})?\s*$", "", s)
    s = re.sub(r"[.,#]", " ", s)
    s = re.sub(r"\b(?:STE|SUITE|APT|UNIT|FL|FLOOR|BLDG|BLD|RM|ROOM)\s+[A-Z0-9-]+\b.*$", "", s)
    replacements = [
        (r"\bSTREET\b", "ST"),
        (r"\bAVENUE\b", "AVE"),
        (r"\bBOULEVARD\b", "BLVD"),
        (r"\bDRIVE\b", "DR"),
        (r"\bROAD\b", "RD"),
        (r"\bLANE\b", "LN"),
        (r"\bCOURT\b", "CT"),
        (r"\bCIRCLE\b", "CIR"),
        (r"\bPLACE\b", "PL"),
        (r"\bPARKWAY\b", "PKWY"),
        (r"\bPKY\b", "PKWY"),
        (r"\bFREEWAY\b", "FWY"),
        (r"\bHIGHWAY\b", "HWY"),
        (r"\bSPEEDWAY\b", "SPWY"),
        (r"\bTERRACE\b", "TER"),
        (r"\bTRAIL\b", "TRL"),
        (r"\bSQUARE\b", "SQ"),
        (r"\bNORTH\b", "N"),
        (r"\bSOUTH\b", "S"),
        (r"\bEAST\b", "E"),
        (r"\bWEST\b", "W"),
    ]
    for pat, rep in replacements:
        s = re.sub(pat, rep, s)
    s = re.sub(r"\s+", " ", s).strip()
    m = re.match(
        r"^(\d+\s+(?:1/2\s+)?(?:[NSEW]\s+)?[A-Z0-9\s]+?\s+(?:ST|AVE|BLVD|DR|RD|LN|CT|CIR|PL|PKWY|FWY|HWY|WAY|TER|TRL|SQ|LOOP))\s+(?:[A-Z]|\d+|A\s*&\s*B|BRDGE|MAIN)$",
        s,
    )
    if m:
        s = m.group(1).strip()
    return s


def compute_feature_centroid(geom):
    """Compute [lng, lat] centroid from a GeoJSON Polygon or MultiPolygon."""
    if not geom:
        return [-95.3698, 29.7604]
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    pts = []
    if gtype == "Point" and len(coords) >= 2:
        return [round(float(coords[0]), 6), round(float(coords[1]), 6)]
    elif gtype == "Polygon" and coords:
        pts = coords[0]
    elif gtype == "MultiPolygon" and coords and coords[0]:
        pts = coords[0][0]
    if not pts:
        return [-95.3698, 29.7604]
    xs = [p[0] for p in pts if len(p) >= 2]
    ys = [p[1] for p in pts if len(p) >= 2]
    if not xs or not ys:
        return [-95.3698, 29.7604]
    return [round(sum(xs) / len(xs), 6), round(sum(ys) / len(ys), 6)]


def enrich_kirby_mansion_metadata(gj_data: dict) -> bool:
    """
    Ensure the John Henry Kirby Mansion (2000 Smith St, HCAD 0152300000001)
    carries its complete architectural and historical provenance from
    Preservation Houston's archival records.
    """
    changed = False
    for feat in gj_data.get("features", []):
        p = feat.get("properties") or {}
        if str(p.get("hcad_num") or "") == "0152300000001":
            updates = {
                "landmark_name": "John Henry Kirby Mansion",
                "alt_names": "Kirby Mansion; J. S. Price House; 2000 Smith Street",
                "year_built": 1894,
                "eff_year_built": 1894,
                "yr_remodel": 1928,
                "architect": "James Ruskin Bailey",
                "style": "Tudor Revival",
                "owner": "OB Texas Properties LLC (Omair Bashir)",
                "year_provenance": (
                    "Original residence built in 1894 for businessman J. S. Price; "
                    "purchased in 1896 by lumber magnate John Henry Kirby and remodeled twice, "
                    "culminating in the 1926-1928 36-room Tudor Revival expansion designed by "
                    "architect James Ruskin Bailey (1979 Preservation Houston Good Brick Award)."
                ),
            }
            for k, v in updates.items():
                if p.get(k) != v:
                    p[k] = v
                    changed = True
    return changed


def update_search_index_for_kirby_mansion():
    """Keep search_index.json synchronized with the enriched Kirby Mansion record."""
    if not SEARCH_INDEX_JSON.exists():
        return
    with open(SEARCH_INDEX_JSON, "r", encoding="utf-8") as f:
        idx_data = json.load(f)
    items = idx_data if isinstance(idx_data, list) else idx_data.get("items", [])
    modified = False
    for item in items:
        if str(item.get("hcad_num") or item.get("h") or "") == "0152300000001":
            if item.get("year_built") != 1894:
                item["year_built"] = 1894
                modified = True
            if item.get("landmark_name") != "John Henry Kirby Mansion":
                item["landmark_name"] = "John Henry Kirby Mansion"
                modified = True
            if item.get("architect") != "James Ruskin Bailey":
                item["architect"] = "James Ruskin Bailey"
                modified = True
            if item.get("style") != "Tudor Revival":
                item["style"] = "Tudor Revival"
                modified = True
    if modified:
        with open(SEARCH_INDEX_JSON, "w", encoding="utf-8") as f:
            json.dump(idx_data, f, separators=(",", ":"))


def query_coh_single_window(seltd: str, srh: str, bdt: datetime.date, edt: datetime.date, ptype: str = "ALL") -> list:
    """Query City of Houston WebFOCUS online_per_se.fex for a single small date window."""
    params = urllib.parse.urlencode(
        {
            "IBIF_ex": "online_per_se.fex",
            "IBIAPP_app": "soldpermits",
            "SELTD": seltd,
            "SRH": srh,
            "BDT": bdt.strftime("%Y/%m/%d"),
            "EDT": edt.strftime("%Y/%m/%d"),
            "PTYPE": ptype,
        }
    )
    url = f"{COH_WFSERVLET_URL}?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=20) as r:
            res = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  [WARN] COH WFServlet query failed ({seltd}={srh}, {bdt}..{edt}): {e}", flush=True)
        return []

    m_ar = re.search(r"ARstrings=(\[\x27Search.*?\]);\s*T_cont", res, re.S)
    m_tc = re.search(r"T_cont\[NumOfTable\]\s*=\s*(\[\[\[.*?\]);", res, re.S)
    if not m_ar or not m_tc:
        return []
    try:
        ar_strings = ast.literal_eval(m_ar.group(1))
        t_cont = ast.literal_eval(m_tc.group(1))
    except Exception:
        return []

    results = []
    for row in t_cont:
        if len(row) < 7:
            continue
        vals = [str(ar_strings[cell[0]]).strip() for cell in row[:7]]
        proj_no, p_desc, owner_occ, addr, proj_desc, val, p_type = vals
        results.append(
            {
                "project_no": proj_no,
                "permit_desc": p_desc,
                "owner_occupant": owner_occ,
                "raw_address": addr,
                "norm_address": normalize_address(addr),
                "use_desc": proj_desc,
                "valuation": val,
                "permit_type": p_type,
            }
        )
    return results


def fetch_coh_permits_parallel(lookback_days: int = 45) -> dict:
    """
    Fetch City of Houston DEMO, SEWER DISC, and SD permits across `lookback_days`
    using parallel 5-day date slices so every slice stays below the 100-row WebFOCUS page cap.
    """
    today = datetime.date.today()
    start_dt = today - datetime.timedelta(days=lookback_days)
    slices = []
    cur = start_dt
    step = datetime.timedelta(days=5)
    while cur <= today:
        end_s = min(today, cur + step)
        slices.append((cur, end_s))
        cur = end_s + datetime.timedelta(days=1)

    tasks = []
    for bdt, edt in slices:
        tasks.append(("BU", "DEMO", bdt, edt, "ALL"))
        tasks.append(("PT", " ", bdt, edt, "SD"))

    combined = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(query_coh_single_window, *t) for t in tasks]
        for fut in concurrent.futures.as_completed(futs):
            for r in fut.result():
                key = (r["project_no"], r.get("permit_type", ""), r.get("permit_desc", ""))
                combined[key] = r
    return combined


def fetch_coh_permit_detail(project_no: str, permit_type: str) -> dict:
    """
    Fetch granular permit details (exact paid date, FCC group, buyer/contractor name,
    buyer address, phone) from City of Houston sold_permit_d.fex.
    """
    params = urllib.parse.urlencode(
        {
            "IBIF_ex": "sold_permit_d.fex",
            "IBIAPP_app": "soldpermits",
            "PN": project_no,
            "PT": permit_type,
        }
    )
    url = f"{COH_WFSERVLET_URL}?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as r:
            res = r.read().decode("utf-8", errors="ignore")
    except Exception:
        return {}

    no_style = re.sub(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", res, flags=re.S | re.I)
    clean = re.sub(r"<[^>]+>", "\n", no_style)
    lines = [re.sub(r"\s+", " ", ln.replace("&nbsp;", " ")).strip() for ln in clean.splitlines()]
    lines = [ln for ln in lines if ln]

    detail = {}
    label_map = {
        "Project No:": "project_no",
        "Date :": "raw_date",
        "USE :": "use_desc",
        "Owner/Occupant :": "owner_occupant",
        "Job Address :": "job_address",
        "Valuation :": "valuation_formatted",
        "Permit Type :": "permit_type",
        "FCC Group :": "fcc_group",
        "Buyer :": "buyer",
        "Address :": "buyer_address",
        "Phone :": "buyer_phone",
    }
    for i, ln in enumerate(lines):
        if ln in label_map and i + 1 < len(lines):
            val = lines[i + 1]
            if val not in label_map:
                detail[label_map[ln]] = val

    raw_dt = detail.get("raw_date", "")
    m_dt = re.search(r"(\d{4})/(\d{2})/(\d{2})", raw_dt)
    if m_dt:
        detail["date"] = f"{m_dt.group(1)}-{m_dt.group(2)}-{m_dt.group(3)}"
    return detail


def parse_xlsx_sold_permits(xlsx_bytes: bytes) -> list:
    """Parse a City of Houston weekly Sold Permits .xlsx spreadsheet in memory."""
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        z = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
        ss = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root:
                ss.append("".join(t.text or "" for t in si.iter() if t.tag.endswith("}t")))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    except Exception:
        return []

    records = []
    for row in sheet.findall(".//a:sheetData/a:row", ns):
        cells = {}
        for c in row.findall("a:c", ns):
            ref = c.attrib.get("r", "")
            col_letters = "".join(ch for ch in ref if ch.isalpha())
            t = c.attrib.get("t", "")
            v_el = c.find("a:v", ns)
            val = v_el.text if v_el is not None and v_el.text is not None else ""
            if t == "s" and val.isdigit() and int(val) < len(ss):
                val = ss[int(val)]
            cells[col_letters] = str(val).strip()
        proj_no = cells.get("D", "")
        addr = cells.get("E", "")
        dt_raw = cells.get("B", "")
        if not proj_no or not addr or proj_no == "Project No":
            continue
        m_dt = re.search(r"(\d{4})/(\d{2})/(\d{2})", dt_raw)
        iso_dt = f"{m_dt.group(1)}-{m_dt.group(2)}-{m_dt.group(3)}" if m_dt else dt_raw
        records.append(
            {
                "project_no": proj_no,
                "date": iso_dt,
                "permit_desc": cells.get("C", ""),
                "raw_address": f"{addr} {cells.get('A', '')}".strip(),
                "norm_address": normalize_address(addr),
                "use_desc": cells.get("F", ""),
                "permit_type": "",
                "source": "coh_weekly_xlsx",
            }
        )
    return records


def fetch_recent_weekly_xlsx_permits(max_files: int = 4) -> list:
    """Download the most recent weekly Sold Permits .xlsx files from houstonpermittingcenter.org."""
    try:
        req = urllib.request.Request(HPC_SOLD_PERMITS_PAGE, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as r:
            body = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  [WARN] Could not fetch {HPC_SOLD_PERMITS_PAGE}: {e}", flush=True)
        return []

    raw_links = re.findall(r'href=["\']([^"\']*xlsx?[^"\']*)["\']', body, re.I)
    clean_urls = []
    for lk in raw_links:
        lk = lk.replace("&amp;", "&")
        if "view.officeapps.live.com" in lk and "src=" in lk:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(lk).query)
            if "src" in parsed and parsed["src"]:
                lk = urllib.parse.unquote(parsed["src"][0])
        if lk.startswith("/"):
            lk = "https://www.houstonpermittingcenter.org" + lk
        if lk.lower().endswith(".xlsx") and lk not in clean_urls:
            clean_urls.append(lk)

    recent_urls = clean_urls[-max_files:]
    all_rows = []
    for u in recent_urls:
        try:
            u_safe = urllib.parse.quote(urllib.parse.unquote(u), safe=":/?&=%")
            req = urllib.request.Request(u_safe, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as r:
                rows = parse_xlsx_sold_permits(r.read())
                print(f"  Parsed {len(rows)} permits from weekly report: {u_safe.split('/')[-1]}", flush=True)
                all_rows.extend(rows)
        except Exception as e:
            print(f"  [WARN] Weekly XLSX download failed for {u}: {e}", flush=True)
    return all_rows


def parse_dshs_pdf_text(pdf_bytes: bytes) -> dict:
    """Extract structured fields from a 3-page Texas DSHS Asbestos/Demolition Notification PDF."""
    try:
        p = subprocess.run(["pdftotext", "-", "-"], input=pdf_bytes, capture_output=True, timeout=10)
        txt = p.stdout.decode("utf-8", errors="ignore")
    except Exception:
        return {}

    info = {}
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]

    if len(lines) >= 2 and re.match(r"^\d{4}/\d{2}/\d{2}$", lines[1]):
        info["filing_date"] = lines[1].replace("/", "-")

    for idx, ln in enumerate(lines):
        if ln == "Section III - Type of Work/Schedule":
            block = lines[idx : idx + 25]
            for bln in block:
                if bln in ("Abatement", "Demolition", "Abatement/Demolition", "Renovation", "Ordered Demolition"):
                    info["work_type"] = bln
                if re.match(r"^[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}$", bln):
                    if "start_date_pretty" not in info:
                        info["start_date_pretty"] = bln
                    elif "end_date_pretty" not in info:
                        info["end_date_pretty"] = bln
                if "AM" in bln and "PM" in bln:
                    info["work_hours"] = bln
                if "Containment" in bln:
                    info["containment_method"] = bln

    for idx, ln in enumerate(lines):
        if "Date of Asbestos Survey/NESHAP Inspection" in ln:
            block = lines[idx : idx + 15]
            for bln in block:
                if re.match(r"^\d+\s+years$", bln, re.I):
                    info["reported_building_age"] = bln
                elif re.match(r"^[\d,]+\s+square\s+feet$", bln, re.I):
                    info["reported_sqft"] = bln
                elif re.match(r"^\d+$", bln) and "reported_floors" not in info:
                    info["reported_floors"] = int(bln)
                elif re.match(r"^[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}$", bln):
                    info["survey_date"] = bln

    for idx, ln in enumerate(lines):
        if "RACM to be removed" in ln:
            nums = [
                lines[j]
                for j in range(idx + 1, min(len(lines), idx + 22))
                if re.match(r"^\d+$", lines[j])
            ]
            if len(nums) >= 2:
                info["racm_linear_ft"] = int(nums[0])
                info["racm_sqft"] = int(nums[1])
            if len(nums) >= 4:
                info["non_friable_int_linear_ft"] = int(nums[2])
                info["non_friable_int_sqft"] = int(nums[3])
            if len(nums) >= 6:
                info["non_friable_ext_linear_ft"] = int(nums[4])
                info["non_friable_ext_sqft"] = int(nums[5])

    m_desc = re.search(
        r"Section V - Description of work practices\s+Description\s+(.*?)\s+Section VI - Project Personnel",
        txt,
        re.S,
    )
    if m_desc:
        desc_clean = re.sub(r"\s+", " ", m_desc.group(1)).strip()
        desc_clean = re.sub(
            r"^Immediately seal off the area,\s*wet materials,\s*clean using wet methods and/or HEPA vacuum\.\s*Notify local authority\.\s*",
            "",
            desc_clean,
            flags=re.I,
        )
        info["work_description"] = desc_clean

    m_contractor = re.search(
        r"Asbestos Abatement Contractor\s+(\d+)\s+DSHS License #\s+([^\n]+)\s+Name\s+([^\n]+)\s+Address\s+([^\n]+)",
        txt,
        re.S,
    )
    if m_contractor:
        info["contractor_license"] = m_contractor.group(1).strip()
        info["abatement_contractor"] = m_contractor.group(2).strip()
        info["contractor_address"] = f"{m_contractor.group(3).strip()}, {m_contractor.group(4).strip()}"

    m_owner = re.search(
        r"Facility Owner\s+([^\n]+)\s+Name\s+([^\n]+)\s+Address\s+([^\n]+)\s+([\d-]+)\s+Phone",
        txt,
        re.S,
    )
    if m_owner:
        info["facility_owner"] = m_owner.group(1).strip()
        info["owner_address"] = f"{m_owner.group(2).strip()}, {m_owner.group(3).strip()}"
        info["owner_phone"] = m_owner.group(4).strip()

    m_cons = re.search(
        r"Project Consultant\s+(\d+)\s+DSHS License #\s+([^\n]+)\s+Name\s+([^\n]+)\s+Attention",
        txt,
        re.S,
    )
    if m_cons:
        info["consultant_license"] = m_cons.group(1).strip()
        info["project_consultant"] = m_cons.group(2).strip()
        info["consultant_contact"] = m_cons.group(3).strip()

    m_disp = re.search(
        r"Waste Disposal Site\s+([A-Z0-9-]+)\s+TCEQ Permit #\s+([^\n]+)\s+Name",
        txt,
        re.S,
    )
    if m_disp:
        info["tceq_permit"] = m_disp.group(1).strip()
        info["disposal_site"] = m_disp.group(2).strip()

    return info


def fetch_dshs_houston_notifications(
    start_from: datetime.date,
    start_to: datetime.date,
    by_addr: dict,
) -> list:
    """
    Query Texas DSHS Online Asbestos Notification System for Houston (Harris County = 101,
    Houston = 25185) and extract full PDF details for any notification matching an Atlas parcel.
    Includes retry logic for transient Texas DSHS portal latency.
    """
    first_html = None
    opener = None
    for attempt in range(1, 4):
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj),
            urllib.request.HTTPSHandler(context=SSL_CTX),
        )
        opener.addheaders = [("User-Agent", "Mozilla/5.0")]
        try:
            opener.open(f"{DSHS_BASE_URL}/login.do", timeout=25)
            cookie_data = urllib.parse.urlencode(
                {"param_rsdIndex": "null", "source": "public", "continue": "Next"}
            ).encode()
            opener.open(f"{DSHS_BASE_URL}/checkCookies.do", data=cookie_data, timeout=25)
            opener.open(f"{DSHS_BASE_URL}/notificationSearchAction.do?from=loginPage", timeout=25)

            search_data = urllib.parse.urlencode(
                {
                    "param_rsdIndex": "null",
                    "licNumber": "",
                    "persDetail_name": "",
                    "facility_name": "",
                    "facility_streetNo": "",
                    "facility_physicalAddress": "",
                    "facility_county": "101",
                    "persDetail_state": "",
                    "facility_city": "25185",
                    "facility_zip": "",
                    "work_abtStartDateStrFrom": start_from.strftime("%m/%d/%Y"),
                    "work_abtStartDateStrTo": start_to.strftime("%m/%d/%Y"),
                    "work_abtEndDateStrFrom": "",
                    "work_abtEndDateStrTo": "",
                    "search": "Search",
                }
            ).encode()
            r = opener.open(f"{DSHS_BASE_URL}/notificationSearchAction.do", data=search_data, timeout=45)
            first_html = r.read().decode("utf-8", errors="ignore")
            break
        except Exception as e:
            print(f"  [WARN] DSHS search attempt {attempt}/3 failed: {e}", flush=True)

    if not first_html or opener is None:
        return []

    pages_html = [first_html]
    page_urls = []
    for m in re.finditer(r'href=["\']([^"\']*notificationSearchAction\.do\?[^"\']*page=\d+[^"\']*)["\']', first_html):
        pu = m.group(1).replace("&amp;", "&")
        if pu not in page_urls:
            page_urls.append(pu)
    for pu in page_urls[:10]:
        full_pu = pu if pu.startswith("http") else f"{DSHS_BASE_URL}/{pu.lstrip('/')}"
        try:
            rp = opener.open(full_pu, timeout=15)
            pages_html.append(rp.read().decode("utf-8", errors="ignore"))
        except Exception:
            pass

    matched_notifications = []
    seen_notif_nums = set()

    for html in pages_html:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)
        for r_html in rows:
            m_link = re.search(r"(notificationPrintAction\.do\?licId=\d+)", r_html)
            if not m_link:
                continue
            cols = [
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ")).strip()
                for c in re.findall(r"<td[^>]*>(.*?)</td>", r_html, re.S | re.I)
            ]
            if len(cols) < 5:
                continue
            notif_no, fac_name, fac_addr, start_dt_str, end_dt_str = cols[:5]
            if notif_no in seen_notif_nums:
                continue
            norm_a = normalize_address(fac_addr)
            if norm_a not in by_addr:
                continue
            seen_notif_nums.add(notif_no)

            def to_iso(mmddyyyy: str) -> str:
                m_d = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", mmddyyyy.strip())
                return f"{m_d.group(3)}-{m_d.group(1)}-{m_d.group(2)}" if m_d else mmddyyyy

            item = {
                "notification_number": notif_no,
                "facility_name": fac_name,
                "raw_address": fac_addr,
                "norm_address": norm_a,
                "start_date": to_iso(start_dt_str),
                "end_date": to_iso(end_dt_str),
                "print_action_url": f"{DSHS_BASE_URL}/{m_link.group(1)}",
            }

            try:
                r_sum = opener.open(item["print_action_url"], timeout=15)
                b_sum = r_sum.read().decode("utf-8", errors="ignore")
                note_links = re.findall(
                    r'(notificationSearchResultAction\.do\?noteId=\d+)', b_sum
                )
                if note_links:
                    latest_note_url = f"{DSHS_BASE_URL}/{note_links[-1]}"
                    item["pdf_url"] = latest_note_url
                    r_pdf = opener.open(latest_note_url, timeout=15)
                    pdf_info = parse_dshs_pdf_text(r_pdf.read())
                    item.update(pdf_info)
            except Exception as e:
                print(f"  [WARN] Could not parse DSHS PDF for {notif_no}: {e}", flush=True)

            matched_notifications.append(item)

    return matched_notifications


def is_interior_only_demo(use_desc: str) -> bool:
    """Return True if a permit description is strictly a non-structural interior partition/buildout remodel."""
    u = (use_desc or "").upper()
    if "NON-LOAD BEARING" in u or "NON LOAD BEARING" in u or "NONFIRE RATED" in u or "NON-FIRE RATED" in u:
        return True
    if "INTERIOR DEMO" in u and "BLD DEMO" not in u and "SEWER" not in u:
        return True
    if "SWIMMING POOL DEMOLITION" in u:
        return True
    return False


def is_garage_or_accessory_only_demo(use_desc: str) -> bool:
    """Return True if a demolition permit is explicitly restricted to a detached garage or accessory structure."""
    u = (use_desc or "").upper()
    if "GARAGE ONLY" in u or "(GARAGE" in u or "- GA" in u or "DETACHED GARAGE" in u or "CARPORT ONLY" in u:
        return True
    return False


def is_structural_or_sewer_demo(permit_desc: str, use_desc: str, permit_type: str) -> bool:
    """Return True if a City of Houston permit is a building demolition or sewer disconnection for demolition."""
    if is_interior_only_demo(use_desc):
        return False
    u = (use_desc or "").upper()
    pd = (permit_desc or "").upper()
    pt = (permit_type or "").upper()
    if pt in ("SD", "4D") or "DEMOLITION" in pd or "DGR BLDG DEMO" in pd:
        return True
    if "SEWER DISC" in u or "BLD DEMO" in u or "DEMO RES" in u or "DEMO BLD" in u or "RES DEMO" in u:
        return True
    if "D.B./RESIENTIAL/DEMO" in u or "DEMO/RESIDENTIAL" in u:
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Fetch live City of Houston permits & Texas DSHS asbestos filings.")
    parser.add_argument("--lookback-days", type=int, default=45, help="Days to look back for COH DEMO/SEWER permits.")
    parser.add_argument("--weekly-xlsx-count", type=int, default=4, help="Number of recent weekly XLSX files to ingest.")
    args = parser.parse_args()

    print("1. Loading Atlas buildings from", BUILDINGS_GEOJSON, flush=True)
    with open(BUILDINGS_GEOJSON, "r", encoding="utf-8") as f:
        gj_data = json.load(f)

    if enrich_kirby_mansion_metadata(gj_data):
        print("   Enriched 2000 Smith St (John Henry Kirby Mansion, HCAD 0152300000001) metadata.", flush=True)
        with open(BUILDINGS_GEOJSON, "w", encoding="utf-8") as f:
            json.dump(gj_data, f, separators=(",", ":"))
        update_search_index_for_kirby_mansion()

    by_addr = {}
    by_hcad = {}
    for feat in gj_data.get("features", []):
        p = feat.get("properties") or {}
        centroid = compute_feature_centroid(feat.get("geometry"))
        p_with_geom = dict(p)
        p_with_geom["_centroid"] = centroid
        na = normalize_address(p.get("address", ""))
        if na:
            by_addr.setdefault(na, []).append(p_with_geom)
        hcad = str(p.get("hcad_num") or "").strip()
        if hcad:
            by_hcad[hcad] = p_with_geom

    # Also index search_index.json so all 26,419 countywide structures are covered
    if SEARCH_INDEX_JSON.exists():
        with open(SEARCH_INDEX_JSON, "r", encoding="utf-8") as f:
            s_idx = json.load(f)
        s_items = s_idx if isinstance(s_idx, list) else s_idx.get("items", [])
        for item in s_items:
            hcad = str(item.get("hcad_num") or item.get("h") or "").strip()
            if hcad and hcad not in by_hcad:
                addr = item.get("address") or item.get("a") or ""
                na = normalize_address(addr)
                lng = item.get("lng") or item.get("x") or -95.3698
                lat = item.get("lat") or item.get("y") or 29.7604
                rec = {
                    "id": item.get("id") or f"bld_{hcad}",
                    "hcad_num": hcad,
                    "address": addr,
                    "year_built": item.get("year_built") or item.get("y") or 0,
                    "landmark_name": item.get("landmark_name") or item.get("l") or "",
                    "historic_district": item.get("historic_district") or item.get("d") or "",
                    "neighborhood": item.get("neighborhood") or item.get("n") or "",
                    "good_brick_summary": item.get("good_brick_summary") or "",
                    "style": item.get("style") or "",
                    "architect": item.get("architect") or "",
                    "_centroid": [round(float(lng), 6), round(float(lat), 6)],
                }
                by_hcad[hcad] = rec
                if na:
                    by_addr.setdefault(na, []).append(rec)

    print(f"   Indexed {len(by_addr):,} normalized street addresses across {len(by_hcad):,} structures.", flush=True)

    today = datetime.date.today()
    bdt = today - datetime.timedelta(days=args.lookback_days)
    print(f"2. Querying City of Houston WebFOCUS Sold Permits ({bdt} to {today})...", flush=True)
    combined_coh = fetch_coh_permits_parallel(lookback_days=args.lookback_days)
    print(f"   Retrieved {len(combined_coh):,} unique City of Houston DEMO / SD permit records.", flush=True)

    # Filter to rows that match an Atlas building first, then fetch details in parallel
    candidate_coh = [r for r in combined_coh.values() if r["norm_address"] in by_addr]
    print(f"   Matched {len(candidate_coh)} COH permit records to Atlas addresses; fetching drilldown details...", flush=True)

    matched_coh_by_hcad = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        detail_futs = {
            pool.submit(fetch_coh_permit_detail, r["project_no"], r.get("permit_type", "")): r
            for r in candidate_coh
        }
        for fut in concurrent.futures.as_completed(detail_futs):
            r = detail_futs[fut]
            detail = fut.result()
            na = r["norm_address"]
            use_d = detail.get("use_desc") or r.get("use_desc", "")
            is_sewer_disc = "SEWER DISC" in use_d.upper()
            is_garage = is_garage_or_accessory_only_demo(use_d)
            is_struct = is_structural_or_sewer_demo(
                r.get("permit_desc", ""),
                use_d,
                r.get("permit_type", ""),
            )
            if is_sewer_disc:
                pt_label = "Sewer Disconnection Permit"
            elif is_garage:
                pt_label = "Garage / Accessory Demolition Permit"
            elif is_struct:
                pt_label = "Building Demolition Permit"
            else:
                pt_label = r.get("permit_desc") or "City Permit"

            merged_permit = {
                "project_no": r["project_no"],
                "date": detail.get("date") or today.strftime("%Y-%m-%d"),
                "permit_type": r.get("permit_type") or detail.get("permit_type", ""),
                "permit_type_label": pt_label,
                "permit_desc": r.get("permit_desc", ""),
                "use_desc": use_d,
                "fcc_group": detail.get("fcc_group", ""),
                "owner_occupant": detail.get("owner_occupant") or r.get("owner_occupant", ""),
                "buyer": detail.get("buyer", ""),
                "buyer_address": detail.get("buyer_address", ""),
                "valuation": detail.get("valuation_formatted") or r.get("valuation", ""),
                "job_address": detail.get("job_address") or r.get("raw_address", ""),
                "is_sewer_disconnect": is_sewer_disc,
                "is_garage_only": is_garage,
                "is_structural_demo": is_struct,
                "is_interior_only": is_interior_only_demo(use_d),
            }
            for bld in by_addr[na]:
                hcad = str(bld.get("hcad_num") or bld.get("id") or "")
                matched_coh_by_hcad.setdefault(hcad, []).append(merged_permit)

    print(f"3. Ingesting recent weekly Sold Permits .xlsx spreadsheets ({args.weekly_xlsx_count} weeks)...", flush=True)
    weekly_rows = fetch_recent_weekly_xlsx_permits(max_files=args.weekly_xlsx_count)
    weekly_matched_by_hcad = {}
    for r in weekly_rows:
        na = r["norm_address"]
        if na not in by_addr:
            continue
        for bld in by_addr[na]:
            hcad = str(bld.get("hcad_num") or bld.get("id") or "")
            weekly_matched_by_hcad.setdefault(hcad, []).append(r)

    print("4. Querying Texas DSHS Asbestos Abatement & Demolition Notifications for Houston...", flush=True)
    dshs_start = today - datetime.timedelta(days=45)
    dshs_end = today + datetime.timedelta(days=90)
    dshs_matched = fetch_dshs_houston_notifications(dshs_start, dshs_end, by_addr)
    print(f"   Matched {len(dshs_matched)} Texas DSHS notifications to Atlas structures.", flush=True)

    dshs_by_hcad = {}
    for item in dshs_matched:
        na = item["norm_address"]
        for bld in by_addr.get(na, []):
            hcad = str(bld.get("hcad_num") or bld.get("id") or "")
            dshs_by_hcad.setdefault(hcad, []).append(item)

    # Carry forward active DSHS notifications from existing ALERTS_OUTPUT_JSON if upstream DSHS portal timed out
    if not dshs_by_hcad and ALERTS_OUTPUT_JSON.exists():
        try:
            with open(ALERTS_OUTPUT_JSON, "r", encoding="utf-8") as f:
                prev_alerts = json.load(f).get("alerts", [])
            cutoff_iso = (today - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
            carried = 0
            for pa in prev_alerts:
                ph = str(pa.get("hcad_num") or "")
                for dn in pa.get("dshs_notifications") or []:
                    if str(dn.get("end_date") or "9999") >= cutoff_iso:
                        dshs_by_hcad.setdefault(ph, []).append(dn)
                        carried += 1
            if carried:
                print(f"   Carried forward {carried} active DSHS notifications from previous snapshot.", flush=True)
        except Exception:
            pass

    all_flagged_hcads = sorted(set(matched_coh_by_hcad.keys()) | set(dshs_by_hcad.keys()))
    alerts_list = []

    for hcad in all_flagged_hcads:
        bld = by_hcad.get(hcad)
        if not bld:
            continue
        coh_list = matched_coh_by_hcad.get(hcad, [])
        dshs_list = dshs_by_hcad.get(hcad, [])

        dedup_coh = []
        seen_c = set()
        for c in sorted(coh_list, key=lambda x: x.get("date", ""), reverse=True):
            k = (c["project_no"], c["permit_desc"])
            if k not in seen_c:
                seen_c.add(k)
                dedup_coh.append(c)

        structural_coh = [c for c in dedup_coh if c["is_structural_demo"] and not c["is_interior_only"]]
        main_bld_coh = [c for c in structural_coh if not c.get("is_garage_only")]
        garage_only_coh = [c for c in structural_coh if c.get("is_garage_only")]
        has_structural_or_sewer = len(structural_coh) > 0
        has_main_bld_demo = len(main_bld_coh) > 0
        has_sewer_disc = any(c["is_sewer_disconnect"] for c in main_bld_coh)
        has_demo_permit = any(
            c["permit_type"] in ("SD", "4D") or "DEMOLITION" in c["permit_desc"].upper()
            for c in main_bld_coh
        )
        has_dshs_demo = any("DEMOLITION" in (d.get("work_type") or "").upper() for d in dshs_list)
        has_dshs = len(dshs_list) > 0

        if not has_structural_or_sewer and not has_dshs:
            continue

        yr = int(bld.get("year_built") or bld.get("eff_year_built") or 0)
        if has_main_bld_demo or has_dshs_demo or (has_sewer_disc and has_dshs):
            severity = "critical"
        else:
            severity = "watch"

        primary_coh = (
            main_bld_coh[0]
            if main_bld_coh
            else (garage_only_coh[0] if garage_only_coh else (dedup_coh[0] if dedup_coh else None))
        )

        if has_sewer_disc and has_dshs and primary_coh:
            severity_label = "Critical Alert: Sewer Disconnection + Asbestos Abatement"
            badge_short = "Sewer Disc + Asbestos"
            headline = (
                f"City sewer disconnection permit (#{primary_coh['project_no']}, issued {primary_coh['date']}) "
                f"paired with Texas DSHS asbestos abatement filing (#{dshs_list[0]['notification_number']}, "
                f"scheduled {dshs_list[0]['start_date']} to {dshs_list[0]['end_date']})."
            )
        elif has_dshs_demo:
            severity_label = "Critical Alert: Texas DSHS Demolition Notification"
            badge_short = "DSHS Demolition Filing"
            headline = (
                f"Texas DSHS demolition notification (#{dshs_list[0]['notification_number']}) "
                f"scheduled {dshs_list[0]['start_date']} to {dshs_list[0]['end_date']}."
            )
        elif has_demo_permit and has_sewer_disc and primary_coh:
            severity_label = "Critical Alert: Active Demolition & Sewer Disconnection Permits"
            badge_short = "Demo + Sewer Disc"
            headline = (
                f"City of Houston demolition and sewer disconnection permits (#{primary_coh['project_no']}, "
                f"issued {primary_coh['date']}: {primary_coh['use_desc']})."
            )
        elif has_demo_permit and primary_coh:
            severity_label = "Critical Alert: City of Houston Demolition Permit Issued"
            badge_short = "Active Demo Permit"
            headline = (
                f"City of Houston demolition permit (#{primary_coh['project_no']}, "
                f"issued {primary_coh['date']}: {primary_coh['use_desc']})."
            )
        elif has_sewer_disc and primary_coh:
            severity_label = "Critical Alert: Pre-Demolition Sewer Disconnection Permit"
            badge_short = "Pre-Demo Sewer Disc"
            headline = (
                f"City of Houston sewer disconnection permit (#{primary_coh['project_no']}, "
                f"issued {primary_coh['date']}: {primary_coh['use_desc']}), a prerequisite for demolition."
            )
        elif garage_only_coh and primary_coh:
            severity_label = "Active Watch: Garage / Accessory Demolition Permit"
            badge_short = "Garage Demo Only"
            headline = (
                f"City of Houston accessory/garage demolition permit (#{primary_coh['project_no']}, "
                f"issued {primary_coh['date']}: {primary_coh['use_desc']})."
            )
        elif has_dshs:
            wt = dshs_list[0].get("work_type") or "Asbestos Abatement"
            wt_clean = wt.lower().replace("notification", "").strip()
            severity_label = f"Active Watch: Texas DSHS {wt} Filing"
            badge_short = f"DSHS {wt}"
            headline = (
                f"Texas DSHS {wt_clean} notification (#{dshs_list[0]['notification_number']}) "
                f"scheduled {dshs_list[0]['start_date']} to {dshs_list[0]['end_date']}."
            )
        else:
            severity_label = "Active Permit Watch"
            badge_short = "Active Permit Watch"
            headline = "Recent permit activity recorded on this historic parcel."

        latest_date = max(
            [c.get("date", "") for c in structural_coh]
            + [d.get("filing_date") or d.get("start_date", "") for d in dshs_list]
            + [""]
        )

        centroid = bld.get("_centroid") or [-95.3698, 29.7604]
        b_name = bld.get("landmark_name") or bld.get("building_name") or ""
        if hcad == "0152300000001":
            b_name = "John Henry Kirby Mansion"
        title = b_name or bld.get("address") or f"HCAD {hcad}"

        preservation_note = ""
        if hcad == "0152300000001":
            preservation_note = (
                "Built in 1894 for businessman J.S. Price and purchased by lumber magnate John Henry Kirby in 1896, "
                "the 36-room Tudor Revival mansion at 2000 Smith Street achieved its landmark appearance in a 1928 "
                "remodeling designed by architect James Ruskin Bailey (1979 Preservation Houston Good Brick Award). "
                "A City of Houston sewer disconnection permit (#26090093, 'BLD DEMO/SEWER DISC', occupant '*KIRBY MANSION') "
                "was issued October 14, 2026 to applicant Robert Camacho, followed by Texas DSHS Notification #2026005825 "
                "scheduling abatement of 5,010 sq ft of Regulated Asbestos-Containing Material (RACM) from October 27 "
                "through November 6, 2026 by operator AAR Incorporated for owner OB Texas Properties LLC (Omair Bashir). "
                "Although the mansion lacks local Protected Landmark designation, Preservation Houston is actively "
                "monitoring the property and seeking dialogue with the owner."
            )

        alert_obj = {
            "hcad_num": hcad,
            "building_id": bld.get("id") or bld.get("building_id") or "",
            "title": title,
            "building_name": b_name,
            "address": bld.get("address") or "",
            "year_built": yr,
            "yr_remodel": bld.get("yr_remodel") or 0,
            "style": bld.get("style") or "",
            "architect": bld.get("architect") or "",
            "neighborhood": bld.get("neighborhood") or bld.get("super_neighborhood") or "",
            "historic_district": bld.get("historic_district") or "",
            "landmark_name": bld.get("landmark_name") or b_name,
            "good_brick_summary": bld.get("good_brick_summary") or "",
            "owner": bld.get("owner") or "",
            "lng": centroid[0],
            "lon": centroid[0],
            "lat": centroid[1],
            "severity": severity,
            "severity_label": severity_label,
            "badge": badge_short,
            "headline": headline,
            "preservation_note": preservation_note,
            "latest_filing_date": latest_date,
            "coh_permits": structural_coh if structural_coh else dedup_coh,
            "dshs_notifications": dshs_list,
        }
        alerts_list.append(alert_obj)

    alerts_list.sort(
        key=lambda a: (
            0 if a["severity"] == "critical" else 1,
            0 if (a["landmark_name"] or a["good_brick_summary"]) else 1,
            a["year_built"] if a["year_built"] >= 1836 else 9999,
        )
    )

    # Deduplicate multi-building campus/lot addresses into a single primary alert entry
    # while preserving all related_hcads so every footprint on the site gets the Inspector banner
    dedup_alerts = []
    by_norm_addr_alert = {}
    for a in alerts_list:
        na = normalize_address(a["address"])
        if na and na in by_norm_addr_alert:
            primary = by_norm_addr_alert[na]
            if a["hcad_num"] not in primary["related_hcads"]:
                primary["related_hcads"].append(a["hcad_num"])
            if a["building_id"] and a["building_id"] not in primary["related_building_ids"]:
                primary["related_building_ids"].append(a["building_id"])
        else:
            a["related_hcads"] = [a["hcad_num"]]
            a["related_building_ids"] = [a["building_id"]] if a["building_id"] else []
            dedup_alerts.append(a)
            if na:
                by_norm_addr_alert[na] = a
    alerts_list = dedup_alerts

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    alerts_payload = {
        "updated_at": now_iso,
        "generated_at": now_iso,
        "lookback_days": args.lookback_days,
        "summary": {
            "total_flagged_buildings": len(alerts_list),
            "critical_count": sum(1 for a in alerts_list if a["severity"] == "critical"),
            "watch_count": sum(1 for a in alerts_list if a["severity"] == "watch"),
            "coh_permits_matched": sum(len(a["coh_permits"]) for a in alerts_list),
            "dshs_notifications_matched": sum(len(a["dshs_notifications"]) for a in alerts_list),
        },
        "alerts": alerts_list,
    }

    with open(ALERTS_OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(alerts_payload, f, indent=2)
    print(f"5. Wrote {len(alerts_list)} building alerts to {ALERTS_OUTPUT_JSON}", flush=True)

    all_hcads_to_update = set(matched_coh_by_hcad.keys()) | set(weekly_matched_by_hcad.keys()) | set(dshs_by_hcad.keys())
    by_prefix = {}
    for hcad in all_hcads_to_update:
        clean_h = re.sub(r"\D", "", str(hcad))
        if len(clean_h) >= 10:
            by_prefix.setdefault(clean_h[:3], []).append(clean_h)

    shards_updated = 0
    for prefix3, hcads in by_prefix.items():
        shard_path = PERMITS_DIR / f"permits_{prefix3}.json"
        if shard_path.exists():
            with open(shard_path, "r", encoding="utf-8") as f:
                shard_data = json.load(f)
        else:
            shard_data = {}

        shard_modified = False
        for hcad in hcads:
            rec = shard_data.get(hcad) or {}
            p_list = list(rec.get("p") or [])
            existing_ids = {str(p.get("id") or "") for p in p_list}

            for cp in matched_coh_by_hcad.get(hcad, []):
                pid = str(cp["project_no"])
                if pid and pid not in existing_ids:
                    cat = "demo" if cp["is_structural_demo"] else "remodel"
                    tp_label = (
                        "Sewer Disconnect / Demo Prep"
                        if cp["is_sewer_disconnect"]
                        else ("Demolition Permit" if cp["is_structural_demo"] else (cp["permit_desc"] or "City Permit"))
                    )
                    desc_parts = [cp["use_desc"]]
                    if cp.get("fcc_group"):
                        desc_parts.append(f"FCC: {cp['fcc_group']}")
                    if cp.get("buyer"):
                        desc_parts.append(f"Contractor: {cp['buyer']}")
                    if cp.get("owner_occupant"):
                        desc_parts.append(f"Applicant/Occupant: {cp['owner_occupant']}")
                    p_list.insert(
                        0,
                        {
                            "id": pid,
                            "dt": cp["date"],
                            "cat": cat,
                            "tp": tp_label,
                            "d": " · ".join(x for x in desc_parts if x),
                            "src": "coh_live",
                        },
                    )
                    existing_ids.add(pid)
                    shard_modified = True

            for wp in weekly_matched_by_hcad.get(hcad, []):
                pid = str(wp["project_no"])
                if pid and pid not in existing_ids:
                    is_demo = is_structural_or_sewer_demo(wp["permit_desc"], wp["use_desc"], "")
                    cat = "demo" if is_demo else "remodel"
                    p_list.insert(
                        0,
                        {
                            "id": pid,
                            "dt": wp["date"],
                            "cat": cat,
                            "tp": wp["permit_desc"] or "City Permit",
                            "d": wp["use_desc"],
                            "src": "coh_weekly",
                        },
                    )
                    existing_ids.add(pid)
                    shard_modified = True

            for dn in dshs_by_hcad.get(hcad, []):
                nid = f"DSHS-{dn['notification_number']}"
                if nid not in existing_ids:
                    wt = dn.get("work_type") or "Asbestos Notification"
                    desc_parts = [
                        f"Scheduled {dn['start_date']} to {dn['end_date']}",
                    ]
                    if dn.get("work_description"):
                        desc_parts.append(dn["work_description"])
                    if dn.get("abatement_contractor"):
                        desc_parts.append(f"Abatement Contractor: {dn['abatement_contractor']}")
                    if dn.get("facility_owner"):
                        desc_parts.append(f"Facility Owner: {dn['facility_owner']}")
                    p_list.insert(
                        0,
                        {
                            "id": nid,
                            "dt": dn.get("filing_date") or dn["start_date"],
                            "cat": "demo" if "DEMOLITION" in wt.upper() else "asbestos",
                            "tp": f"Texas DSHS {wt}",
                            "d": " · ".join(desc_parts),
                            "src": "tx_dshs",
                        },
                    )
                    existing_ids.add(nid)
                    shard_modified = True

            if hcad == "0152300000001" and (rec.get("yr") != 1894 or rec.get("remod") != 1928):
                rec["yr"] = 1894
                rec["remod"] = 1928
                shard_modified = True

            if shard_modified:
                p_list.sort(key=lambda x: str(x.get("dt") or ""), reverse=True)
                rec["p"] = p_list
                shard_data[hcad] = rec

        if shard_modified:
            with open(shard_path, "w", encoding="utf-8") as f:
                json.dump(shard_data, f, separators=(",", ":"))
            shards_updated += 1

    print(f"6. Updated {shards_updated} permit shard(s) in {PERMITS_DIR}", flush=True)
    for a in alerts_list:
        print(
            f"   [{a['severity'].upper()}] {a['address']} ({a['title']}, built {a['year_built']}, HCAD {a['hcad_num']}): {a['headline']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
