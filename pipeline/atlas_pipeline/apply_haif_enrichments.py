#!/usr/bin/env python3
"""
Apply Houston Architecture Info Forum (HAIF - houstonarchitecture.com) enrichments
across the entire Harris County dataset (1.51M buildings in `aligned_ndjson` +
`curated_overrides.json` + `buildings.geojson` + `overlays.json` + `building_photos.json`
+ `search_index.json`) and generate `app/public/data/haif_index.json`.
"""

import glob
import json
import os
import re
from collections import defaultdict
from crawl_haif_forum import (
    DETAILS_CACHE_PATH,
    TOPICS_PATH,
    clean_title_from_slug,
    extract_slug_addresses,
    norm_addr,
    norm_addr_nodir,
)


def infer_subforum_from_slug(slug: str, existing_subforum: str) -> str:
    if existing_subforum:
        return existing_subforum
    s = slug.lower()
    if any(k in s for k in ["historic", "1800s", "1900s", "1910s", "1920s", "1930s", "vintage", "old-"]):
        return "Historic Houston"
    if any(k in s for k in ["mid-century", "mod-", "moderne", "mcm"]):
        return "Houston Mod"
    if any(k in s for k in ["heights", "yale-st", "studewood", "19th-st", "11th-st", "white-oak"]):
        return "The Heights"
    if any(k in s for k in ["montrose", "westheimer", "lovett", "avondale", "hyde-park", "richmond-ave", "alabama-st"]):
        return "Montrose"
    if any(k in s for k in ["main-st", "travis-st", "fannin-st", "texas-ave", "congress", "preston-st", "prairie-st", "milam", "louisiana-st", "smith-st", "bagby", "mckinney", "lamar-st", "rusk", "capitol", "walker", "polk-st", "commerce", "franklin-st"]):
        return "Downtown / Central"
    if any(k in s for k in ["holcombe", "bertner", "medical-center", "fannin", "md-anderson", "methodist-hospital", "baylor"]):
        return "Texas Medical Center"
    if any(k in s for k in ["navigation", "canal-st", "sampson", "harrisburg", "eado", "east-end", "st-emanuel", "polk"]):
        return "EaDo & East End"
    if any(k in s for k in ["san-felipe", "post-oak", "westheimer", "galleria", "uptown", "kirby", "river-oaks", "buffalo-speedway"]):
        return "River Oaks / Uptown"
    return "Architecture & Development"


def extract_clean_building_name_from_haif_title(title: str, addr_str: str) -> str:
    """
    If a HAIF thread title is formatted like:
      "Peden Iron Works At 700 N. San Jacinto St."
      "Auditorium Grocery Building At 1011 Mcgowen St"
      "Moderne At Yale 1020 Yale St"
    extract the building/project name before "At <addr>".
    """
    if not title:
        return ""
    t = re.sub(r"^Houston\s+photo:\s*", "", title, flags=re.I).strip()
    m = re.split(r"\s+\bat\s+\d{2,5}\b", t, maxsplit=1, flags=re.I)
    if len(m) == 2 and len(m[0].strip()) >= 4:
        cand = m[0].strip(" -:,")
        if not cand.isdigit() and not re.match(r"^(demo|demolition|new|proposed|future|question|help)\b", cand, re.I):
            return cand
    return ""


