import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";

import {
  CONFIDENTIAL_OWNER_TOKENS,
  PLACEHOLDER_OWNER_TOKENS,
  buildResearchKitDeedChainView,
  decodeCompactDeedChain,
  decodeCompactOwnerHistory,
  decompressOwnerHistoryBytes,
  describeDeedChainCoverage,
  formatClerkInstrumentToken,
  formatClerkPartyTokens,
  isConfidentialOwnerName,
  isEntityOrTrustOwnerName,
  isPlaceholderOwnerName,
  parseClerkLegalTokens,
  parseCompactDeedWireEntries,
} from "../../app/js/clerkSearchHelpers.js";
import {
  buildStewardshipTimelineModel,
  decodeParcelOwnershipXray,
} from "../../app/js/guidedChaptersAndPortfolios.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..", "..");

test("UT-2: ordinary isRedactedHomestead retains 2005-2026 tenures while all CONFIDENTIAL_OWNER_TOKENS suppress all 3 surfaces", () => {
  const sampleOh =
    "2019-2026~20190415~R2019-149789~GARCIA ELENA & MARCOS|2005-2018~<=1988~~THOMPSON HAROLD E";
  const sampleDeeds = "20190415:R2019-149789:101:D";

  // 1. Ordinary owner-occupied homestead (isRedactedHomestead === true, isConfidential === false)
  const homesteadXray = decodeParcelOwnershipXray({
    featureProps: {
      hcad_num: "0621100000014",
      owner: "GARCIA ELENA & MARCOS",
      own_tier: "homestead",
      use_category: "Single-Family",
    },
    permitShardRec: {
      ot: "homestead",
      d: sampleDeeds,
      oh: sampleOh,
      // m and co intentionally omitted by homestead privacy redaction
    },
  });
  assert.equal(homesteadXray.isConfidential, false);
  assert.equal(homesteadXray.isRedactedHomestead, true);
  assert.equal(homesteadXray.billingAddress, "");
  assert.equal(homesteadXray.careOfAgent, "");
  assert.equal(homesteadXray.ownerHistoryTenures.length, 2);
  assert.equal(homesteadXray.priorOwners.length, 1);
  assert.equal(homesteadXray.priorOwners[0].ownerName, "THOMPSON HAROLD E");
  assert.equal(homesteadXray.priorOwners[0].spanLabel, "2005-2018");
  assert.equal(homesteadXray.recordedTenureSince, "2019-04-15");

  const homesteadRk = buildResearchKitDeedChainView(
    "0621100000014",
    "0621100000014",
    { d: sampleDeeds, oh: sampleOh },
    "GARCIA ELENA & MARCOS"
  );
  assert.equal(homesteadRk.hidden, false);
  assert.equal(homesteadRk.isEmptyCama, false);
  assert.match(homesteadRk.html, /THOMPSON HAROLD E/);
  assert.match(homesteadRk.html, /Copy Prior Owner/);

  // 2. Every canonical statutory TX Tax Code Sec. 25.025 confidential token suppresses all 3 surfaces
  assert.equal(CONFIDENTIAL_OWNER_TOKENS.size, 7);
  for (const confTok of CONFIDENTIAL_OWNER_TOKENS) {
    assert.equal(isConfidentialOwnerName(confTok), true, `Expected confidential: ${confTok}`);
    assert.deepEqual(formatClerkPartyTokens(confTok), []);
    assert.deepEqual(decodeCompactOwnerHistory(sampleOh, confTok), []);

    const confXray = decodeParcelOwnershipXray({
      featureProps: {
        hcad_num: "0010020000001",
        owner: confTok,
        own_tier: "homestead",
        use_category: "Single-Family",
      },
      permitShardRec: {
        ot: "homestead",
        d: sampleDeeds,
        oh: sampleOh,
      },
    });
    assert.equal(confXray.isConfidential, true);
    assert.deepEqual(confXray.ownerHistoryTenures, []);
    assert.deepEqual(confXray.priorOwners, []);
    assert.equal(confXray.recordedTenureSince, "");

    const confRk = buildResearchKitDeedChainView(
      "0010020000001",
      "0010020000001",
      { d: sampleDeeds, oh: sampleOh },
      confTok
    );
    assert.equal(confRk.count, 0, `Expected 0 deeds in Step 3 for ${confTok}`);
    assert.ok(!confRk.html.includes("GARCIA ELENA"), `Leaked owner in Step 3 for ${confTok}`);
    assert.ok(!confRk.html.includes("THOMPSON HAROLD"), `Leaked prior owner in Step 3 for ${confTok}`);
    assert.ok(!confRk.html.includes("owner-party"), `Leaked Grantee copy pill in Step 3 for ${confTok}`);
    assert.ok(!confRk.html.includes("RP-2019-149789"), `Leaked Clerk File # in Step 3 for ${confTok}`);
    assert.ok(!confRk.html.includes("149789"), `Leaked raw Clerk digits in Step 3 for ${confTok}`);

    const confTimeline = buildStewardshipTimelineModel({
      featureProps: {
        hcad_num: "0010020000001",
        owner: confTok,
        year_built: 1928,
      },
      permitShardRec: {
        d: sampleDeeds,
        oh: sampleOh,
        flip: 35,
      },
    });
    assert.deepEqual(confTimeline.ownerTenures, []);
    for (const ev of confTimeline.events) {
      assert.notEqual(ev.kind, "owner_tenure");
      assert.notEqual(ev.kind, "deed", `Leaked deed event in Surface 3 for ${confTok}`);
      assert.notEqual(ev.kind, "rapid_flip", `Leaked rapid_flip event in Surface 3 for ${confTok}`);
      assert.ok(!String(ev.title).includes("GARCIA ELENA"));
      assert.ok(!String(ev.title).includes("THOMPSON HAROLD"));
      assert.ok(!String(ev.subtitle || "").includes("149789"));
      assert.ok(!String(ev.citation || "").includes("149789"));
    }

    // Also verify mixed props ({ owner_name: "JANE ROE", owner: confTok } and { owner: "JANE ROE", owner_name: confTok })
    for (const mixedProps of [
      { hcad_num: "0010020000001", owner_name: "JANE ROE", owner: confTok, year_built: 1928 },
      { hcad_num: "0010020000001", owner: "JANE ROE", owner_name: confTok, year_built: 1928 },
    ]) {
      const mixedXray = decodeParcelOwnershipXray({
        featureProps: mixedProps,
        permitShardRec: { ot: "homestead", d: sampleDeeds, oh: sampleOh },
      });
      assert.equal(mixedXray.isConfidential, true);
      assert.deepEqual(mixedXray.ownerHistoryTenures, []);
      assert.equal(mixedXray.clerkUrl.includes("149789"), false);

      const mixedTl = buildStewardshipTimelineModel({
        featureProps: mixedProps,
        permitShardRec: { d: sampleDeeds, oh: sampleOh, flip: 35 },
      });
      assert.deepEqual(mixedTl.ownerTenures, []);
      assert.equal(mixedTl.events.some((ev) => ev.kind === "deed" || ev.kind === "rapid_flip"), false);
    }

    // Statutory Sec. 25.025 redaction applies even when shard.ot is non-homestead (e.g., 0010020000001 ot='anonymity_shield')
    const confNonHomesteadXray = decodeParcelOwnershipXray({
      featureProps: {
        hcad_num: "0010020000001",
        owner: confTok,
        own_tier: "homestead",
        use_category: "Residential",
      },
      permitShardRec: {
        ot: "anonymity_shield",
        m: "510 BERING DR STE 680, HOUSTON, TX 77057",
        d: sampleDeeds,
        oh: sampleOh,
      },
    });
    assert.equal(confNonHomesteadXray.isConfidential, true);
    assert.equal(confNonHomesteadXray.isRedactedHomestead, true);
    assert.equal(confNonHomesteadXray.mailingAddress, "");
    assert.equal(confNonHomesteadXray.careOf, "");
    assert.equal(
      confNonHomesteadXray.recordedOwner,
      "Protected Individual Homestead (TX Tax Code Sec. 25.025)"
    );
  }

  // 3. Map-clicked ordinary homestead with empty/placeholder props.owner hydrates recordedOwner from active 2026 oh tenure
  const mapClickedXray = decodeParcelOwnershipXray({
    featureProps: {
      hcad_num: "0621100000014",
      owner: "Public / Unlisted",
      own_tier: "homestead",
      use_category: "Residential",
    },
    permitShardRec: {
      ot: "homestead",
      d: sampleDeeds,
      oh: sampleOh,
    },
  });
  assert.equal(mapClickedXray.recordedOwner, "GARCIA ELENA & MARCOS");
});

