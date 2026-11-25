import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  AI_COMPANION_PLATFORMS,
  SAMPLE_AI_RESEARCH_PROMPTS,
  buildOneClickAiLaunchUrl,
  buildPlatformSystemInstructions,
  buildPropertyAiResearchDossier,
  buildSelfContainedAiPrompt,
} from "../../app/js/aiCompanion.js";
import {
  DEFAULT_ATLAS_BASE_URL,
  DEFAULT_ATLAS_SANDBOX_URL,
  DEFAULT_FILTER_STATE,
  resolveAtlasBaseOriginPath,
  parseHashToState,
  serializeStateToHash,
  buildShareableUrl,
} from "../../app/js/filterStore.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const APP_DIR = path.join(REPO_ROOT, "app");

test("UT-AI-1: Centralized environment URLs, resolveAtlasBaseOriginPath priority, and atlas_environments.json contract", () => {
  assert.equal(
    DEFAULT_ATLAS_BASE_URL,
    "https://davemorris-gcp.github.io/houston-building-atlas/"
  );
  assert.equal(
    DEFAULT_ATLAS_SANDBOX_URL,
    "https://davemorris-gcp.github.io/houston-building-atlas/"
  );

  // 1. Explicit options.baseOriginPath takes highest priority
  assert.equal(
    resolveAtlasBaseOriginPath({ baseOriginPath: "https://atlas.preservationhouston.org/" }),
    "https://atlas.preservationhouston.org/"
  );

  // 2. globalThis.__ATLAS_BASE_URL__ override
  const prevGlobal = globalThis.__ATLAS_BASE_URL__;
  try {
    globalThis.__ATLAS_BASE_URL__ = "https://atlas.preservationhouston.org";
    assert.equal(
      resolveAtlasBaseOriginPath({}),
      "https://atlas.preservationhouston.org"
    );
  } finally {
    if (prevGlobal === undefined) {
      delete globalThis.__ATLAS_BASE_URL__;
    } else {
      globalThis.__ATLAS_BASE_URL__ = prevGlobal;
    }
  }

  // 3. Fallback when window is undefined in Node test runner
  assert.equal(resolveAtlasBaseOriginPath({}), DEFAULT_ATLAS_BASE_URL);

  // 4. Validate app/public/data/atlas_environments.json on disk
  const envPath = path.join(APP_DIR, "public", "data", "atlas_environments.json");
  assert.ok(fs.existsSync(envPath), "atlas_environments.json must exist on disk");
  const envDoc = JSON.parse(fs.readFileSync(envPath, "utf8"));
  assert.equal(envDoc.schema_version, 1);
  assert.ok(
    envDoc.active_environment === "production" || envDoc.active_environment === "sandbox"
  );
  assert.equal(envDoc.canonical_base_url, DEFAULT_ATLAS_BASE_URL);
  assert.equal(envDoc.vanity_cutover_ready, true);
  assert.equal(envDoc.environments?.production?.base_url, DEFAULT_ATLAS_BASE_URL);
  assert.equal(envDoc.environments?.sandbox?.base_url, DEFAULT_ATLAS_SANDBOX_URL);
  assert.equal(
    envDoc.environments?.production?.llms_txt_url,
    `${DEFAULT_ATLAS_BASE_URL}llms.txt`
  );
  assert.equal(
    envDoc.environments?.production?.llms_full_txt_url,
    `${DEFAULT_ATLAS_BASE_URL}llms-full.txt`
  );
  assert.equal(
    envDoc.environments?.production?.ai_companion_dir_url,
    `${DEFAULT_ATLAS_BASE_URL}public/ai-companion/`
  );
});

