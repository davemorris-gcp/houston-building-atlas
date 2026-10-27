import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  buildResearchKitDeedChainView,
  cleanClerkSubdivisionDesc,
  decodeCompactDeedChain,
  describeDeedChainCoverage,
  extractLegalSubdivisionFromLines,
  formatClerkPartyTokens,
  parseClerkLegalTokens,
  parseRootPlatCitation,
} from "../../app/js/clerkSearchHelpers.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");

test("1127 Key St (0621100000014): resolves NORTH NORHILL plat over HCAD NORHILL market neighborhood, splits Grantee spouses, and decodes 6 recorded deeds", () => {
  // Exact payload returned by live HCAD public_query/MapServer/0 for 0621100000014
  const liveLegalLines = ["LT 14 BLK 131", "NORTH NORHILL"];
  const liveMarketNeighborhood = "NORHILL";

  assert.equal(extractLegalSubdivisionFromLines(liveLegalLines), "NORTH NORHILL");

  const tokens = parseClerkLegalTokens({
    legalLines: liveLegalLines,
    legalDescription: "LT 14 BLK 131 · NORTH NORHILL",
    legalSubdivision: extractLegalSubdivisionFromLines(liveLegalLines),
    subdivision: liveMarketNeighborhood,
    marketNeighborhood: liveMarketNeighborhood,
    historicParentPlat: {
      id: "plat_3913_north_norhill",
      name: "North Norhill",
      full_name: "North Norhill Addition",
      estimated_plat_year: 1923,
    },
  });

  assert.equal(tokens.subdivisionDesc, "NORTH NORHILL");
  assert.equal(tokens.hasDistinctHistoricParent, false);
  assert.equal(tokens.historicParentDesc, "");
  assert.equal(tokens.lot, "14");
  assert.equal(tokens.lotDisplay, "14");
  assert.equal(tokens.block, "131");
  assert.equal(tokens.tract, "");
  assert.equal(tokens.section, "");

  // Owner sanitization for County Clerk "Last Name First - No Punctuation"
  const partyTokens = formatClerkPartyTokens("MORRIS LAURA M & DAVID V");
  assert.deepEqual(
    partyTokens.map((p) => p.copy),
    ["MORRIS LAURA", "MORRIS DAVID"]
  );
  for (const pt of partyTokens) {
    assert.doesNotMatch(pt.copy, /[&.,'"\-()/]/);
  }

  // Verify permits_062.json on disk contains the normalized recorded deed chain for 0621100000014
  const shard062Path = path.join(
    REPO_ROOT,
    "app",
    "public",
    "data",
    "permits",
    "permits_062.json"
  );
  assert.ok(fs.existsSync(shard062Path), "permits_062.json must exist");
  const shard062 = JSON.parse(fs.readFileSync(shard062Path, "utf8"));
  const keyStRec = shard062["0621100000014"];
  assert.ok(keyStRec && keyStRec.d, "0621100000014 must include compact deed chain field 'd'");

  const deeds = decodeCompactDeedChain(keyStRec.d);
  assert.equal(deeds.length, 6);
  assert.deepEqual(
    deeds.map((d) => ({ date: d.date, fileNo: d.copyValue, box: d.clerkBoxLabel })),
    [
      { date: "2019-03-29", fileNo: "RP-2019-149789", box: "File Number" },
      { date: "2012-08-20", fileNo: "20120430232", box: "File Number" },
      { date: "2011-12-28", fileNo: "20120430231", box: "File Number" },
      { date: "2009-01-26", fileNo: "20090077820", box: "File Number" },
      { date: "2008-09-11", fileNo: "20090077819", box: "File Number" },
      { date: "2004-11-05", fileNo: "Y051716", box: "File Number" },
    ]
  );
});

test("parseClerkLegalTokens extracts all 8 County Clerk legal boxes cleanly across Harris County edge cases", () => {
  // 1. Tract vs Lot separation (Tract-only parcel)
  const tractOnly = parseClerkLegalTokens({
    legalLines: ["TR 15 BLK 2", "SSBB"],
  });
  assert.equal(tractOnly.subdivisionDesc, "SSBB");
  assert.equal(tractOnly.tract, "15");
  assert.equal(tractOnly.lot, "");
  assert.equal(tractOnly.block, "2");

  // 2. Complex 4-line metes-and-bounds legal description with Tract + Lot + Section + Block + Addition on line 4
  const complex4Line = parseClerkLegalTokens({
    legalLines: [
      "TR 13 E 13.7X50 OF LT 6 E",
      "13.6X50 OF LT 7 SO 48.72X100",
      "OF LT SEC 12 BLK 263 (NM)",
      "SSBB",
    ],
  });
  assert.equal(complex4Line.subdivisionDesc, "SSBB");
  assert.equal(complex4Line.tract, "13");
  assert.equal(complex4Line.lot, "6");
  assert.equal(complex4Line.section, "12");
  assert.equal(complex4Line.block, "263");

  // 3. Multi-lot parcel copies single primary lot number for Clerk txtLot
  const multiLot = parseClerkLegalTokens({
    legalLines: ["LTS 14 & 15 BLK 288", "HOUSTON HEIGHTS R/P"],
  });
  assert.equal(multiLot.subdivisionDesc, "HOUSTON HEIGHTS");
  assert.equal(multiLot.lot, "14");
  assert.equal(multiLot.lotDisplay, "14 (of 14 & 15)");
  assert.equal(multiLot.block, "288");

  // 4. Section embedded in subdivision line
  const withSec = parseClerkLegalTokens({
    legalLines: ["LTS 1 2 & 3 BLK 1", "DEPENBROCK ALLEN SEC 62"],
  });
  assert.equal(withSec.subdivisionDesc, "DEPENBROCK ALLEN");
  assert.equal(withSec.section, "62");
  assert.equal(withSec.lot, "1");
  assert.equal(withSec.block, "1");

  // 5. Reserve + Partial Replat stripping
  const withRes = parseClerkLegalTokens({
    legalLines: ["RES A BLK 1", "SSBB PAR R/P"],
  });
  assert.equal(withRes.subdivisionDesc, "SSBB");
  assert.equal(withRes.reserve, "A");
  assert.equal(withRes.block, "1");
  assert.equal(withRes.lot, "");

  // 6. Texas GLO Abstract + Survey Grantee + Fractional Thru Lots
  const withAbst = parseClerkLegalTokens({
    legalLines: ["W 8 FT OF LTS 6 THRU 10 BLK 467", "ABST 323 J HOLMAN", "SSBB"],
  });
  assert.equal(withAbst.subdivisionDesc, "SSBB");
  assert.equal(withAbst.abstract, "323");
  assert.equal(withAbst.abstractSurvey, "J HOLMAN");
  assert.equal(withAbst.lot, "6");
  assert.equal(withAbst.block, "467");

  // 7. Outlot + Multi-Tract across arbitrary townsite names (no hardcoded townsite nouns)
  const withOutlotBarker = parseClerkLegalTokens({
    legalLines: ["TRA 3A 4A & 4A-1 BARKER O/L 62"],
  });
  assert.equal(withOutlotBarker.tract, "3A");
  assert.equal(withOutlotBarker.tractDisplay, "3A (of 3A 4A & 4A-1)");
  assert.equal(withOutlotBarker.outlot, "62");

  const withOutlotHouston = parseClerkLegalTokens({
    legalLines: ["TRA 3A 4A & 4A-1 HOUSTON O/L 62"],
  });
  assert.equal(withOutlotHouston.tract, "3A");
  assert.equal(withOutlotHouston.tractDisplay, "3A (of 3A 4A & 4A-1)");
  assert.equal(withOutlotHouston.outlot, "62");

  // 8. Multi-line SEC <N> BLK <M> and fractional 1/2 OF LT continuations + lettered Lots/Tracts
  const hydeParkMulti = parseClerkLegalTokens({
    legalLines: ["E 3427' OF TR 9 LT 8 HLF OF", "SEC 7 BLK 9", "(NM)", "HYDE PARK"],
  });
  assert.equal(hydeParkMulti.subdivisionDesc, "HYDE PARK");
  assert.equal(hydeParkMulti.tract, "9");
  assert.equal(hydeParkMulti.lot, "8");
  assert.equal(hydeParkMulti.section, "7");
  assert.equal(hydeParkMulti.block, "9");

  const heightsFractional = parseClerkLegalTokens({
    legalLines: [
      "LTS 1 THRU 25 32 THRU 50 & W",
      "1/2 OF LT 31 & TR A BLK 86",
      "HOUSTON HEIGHTS",
    ],
  });
  assert.equal(heightsFractional.subdivisionDesc, "HOUSTON HEIGHTS");
  assert.equal(heightsFractional.lot, "1");
  assert.equal(heightsFractional.tract, "A");
  assert.equal(heightsFractional.block, "86");

  const eastwoodLetterLot = parseClerkLegalTokens({
    legalLines: ["LT B BLK 9", "(NM)", "EASTWOOD"],
  });
  assert.equal(eastwoodLetterLot.subdivisionDesc, "EASTWOOD");
  assert.equal(eastwoodLetterLot.lot, "B");
  assert.equal(eastwoodLetterLot.lotDisplay, "B");
  assert.equal(eastwoodLetterLot.block, "9");

  const multiLetterLots = parseClerkLegalTokens({
    legalLines: ["LTS E & F BLK 12", "EASTWOOD"],
  });
  assert.equal(multiLetterLots.lot, "E");
  assert.equal(multiLetterLots.lotDisplay, "E (of E & F)");

  // 9. Formatted UI metadata fallback ("State Class A1 · Grade C+ · ...") never leaks GRADE as subdivision
  const fromUiBanner = parseClerkLegalTokens(
    "State Class A1 · Grade C+ · LT 14 BLK 131 · NORTH NORHILL",
    "NORHILL"
  );
  assert.equal(fromUiBanner.subdivisionDesc, "NORTH NORHILL");
  assert.equal(fromUiBanner.lot, "14");
  assert.equal(fromUiBanner.block, "131");

  // 10. Condominium Unit
  const withUnit = parseClerkLegalTokens({
    legalLines: ["UNIT 102B BLDG C", "PROVIDENCE T/H"],
  });
  assert.equal(withUnit.unit, "102B");
  assert.equal(withUnit.subdivisionDesc, "PROVIDENCE T/H");

  // 9. Modern townhome replat inside historic parent addition (Montrose / Fairview)
  const replatted = parseClerkLegalTokens({
    legalLines: ["LT 2 BLK 1", "SONORA AT FAIRVIEW HEIGHTS"],
    historicParentPlat: {
      id: "plat_1908_fairview",
      name: "Fairview",
      full_name: "Fairview Addition",
      deed_citation: "Harris County Deed Records Vol. 69, Pg. 368",
    },
  });
  assert.equal(replatted.hasDistinctHistoricParent, true);
  assert.equal(replatted.historicParentDesc, "FAIRVIEW");
  assert.equal(replatted.subdivisionDesc, "SONORA AT FAIRVIEW HEIGHTS");
  assert.equal(replatted.rootPlatCitation.volume, "69");
  assert.equal(replatted.rootPlatCitation.page, "368");
  assert.equal(replatted.rootPlatCitation.series, "Deed Records (DR)");

  assert.equal(cleanClerkSubdivisionDesc("Fairview Addition"), "FAIRVIEW");
  assert.deepEqual(
    parseRootPlatCitation("Harris County Clerk Instrument #90214 · Montrose"),
    {
      series: "",
      volume: "",
      page: "",
      instrument: "90214",
      summary: "Clerk Instrument #90214",
    }
  );
});

test("formatClerkPartyTokens sanitizes co-owners, multi-word middle names, trusts, and corporate entities", () => {
  assert.deepEqual(
    formatClerkPartyTokens("SANDERS JOHNNY RAY JR & CANDICE PAIGE").map((t) => t.copy),
    ["SANDERS JOHNNY", "SANDERS CANDICE"]
  );
  assert.deepEqual(
    formatClerkPartyTokens("MOORE W I III & BARBARA K").map((t) => t.copy),
    ["MOORE W I", "MOORE BARBARA"]
  );
  assert.deepEqual(
    formatClerkPartyTokens("LE TAMMY TAM").map((t) => t.copy),
    ["LE TAMMY"]
  );
  assert.deepEqual(
    formatClerkPartyTokens("LE HUNG V & HOA T").map((t) => t.copy),
    ["LE HUNG", "LE HOA"]
  );
  assert.deepEqual(
    formatClerkPartyTokens("ARMADILLEAUX L P").map((t) => t.copy),
    ["ARMADILLEAUX L P"]
  );
  assert.deepEqual(
    formatClerkPartyTokens("JESSUP DEBORAH H & JAMES W % RACE HOME MANAGEMENT").map(
      (t) => t.copy
    ),
    ["JESSUP DEBORAH", "JESSUP JAMES"]
  );
  assert.deepEqual(
    formatClerkPartyTokens("KENDRICK STEVEN J & CONDON KRISTIN M").map((t) => t.copy),
    ["KENDRICK STEVEN", "CONDON KRISTIN"]
  );
  assert.deepEqual(
    formatClerkPartyTokens("SMITH MARY ANN L E").map((t) => t.copy),
    ["SMITH MARY"]
  );
  assert.deepEqual(
    formatClerkPartyTokens("DOE, JANE A. TRUSTEE ET AL").map((t) => t.copy),
    ["DOE JANE"]
  );
  assert.deepEqual(
    formatClerkPartyTokens("ABC HOLDINGS, L.L.C.").map((t) => t.copy),
    ["ABC HOLDINGS LLC"]
  );
  assert.deepEqual(
    formatClerkPartyTokens("STATE DEPT HWY & PUB TRANS").map((t) => t.copy),
    ["STATE DEPT HWY PUB TRANS"]
  );
  assert.deepEqual(
    formatClerkPartyTokens("H B & T RR CO").map((t) => t.copy),
    ["H B T RR CO"]
  );
  assert.deepEqual(formatClerkPartyTokens("Public / Unlisted"), []);
});

test("decodeCompactDeedChain decodes all Clerk File Number eras plus Film Codes, Probate cases, and Vol/Page books", () => {
  const decoded = decodeCompactDeedChain(
    "20220415:R2022-88575|20160110:20160000146|19990512:T543210|19850601:FC:123-45-6789|19750000:PR:145892|19520314:VP:2410/118"
  );
  assert.deepEqual(decoded, [
    {
      date: "2022-04-15",
      kind: "file_number",
      clerkBoxLabel: "File Number",
      copyValue: "RP-2022-88575",
      displayValue: "RP-2022-88575",
      portal: "rp",
    },
    {
      date: "2016-01-10",
      kind: "file_number",
      clerkBoxLabel: "File Number",
      copyValue: "20160000146",
      displayValue: "20160000146",
      portal: "rp",
    },
    {
      date: "1999-05-12",
      kind: "file_number",
      clerkBoxLabel: "File Number",
      copyValue: "T543210",
      displayValue: "T543210",
      portal: "rp",
    },
    {
      date: "1985-06-01",
      kind: "film_code",
      clerkBoxLabel: "Film Code",
      copyValue: "123-45-6789",
      displayValue: "123-45-6789",
      portal: "rp",
    },
    {
      date: "1975",
      kind: "probate",
      clerkBoxLabel: "Probate Case #",
      copyValue: "145892",
      displayValue: "145892",
      portal: "probate",
    },
    {
      date: "1952-03-14",
      kind: "vol_page",
      clerkBoxLabel: "Vol / Page",
      copyValue: "2410",
      pageValue: "118",
      displayValue: "Vol 2410, Pg 118",
      portal: "archives",
    },
  ]);
});

test("Python enrich_deed_chains normalizes all Harris County Clerk eras and repairs 1993 P202281 on disk", () => {
  const pyScript = [
    "import sys",
    "sys.path.insert(0, 'pipeline')",
    "from atlas_pipeline.enrich_deed_chains import normalize_hcad_clerk_instrument as n, parse_dos_yyyymmdd as d",
    "assert n('2016', '10026') == '20160010026'",
    "assert n('2016', '10027') == 'R2016-10027'",
    "assert n('2016', 'RP-2016-0130748') == 'R2016-130748'",
    "assert n('2022', 'RP-2022-088575') == 'R2022-88575'",
    "assert n('1993', 'P202281') == 'P202281'",
    "assert n('1993', 'P202329') == 'P202329'",
    "assert n('2004', 'Y05171O') == 'Y051710'",
    "assert n('2013', '066860') == '20130066860'",
    "assert n('2007', '70115058') == '20070115058'",
    "assert n('2012', '00043023') == '20120043023'",
    "assert n('2014', '01492230') == '20140149223'",
    "assert n('1985', '123-45-6789') == 'FC:123-45-6789'",
    "assert n('1975', 'PR 145892') == 'PR:145892'",
    "assert n('1952', 'VOL 02410 PG 0118') == 'VP:2410/118'",
    "assert d('03/29/2019', '2019') == '20190329'",
    "assert d('2012-08-20', '2012') == '20120820'",
    "assert d('', '1993') == '19930000'",
  ].join("; ");

  const pyOut = execFileSync("python3", ["-c", pyScript], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  });
  assert.equal(pyOut, "");

  const shard110Path = path.join(
    REPO_ROOT,
    "app",
    "public",
    "data",
    "permits",
    "permits_110.json"
  );
  if (fs.existsSync(shard110Path)) {
    const shard110 = JSON.parse(fs.readFileSync(shard110Path, "utf8"));
    const rec = shard110["1105210000001"];
    assert.ok(rec && rec.d, "1105210000001 must have deed chain in permits_110.json");
    const deeds = decodeCompactDeedChain(rec.d);
    assert.ok(
      deeds.some((item) => item.copyValue === "P202281" && item.date === "1993-04-13"),
      `Expected 1993-04-13 P202281 in 1105210000001 deed chain, got: ${rec.d}`
    );
  }
});

test("describeDeedChainCoverage summarizes empty pre-2004 HCAD coverage, singular 1-filing, and plural multi-filing chains across Key St accounts", () => {
  // Given real permits_062.json records for 1115-1133 Key St plus non-array boundary inputs.
  const shard062Path = path.join(
    REPO_ROOT,
    "app",
    "public",
    "data",
    "permits",
    "permits_062.json"
  );
  const shard062 = JSON.parse(fs.readFileSync(shard062Path, "utf8"));

  // When evaluating 0-filing pre-2004 accounts (1133 Key St / 0621100000013 and 1119 Key St / 0621100000016).
  const empty1133 = describeDeedChainCoverage(
    decodeCompactDeedChain(shard062["0621100000013"]?.d || "")
  );
  const empty1119 = describeDeedChainCoverage(
    decodeCompactDeedChain(shard062["0621100000016"]?.d || "")
  );
  const nonArrayFallback = describeDeedChainCoverage(null);

  // Then empty accounts return transparent mid-2004 database cutover guidance.
  assert.equal(empty1133.hasRecordedFilings, false);
  assert.equal(empty1133.count, 0);
  assert.equal(empty1133.title, "Recorded Deed Transfers (HCAD Bulk Index)");
  assert.equal(empty1133.countLabel, "No Post-2004 HCAD Sales");
  assert.equal(empty1133.scopeNote, "");
  assert.match(empty1133.emptyExplanation, /mid-2004 database cutover/);
  assert.match(empty1133.emptyExplanation, /1-Click Copy Tokens/);
  assert.match(empty1133.emptyExplanation, /1961-present/);
  assert.equal(empty1119.hasRecordedFilings, false);
  assert.equal(empty1119.countLabel, "No Post-2004 HCAD Sales");
  assert.equal(nonArrayFallback.hasRecordedFilings, false);
  assert.equal(nonArrayFallback.count, 0);

  // When evaluating singular 1-filing account 1115 Key St (0621100000017), plural accounts 1121 Key St (0621100000015) & 1127 Key St (0621100000014), and a pre-2004 HCAD backfill chain.
  const single1115 = describeDeedChainCoverage(
    decodeCompactDeedChain(shard062["0621100000017"]?.d || "")
  );
  const triple1121 = describeDeedChainCoverage(
    decodeCompactDeedChain(shard062["0621100000015"]?.d || "")
  );
  const six1127 = describeDeedChainCoverage(
    decodeCompactDeedChain(shard062["0621100000014"]?.d || "")
  );
  const pre2004Backfill = describeDeedChainCoverage(
    decodeCompactDeedChain("19930413:P202281")
  );

  // Then singular vs plural count labels and post-2004 vs partial-backfill scope notes are returned accurately.
  assert.equal(single1115.hasRecordedFilings, true);
  assert.equal(single1115.count, 1);
  assert.equal(single1115.countLabel, "1 Recorded Filing");
  assert.equal(single1115.title, "Recorded Deed Transfers (1-Click Clerk File Number)");
  assert.match(single1115.scopeNote, /post-mid-2004 ownership sales only/);
  assert.equal(single1115.emptyExplanation, "");
  assert.equal(triple1121.count, 3);
  assert.equal(triple1121.countLabel, "3 Recorded Filings");
  assert.equal(six1127.count, 6);
  assert.equal(six1127.countLabel, "6 Recorded Filings");
  assert.match(pre2004Backfill.scopeNote, /partial pre-2004 backfill/);
});

test("buildResearchKitDeedChainView renders isEmptyCama for 0-row parcels, .rk-deed-scope-note for recorded filings, and hides non-parcel features", () => {
  // 1. Pre-2004 parcel with 0 HCAD deeds.txt rows (1133 Key St / 0621100000013) produces visible isEmptyCama explanation.
  const emptyView = buildResearchKitDeedChainView("0621100000013", "0621100000013", { yr: 1930 });
  assert.equal(emptyView.ignore, false);
  assert.equal(emptyView.hidden, false);
  assert.equal(emptyView.isEmptyCama, true);
  assert.equal(emptyView.count, 0);
  assert.match(emptyView.html, /No Post-2004 HCAD Sales/);
  assert.match(emptyView.html, /rk-deed-chain-count is-muted/);
  assert.match(emptyView.html, /mid-2004 database cutover/);

  // 2. Post-2004 parcel with recorded deeds (1115 Key St / 0621100000017) renders pills + .rk-deed-scope-note.
  const post2004View = buildResearchKitDeedChainView("0621100000017", "0621100000017", {
    d: "20171121:R2017-512003",
  });
  assert.equal(post2004View.ignore, false);
  assert.equal(post2004View.hidden, false);
  assert.equal(post2004View.isEmptyCama, false);
  assert.equal(post2004View.count, 1);
  assert.match(post2004View.html, /1 Recorded Filing/);
  assert.match(post2004View.html, /data-rk-copy="RP-2017-512003"/);
  assert.match(post2004View.html, /class="rk-deed-scope-note"/);
  assert.match(post2004View.html, /post-mid-2004 ownership sales only/);

  // 3. Stale async callback for a previous parcel returns ignore=true.
  const staleView = buildResearchKitDeedChainView("0621100000013", "0621100000017", { yr: 1930 });
  assert.equal(staleView.ignore, true);
  assert.equal(staleView.html, "");

  // 4. Non-parcel, demolished, empty, and all-zero HCAD features return hidden=true with isEmptyCama=false.
  for (const nonParcelHcad of ["", "Demolished", "0000000000000", "1423010010001#aux_1"]) {
    const nonParcelView = buildResearchKitDeedChainView(nonParcelHcad, nonParcelHcad, null);
    assert.equal(nonParcelView.ignore, false);
    assert.equal(
      nonParcelView.hidden,
      true,
      `Expected non-parcel HCAD '${nonParcelHcad}' to remain hidden`
    );
    assert.equal(nonParcelView.isEmptyCama, false);
    assert.equal(nonParcelView.html, "");
  }
});

test("curated_overrides.json purges cross-parcel 0621100000014#aux_1, re-keys Discovery Green #aux_4 with contributing status, and stays compact", () => {
  const overridesPath = path.join(REPO_ROOT, "app", "public", "data", "curated_overrides.json");
  const rawBytes = fs.statSync(overridesPath).size;
  const rawText = fs.readFileSync(overridesPath, "utf8");
  const doc = JSON.parse(rawText);
  const overrides = doc.overrides || doc;

  assert.ok(
    rawBytes < 8 * 1024 * 1024,
    `Expected compact curated_overrides.json (< 8 MB) for uncached initial load, got ${(
      rawBytes /
      (1024 * 1024)
    ).toFixed(2)} MB`
  );
  assert.equal("0621100000014#aux_1" in overrides, false);
  assert.ok(overrides["0621100000014"], "Primary 0621100000014 override must remain intact");
  assert.equal(overrides["0621100000014"].address, "1127 KEY ST");
  assert.equal(overrides["0621100000014"].year_built, 1928);
  assert.equal("1319030010003#aux_1" in overrides, false);
  assert.ok(overrides["1319030010001#aux_4"], "Discovery Green re-keyed #aux_4 override must exist");
  assert.equal(overrides["1319030010001#aux_4"].contributing, "Outside Historic District");

  const validContrib = new Set(["Contributing", "Non-Contributing", "Outside Historic District"]);
  for (const [k, rec] of Object.entries(overrides)) {
    if (k.includes("#aux_") && rec && rec.geometry && !rec.suppress_only) {
      assert.ok(
        validContrib.has(rec.contributing),
        `Expected explicit contributing status on ${k}, got ${JSON.stringify(rec.contributing)}`
      );
    }
  }
});

test("ES module, stylesheet, curated_overrides, permits, and pmtiles_manifest ?v= cache-busters stay synchronized across app/", () => {
  const versionedFiles = [
    "app/index.html",
    "app/plat-monitor.html",
    "app/js/app.js",
    "app/js/mapController.js",
    "app/js/hcadLink.js",
    "app/js/watchlistSyncHelpers.js",
  ];
  const versions = new Set();
  const re = /(?:styles\/index\.css|\.js)\?v=([0-9A-Za-z._\-]+)/g;
  for (const rel of versionedFiles) {
    const txt = fs.readFileSync(path.join(REPO_ROOT, rel), "utf8");
    let m;
    while ((m = re.exec(txt)) !== null) {
      versions.add(m[1]);
    }
  }

  const manifest = JSON.parse(
    fs.readFileSync(path.join(REPO_ROOT, "app", "public", "data", "pmtiles_manifest.json"), "utf8")
  );
  versions.add(manifest.cache_bust);

  const curatedEditsTxt = fs.readFileSync(path.join(REPO_ROOT, "app", "js", "curatedEdits.js"), "utf8");
  const curatedMatch = curatedEditsTxt.match(/curated_overrides\.json\?v=([0-9A-Za-z._\-]+)/);
  assert.ok(curatedMatch, "curatedEdits.js must carry ?v= cache-buster on curated_overrides.json");
  versions.add(curatedMatch[1]);

  const mapCtrlTxt = fs.readFileSync(path.join(REPO_ROOT, "app", "js", "mapController.js"), "utf8");
  const manifestFetchMatch = mapCtrlTxt.match(/pmtiles_manifest\.json\?v=([0-9A-Za-z._\-]+)/);
  assert.ok(manifestFetchMatch, "mapController.js must carry ?v= cache-buster on pmtiles_manifest.json");
  versions.add(manifestFetchMatch[1]);

  const appTxt = fs.readFileSync(path.join(REPO_ROOT, "app", "js", "app.js"), "utf8");
  const permitsFetchMatch = appTxt.match(/permits_\$\{prefix3\}\.json\?v=([0-9A-Za-z._\-]+)/);
  assert.ok(permitsFetchMatch, "app.js must carry ?v= cache-buster on permits shard fetch");
  versions.add(permitsFetchMatch[1]);

  assert.equal(
    versions.size,
    1,
    `Expected single synchronized ?v= cache-buster across app/ and pmtiles_manifest.json, found: ${[
      ...versions,
    ].join(", ")}`
  );
  assert.match([...versions][0], /^\d{8}[a-z]?$/);
});

test("Modified Clerk search, UI, override repair, and deed chain files contain zero em-dash characters", () => {
  const filesToCheck = [
    "app/index.html",
    "app/plat-monitor.html",
    "app/js/app.js",
    "app/js/clerkSearchHelpers.js",
    "app/js/curatedEdits.js",
    "app/js/hcadLink.js",
    "app/js/hcadViewportParcels.js",
    "app/js/mapController.js",
    "app/js/platMonitorHelpers.js",
    "app/js/watchlistSyncHelpers.js",
    "app/styles/index.css",
    "pipeline/atlas_pipeline/enrich_deed_chains.py",
    "pipeline/atlas_pipeline/build_permits_and_contractors.py",
    "pipeline/atlas_pipeline/fetch_plat_and_watchlist.py",
    "pipeline/atlas_pipeline/fix_polygon_orientations.py",
    "pipeline/atlas_pipeline/repair_cross_parcel_aux_overrides.py",
    "tests/unit/clerk_search_and_deed_chains.test.mjs",
  ];
  for (const rel of filesToCheck) {
    const content = fs.readFileSync(path.join(REPO_ROOT, rel), "utf8");
    assert.equal(
      content.includes("\u2014"),
      false,
      `Expected zero em-dash characters in ${rel}`
    );
  }
});
