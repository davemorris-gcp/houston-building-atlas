"""
Curated National Register of Historic Places (NRHP) nomination metadata for Harris County
(`overlays.json["nrhp_properties"]`) and HAIF architect attribution patterns.

Sources:
- Texas Historical Commission (THC) Historic Sites Atlas NRHP Nomination PDFs
- National Park Service (NPS) NRIS / NPGallery Nomination Forms (including the 1979 Courtlandt Place HD,
  1983/1984/1987/1990 Houston Heights Multiple Resource Area, and 1994 Westmoreland HD nominations)
- City of Houston Archaeological & Historical Commission (HAHC) Designation Reports
"""

import re

CURATED_NRHP_NOMINATION_METADATA: dict[str, dict] = {
    # 17 NRHP properties that previously had year_built = 0 or post-1985 HCAD remodel/campus years
    "84000388": {
        "year_built": 1927,
        "architect": "William Ward Watkin",
        "style": "Tudor Revival & Italian Romanesque",
        "hcad_num": "1233520010001",
    },
    "84001759": {
        "year_built": 1913,
        "architect": "Mauran, Russell & Crowell",
        "style": "Georgian Revival / Italian Renaissance",
        "hcad_num": "1240560000010",
    },
    "84001777": {
        "year_built": 1912,
        "style": "Queen Anne / L-Plan Cottage",
        "hcad_num": "0201790000007",
    },
    "00000291": {
        "year_built": 1947,
        "architect": "Alfred C. Finn",
        "builder": "W. S. Bellows Construction Corp.",
        "style": "Art Moderne / Late Art Deco",
        "hcad_num": "0011370000011",
    },
    "94000354": {
        "year_built": 1936,
        "architect": "Joseph Finger",
        "style": "Art Moderne",
        "hcad_num": "0131910000001",
    },
    "87002251": {
        "year_built": 1911,
        "style": "Craftsman Bungalow",
        "hcad_num": "0201520000004",
    },
    "83004425": {
        "year_built": 1926,
        "architect": "Maurice J. Sullivan",
        "style": "Spanish Eclectic / Lombard Romanesque",
        "hcad_num": "0202140000033",
    },
    "02000729": {
        "year_built": 1932,
        "architect": "J. G. McKenzie (City of Houston Bridge Engineer)",
        "style": "Concrete Girder & Steel Truss Bridge",
    },
    "00000883": {
        "year_built": 1971,
        "architect": "Philip Johnson, Howard Barnstone & Eugene Aubry",
        "style": "Modernist",
        "hcad_num": "0502350020001",
    },
    "04000547": {
        "year_built": 1902,
        "architect": "F. L. Dormant (City Engineer)",
        "style": "Romanesque Revival Industrial",
        "hcad_num": "0031350000001",
    },
    "05000858": {
        "year_built": 1925,
        "architect": "Lamar Q. Cato",
        "style": "Spanish Colonial Revival",
        "hcad_num": "0440970000166",
    },
    "07001131": {
        "year_built": 1937,
        "architect": "J. G. McKenzie (City of Houston Bridge Engineer)",
        "style": "PWA Moderne Concrete Arch Bridge",
    },
    "07001023": {
        "year_built": 1924,
        "architect": "J. C. McVea (City Engineer)",
        "style": "Classical Revival Concrete Arch Bridge",
    },
    "07001235": {
        "year_built": 1931,
        "architect": "J. G. McKenzie (City of Houston Bridge Engineer)",
        "style": "Art Deco Concrete Girder Bridge",
    },
    "07001234": {
        "year_built": 1925,
        "architect": "J. C. McVea (City Engineer)",
        "style": "Classical Revival Concrete Girder Bridge",
    },
    "11000981": {
        "year_built": 1931,
        "architect": "J. G. McKenzie (City of Houston Bridge Engineer)",
        "style": "Art Deco Concrete Girder Bridge",
    },
    "100004968": {
        "year_built": 1933,
        "architect": "Salisbury & McHale",
        "style": "Spanish Eclectic",
        "hcad_num": "0421330050001",
    },
    # Courtlandt Place NRHP nominations & other documented NRHP nominations
    "79002955": {
        "year_built": 1912,
        "architect": "Sanguinet & Staats",
        "style": "Colonial Revival",
    },
    "79002958": {
        "year_built": 1911,
        "architect": "Sanguinet & Staats",
        "style": "Colonial Revival",
    },
    "79002959": {
        "year_built": 1916,
        "architect": "Warren & Wetmore",
        "style": "Georgian Revival",
    },
    "79002960": {
        "year_built": 1914,
        "architect": "Sanguinet & Staats",
        "style": "Italian Renaissance Revival",
    },
    "79002963": {
        "year_built": 1861,
        "builder": "John Kennedy",
        "style": "19th-Century Commercial Brick",
    },
    "79002964": {
        "year_built": 1910,
        "architect": "Sanguinet & Staats",
        "style": "Tudor Revival",
    },
    "79002965": {
        "year_built": 1910,
        "architect": "C. D. Hill & Company",
        "style": "Colonial Revival",
    },
    "79002966": {
        "year_built": 1909,
        "architect": "Sanguinet & Staats",
        "style": "English Manorial / Tudor Revival",
    },
    "79002967": {
        "year_built": 1907,
        "architect": "Sanguinet & Staats",
        "style": "Neo-Classical / Chicago School",
    },
    "79002968": {
        "year_built": 1916,
        "architect": "Birdsall P. Briscoe",
        "style": "Georgian Revival",
    },
    "71000938": {
        "year_built": 1884,
        "architect": "Eugene T. Heiner",
        "style": "Victorian Renaissance Revival",
    },
    "75001991": {
        "year_built": 1924,
        "architect": "Ralph Adams Cram",
        "style": "Spanish Eclectic",
    },
    "77001448": {
        "year_built": 1911,
        "architect": "Warren & Wetmore",
        "style": "Beaux-Arts Classical",
    },
    "78002943": {
        "year_built": 1921,
        "architect": "Lamar Q. Cato",
        "style": "Early 20th-Century Commercial / Chicago School",
    },
    "78002947": {
        "year_built": 1913,
        "architect": "Mauran, Russell & Crowell",
        "style": "Beaux-Arts / Second Renaissance Revival",
    },
    "80004130": {
        "year_built": 1930,
        "architect": "Giesecke & Harris",
        "builder": "American Construction Company",
        "style": "Art Deco",
    },
    "80004131": {
        "year_built": 1898,
        "builder": "John Milroy",
        "style": "Queen Anne Victorian",
    },
    "83004474": {
        "year_built": 1898,
        "builder": "John Milroy",
        "style": "Queen Anne Victorian",
    },
    "84001816": {
        "year_built": 1895,
        "builder": "John Milroy",
        "style": "Queen Anne Victorian",
    },
    "82004858": {
        "year_built": 1927,
        "architect": "Alfred C. Finn",
        "style": "Colonial Revival",
    },
    "83003788": {
        "year_built": 1905,
        "architect": "Sanguinet & Staats",
        "style": "Queen Anne",
    },
    "83004427": {
        "year_built": 1918,
        "style": "Craftsman Bungalow",
    },
    "83004428": {
        "year_built": 1911,
        "architect": "Alonzo C. Pigg",
        "style": "Beaux-Arts / Classical Revival",
    },
    "83004438": {
        "year_built": 1927,
        "style": "Classical Revival",
    },
    "83004439": {
        "year_built": 1925,
        "architect": "Joseph Finger",
        "style": "Classical Revival Commercial",
    },
    "83004450": {
        "year_built": 1902,
        "style": "Queen Anne Cottage",
    },
    "83004454": {
        "year_built": 1910,
        "style": "L-Plan Folk Victorian",
    },
    "83004458": {
        "year_built": 1908,
        "style": "L-Plan Queen Anne Cottage",
    },
    "83004459": {
        "year_built": 1908,
        "style": "L-Plan Queen Anne Cottage",
    },
    "83004462": {
        "year_built": 1928,
        "architect": "City of Houston Water Department Engineers",
        "style": "Art Deco",
    },
    "83004467": {
        "year_built": 1906,
        "style": "Queen Anne / Colonial Revival",
    },
    "83004468": {
        "year_built": 1906,
        "style": "Queen Anne Cottage",
    },
    "83004469": {
        "year_built": 1905,
        "style": "Queen Anne Cottage",
    },
    "83004476": {
        "year_built": 1893,
        "builder": "Omaha & South Texas Land Company",
        "style": "19th-Century Brick Industrial Mill",
    },
    "83004479": {
        "year_built": 1916,
        "architect": "Alfred C. Finn",
        "style": "Italian Renaissance Revival",
    },
    "83004481": {
        "year_built": 1919,
        "architect": "Cram and Ferguson / William Ward Watkin",
        "style": "Gothic Revival",
    },
    "83004486": {
        "year_built": 1906,
        "architect": "J. A. Tempest",
        "style": "Renaissance Revival / Queen Anne",
    },
    "84001780": {
        "year_built": 1905,
        "style": "Queen Anne Cottage",
    },
    "84001788": {
        "year_built": 1905,
        "style": "Modified L-Plan Queen Anne Cottage",
    },
    "00000751": {
        "year_built": 1912,
        "architect": "Sanguinet & Staats",
        "style": "Neoclassical Revival / Beaux-Arts",
    },
    "84001791": {
        "year_built": 1900,
        "style": "L-Plan Folk Victorian Cottage",
    },
    "84001826": {
        "year_built": 1925,
        "architect": "Joseph Finger",
        "style": "Beaux-Arts / Classical Revival",
    },
    "84001847": {
        "year_built": 1910,
        "style": "Craftsman Bungalow",
    },
    "84001849": {
        "year_built": 1902,
        "builder": "Charlie and Dell Wunsche",
        "style": "Two-Story Railroad Frame Hotel",
    },
    "85002810": {
        "year_built": 1965,
        "architect": "Brown & Root / Charles Luckman Associates",
        "style": "Aerospace Modernist",
    },
    "85002815": {
        "year_built": 1965,
        "architect": "Charles Luckman Associates / Brown & Root",
        "style": "International Style / New Formalism",
    },
    "85002771": {
        "year_built": 1905,
        "architect": "Olle & Lorehn",
        "style": "Queen Anne / Colonial Revival",
    },
    "86000798": {
        "year_built": 1899,
        "architect": "Oscar J. Bourgeois",
        "style": "Romanesque Revival Civic",
    },
    "87002243": {
        "year_built": 1914,
        "builder": "George L. Burlingame",
        "style": "Craftsman Hip Bungalow",
    },
    "87002248": {
        "year_built": 1908,
        "builder": "Austin Copeland",
        "style": "Queen Anne",
    },
    "87002254": {
        "year_built": 1912,
        "style": "Craftsman Bungalow",
    },
    "87002258": {
        "year_built": 1912,
        "style": "Queen Anne / Colonial Revival",
    },
    "88001053": {
        "year_built": 1929,
        "architect": "William Ward Watkin",
        "style": "Colonial Revival",
    },
    "90001293": {
        "year_built": 1907,
        "architect": "H. C. Cooke & Company",
        "style": "Colonial Revival / Neo-Classical",
    },
    "91000889": {
        "year_built": 1923,
        "architect": "Birdsall P. Briscoe",
        "style": "Italian Renaissance Revival",
    },
    "93000844": {
        "year_built": 1939,
        "architect": "John F. Staub",
        "style": "Regency Revival",
    },
    "94000266": {
        "year_built": 1919,
        "architect": "Alfred C. Finn",
        "style": "Mediterranean Revival / Prairie",
    },
    "94000628": {
        "year_built": 1928,
        "architect": "W. D. Bordeaux",
        "style": "Spanish Colonial Revival",
    },
    "94000677": {
        "year_built": 1910,
        "architect": "Howell & Taylor",
        "builder": "James Stewart & Company",
        "style": "Heavy Timber & Brick Industrial Warehouse",
    },
    "96000067": {
        "year_built": 1893,
        "style": "Queen Anne Cottage",
    },
    "96000587": {
        "year_built": 1928,
        "architect": "Joseph Finger",
        "style": "Art Deco / Spanish Eclectic Fourplex",
    },
    "97000546": {
        "year_built": 1926,
        "style": "Craftsman Bungalow",
    },
    "97000550": {
        "year_built": 1928,
        "builder": "Charles Johnson",
        "style": "Tudor Revival / Craftsman",
    },
    "97001599": {
        "year_built": 1938,
        "architect": "Stayton Nunn & Milton McGinty",
        "style": "Renaissance Revival / Regency",
    },
    "98000428": {
        "year_built": 1923,
        "architect": "William Ward Watkin",
        "style": "Spanish Colonial Revival / Italian Villa",
    },
    "00001665": {
        "year_built": 1916,
        "builder": "American Construction Company",
        "style": "Early 20th-Century Reinforced Concrete Warehouse",
    },
    "05000387": {
        "year_built": 1919,
        "architect": "Birdsall P. Briscoe",
        "style": "Prairie / Craftsman",
    },
    "06001063": {
        "year_built": 1956,
        "architect": "Jeff McKissack",
        "style": "Folk Art Environment",
    },
    "06001066": {
        "year_built": 1926,
        "architect": "Joseph Finger",
        "style": "Classical Revival / Brick Gothic",
    },
    "07001098": {
        "year_built": 1914,
        "architect": "F. L. Dormant (City Engineer)",
        "style": "Classical Revival Concrete Arch Bridge",
    },
    "07001384": {
        "year_built": 1929,
        "architect": "Joseph Finger",
        "style": "Spanish Colonial Revival / Second Renaissance Revival",
    },
    "12000195": {
        "year_built": 1932,
        "architect": "Briscoe & Dixon",
        "style": "Colonial Revival",
    },
    "12000351": {
        "year_built": 1937,
        "architect": "Cameron Fairchild",
        "style": "Regency Revival",
    },
    "100001254": {
        "year_built": 1929,
        "architect": "Giesecke & Harris",
        "style": "Art Deco / Modernized 1966",
    },
    "100002842": {
        "year_built": 1961,
        "architect": "Anderson Todd",
        "style": "Modern / Miesian",
    },
    "100003488": {
        "year_built": 1940,
        "architect": "Joseph Finger",
        "style": "Art Moderne",
    },
}