test("UT-AI-2: filterStore URL hash and query codec supports #aiCompanion=1, platform tabs, and 13-digit HCAD deep links", () => {
  // Boolean flag #aiCompanion=1 defaults tab to chatgpt
  const parsedFlag = parseHashToState("#aiCompanion=1");
  assert.equal(parsedFlag.aiCompanion, true);
  assert.equal(parsedFlag.aiCompanionTab, "chatgpt");

  // Platform tab slugs: chatgpt, claude, gemini, cli
  for (const tab of ["chatgpt", "claude", "gemini", "cli"]) {
    const parsedTab = parseHashToState(`#aiCompanion=${tab}`);
    assert.equal(parsedTab.aiCompanion, true);
    assert.equal(parsedTab.aiCompanionTab, tab);
  }

  // Query alias ?ai_companion=claude and ?ai=gemini
  const parsedAlias1 = parseHashToState("", "?ai_companion=claude");
  assert.equal(parsedAlias1.aiCompanion, true);
  assert.equal(parsedAlias1.aiCompanionTab, "claude");

  const parsedAlias2 = parseHashToState("", "?ai=gemini");
  assert.equal(parsedAlias2.aiCompanion, true);
  assert.equal(parsedAlias2.aiCompanionTab, "gemini");

  // 13-digit HCAD account passed directly to #aiCompanion=0152300000001
  const parsedHcad = parseHashToState("#aiCompanion=0152300000001");
  assert.equal(parsedHcad.aiCompanion, true);
  assert.equal(parsedHcad.aiCompanionTab, "chatgpt");
  assert.equal(parsedHcad.selection?.hcadNum, "0152300000001");

  // Serialization round-trip for boolean, explicit platform string, and aiCompanionTab option
  const hashDefault = serializeStateToHash(DEFAULT_FILTER_STATE, null, {
    includeViewport: false,
    aiCompanion: true,
    aiCompanionTab: "chatgpt",
  });
  assert.ok(hashDefault.includes("aiCompanion=1"), `Expected aiCompanion=1 in ${hashDefault}`);

  const hashClaude = serializeStateToHash(DEFAULT_FILTER_STATE, null, {
    includeViewport: false,
    aiCompanion: true,
    aiCompanionTab: "claude",
  });
  assert.ok(hashClaude.includes("aiCompanion=claude"), `Expected aiCompanion=claude in ${hashClaude}`);

  const shareGemini = buildShareableUrl(DEFAULT_FILTER_STATE, null, {
    baseOriginPath: "https://atlas.preservationhouston.org/",
    includeViewport: false,
    selectedHcad: "0550140020008",
    aiCompanion: true,
    aiCompanionTab: "gemini",
  });
  assert.ok(shareGemini.startsWith("https://atlas.preservationhouston.org/?"));
  assert.ok(shareGemini.includes("hcad=0550140020008"));
  assert.ok(shareGemini.includes("aiCompanion=gemini"));
});

test("UT-AI-3: AI_COMPANION_PLATFORMS, SAMPLE_AI_RESEARCH_PROMPTS, and buildPlatformSystemInstructions uphold contracts", () => {
  assert.equal(AI_COMPANION_PLATFORMS.length, 4);
  const platformIds = AI_COMPANION_PLATFORMS.map((p) => p.id);
  assert.deepEqual(platformIds, ["chatgpt", "claude", "gemini", "cli"]);

  for (const plat of AI_COMPANION_PLATFORMS) {
    assert.ok(plat.label, `Platform ${plat.id} missing label`);
    assert.ok(plat.shortLabel, `Platform ${plat.id} missing shortLabel`);
    assert.ok(plat.actionLabel, `Platform ${plat.id} missing actionLabel`);
    assert.ok(plat.subtitle, `Platform ${plat.id} missing subtitle`);
    assert.ok(plat.launchUrl, `Platform ${plat.id} missing launchUrl`);
    assert.ok(Array.isArray(plat.steps) && plat.steps.length >= 2);

    const instructions = buildPlatformSystemInstructions(plat.id, {
      baseOriginPath: "https://atlas.preservationhouston.org/",
    });
    assert.ok(instructions.length > 200);
    assert.ok(!/[\u2013\u2014]/.test(instructions), `Platform ${plat.id} instructions contain forbidden dashes`);
    if (plat.id === "cli") {
      assert.ok(instructions.includes("query_atlas.py --address"));
      assert.ok(instructions.includes("--architect \"James Ruskin Bailey\""));
    } else {
      assert.ok(instructions.includes("https://atlas.preservationhouston.org/llms-full.txt"));
      assert.ok(instructions.includes("Texas Tax Code Sec. 25.025"));
      assert.ok(instructions.includes("Freedmen's Town Heritage District"));
    }
  }

  assert.equal(SAMPLE_AI_RESEARCH_PROMPTS.length, 6);
  for (const item of SAMPLE_AI_RESEARCH_PROMPTS) {
    assert.ok(item.id && item.category && item.title && item.prompt && item.linkLabel);
    assert.ok(item.atlasHash && item.deepLink);
    assert.equal(item.atlasHash, item.deepLink);
    assert.ok(!/[\u2013\u2014]/.test(item.prompt), `Sample prompt ${item.id} contains forbidden dashes`);
  }
});

