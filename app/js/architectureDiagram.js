// Copyright 2026 Google LLC
// SPDX-License-Identifier: Apache-2.0
/**
 * Interactive Animated System & LLM Data-Flow Architecture Diagram (`app/js/architectureDiagram.js`).
 *
 * Illustrates the complete end-to-end architecture of The Houston Building Atlas:
 *   1. Developer Workstation (Antigravity / Claude Code reading external data sources, querying Frontier LLMs,
 *      and pushing code/data to GitHub AND schema/code updates to Google Sheets & Apps Script)
 *   2. Consolidated External Data Sources (4 organized domain clusters covering 29 institutions / 46 feeds)
 *   3. Live Runtime Map Tile & Layer Providers (OpenFreeMap, Esri Imagery, LOC Sanborn IIIF, PMTiles HTTP 206)
 *   4. GitHub Platform (Code Compilation, GitHub Actions Batch ETL, 6 Synthesis Engines & GitHub Pages CDN)
 *   5. Code Libraries, Languages & Tooling Stack with Inline SVG Project Logos
 *   6. End-User Personas (Preservation Staff Monitoring, Preservation Staff Research/Curation, Public Enthusiasts,
 *      and End-User Cloud & Local LLM Tools: Claude Code, Gemini, ChatGPT, Ollama, LM Studio, llama.cpp)
 *   7. 2-Way Community & Local-LLM Contribution Loop (Browser + CLI/Local LLM -> Google Apps Script Webhook
 *      -> Moderated Google Sheet -> GitHub Curated Overrides Sync)
 */

export const ARCHITECTURE_FLOW_MODES = Object.freeze([
  {
    id: "all",
    label: "🔄 All System Flows (Full Lifecycle)",
    shortLabel: "All Flows",
    description:
      "Simultaneously animates agentic development (reading primary sources + pushing to GitHub & Google Sheets/Apps Script), scheduled GitHub Actions ETL, live WebGL2 map streaming, and 2-way community + local-LLM contributions.",
  },
  {
    id: "dev_cicd",
    label: "💻 1. Agentic Dev, Source Research & Deploys",
    shortLabel: "1. Agentic Dev",
    description:
      "Developer Computer & AI coding agents (Antigravity / Claude Code + Gemini, Claude, OpenAI) read and audit the 46 external data feeds, push code & shards to GitHub, and deploy schema & webhook code to the Google Sheet & Apps Script hub.",
  },
  {
    id: "batch_etl",
    label: "🏛️ 2. Multi-Agency Batch ETL (37 Datasets)",
    shortLabel: "2. Batch ETL",
    description:
      "Developer local uv pipelines and GitHub Actions ingest 4 consolidated external data clusters (HCAD, County Clerk, COH Permits/Plats/HPO, Sanborn/Directories, TARO/HPL) into static PMTiles & .json.gz shards.",
  },
  {
    id: "live_runtime",
    label: "🗺️ 3. Live Map Tiles & Research Queries",
    shortLabel: "3. Map & LLM Queries",
    description:
      "End users (Preservation Staff, Public Enthusiasts, and Cloud/Local LLMs) stream HTTP 206 PMTiles, OpenFreeMap/Esri/LOC tiles, and /llms-full.txt + .json.gz shards.",
  },
  {
    id: "two_way_contribute",
    label: "🤖 4. 2-Way Browser & Local-LLM Contributions",
    shortLabel: "4. 2-Way Contributions",
    description:
      "Public users, field surveyors, and Local LLM tools (Ollama, LM Studio, Claude Code, Gemini CLI) submit citations, surveys, and flags via query_atlas.py / browser to the moderated Google Sheet -> GitHub sync loop.",
  },
]);