HAIF_ARCHITECT_PATTERNS: list[tuple[str, str]] = [
    ("Karl Kamrath", "MacKie & Kamrath"),
    ("MacKie and Kamrath", "MacKie & Kamrath"),
    ("Mackie & Kamrath", "MacKie & Kamrath"),
    ("Fred MacKie", "MacKie & Kamrath"),
    ("Burdette Keeland", "Burdette Keeland"),
    ("Preston Bolton", "Preston M. Bolton"),
    ("Bolton & Barnstone", "Bolton & Barnstone"),
    ("Bolton and Barnstone", "Bolton & Barnstone"),
    ("Howard Barnstone", "Howard Barnstone"),
    ("Eugene Aubry", "Eugene Aubry"),
    ("Barnstone & Aubry", "Barnstone & Aubry"),
    ("Barnstone and Aubry", "Barnstone & Aubry"),
    ("Hugo Neuhaus", "Hugo V. Neuhaus Jr."),
    ("Cowell & Neuhaus", "Cowell & Neuhaus"),
    ("George Rustay", "Finger & Rustay"),
    ("Finger & Rustay", "Finger & Rustay"),
    ("Hamilton Brown", "Hamilton Brown"),
    ("William Floyd", "William Norman Floyd"),
    ("Norman Floyd", "William Norman Floyd"),
    ("Lucian Hood", "Lucian T. Hood Jr."),
    ("Lars Bang", "Lars Bang"),
    ("Robert C. Small", "Robert C. Small"),
    ("A. Carroll Brodnax", "A. Carroll Brodnax"),
    ("Carroll Brodnax", "A. Carroll Brodnax"),
    ("Wilson, Morris, Crain & Anderson", "Wilson, Morris, Crain & Anderson"),
    ("Golemon & Rolfe", "Golemon & Rolfe"),
    ("Golemon and Rolfe", "Golemon & Rolfe"),
    ("John S. Chase", "John S. Chase"),
    ("John Chase", "John S. Chase"),
    ("Stayton Nunn", "Stayton Nunn"),
    ("Milton McGinty", "Milton McGinty"),
    ("Nunn & McGinty", "Stayton Nunn & Milton McGinty"),
    ("Harvin Moore", "Harvin C. Moore"),
    ("Hermon Lloyd", "Lloyd & Morgan"),
    ("Lloyd & Morgan", "Lloyd & Morgan"),
    ("Lloyd and Morgan", "Lloyd & Morgan"),
    ("Cameron Fairchild", "Cameron Fairchild"),
    ("Irving Klein", "Irving R. Klein"),
    ("Joseph Finger", "Joseph Finger"),
    ("Alfred C. Finn", "Alfred C. Finn"),
    ("Alfred Finn", "Alfred C. Finn"),
    ("Kenneth Franzheim", "Kenneth Franzheim"),
    ("John F. Staub", "John F. Staub"),
    ("John Staub", "John F. Staub"),
    ("Birdsall Briscoe", "Birdsall P. Briscoe"),
    ("Birdsall P. Briscoe", "Birdsall P. Briscoe"),
    ("William Ward Watkin", "William Ward Watkin"),
    ("Wyatt C. Hedrick", "Wyatt C. Hedrick"),
    ("Wyatt Hedrick", "Wyatt C. Hedrick"),
    ("Sanguinet & Staats", "Sanguinet & Staats"),
    ("Sanguinet and Staats", "Sanguinet & Staats"),
    ("Lamar Q. Cato", "Lamar Q. Cato"),
    ("Lamar Cato", "Lamar Q. Cato"),
    ("Maurice Sullivan", "Maurice J. Sullivan"),
    ("Russell Brown", "Russell Brown Company"),
    ("Charles Tapley", "Charles Tapley"),
    ("Anderson Todd", "Anderson Todd"),
    ("Frank Lloyd Wright", "Frank Lloyd Wright"),
    ("Mies van der Rohe", "Ludwig Mies van der Rohe"),
    ("Philip Johnson", "Philip Johnson"),
    ("I. M. Pei", "I. M. Pei"),
    ("Cesar Pelli", "Cesar Pelli"),
    ("Gunnar Birkerts", "Gunnar Birkerts"),
    ("Alden Dow", "Alden B. Dow"),
    ("Paul Rudolph", "Paul Rudolph"),
    ("Ulrich Franzen", "Ulrich Franzen"),
]