test("UT-AI-4: buildPropertyAiResearchDossier supports dual signatures, fail-closed TX Tax Code Sec. 25.025 redaction, and dash sanitization", () => {
  // 1. Empty context returns starter research prompt template
  const emptyDossier = buildPropertyAiResearchDossier({});
  assert.ok(emptyDossier.includes("# Houston Building Atlas - AI Research Companion Context"));
  assert.ok(!/[\u2013\u2014]/.test(emptyDossier));

  // 2. Wrapped context signature ({ props, override, permitRecord, ownerTenures, hpoRows, archivalCitations })
  const kirbyDossier = buildPropertyAiResearchDossier(
    {
      hcadNum: "0152300000001",
      lostId: "lost_kirby_mansion_1894",
      props: {
        address: "2000 SMITH ST",
        building_name: "John Henry Kirby Mansion",
        year_built: 1894,
        demolished_year: 2026,
        demolished: true,
        architect: "Olle J. Lorehn; James Ruskin Bailey (1928 remodel)",
        style: "Victorian Chateauesque / Tudor Revival",
        narrative: "Built 1894\u20131928\u2014demolished Nov 2, 2026.",
      },
      permitRecord: {
        yr: 1894,
        remod: 1928,
        co: ["2000 SMITH TOWER LP"],
        note: "4/1/26 BLD DEMO/SEWER DISC\u2014STAGE 2 ISSUED",
        p: [{ id: "26090093", dt: "2026-10-20", tp: "SD", d: "BLD DEMO" }],
      },
      ownerTenures: [
        {
          roll_years: "2018-2026",
          deed_date: "2018-05-01",
          clerk_file_num: "RP-2018-199201",
          owner_name: "2000 SMITH TOWER LP",
        },
      ],
    },
    { baseOriginPath: "https://atlas.preservationhouston.org/" }
  );
  assert.ok(kirbyDossier.includes("John Henry Kirby Mansion"));
  assert.ok(kirbyDossier.includes("`0152300000001`"));
  assert.ok(kirbyDossier.includes("DEMOLISHED / LOST HOUSTON"));
  assert.ok(kirbyDossier.includes("2000 SMITH TOWER LP"));
  assert.ok(kirbyDossier.includes("https://atlas.preservationhouston.org/#hcad=0152300000001&thcSurvey=1"));
  assert.ok(
    kirbyDossier.includes(
      "https://atlas.preservationhouston.org/?id=lost_kirby_mansion_1894&layers=lost_houston"
    )
  );
  assert.ok(
    !/[\u2013\u2014]/.test(kirbyDossier),
    "buildPropertyAiResearchDossier must sanitize all U+2013 and U+2014 dashes"
  );

  // 3. Direct (props, options) signature used by app.js _buildActivePropertyAiDossier()
  const directDossier = buildPropertyAiResearchDossier(
    {
      hcad_num: "0550140020008",
      address: "3700 N MAIN ST",
      building_name: "Granada Theater",
      year_built: 1946,
      year_source: "Preservation Houston Archival Override",
      historic_district: "HighFirst",
    },
    {
      title: "Granada Theater (Heights Theater)",
      altNames: ["Heights Theater", " Orion Theater "],
      displayStyle: "Art Moderne",
      ownerDisplay: "HEIGHTS THEATER HOLDINGS LLC",
      goodBrickAwards: [{ award_year: 2018, recipient: "Heights Theater Restoration" }],
      haifThreads: [{ title: "Heights Theater At 3700 N. Main St.", url: "https://www.houstonarchitecture.com/topic/1/" }],
      shareUrl: "https://davemorris-gcp.github.io/houston-building-atlas/?hcad=0550140020008",
    }
  );
  assert.ok(directDossier.includes("Granada Theater (Heights Theater)"));
  assert.ok(directDossier.includes("Art Moderne"));
  assert.ok(directDossier.includes("HEIGHTS THEATER HOLDINGS LLC"));
  assert.ok(directDossier.includes("2018 (Heights Theater Restoration)"));
  assert.ok(directDossier.includes("Heights Theater At 3700 N. Main St."));

  // 4. Fail-closed TX Tax Code Sec. 25.025 redaction across all confidentiality signals
  const secretOwner = "JANE Q PUBLIC PROTECTED JUDGE";
  const confDossier = buildPropertyAiResearchDossier(
    {
      hcad_num: "0621100000014",
      address: "1127 KEY ST",
      owner_name: secretOwner,
      owner: secretOwner,
      year_built: 1925,
    },
    {
      title: `${secretOwner} RESIDENCE`,
      ownerDisplay: secretOwner,
      permitShardRec: {
        conf: 1,
        co: [secretOwner],
      },
      ownerTenures: [
        {
          roll_years: "2010-2026",
          owner_name: secretOwner,
          clerk_file_num: "RP-2010-000111",
        },
      ],
    }
  );
  assert.ok(
    confDossier.includes("Texas Tax Code Sec. 25.025"),
    "Confidential dossier must state TX Tax Code Sec. 25.025 redaction"
  );
  assert.ok(
    !confDossier.includes(secretOwner),
    "Confidential homeowner name must never leak into title, owner row, or tenure chain"
  );
  assert.ok(
    !confDossier.includes("RP-2010-000111"),
    "Confidential tenure chain must be suppressed"
  );

  // 5. Pre-masked ownerDisplay with raw permitRecord.co and altNames must still scrub secretOwner
  const preMaskedDossier = buildPropertyAiResearchDossier(
    {
      hcad_num: "0621100000014",
      address: "1127 KEY ST",
      building_name: `${secretOwner} HOUSE`,
      alt_names: [`${secretOwner} RESIDENCE`, "HISTORIC NORHILL COTTAGE"],
      conf: 1,
    },
    {
      ownerDisplay: "Confidential (TX Tax Code Sec. 25.025)",
      permitShardRec: {
        conf: 1,
        co: [secretOwner],
      },
      ownerTenures: [
        {
          roll_years: "2015-2026",
          owner_name: secretOwner,
        },
      ],
    }
  );
  assert.ok(
    !preMaskedDossier.includes(secretOwner),
    "Pre-masked ownerDisplay must not bypass secretOwner scrubbing in candidateName or altNames"
  );
  assert.ok(
    preMaskedDossier.includes("HISTORIC NORHILL COTTAGE"),
    "Non-confidential alternate names should be preserved"
  );

  // 6. Full 1-click Claude/ChatGPT launch URL for 1917 WASHINGTON AVE (0052090000011) preserves
  //    top Research Question, camelCase HPO Contributing status, compact oh tenures, cd hit,
  //    storefront number clarification, and full untruncated Section 6 deep links.
  const washingtonDossier = buildPropertyAiResearchDossier(
    {
      hcad_num: "0052090000011",
      address: "1917 WASHINGTON AVE",
      year_built: 1920,
      remodel_year: 2023,
      historic_district: "Old Sixth Ward Historic District",
    },
    {
      title: "1917 WASHINGTON AVE",
      displayStyle: "Masonry Bearing",
      ownerDisplay: "BE 1911 WASHINGTON REALTY LTD",
      hpoSurveyRec: {
        districtName: "Old Sixth Ward Historic District",
        statusCode: "C",
        statusLabel: "Contributing",
        surveyYearLabel: "",
        usableStyle: "",
      },
      permitShardRec: {
        yr: 1920,
        remod: 2023,
        co: ["1601 RICHMOND REALT Y LTD 55%"],
        cd: {
          e: 1892,
          q: 'Houston city directory for 1892-93 (page 115): "end San Jacinto. Fortney Mary (wid John), r. 1917 Washington cor White."',
        },
        oh: "2026~20260128~~BE 1911 WASHINGTON REALTY LTD|2022-2025~20220526~~1911 WASHINGTON HOLDING LLC (DBA JULEP)|2016-2021~20160801~R2016-337670~WASHINGTON WHITE LTD (DBA JULEP/WASHINGTON GYM)",
        note: "1919- JULEP BAR  1923- WASHINGTON OFFICE CO  1925- SOHOU BAR",
        p: [
          { id: "1871524", dt: "2022-03-22", tp: "Commercial Addition / Remodel", d: "ADD GREASE TRAP TO EXISTING BAR/LOUNGE  1-2-5-A2-B" },
          { id: "1841709", dt: "2021-08-09", tp: "Commercial Addition / Remodel", d: "4 712 SQ FT FITNESS CENTER REMODEL 1-1-5-A3-B '12" },
          { id: "1666445", dt: "2018-05-09", tp: "Commercial Addition / Remodel", d: "CONVERT 8 003 SQ FT TO OFFICE 1-2-5-B-B '12 IBC 10" },
          { id: "1494816", dt: "2016-01-08", tp: "Commercial Addition / Remodel", d: "CORE RESTROOM OFFICE REMODEL 1-2-5-B-B 2006 IBC" },
          { id: "1373262", dt: "2013-10-04", tp: "Commercial Addition / Remodel", d: "BAR REMODEL" },
          { id: "1357699", dt: "2013-06-17", tp: "Commercial Addition / Remodel", d: "DEMO OF INTERIOR NON-LOAD BEARING NON-FIRE RATED W" },
          { id: "1348988", dt: "2013-04-11", tp: "Commercial Addition / Remodel", d: "EXTERIOR BRICK REPAIRS PER WORKSHEET 2006 IBC" },
          { id: "1321515", dt: "2012-08-29", tp: "Commercial Addition / Remodel", d: "DEMO OF NON LOAD BEARING NON FIRE RATED INTERIOR P" },
          { id: "1296047", dt: "2012-01-13", tp: "Commercial Addition / Remodel", d: "DEMO OF NON LOAD BEARING NON FIRE RATED INTERIOR P" },
          { id: "1292933", dt: "2011-12-27", tp: "Commercial Addition / Remodel", d: "CONV. RETAIL FOR GYM EXPANSION 1-2-5-A3-B 100% '06" },
        ],
      },
      shareUrl:
        "https://davemorris-gcp.github.io/houston-building-atlas/?base=dark&buildings=buildings&layers=permit_watch,good_brick,landmarks,historic_districts,heritage_districts,lost_houston&hcad=0052090000011",
    }
  );
  assert.equal(
    buildSelfContainedAiPrompt(washingtonDossier),
    washingtonDossier,
    "buildSelfContainedAiPrompt must not prepend a duplicate header to a Property Research Dossier"
  );
  const claudeLaunchUrl = buildOneClickAiLaunchUrl("claude", washingtonDossier);
  const decodedClaudeQuery = decodeURIComponent(claudeLaunchUrl.split("?q=")[1] || "");
  assert.ok(decodedClaudeQuery.includes("**Research Question:** Summarize the architectural and historical significance of 1917 WASHINGTON AVE"));
  assert.ok(decodedClaudeQuery.includes("Earliest verified listing `1892`"));
  assert.ok(decodedClaudeQuery.includes("Contributing (Protected under COH Code Ch. 33"));
  assert.ok(decodedClaudeQuery.includes("2022-2025`: 1911 WASHINGTON HOLDING LLC (DBA JULEP)"));
  assert.ok(decodedClaudeQuery.includes("street storefront numbers on the parcel, not calendar years"));
  assert.ok(decodedClaudeQuery.includes("1-Click Printable THC Historic Resources Survey Form:"));
  assert.ok(decodedClaudeQuery.includes("#hcad=0052090000011&thcSurvey=1"));
});