export const TECH_STACK_LOGOS = Object.freeze([
  {
    id: "python",
    name: "Python 3.12 + uv",
    role: "Spatial ETL, 6 Cross-Check Engines & CLI Skill (`query_atlas.py`)",
    badgeColor: "#3776AB",
    svg: `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="#38bdf8" d="M11.9 2c-5.1 0-4.8 2.2-4.8 2.2l.01 2.3h4.9v.7H5.2S2 6.8 2 11.9c0 5.1 2.8 4.9 2.8 4.9h1.7v-2.4s-.1-2.8 2.8-2.8h4.8s2.7.04 2.7-2.6V4.6S17.2 2 11.9 2zm-2.7 1.5a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8z"/><path fill="#facc15" d="M12.1 22c5.1 0 4.8-2.2 4.8-2.2l-.01-2.3h-4.9v-.7h6.8S22 17.2 22 12.1c0-5.1-2.8-4.9-2.8-4.9h-1.7v2.4s.1 2.8-2.8 2.8H9.9s-2.7-.04-2.7 2.6v4.4S6.8 22 12.1 22zm2.7-1.5a.9.9 0 1 1 0-1.8.9.9 0 0 1 0 1.8z"/></svg>`,
  },
  {
    id: "javascript",
    name: "JavaScript (ES2024)",
    role: "Zero-Build Browser Runtime & Node.js CLI (`query_atlas.mjs`)",
    badgeColor: "#F7DF1E",
    svg: `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><rect x="2" y="2" width="20" height="20" rx="3" fill="#facc15"/><path fill="#0f172a" d="M12.8 17.3c0 1.6-.9 2.4-2.3 2.4-1.2 0-2-.6-2.3-1.4l1.3-.8c.2.5.5.8 1 .8.5 0 .8-.2.8-1v-5.4h1.5v5.4zm4.7 2.4c-1.5 0-2.5-.7-2.9-1.7l1.3-.7c.3.6.8 1 1.6 1 .7 0 1.1-.3 1.1-.8 0-.6-.4-.8-1.3-1.2l-.5-.2c-1.3-.5-2.1-1.2-2.1-2.5 0-1.3 1-2.2 2.5-2.2 1.1 0 1.9.4 2.4 1.3l-1.2.8c-.3-.5-.6-.7-1.2-.7-.5 0-.9.3-.9.7 0 .5.3.7 1.1 1l.5.2c1.5.6 2.3 1.3 2.3 2.7 0 1.5-1.2 2.3-2.7 2.3z"/></svg>`,
  },
  {
    id: "maplibre",
    name: "MapLibre GL JS",
    role: "60fps WebGL2 Vector Rendering, 3D Extrusions & Choropleths",
    badgeColor: "#38BDF8",
    svg: `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="12" cy="12" r="9.5" fill="none" stroke="#38bdf8" stroke-width="2"/><path fill="#38bdf8" d="M12 4.5c-3 0-5.5 2.4-5.5 5.4 0 4.1 5.5 9.6 5.5 9.6s5.5-5.5 5.5-9.6c0-3-2.5-5.4-5.5-5.4zm0 7.4a2 2 0 1 1 0-4 2 2 0 0 1 0 4z"/></svg>`,
  },
  {
    id: "pmtiles",
    name: "PMTiles + Tippecanoe",
    role: "Serverless HTTP 206 Byte-Range Vector Tile Archive (`121 MB`)",
    badgeColor: "#A78BFA",
    svg: `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><polygon points="12,2 22,7.5 12,13 2,7.5" fill="#a78bfa"/><polyline points="2,12 12,17.5 22,12" fill="none" stroke="#c084fc" stroke-width="2"/><polyline points="2,16.5 12,22 22,16.5" fill="none" stroke="#38bdf8" stroke-width="2"/></svg>`,
  },
  {
    id: "geopandas",
    name: "GeoPandas & Shapely",
    role: "EPSG:2278 -> WGS84 Parcel Reprojection & Polygon Joins",
    badgeColor: "#10B981",
    svg: `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5" fill="#34d399"/><rect x="14" y="3" width="7" height="7" rx="1.5" fill="#38bdf8"/><rect x="3" y="14" width="7" height="7" rx="1.5" fill="#f59e0b"/><rect x="14" y="14" width="7" height="7" rx="1.5" fill="#a78bfa"/></svg>`,
  },
  {
    id: "github_actions",
    name: "GitHub Actions & Pages",
    role: "Automated CI/CD, Scheduled Permit/Override Sync & Static CDN",
    badgeColor: "#60A5FA",
    svg: `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="6" cy="6" r="3" fill="none" stroke="#60a5fa" stroke-width="2"/><circle cx="18" cy="6" r="3" fill="none" stroke="#34d399" stroke-width="2"/><circle cx="12" cy="18" r="3" fill="none" stroke="#a78bfa" stroke-width="2"/><path d="M8.5 7.5L11 15.5M15.5 7.5L13 15.5M9 6h6" stroke="#94a3b8" stroke-width="1.8"/></svg>`,
  },
  {
    id: "google_sheets_apps_script",
    name: "Google Sheets & Apps Script",
    role: "14-Tab Moderation Hub, `doPost` Webhook & CSV Override Sync",
    badgeColor: "#34D399",
    svg: `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><rect x="4" y="2" width="16" height="20" rx="2.5" fill="#10b981"/><path fill="#ffffff" d="M7 8h10v2H7V8zm0 4h10v2H7v-2zm0 4h7v2H7v-2z"/></svg>`,
  },
  {
    id: "chartjs",
    name: "Chart.js + SVG Canvas",
    role: "Era Histograms, District Scorecards & 24x36 Blueprint Posters",
    badgeColor: "#FB7185",
    svg: `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><rect x="3" y="13" width="4" height="8" rx="1" fill="#fb7185"/><rect x="10" y="8" width="4" height="13" rx="1" fill="#38bdf8"/><rect x="17" y="4" width="4" height="17" rx="1" fill="#34d399"/></svg>`,
  },
  {
    id: "local_cloud_llms",
    name: "Cloud & Local LLM Tools",
    role: "Claude, Gemini, ChatGPT, Ollama, LM Studio & llama.cpp (`llms-full.txt`)",
    badgeColor: "#F59E0B",
    svg: `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="#f59e0b" d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2z"/></svg>`,
  },
]);