def extract_haif_architects(title: str, excerpt: str, existing: list[str] | None = None) -> list[str]:
    out = list(existing or [])
    txt = f"{title or ''} {excerpt or ''}"
    for pat, canon in HAIF_ARCHITECT_PATTERNS:
        if re.search(r"\b" + re.escape(pat) + r"\b", txt, re.I):
            if canon not in out:
                out.append(canon)
    return out


def _norm_nrhp_addr(a: str) -> str:
    if not a:
        return ""
    s = re.sub(r"[^A-Z0-9\s]", " ", str(a).upper())
    s = re.sub(
        r"\b(STREET|ST|AVENUE|AVE|BOULEVARD|BLVD|DRIVE|DR|ROAD|RD|LANE|LN|COURT|CT|PLACE|PL|CIRCLE|CIR|PARKWAY|PKWY|HIGHWAY|HWY|FREEWAY|FWY)\b",
        "",
        s,
    )
    return re.sub(r"\s+", " ", s).strip()


def enrich_nrhp_and_cross_catalog(
    overlays_doc: dict,
    overrides: dict,
    buildings_fc: dict,
    hpo_doc: dict,
    tid_to_record: dict,
    by_hcad_idx: dict,
    by_addr_idx: dict,
    topics: list[dict],
    norm_addr_fn,
    norm_addr_nodir_fn,
    generic_stopwords: set[str],
) -> dict:
    by_hcad: dict[str, dict] = {}
    by_addr: dict[str, dict] = {}

    for feat in buildings_fc.get("features", []):
        p = feat.get("properties", {})
        h = str(p.get("hcad_num") or "").strip()
        a = _norm_nrhp_addr(p.get("address") or "")
        info = {
            "architect": p.get("architect") or "",
            "builder": p.get("builder") or "",
            "style": p.get("style") or "",
            "year_built": int(p.get("year_built") or 0),
        }
        if h:
            by_hcad[h] = dict(info)
        if a:
            by_addr[a] = dict(info)

    for layer_key in ("landmarks", "lost_houston"):
        for feat in overlays_doc.get(layer_key, {}).get("features", []):
            p = feat.get("properties", {})
            h = str(p.get("hcad_num") or "").strip()
            a = _norm_nrhp_addr(p.get("address") or "")
            for target in ([by_hcad.setdefault(h, {})] if h else []) + ([by_addr.setdefault(a, {})] if a else []):
                for k in ("architect", "builder", "style", "year_built"):
                    if p.get(k) and not target.get(k):
                        target[k] = p[k]

    for k, v in overrides.items():
        h = k.split("#")[0]
        a = _norm_nrhp_addr(v.get("address") or "")
        for target in ([by_hcad.setdefault(h, {})] if h else []) + ([by_addr.setdefault(a, {})] if a else []):
            for fld in ("architect", "builder", "style", "year_built"):
                if v.get(fld):
                    target[fld] = v[fld]

    for h, val in (hpo_doc or {}).get("by_hcad", {}).items():
        target = by_hcad.setdefault(h, {})
        it = val[0] if isinstance(val, list) and val and isinstance(val[0], dict) else (val if isinstance(val, dict) else {})
        if it.get("s") and not target.get("style"):
            target["style"] = it["s"]
        if it.get("y") and not target.get("year_built"):
            target["year_built"] = int(it["y"])
        if it.get("a") and not target.get("architect"):
            target["architect"] = it["a"]

    return _apply_nrhp_cross_links(
        overlays_doc, overrides, buildings_fc, by_hcad, by_addr,
        tid_to_record, by_hcad_idx, by_addr_idx, topics, norm_addr_fn, norm_addr_nodir_fn, generic_stopwords
    )