test("UT-AI-5: DOM wiring in index.html/app.js, portable skill mirror parity, and zero forbidden dashes or staff personal names", () => {
  const indexHtml = fs.readFileSync(path.join(APP_DIR, "index.html"), "utf8");
  const appJs = fs.readFileSync(path.join(APP_DIR, "js", "app.js"), "utf8");
  const indexCss = fs.readFileSync(path.join(APP_DIR, "styles", "index.css"), "utf8");

  const requiredDomIds = [
    "btn-more-ai-companion",
    "btn-open-ai-companion-from-about",
    "ai-companion-modal",
    "btn-close-ai-companion-modal",
    "btn-download-skill-zip",
    "btn-download-llms-full",
    "btn-download-query-py",
    "btn-download-skill-md",
    "btn-open-llms-txt",
    "ai-companion-dossier-preview",
    "btn-launch-dossier-claude",
    "btn-launch-dossier-chatgpt",
    "btn-launch-dossier-gemini",
    "btn-copy-ai-active-dossier",
    "btn-ai-tab-chatgpt",
    "btn-ai-tab-claude",
    "btn-ai-tab-gemini",
    "btn-ai-tab-cli",
    "ai-platform-steps-list",
    "btn-copy-ai-platform-instructions",
    "btn-launch-ai-platform",
    "ai-platform-instructions-preview",
    "ai-sample-prompts-grid",
  ];
  for (const id of requiredDomIds) {
    assert.ok(indexHtml.includes(`id="${id}"`), `app/index.html missing id="${id}"`);
  }
  assert.ok(appJs.includes('id="btn-inspector-ai-companion"'));
  assert.ok(indexCss.includes(".ai-companion-modal-card"));
  assert.ok(indexCss.includes(".ai-sample-prompt-card"));

  // Verify buildOneClickAiLaunchUrl generates valid pre-filled URLs for Claude and ChatGPT
  const claudeUrl = buildOneClickAiLaunchUrl("claude", "What happened to the Kirby Mansion?");
  assert.ok(claudeUrl.startsWith("https://claude.ai/new?q="));
  assert.ok(decodeURIComponent(claudeUrl).includes("llms.txt"));
  assert.ok(decodeURIComponent(claudeUrl).includes("What happened to the Kirby Mansion?"));

  const chatgptUrl = buildOneClickAiLaunchUrl("chatgpt", "What happened to the Kirby Mansion?");
  assert.ok(chatgptUrl.startsWith("https://chatgpt.com/?q="));
  assert.ok(decodeURIComponent(chatgptUrl).includes("llms-full.txt"));

  const geminiUrl = buildOneClickAiLaunchUrl("gemini", "What happened to the Kirby Mansion?");
  assert.equal(geminiUrl, "https://gemini.google.com/app");
  const selfPrompt = buildSelfContainedAiPrompt("What happened to the Kirby Mansion?");
  assert.ok(selfPrompt.includes("llms.txt") && selfPrompt.includes("What happened to the Kirby Mansion?"));

  const zipPath = path.join(APP_DIR, "public", "ai-companion", "houston-building-atlas-skill.zip");
  assert.ok(fs.existsSync(zipPath), "houston-building-atlas-skill.zip must exist on disk");
  assert.ok(fs.statSync(zipPath).size > 2000, "houston-building-atlas-skill.zip must be non-empty");

  // Verify byte-for-byte parity between skills/houston-building-atlas/ and app/public/ai-companion/
  // (supporting both flat download URLs and standard Agent Skill scripts/ + references/ subdirectories)
  const mirrorPairs = [
    ["skills/houston-building-atlas/SKILL.md", "app/public/ai-companion/SKILL.md"],
    ["skills/houston-building-atlas/scripts/query_atlas.py", "app/public/ai-companion/query_atlas.py"],
    ["skills/houston-building-atlas/scripts/query_atlas.mjs", "app/public/ai-companion/query_atlas.mjs"],
    [
      "skills/houston-building-atlas/references/PLATFORM_SETUP_GUIDE.md",
      "app/public/ai-companion/PLATFORM_SETUP_GUIDE.md",
    ],
    [
      "skills/houston-building-atlas/scripts/query_atlas.py",
      "app/public/ai-companion/scripts/query_atlas.py",
    ],
    [
      "skills/houston-building-atlas/scripts/query_atlas.mjs",
      "app/public/ai-companion/scripts/query_atlas.mjs",
    ],
    [
      "skills/houston-building-atlas/references/PLATFORM_SETUP_GUIDE.md",
      "app/public/ai-companion/references/PLATFORM_SETUP_GUIDE.md",
    ],
  ];
  for (const [srcRel, dstRel] of mirrorPairs) {
    const srcText = fs.readFileSync(path.join(REPO_ROOT, srcRel), "utf8");
    const dstText = fs.readFileSync(path.join(REPO_ROOT, dstRel), "utf8");
    assert.equal(srcText, dstText, `Mirrored AI skill file ${dstRel} drifted from ${srcRel}`);
    assert.ok(!/[\u2013\u2014]/.test(srcText), `${srcRel} contains forbidden U+2013/U+2014 dashes`);
  }

  // Verify /llms.txt and /llms-full.txt content, Phase 1 public-only Section 1, and Section 2 completeness
  const llmsTxt = fs.readFileSync(path.join(APP_DIR, "llms.txt"), "utf8");
  const llmsFullTxt = fs.readFileSync(path.join(APP_DIR, "llms-full.txt"), "utf8");
  assert.ok(llmsTxt.includes("# The Houston Building Atlas (Preservation Houston)"));
  assert.ok(
    llmsFullTxt.includes(
      "## Section 1: Public Planning Commission Pre-1976 Filings & Recent Lost Houston Graduations"
    )
  );
  assert.equal(
    llmsFullTxt.includes("## Section 1: Active Endangered Property Watchlist"),
    false,
    "app/llms-full.txt must not leak private Watchlist dossier tables"
  );
  assert.equal(
    llmsFullTxt.includes("plat-monitor.html#tab=watchlist&watch="),
    false,
    "app/llms-full.txt must not leak private Watchlist deep links"
  );
  assert.ok(llmsFullTxt.includes("## Section 2: Active Demolition Permits & Texas DSHS Asbestos Alerts"));
  assert.ok(llmsFullTxt.includes("## Section 8: Linked Archival Finding Aids"));
  const sec2Slice = (llmsFullTxt.split("## Section 2:")[1] || "").split("## Section 3:")[0] || "";
  assert.match(sec2Slice, /`26\d{6}`/, "Section 2 must populate 8-digit COH project numbers");
  assert.ok(llmsFullTxt.includes("DSHS Abatement"), "Section 2 must populate Signal Type labels");
  assert.ok(!/[\u2013\u2014]/.test(llmsTxt), "app/llms.txt contains forbidden U+2013/U+2014 dashes");
  assert.ok(!/[\u2013\u2014]/.test(llmsFullTxt), "app/llms-full.txt contains forbidden U+2013/U+2014 dashes");
});