export const ARCHITECTURE_NODES = Object.freeze([
  // Zone 1: Developer Workstation & Agentic Coding
  {
    id: "dev_workstation",
    zone: "1. Developer Workstation & Agentic AI Loop",
    title: "Developer Computer (Antigravity & Claude Code)",
    subtitle:
      "Reads 46 External Feeds · Runs Local uv ETL & 4-Layer Tests · Pushes to GitHub & Google Sheet/Apps Script",
    badge: "DEV & AGENT HOST",
    color: "#38bdf8",
    flowModes: ["all", "dev_cicd", "batch_etl"],
    x: 62,
    y: 94,
    w: 398,
    h: 112,
    details: {
      summary:
        "Developer workstation running Antigravity (Jetski) and Claude Code CLI agents that directly fetch and analyze external primary-source data, build shards, and push updates to GitHub and the Google Sheet / Apps Script moderation hub.",
      components: [
        "Direct Primary-Source Ingestion: fetches HCAD bulk zips, County Clerk TPIA deeds, COH permits/plats, Sanborn volumes, 1866-1926 City Directories, and TARO finding aids",
        "GitHub Code & Data Push: compiles `buildings.pmtiles` and 850+ `.json.gz` shards and runs 4-layer verification (`pytest`, `node:test`, Playwright) before `git push`",
        "Google Sheet & Apps Script Deployment: configures the 14-tab moderation workbook structure, formula rules, and `doPost`/`doGet` Apps Script webhook code",
      ],
      codePaths: ["pipeline/atlas_pipeline/", "app/js/", "tests/unit/run_all.mjs"],
    },
  },
  {
    id: "frontier_llm_apis",
    zone: "1. Developer Workstation & Agentic AI Loop",
    title: "Frontier LLM Providers (Gemini · Claude · OpenAI)",
    subtitle:
      "Analyzes Primary-Source Records · Multi-Agent Code Generation · Historical OCR & Cross-Check Synthesis",
    badge: "AGENT LLM APIS",
    color: "#a78bfa",
    flowModes: ["all", "dev_cicd", "batch_etl"],
    x: 62,
    y: 224,
    w: 398,
    h: 110,
    details: {
      summary:
        "Cloud LLM APIs invoked by Antigravity and Claude Code to inspect raw external data feeds, audit OCR transcripts, and author pipeline/UI code.",
      components: [
        "Google Gemini Pro / Flash: primary-source archival synthesis, Sanborn/directory OCR verification & contradiction auditing",
        "Anthropic Claude Opus / Sonnet: code generation, test authoring & HCAD appraiser shorthand parsers",
        "OpenAI GPT: cross-checking historical architect biographies, subdivision deed restrictions & TARO finding aids",
      ],
      codePaths: ["skills/houston-building-atlas/SKILL.md", "pipeline/atlas_pipeline/build_ai_companion.py"],
    },
  },

  // Zone 2: Consolidated External Data Sources (4 Domain Clusters)
  {
    id: "src_cadastre_deeds",
    zone: "2. Consolidated External Primary Data Sources (29 Orgs / 46 Feeds)",
    title: "Tax Cadastre, Deeds & County Archives",
    subtitle:
      "HCAD (1.51M structures · 22 rolls 2005-2026) · County Clerk (1836-2026) · Harris County Archives",
    badge: "9 FEEDS · COUNTY",
    color: "#60a5fa",
    flowModes: ["all", "dev_cicd", "batch_etl"],
    x: 62,
    y: 432,
    w: 398,
    h: 98,
    details: {
      summary:
        "Consolidated county property appraisal, 22-year certified ownership chains, 5-era deed records (1836-2026), and 1983-1985 microfilm rolls read by both Developer/Agent LLMs and GitHub batch pipelines.",
      components: [
        "HCAD CAMA Real & Building Data (`1,507,897` structures + 22 annual certified rolls `2005-2026` + `01/02/1988` baseline)",
        "Harris County Clerk Real Property (`1960-2026`), Historical Deed Volumes (`1836-1960`), and Plats (`MC.aspx`, `1823-2026`)",
        "Harris County Archives (`1983-1985` microfilm rolls & pre-1983 Tax Assessor-Collector records)",
      ],
      codePaths: ["pipeline/atlas_pipeline/extract.py", "pipeline/atlas_pipeline/build_owner_history_shards.py"],
    },
  },
  {
    id: "src_permits_plats",
    zone: "2. Consolidated External Primary Data Sources (29 Orgs / 46 Feeds)",
    title: "Permits, Subdivision Plats & Environmental",
    subtitle:
      "COH 2-Stage Permits (1.30M) · Biweekly Plat Tracker · TX DSHS Asbestos · FEMA NFHL Flood Zones",
    badge: "8 FEEDS · CITY/STATE",
    color: "#fb7185",
    flowModes: ["all", "dev_cicd", "batch_etl"],
    x: 62,
    y: 542,
    w: 398,
    h: 98,
    details: {
      summary:
        "Municipal building/demolition permits, biweekly Planning Commission subdivision replats, state asbestos abatement notices, and federal floodplains.",
      components: [
        "COH Structural & Plumbing Permits (`1.30M` decoded permits; Stage 1 Sewer Disconnect vs. Stage 2 Building Demo)",
        "COH Planning Commission Biweekly Plat Agendas (`plat-monitor.html` & Ch. 42 SMLS/SMBL covenants)",
        "Texas DSHS 10-Day Commercial Asbestos Demolition Notifications & FEMA NFHL Flood Zones",
      ],
      codePaths: ["pipeline/atlas_pipeline/refresh_permit_alerts.py", "app/plat-monitor.html"],
    },
  },
  {
    id: "src_preservation_surveys",
    zone: "2. Consolidated External Primary Data Sources (29 Orgs / 46 Feeds)",
    title: "Statutory Preservation & Landmark Surveys",
    subtitle:
      "COH HPO Landmarks (509) · 23 Historic Districts (6,169 rows) · NRHP · THC · Lost Houston (623)",
    badge: "10 FEEDS · STATUTORY",
    color: "#34d399",
    flowModes: ["all", "dev_cicd", "batch_etl"],
    x: 62,
    y: 652,
    w: 398,
    h: 98,
    details: {
      summary:
        "Local, state, federal, and nonprofit historic designations, parcel-by-parcel district inventories, and demolished landmark catalogs.",
      components: [
        "COH HPO Protected Landmarks (`PLM`), Landmarks (`509`), and `23` Historic District surveys (`6,169` parcel rows)",
        "National Register (`NRHP`, `312` properties/districts) & Texas Historical Commission (`RTHL` / Markers, `362` sites)",
        "Preservation Houston Good Brick Awards (`336` winners) & *Lost Houston* (`623` demolished sites)",
      ],
      codePaths: ["app/public/data/overlays.json", "app/public/data/hpo_district_surveys.json"],
    },
  },
  {
    id: "src_maps_archives",
    zone: "2. Consolidated External Primary Data Sources (29 Orgs / 46 Feeds)",
    title: "Sanborn Maps, City Directories & Archives",
    subtitle:
      "16 Sanborn Vols (878 sheets) · 39 City Directories (1866-1926) · 65 TARO & HPL Archival Collections",
    badge: "19 FEEDS · ARCHIVAL",
    color: "#f59e0b",
    flowModes: ["all", "dev_cicd", "batch_etl"],
    x: 62,
    y: 762,
    w: 398,
    h: 98,
    details: {
      summary:
        "Historical cartography, pre-1926 street directories, university/library finding aids, and historical photography analyzed by Developer/Agent LLMs and ETL pipelines.",
      components: [
        "16 Digitized Sanborn Fire Insurance Map Volumes (`1877-1955`, `878` sheets across Library of Congress & UT PCL)",
        "39 Houston City Directories (`1866-1926`) & 1915 *Red Book of Houston*",
        "65 TARO 2.0 & Houston Public Library Archival Collections (`595` parcel citations), UNT Portal to Texas History, Wikimedia & HAIF (`1,472` threads)",
      ],
      codePaths: ["app/public/data/sanborn_catalog.json", "app/public/data/archival_index.json"],
    },
  },

  // Zone 3: GitHub Platform, Runtime Tile Servers & Google Sheet / Apps Script Hub
  {
    id: "github_actions_etl",
    zone: "3. GitHub Platform, Runtime Tile Servers & 2-Way Moderation",
    title: "GitHub Repository & Actions Batch Pipelines",
    subtitle:
      "Automated 4-Layer CI/CD (`deploy-pages.yml`), Daily Permit/Watchlist Refresh & 6 Cross-Check Engines",
    badge: "GITHUB ACTIONS",
    color: "#38bdf8",
    flowModes: ["all", "dev_cicd", "batch_etl", "two_way_contribute"],
    x: 545,
    y: 94,
    w: 430,
    h: 145,
    details: {
      summary:
        "GitHub hosts the canonical codebase, runs automated 4-layer CI tests, executes scheduled batch ETL jobs, and merges approved community/LLM overrides.",
      components: [
        "Workflows: `deploy-pages.yml` (4-layer verification + deploy), `update_live_permits.yml` (daily 7 AM CT), `external-link-health.yml` (weekly Monday 8 AM CT)",
        "6 Cross-Check Synthesis Engines: Chronology Provenance, 22-Year Ownership & Sec. 25.025 Shield, Demolition & Plat Monitor, Sanborn/Directory Linker, Portfolio & Scorecard Compiler, and AI Companion Builder",
        "Compiles raw spatial/tabular feeds into zero-cost static shards with deterministic cache-busting",
      ],
      codePaths: [".github/workflows/", "pipeline/atlas_pipeline/"],
    },
  },
  {
    id: "github_pages_cdn",
    zone: "3. GitHub Platform, Runtime Tile Servers & 2-Way Moderation",
    title: "GitHub Pages Static CDN & Sharded Data Lake",
    subtitle:
      "`buildings.pmtiles` (121 MB) · 850+ `.json.gz` Shards · `/llms.txt` & `/llms-full.txt` · Static Web Apps",
    badge: "STATIC HTTPS CDN",
    color: "#34d399",
    flowModes: ["all", "dev_cicd", "batch_etl", "live_runtime"],
    x: 545,
    y: 278,
    w: 430,
    h: 145,
    details: {
      summary:
        "Zero-backend static HTTPS hosting serving the 3 web applications, HTTP 206 byte-range vector tiles, 850+ gzipped shards, and the AI Research Companion bundle.",
      components: [
        "Web Surfaces: `index.html` (3D Map, Inspector, `#audit=1` Workbench & `#admin=1` Console), `plat-monitor.html` (Plat & Watchlist Monitor), `data-ecosystem.html` (Provenance & Architecture)",
        "Spatial & Tabular Shards: `buildings.pmtiles` (`1.51M` footprints), `permits_000..999.json.gz`, `owners_000..999.json.gz`, `addr_00..99.json.gz`",
        "AI Companion Bundle: `/llms.txt`, `/llms-full.txt` (`462 KB`), `houston-building-atlas-skill.zip`, `query_atlas.py`, `query_atlas.mjs`",
      ],
      codePaths: ["app/index.html", "app/llms-full.txt", "app/public/ai-companion/"],
    },
  },
  {
    id: "live_tile_providers",
    zone: "3. GitHub Platform, Runtime Tile Servers & 2-Way Moderation",
    title: "External Runtime Map Tile & Imagery Systems",
    subtitle:
      "OpenFreeMap Vector Basemaps · Esri World Imagery & Wayback · LOC IIIF Sanborn Tiles · Mapillary Photos",
    badge: "LIVE WEBGL2 TILES",
    color: "#c084fc",
    flowModes: ["all", "live_runtime"],
    x: 545,
    y: 462,
    w: 430,
    h: 135,
    details: {
      summary:
        "External tile servers and IIIF endpoints queried directly by the user's browser alongside `buildings.pmtiles`.",
      components: [
        "OpenFreeMap Vector Tiles (`Positron`, `Liberty`, `Dark Matter` basemap styles)",
        "Esri World Imagery & Historical Aerial Wayback tile layers",
        "Library of Congress IIIF Sanborn Fire Insurance Map overlays & Wikimedia Commons / Mapillary street photos",
      ],
      codePaths: ["app/js/mapController.js", "app/public/data/sanborn_catalog.json"],
    },
  },
  {
    id: "moderation_sheet_loop",
    zone: "3. GitHub Platform, Runtime Tile Servers & 2-Way Moderation",
    title: "Moderated Google Sheet & Apps Script Webhook",
    subtitle:
      "14-Tab Workbook (`Pending_Submissions`, `Field_Surveys`, `Approved_Edits`) · Dev Schema & `doPost` Push",
    badge: "2-WAY MODERATION",
    color: "#f59e0b",
    flowModes: ["all", "dev_cicd", "two_way_contribute"],
    x: 545,
    y: 636,
    w: 430,
    h: 165,
    details: {
      summary:
        "Unified 14-tab Google Sheet & Apps Script moderation hub whose tab structure, validation rules, and webhook code are deployed from the Developer Computer, receiving browser & LLM contributions and syncing approved edits back to GitHub.",
      components: [
        "Developer Schema & Code Push: Developer Computer pushes 14-tab workbook schemas, formula validation rules, and Apps Script `doPost`/`doGet` webhook logic",
        "Google Apps Script Webhook (`DEFAULT_SUBMISSION_WEBHOOK_URL`) routing browser & CLI/LLM submissions into `Pending_Submissions` and `Field_Surveys` tabs",
        "Approved rows publish via live CSV (`Approved_Edits`) and sync via `sync_curated_overrides.py` into `app/public/data/curated_overrides.json` on GitHub",
      ],
      codePaths: ["app/js/curatedEdits.js", "pipeline/atlas_pipeline/sync_curated_overrides.py"],
    },
  },

  // Zone 4: End-User Personas & Cloud/Local LLM Tools
  {
    id: "user_ph_monitoring",
    zone: "4. End-User Personas & Local/Cloud AI Companions",
    title: "Preservation Staff (City & County Monitoring)",
    subtitle:
      "Monitors Biweekly COH Planning Commission Plats, 15-Point Watchlist & Demolition / Asbestos Alerts",
    badge: "STAFF · MONITORING",
    color: "#fb7185",
    flowModes: ["all", "live_runtime"],
    x: 1068,
    y: 94,
    w: 448,
    h: 145,
    details: {
      summary:
        "Preservation Staff and advocates tracking biweekly City of Houston Planning Commission subdivision agendas, demolition permits, and endangered landmarks.",
      components: [
        "Biweekly Plat & Property Monitor (`plat-monitor.html`) with 15-Point Watchlist (`34` tracked properties)",
        "90-Day Multi-Agency Demolition Activity Log (COH Stage 1/2 Demo Permits + TX DSHS 10-Day Asbestos Filings)",
        "Chapter 42 SMLS / SMBL block-face eligibility calculator for neighborhood opt-in protection",
      ],
      codePaths: ["app/plat-monitor.html", "app/public/data/property_watchlist.json"],
    },
  },
  {
    id: "user_ph_research",
    zone: "4. End-User Personas & Local/Cloud AI Companions",
    title: "Preservation Staff (Archival Research & Curation)",
    subtitle:
      "Authors Landmark Designations, Generates 1-Click THC Survey Forms & Moderates Queue (`#audit=1`)",
    badge: "STAFF · RESEARCH",
    color: "#38bdf8",
    flowModes: ["all", "live_runtime", "two_way_contribute"],
    x: 1068,
    y: 270,
    w: 448,
    h: 145,
    details: {
      summary:
        "Preservation Staff conducting primary-source building research, preparing HAHC / THC survey nominations, and reviewing community & AI submissions.",
      components: [
        "4-Tab Property Inspector (`Overview`, `1836-2026 History`, `Permits & Appraiser Shorthand`, `HCAD CAMA`)",
        "1-Click Printable Texas Historical Commission (THC) Historic Resources Survey Form (`#thcSurvey=1`)",
        "Curator Moderation Queue: reviews `Pending_Submissions` & `Field_Surveys` in Google Sheets / `index.html#audit=1` (`#admin=1`)",
      ],
      codePaths: ["app/js/thcSurveyExport.js", "app/js/staffWorkbench.js"],
    },
  },
  {
    id: "user_public_enthusiasts",
    zone: "4. End-User Personas & Local/Cloud AI Companions",
    title: "Public, Homeowners & Walk-the-Block Surveyors",
    subtitle:
      "Explores 1.51M Buildings, Traces House Histories & Submits Browser Corrections / Field Surveys",
    badge: "PUBLIC · 2-WAY WEB",
    color: "#34d399",
    flowModes: ["all", "live_runtime", "two_way_contribute"],
    x: 1068,
    y: 446,
    w: 448,
    h: 150,
    details: {
      summary:
        "Members of the public, homeowners, neighborhood civic clubs, students, and journalists exploring Houston's architectural history and contributing back.",
      components: [
        "Interactive 3D Building Chronology (`1823-2026`), Architect/Style Portfolios (`95+`), and Neighborhood Scorecards",
        "Browser Contribution Modal: submit primary-source date/architect overrides, historical photos, and footprint fixes",
        "Mobile Walk-the-Block Field Survey Mode & 1-Click Quick Flags (Claim Dispute, Photo Flag, Narrative Error)",
      ],
      codePaths: ["app/js/curatedEdits.js", "app/index.html"],
    },
  },
  {
    id: "user_local_cloud_llms",
    zone: "4. End-User Personas & Local/Cloud AI Companions",
    title: "End-User Cloud & Local LLM Tools (2-Way Skill)",
    subtitle:
      "Claude Code · Gemini · ChatGPT · Ollama · LM Studio · llama.cpp (`query_atlas.py` / `query_atlas.mjs`)",
    badge: "LOCAL & CLOUD LLMS",
    color: "#f59e0b",
    flowModes: ["all", "live_runtime", "two_way_contribute"],
    x: 1068,
    y: 626,
    w: 448,
    h: 175,
    details: {
      summary:
        "End users running their own Cloud AI (`Claude`, `Gemini`, `ChatGPT`) or Local Open-Weights LLMs (`Ollama`, `LM Studio`, `llama.cpp`, `OpenWebUI`, `Claude Code`, `Gemini CLI`) to research AND contribute back to the Atlas.",
      components: [
        "Read Path: queries `/llms.txt`, `/llms-full.txt`, and static `.json.gz` shards via `query_atlas.py` or `query_atlas.mjs`",
        "Write Path (2-Way Contributions): runs `--submit-correction`, `--submit-survey`, or `--submit-flag` with `--agent-tool 'Ollama Llama-3.3'`",
        "Offline Ledger & CSV Export: logs every agent contribution to `~/.houston_building_atlas/pending_contributions.json` and exports RFC 4180 CSV",
      ],
      codePaths: [
        "skills/houston-building-atlas/scripts/query_atlas.py",
        "skills/houston-building-atlas/scripts/query_atlas.mjs",
        "skills/houston-building-atlas/SKILL.md",
      ],
    },
  },
]);

