"""Fetches and builds compact WebP thumbs + preview plates for the 6 unique UT PCL Houston Sanborn volumes
(1877, 1917 Supp, 1919 Supp, 1924 Vol. 2, 1924 Vol. 3, and 1948 Vol. 6 - 252 sheets not digitized at LOC)
and merges them chronologically into app/public/data/sanborn_catalog.json.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "app" / "public" / "data"
UT_OUT_DIR = DATA_DIR / "sanborn_ut"
THUMBS_DIR = UT_OUT_DIR / "thumbs"
PLATES_DIR = UT_OUT_DIR / "plates"

UT_INDEX_URL = "https://maps.lib.utexas.edu/maps/sanborn/h.html"
UT_BASE_URL = "https://maps.lib.utexas.edu/maps/sanborn/"

# Raw download cache lives in scratch/tmp outside git repo so app/ stays compact
RAW_CACHE_DIR = Path(
    os.environ.get(
        "UT_SANBORN_RAW_CACHE",
        "/tmp/houston_atlas_ut_pcl_raw",
    )
)

UT_VOLUME_SPECS = [
    {
        "id": "utpcl_houston_1877",
        "prefixes": ["txu-sanborn-houston-1877-"],
        "era": "1877",
        "date": "May 1877",
        "sort_year": 1877.0,
        "title": "1877 Edition (4 Sheets - Pioneer Houston, Main St, Market Square & Bayou Wharves · UT PCL)",
    },
    {
        "id": "utpcl_houston_1917",
        "prefixes": ["txu-sanborn-houston-1917-vol1-", "txu-sanborn-houston-1917-vol2-"],
        "era": "1917",
        "date": "1917 Supplement (Vols. 1 & 2)",
        "sort_year": 1917.0,
        "title": "1917 Supplemental Plates, Vols. 1 & 2 (9 Sheets - WWI-Era Expansion · UT PCL)",
    },
    {
        "id": "utpcl_houston_1919",
        "prefixes": ["txu-sanborn-houston-1919-vol1-", "txu-sanborn-houston-1919-vol2-"],
        "era": "1919",
        "date": "1919 Supplement (Vols. 1 & 2)",
        "sort_year": 1919.0,
        "title": "1919 Supplemental Plates, Vols. 1 & 2 (10 Sheets - Post-WWI Annexation · UT PCL)",
    },
    {
        "id": "utpcl_houston_1924_vol2",
        "prefixes": ["txu-sanborn-houston-1924-vol02-"],
        "era": "1924-1929",
        "date": "1924 (Vol. 2)",
        "sort_year": 1924.2,
        "title": "1924 Edition, Volume 2 (80 Sheets - Norhill, Woodland Heights, First & Sixth Wards, Near Northside · UT PCL)",
    },
    {
        "id": "utpcl_houston_1924_vol3",
        "prefixes": ["txu-sanborn-houston-1924-vol03-"],
        "era": "1924-1929",
        "date": "1924 (Vol. 3)",
        "sort_year": 1924.3,
        "title": "1924 Edition, Volume 3 (83 Sheets - Houston Heights, Montrose, West End & Washington Corridor · UT PCL)",
    },
    {
        "id": "utpcl_houston_1948_vol6",
        "prefixes": ["txu-sanborn-houston-1948-vol06-"],
        "era": "1946-1951",
        "date": "1925-1948 (Vol. 6)",
        "sort_year": 1948.6,
        "title": "1925-1948 Reprint, Volume 6 (66 Sheets - East End, Second Ward, Magnolia Park & Navigation · UT PCL)",
    },
]

LOC_SORT_YEARS = {
    "sanborn08580_001": 1885.0,
    "sanborn08580_002": 1890.0,
    "sanborn08580_003": 1896.0,
    "sanborn08580_004": 1907.1,
    "sanborn08580_005": 1907.2,
    "sanborn08580_015": 1946.12,
    "sanborn08580_027": 1950.11,
    "sanborn08580_028": 1950.12,
    "sanborn08580_016": 1950.15,
    "sanborn08580_030": 1955.0,
}

CURATED_UT_LABELS = {
    "txu-sanborn-houston-1877-1": "Sheet 1: Key Map, Bayou Steamship Landing, Foot of Main & Market Square (May 1877)",
    "txu-sanborn-houston-1877-2": "Sheet 2: Main St Commercial Core, Courthouse Square & Cotton Presses (May 1877)",
    "txu-sanborn-houston-1877-3": "Sheet 3: Railroad Depots, Sawmills & Industrial Wharves (May 1877)",
    "txu-sanborn-houston-1877-4": "Sheet 4: Southern Main St, Churches & Foundry Blocks (May 1877)",
    "txu-sanborn-houston-1924-vol02-273": "Sheet 273: Norhill (Key Ave, Merrill, Omar, Highland, Norhill Blvd & Studewood, 1924)",
    "txu-sanborn-houston-1924-vol02-275": "Sheet 275: North Norhill (Temple, Cottage, Melwood, Frigate, 14th St & Studewood, 1924)",
    "txu-sanborn-houston-1924-vol02-259": "Sheet 259: Woodland Heights & Woodland Park (Bayland, Woodland, Morrison, 1924)",
    "txu-sanborn-houston-1924-vol02-208": "Sheet 208: Old Sixth Ward Historic District (Washington, Sabine, State, Kane, Lubbock, 1924)",
}


def parse_sheet_code_and_label(stem: str, raw_label: str, vol_id: str, date_str: str) -> tuple[str, str, bool, bool]:
    """Returns (code, human_label, is_featured, is_highres_index)."""
    tail = stem.split("-")[-1]
    if stem in CURATED_UT_LABELS:
        is_1877 = vol_id == "utpcl_houston_1877"
        return tail, CURATED_UT_LABELS[stem], True, is_1877

    if tail.endswith("t") and tail[:-1].isdigit():
        return tail, f"Title Sheet & Underwriter Notes ({date_str})", True, True
    if re.match(r"^\d+i\d*$", tail):
        return tail, f"Alphabetical Street Index - Look Up Street -> Sheet # ({date_str})", True, True
    if re.match(r"^\d+k\d*$", tail):
        return tail, f"Graphic Key Map & Sheet Number Grid ({date_str})", True, True

    if "1917-vol1-" in stem or "1919-vol1-" in stem:
        num = int(tail) if tail.isdigit() else tail
        return f"v1-{num}", f"Vol. 1 Supplemental Sheet {num} ({date_str})", num in (128, 131), False
    if "1917-vol2-" in stem or "1919-vol2-" in stem:
        num = int(tail) if tail.isdigit() else tail
        return f"v2-{num}", f"Vol. 2 Supplemental Sheet {num} ({date_str})", num in (91, 97), False

    if tail.isdigit():
        num = int(tail)
        is_feat = num in (1, 201, 301, 601)
        return str(num), f"Sheet {num} ({date_str})", is_feat, False

    return tail, f"{raw_label} ({date_str})", False, False


def process_one_sheet(task: dict) -> dict:
    href = task["href"]
    stem = task["stem"]
    is_hi_index = task["is_hi_index"]
    full_url = UT_BASE_URL + href

    raw_jpg = RAW_CACHE_DIR / f"{stem}.jpg"
    thumb_webp = THUMBS_DIR / f"{stem}.webp"
    plate_webp = PLATES_DIR / f"{stem}.webp"

    if not thumb_webp.exists() or not plate_webp.exists():
        if not raw_jpg.exists() or raw_jpg.stat().st_size < 10000:
            subprocess.check_call(
                ["curl", "-sSL", "--retry", "3", "--max-time", "45", full_url, "-o", str(raw_jpg)]
            )
        with Image.open(raw_jpg) as im:
            im_rgb = im.convert("RGB")
            w0, h0 = im_rgb.size

            tw = 260
            th = max(1, int(round(h0 * (tw / w0))))
            im_rgb.resize((tw, th), Image.Resampling.LANCZOS).save(
                thumb_webp, "WEBP", quality=65, method=4
            )

            pw = 1700 if is_hi_index else 1350
            pq = 78 if is_hi_index else 74
            ph = max(1, int(round(h0 * (pw / w0))))
            im_rgb.resize((pw, ph), Image.Resampling.LANCZOS).save(
                plate_webp, "WEBP", quality=pq, method=4
            )

    return {
        "sp": task["sp"],
        "code": task["code"],
        "label": task["label"],
        "is_featured": task["is_featured"],
        "source": "ut_pcl",
        "thumb_url": f"public/data/sanborn_ut/thumbs/{stem}.webp",
        "preview_url": f"public/data/sanborn_ut/plates/{stem}.webp",
        "hires_url": full_url,
        "loc_url": UT_INDEX_URL,
    }


def main() -> None:
    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    PLATES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching UT PCL Houston Sanborn index from {UT_INDEX_URL}...")
    html = subprocess.check_output(["curl", "-sSL", "--retry", "3", UT_INDEX_URL]).decode(
        "utf-8", errors="replace"
    )
    links = re.findall(
        r'<li>\s*<a\s+href="(g-i/txu-sanborn-houston-[^"]+\.jpg)"[^>]*>([^<]+)</a>',
        html,
        re.I,
    )
    print(f"Parsed {len(links)} Houston Sanborn links on h.html.")

    ut_volumes = []
    all_tasks = []
    tasks_by_vol: dict[str, list[dict]] = {}

    for spec in UT_VOLUME_SPECS:
        vid = spec["id"]
        matched = []
        for href, raw_lbl in links:
            fname = href.split("/")[-1]
            if any(fname.startswith(pref) for pref in spec["prefixes"]):
                matched.append((href, raw_lbl.strip()))
        # Re-order so Graphic Key Map (k) and Alphabetical Street Index (i) appear right at top with Title (t)!
        def sheet_sort_key(pair: tuple[str, str]) -> tuple[int, int]:
            st = pair[0].split("/")[-1].rsplit(".", 1)[0]
            tl = st.split("-")[-1]
            if re.match(r"^\d+k\d*$", tl):
                return (0, 0)
            if re.match(r"^\d+i\d*$", tl):
                return (1, 0)
            if tl.endswith("t") and tl[:-1].isdigit():
                return (2, 0)
            return (3, 0)

        ordered = sorted(enumerate(matched), key=lambda idx_pair: (sheet_sort_key(idx_pair[1]), idx_pair[0]))
        vol_tasks = []
        for sp_idx, (_, (href, raw_lbl)) in enumerate(ordered, 1):
            stem = href.split("/")[-1].rsplit(".", 1)[0]
            code, label, is_feat, is_hi = parse_sheet_code_and_label(
                stem, raw_lbl, vid, spec["date"]
            )
            t = {
                "vol_id": vid,
                "sp": sp_idx,
                "href": href,
                "stem": stem,
                "code": code,
                "label": label,
                "is_featured": is_feat,
                "is_hi_index": is_hi,
            }
            vol_tasks.append(t)
            all_tasks.append(t)
        tasks_by_vol[vid] = vol_tasks
        print(f"  Queued {vid}: {len(vol_tasks)} sheets")

    print(f"Converting {len(all_tasks)} UT PCL sheets to WebP thumbs + plates with 12 workers...")
    results_by_key: dict[tuple[str, int], dict] = {}
    done_cnt = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        fut_map = {pool.submit(process_one_sheet, t): t for t in all_tasks}
        for fut in concurrent.futures.as_completed(fut_map):
            t = fut_map[fut]
            rec = fut.result()
            results_by_key[(t["vol_id"], t["sp"])] = rec
            done_cnt += 1
            if done_cnt % 40 == 0 or done_cnt == len(all_tasks):
                print(f"  Processed {done_cnt}/{len(all_tasks)} sheets...")

    for spec in UT_VOLUME_SPECS:
        vid = spec["id"]
        v_sheets = [results_by_key[(vid, t["sp"])] for t in tasks_by_vol[vid]]
        feat_list = [
            {
                "sheet": s["sp"],
                "code": s["code"],
                "sp": s["sp"],
                "label": s["label"],
            }
            for s in v_sheets
            if s["is_featured"]
        ]
        ut_volumes.append(
            {
                "id": vid,
                "source": "ut_pcl",
                "sort_year": spec["sort_year"],
                "era": spec["era"],
                "date": spec["date"],
                "title": spec["title"],
                "sheet_count": len(v_sheets),
                "loc_url": UT_INDEX_URL,
                "loc_resource_url": UT_INDEX_URL,
                "featured_sheets": feat_list,
                "sheets": v_sheets,
            }
        )

    cat_path = DATA_DIR / "sanborn_catalog.json"
    cat = json.loads(cat_path.read_text(encoding="utf-8"))
    loc_vols = []
    for v in cat.get("volumes", []):
        if str(v.get("id", "")).startswith("utpcl_"):
            continue
        v["source"] = "loc"
        v["sort_year"] = LOC_SORT_YEARS.get(v["id"], 1900.0)
        loc_vols.append(v)

    merged_vols = sorted(loc_vols + ut_volumes, key=lambda x: float(x.get("sort_year", 1900.0)))
    for v in merged_vols:
        v.pop("sort_year", None)

    cat["title"] = (
        "Library of Congress & UT PCL Digitized Houston Sanborn Fire Insurance Atlases (1877-1955)"
    )
    cat["total_digitized_sheets"] = sum(int(v.get("sheet_count", 0)) for v in merged_vols)
    cat["volumes"] = merged_vols

    cat_path.write_text(json.dumps(cat, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(
        f"Updated {cat_path}: {len(merged_vols)} volumes, {cat['total_digitized_sheets']} total digitized sheets."
    )


if __name__ == "__main__":
    main()
