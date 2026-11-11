/**
 * Houston Building Atlas - AI Research Companion & Multi-Platform Skill Pack (`app/js/aiCompanion.js`).
 *
 * Provides:
 *   1. Platform-tailored system instructions for ChatGPT Plus/Web, Claude Web/Desktop,
 *      Google Gemini, and CLI/Coding Agents.
 *   2. 1-click Markdown Property Research Dossier + AI Prompt synthesis for any
 *      selected HCAD building, tax parcel, or Lost Houston site.
 *   3. Curated Houston architectural history & preservation test prompts.
 *   4. Environment & Sandbox badge resolution from `public/data/atlas_environments.json`.
 */

import { DEFAULT_ATLAS_BASE_URL, resolveAtlasBaseOriginPath } from "./filterStore.js?v=20261110a";

const SAMPLE_AI_RESEARCH_PROMPTS_SEED_QUESTION =
  "Using The Houston Building Atlas, explain what happened to the 1894 John Henry Kirby Mansion at 2000 Smith St (HCAD 0152300000001), including the October 28, 2026 Smith Street Plaza replat (#RP-2026-435651), the 2-stage City of Houston demolition permits, and the Texas DSHS asbestos filings that preceded its November 2, 2026 loss. Which buildings by its 1928 Tudor Revival remodel architect James Ruskin Bailey are still standing in Houston? Include clickable Atlas links for each.";

export function buildOneClickAiLaunchUrl(platform = "claude", queryOrDossier = "", options = {}) {
  const baseUrl = resolveAtlasBaseOriginPath(options);
  const cleanBase = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  const platKey = String(platform || "claude").trim().toLowerCase();
  if (platKey === "cli") {
    return "public/ai-companion/SKILL.md";
  }
  if (platKey === "gemini") {
    return "https://gemini.google.com/app";
  }

  const maxChars = Number(options.maxQueryChars) > 200 ? Number(options.maxQueryChars) : 3200;
  const rawInput = _sanitizeDashes(String(queryOrDossier || "").trim());
  const isPropertyDossier = rawInput.startsWith("# Houston Building Atlas - Property Research Dossier");

  let promptBody = "";
  if (isPropertyDossier) {
    promptBody = rawInput;
  } else {
    const questionText =
      rawInput ||
      "Summarize what datasets The Houston Building Atlas covers and give 3 example questions I can ask about Houston historic buildings, architects, or demolition permits.";
    promptBody = [
      `Use The Houston Building Atlas (${cleanBase}llms.txt and ${cleanBase}llms-full.txt) to answer accurately with verified 4-tier construction dates, statutory protection rules (Protected Landmark vs Historic District vs Heritage District), Lost Houston status, and clickable Atlas deep links:`,
      "",
      questionText,
    ].join("\n");
  }

  if (promptBody.length > maxChars) {
    promptBody = `${promptBody.slice(0, maxChars - 120).trimEnd()}\n\n[Full corpus & live shards: ${cleanBase}llms-full.txt]`;
  }

  const encoded = encodeURIComponent(promptBody);
  if (platKey === "claude") {
    return `https://claude.ai/new?q=${encoded}`;
  }
  return `https://chatgpt.com/?q=${encoded}`;
}