import {
  AtlasNodeClient,
  sanitizeSpreadsheetCell,
  formatCsvRows,
  SUGGESTION_CSV_COLUMNS,
  FIELD_SURVEY_CSV_COLUMNS,
} from "../../skills/houston-building-atlas/scripts/query_atlas.mjs";
import {
  ARCHITECTURE_FLOW_MODES,
  TECH_STACK_LOGOS,
  ARCHITECTURE_NODES,
  ARCHITECTURE_EDGES,
  renderArchitectureDiagramSectionHtml,
} from "../../app/js/architectureDiagram.js";

test("UT-AI-6: 2-Way Community & Local LLM Contribution API in query_atlas.mjs upholds moderation, formula safety, and dry-run contracts", async () => {
  const client = new AtlasNodeClient();

  // 1. Dry-run property correction with Local LLM agent_tool tagging
  const corrRes = await client.submitCorrection(
    {
      hcad_num: "0550140020008",
      address: "3700 N Main St",
      building_name: "Granada Theater",
      suggested_year_built: 1946,
      architect: "R. G. Schneider",
      style: "Art Moderne",
      source_type: "ARCHIVAL_CITATION",
      source_citation: "Houston Post, Oct 12, 1946",
      agent_tool: "Ollama Llama-3.3-70B",
    },
    { dryRun: true }
  );
  assert.equal(corrRes.ok, true);
  assert.equal(corrRes.mode, "dry_run");
  assert.equal(corrRes.payload.hcad_num, "0550140020008");
  assert.equal(corrRes.payload.suggested_year_built, 1946);
  assert.ok(corrRes.payload.source_citation.includes("[AI_AGENT:Ollama Llama-3.3-70B]"));

  // 2. Dry-run Walk-the-Block field survey
  const surveyRes = await client.submitFieldSurvey(
    {
      hcad_num: "0052090000011",
      address: "1917 Washington Ave",
      historic_district: "Old Sixth Ward",
      integrity_grade: "Intact",
      condition_rating: "Good",
      field_notes: "Masonry bearing commercial storefront intact",
      agent_tool: "Claude Code",
    },
    { dryRun: true }
  );
  assert.equal(surveyRes.ok, true);
  assert.equal(surveyRes.payload.action, "field_survey");
  assert.equal(surveyRes.payload.source_type, "FIELD_SURVEY_OBSERVATION");
  assert.ok(surveyRes.payload.source_citation.includes("[AI_AGENT:Claude Code]"));

  // 3. Dry-run 1-click quick flag
  const flagRes = await client.submitQuickFlag(
    {
      flag_type: "claim_dispute",
      hcad_num: "0152300000001",
      address: "2000 Smith St",
      disputed_field: "year_built",
      disputed_value: "1928",
      suggested_value: "1894",
      notes: "Built 1894 for John Henry Kirby; 1928 is James Ruskin Bailey remodel",
      agent_tool: "Gemini CLI",
    },
    { dryRun: true }
  );
  assert.equal(flagRes.ok, true);
  assert.equal(flagRes.payload.suggested_year_built, 1894);
  assert.ok(flagRes.payload.source_citation.includes("[CLAIM_DISPUTE:year_built]"));

  // 4. Formula-injection neutralization & RFC 4180 CSV export
  assert.equal(sanitizeSpreadsheetCell("=CMD|' /C calc'!A0"), "'=CMD|' /C calc'!A0");
  const csvOut = client.exportContributionsCsv({
    kind: "suggestions",
    records: [corrRes.payload],
  });
  assert.ok(csvOut.startsWith(SUGGESTION_CSV_COLUMNS.join(",")));
  assert.ok(csvOut.includes("0550140020008"));

  const surveyCsvOut = client.exportContributionsCsv({
    kind: "surveys",
    records: [surveyRes.payload],
  });
  assert.ok(surveyCsvOut.startsWith(FIELD_SURVEY_CSV_COLUMNS.join(",")));
  assert.ok(surveyCsvOut.includes("0052090000011"));
});