test("UT-5: decodeCompactOwnerHistory handles explicit names, elided active owner, rest-joined tildes, and placeholders", () => {
  // Explicit 4-field wire entries + defensive rest-join if ~ ever appeared inside field[3]
  const decoded = decodeCompactOwnerHistory(
    "2021-2026~20210803~R2021-555123~CARTER LINDA & JAMES|2005-2020~<=1988~~MORALES ROBERTO~JR",
    "PUBLIC / UNLISTED"
  );
  assert.equal(decoded.length, 2);
  assert.equal(decoded[0].ownerName, "CARTER LINDA & JAMES");
  assert.equal(decoded[0].spanLabel, "2021-2026");
  assert.equal(decoded[0].acqDate, "2021-08-03");
  assert.equal(decoded[0].deed?.copyValue, "RP-2021-555123");
  assert.equal(decoded[1].ownerName, "MORALES ROBERTO~JR");
  assert.equal(decoded[1].isPreMainframe, true);
  assert.equal(decoded[1].acqDate, "<=1988");

  // Elided '' field[3] on index 0 falls back ONLY when currentOwnerName is non-empty and non-placeholder
  const fallbackValid = decodeCompactOwnerHistory(
    "2020-2026~20200214~R2020-100200~|2005-2019~19960501~~PRIOR FAMILY TRUST",
    "BENNETT SARAH M"
  );
  assert.equal(fallbackValid.length, 2);
  assert.equal(fallbackValid[0].ownerName, "BENNETT SARAH M");

  assert.ok(
    PLACEHOLDER_OWNER_TOKENS.has("OWNER OF RECORD HCAD") &&
      PLACEHOLDER_OWNER_TOKENS.has("PUBLIC / UNLISTED")
  );
  for (const placeholder of [
    "",
    "PUBLIC / UNLISTED",
    "Owner of Record (HCAD)",
    "UNKNOWN",
    "PENDING",
  ]) {
    assert.equal(isPlaceholderOwnerName(placeholder), true);
    const fallbackSkipped = decodeCompactOwnerHistory(
      "2020-2026~20200214~R2020-100200~|2005-2019~19960501~~PRIOR FAMILY TRUST",
      placeholder
    );
    // Elided index 0 is cleanly skipped rather than emitting blank or placeholder attribution!
    assert.equal(fallbackSkipped.length, 1);
    assert.equal(fallbackSkipped[0].ownerName, "PRIOR FAMILY TRUST");
  }
});

