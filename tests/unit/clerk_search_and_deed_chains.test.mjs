import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  cleanClerkSubdivisionDesc,
  decodeCompactDeedChain,
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

test("ES module and stylesheet ?v= cache-busting query strings stay synchronized across app/", () => {
  const versionedFiles = [
    "app/index.html",
    "app/js/app.js",
    "app/js/mapController.js",
    "app/js/hcadLink.js",
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
  assert.equal(
    versions.size,
    1,
    `Expected a single synchronized ?v= cache-buster across ES modules and CSS, found: ${[
      ...versions,
    ].join(", ")}`
  );
});

test("Modified Clerk search, UI, and deed chain files contain zero em-dash characters", () => {
  const filesToCheck = [
    "app/index.html",
    "app/js/app.js",
    "app/js/clerkSearchHelpers.js",
    "app/js/hcadLink.js",
    "app/js/mapController.js",
    "app/styles/index.css",
    "pipeline/atlas_pipeline/enrich_deed_chains.py",
    "pipeline/atlas_pipeline/build_permits_and_contractors.py",
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