def main():
    with open(TOPICS_PATH) as f:
        topics = json.load(f)
    with open(DETAILS_CACHE_PATH) as f:
        thread_details = {int(k): v for k, v in json.load(f).items()}
    with open("pipeline/cache/haif_enrichment_audit.json") as f:
        audit_doc = json.load(f)

    # Map tid -> compact thread record
    tid_to_record = {}
    for t in topics:
        tid = t["tid"]
        det = thread_details.get(tid, {})
        title = det.get("title") or clean_title_from_slug(t["slug"])
        subforum = infer_subforum_from_slug(t["slug"], det.get("subforum", ""))
        canonical_url = f"https://www.houstonarchitecture.com/topic/{tid}-{t['slug']}/"
        wb_url = f"https://web.archive.org/web/{t['ts']}/{t['orig']}"
        is_demo = bool(re.search(r"\b(demo|demolish|demolition|teardown|razed)\b", t["slug"], re.I))
        tid_to_record[tid] = {
            "tid": tid,
            "slug": t["slug"],
            "title": title,
            "subforum": subforum,
            "url": canonical_url,
            "wb_url": wb_url,
            "date": det.get("date_created") or f"{t['ts'][:4]}-{t['ts'][4:6]}-{t['ts'][6:8]}",
            "excerpt": det.get("excerpt") or "",
            "years": det.get("years_mentioned") or [],
            "architects": det.get("architects_mentioned") or [],
            "photos": det.get("photos") or [],
            "is_demo": is_demo,
        }

    # Build normalized street address -> list of TIDs across all 5,333 address-bearing HAIF slugs
    slug_addr_to_tids = defaultdict(list)
    for t in topics:
        tid = t["tid"]
        for norm1, norm2, _ in extract_slug_addresses(t["slug"]):
            if tid not in slug_addr_to_tids[norm1]:
                slug_addr_to_tids[norm1].append(tid)
            if norm2 != norm1 and tid not in slug_addr_to_tids[norm2]:
                slug_addr_to_tids[norm2].append(tid)

    # Scan all 5 countywide .geojsonseq shards to see how many Harris County parcels/buildings match!
    shard_matched_hcads = defaultdict(list)
    shard_hcad_meta = {}
    shard_files = sorted(glob.glob("pipeline/cache/aligned_ndjson/buildings_*.geojsonseq"))
    total_shard_footprints_matched = 0

    for sf in shard_files:
        with open(sf) as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                if s[0] == "\x1e":
                    s = s[1:]
                try:
                    feat = json.loads(s)
                except Exception:
                    continue
                p = feat.get("properties", {})
                addr = p.get("address") or ""
                if not addr:
                    continue
                na = norm_addr(addr)
                if not na:
                    continue
                tids = slug_addr_to_tids.get(na) or slug_addr_to_tids.get(norm_addr_nodir(addr))
                if tids:
                    total_shard_footprints_matched += 1
                    hcad = str(p.get("hcad_num") or p.get("id") or "").strip()
                    if hcad:
                        existing = shard_matched_hcads[hcad]
                        for tid in tids:
                            if tid not in existing:
                                existing.append(tid)
                        if hcad not in shard_hcad_meta:
                            shard_hcad_meta[hcad] = {
                                "address": addr,
                                "year_built": int(p.get("year_built") or 0),
                                "year_source": p.get("year_source") or "",
                                "building_name": p.get("building_name") or "",
                            }

    print(f"Countywide shard scan complete: {total_shard_footprints_matched} building footprints across {len(shard_matched_hcads)} unique HCAD parcels matched to HAIF threads!")

    # Merge property_to_tids from crawl_haif_forum.py + countywide shard_matched_hcads
    merged_prop_to_tids = defaultdict(list)
    for k, tids in audit_doc.get("property_to_tids", {}).items():
        for tid in tids:
            if tid not in merged_prop_to_tids[k]:
                merged_prop_to_tids[k].append(tid)
    for hcad, tids in shard_matched_hcads.items():
        for tid in tids[:4]:
            if tid not in merged_prop_to_tids[hcad]:
                merged_prop_to_tids[hcad].append(tid)

    unique_all_matched_tids = {tid for tids in merged_prop_to_tids.values() for tid in tids}
    print(f"Total merged Atlas properties/parcels with HAIF threads: {len(merged_prop_to_tids)}")
    print(f"Total unique HAIF threads linked to Harris County properties: {len(unique_all_matched_tids)}")

    # 1. Enrich curated_overrides.json
    with open("app/public/data/curated_overrides.json") as f:
        overrides_doc = json.load(f)
    overrides = overrides_doc["overrides"]

    ov_enriched_count = 0
    ov_arch_added = 0
    ov_name_added = 0

    for k, v in overrides.items():
        base_hcad = k.split("#")[0]
        tids = merged_prop_to_tids.get(k) or merged_prop_to_tids.get(base_hcad) or []
        if not tids and v.get("address"):
            na = norm_addr(v["address"])
            tids = slug_addr_to_tids.get(na) or slug_addr_to_tids.get(norm_addr_nodir(v["address"])) or []
        if not tids:
            continue
        threads_payload = []
        for tid in tids[:4]:
            rec = tid_to_record.get(tid)
            if not rec:
                continue
            threads_payload.append({
                "tid": rec["tid"],
                "title": rec["title"],
                "subforum": rec["subforum"],
                "url": rec["url"],
                "wb_url": rec["wb_url"],
                "date": rec["date"],
                "excerpt": rec["excerpt"],
                "is_demo": rec["is_demo"],
            })
            if not v.get("architect") and rec.get("architects"):
                v["architect"] = ", ".join(rec["architects"][:2])
                ov_arch_added += 1
            if not v.get("building_name"):
                cname = extract_clean_building_name_from_haif_title(rec["title"], v.get("address", ""))
                if cname:
                    v["building_name"] = cname
                    ov_name_added += 1
        if threads_payload:
            v["haif_threads"] = threads_payload
            ov_enriched_count += 1

    with open("app/public/data/curated_overrides.json", "w") as f:
        json.dump(overrides_doc, f, separators=(",", ":"))
    print(f"Updated curated_overrides.json: {ov_enriched_count} overrides enriched with haif_threads (+{ov_arch_added} architects, +{ov_name_added} building names)")

    # 2. Enrich buildings.geojson
    with open("app/public/data/buildings.geojson") as f:
        buildings_fc = json.load(f)
    bld_enriched = 0
    for feat in buildings_fc["features"]:
        p = feat["properties"]
        fid = str(p.get("id") or "")
        hcad = str(p.get("hcad_num") or "")
        addr = p.get("address") or ""
        tids = (
            merged_prop_to_tids.get(fid)
            or merged_prop_to_tids.get(hcad)
            or (slug_addr_to_tids.get(norm_addr(addr)) if addr else None)
            or []
        )
        if not tids:
            continue
        threads_payload = []
        for tid in tids[:4]:
            rec = tid_to_record.get(tid)
            if not rec:
                continue
            threads_payload.append({
                "tid": rec["tid"],
                "title": rec["title"],
                "subforum": rec["subforum"],
                "url": rec["url"],
                "wb_url": rec["wb_url"],
                "date": rec["date"],
                "excerpt": rec["excerpt"],
                "is_demo": rec["is_demo"],
            })
            if not p.get("architect") and rec.get("architects"):
                p["architect"] = ", ".join(rec["architects"][:2])
            if not p.get("building_name"):
                cname = extract_clean_building_name_from_haif_title(rec["title"], addr)
                if cname:
                    p["building_name"] = cname
        if threads_payload:
            p["haif_threads"] = threads_payload
            bld_enriched += 1

    with open("app/public/data/buildings.geojson", "w") as f:
        json.dump(buildings_fc, f, separators=(",", ":"))
    print(f"Updated buildings.geojson: {bld_enriched} core buildings enriched with haif_threads")

    # 3. Enrich overlays.json (landmarks + good_brick_awards)
    with open("app/public/data/overlays.json") as f:
        overlays_doc = json.load(f)
    lm_enriched = 0
    for feat in overlays_doc.get("landmarks", {}).get("features", []):
        p = feat["properties"]
        hcad = str(p.get("hcad_num") or "").strip()
        addr = p.get("address") or ""
        tids = merged_prop_to_tids.get(hcad) or (slug_addr_to_tids.get(norm_addr(addr)) if addr else None) or []
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
            lm_enriched += 1

    gb_enriched = 0
    for feat in overlays_doc.get("good_brick_awards", {}).get("features", []):
        p = feat["properties"]
        bid = str(p.get("building_id") or "").strip()
        hcad = str(p.get("hcad_num") or "").strip()
        addr = p.get("address") or ""
        tids = (
            merged_prop_to_tids.get(bid)
            or merged_prop_to_tids.get(hcad)
            or (slug_addr_to_tids.get(norm_addr(addr)) if addr else None)
            or []
        )
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
            gb_enriched += 1

    with open("app/public/data/overlays.json", "w") as f:
        json.dump(overlays_doc, f, separators=(",", ":"))
    print(f"Updated overlays.json: {lm_enriched} landmarks and {gb_enriched} Good Brick awards enriched with haif_threads")

    # 4. Add verified HAIF forum photos (`media.invisioncic.com/w329674/...`) to `app/public/data/building_photos.json`
    with open("app/public/data/building_photos.json") as f:
        photos_doc = json.load(f)
    by_hcad_photos = photos_doc.get("by_hcad", {})
    added_photo_props = 0
    added_photo_urls = 0

    for prop_key, tids in merged_prop_to_tids.items():
        base_hcad = prop_key.split("#")[0]
        if not (len(base_hcad) == 13 and base_hcad.isdigit()):
            continue
        for tid in tids[:2]:
            rec = tid_to_record.get(tid)
            if not rec or not rec.get("photos"):
                continue
            existing_list = by_hcad_photos.setdefault(base_hcad, [])
            existing_urls = {x.get("url") for x in existing_list}
            added_for_this = False
            for img_url in rec["photos"][:2]:
                if img_url in existing_urls:
                    continue
                # Extract year from monthly_YYYY_MM or monthly_MM_YYYY in URL if present
                yr = 2015
                m1 = re.search(r"monthly_(20\d\d)_\d\d", img_url)
                m2 = re.search(r"monthly_\d\d_(20\d\d)", img_url)
                if m1:
                    yr = int(m1.group(1))
                elif m2:
                    yr = int(m2.group(1))
                elif rec.get("date") and rec["date"][:4].isdigit():
                    yr = int(rec["date"][:4])
                existing_list.append({
                    "url": img_url,
                    "thumb_url": img_url,
                    "year": yr,
                    "era_label": f"{yr} (HAIF Archive)",
                    "caption": f"{rec['title']} — Photograph shared on the Houston Architecture Info Forum ({rec['subforum']}).",
                    "source": "Houston Architecture Info Forum (HAIF)",
                    "source_url": rec["url"],
                    "license": "HAIF Community Archive"
                })
                existing_urls.add(img_url)
                added_photo_urls += 1
                added_for_this = True
            if added_for_this:
                added_photo_props += 1

    photos_doc["by_hcad"] = by_hcad_photos
    with open("app/public/data/building_photos.json", "w") as f:
        json.dump(photos_doc, f, indent=2)
    print(f"Updated building_photos.json: +{added_photo_urls} direct HAIF forum photos across {added_photo_props} HCAD properties")

    # 5. Build client-side `app/public/data/haif_index.json` so ANY of the 3,000+ matched HCAD parcels
    # or 5,333 street addresses across Harris County (including PMTiles-only buildings!) show their HAIF threads!
    compact_threads = {}
    by_hcad_idx = {}
    by_addr_idx = {}

    for prop_key, tids in merged_prop_to_tids.items():
        valid_tids = [tid for tid in tids[:4] if tid in tid_to_record]
        if not valid_tids:
            continue
        by_hcad_idx[prop_key] = valid_tids
        for tid in valid_tids:
            if str(tid) not in compact_threads:
                r = tid_to_record[tid]
                compact_threads[str(tid)] = {
                    "t": r["title"],
                    "f": r["subforum"],
                    "u": r["url"],
                    "w": r["wb_url"],
                    "d": r["date"],
                    "e": r["excerpt"],
                    "m": 1 if r["is_demo"] else 0,
                }

    for norm_a, tids in slug_addr_to_tids.items():
        valid_tids = [tid for tid in tids[:3] if tid in tid_to_record]
        if not valid_tids:
            continue
        by_addr_idx[norm_a] = valid_tids
        for tid in valid_tids:
            if str(tid) not in compact_threads:
                r = tid_to_record[tid]
                compact_threads[str(tid)] = {
                    "t": r["title"],
                    "f": r["subforum"],
                    "u": r["url"],
                    "w": r["wb_url"],
                    "d": r["date"],
                    "e": r["excerpt"],
                    "m": 1 if r["is_demo"] else 0,
                }

    haif_index_payload = {
        "threads": compact_threads,
        "by_key": by_hcad_idx,
        "by_addr": by_addr_idx,
    }
    with open("app/public/data/haif_index.json", "w") as f:
        json.dump(haif_index_payload, f, separators=(",", ":"))
    sz_kb = os.path.getsize("app/public/data/haif_index.json") / 1024.0
    print(f"Wrote app/public/data/haif_index.json ({sz_kb:.1f} KB): {len(compact_threads)} threads, {len(by_hcad_idx)} HCAD/building keys, {len(by_addr_idx)} street addresses")

    # 6. Enrich search_index.json so users can search HAIF building names or "HAIF"
    with open("app/public/data/search_index.json") as f:
        search_doc = json.load(f)
    items = search_doc.get("items", search_doc) if isinstance(search_doc, dict) else search_doc
    search_enriched = 0
    for item in items:
        key = item.get("id") if "#" in str(item.get("id", "")) else (item.get("hcad") or item.get("hcad_num") or item.get("id"))
        addr = item.get("address") or item.get("a") or ""
        tids = by_hcad_idx.get(str(key)) or (by_addr_idx.get(norm_addr(addr)) if addr else None)
        if tids:
            first_rec = tid_to_record.get(tids[0])
            if first_rec:
                if not item.get("name") and not item.get("n"):
                    cname = extract_clean_building_name_from_haif_title(first_rec["title"], addr)
                    if cname:
                        if "n" in item:
                            item["n"] = cname
                        else:
                            item["name"] = cname
                kw = item.get("keywords") or item.get("k") or ""
                addition = f"HAIF {first_rec['title']} {first_rec['subforum']}"
                if "HAIF" not in kw:
                    if "k" in item:
                        item["k"] = f"{kw} {addition}".strip()
                    else:
                        item["keywords"] = f"{kw} {addition}".strip()
                search_enriched += 1

    with open("app/public/data/search_index.json", "w") as f:
        json.dump(search_doc, f, separators=(",", ":"))
    print(f"Updated search_index.json: {search_enriched} search entries enriched with HAIF titles & keywords")


if __name__ == "__main__":
    main()