export const AI_COMPANION_PLATFORMS = Object.freeze([
  {
    id: "chatgpt",
    label: "ChatGPT Plus / Web",
    shortLabel: "ChatGPT",
    actionLabel: "Copy Instructions for ChatGPT",
    subtitle: "1-Click Web Launch, ChatGPT Projects & Custom GPTs",
    launchUrl: buildOneClickAiLaunchUrl("chatgpt", SAMPLE_AI_RESEARCH_PROMPTS_SEED_QUESTION),
    steps: [
      "Option A (Zero Setup - 1 Click): Click 'Open ChatGPT (Pre-Loaded)' below (or click 'Ask ChatGPT' on any Property Dossier or Sample Prompt) to open chatgpt.com with the Atlas context already loaded in the chat box.",
      "Option B (Reusable Project or Custom GPT): Create a Project in ChatGPT, attach llms-full.txt and query_atlas.py, and paste the instructions below.",
    ],
  },
  {
    id: "claude",
    label: "Claude Web / Desktop",
    shortLabel: "Claude",
    actionLabel: "Copy Instructions for Claude",
    subtitle: "1-Click Web Launch, 1-File Skill (.zip) Upload & Claude Projects",
    launchUrl: buildOneClickAiLaunchUrl("claude", SAMPLE_AI_RESEARCH_PROMPTS_SEED_QUESTION),
    steps: [
      "Option A (Zero Setup - 1 Click): Click 'Open Claude (Pre-Loaded)' below (or click 'Ask Claude' on any Property Dossier or Sample Prompt) to open claude.ai/new with the Atlas context already loaded in the chat box.",
      "Option B (1-File Permanent Skill Upload): Click 'Download Claude Skill (.zip)' at the top of this window, then in Claude go to Settings -> Capabilities -> Skills -> Upload a skill and select houston-building-atlas-skill.zip.",
      "Option C (Claude Project): Create a Project in Claude and attach llms-full.txt for offline access to all 1,622 landmark/historic records and 623 Lost Houston sites.",
    ],
  },
  {
    id: "gemini",
    label: "Google Gemini",
    shortLabel: "Gemini",
    actionLabel: "Copy Instructions for Gemini",
    subtitle: "Google Gemini Gems & Web Chat with Python Execution",
    launchUrl: "https://gemini.google.com/app",
    steps: [
      "Option A (Fast Chat): Click 'Copy Property Dossier + Prompt' above (or 'Copy Research Prompt' on any question below) and paste straight into Google Gemini.",
      "Option B (Reusable Gemini Gem): In Google Gemini, create a Gem named 'Houston Building Atlas Historian', attach llms-full.txt and query_atlas.py, and paste the instructions below.",
    ],
  },
  {
    id: "cli",
    label: "CLI & Coding Agents",
    shortLabel: "CLI / Skill",
    actionLabel: "Copy CLI Quick-Start Commands",
    subtitle: "Claude Code, Gemini CLI, Cursor & Local Python / Node Scripts",
    launchUrl: "public/ai-companion/SKILL.md",
    steps: [
      "Unzip houston-building-atlas-skill.zip into your agent's skills/ folder (or download query_atlas.py directly).",
      "Run python3 query_atlas.py --address \"2000 Smith St\" or --architect \"James Ruskin Bailey\" to query all 37 datasets over HTTPS in < 1 second.",
      "The script automatically checks atlas_environments.json so future vanity URL cutovers work with zero reconfiguration.",
    ],
  },
]);

