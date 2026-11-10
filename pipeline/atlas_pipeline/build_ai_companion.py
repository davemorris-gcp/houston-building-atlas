"""Build the Houston Building Atlas AI Research Companion artifacts:
1. `app/public/data/atlas_environments.json` (Sandbox vs. Production + Vanity URL manifest)
2. `app/llms.txt` (Standard llms.txt discovery index for ChatGPT Web, Claude Web, and Google Gemini)
3. `app/llms-full.txt` (Comprehensive self-contained Markdown corpus for ChatGPT Projects/Custom GPTs, Claude Projects/Skills, and Google Gemini Gems)
4. Mirrors `skills/houston-building-atlas/` into `app/public/ai-companion/` for 1-click browser downloads.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
from typing import Any

from atlas_pipeline.schema import (
    ACTIVE_CACHE_BUST,
    ACTIVE_TAX_YEAR,
    DEFAULT_ATLAS_BASE_URL,
    DEFAULT_ATLAS_SANDBOX_URL,
    sanitize_atlas_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"
DATA_DIR = APP_DIR / "public" / "data"
AI_COMPANION_PUBLIC_DIR = APP_DIR / "public" / "ai-companion"
SKILL_DIR = REPO_ROOT / "skills" / "houston-building-atlas"


def _ensure_trailing_slash(url: str) -> str:
    clean = str(url or "").strip()
    if not clean:
        return DEFAULT_ATLAS_BASE_URL
    return clean if clean.endswith("/") else f"{clean}/"


def _clean_dash_text(val: Any) -> str:
    txt = sanitize_atlas_text(str(val or ""))
    return txt.replace("\u2014", "-").replace("\u2013", "-").strip()


def build_atlas_environments_payload(
    *,
    production_url: str = DEFAULT_ATLAS_BASE_URL,
    sandbox_url: str = DEFAULT_ATLAS_SANDBOX_URL,
    active_environment: str = "production",
) -> dict[str, Any]:
    """Build the canonical `atlas_environments.json` payload."""
    prod_base = _ensure_trailing_slash(production_url)
    sand_base = _ensure_trailing_slash(sandbox_url)
    active_env = "sandbox" if str(active_environment).strip().lower() == "sandbox" else "production"
    canon_base = sand_base if active_env == "sandbox" else prod_base
    is_split = prod_base.rstrip("/").lower() != sand_base.rstrip("/").lower()

    return {
        "schema_version": 1,
        "active_environment": active_env,
        "canonical_base_url": canon_base,
        "vanity_cutover_ready": True,
        "is_sandbox_split_active": is_split,
        "cache_bust": ACTIVE_CACHE_BUST,
        "tax_year": ACTIVE_TAX_YEAR,
        "environments": {
            "production": {
                "label": "Live Production",
                "base_url": prod_base,
                "plat_monitor_url": f"{prod_base}plat-monitor.html",
                "llms_txt_url": f"{prod_base}llms.txt",
                "llms_full_txt_url": f"{prod_base}llms-full.txt",
                "ai_companion_dir_url": f"{prod_base}public/ai-companion/",
                "show_sandbox_badge": False,
            },
            "sandbox": {
                "label": "Sandbox / Staging Preview",
                "base_url": sand_base,
                "plat_monitor_url": f"{sand_base}plat-monitor.html",
                "llms_txt_url": f"{sand_base}llms.txt",
                "llms_full_txt_url": f"{sand_base}llms-full.txt",
                "ai_companion_dir_url": f"{sand_base}public/ai-companion/",
                "show_sandbox_badge": True,
            },
        },
        "discovery_urls": [
            f"{prod_base}public/data/atlas_environments.json",
            f"{sand_base}public/data/atlas_environments.json",
        ],
    }


def write_atlas_environments_json(
    output_path: Path | None = None,
    *,
    production_url: str = DEFAULT_ATLAS_BASE_URL,
    sandbox_url: str = DEFAULT_ATLAS_SANDBOX_URL,
    active_environment: str = "production",
) -> Path:
    """Write `app/public/data/atlas_environments.json`."""
    target = output_path or (DATA_DIR / "atlas_environments.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = build_atlas_environments_payload(
        production_url=production_url,
        sandbox_url=sandbox_url,
        active_environment=active_environment,
    )
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def set_environment_urls(
    *,
    production_url: str,
    sandbox_url: str = DEFAULT_ATLAS_SANDBOX_URL,
    active_environment: str = "production",
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Atomically update the production and sandbox base URLs across the repository."""
    root = repo_root or REPO_ROOT
    prod_base = _ensure_trailing_slash(production_url)
    sand_base = _ensure_trailing_slash(sandbox_url)
    env_path = root / "app" / "public" / "data" / "atlas_environments.json"
    write_atlas_environments_json(
        env_path,
        production_url=prod_base,
        sandbox_url=sand_base,
        active_environment=active_environment,
    )

    updated_files: list[str] = [str(env_path.relative_to(root))]

    schema_path = root / "pipeline" / "atlas_pipeline" / "schema.py"
    if schema_path.is_file():
        src = schema_path.read_text(encoding="utf-8")
        next_src = re.sub(
            r'^DEFAULT_ATLAS_BASE_URL\s*=\s*"[^"]+"',
            f'DEFAULT_ATLAS_BASE_URL = "{prod_base}"',
            src,
            flags=re.MULTILINE,
        )
        next_src = re.sub(
            r'^DEFAULT_ATLAS_SANDBOX_URL\s*=\s*"[^"]+"',
            f'DEFAULT_ATLAS_SANDBOX_URL = "{sand_base}"',
            next_src,
            flags=re.MULTILINE,
        )
        if next_src != src:
            schema_path.write_text(next_src, encoding="utf-8")
            updated_files.append(str(schema_path.relative_to(root)))

    filter_store_path = root / "app" / "js" / "filterStore.js"
    if filter_store_path.is_file():
        src = filter_store_path.read_text(encoding="utf-8")
        next_src = re.sub(
            r'export const DEFAULT_ATLAS_BASE_URL\s*=\s*"[^"]+";',
            f'export const DEFAULT_ATLAS_BASE_URL = "{prod_base}";',
            src,
        )
        next_src = re.sub(
            r'export const DEFAULT_ATLAS_SANDBOX_URL\s*=\s*"[^"]+";',
            f'export const DEFAULT_ATLAS_SANDBOX_URL = "{sand_base}";',
            next_src,
        )
        if next_src != src:
            filter_store_path.write_text(next_src, encoding="utf-8")
            updated_files.append(str(filter_store_path.relative_to(root)))

    for script_rel, pattern, replacement in (
        (
            "skills/houston-building-atlas/scripts/query_atlas.py",
            r'^DEFAULT_BASE_URL\s*=\s*"[^"]+"',
            f'DEFAULT_BASE_URL = "{prod_base}"',
        ),
        (
            "skills/houston-building-atlas/scripts/query_atlas.mjs",
            r'^export const DEFAULT_BASE_URL\s*=\s*"[^"]+";',
            f'export const DEFAULT_BASE_URL = "{prod_base}";',
        ),
    ):
        script_path = root / script_rel
        if script_path.is_file():
            s_src = script_path.read_text(encoding="utf-8")
            s_next = re.sub(pattern, replacement, s_src, flags=re.MULTILINE)
            if s_next != s_src:
                script_path.write_text(s_next, encoding="utf-8")
                updated_files.append(script_rel)

    return {
        "production_url": prod_base,
        "sandbox_url": sand_base,
        "active_environment": active_environment,
        "updated_files": updated_files,
    }