def _apply_nrhp_cross_links(
    overlays_doc: dict,
    overrides: dict,
    buildings_fc: dict,
    by_hcad: dict,
    by_addr: dict,
    tid_to_record: dict,
    by_hcad_idx: dict,
    by_addr_idx: dict,
    topics: list[dict],
    norm_addr_fn,
    norm_addr_nodir_fn,
    generic_stopwords: set[str],
) -> dict:
    slug_words_list = [(t["tid"], set(t["slug"].lower().split("-")), t["slug"].lower()) for t in (topics or [])]
    bld_by_hcad = {str(f["properties"].get("hcad_num") or "").strip(): f["properties"] for f in buildings_fc.get("features", []) if f.get("properties", {}).get("hcad_num")}
    nrhp_arch_count = 0
    nrhp_style_count = 0
    nrhp_haif_count = 0

    for feat in overlays_doc.get("nrhp_properties", {}).get("features", []):
        p = feat.get("properties", {})
        refnum = str(p.get("refnum") or "").strip()
        cur = CURATED_NRHP_NOMINATION_METADATA.get(refnum, {})
        if cur.get("hcad_num") and not p.get("hcad_num"):
            p["hcad_num"] = cur["hcad_num"]
        h = str(p.get("hcad_num") or "").strip()
        bid = str(p.get("building_id") or "").strip()
        addr = p.get("address") or ""
        a = _norm_nrhp_addr(addr)
        ch = by_hcad.get(h) or {}
        ca = by_addr.get(a) or {}
        cross = {
            "year_built": ch.get("year_built") or ca.get("year_built") or 0,
            "architect": ch.get("architect") or ca.get("architect") or "",
            "builder": ch.get("builder") or ca.get("builder") or "",
            "style": ch.get("style") or ca.get("style") or "",
        }

        if (not p.get("year_built") or int(p.get("year_built") or 0) > 1985) and cur.get("year_built"):
            p["year_built"] = int(cur["year_built"])
        elif (not p.get("year_built") or int(p.get("year_built") or 0) > 1985) and cross.get("year_built"):
            cyr = int(cross["year_built"])
            if 1836 <= cyr <= 1985:
                p["year_built"] = cyr
        elif cur.get("year_built") and abs(int(p.get("year_built") or 0) - int(cur["year_built"])) >= 15:
            p["year_built"] = int(cur["year_built"])

        arch = cross.get("architect") or cur.get("architect") or p.get("architect") or ""
        bldr = cross.get("builder") or cur.get("builder") or p.get("builder") or ""
        style = cross.get("style") or cur.get("style") or p.get("style") or ""
        if arch:
            p["architect"] = arch
        if bldr:
            p["builder"] = bldr
        if style:
            p["style"] = style
        if p.get("architect") or p.get("builder"):
            nrhp_arch_count += 1
        if p.get("style"):
            nrhp_style_count += 1

        # Propagate curated NRHP metadata onto overrides & buildings.geojson when missing
        if h and cur:
            for target_dict in [overrides.get(h), bld_by_hcad.get(h)]:
                if not target_dict:
                    continue
                if cur.get("architect") and not target_dict.get("architect"):
                    target_dict["architect"] = cur["architect"]
                if cur.get("builder") and not target_dict.get("builder"):
                    target_dict["builder"] = cur["builder"]
                if cur.get("style") and not target_dict.get("style"):
                    target_dict["style"] = cur["style"]
                tyr = int(target_dict.get("year_built") or 0)
                if cur.get("year_built") and (tyr == 0 or tyr > 1985 or target_dict.get("year_source") in ("nrhp", "landmark")):
                    target_dict["year_built"] = int(cur["year_built"])
                    target_dict["year_source"] = "archival"

        # Match HAIF threads for NRHP property
        if tid_to_record:
            tids = list(
                by_hcad_idx.get(h)
                or by_hcad_idx.get(bid)
                or (by_addr_idx.get(norm_addr_fn(addr)) if addr else None)
                or (by_addr_idx.get(norm_addr_nodir_fn(addr)) if addr else None)
                or []
            )
            if not tids and slug_words_list:
                nm = re.sub(r"[^a-z0-9\s]", " ", (p.get("name") or "").lower())
                toks = [w for w in nm.split() if len(w) >= 4 and w not in generic_stopwords]
                if len(toks) >= 2:
                    phrase = "-".join(toks[:3])
                    for tid, sw, slug_str in slug_words_list:
                        if phrase in slug_str or (all(tk in sw for tk in toks[:2]) and len(toks[0]) >= 5):
                            tids.append(tid)
                            if len(tids) >= 3:
                                break
            if tids:
                p["haif_threads"] = [
                    {
                        "tid": tid_to_record[tid]["tid"],
                        "title": tid_to_record[tid]["title"],
                        "subforum": tid_to_record[tid]["subforum"],
                        "url": tid_to_record[tid]["url"],
                        "wb_url": tid_to_record[tid]["wb_url"],
                        "date": tid_to_record[tid]["date"],
                        "excerpt": tid_to_record[tid]["excerpt"],
                        "is_demo": tid_to_record[tid]["is_demo"],
                    }
                    for tid in tids[:3]
                    if tid in tid_to_record
                ]
                if p["haif_threads"]:
                    nrhp_haif_count += 1
                    for tid in tids[:3]:
                        if tid in tid_to_record and h and tid not in by_hcad_idx.get(h, []):
                            by_hcad_idx.setdefault(h, []).append(tid)

    return {
        "nrhp_arch_count": nrhp_arch_count,
        "nrhp_style_count": nrhp_style_count,
        "nrhp_haif_count": nrhp_haif_count,
    }