test("UT-AI-7: Interactive Animated System & LLM Architecture Diagram upholds node, flow, logo, and data-ecosystem.html contracts", () => {
  assert.equal(ARCHITECTURE_FLOW_MODES.length, 5);
  assert.ok(TECH_STACK_LOGOS.length >= 8);
  assert.ok(ARCHITECTURE_NODES.length >= 10);
  assert.ok(ARCHITECTURE_EDGES.length >= 19);

  const edgeIds = new Set(ARCHITECTURE_EDGES.map((e) => e.id));
  for (const reqEdge of [
    "edge_cadastre_to_dev",
    "edge_permits_to_dev",
    "edge_pres_to_llm",
    "edge_archives_to_llm",
    "edge_dev_to_github",
    "edge_dev_to_sheet",
  ]) {
    assert.ok(edgeIds.has(reqEdge), `Expected architecture diagram edge ${reqEdge}`);
  }

  const html = renderArchitectureDiagramSectionHtml({
    activeFlowMode: "all",
    isPlaying: true,
    speedMultiplier: 1,
    selectedNodeId: "user_local_cloud_llms",
  });
  assert.ok(html.includes('id="animatedArchitectureSection"'));
  assert.ok(html.includes('id="archAnimatedSvg"'));
  assert.ok(html.includes("<animateMotion"));
  assert.ok(html.includes("Antigravity &amp; Claude Code"));
  assert.ok(html.includes("GitHub Repository &amp; Actions Batch Pipelines"));
  assert.ok(html.includes("End-User Cloud &amp; Local LLM Tools (2-Way Skill)"));
  assert.ok(!/[\u2013\u2014]/.test(html), "Architecture diagram HTML must not contain U+2013 or U+2014 dashes");
  assert.ok(!html.includes("admin.html"), "Architecture diagram must not reference nonexistent admin.html");
  assert.ok(html.includes("var(--arch-node-bg"), "Architecture diagram nodes must use theme-adaptive --arch-node-bg variable");
  assert.ok(html.includes("var(--arch-lane-bg"), "Architecture diagram lanes must use theme-adaptive --arch-lane-bg variable");
  assert.ok(html.includes("var(--ink"), "Architecture diagram text must use theme-adaptive --ink variable");

  const ecosystemHtml = fs.readFileSync(path.join(APP_DIR, "data-ecosystem.html"), "utf8");
  assert.ok(ecosystemHtml.includes('data-view-mode="architecture"'));
  assert.ok(ecosystemHtml.includes("renderArchitectureDiagramSectionHtml"));
  assert.ok(ecosystemHtml.includes("--arch-canvas-bg: #eef2f6"), "data-ecosystem.html must define light-theme --arch-canvas-bg");
  assert.ok(ecosystemHtml.includes('href="index.html#audit=1"'));
  assert.ok(ecosystemHtml.includes('href="index.html#admin=1"'));
  assert.ok(!ecosystemHtml.includes('href="admin.html"'), "data-ecosystem.html must not link to nonexistent admin.html");

  const parsedAdmin = parseHashToState("#admin=1");
  assert.equal(parsedAdmin.activeAdmin, true);
});