test("UT-6: decompressOwnerHistoryBytes decodes raw RFC 1952 gzip, plain UTF-8 JSON (with/without BOM/ws), and rejects corrupt/HTML buffers", async () => {
  const expectedObj = {
    "0621100000014": "2019-2026~20190415~R2019-149789~OWNER A|2005-2018~<=1988~~OWNER B",
    "0010020000016": "2005-2026~<=1988~~HISTORIC BUILDING LP (DBA COMMERCE PLACE)",
  };
  const jsonText = JSON.stringify(expectedObj);

  // 1. Raw RFC 1952 gzip bytes (0x1f 0x8b) via Uint8Array and ArrayBuffer
  const gzBuf = zlib.gzipSync(Buffer.from(jsonText, "utf8"), { level: 9 });
  assert.equal(gzBuf[0], 0x1f);
  assert.equal(gzBuf[1], 0x8b);
  const fromGzUint8 = await decompressOwnerHistoryBytes(new Uint8Array(gzBuf));
  assert.deepEqual(fromGzUint8, expectedObj);
  const exactArrayBuf = gzBuf.buffer.slice(gzBuf.byteOffset, gzBuf.byteOffset + gzBuf.byteLength);
  const fromGzArrayBuf = await decompressOwnerHistoryBytes(exactArrayBuf);
  assert.deepEqual(fromGzArrayBuf, expectedObj);

  // 2. Pre-inflated plain UTF-8 JSON (0x7b), with leading whitespace, and with UTF-8 BOM (0xEF 0xBB 0xBF)
  const plainUtf8 = new TextEncoder().encode(jsonText);
  assert.deepEqual(await decompressOwnerHistoryBytes(plainUtf8), expectedObj);

  const wsUtf8 = new TextEncoder().encode(`  \n\t${jsonText}\n`);
  assert.deepEqual(await decompressOwnerHistoryBytes(wsUtf8), expectedObj);

  const bomBytes = new Uint8Array([0xef, 0xbb, 0xbf, ...wsUtf8]);
  assert.deepEqual(await decompressOwnerHistoryBytes(bomBytes), expectedObj);

  // 3. Predictable rejections on empty, whitespace-only, truncated gzip, HTML 404 fallback, and JSON array root
  await assert.rejects(() => decompressOwnerHistoryBytes(new Uint8Array(0)), /empty/i);
  await assert.rejects(
    () => decompressOwnerHistoryBytes(new TextEncoder().encode("   \r\n ")),
    /whitespace/i
  );
  await assert.rejects(
    () => decompressOwnerHistoryBytes(new Uint8Array(gzBuf.subarray(0, 10))),
    /gunzip/i
  );
  await assert.rejects(
    () => decompressOwnerHistoryBytes(new TextEncoder().encode("<!DOCTYPE html><html>404</html>")),
    /HTML/i
  );
  await assert.rejects(
    () => decompressOwnerHistoryBytes(new TextEncoder().encode('["not-an-object"]')),
    /Unexpected leading byte 0x5b/i
  );
  await assert.rejects(
    () => decompressOwnerHistoryBytes(new Uint8Array(zlib.gzipSync(Buffer.from('["not-an-object"]', "utf8")))),
    /plain JSON object/i
  );
});