export const SAMPLE_AI_RESEARCH_PROMPTS = Object.freeze([
  {
    id: "kirby_ruskin_bailey",
    category: "Lost Houston & Architect Portfolio",
    title: "Kirby Mansion Demolition & James Ruskin Bailey Portfolio",
    prompt:
      "Using The Houston Building Atlas, explain what happened to the 1894 John Henry Kirby Mansion at 2000 Smith St (HCAD 0152300000001), including the 2-stage City of Houston demolition permits and Texas DSHS asbestos filings that preceded its November 2, 2026 loss. Which buildings by its 1928 Tudor Revival remodel architect James Ruskin Bailey are still standing in Houston? Include clickable Atlas links for each.",
    atlasHash: "?id=lost_kirby_mansion_1894&layers=lost_houston",
    deepLink: "?id=lost_kirby_mansion_1894&layers=lost_houston",
  },
  {
    id: "hahc_landmark_candidates",
    category: "HAHC Landmark Nominations & THC Forms",
    title: "Unprotected Cultural & Architectural Landmark Candidates",
    prompt:
      "We are preparing unprotected landmark nomination candidates for the Houston Archaeological and Historical Commission (HAHC). Using The Houston Building Atlas, provide the 13-digit HCAD accounts, construction dates, architectural/cultural significance, and 1-click printable THC Historic Resources Survey Form links (#thcSurvey=1) for Granada Theater (3700 N Main St), LULAC Council 60 Clubhouse (3004 Bagby St), and Talento Bilingue de Houston (333 S Jensen Dr).",
    atlasHash: "#hcad=0550140020008&thcSurvey=1",
    deepLink: "#hcad=0550140020008&thcSurvey=1",
  },
  {
    id: "heritage_vs_historic_district",
    category: "Statutory Preservation Policy",
    title: "Heritage District vs. COH Historic District Protection",
    prompt:
      "Does the City of Houston's Freedmen's Town Heritage District protect private historic homes from demolition? Compare its legal authority under City of Houston ordinances with a designated COH Historic District like Old Sixth Ward, and include links to both in The Houston Building Atlas.",
    atlasHash: "#scorecard=historic_districts:hist_dist_old_sixth_ward",
    deepLink: "#scorecard=historic_districts:hist_dist_old_sixth_ward",
  },
  {
    id: "freedmens_town_stewardship",
    category: "1836-2026 Chain of Stewardship",
    title: "134-Year Stewardship & Deed Chain (1318 Andrews St)",
    prompt:
      "Using The Houston Building Atlas, trace the historical resident and ownership chain for 1318 Andrews St (HCAD 0090790000009) in Freedmen's Town / Fourth Ward from the 1892 City Directory and 1915 Red Book of Houston through recent Harris County Clerk deeds, and share the Atlas History Tab link.",
    atlasHash: "?color=ownership_tier#hcad=0090790000009&tab=history",
    deepLink: "?color=ownership_tier#hcad=0090790000009&tab=history",
  },
  {
    id: "permit_shorthand_decoder",
    category: "Plain-English Permit Decoder",
    title: "Decode HCAD Appraiser Notes & 2-Stage Demo Permits",
    prompt:
      "A Houston property record shows a City of Houston Type 12 permit for 'BLD DEMO/SEWER DISC' under project #26090093 and an HCAD appraiser note reading '4/1/26 NC ... CDU @ GD'. Translate both into clear plain English for a homeowner or journalist, explaining the difference between Stage 1 and Stage 2 demolition permits.",
    atlasHash: "#hcad=0152300000001&tab=permits",
    deepLink: "#hcad=0152300000001&tab=permits",
  },
  {
    id: "council_district_scorecard",
    category: "Preservation Scorecards & Ch. 42",
    title: "City Council District C vs. Citywide Preservation Scorecard",
    prompt:
      "Using The Houston Building Atlas Preservation Scorecards, summarize how historic pre-1940 housing stock, statutory landmark/district protection, and Chapter 42 minimum lot size (SMLS) block-face tools work in City Council District C compared with the citywide rollup, with links to both scorecards.",
    atlasHash: "#scorecard=council_districts:council_dist_coh_all",
    deepLink: "#scorecard=council_districts:council_dist_coh_all",
  },
]);

function _sanitizeDashes(str) {
  return String(str || "")
    .replace(/\u2014/g, "-")
    .replace(/\u2013/g, "-");
}