export const ARCHITECTURE_EDGES = Object.freeze([
  // Flow 1: Agentic Dev reads External Primary Sources, queries Frontier LLMs, and pushes to GitHub + Google Sheet/Apps Script
  {
    id: "edge_dev_to_llm",
    from: "dev_workstation",
    to: "frontier_llm_apis",
    label: "Prompts, Code & Data Audits",
    color: "#a78bfa",
    flowModes: ["all", "dev_cicd"],
    path: "M 261 206 L 261 224",
  },
  {
    id: "edge_cadastre_to_dev",
    from: "src_cadastre_deeds",
    to: "dev_workstation",
    label: "Read HCAD Rolls & Clerk TPIA Deeds",
    color: "#60a5fa",
    flowModes: ["all", "dev_cicd", "batch_etl"],
    path: "M 62 475 L 36 475 L 36 145 L 62 145",
  },
  {
    id: "edge_permits_to_dev",
    from: "src_permits_plats",
    to: "dev_workstation",
    label: "Read COH Permits, Plats & DSHS Feeds",
    color: "#fb7185",
    flowModes: ["all", "dev_cicd", "batch_etl"],
    path: "M 62 585 L 36 585 L 36 165 L 62 165",
  },
  {
    id: "edge_pres_to_llm",
    from: "src_preservation_surveys",
    to: "frontier_llm_apis",
    label: "Read HPO Surveys, NRHP, THC & Lost Houston",
    color: "#34d399",
    flowModes: ["all", "dev_cicd", "batch_etl"],
    path: "M 62 695 L 44 695 L 44 265 L 62 265",
  },
  {
    id: "edge_archives_to_llm",
    from: "src_maps_archives",
    to: "frontier_llm_apis",
    label: "Read Sanborn Maps, 1866-1926 Directories & TARO",
    color: "#f59e0b",
    flowModes: ["all", "dev_cicd", "batch_etl"],
    path: "M 62 805 L 44 805 L 44 290 L 62 290",
  },
  {
    id: "edge_dev_to_github",
    from: "dev_workstation",
    to: "github_actions_etl",
    label: "git push (Code, Shards & 4-Layer Tests)",
    color: "#38bdf8",
    flowModes: ["all", "dev_cicd"],
    path: "M 460 140 C 500 140, 505 145, 545 145",
  },
  {
    id: "edge_dev_to_sheet",
    from: "dev_workstation",
    to: "moderation_sheet_loop",
    label: "Push 14-Tab Sheet Schema, Formulas & Apps Script Code",
    color: "#f59e0b",
    flowModes: ["all", "dev_cicd", "two_way_contribute"],
    path: "M 460 185 C 512 185, 500 680, 545 680",
  },
  {
    id: "edge_actions_to_pages",
    from: "github_actions_etl",
    to: "github_pages_cdn",
    label: "Build & Deploy Verified Static Bundle",
    color: "#34d399",
    flowModes: ["all", "dev_cicd", "batch_etl"],
    path: "M 760 239 L 760 278",
  },

  // Flow 2: Batch ETL from 4 Consolidated Source Clusters to GitHub Actions
  {
    id: "edge_cadastre_to_etl",
    from: "src_cadastre_deeds",
    to: "github_actions_etl",
    label: "HCAD + Clerk ETL",
    color: "#60a5fa",
    flowModes: ["all", "batch_etl"],
    path: "M 460 480 C 502 480, 502 175, 545 175",
  },
  {
    id: "edge_permits_to_etl",
    from: "src_permits_plats",
    to: "github_actions_etl",
    label: "COH Permits/Plats + DSHS",
    color: "#fb7185",
    flowModes: ["all", "batch_etl"],
    path: "M 460 590 C 508 590, 508 192, 545 192",
  },
  {
    id: "edge_pres_to_etl",
    from: "src_preservation_surveys",
    to: "github_actions_etl",
    label: "HPO/NRHP/THC/Lost",
    color: "#34d399",
    flowModes: ["all", "batch_etl"],
    path: "M 460 700 C 514 700, 514 208, 545 208",
  },
  {
    id: "edge_archives_to_etl",
    from: "src_maps_archives",
    to: "github_actions_etl",
    label: "Sanborn/Directories/TARO",
    color: "#f59e0b",
    flowModes: ["all", "batch_etl"],
    path: "M 460 810 C 520 810, 520 224, 545 224",
  },

  // Flow 3: Live Runtime Queries & Map Tiles to End-User Personas
  {
    id: "edge_pages_to_monitoring",
    from: "github_pages_cdn",
    to: "user_ph_monitoring",
    label: "plat-monitor.html + Alerts JSON",
    color: "#fb7185",
    flowModes: ["all", "live_runtime"],
    path: "M 975 315 C 1020 315, 1020 165, 1068 165",
  },
  {
    id: "edge_pages_to_research",
    from: "github_pages_cdn",
    to: "user_ph_research",
    label: "4-Tab Dossiers + THC Form",
    color: "#38bdf8",
    flowModes: ["all", "live_runtime"],
    path: "M 975 342 C 1020 342, 1020 342, 1068 342",
  },
  {
    id: "edge_pages_to_public",
    from: "github_pages_cdn",
    to: "user_public_enthusiasts",
    label: "HTTP 206 PMTiles + Shards",
    color: "#34d399",
    flowModes: ["all", "live_runtime"],
    path: "M 975 370 C 1020 370, 1020 495, 1068 495",
  },
  {
    id: "edge_tiles_to_public",
    from: "live_tile_providers",
    to: "user_public_enthusiasts",
    label: "Vector/Aerial/Sanborn Tiles",
    color: "#c084fc",
    flowModes: ["all", "live_runtime"],
    path: "M 975 530 C 1020 530, 1020 530, 1068 530",
  },
  {
    id: "edge_pages_to_llms",
    from: "github_pages_cdn",
    to: "user_local_cloud_llms",
    label: "/llms-full.txt + .json.gz Shards",
    color: "#f59e0b",
    flowModes: ["all", "live_runtime"],
    path: "M 975 400 C 1020 400, 1020 675, 1068 675",
  },

  // Flow 4: 2-Way Community & Local LLM Contributions back to the Atlas
  {
    id: "edge_public_to_sheet",
    from: "user_public_enthusiasts",
    to: "moderation_sheet_loop",
    label: "Browser Edits, Photos & Surveys",
    color: "#34d399",
    flowModes: ["all", "two_way_contribute"],
    path: "M 1068 570 C 1020 570, 1020 715, 975 715",
  },
  {
    id: "edge_llms_to_sheet",
    from: "user_local_cloud_llms",
    to: "moderation_sheet_loop",
    label: "CLI / Local LLM (--submit-correction / --submit-survey)",
    color: "#f59e0b",
    flowModes: ["all", "two_way_contribute"],
    path: "M 1068 755 C 1020 755, 1020 755, 975 755",
  },
  {
    id: "edge_research_moderates_sheet",
    from: "user_ph_research",
    to: "moderation_sheet_loop",
    label: "Curator Review & Approval",
    color: "#38bdf8",
    flowModes: ["all", "two_way_contribute"],
    path: "M 1068 388 C 1028 388, 1028 675, 975 675",
  },
  {
    id: "edge_sheet_to_github",
    from: "moderation_sheet_loop",
    to: "github_actions_etl",
    label: "sync_curated_overrides.py -> GitHub",
    color: "#f59e0b",
    flowModes: ["all", "two_way_contribute"],
    path: "M 545 735 C 510 735, 510 232, 545 232",
  },
]);