test("UT-7, REG-1, REG-3 & REG-4: 3-surface view models, Clerk token deduplication, <=1988 timeline sort, and legacy fallbacks", () => {
  // 1. Step 3 buildResearchKitDeedChainView: stale activeBoxHcadNum guard + non-parcel guard
  const ignored = buildResearchKitDeedChainView(
    "0621100000014",
    "0010020000016",
    { oh: "2005-2026~<=1988~~OWNER A" },
    "OWNER A"
  );
  assert.equal(ignored.ignore, true);
  assert.equal(ignored.html, "");

  const nonParcel = buildResearchKitDeedChainView("school_101", "school_101", null, "");
  assert.equal(nonParcel.hidden, true);

  // Multi-owner chain + unpaired Clerk deed preserved under "Additional Recorded Clerk Transfers"
  const multiRk = buildResearchKitDeedChainView(
    "0621100000014",
    "0621100000014",
    {
      oh: "2019-2026~20190415~R2019-149789~NAVARRO LUCIA|2005-2018~19980601~~KENDALL THOMAS R",
      d: "20190415:R2019-149789:1:D|20010510:FC:123-45-6789:2:D",
    },
    "NAVARRO LUCIA"
  );
  assert.equal(multiRk.count, 3); // 2 tenures + 1 unpaired Film Code deed
  assert.match(multiRk.html, /2 HCAD Owners \(2005-2026\)/);
  assert.match(multiRk.html, /data-rk-copy="RP-2019-149789"/);
  assert.match(multiRk.html, /data-rk-copy="NAVARRO LUCIA"/);
  assert.match(multiRk.html, />KENDALL THOMAS R</);
  assert.match(multiRk.html, /data-rk-copy="KENDALL THOMAS"/);
  assert.match(multiRk.html, /Additional Recorded Clerk Transfers/);
  assert.match(multiRk.html, /123-45-6789/);

  // Single-owner <=1988 continuous baseline, pre-2005 continuous, and post-2005 replat (start_yr > 2005)
  const preMainRk = buildResearchKitDeedChainView(
    "0152300000001",
    "0152300000001",
    { oh: "2005-2026~<=1988~~HERITAGE STEWARD INC", d: "" },
    "HERITAGE STEWARD INC"
  );
  assert.match(preMainRk.html, /Continuous HCAD Roll Tenure Since &lt;=1988/);

  const pre2005Rk = buildResearchKitDeedChainView(
    "0152300000001",
    "0152300000001",
    { oh: "2005-2026~19970820~~HERITAGE STEWARD INC", d: "" },
    "HERITAGE STEWARD INC"
  );
  assert.match(pre2005Rk.html, /Zero post-2005 HCAD ownership changes recorded/);

  const replatRk = buildResearchKitDeedChainView(
    "0629990000001",
    "0629990000001",
    { oh: "2014-2026~20140301~~TOWNHOUSE REPLAT OWNER", d: "" },
    "TOWNHOUSE REPLAT OWNER"
  );
  assert.match(replatRk.html, /Parcel Account Created \/ Replatted in 2014/);

  // Multi-owner post-2005 replat uses actual tenure bounds (2016-2026) instead of hardcoded (2005-2026)
  const post2005MultiRk = buildResearchKitDeedChainView(
    "0629990000002",
    "0629990000002",
    { oh: "2021-2026~20210401~~BETA LLC|2016-2020~20160201~~ACME LLC", d: "" },
    "BETA LLC"
  );
  assert.match(post2005MultiRk.html, /2 HCAD Owners \(2016-2026\)/);

  // Undated 00000000 deed sentinel suppressed in parseCompactDeedWireEntries and buildStewardshipTimelineModel
  const parsedUndated = parseCompactDeedWireEntries(
    "00000000:FC:123-45-6789:10:D|20190415:R2019-149789:11:D"
  );
  assert.equal(parsedUndated.length, 2);
  assert.equal(parsedUndated[0].isoDate, "");
  assert.equal(parsedUndated[0].token, "FC:123-45-6789");
  assert.equal(parsedUndated[1].isoDate, "2019-04-15");
  const undatedTimeline = buildStewardshipTimelineModel({
    featureProps: { hcad_num: "0621100000014", year_built: 1928 },
    permitShardRec: { d: "00000000:FC:123-45-6789:10:D|20190415:R2019-149789:11:D" },
  });
  assert.equal(
    undatedTimeline.events.some((e) => e.year === 0 || String(e.sortKey).startsWith("0000")),
    false,
    "Undated 00000000 HCAD deed sentinel must not emit a year-0000 timeline event"
  );

  // 2. X-Ray Recorded Tenure Since when single continuous tenure (0 priorOwners) vs multi-owner priorOwners
  const singleXray = decodeParcelOwnershipXray(
    { ot: "corporate_llc", oh: "2005-2026~<=1988~~SMITH TOWER CORP" },
    { hcad_num: "0152300000001", owner: "SMITH TOWER CORP", use_category: "Commercial" }
  );
  assert.equal(singleXray.priorOwners.length, 0);
  assert.equal(singleXray.recordedTenureSince, "<=1988 (HCAD Mainframe Baseline)");
  assert.match(singleXray.recordedTenureTooltip, /January 2, 1988/);
  assert.match(singleXray.recordedTenureTooltip, /Harris County Archives/);
  assert.match(singleXray.recordedTenureTooltip, /archives@hctx\.net/);
  const singleRk = buildResearchKitDeedChainView(
    "0152300000001",
    "0152300000001",
    { ot: "corporate_llc", oh: "2005-2026~<=1988~~SMITH TOWER CORP" },
    "SMITH TOWER CORP"
  );
  assert.match(singleRk.html, /Continuous HCAD Roll Tenure Since &lt;=1988 \(HCAD Mainframe Baseline\):/);
  assert.match(singleRk.html, /January 2, 1988/);
  assert.match(singleRk.html, /Harris County Archives/);

  // 2b. Countywide PMTiles shape ({ hcad_num } with props.owner === undefined) + static permit-shard `conf: 1`
  //     suppresses all deed instruments, prior owners, and rapid_flip nodes across all 3 surfaces before live HCAD resolves
  const confCountywideShard = {
    conf: 1,
    ot: "anonymity_shield",
    d: "20210120:R2022-49567",
    flip: 71,
    oh: "2021-2026~20210120~R2022-49567~LEAKED PROTECTED NAME",
  };
  const confCountywideXray = decodeParcelOwnershipXray(confCountywideShard, {
    hcad_num: "0071270000013",
  });
  assert.equal(confCountywideXray.isConfidential, true);
  assert.equal(confCountywideXray.flipDays, null);
  assert.equal(confCountywideXray.latestClerkFileNum, "");
  assert.equal(confCountywideXray.priorOwners.length, 0);
  assert.equal(confCountywideXray.clerkUrl.includes("49567"), false);

  const confCountywideRk = buildResearchKitDeedChainView(
    "0071270000013",
    "0071270000013",
    confCountywideShard,
    ""
  );
  assert.equal(confCountywideRk.count, 0);
  assert.doesNotMatch(confCountywideRk.html, /49567|LEAKED/);

  const confCountywideTl = buildStewardshipTimelineModel({
    featureProps: { hcad_num: "0071270000013", year_built: 1930 },
    permitShardRec: confCountywideShard,
  });
  assert.equal(confCountywideTl.ownerTenures.length, 0);
  assert.equal(
    confCountywideTl.events.some(
      (ev) => ev.kind === "deed" || ev.kind === "owner_tenure" || ev.kind === "rapid_flip"
    ),
    false
  );

  // 2c. Canonical Clerk wire-token parsing in decodeParcelOwnershipXray (FC: unwrap -> 'Y051716' instead of 'FC', PR:/VP: non-RP suppression, R20XX -> RP-20XX, and oh fallback)
  const fcXray = decodeParcelOwnershipXray(
    { d: "20041105:FC:Y051716:1:D" },
    { hcad_num: "0010020000016", owner: "DESEL WAREHOUSE LLC", use_category: "Commercial" }
  );
  assert.equal(fcXray.latestClerkFileNum, "Y051716");

  const prXray = decodeParcelOwnershipXray(
    { d: "19980512:PR:145892:1:D" },
    { hcad_num: "0010020000016", owner: "DESEL WAREHOUSE LLC", use_category: "Commercial" }
  );
  assert.equal(prXray.latestClerkFileNum, "");

  const rNormXray = decodeParcelOwnershipXray(
    { d: "20190329:R2019-149789:1:D" },
    { hcad_num: "0010020000016", owner: "DESEL WAREHOUSE LLC", use_category: "Commercial" }
  );
  assert.equal(rNormXray.latestClerkFileNum, "RP-2019-149789");

  const ohFallbackXray = decodeParcelOwnershipXray(
    { d: "", oh: "2020-2026~20200601~R2020-555111~ACME LLC" },
    { hcad_num: "0010020000016", owner: "ACME LLC", use_category: "Commercial" }
  );
  assert.equal(ohFallbackXray.latestClerkFileNum, "RP-2020-555111");

  // 2d. Shared corporate/trust owner classifier (PLLC, FOUNDATION, TRUST vs Vietnamese/personal 'HOA')
  assert.equal(isEntityOrTrustOwnerName("SMITH MEDICAL PLLC"), true);
  assert.equal(isEntityOrTrustOwnerName("SMITH MEDICAL P.L.L.C."), true);
  assert.equal(isEntityOrTrustOwnerName("HOUSTON ARTS FOUNDATION"), true);
  assert.equal(isEntityOrTrustOwnerName("OAK FAMILY LIVING TRUST"), true);
  assert.equal(isEntityOrTrustOwnerName("LE HUNG V & HOA T"), false);
  const pllcXray = decodeParcelOwnershipXray(
    { ot: "local_small" },
    { hcad_num: "0010020000016", owner: "SMITH MEDICAL PLLC", use_category: "Commercial" }
  );
  assert.equal(pllcXray.isCorporateOrTrust, true);

  // 3. Provenance Timeline: R2019- vs RP-2019- & FC:/VP: normalization, Grantee attachment without duplicate nodes,
  //    '<=1988' -> '1988-01-02' sorting AFTER 1915 Red Book and 1928 construction, and REG-3 non-ownership milestones!
  const timeline = buildStewardshipTimelineModel({
    featureProps: {
      hcad_num: "0621100000014",
      owner: "NAVARRO LUCIA",
      year_built: 1928,
      remodel_year: 2006,
      is_landmark: true,
      landmark_name: "Key Street Bungalow",
      landmark_year: 2010,
    },
    permitShardRec: {
      d: "20190415:RP-2019-149789:10:D|19550601:VP:2410/118:11:D",
      oh: "2019-2026~20190415~R2019-149789~NAVARRO LUCIA|2005-2018~<=1988~~KENDALL THOMAS R",
      p: [
        ["2024-02-01", "Plumbing", "SEWER DISCONNECT (1 OF 2)", 0],
        ["2024-05-10", "Building Demolition", "DEMO HOUSE (2 OF 2)", 0],
      ],
      flip: 45,
    },
    redBookEntries: [{ year: 1915, title: "1915 Red Book Entry", citation: "p. 42" }],
    cityDirInfo: { entries: [{ year: 1934, occupant: "Kendall, Thos." }] },
  });

  // R2019-149789 in `oh` matched RP-2019-149789 in `d` via canonical matchKey FN:RP-2019-149789 (no duplicate node!)
  const navarroDeedEvents = timeline.events.filter(
    (e) => e.kind === "deed" && e.clerkFileNum === "RP-2019-149789"
  );
  assert.equal(navarroDeedEvents.length, 1);
  assert.equal(navarroDeedEvents[0].grantee, "NAVARRO LUCIA");
  assert.match(navarroDeedEvents[0].title, /to NAVARRO LUCIA/);
  assert.equal(
    timeline.events.filter((e) => e.kind === "owner_tenure" && e.ownerName === "NAVARRO LUCIA").length,
    0,
    "Paired tenure must not emit a duplicate owner_tenure node"
  );

  // Unpaired <=1988 prior owner emits owner_tenure with sortKey '1988-01-02' AFTER 1915, 1928, 1934, and 1955 VP deed!
  const kendallNode = timeline.events.find(
    (e) => e.kind === "owner_tenure" && e.ownerName === "KENDALL THOMAS R"
  );
  assert.ok(kendallNode);
  assert.equal(kendallNode.sortKey, "1988-01-02");
  const kindsInOrder = timeline.events.map((e) => `${e.year}:${e.kind}`);
  const idx1915 = timeline.events.findIndex((e) => e.year === 1915);
  const idx1928 = timeline.events.findIndex((e) => e.kind === "construction");
  const idx1955Vp = timeline.events.findIndex((e) => e.kind === "deed" && e.year === 1955);
  const idx1988Oh = timeline.events.indexOf(kendallNode);
  const idx2019Deed = timeline.events.indexOf(navarroDeedEvents[0]);
  assert.ok(
    idx1915 < idx1928 && idx1928 < idx1955Vp && idx1955Vp < idx1988Oh && idx1988Oh < idx2019Deed,
    `Unexpected chronological order: ${kindsInOrder.join(" -> ")}`
  );
  // Stage 1 sewer disconnect vs Stage 2 building demo badges preserved (REG-3)
  assert.ok(timeline.events.some((e) => e.kind === "sewer_disc_permit"));
  assert.ok(timeline.events.some((e) => e.kind === "demo_permit"));
  assert.ok(timeline.events.some((e) => e.kind === "rapid_flip"));

  // 3b. Past & future City Directory / HPO Survey overrides calibrate Original Structure Built when cdRec.e < year_built,
  //     and generate per-year citations on each Directory Occupant card instead of repeating cdRec.c (1927) across 1928/1929/1932
  const calibratedOverrideTl = buildStewardshipTimelineModel({
    featureProps: {
      hcad_num: "0621100000014",
      address: "1127 KEY ST",
      year_built: 1928,
      is_curated_override: true,
      source_type: "Houston City Directory (Morrison & Fourmy)",
      source_citation: "1928 Houston City Directory (Morrison & Fourmy); lot unimproved through 1926 directory (HCAD defaults to 1920)",
    },
    cityDirInfo: {
      e: 1927,
      f: "confirmed",
      c: "1927 Houston City Directory: J F Lake (Street Guide Occupant) at 1127 KEY ST",
      r: [
        [1927, "J F Lake", "Street Guide Occupant"],
        [1928, "John F Lake (Johnie M)", "Clerk (SP Lines)"],
        [1929, "Fannie Lake", "Widow of Wm T Lake"],
      ],
    },
  });
  const constrNode = calibratedOverrideTl.events.find((e) => e.kind === "construction");
  assert.ok(constrNode, "Expected construction milestone event");
  assert.equal(constrNode.year, 1927, "Directory override with year_built=1928 must calibrate construction year to cdRec.e=1927");
  assert.equal(constrNode.title, "Original Structure Built (1927)");
  assert.match(constrNode.citation, /1927 Houston City Directory/);
  assert.equal(constrNode.citation.includes("1928 Houston City Directory"), false);

  const dirNodes = calibratedOverrideTl.events.filter((e) => e.kind === "city_directory");
  assert.equal(dirNodes.length, 3);
  assert.equal(
    dirNodes[0].citation,
    "1927 Houston City Directory: J F Lake (Street Guide Occupant) at 1127 KEY ST"
  );
  assert.equal(
    dirNodes[1].citation,
    "1928 Houston City Directory: John F Lake (Johnie M) (Clerk (SP Lines)) at 1127 KEY ST",
    "1928 Directory Occupant card must cite 1928 rather than repeating 1927"
  );
  assert.equal(
    dirNodes[2].citation,
    "1929 Houston City Directory: Fannie Lake (Widow of Wm T Lake) at 1127 KEY ST",
    "1929 Directory Occupant card must cite 1929 rather than repeating 1927"
  );

  // Landmark / architect overrides (e.g. 1939 City Hall on a parcel with 1927 predecessor directory entries) keep their landmark year_built
  const landmarkOverrideTl = buildStewardshipTimelineModel({
    featureProps: {
      hcad_num: "0010010000001",
      address: "901 BAGBY ST",
      year_built: 1939,
      is_curated_override: true,
      is_landmark: true,
      source_type: "COH Protected Landmark Designation Report",
      source_citation: "Houston City Hall (Joseph Finger, 1939)",
    },
    cityDirInfo: {
      e: 1927,
      f: "confirmed",
      r: [[1927, "Prior Site Tenant", "Street Guide Occupant"]],
    },
  });
  const lmConstrNode = landmarkOverrideTl.events.find((e) => e.kind === "construction");
  assert.equal(lmConstrNode.year, 1939, "Landmark override must preserve its documented 1939 construction date");

  // Non-override redeveloped parcels (e.g. 2001 townhome on a lot with 1910 predecessor directory entry) keep their standing 2001 year_built
  const redevelopedNonOverrideTl = buildStewardshipTimelineModel({
    featureProps: {
      hcad_num: "1215360010001",
      address: "437 COLUMBIA ST",
      year_built: 2001,
      is_curated_override: false,
      source_citation: "",
    },
    citydirRec: {
      e: 1910,
      f: "earlier",
      r: [[1910, "Predecessor Resident", "Street Guide Occupant"]],
    },
  });
  const redevConstrNode = redevelopedNonOverrideTl.events.find((e) => e.kind === "construction");
  assert.equal(
    redevConstrNode.year,
    2001,
    "Non-override redeveloped parcel must keep its standing 2001 construction year rather than calibrating to a 1910 predecessor"
  );

  // 4. REG-1 & REG-4: 8-box County Clerk legal extraction & legacy empty-state contracts
  const legal = parseClerkLegalTokens({
    legalLines: ["LT 12 BLK 8", "NORTH NORHILL SEC 2"],
    subdivision: "NORHILL",
    marketNeighborhood: "NORHILL",
  });
  assert.equal(legal.subdivisionDesc, "NORTH NORHILL");
  assert.equal(legal.lot, "12");
  assert.equal(legal.block, "8");
  assert.equal(legal.section, "2");

  const emptyRk = buildResearchKitDeedChainView("0621100000014", "0621100000014", null, "");
  assert.equal(emptyRk.isEmptyCama, true);
  assert.equal(describeDeedChainCoverage([]).hasRecordedFilings, false);
  assert.equal(decodeCompactDeedChain("").length, 0);
  assert.equal(formatClerkInstrumentToken("", "2020-01-01"), null);
});