export function buildPlatformSystemInstructions(platform = "chatgpt", options = {}) {
  const baseUrl = resolveAtlasBaseOriginPath(options);
  const cleanBase = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  const platKey = String(platform || "chatgpt").trim().toLowerCase();

  if (platKey === "cli") {
    return [
      "# Quick-Start CLI Commands (Python 3 or Node.js 18+)",
      "",
      "# 1. Query any Houston street address across all 37 datasets:",
      "python3 skills/houston-building-atlas/scripts/query_atlas.py --address \"2000 Smith St\"",
      "python3 skills/houston-building-atlas/scripts/query_atlas.py --address \"3700 N Main St\"",
      "",
      "# 2. Query by 13-digit HCAD account number:",
      "python3 skills/houston-building-atlas/scripts/query_atlas.py --hcad 0152300000001",
      "",
      "# 3. Query an Architect or Architectural Style portfolio:",
      "python3 skills/houston-building-atlas/scripts/query_atlas.py --architect \"James Ruskin Bailey\"",
      "",
      "# 4. Query a Historic District or City Council District Scorecard:",
      "python3 skills/houston-building-atlas/scripts/query_atlas.py --scorecard \"Old Sixth Ward\"",
      "",
      "# 5. List the 15-Point Endangered Property Watchlist:",
      "python3 skills/houston-building-atlas/scripts/query_atlas.py --watchlist",
    ].join("\n");
  }

  const toolSpecificLine =
    platKey === "claude"
      ? "Consult the uploaded Project Knowledge files (`llms-full.txt`, `SKILL.md`, and `query_atlas.py`) and use web fetch on the Atlas endpoints whenever needed."
      : platKey === "gemini"
      ? "Consult the attached Gem Knowledge files (`llms-full.txt` and `query_atlas.py`) and ground every Houston architectural or property claim in the Atlas data."
      : "Consult the attached Project/GPT files (`llms-full.txt` and `query_atlas.py` via Data Analysis / Code Interpreter) and browse the live Atlas URLs whenever needed.";

  return [
    "You are the Houston Building Atlas AI Research Companion, an expert Houston architectural historian, preservation policy analyst, and public-records researcher powered by The Houston Building Atlas (Preservation Houston).",
    "",
    toolSpecificLine,
    "",
    `Canonical Live Atlas URL: ${cleanBase}`,
    `LLM Discovery Index: ${cleanBase}llms.txt`,
    `Full Reference Corpus: ${cleanBase}llms-full.txt`,
    `Environment Manifest: ${cleanBase}public/data/atlas_environments.json`,
    "",
    "Follow these 7 Golden Rules on every response:",
    "1. 4-Tier Construction Date Provenance: Never trust HCAD's rounded decade remodel/effective placeholders (1920, 1930, 1940, 1950) at face value when Sanborn Fire Insurance Maps (1877-1955), Houston City Directories (1866-1926), COH HPO Historic District surveys, or Preservation Houston curated overrides establish an earlier or more accurate date. State the evidentiary tier clearly.",
    "2. Statutory Protection Accuracy (COH Code Ch. 33 & Ch. 42):",
    "   - Protected Landmark (PLM): permanent binding protection against demolition.",
    "   - COH Historic District (23 districts): contributing buildings require an HAHC Certificate of Appropriateness (COA).",
    "   - Standard City Landmark (LM outside a Historic District): only a 90-day demolition waiting period waiver (Sec. 33-250).",
    "   - City of Houston Heritage District (e.g., Freedmen's Town Heritage District): applies ONLY to public right-of-way infrastructure and city-owned land; it does NOT protect private residential or commercial buildings from demolition.",
    "   - National Register (NRHP) & Recorded Texas Historic Landmark (RTHL): federal/state honors that do not block private demolition using private funds unless paired with a local Protected Landmark designation.",
    "3. Check Lost Houston (623 Demolished Sites) & the 15-Point Watchlist (34 Sites): Before stating that a historic building is still standing, check whether it has been demolished and graduated to Lost Houston (for example, the 1894 John Henry Kirby Mansion at 2000 Smith St, HCAD 0152300000001, remodeled 1928 by James Ruskin Bailey, demolished Nov 2, 2026 -> lost_kirby_mansion_1894).",
    "4. Decode HCAD Appraiser Shorthand & COH Permits into Plain English: Distinguish Stage 1 Sewer Disconnect plumbing permits (Type 12/4P, 'BLD DEMO/SEWER DISC') from Stage 2 structural Building Demolition permits (Type SD/4D, 'BLD DEMO'), and translate HCAD shorthand (NC = New Construction, CDU = Condition/Desirability/Utility grade, REMOD = Remodel, ADDN = Addition) while citing the verbatim record.",
    "5. Trace 5-Era Harris County Clerk Deed Chains (1836-2026): Vol/Page (1836-1960), 7-digit Film Code (1961-1999), 10-digit File # (2000-2015), and RP-YYYY-NNNNNN (2016-present).",
    "6. Respect Texas Tax Code Sec. 25.025 Confidentiality: Never speculate on or attempt to deanonymize homeowner identities on parcels flagged conf: 1.",
    "7. Always Emit Clickable Deep Links to the Live Atlas and Primary Sources:",
    `   - Property Overview: ${cleanBase}#hcad=<13-digit-hcad>`,
    `   - Permits & Appraiser Tab: ${cleanBase}#hcad=<13-digit-hcad>&tab=permits`,
    `   - 1836-2026 Ownership History Tab: ${cleanBase}#hcad=<13-digit-hcad>&tab=history`,
    `   - 1-Click Printable THC Survey Form: ${cleanBase}#hcad=<13-digit-hcad>&thcSurvey=1`,
    `   - Lost Houston Site: ${cleanBase}?id=<lost_id>&layers=lost_houston`,
    `   - Architect / Style Portfolio: ${cleanBase}#portfolio=arch_<slug>`,
    `   - Preservation Scorecard: ${cleanBase}#scorecard=<layer>:<id>`,
    `   - Plat & Property Monitor Watchlist: ${cleanBase}plat-monitor.html#tab=watchlist&watch=<id>`,
  ].join("\n");
}

