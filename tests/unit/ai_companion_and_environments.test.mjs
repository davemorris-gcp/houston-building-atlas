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

  // Verify /llms.txt and /llms-full.txt content and Section 2 completeness
  const llmsTxt = fs.readFileSync(path.join(APP_DIR, "llms.txt"), "utf8");
  const llmsFullTxt = fs.readFileSync(path.join(APP_DIR, "llms-full.txt"), "utf8");
  assert.ok(llmsTxt.includes("# The Houston Building Atlas (Preservation Houston)"));
  assert.ok(llmsFullTxt.includes("## Section 1: Active Endangered Property Watchlist"));
  assert.ok(llmsFullTxt.includes("## Section 2: Active Demolition Permits & Texas DSHS Asbestos Alerts"));
  assert.ok(llmsFullTxt.includes("## Section 8: Linked Archival Finding Aids"));
  assert.ok(llmsFullTxt.includes("`26090093`"), "Section 2 must populate COH project numbers");
  assert.ok(llmsFullTxt.includes("DSHS Abatement"), "Section 2 must populate Signal Type labels");
  assert.ok(!/[\u2013\u2014]/.test(llmsTxt), "app/llms.txt contains forbidden U+2013/U+2014 dashes");
  assert.ok(!/[\u2013\u2014]/.test(llmsFullTxt), "app/llms-full.txt contains forbidden U+2013/U+2014 dashes");
});