function _esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const ACCENT_CSS_VAR_MAP = Object.freeze({
  "#38bdf8": "var(--arch-cyan, #38bdf8)",
  "#a78bfa": "var(--arch-purple, #a78bfa)",
  "#60a5fa": "var(--arch-blue, #60a5fa)",
  "#fb7185": "var(--arch-rose, #fb7185)",
  "#34d399": "var(--arch-emerald, #34d399)",
  "#f59e0b": "var(--arch-amber, #f59e0b)",
  "#c084fc": "var(--arch-violet, #c084fc)",
});

function _themeAccent(hexColor) {
  const key = String(hexColor || "").trim().toLowerCase();
  return ACCENT_CSS_VAR_MAP[key] || hexColor;
}

/**
 * Wrap a string into lines that strictly fit within `maxChars` per line without truncating words.
 */
export function wrapSvgTextLines(text, maxChars = 48, maxLines = 3) {
  const words = String(text ?? "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!words.length) return [];
  const lines = [];
  let cur = "";
  for (let i = 0; i < words.length; i += 1) {
    const w = words[i];
    const cand = cur ? `${cur} ${w}` : w;
    if (cand.length <= maxChars || !cur) {
      cur = cand;
    } else if (lines.length < maxLines - 1) {
      lines.push(cur);
      cur = w;
    } else {
      cur = cand;
    }
  }
  if (cur) lines.push(cur);
  return lines;
}

export function renderArchitectureDiagramSectionHtml({
  activeFlowMode = "all",
  isPlaying = true,
  speedMultiplier = 1,
  selectedNodeId = "user_local_cloud_llms",
} = {}) {
  const activeModeObj =
    ARCHITECTURE_FLOW_MODES.find((m) => m.id === activeFlowMode) || ARCHITECTURE_FLOW_MODES[0];
  const selectedNode =
    ARCHITECTURE_NODES.find((n) => n.id === selectedNodeId) || ARCHITECTURE_NODES[13];

  const modeButtonsHtml = ARCHITECTURE_FLOW_MODES.map((m) => {
    const isSel = m.id === activeModeObj.id;
    return `
      <button
        type="button"
        class="arch-flow-mode-btn ${isSel ? "active" : ""}"
        data-arch-flow-mode="${_esc(m.id)}"
        aria-pressed="${isSel ? "true" : "false"}"
      >${_esc(m.label)}</button>
    `;
  }).join("");

  const svgClipPathsHtml = ARCHITECTURE_NODES.map(
    (node) => `
      <clipPath id="arch_clip_${_esc(node.id)}">
        <rect x="2" y="2" width="${node.w - 4}" height="${node.h - 4}" rx="9" />
      </clipPath>
    `
  ).join("");

  const svgEdgesHtml = ARCHITECTURE_EDGES.map((edge, idx) => {
    const isActive = edge.flowModes.includes(activeModeObj.id);
    const opacity = isActive ? "0.92" : "0.16";
    const baseDur = (3.4 + (idx % 4) * 0.45) / (Number(speedMultiplier) || 1);
    const durStr = `${baseDur.toFixed(2)}s`;
    return `
      <g class="arch-edge-group ${isActive ? "is-active" : "is-dimmed"}" data-edge-id="${_esc(edge.id)}">
        <path
          id="arch_path_${_esc(edge.id)}"
          d="${_esc(edge.path)}"
          fill="none"
          stroke="${_esc(_themeAccent(edge.color))}"
          stroke-width="${isActive ? "2.4" : "1.4"}"
          stroke-opacity="${opacity}"
          stroke-dasharray="${isActive ? "6 5" : "3 5"}"
        />
        ${
          isActive && isPlaying
            ? `
          <circle r="5" fill="${_esc(_themeAccent(edge.color))}" filter="url(#archPacketGlow)">
            <animateMotion dur="${durStr}" repeatCount="indefinite" rotate="auto">
              <mpath href="#arch_path_${_esc(edge.id)}" />
            </animateMotion>
          </circle>
          <circle r="3.5" fill="var(--arch-packet-core, #ffffff)" opacity="0.85">
            <animateMotion dur="${durStr}" begin="${(baseDur / 2).toFixed(2)}s" repeatCount="indefinite" rotate="auto">
              <mpath href="#arch_path_${_esc(edge.id)}" />
            </animateMotion>
          </circle>
        `
            : ""
        }
      </g>
    `;
  }).join("");

  const svgNodesHtml = ARCHITECTURE_NODES.map((node) => {
    const isActive = node.flowModes.includes(activeModeObj.id);
    const isSelected = node.id === selectedNode.id;
    const strokeW = isSelected ? "3" : isActive ? "2" : "1.2";
    const opacity = isActive ? "1" : "0.42";

    const maxTitleChars = Math.max(36, Math.floor((node.w - 30) / 7.5));
    const maxSubChars = Math.max(44, Math.floor((node.w - 30) / 6.2));
    const titleLines = wrapSvgTextLines(node.title, maxTitleChars, 2);
    const subLines = wrapSvgTextLines(node.subtitle, maxSubChars, 3);

    const titleStartY = 42;
    const titleLineHeight = 16;
    const nodeAccent = _themeAccent(node.color);
    const titleSvg = titleLines
      .map(
        (line, idx) =>
          `<text x="14" y="${titleStartY + idx * titleLineHeight}" fill="var(--ink, #f8fafc)" font-family="Inter, system-ui, sans-serif" font-size="13" font-weight="700">${_esc(line)}</text>`
      )
      .join("");

    const subStartY = titleStartY + titleLines.length * titleLineHeight + 4;
    const subLineHeight = 14.5;
    const subSvg = subLines
      .map(
        (line, idx) =>
          `<text x="14" y="${subStartY + idx * subLineHeight}" fill="${idx === 0 ? "var(--ink-2, #cbd5e1)" : "var(--muted, #94a3b8)"}" font-family="Inter, system-ui, sans-serif" font-size="11">${_esc(line)}</text>`
      )
      .join("");

    return `
      <g
        class="arch-svg-node ${isSelected ? "is-selected" : ""}"
        data-arch-node-id="${_esc(node.id)}"
        transform="translate(${node.x}, ${node.y})"
        style="cursor:pointer;opacity:${opacity}"
        role="button"
        tabindex="0"
        aria-label="${_esc(node.title)}"
      >
        <rect
          width="${node.w}"
          height="${node.h}"
          rx="10"
          fill="var(--arch-node-bg, #111a2e)"
          stroke="${_esc(nodeAccent)}"
          stroke-width="${strokeW}"
        />
        <g clip-path="url(#arch_clip_${_esc(node.id)})">
          <rect
            x="12"
            y="9"
            width="${Math.min(node.w - 24, node.badge.length * 6.8 + 16)}"
            height="18"
            rx="4"
            fill="${_esc(nodeAccent)}"
            fill-opacity="0.16"
          />
          <text x="18" y="21" fill="${_esc(nodeAccent)}" font-family="JetBrains Mono, monospace" font-size="10" font-weight="700">
            ${_esc(node.badge)}
          </text>
          ${titleSvg}
          ${subSvg}
        </g>
      </g>
    `;
  }).join("");

  const logosHtml = TECH_STACK_LOGOS.map(
    (tech) => `
      <div class="arch-tech-card" data-tech-id="${_esc(tech.id)}">
        <div class="arch-tech-icon">${tech.svg}</div>
        <div>
          <div class="arch-tech-name">${_esc(tech.name)}</div>
          <div class="arch-tech-role">${_esc(tech.role)}</div>
        </div>
      </div>
    `
  ).join("");

  const compListHtml = (selectedNode.details?.components || [])
    .map((c) => `<li>${_esc(c)}</li>`)
    .join("");
  const codePathsHtml = (selectedNode.details?.codePaths || [])
    .map((cp) => `<code class="mono-tag">${_esc(cp)}</code>`)
    .join(" ");

  return `
    <section class="sector-block" id="animatedArchitectureSection" aria-labelledby="archDiagramHeading">
      <div class="sector-header">
        <div>
          <h2 class="sector-title" id="archDiagramHeading">⚡ Interactive Animated System, Agent &amp; 2-Way LLM Architecture Diagram</h2>
          <p class="sector-desc">
            End-to-end visual blueprint showing how <strong>Antigravity &amp; Claude Code</strong> on the developer workstation read and audit the <strong>46 primary-source feeds</strong> with <strong>Frontier LLMs</strong>, push code and shards to <strong>GitHub Actions &amp; Pages</strong> plus schema/webhook updates to the <strong>Google Sheet &amp; Apps Script Hub</strong>, and power <strong>4 end-user &amp; Local/Cloud LLM personas</strong>.
          </p>
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button type="button" class="btn primary" id="archPlayPauseBtn" data-arch-toggle-play="1">
            ${isPlaying ? "⏸️ Pause Flow Animation" : "▶️ Play Flow Animation"}
          </button>
          <button type="button" class="btn" id="archSpeedBtn" data-arch-cycle-speed="1">
            ⚡ Speed: ${_esc(speedMultiplier)}x
          </button>
        </div>
      </div>

      <div class="arch-mode-bar" role="toolbar" aria-label="Filter animated data flow by lifecycle stage">
        ${modeButtonsHtml}
      </div>
      <div class="arch-mode-caption">
        <strong>Active Flow Stage:</strong> ${_esc(activeModeObj.label)} - ${_esc(activeModeObj.description)}
      </div>

      <div class="arch-canvas-wrap">
        <svg
          id="archAnimatedSvg"
          viewBox="0 0 1550 885"
          width="100%"
          height="auto"
          role="img"
          aria-label="Animated system architecture diagram of The Houston Building Atlas"
        >
          <defs>
            <filter id="archPacketGlow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="3.2" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <clipPath id="arch_zone_clip_1"><rect x="18" y="18" width="462" height="332" rx="14" /></clipPath>
            <clipPath id="arch_zone_clip_2"><rect x="18" y="366" width="462" height="504" rx="14" /></clipPath>
            <clipPath id="arch_zone_clip_3"><rect x="510" y="18" width="500" height="852" rx="14" /></clipPath>
            <clipPath id="arch_zone_clip_4"><rect x="1038" y="18" width="494" height="852" rx="14" /></clipPath>
            ${svgClipPathsHtml}
          </defs>

          <!-- Background Swimlanes -->
          <g clip-path="url(#arch_zone_clip_1)">
            <rect x="18" y="18" width="462" height="332" rx="14" fill="var(--arch-lane-bg, #0d1627)" stroke="var(--arch-lane-border, #1e293b)" stroke-width="1.5" />
            <text x="62" y="45" fill="var(--arch-cyan, #38bdf8)" font-family="Inter, system-ui, sans-serif" font-size="12" font-weight="700">
              1. DEVELOPER COMPUTER &amp; AGENTIC AI CODING LOOP
            </text>
            <text x="62" y="63" fill="var(--muted, #94a3b8)" font-family="Inter, system-ui, sans-serif" font-size="10.5">
              Reads 46 Feeds -&gt; Frontier LLMs -&gt; GitHub &amp; Sheet/Apps Script Push
            </text>
          </g>

          <g clip-path="url(#arch_zone_clip_2)">
            <rect x="18" y="366" width="462" height="504" rx="14" fill="var(--arch-lane-bg, #0d1627)" stroke="var(--arch-lane-border, #1e293b)" stroke-width="1.5" />
            <text x="62" y="393" fill="var(--arch-blue, #60a5fa)" font-family="Inter, system-ui, sans-serif" font-size="11.5" font-weight="700">
              2. EXTERNAL PRIMARY SOURCES (29 ORGS / 46 FEEDS)
            </text>
            <text x="62" y="411" fill="var(--muted, #94a3b8)" font-family="Inter, system-ui, sans-serif" font-size="10.5">
              Read by Developer / Agent LLMs (Local ETL) &amp; GitHub Actions Batch Jobs
            </text>
          </g>

          <g clip-path="url(#arch_zone_clip_3)">
            <rect x="510" y="18" width="500" height="852" rx="14" fill="var(--arch-lane-bg, #0d1627)" stroke="var(--arch-lane-border, #1e293b)" stroke-width="1.5" />
            <text x="528" y="45" fill="var(--arch-emerald, #34d399)" font-family="Inter, system-ui, sans-serif" font-size="12" font-weight="700">
              3. GITHUB PLATFORM, RUNTIME TILES &amp; 2-WAY MODERATION
            </text>
            <text x="528" y="63" fill="var(--muted, #94a3b8)" font-family="Inter, system-ui, sans-serif" font-size="10.5">
              GitHub Actions ETL · Pages Static CDN · WebGL2 Tiles · Sheet &amp; Apps Script
            </text>
          </g>

          <g clip-path="url(#arch_zone_clip_4)">
            <rect x="1038" y="18" width="494" height="852" rx="14" fill="var(--arch-lane-bg, #0d1627)" stroke="var(--arch-lane-border, #1e293b)" stroke-width="1.5" />
            <text x="1056" y="45" fill="var(--arch-amber, #f59e0b)" font-family="Inter, system-ui, sans-serif" font-size="12" font-weight="700">
              4. END-USER PERSONAS &amp; CLOUD / LOCAL LLM COMPANIONS
            </text>
            <text x="1056" y="63" fill="var(--muted, #94a3b8)" font-family="Inter, system-ui, sans-serif" font-size="10.5">
              PH Monitoring · PH Research · Public Enthusiasts · Local/Cloud LLMs (2-Way)
            </text>
          </g>

          <!-- Animated Data-Flow Conduits -->
          ${svgEdgesHtml}

          <!-- Clickable Architecture Nodes -->
          ${svgNodesHtml}
        </svg>
      </div>

      <!-- Selected Node Inspector Card -->
      <div class="arch-inspector-card" id="archNodeInspector">
        <div class="arch-inspector-head">
          <div>
            <span class="mono-tag" style="color:${_esc(_themeAccent(selectedNode.color))}">${_esc(selectedNode.zone)}</span>
            <h3 style="margin:6px 0 2px;font-size:19px;color:var(--ink)">${_esc(selectedNode.title)}</h3>
            <div style="font-size:13px;color:var(--ink-2)">${_esc(selectedNode.details?.summary || selectedNode.subtitle)}</div>
          </div>
          <div style="font-size:12px;color:var(--muted)">Click any node in the diagram above to inspect its pipeline &amp; files</div>
        </div>
        <ul class="arch-inspector-list">${compListHtml}</ul>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:8px">
          <span style="font-size:12px;font-weight:700;color:var(--muted)">Key Code &amp; Data Paths:</span>
          ${codePathsHtml}
        </div>
      </div>

      <!-- Languages, Libraries & Tooling Ribbon with Project Logos -->
      <div style="margin-top:22px">
        <h3 style="font-size:16px;font-weight:700;margin:0 0 10px;color:var(--ink)">
          🛠️ Languages, Open-Source Libraries &amp; AI Tooling Stack (With Project Logos)
        </h3>
        <div class="arch-tech-grid">
          ${logosHtml}
        </div>
      </div>
    </section>
  `;
}