export function buildPropertyAiResearchDossier(contextOrProps = {}, options = {}) {
  const baseUrl = resolveAtlasBaseOriginPath(options);
  const cleanBase = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  const isWrappedContext =
    contextOrProps &&
    typeof contextOrProps === "object" &&
    ("props" in contextOrProps ||
      "permitRecord" in contextOrProps ||
      "override" in contextOrProps ||
      "ownerTenures" in contextOrProps ||
      "hpoRows" in contextOrProps ||
      "hcadNum" in contextOrProps ||
      "lostId" in contextOrProps);

  const context = isWrappedContext ? contextOrProps : {};
  const props = isWrappedContext ? context.props || {} : contextOrProps || {};
  const override = context.override || options.override || {};
  const permitRecord =
    context.permitRecord || options.permitShardRec || options.permitRecord || {};
  const ownerTenures = Array.isArray(context.ownerTenures)
    ? context.ownerTenures
    : Array.isArray(options.ownerTenures)
    ? options.ownerTenures
    : [];
  const hpoRows = Array.isArray(context.hpoRows)
    ? context.hpoRows
    : Array.isArray(options.hpoRows)
    ? options.hpoRows
    : options.hpoSurveyRec
    ? Array.isArray(options.hpoSurveyRec.rows)
      ? options.hpoSurveyRec.rows
      : [options.hpoSurveyRec]
    : [];
  const archivalCitations = Array.isArray(context.archivalCitations)
    ? context.archivalCitations
    : Array.isArray(options.archivalCitations)
    ? options.archivalCitations
    : [];
  const activeAlerts = Array.isArray(context.activeAlerts)
    ? context.activeAlerts
    : Array.isArray(options.activeAlerts)
    ? options.activeAlerts
    : [];
  const haifThreads = Array.isArray(context.haifThreads)
    ? context.haifThreads
    : Array.isArray(options.haifThreads)
    ? options.haifThreads
    : [];
  const goodBrickAwards = Array.isArray(context.goodBrickAwards)
    ? context.goodBrickAwards
    : Array.isArray(options.goodBrickAwards)
    ? options.goodBrickAwards
    : [];

  const rawHcad = String(
    context.hcadNum ||
      options.hcadNum ||
      props.hcad_num ||
      override.hcad_num ||
      ""
  )
    .split("#")[0]
    .trim();
  const hcad = /^\d{10,13}$/.test(rawHcad) ? rawHcad.padStart(13, "0") : "";
  const lostId = String(
    context.lostId ||
      options.lostId ||
      (props.layer_key === "lost_houston" ? props.id : "") ||
      override.lost_houston_id ||
      ""
  ).trim();

  const rawOwnerCandidate = String(
    props.owner_name ||
      props.owner ||
      options.ownerDisplay ||
      (Array.isArray(permitRecord.co) ? permitRecord.co.join("; ") : "") ||
      ""
  ).trim();
  const isConf = Boolean(
    permitRecord.conf === 1 ||
      permitRecord.confidential ||
      props.conf === 1 ||
      props.confidential ||
      props.is_confidential ||
      override.conf === 1 ||
      override.confidential ||
      options.isConfidential ||
      /^confidential\b/i.test(rawOwnerCandidate) ||
      /25\.025/.test(rawOwnerCandidate)
  );
  const secretOwnerTokens = isConf
    ? [
        props.owner_name,
        props.owner,
        options.ownerDisplay,
        ...(Array.isArray(permitRecord.co) ? permitRecord.co : []),
        ...ownerTenures.map((t) => t?.owner_name || t?.owner),
      ]
        .flatMap((v) => String(v || "").split(";"))
        .map((s) => s.trim())
        .filter((s) => s && !/^confidential\b/i.test(s) && !/25\.025/.test(s))
    : [];

  const address = override.address || props.address || props.situs_address || "";
  let candidateName =
    options.title ||
    override.building_name ||
    props.building_name ||
    props.landmark_name ||
    props.name ||
    address ||
    (hcad ? `HCAD ${hcad}` : "Houston Historic Property Research Context");
  if (
    isConf &&
    secretOwnerTokens.some((tok) =>
      String(candidateName).toLowerCase().includes(tok.toLowerCase())
    )
  ) {
    candidateName =
      address || (hcad ? `HCAD ${hcad}` : "Houston Historic Property Research Context");
  }
  const name = candidateName;
  const rawAltNames = Array.isArray(options.altNames)
    ? options.altNames
    : Array.isArray(props.alt_names)
    ? props.alt_names
    : [];
  const altNames = isConf
    ? rawAltNames.filter(
        (a) =>
          !secretOwnerTokens.some((tok) =>
            String(a || "").toLowerCase().includes(tok.toLowerCase())
          )
      )
    : rawAltNames;
  const yearBuilt =
    override.year_built ||
    props.year_built ||
    props.built_year ||
    permitRecord.yr ||
    "";
  const yearDemolished =
    override.demolished_date ||
    props.demolished_year ||
    props.year_demolished ||
    "";
  const isDemolished = Boolean(
    override.suppress_only ||
      override.is_demolished ||
      props.demolished ||
      props.layer_key === "lost_houston" ||
      yearDemolished
  );
  const architect = override.architect || props.architect || "";
  const style =
    options.displayStyle ||
    override.style ||
    props.style ||
    props.bld_style ||
    (hpoRows[0]?.style || "");

  if (!hcad && !lostId && !address && !props.building_name && !props.landmark_name && !props.name) {
    return _sanitizeDashes(
      [
        "# Houston Building Atlas - AI Research Companion Context",
        "",
        `Use The Houston Building Atlas (${cleanBase}llms-full.txt and ${cleanBase}llms.txt) to research Houston's built environment, verify construction dates across 37 datasets, decode HCAD/COH permits, and return clickable deep links.`,
        "",
        "## My Research Question:",
        "[Type your question about a Houston building, street address, architect, or neighborhood here]",
      ].join("\n")
    );
  }

  const lines = [
    `# Houston Building Atlas - Pre-Joined Property Research Dossier: ${name}`,
    "",
    "Please use the verified multi-dataset dossier below from The Houston Building Atlas (Preservation Houston) to answer my research question accurately, and include the clickable Atlas deep links in your response.",
    "",
    "## 1. Core Property Identity & Chronology",
    `- **Property / Building Name:** ${name}`,
    ...(altNames.length > 0 ? [`- **Historical / Alternate Names:** ${altNames.join(" | ")}`] : []),
    `- **Street Address:** ${address || "N/A"}`,
    ...(hcad ? [`- **13-Digit HCAD Account:** \`${hcad}\``] : []),
    `- **Current Status:** ${
      isDemolished
        ? `DEMOLISHED / LOST HOUSTON (${yearDemolished || "Confirmed Demolished"})`
        : "EXTANT (Standing)"
    }`,
    `- **Verified Construction Year:** \`${yearBuilt || "Unknown"}\` (${
      override.year_source || props.year_source || "Atlas Multi-Source Chronology"
    })`,
  ];

  if (permitRecord.remod || props.remodel_year || props.yr_remodel) {
    lines.push(
      `- **HCAD Remodel / Effective Year:** \`${
        permitRecord.remod || props.remodel_year || props.yr_remodel
      }\``
    );
  }
  if (architect) {
    lines.push(`- **Architect / Builder:** ${architect}`);
  }
  if (style) {
    lines.push(`- **Architectural Style:** ${style}`);
  }
  if (props.historic_district) {
    lines.push(`- **Historic District:** ${props.historic_district}`);
  }
  if (goodBrickAwards.length > 0) {
    const gbSummary = goodBrickAwards
      .map((gb) =>
        typeof gb === "string"
          ? gb
          : `${gb.award_year || gb.year || ""} (${gb.recipient || gb.building_name || "Good Brick Award"})`.trim()
      )
      .filter(Boolean)
      .join("; ");
    if (gbSummary) {
      lines.push(`- **Preservation Houston Good Brick Award:** ${gbSummary}`);
    }
  }
  if (override.notes || props.description || props.narrative || props.source_citation) {
    lines.push(
      `- **Curated Historical Narrative:** ${
        override.notes || props.description || props.narrative || props.source_citation
      }`
    );
  }

  if (hpoRows.length > 0) {
    lines.push("", "## 2. City of Houston HPO Historic District Survey Status");
    for (const r of hpoRows) {
      lines.push(
        `- **District:** ${r.district_name || r.district || props.historic_district || "Historic District"} | **Status:** ${
          r.contributing_status || r.status || "Surveyed"
        } | **Survey Year:** \`${r.survey_year || r.year || "N/A"}\` | **Style:** ${
          r.style || "N/A"
        }`
      );
    }
  }

  lines.push("", "## 3. Ownership & Chain of Stewardship (TX Tax Code Sec. 25.025 Safe)");
  if (isConf) {
    lines.push(
      "- **Statutory Confidentiality:** Flagged `conf: 1` under Texas Tax Code Sec. 25.025 (Individual homeowner name suppressed)."
    );
  } else {
    if (Array.isArray(permitRecord.co) && permitRecord.co.length > 0) {
      lines.push(`- **Current Certified Roll Owner:** ${permitRecord.co.join("; ")}`);
    } else if (options.ownerDisplay) {
      lines.push(`- **Current Owner:** ${options.ownerDisplay}`);
    } else if (props.owner_name || props.owner) {
      lines.push(`- **Current Owner:** ${props.owner_name || props.owner}`);
    }
    if (ownerTenures.length > 0) {
      lines.push("- **22-Year Certified HCAD Ownership Chain (2005-2026):**");
      for (const t of ownerTenures.slice(0, 8)) {
        const clerk =
          t.clerk_file_num || t.clerkFile
            ? ` (Clerk File \`${t.clerk_file_num || t.clerkFile}\`)`
            : "";
        const ddate = t.deed_date || t.deedDate ? `, Deed \`${t.deed_date || t.deedDate}\`` : "";
        lines.push(
          `  - \`${t.roll_years || t.years}\`: ${t.owner_name || t.owner}${ddate}${clerk}`
        );
      }
    }
  }

  if (
    permitRecord.note ||
    (Array.isArray(permitRecord.p) && permitRecord.p.length > 0) ||
    activeAlerts.length > 0
  ) {
    lines.push("", "## 4. Permits, Appraiser Notes & Multi-Agency Demolition Signals");
    if (permitRecord.note) {
      lines.push(`- **Verbatim HCAD Appraiser Note:** \`${permitRecord.note}\``);
    }
    for (const al of activeAlerts.slice(0, 5)) {
      lines.push(
        `- **Active Watch Alert [${al.severity || "watch"}]:** \`${
          al.issue_date || al.date || al.latest_filing_date || ""
        }\` ${al.permit_stage_label || al.badge || al.permit_type || ""} (\`#${
          al.permit_num || al.notification_num || ""
        }\`) - ${al.description || al.headline || ""}`
      );
    }
    if (Array.isArray(permitRecord.p)) {
      for (const p of permitRecord.p.slice(0, 10)) {
        lines.push(
          `- **Permit \`#${p.id || ""}\` (\`${p.dt || ""}\`, ${
            p.tp || p.cat || "Permit"
          }):** \`${p.d || ""}\``
        );
      }
    }
  }

  if (archivalCitations.length > 0 || haifThreads.length > 0) {
    lines.push("", "## 5. Linked TARO 2.0, Houston Public Library & HAIF Archival Citations");
    for (const ac of archivalCitations.slice(0, 6)) {
      lines.push(
        `- **${ac.repository || ac.repo || "Archive"}:** ${ac.item_title || ac.title || ""} (${
          ac.box_folder || ac.date || ""
        }) - ${ac.url || ""}`
      );
    }
    for (const ht of haifThreads.slice(0, 4)) {
      lines.push(
        `- **HAIF Thread:** ${ht.title || ht.t || "Architectural Discussion"} - ${
          ht.url || ht.u || ""
        }`
      );
    }
  }

  lines.push("", "## 6. Verified Deep Links (Include in Response)");
  if (options.shareUrl) {
    lines.push(`- **Direct Shareable Link:** ${options.shareUrl}`);
  }
  if (hcad) {
    lines.push(`- **Atlas Property Overview:** ${cleanBase}#hcad=${hcad}`);
    lines.push(`- **Atlas Permits & Appraiser Shorthand Tab:** ${cleanBase}#hcad=${hcad}&tab=permits`);
    lines.push(`- **Atlas 1836-2026 Ownership History Tab:** ${cleanBase}#hcad=${hcad}&tab=history`);
    lines.push(
      `- **1-Click Printable THC Historic Resources Survey Form:** ${cleanBase}#hcad=${hcad}&thcSurvey=1`
    );
  }
  if (lostId) {
    lines.push(`- **Lost Houston Demolished Footprint:** ${cleanBase}?id=${lostId}&layers=lost_houston`);
  }
  lines.push(
    "",
    "---",
    "## My Question About This Property:",
    `Summarize the architectural and historical significance of ${name}${
      address ? ` (${address})` : ""
    }, explain what the permits and ownership records show, and highlight any preservation status or risks.`
  );

  return _sanitizeDashes(lines.join("\n"));
}