test("MOD-3: clerkSearchHelpers uses pure Web Streams without node:zlib and app.js preserves immutable _permitShardCache", async () => {
  const helperSrc = fs.readFileSync(
    path.join(REPO_ROOT, "app", "js", "clerkSearchHelpers.js"),
    "utf8"
  );
  const helperCodeNoComments = helperSrc
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/[^\n]*/g, "");
  assert.ok(
    !/node:zlib|\bzlib\b/.test(helperCodeNoComments),
    "clerkSearchHelpers.js executable code must use standard DecompressionStream('gzip') without node:zlib branches"
  );

  // Import HoustonAtlasApp in bare Node and verify _populateInspectorPermits neither mutates _permitShardCache
  // nor skips owner history on zero-permit parcels where permitShardRec is null!
  const manifestDoc = JSON.parse(
    fs.readFileSync(path.join(REPO_ROOT, "app", "public", "data", "pmtiles_manifest.json"), "utf8")
  );
  const { HoustonAtlasApp, resolveEffectiveInspectorOwner } = await import(
    `../../app/js/app.js?v=${manifestDoc.cache_bust}`
  );
  const cachedPermitRec = Object.freeze({
    y: 1928,
    d: "20190415:R2019-149789:1:D",
    p: [],
  });
  const fakeShard = Object.freeze({
    "0621100000014": cachedPermitRec,
    // 0629990000099 intentionally absent (zero-permit parcel -> _fetchPermitRecord returns null)
  });
  const gzOwnerShard = zlib.gzipSync(
    Buffer.from(
      JSON.stringify({
        "0621100000014": "2019-2026~20190415~R2019-149789~OWNER A|2005-2018~<=1988~~OWNER B",
        "0629990000099": "2005-2026~<=1988~~ZERO PERMIT STEWARD",
      }),
      "utf8"
    )
  );

  const fakeCtx = {
    _permitShardCache: new Map([["062", Promise.resolve(fakeShard)]]),
    _ownerHistoryShardCache: new Map(),
    _activeInspectorCompositeRec: null,
    mapController: { pmtilesManifest: { owner_history_prefixes: ["062"] } },
    _fetchPermitRecord: HoustonAtlasApp.prototype._fetchPermitRecord,
    _fetchOwnerHistoryRecord: HoustonAtlasApp.prototype._fetchOwnerHistoryRecord,
  };

  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes("owners_062.json.gz")) {
      return new Response(gzOwnerShard, {
        status: 200,
        headers: { "Content-Type": "application/gzip" },
      });
    }
    throw new Error(`Unexpected fetch in MOD-3 test: ${url}`);
  };
  try {
    // 1. Existing frozen permit record is NEVER mutated in-place (Object.freeze would throw TypeError if mutated!)
    await HoustonAtlasApp.prototype._populateInspectorPermits.call(
      fakeCtx,
      { owner: "OWNER A" },
      "0621100000014"
    );
    assert.equal("oh" in cachedPermitRec, false);
    assert.equal(fakeCtx._activeInspectorCompositeRec.hcadNum, "0621100000014");
    assert.match(fakeCtx._activeInspectorCompositeRec.rec.oh, /OWNER B/);
    assert.equal(fakeCtx._activeInspectorCompositeRec.rec.y, 1928);

    // 2. Zero-permit parcel (rec === null) still hydrates composite `{ oh }` cleanly!
    await HoustonAtlasApp.prototype._populateInspectorPermits.call(
      fakeCtx,
      { owner: "ZERO PERMIT STEWARD" },
      "0629990000099"
    );
    assert.equal(fakeCtx._activeInspectorCompositeRec.hcadNum, "0629990000099");
    assert.equal(
      fakeCtx._activeInspectorCompositeRec.rec.oh,
      "2005-2026~<=1988~~ZERO PERMIT STEWARD"
    );

    // 3. Prefix absent from pmtilesManifest.owner_history_prefixes (including empty []) short-circuits with zero network fetches
    const unindexedOh = await HoustonAtlasApp.prototype._fetchOwnerHistoryRecord.call(
      fakeCtx,
      "9990000000001"
    );
    assert.equal(unindexedOh, "");
    const emptyManifestCtx = {
      _ownerHistoryShardCache: new Map(),
      mapController: {
        pmtilesManifest: {
          cache_bust: "20261106b",
          owner_history_prefixes: [],
          owner_history_accounts: 0,
        },
      },
    };
    assert.equal(
      await HoustonAtlasApp.prototype._fetchOwnerHistoryRecord.call(
        emptyManifestCtx,
        "0621100000014"
      ),
      ""
    );

    // 3b. Live confidential re-hydration in decodeParcelOwnershipXray suppresses all static portfolio/officer/deed links
    assert.equal(
      isConfidentialOwnerName("Protected Individual Homestead (TX Tax Code Sec. 25.025)"),
      true
    );
    const liveRehydratedXray = decodeParcelOwnershipXray({
      featureProps: {
        hcad_num: "0660640330001",
        owner: "CURRENT OWNER",
        port_id: "p_00123",
      },
      permitShardRec: {
        pid: "p_00123",
        co: ["JANE DOE"],
        d: "20240301:2024098765",
        oh: "2020-2026~20200101~2024098765~JANE DOE",
      },
      ownershipPortfolios: {
        portfolios: {
          p_00123: {
            label: "DOE FAMILY TRUST",
            primary_entity: "DOE HOLDINGS LLC",
            entity_samples: ["DOE HOLDINGS LLC"],
            tx_sos_officers: ["JANE DOE (MANAGER)"],
            c: 4,
          },
        },
      },
    });
    assert.equal(liveRehydratedXray.isConfidential, true);
    assert.equal(liveRehydratedXray.portfolioId, "");
    assert.equal(liveRehydratedXray.portfolioMeta, null);
    assert.equal(liveRehydratedXray.portfolioCount, 0);
    assert.deepEqual(liveRehydratedXray.coOwners, []);
    assert.deepEqual(liveRehydratedXray.siblingEntities, []);
    assert.deepEqual(liveRehydratedXray.officers, []);
    assert.equal(liveRehydratedXray.beneficialSteward, "");
    assert.equal(liveRehydratedXray.txComptrollerUrl.includes("DOE"), false);
    assert.equal(liveRehydratedXray.clerkUrl.includes("2024098765"), false);

    // 4. Pre-resolved _activeInspectorLiveOwner ("CURRENT OWNER") overrides static props.owner during _populateInspectorPermits
    const hydratedCalls = [];
    const liveConfCtx = {
      ...fakeCtx,
      _activeInspectorLiveOwner: { hcadNum: "0621100000014", owner: "CURRENT OWNER" },
      _populateResearchKitDeedChain: (_h, _r, effOwn) => hydratedCalls.push(["rk", effOwn]),
      _hydrateOwnershipAndStewardshipSlots: (effP) => hydratedCalls.push(["xray", effP.owner]),
    };
    const origDoc = globalThis.document;
    globalThis.document = {
      getElementById: () => ({
        getAttribute: () => "0621100000014",
        textContent: "Protected Individual Homestead (TX Tax Code Sec. 25.025)",
      }),
    };
    try {
      await HoustonAtlasApp.prototype._populateInspectorPermits.call(
        liveConfCtx,
        { owner: "MORRISON CLARA" },
        "0621100000014"
      );
      assert.deepEqual(hydratedCalls, [
        ["rk", "CURRENT OWNER"],
        ["xray", "CURRENT OWNER"],
      ]);

      // 5. Reverse race: non-confidential _activeInspectorLiveOwner ("GARCIA ELENA") propagates into effProps.owner
      //    when props.owner is empty or "Public / Unlisted", and never scrapes DOM textContent.
      const nonConfCalls = [];
      const livePublicCtx = {
        ...fakeCtx,
        _activeInspectorLiveOwner: { hcadNum: "0621100000014", owner: "GARCIA ELENA" },
        _populateResearchKitDeedChain: (_h, _r, effOwn) => nonConfCalls.push(["rk", effOwn]),
        _hydrateOwnershipAndStewardshipSlots: (effP) => nonConfCalls.push(["xray", effP.owner]),
      };
      await HoustonAtlasApp.prototype._populateInspectorPermits.call(
        livePublicCtx,
        { owner: "" },
        "0621100000014"
      );
      await HoustonAtlasApp.prototype._populateInspectorPermits.call(
        livePublicCtx,
        { owner: "Public / Unlisted" },
        "0621100000014"
      );
      await HoustonAtlasApp.prototype._populateInspectorPermits.call(
        livePublicCtx,
        { owner: "UNKNOWN" },
        "0621100000014"
      );
      await HoustonAtlasApp.prototype._populateInspectorPermits.call(
        livePublicCtx,
        { owner: "OWNER OF RECORD" },
        "0621100000014"
      );
      assert.deepEqual(nonConfCalls, [
        ["rk", "GARCIA ELENA"],
        ["xray", "GARCIA ELENA"],
        ["rk", "GARCIA ELENA"],
        ["xray", "GARCIA ELENA"],
        ["rk", "GARCIA ELENA"],
        ["xray", "GARCIA ELENA"],
        ["rk", "GARCIA ELENA"],
        ["xray", "GARCIA ELENA"],
      ]);

      // 6. resolveEffectiveInspectorOwner synchronizes both `owner` and `owner_name` and enforces
      //    settlement-order-independent precedence across all PLACEHOLDER_OWNER_TOKENS.
      const syncConf = resolveEffectiveInspectorOwner(
        { hcad_num: "0621100000014", owner: "JANE ROE", owner_name: "JANE ROE" },
        "CURRENT OWNER"
      );
      assert.equal(syncConf.isConfidential, true);
      assert.equal(syncConf.shouldApplyLiveOwner, true);
      assert.equal(syncConf.effOwner, "CURRENT OWNER");
      assert.equal(syncConf.effProps.owner, "CURRENT OWNER");
      assert.equal(syncConf.effProps.owner_name, "CURRENT OWNER");

      for (const placeholderTok of ["UNKNOWN", "UNLISTED", "OWNER OF RECORD", "PENDING", "Public / Unlisted"]) {
        const fillRes = resolveEffectiveInspectorOwner(
          { hcad_num: "0621100000014", owner: placeholderTok, owner_name: placeholderTok },
          "ACME HISTORIC LOFTS LLC"
        );
        assert.equal(fillRes.isConfidential, false);
        assert.equal(fillRes.shouldApplyLiveOwner, true, `Expected live override for ${placeholderTok}`);
        assert.equal(fillRes.effOwner, "ACME HISTORIC LOFTS LLC");
        assert.equal(fillRes.effProps.owner, "ACME HISTORIC LOFTS LLC");
        assert.equal(fillRes.effProps.owner_name, "ACME HISTORIC LOFTS LLC");
      }

      // 7. Countywide PMTiles feature (props.owner === undefined) + permitShardRec.conf === 1 resolves fail-closed
      const shardConfRes = resolveEffectiveInspectorOwner(
        { hcad_num: "0071270000013" },
        "",
        { conf: 1, ot: "anonymity_shield" }
      );
      assert.equal(shardConfRes.isConfidential, true);
      assert.equal(shardConfRes.effOwner, "CONFIDENTIAL OWNER");
      assert.equal(shardConfRes.effProps.owner, "CONFIDENTIAL OWNER");
      assert.equal(
        shardConfRes.displayLabel,
        "Protected Individual Homestead (TX Tax Code Sec. 25.025)"
      );

      const countywideConfCalls = [];
      const ownerCellStub = { textContent: "Public / Unlisted" };
      globalThis.document = {
        getElementById: (id) =>
          id === "inspector-cell-owner"
            ? ownerCellStub
            : { getAttribute: () => "0071270000013", textContent: "" },
      };
      const countywideConfCtx = {
        _permitShardCache: new Map([
          ["007", Promise.resolve({ "0071270000013": { conf: 1, ot: "anonymity_shield" } })],
        ]),
        _ownerHistoryShardCache: new Map(),
        _activeInspectorCompositeRec: null,
        _activeInspectorLiveOwner: null,
        mapController: { pmtilesManifest: { owner_history_prefixes: [] } },
        _fetchPermitRecord: HoustonAtlasApp.prototype._fetchPermitRecord,
        _fetchOwnerHistoryRecord: HoustonAtlasApp.prototype._fetchOwnerHistoryRecord,
        _populateResearchKitDeedChain: (_h, _r, effOwn) => countywideConfCalls.push(["rk", effOwn]),
        _hydrateOwnershipAndStewardshipSlots: (effP) => countywideConfCalls.push(["xray", effP.owner]),
      };
      await HoustonAtlasApp.prototype._populateInspectorPermits.call(
        countywideConfCtx,
        { hcad_num: "0071270000013" },
        "0071270000013"
      );
      assert.equal(
        ownerCellStub.textContent,
        "Protected Individual Homestead (TX Tax Code Sec. 25.025)"
      );
      assert.deepEqual(countywideConfCalls, [
        ["rk", "CONFIDENTIAL OWNER"],
        ["xray", "CONFIDENTIAL OWNER"],
      ]);
    } finally {
      globalThis.document = origDoc;
    }
  } finally {
    globalThis.fetch = origFetch;
  }
});