def _load_json_safe(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def build_llms_txt_content(
    *,
    base_url: str = DEFAULT_ATLAS_BASE_URL,
    data_dir: Path | None = None,
) -> str:
    """Build standard `/llms.txt` index content for AI web-browsing tools."""
    base = _ensure_trailing_slash(base_url)
    d_dir = data_dir or DATA_DIR
    stats = _load_json_safe(d_dir / "stats_summary.json", {})
    total_bld = int(stats.get("total_buildings") or 1507897)
    min_yr = int(stats.get("min_year") or 1823)
    max_yr = int(stats.get("max_year") or ACTIVE_TAX_YEAR)

    lines = [
        "# The Houston Building Atlas (Preservation Houston)",
        "",
        f"> Open-source historical cartography, house-history research, and preservation advocacy platform mapping {total_bld:,} dated structures ({min_yr}-{max_yr}) across Houston and Harris County, Texas, alongside 623 demolished Lost Houston landmarks, 509 City of Houston Landmarks, 23 HPO Historic Districts (6,169 parcel survey rows), 22 annual HCAD certified ownership rolls (2005-2026), 1.30M decoded building permits, 878 Sanborn Fire Insurance Map sheets (1877-1955), 39 Houston City Directories (1866-1926), and 595 TARO 2.0 / Houston Public Library archival citations.",
        "",
        "## Canonical URLs & Environment Discovery",
        "",
        f"- [Live Interactive Web Application]({base}): Main MapLibre GL JS + PMTiles historical map, 4-tab Property Inspector, and 1-click THC Historic Resources Survey Form generator.",
        f"- [Companion Plat & Property Monitor]({base}plat-monitor.html): Biweekly Planning Commission Plat Review, 15-Point Property Watchlist, and 90-day Multi-Agency Demolition Activity Log.",
        f"- [Environment & Vanity URL Discovery Manifest]({base}public/data/atlas_environments.json): Canonical `production` and `sandbox` base URLs.",
        f"- [Full Self-Contained LLM Knowledge Corpus (llms-full.txt)]({base}llms-full.txt): Complete tables of all 623 Lost Houston demolished landmarks, 500+ designated landmarks/NRHP/Good Brick winners, top unprotected HAHC Landmark Candidates, 23 Historic District & 11 Council District scorecards, Architect/Style portfolios, and permit shorthand glossary.",
        "",
        "## AI Research Companion Skill & Scripts (ChatGPT, Claude, Gemini, CLI)",
        "",
        f"- [Universal Agent Skill Definition (SKILL.md)]({base}public/ai-companion/SKILL.md): Portable skill and system instructions for ChatGPT Plus/Web, Claude Web/Desktop, Google Gemini, and coding agents.",
        f"- [Platform Setup & Pilot Guide (PLATFORM_SETUP_GUIDE.md)]({base}public/ai-companion/PLATFORM_SETUP_GUIDE.md): 2-minute setup guide and 6 test prompts for ChatGPT Plus/Web, Claude Web/Desktop, and Google Gemini.",
        f"- [Zero-Dependency Python Lookup Script (query_atlas.py)]({base}public/ai-companion/query_atlas.py): Standard-library Python 3 CLI and Code Interpreter helper that queries any Harris County street address, 13-digit HCAD account, architect, or neighborhood across static `.json.gz` shards.",
        f"- [Zero-Dependency Node.js Lookup Script (query_atlas.mjs)]({base}public/ai-companion/query_atlas.mjs): Node 18+ ESM equivalent for local or agentic CLI queries.",
        "",
        "## 7 Golden Rules for Accurate Houston Built-Environment Research",
        "",
        "1. **Check 4-Tier Construction Date Provenance (Never Trust Rounded HCAD Decade Placeholders Alone):** HCAD appraisal rolls frequently assign rounded effective/remodel years (`1920`, `1930`, `1940`, `1950`) to older structures or list a pre-1926 date on a lot proven unimproved in the 1926 Houston City Directory. Always prioritize Tier 1 (Statutory/Archival Primary Records: Sanborn maps, City Directories, HPO designation reports, building permits) and Tier 2 (Preservation Houston Curated Overrides in `curated_overrides.json`) over Tier 3 (un-overridden HCAD appraisal dates).",
        "2. **Distinguish Houston's Statutory Protection Tiers (COH Code Ch. 33 & Ch. 42):**",
        "   - **Protected Landmark (`PLM`):** Permanent binding protection against demolition under COH Code Ch. 33.",
        "   - **City of Houston Historic District (`23` districts):** Contributing structures require a Certificate of Appropriateness (COA) from the Houston Archaeological and Historical Commission (HAHC) before exterior alteration or demolition.",
        "   - **Standard City Landmark (`LM` outside a Historic District):** Carries only a 90-day demolition waiting period waiver (`Sec. 33-250`) unless upgraded to Protected Landmark.",
        "   - **City of Houston Heritage District (e.g., Freedmen's Town Heritage District):** Applies ONLY to infrastructure in the public right-of-way and city-owned land; it does NOT protect private residential or commercial buildings from demolition.",
        "   - **National Register of Historic Places (`NRHP`) & Recorded Texas Historic Landmark (`RTHL`):** Federal/state recognitions that do not block private-owner demolition using private funds unless paired with a local City Protected Landmark designation.",
        "3. **Always Cross-Check *Lost Houston* (`623` Demolished Sites) & the Active Property Watchlist (`34` Sites):** Before stating that a historic Houston building is still standing, check `overlays.json` (`lost_houston`), `property_watchlist.json`, and `active_permit_alerts.json`. For example, the `1894` John Henry Kirby Mansion at `2000 Smith St` (HCAD `0152300000001`, remodeled in `1928` by architect James Ruskin Bailey) was demolished Oct 30 - Nov 2, 2026 and graduated to Lost Houston (`lost_kirby_mansion_1894`).",
        "4. **Decode HCAD Appraiser Shorthand & 2-Stage COH Demolition Permits into Plain English:**",
        "   - In COH demolition permits, distinguish **Stage 1 Sewer Disconnect** (`Type 12` or `4P` Plumbing Permit, `BLD DEMO/SEWER DISC`) from **Stage 2 Building Demolition** (`Type SD` or `4D`, `BLD DEMO`) sharing the same 8-digit project number.",
        "   - In HCAD permits/notes, decode `NC` (New Construction), `CDU` (Condition/Desirability/Utility grade: `EX` Excellent, `VG` Very Good, `GD` Good, `AV` Average, `FR` Fair, `PR` Poor, `UN` Unsound), `REMOD` (Remodel), `ADDN` (Addition), and `GARAGE ONLY` / `GARAGE APT ONLY` (partial accessory work).",
        "5. **Trace 5-Era Harris County Clerk Deed Chains (`1836-2026`):**",
        "   - `1836-1960`: Deed Book Volume & Page (`Vol 1234 Pg 567`)",
        "   - `1961-1999`: 7-digit Microfilm Code (`NNN-NN-NNNN`) & Letter-Prefixed Clerk File # (`C123456` - `Z999999`)",
        "   - `2000-2015`: 10-digit Annual Clerk File # (`YYYYNNNNNN` / `20070123456`)",
        "   - `2016-present`: Real Property e-Recording File # (`RP-YYYY-NNNNNN`)",
        "6. **Respect Texas Tax Code `Sec. 25.025` Statutory Confidentiality:** Never speculate on or attempt to deanonymize individual homeowner names on parcels flagged with `\"conf\": 1` (`Confidential Owner`).",
        "7. **Always Emit Clickable Deep Links to the Atlas and Public Primary Sources:** Every property, architect, district, or demolished site mentioned in an answer should include a working clickable link using the templates below.",
        "",
        "## Deep-Link URL Grammar Into the Live Atlas",
        "",
        f"- **Inspect Property by 13-Digit HCAD Account:** `{base}#hcad=0550140020008`",
        f"- **Open Specific Property Tab (`overview`, `history`, `permits`, `hcad`):** `{base}#hcad=0090790000009&tab=history`",
        f"- **Open 1-Click Printable THC Historic Resources Survey Form (v3/2013):** `{base}#hcad=0550140020008&thcSurvey=1`",
        f"- **Inspect Demolished *Lost Houston* Building:** `{base}?id=lost_kirby_mansion_1894&layers=lost_houston`",
        f"- **Open Architect or Architectural Style Portfolio:** `{base}#portfolio=arch_james_ruskin_bailey` or `{base}#portfolio=style_art_deco`",
        f"- **Open Beneficial Ownership Portfolio Monograph:** `{base}?color=ownership_tier#portfolio=owner_portfolio:port_2410_polk_200`",
        f"- **Open Neighborhood, Historic District, or Council District (`A-K`) Scorecard:** `{base}#scorecard=historic_districts:hist_dist_old_sixth_ward` or `{base}#scorecard=council_districts:council_dist_coh_all`",
        f"- **Open Chapter 42 SMLS / SMBL Block-Face Calculator:** `{base}#hcad=0202370000031&ch42=0202370000031`",
        f"- **Open AI Research Companion Modal:** `{base}#aiCompanion=1`",
        f"- **Open Plat & Property Monitor Watchlist Dossier:** `{base}plat-monitor.html#tab=watchlist&watch=prop-kirby-mansion`",
        "",
        "## Primary Public Data & Archive URL Templates",
        "",
        "- **HCAD Property Search:** `https://search.hcad.org/` (query by 13-digit account number or street address)",
        "- **Harris County Clerk Real Property Records (`1961-present`):** `https://www.cclerk.hctx.net/Applications/WebSearch/RP.aspx`",
        "- **City of Houston Historic Preservation Office (HPO):** `https://www.houstontx.gov/planning/HistoricPres/historic_preservation.html`",
        "- **Library of Congress Sanborn Fire Insurance Maps (Houston):** `https://www.loc.gov/collections/sanborn-maps/?fa=location:texas%7Clocation:harris+county%7Clocation:houston`",
        "- **Texas Archival Resources Online (TARO 2.0):** `https://txarchives.org/`",
        "- **Houston Public Library Digital Archives:** `https://cdm17006.contentdm.oclc.org/`",
        "- **Portal to Texas History (UNT):** `https://texashistory.unt.edu/`",
        "- **Texas Historic Sites Atlas (THC):** `https://atlas.thc.texas.gov/`",
        "",
        "## Direct JSON Catalogs Served by the Atlas",
        "",
        f"- [Summary Statistics (`stats_summary.json`)]({base}public/data/stats_summary.json): Countywide dated building counts and year bounds.",
        f"- [Active Demolition & Asbestos Alerts (`active_permit_alerts.json`)]({base}public/data/active_permit_alerts.json): Live 2-stage COH demolition permits and TX DSHS 10-day asbestos notifications.",
        f"- [15-Point Property Watchlist (`property_watchlist.json`)]({base}public/data/property_watchlist.json): Curated endangered historic properties and multi-agency check logs.",
        f"- [Architect & Style Portfolio Metadata (`portfolio-metadata.json`)]({base}public/data/portfolio-metadata.json): Biographies, eras, and primary sources for Houston architects, builders, and styles.",
        f"- [TARO 2.0 & HPL Archival Finding-Aid Index (`archival_index.json`)]({base}public/data/archival_index.json): 595 parcel-linked architectural drawings, photos, and box/folder citations across 65 collections.",
        f"- [COH Historic District Parcel Surveys (`hpo_district_surveys.json`)]({base}public/data/hpo_district_surveys.json): All 23 COH Historic District survey inventories (6,169 rows), Design Guidelines PDFs, and official maps.",
        f"- [Sanborn Fire Insurance Map Catalog (`sanborn_catalog.json`)]({base}public/data/sanborn_catalog.json): 16 digitized Sanborn volumes (1877-1955, 878 sheets) across LOC and UT PCL.",
        f"- [Subdivision Deed Restrictions Catalog (`deed_restrictions_catalog.json`)]({base}public/data/deed_restrictions_catalog.json): 65 mirrored historic deed restriction instruments and civic club enforcement records.",
        f"- [Curated Property Overrides (`curated_overrides.json`)]({base}public/data/curated_overrides.json): 2,400+ historian-verified dates, names, architects, styles, and narratives.",
    ]
    return _clean_dash_text("\n".join(lines)) + "\n"


def build_llms_full_txt_content(
    *,
    base_url: str = DEFAULT_ATLAS_BASE_URL,
    data_dir: Path | None = None,
) -> str:
    """Build `/llms-full.txt` comprehensive Markdown knowledge pack for ChatGPT, Claude, and Gemini."""
    base = _ensure_trailing_slash(base_url)
    d_dir = data_dir or DATA_DIR

    overlays = _load_json_safe(d_dir / "overlays.json", {})
    overrides_doc = _load_json_safe(d_dir / "curated_overrides.json", {})
    overrides = overrides_doc.get("overrides") if isinstance(overrides_doc, dict) and "overrides" in overrides_doc else overrides_doc
    if not isinstance(overrides, dict):
        overrides = {}

    watchlist_doc = _load_json_safe(d_dir / "property_watchlist.json", {})
    watched_props = watchlist_doc.get("watchedProperties") or []

    alerts_doc = _load_json_safe(d_dir / "active_permit_alerts.json", {})
    alerts_list = alerts_doc.get("alerts") or []

    portfolios_doc = _load_json_safe(d_dir / "portfolio-metadata.json", {})
    hpo_doc = _load_json_safe(d_dir / "hpo_district_surveys.json", {})
    scorecards_doc = _load_json_safe(d_dir / "neighborhood_scorecards.json", {})
    archival_doc = _load_json_safe(d_dir / "archival_index.json", {})

    sections: list[str] = [
        build_llms_txt_content(base_url=base, data_dir=d_dir).strip(),
        "",
        "---",
        "",
        "## Section 1: Active Endangered Property Watchlist & Recent Graduations",
        "",
        "These properties are tracked in the Preservation Houston 15-Point Property Watchlist (`plat-monitor.html#tab=watchlist`).",
        "",
        "| Status | Tier | Address | Property Name | Year Built | HCAD Account | Architect / Designation | Watchlist Deep Link |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for wp in watched_props:
        if not isinstance(wp, dict) or wp.get("removedFromWatchlist"):
            continue
        wp_id = _clean_dash_text(wp.get("id") or "")
        status = _clean_dash_text(wp.get("trackerStatus") or "Monitoring")
        if wp.get("demolishedDate"):
            status = f"{status} (Demolished {_clean_dash_text(wp.get('demolishedDate'))})"
        tier = _clean_dash_text(wp.get("tier") or "")
        addr = _clean_dash_text(wp.get("address") or "")
        name = _clean_dash_text(wp.get("propertyName") or wp.get("platProjectName") or addr)
        yr = _clean_dash_text(wp.get("yearBuilt") or "")
        hcad = _clean_dash_text(wp.get("hcadAccount") or "")
        arch = _clean_dash_text(wp.get("architect") or wp.get("designation") or wp.get("neighborhood") or "")
        link = f"{base}plat-monitor.html#tab=watchlist&watch={wp_id}"
        sections.append(
            f"| {status} | Tier {tier} | {addr} | {name} | {yr} | `{hcad}` | {arch} | [Watchlist]({link}) |"
        )

    sections.extend([
        "",
        "---",
        "",
        "## Section 2: Active Demolition Permits & Texas DSHS Asbestos Alerts",
        "",
        "| Severity | Signal Type | Address | Building / Landmark Name | Year | HCAD Account | Permit / Project # | Atlas Link |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ])
    for al in alerts_list[:60]:
        if not isinstance(al, dict):
            continue
        sev = _clean_dash_text(al.get("severity") or "watch").upper()
        sig = _clean_dash_text(
            al.get("permit_stage_label")
            or al.get("badge")
            or al.get("severity_label")
            or al.get("source")
            or al.get("permit_type")
            or ""
        )
        addr = _clean_dash_text(al.get("address") or "")
        bname = _clean_dash_text(
            al.get("landmark_name") or al.get("building_name") or al.get("occupant") or al.get("title") or ""
        )
        yr = _clean_dash_text(al.get("year_built") or "")
        raw_hcad = str(al.get("hcad_num") or "").split("#")[0].strip()
        raw_pnum = al.get("permit_num") or al.get("notification_num") or ""
        if not raw_pnum:
            coh_list = al.get("coh_permits") or []
            dshs_list = al.get("dshs_notifications") or []
            if coh_list and isinstance(coh_list[0], dict):
                raw_pnum = coh_list[0].get("project_no") or coh_list[0].get("permit_num") or ""
            elif dshs_list and isinstance(dshs_list[0], dict):
                raw_pnum = dshs_list[0].get("notification_number") or dshs_list[0].get("notification_num") or ""
        pnum = _clean_dash_text(raw_pnum)
        if al.get("lost_houston_id"):
            lid = _clean_dash_text(al.get("lost_houston_id"))
            alink = f"{base}?id={lid}&layers=lost_houston"
        elif raw_hcad.isdigit() and 10 <= len(raw_hcad) <= 13:
            alink = f"{base}#hcad={raw_hcad.zfill(13)}&tab=permits"
        elif al.get("building_id") or raw_hcad:
            bid = _clean_dash_text(al.get("building_id") or raw_hcad)
            alink = f"{base}?id={bid}"
        else:
            alink = base
        sections.append(
            f"| {sev} | {sig} | {addr} | {bname} | {yr} | `{raw_hcad}` | `{pnum}` | [Inspect]({alink}) |"
        )

    # Section 3: Lost Houston Catalog (all 623 sites)
    lost_fc = (overlays.get("lost_houston") or {}).get("features") or []
    sections.extend([
        "",
        "---",
        "",
        f"## Section 3: Complete Catalog of Lost Houston Demolished Landmarks ({len(lost_fc)} Sites)",
        "",
        "| ID | Name | Address | Built | Demolished | Architect | Style | Atlas Link |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ])
    sorted_lost = sorted(
        lost_fc,
        key=lambda f: (
            int((f.get("properties") or {}).get("built_year") or 9999),
            str((f.get("properties") or {}).get("name") or ""),
        ),
    )
    for feat in sorted_lost:
        props = feat.get("properties") or {}
        lid = _clean_dash_text(props.get("id") or feat.get("id") or "")
        name = _clean_dash_text(props.get("name") or "")
        addr = _clean_dash_text(props.get("address") or "")
        built = _clean_dash_text(props.get("built_year") or props.get("year_built") or "")
        demo = _clean_dash_text(props.get("demolished_year") or props.get("year_demolished") or "")
        arch = _clean_dash_text(props.get("architect") or "")
        style = _clean_dash_text(props.get("style") or "")
        link = f"{base}?id={lid}&layers=lost_houston"
        sections.append(
            f"| `{lid}` | {name} | {addr} | {built} | {demo} | {arch} | {style} | [Lost Map]({link}) |"
        )

    # Section 4: Standing COH Landmarks & Protected Landmarks
    lm_fc = (overlays.get("landmarks") or {}).get("features") or []
    standing_lms = [
        f for f in lm_fc if not (f.get("properties") or {}).get("demolished")
    ]
    sections.extend([
        "",
        "---",
        "",
        f"## Section 4: Standing City of Houston Landmarks & Protected Landmarks ({len(standing_lms)} Sites)",
        "",
        "| HCAD Account | Landmark Name | Address | Year | Protection Tier | Architect | Style | Atlas & THC Survey Links |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ])
    sorted_lms = sorted(
        standing_lms,
        key=lambda f: (
            int((f.get("properties") or {}).get("year_built") or 9999),
            str((f.get("properties") or {}).get("name") or ""),
        ),
    )
    for feat in sorted_lms:
        props = feat.get("properties") or {}
        hcad = _clean_dash_text(str(props.get("hcad_num") or "").split("#")[0])
        name = _clean_dash_text(props.get("name") or "")
        addr = _clean_dash_text(props.get("address") or "")
        yr = _clean_dash_text(props.get("year_built") or "")
        tier = _clean_dash_text(props.get("protection_tier") or "")
        arch = _clean_dash_text(props.get("architect") or "")
        style = _clean_dash_text(props.get("style") or "")
        if hcad:
            links_cell = f"[Map]({base}#hcad={hcad}) / [THC Form]({base}#hcad={hcad}&thcSurvey=1)"
        else:
            links_cell = f"[Map]({base})"
        sections.append(
            f"| `{hcad}` | {name} | {addr} | {yr} | `{tier}` | {arch} | {style} | {links_cell} |"
        )

    # Section 5: Unprotected NRHP, RTHL, Cultural, and Master Architect Landmark Candidates
    nrhp_fc = (overlays.get("nrhp_properties") or {}).get("features") or []
    protected_hcads = {
        str((f.get("properties") or {}).get("hcad_num") or "").split("#")[0].strip()
        for f in standing_lms
        if (f.get("properties") or {}).get("protection_tier") in ("plm", "lm_in_hd")
    }
    sections.extend([
        "",
        "---",
        "",
        "## Section 5: Highlighted Unprotected Landmark-Eligible Properties (NRHP, Cultural & Architectural)",
        "",
        "Properties with National Register (NRHP), Recorded Texas Historic Landmark (RTHL), Good Brick Award, or major neighborhood/cultural significance that currently lack permanent local Protected Landmark (`PLM`) protection.",
        "",
        "| HCAD Account | Property Name | Address | Year | Category / Significance | Architect / Style | Atlas & 1-Click THC Survey Form |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ])
    seen_cand_hcads: set[str] = set()
    # First include curated cultural/architectural highlights in overrides
    priority_hcads = [
        "0550140020008",  # Granada Theater (3700 N Main St)
        "0371100000002",  # Talento Bilingue de Houston (333 S Jensen Dr)
        "0132250000003",  # LULAC Council 60 Clubhouse (3004 Bagby St)
        "1233520010001",  # Palmer Memorial Chapel & Autry House (6221 Main St)
    ]
    for phcad in priority_hcads:
        ov = overrides.get(phcad)
        if isinstance(ov, dict) and not ov.get("suppress_only") and not ov.get("is_demolished"):
            seen_cand_hcads.add(phcad)
            bname = _clean_dash_text(ov.get("building_name") or "")
            addr = _clean_dash_text(ov.get("address") or "")
            yr = _clean_dash_text(ov.get("year_built") or "")
            arch = _clean_dash_text(ov.get("architect") or "")
            style = _clean_dash_text(ov.get("style") or "")
            cat = "Cultural / Architectural Landmark Candidate"
            sections.append(
                f"| `{phcad}` | {bname} | {addr} | {yr} | {cat} | {arch} ({style}) | [Map]({base}#hcad={phcad}) / [THC Form]({base}#hcad={phcad}&thcSurvey=1) |"
            )

    for feat in nrhp_fc:
        props = feat.get("properties") or {}
        if props.get("demolished"):
            continue
        hcad = str(props.get("hcad_num") or "").split("#")[0].strip()
        if not hcad or hcad in protected_hcads or hcad in seen_cand_hcads:
            continue
        seen_cand_hcads.add(hcad)
        name = _clean_dash_text(props.get("name") or "")
        addr = _clean_dash_text(props.get("address") or "")
        yr = _clean_dash_text(props.get("year_built") or "")
        refnum = _clean_dash_text(props.get("refnum") or "")
        arch = _clean_dash_text(props.get("architect") or "")
        style = _clean_dash_text(props.get("style") or "")
        sections.append(
            f"| `{hcad}` | {name} | {addr} | {yr} | NRHP #{refnum} (No Local PLM) | {arch} {style} | [Map]({base}#hcad={hcad}) / [THC Form]({base}#hcad={hcad}&thcSurvey=1) |"
        )

    # Section 6: Architect & Architectural Style Portfolios
    arch_dict = (portfolios_doc.get("architects") if isinstance(portfolios_doc, dict) else None) or {}
    style_dict = (portfolios_doc.get("styles") if isinstance(portfolios_doc, dict) else None) or {}
    sections.extend([
        "",
        "---",
        "",
        f"## Section 6: Curated Architect, Builder & Style Portfolios ({len(arch_dict)} Architects, {len(style_dict)} Styles)",
        "",
        "| Type | Name | Era / Span | Subtitle / Role | Portfolio Deep Link |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ])
    for name, meta in sorted(arch_dict.items()):
        if not isinstance(meta, dict):
            continue
        slug = "arch_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        era = _clean_dash_text(meta.get("years") or meta.get("era") or "")
        sub = _clean_dash_text(meta.get("subtitle") or "Architect / Builder")
        sections.append(
            f"| Architect | {_clean_dash_text(name)} | {era} | {sub} | [Portfolio]({base}#portfolio={slug}) |"
        )
    for name, meta in sorted(style_dict.items()):
        if not isinstance(meta, dict):
            continue
        slug = "style_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        era = _clean_dash_text(meta.get("years") or meta.get("era") or "")
        sub = _clean_dash_text(meta.get("subtitle") or "Architectural Style")
        sections.append(
            f"| Style | {_clean_dash_text(name)} | {era} | {sub} | [Portfolio]({base}#portfolio={slug}) |"
        )

    # Section 7: 23 COH Historic Districts & 11 City Council District Scorecards
    districts_meta = (hpo_doc.get("districts") if isinstance(hpo_doc, dict) else None) or {}
    sections.extend([
        "",
        "---",
        "",
        f"## Section 7: All {len(districts_meta)} City of Houston Historic Districts (HPO Surveys & Design Guidelines)",
        "",
        "| Historic District | Surveyed Rows | Contributing | Non-Contributing | Design Guidelines PDF | Inventory PDF | Atlas District Link |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ])
    for dname, dmeta in sorted(districts_meta.items()):
        if not isinstance(dmeta, dict):
            continue
        total_rows = int(dmeta.get("total_rows") or 0)
        contrib = int(dmeta.get("contributing_count") or 0)
        non_contrib = int(dmeta.get("non_contributing_count") or 0)
        dg_url = _clean_dash_text(dmeta.get("design_guidelines_url") or "")
        inv_url = _clean_dash_text(dmeta.get("inventory_pdf_url") or "")
        dg_cell = f"[Guidelines]({dg_url})" if dg_url else "N/A"
        inv_cell = f"[Inventory]({inv_url})" if inv_url else "N/A"
        slug = re.sub(r"[^a-z0-9]+", "_", dname.lower()).strip("_")
        sections.append(
            f"| {_clean_dash_text(dname)} | {total_rows} | {contrib} | {non_contrib} | {dg_cell} | {inv_cell} | [District Map]({base}#bnd=historic_districts:hist_dist_{slug}) |"
        )

    # Council District Scorecards
    sc_layers = (scorecards_doc.get("layers") if isinstance(scorecards_doc, dict) else None) or {}
    council_cards_dict: dict[str, dict[str, Any]] = {}
    if isinstance(sc_layers.get("council_districts"), dict):
        council_cards_dict.update(sc_layers["council_districts"])
    if isinstance(scorecards_doc, dict):
        cw_card = scorecards_doc.get("citywide_scorecard")
        if isinstance(cw_card, dict) and cw_card.get("id"):
            council_cards_dict[str(cw_card["id"])] = cw_card
        for item in scorecards_doc.get("scorecards") or []:
            if isinstance(item, dict) and item.get("ly") == "council_districts" and item.get("id"):
                council_cards_dict[str(item["id"])] = item
    if council_cards_dict:
        sections.extend([
            "",
            "### City of Houston Council District Preservation Scorecards (`A-K` + Citywide)",
            "",
            "| District ID | Name | Total Structures | Pre-1940 Stock | Protected Buildings | 2020-2026 Demolitions | Scorecard Link |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ])
        for cid, card in sorted(council_cards_dict.items()):
            if not isinstance(card, dict):
                continue
            cname = _clean_dash_text(card.get("name") or card.get("nm") or cid)
            tot = int(card.get("total_buildings") or card.get("total") or card.get("bc") or 0)
            pre40 = int(card.get("pre_1940_count") or card.get("pre1940") or card.get("p40") or 0)
            prot = int(
                card.get("protected_count")
                or card.get("protected")
                or ((int(card.get("plm") or 0)) + (int(card.get("hdp") or 0)))
            )
            demos = int(card.get("demolitions_2020_2026") or card.get("demo_count") or card.get("dmt") or 0)
            sections.append(
                f"| `{cid}` | {cname} | {tot:,} | {pre40:,} | {prot:,} | {demos:,} | [Scorecard]({base}#scorecard=council_districts:{cid}) |"
            )

    # Section 8: Archival Collections Summary
    collections = (archival_doc.get("collections") if isinstance(archival_doc, dict) else None) or {}
    sections.extend([
        "",
        "---",
        "",
        f"## Section 8: Linked Archival Finding Aids ({len(collections)} TARO 2.0 & HPL Collections)",
        "",
        "| Collection ID | Repository | Title | Linked Parcels | Finding Aid URL |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ])
    for col_id, cmeta in sorted(collections.items()):
        if not isinstance(cmeta, dict):
            continue
        repo = _clean_dash_text(cmeta.get("repo") or cmeta.get("repository") or col_id)
        title = _clean_dash_text(cmeta.get("title") or col_id)
        cnt = int(cmeta.get("linked_parcel_count") or cmeta.get("citation_count") or 0)
        furl = _clean_dash_text(cmeta.get("url") or cmeta.get("finding_aid_url") or "")
        fcell = f"[Finding Aid]({furl})" if furl else "N/A"
        sections.append(f"| `{_clean_dash_text(col_id)}` | {repo} | {title} | {cnt} | {fcell} |")

    return _clean_dash_text("\n".join(sections)) + "\n"


def sync_ai_companion_bundle(
    *,
    repo_root: Path | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Generate `atlas_environments.json`, `app/llms.txt`, `app/llms-full.txt`, and mirror `skills/houston-building-atlas/` into `app/public/ai-companion/`."""
    root = repo_root or REPO_ROOT
    app_dir = root / "app"
    data_dir = app_dir / "public" / "data"
    pub_ai_dir = app_dir / "public" / "ai-companion"
    skill_dir = root / "skills" / "houston-building-atlas"

    env_path = data_dir / "atlas_environments.json"
    if not env_path.is_file():
        write_atlas_environments_json(env_path)
    env_payload = _load_json_safe(env_path, {})
    eff_base_url = _ensure_trailing_slash(
        base_url or env_payload.get("canonical_base_url") or DEFAULT_ATLAS_BASE_URL
    )

    llms_txt_path = app_dir / "llms.txt"
    llms_full_txt_path = app_dir / "llms-full.txt"

    llms_txt = build_llms_txt_content(base_url=eff_base_url, data_dir=data_dir)
    llms_full_txt = build_llms_full_txt_content(base_url=eff_base_url, data_dir=data_dir)

    llms_txt_path.write_text(llms_txt, encoding="utf-8")
    llms_full_txt_path.write_text(llms_full_txt, encoding="utf-8")

    mirrored_files: list[str] = []
    if skill_dir.is_dir():
        pub_ai_dir.mkdir(parents=True, exist_ok=True)
        mirror_map = [
            (skill_dir / "SKILL.md", pub_ai_dir / "SKILL.md"),
            (skill_dir / "scripts" / "query_atlas.py", pub_ai_dir / "query_atlas.py"),
            (skill_dir / "scripts" / "query_atlas.mjs", pub_ai_dir / "query_atlas.mjs"),
            (
                skill_dir / "references" / "PLATFORM_SETUP_GUIDE.md",
                pub_ai_dir / "PLATFORM_SETUP_GUIDE.md",
            ),
        ]
        for src_p, dst_p in mirror_map:
            if src_p.is_file():
                shutil.copyfile(src_p, dst_p)
                mirrored_files.append(str(dst_p.relative_to(root)))
        pycache_dir = pub_ai_dir / "__pycache__"
        if pycache_dir.is_dir():
            shutil.rmtree(pycache_dir, ignore_errors=True)

    return {
        "canonical_base_url": eff_base_url,
        "llms_txt_bytes": llms_txt_path.stat().st_size,
        "llms_full_txt_bytes": llms_full_txt_path.stat().st_size,
        "mirrored_files": mirrored_files,
    }
