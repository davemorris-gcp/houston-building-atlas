import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { fileURLToPath } from "node:url";

import { HoustonAtlasApp } from "../../app/js/app.js";
import { isConfidentialOwnerName } from "../../app/js/clerkSearchHelpers.js";
import { hideDisputedClaimLocally, isClaimSuppressed } from "../../app/js/curatedEdits.js";
import {
  buildCohLandmarkDossierCardHtml,
  buildHpoDistrictDossierCardHtml,
  buildHpoParcelSurveyCardHtml,
  HCAD_GRADE_LABELS,
  isArchitecturalStyleDisplayCandidate,
  isPostLandmarkReplacement,
  isPostSurveyReplacement,
  isReplaceableStyleValue,
  isStructuralClassStyleValue,
  isUsableHpoSurveyStyle,
  normalizeHpoLookupAddress,
  normalizeHpoStreetAddressKeys,
  resolveCohLandmarkOverlayMetadata,
  resolveHpoDistrictDocs,
  resolveHpoParcelSurvey,
} from "../../app/js/hpoSurveyHelpers.js";
import { sanitizeHttpUrl, validateExternalAtlasUrl } from "../../app/js/urlValidators.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const DATA_DIR = path.join(REPO_ROOT, "app", "public", "data");
const VENV_PY = path.join(REPO_ROOT, "pipeline", ".venv", "bin", "python");

function resolvePythonExecutable() {
  const candidates = [VENV_PY, process.env.PYTHON, "python3"].filter(Boolean);
  for (const cand of candidates) {
    if (cand === VENV_PY && !fs.existsSync(cand)) continue;
    try {
      execFileSync(
        cand,
        ["-c", "import atlas_pipeline.harvest_coh_hpo_surveys_and_landmarks"],
        {
          env: { ...process.env, PYTHONPATH: path.join(REPO_ROOT, "pipeline") },
          stdio: "ignore",
        }
      );
      return cand;
    } catch {
      // Try next candidate
    }
  }
  return null;
}

const PY_BIN = resolvePythonExecutable();

function runPythonHelper(pyScript, argJson) {
  if (!PY_BIN) {
    if (fs.existsSync(VENV_PY)) {
      assert.fail("PY_BIN unavailable despite pipeline/.venv existing: cross-language parity checks must execute");
    }
    return null;
  }
  const raw = execFileSync(PY_BIN, ["-c", pyScript, argJson], {
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: path.join(REPO_ROOT, "pipeline") },
  });
  return JSON.parse(raw.trim());
}

const UNI_HYPHEN = String.fromCodePoint(0x2010);
const EN_DASH = String.fromCodePoint(0x2013);
const EM_DASH = String.fromCodePoint(0x2014);
const HALF_FRAC = String.fromCodePoint(0x00bd);

function hasForbiddenDash(str) {
  const s = String(str || "");
  return s.includes(UNI_HYPHEN) || s.includes(EN_DASH) || s.includes(EM_DASH);
}

function createFakeAtlasApp(overrides = {}) {
  const app = Object.create(HoustonAtlasApp.prototype);
  app.hpoSurveyIndex = null;
  app.archivalIndex = null;
  app.citydirIndex = null;
  app.haifIndex = null;
  app._globalRejections = null;
  app.mapController = { overlaysData: null };
  app.filterStore = {
    getState: () => ({
      layers: { historicMap: false },
      historicMapId: "",
    }),
  };
  Object.assign(app, overrides);
  return app;
}

test("UT-M12-1: normalizeHpoLookupAddress and normalizeHpoStreetAddressKeys uphold address normalization across directionals, unit/rear suffixes, fused/trailing 1/2 addresses, abbreviated ranges, and Saint/Mt/Ave variants", () => {
  const vectors = [
    ["1119 North Walling Street, Houston, TX 77009", "1119 N WALLING"],
    ["1119 N. Walling St.", "1119 N WALLING"],
    ["1511 North Boulevard", "1511 NORTH"],
    ["1511 N. Blvd.", "1511 NORTH"],
    ["1512 South Boulevard", "1512 SOUTH"],
    ["1512 S Blvd", "1512 SOUTH"],
    ["3917 Main Street, #16", "3917 MAIN"],
    ["2700 N Albany St Ste 100, Houston TX 77006", "2700 N ALBANY"],
    ["804 Avondale St Apt B", "804 AVONDALE"],
    ["512 Harvard St Rear", "512 HARVARD"],
    ["512 Harvard St (Rear)", "512 HARVARD"],
    [`3410 ${HALF_FRAC} Burlington St`, "3410 1/2 BURLINGTON"],
    ["3410 1/2 Burlington Street", "3410 1/2 BURLINGTON"],
    ["18361/2 Columbia St", "1836 1/2 COLUMBIA"],
    ["2091/2 E WOODLAND ST", "209 1/2 E WOODLAND"],
    ["209 E WOODLAND ST 1/2", "209 1/2 E WOODLAND"],
    ["701 E 51/2 St", "701 E 5 1/2"],
    ["111 E 31st 1/2 St", "111 E 31 1/2"],
    [`111 E. 31 ${HALF_FRAC} Street`, "111 E 31 1/2"],
    [`111 31 ${HALF_FRAC} ST`, "111 31 1/2"],
    ["405 Saint Emanuel St", "405 ST EMANUEL"],
    ["405 St. Emanuel Street", "405 ST EMANUEL"],
    ["1200 Mount Vernon St", "1200 MT VERNON"],
    ["1200 Mt. Vernon Street", "1200 MT VERNON"],
    ["1015 Avenue A", "1015 AVE A"],
    ["1015 Ave. A", "1015 AVE A"],
    ["7106 Avenue E", "7106 AVE E"],
    ["7106 AVE E", "7106 AVE E"],
    ["7106 Avenue N", "7106 AVE N"],
    ["7106 Avenue S", "7106 AVE S"],
    ["7106 Avenue W", "7106 AVE W"],
    ["1000 Heights Blvd N", "1000 N HEIGHTS"],
    ["1000 Heights Blvd S", "1000 S HEIGHTS"],
    ["2200 Memorial Dr E", "2200 E MEMORIAL"],
    ["2200 Memorial Dr W", "2200 W MEMORIAL"],
    ["4-B West Eleventh Place", "4B W 11TH"],
    [`4${String.fromCodePoint(0x2011)}B West 11th Pl`, "4B W 11TH"],
    ["14-D West 11th Place", "14D W 11TH"],
    ["14D West 11th Place", "14D W 11TH"],
    [`1414${EN_DASH}1416 East Austin St`, "1414 E AUSTIN"],
    ["1647-49 Cortlandt St", "1647 CORTLANDT"],
    ["120-122 E 11th St", "120 E 11TH"],
    ["802/804 Commerce St", "802 COMMERCE"],
    ["0 North Blvd", "0 NORTH"],
    ["Avenida De Las Americas", ""],
    ["Main Street", ""],
    ["", ""],
  ];

  const jsOutputs = vectors.map(([inp, expected]) => {
    const actual = normalizeHpoLookupAddress(inp);
    assert.equal(actual, expected, `JS mismatch on ${JSON.stringify(inp)}`);
    return actual;
  });

  assert.deepEqual(normalizeHpoStreetAddressKeys("1647-49 Cortlandt St"), [
    "1647 CORTLANDT",
    "1649 CORTLANDT",
  ]);
  assert.deepEqual(normalizeHpoStreetAddressKeys("120-122 E 11th St"), [
    "120 E 11TH",
    "120 11TH",
    "122 E 11TH",
    "122 11TH",
  ]);

  const streetKeyInputs = [
    "1119 North Walling Street",
    `1414${EN_DASH}1416 East Austin St`,
    "1647-49 Cortlandt St",
    "120-122 E 11th St",
    "18361/2 Columbia St",
    "209 E WOODLAND ST 1/2",
    "701 E 51/2 St",
    "802/804 Commerce St",
    "3909--3917 S. Main St.",
    "14-D West 11th Place",
    "111 E 31st 1/2 St",
    "Avenida De Las Americas",
  ];
  const jsStreetKeyOutputs = streetKeyInputs.map((inp) => normalizeHpoStreetAddressKeys(inp));

  const pyOutputs = runPythonHelper(
    [
      "import json, sys",
      "from atlas_pipeline.harvest_coh_hpo_surveys_and_landmarks import normalize_hpo_lookup_address, normalize_hpo_street_address_keys",
      "payload = json.loads(sys.argv[1])",
      "print(json.dumps({",
      "  'lookup': [normalize_hpo_lookup_address(v) for v in payload['vecs']],",
      "  'keys': [normalize_hpo_street_address_keys(v) for v in payload['keys']],",
      "}))",
    ].join("\n"),
    JSON.stringify({ vecs: vectors.map((v) => v[0]), keys: streetKeyInputs })
  );
  if (pyOutputs !== null) {
    assert.deepEqual(
      jsOutputs,
      pyOutputs.lookup,
      "normalizeHpoLookupAddress JS and Python outputs must match 1:1"
    );
    assert.deepEqual(
      jsStreetKeyOutputs,
      pyOutputs.keys,
      "normalizeHpoStreetAddressKeys JS and Python outputs must match 1:1"
    );
  }
});

test("UT-M12-2: isUsableHpoSurveyStyle, isStructuralClassStyleValue, isArchitecturalStyleDisplayCandidate, and resolveHpoDistrictDocs enforce style filters, boundary URL sanitization, JS/Python parity, and COH district aliases", () => {
  const rejectedVecs = [
    "",
    "n/a",
    "NA",
    "-",
    "Vacant",
    "Lot",
    "Parking lot",
    "New",
    "New construction",
    "New townhome",
    "Building",
    "Secondary structure",
    "Commercial",
    "Vacant parcel",
    "YEAR          BLDG",
    "CIRCA      BLDG Georgian/Colonial Revival",
    "Year Bldg",
    "Circa Bldg",
    "1940 Added quarters in back",
    "1930               Garage apartment American Foursquare",
  ];
  for (const rejected of rejectedVecs) {
    assert.equal(isUsableHpoSurveyStyle(rejected), false, `Should reject non-architectural style '${rejected}'`);
  }
  const acceptedVecs = [
    "Craftsman Bungalow",
    "Queen Anne",
    "Modern Ranch",
    "Colonial Revival",
    "Mediterranean Villa",
  ];
  for (const accepted of acceptedVecs) {
    assert.equal(isUsableHpoSurveyStyle(accepted), true, `Should accept architectural style '${accepted}'`);
  }

  for (const nonCandidate of [
    "",
    "   ",
    "Residential",
    "Historical / Architectural Structure",
    "Houston Historic Structure",
    "structure",
    "none",
    "null",
    "n/a",
    "-",
    "A++",
    "B+",
    "C",
    "c-",
    "F",
    "X",
  ]) {
    assert.equal(
      isArchitecturalStyleDisplayCandidate(nonCandidate),
      false,
      `Should exclude placeholder/grade '${nonCandidate}' from primary style display chip`
    );
  }
  for (const validCandidate of [
    "Craftsman Bungalow",
    "Louisiana French Plantation style",
    "Masonry Bearing",
    "Tilt-Up Concrete",
    "Open Steel Skeleton",
  ]) {
    assert.equal(
      isArchitecturalStyleDisplayCandidate(validCandidate),
      true,
      `Should accept display candidate '${validCandidate}'`
    );
  }

  const allStyleVecs = [...rejectedVecs, ...acceptedVecs];
  const jsStyleBools = allStyleVecs.map((v) => isUsableHpoSurveyStyle(v));
  const replaceableVecs = [
    "",
    "Residential",
    "Historical / Architectural Structure",
    "Houston Historic Structure",
    "historic structure",
    "structure",
    "none",
    "null",
    "n/a",
    "na",
    "-",
    "Wood or Light Steel",
    "Masonry Bearing",
    "Open Steel Skeleton",
    "Reinforced Concrete",
    "Fireproofed Steel",
    "Tilt-Up Concrete",
    "CANOPY ONLY",
    "Paving - Concrete",
    "Utility Building - Frame",
    "Portable/Modular Office",
    "Plant Assets - Industrial",
    "Shed, Storage",
    "C",
    "c",
    "B+",
    "A++",
    "D-",
    "E+",
    "F",
    "X",
    "Craftsman Bungalow",
    "Queen Anne",
    "Beaux-Arts",
    "Louisiana French Plantation style",
    "Miesian Steel & Rose-Brick International Style",
    "Modified Foursquare Frame",
  ];
  assert.equal(isStructuralClassStyleValue("Tilt-Up Concrete"), true);
  assert.equal(isStructuralClassStyleValue("CANOPY ONLY"), true);
  assert.equal(isStructuralClassStyleValue("Plant Assets - Industrial"), true);
  assert.equal(isStructuralClassStyleValue("Louisiana French Plantation style"), false);
  assert.equal(isStructuralClassStyleValue("Miesian Steel & Rose-Brick International Style"), false);
  assert.equal(isStructuralClassStyleValue("Modified Foursquare Frame"), false);

  const jsReplaceableBools = replaceableVecs.map((v) => isReplaceableStyleValue(v));
  const jsStructuralBools = replaceableVecs.map((v) => isStructuralClassStyleValue(v));

  const hpoIndex = JSON.parse(fs.readFileSync(path.join(DATA_DIR, "hpo_district_surveys.json"), "utf8"));
  const pyParity = runPythonHelper(
    [
      "import json, sys",
      "from atlas_pipeline.schema import GENERIC_STYLE_PLACEHOLDERS, STRUCTURAL_STYLE_CLASSES, HCAD_STYLE_GRADE_CODES, is_replaceable_style_value, is_structural_class_style",
      "from atlas_pipeline.harvest_coh_hpo_surveys_and_landmarks import HPO_DISTRICT_NAME_ALIASES, NON_ARCHITECTURAL_SURVEY_STYLES, STRIP_STREET_TAIL, is_usable_hpo_survey_style, normalize_hpo_lookup_address",
      "payload = json.loads(sys.argv[1])",
      "tail_probes = [f'100 OAK {tok}' for tok in sorted(STRIP_STREET_TAIL)]",
      "all_vocab = sorted(GENERIC_STYLE_PLACEHOLDERS | STRUCTURAL_STYLE_CLASSES | HCAD_STYLE_GRADE_CODES | NON_ARCHITECTURAL_SURVEY_STYLES)",
      "print(json.dumps({",
      "  'usable': [is_usable_hpo_survey_style(v) for v in payload['style_vecs']],",
      "  'replaceable': [is_replaceable_style_value(v) for v in payload['repl_vecs']],",
      "  'structural_fn': [is_structural_class_style(v) for v in payload['repl_vecs']],",
      "  'vocab': all_vocab,",
      "  'vocab_usable': [is_usable_hpo_survey_style(v) for v in all_vocab],",
      "  'vocab_replaceable': [is_replaceable_style_value(v) for v in all_vocab],",
      "  'vocab_structural': [is_structural_class_style(v) for v in all_vocab],",
      "  'tail_probes': tail_probes,",
      "  'tail_norm': [normalize_hpo_lookup_address(p) for p in tail_probes],",
      "  'district_aliases': HPO_DISTRICT_NAME_ALIASES,",
      "}))",
    ].join("\n"),
    JSON.stringify({ style_vecs: allStyleVecs, repl_vecs: replaceableVecs })
  );
  if (pyParity !== null) {
    assert.deepEqual(
      jsStyleBools,
      pyParity.usable,
      "isUsableHpoSurveyStyle JS and Python outputs must match 1:1"
    );
    assert.deepEqual(
      jsStructuralBools,
      pyParity.structural_fn,
      "isStructuralClassStyleValue (JS) and is_structural_class_style (Python) must match 1:1"
    );
    assert.deepEqual(
      jsReplaceableBools,
      pyParity.replaceable,
      "isReplaceableStyleValue (JS) and is_replaceable_style_value (Python) must match 1:1"
    );
    assert.deepEqual(
      pyParity.vocab.map((v) => isUsableHpoSurveyStyle(v)),
      pyParity.vocab_usable,
      "Every Python survey/style vocabulary token must classify identically in isUsableHpoSurveyStyle"
    );
    assert.deepEqual(
      pyParity.vocab.map((v) => isReplaceableStyleValue(v)),
      pyParity.vocab_replaceable,
      "Every Python placeholder/grade/structural token must classify identically in isReplaceableStyleValue"
    );
    assert.deepEqual(
      pyParity.vocab.map((v) => isStructuralClassStyleValue(v)),
      pyParity.vocab_structural,
      "Every Python structural token must classify identically in isStructuralClassStyleValue"
    );
    assert.deepEqual(
      pyParity.tail_probes.map((p) => normalizeHpoLookupAddress(p)),
      pyParity.tail_norm,
      "Every Python STRIP_STREET_TAIL suffix token must normalize identically in JS"
    );
    assert.deepEqual(
      hpoIndex.district_aliases,
      pyParity.district_aliases,
      "hpo_district_surveys.json district_aliases must match HPO_DISTRICT_NAME_ALIASES 1:1"
    );
    const strippedAliasHpo = { ...hpoIndex, district_aliases: undefined };
    for (const [aliasKey, expectedCanon] of Object.entries(pyParity.district_aliases)) {
      assert.equal(
        resolveHpoDistrictDocs(strippedAliasHpo, aliasKey)?.districtName,
        expectedCanon,
        `Fallback JS district alias resolution must map '${aliasKey}' -> '${expectedCanon}'`
      );
    }
  }

  // Boundary URL sanitization inside resolveHpoDistrictDocs
  const untrustedHpo = {
    districts: {
      "Test Historic District": {
        designation_date: "2020-01-01",
        inventory_pdf_url: "javascript:alert(1)",
        designation_report_url: "data:text/html,<script>alert(1)</script>",
        guidelines_pdf_url: "https://www.houstontx.gov/planning/HistoricPres/valid_guidelines.pdf",
        map_pdf_url: "ftp://example.com/map.pdf",
      },
    },
  };
  const sanitizedDocs = resolveHpoDistrictDocs(untrustedHpo, "Test Historic District");
  assert.ok(sanitizedDocs);
  assert.equal(sanitizedDocs.inventoryPdfUrl, "");
  assert.equal(sanitizedDocs.designationReportUrl, "");
  assert.equal(
    sanitizedDocs.guidelinesPdfUrl,
    "https://www.houstontx.gov/planning/HistoricPres/valid_guidelines.pdf"
  );
  assert.equal(sanitizedDocs.mapPdfUrl, "");

  assert.equal(resolveHpoDistrictDocs(hpoIndex, ""), null);
  assert.equal(resolveHpoDistrictDocs(hpoIndex, "none"), null);
  assert.equal(resolveHpoDistrictDocs(hpoIndex, "Outside Historic District"), null);
  assert.equal(resolveHpoDistrictDocs(hpoIndex, "Nonexistent District"), null);

  const norhill = resolveHpoDistrictDocs(hpoIndex, "Norhill");
  assert.ok(norhill);
  assert.equal(norhill.districtName, "Norhill Historic District");
  assert.ok(norhill.inventoryPdfUrl.endsWith("/Norhill_Inventory.pdf"));
  assert.ok(norhill.guidelinesPdfUrl.includes("Norhill-Design-Guidelines"));
  assert.ok(norhill.mapPdfUrl.endsWith("/pj17025_Norhill.pdf"));

  const courtlandAlias = resolveHpoDistrictDocs(hpoIndex, "Courtland Place");
  assert.ok(courtlandAlias);
  assert.equal(courtlandAlias.districtName, "Courtlandt Place Historic District");

  const mainStAlias = resolveHpoDistrictDocs(hpoIndex, "Main Street Market Square Historic District");
  assert.ok(mainStAlias);
  assert.equal(mainStAlias.districtName, "Main Street/Market Square Historic District");
  assert.equal(
    resolveHpoDistrictDocs(hpoIndex, "Main St Market Sq")?.districtName,
    "Main Street/Market Square Historic District"
  );

  const w11Alias = resolveHpoDistrictDocs(hpoIndex, "W. 11th Place");
  assert.ok(w11Alias);
  assert.equal(w11Alias.districtName, "West Eleventh Place Historic District");
  assert.equal(
    resolveHpoDistrictDocs(hpoIndex, "W Eleventh Place")?.districtName,
    "West Eleventh Place Historic District"
  );
  assert.equal(
    resolveHpoDistrictDocs(hpoIndex, "OSW")?.districtName,
    "Old Sixth Ward Historic District"
  );
  assert.equal(
    resolveHpoDistrictDocs(hpoIndex, "Houston Heights Historic District East")?.districtName,
    "Houston Heights East Historic District"
  );
});

test("UT-M12-3: resolveHpoParcelSurvey enforces by_hcad, #fp_1 baseId, by_addr fallback, lost_* isolation, post-1965 replacement guard, and field 'hpo_survey' suppression", async (t) => {
  const prevLocalStorage = globalThis.localStorage;
  const lsMap = new Map();
  globalThis.localStorage = {
    getItem: (k) => (lsMap.has(k) ? lsMap.get(k) : null),
    setItem: (k, v) => lsMap.set(k, String(v)),
    removeItem: (k) => lsMap.delete(k),
  };

  try {
    const hpoIndex = {
      version: 1,
      district_order: [
        "Norhill Historic District",
        "Old Sixth Ward Historic District",
        "Glenbrook Valley Historic District",
      ],
      districts: {
        "Norhill Historic District": {
          designation_date: "2000-06-14",
          inventory_pdf_url:
            "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Norhill_Inventory.pdf",
          guidelines_pdf_url:
            "https://www.houstontx.gov/planning/HistoricPres/norhill/Norhill-Design-Guidelines_Jan2026_CQ-FINAL_draft.pdf",
          designation_report_url: "",
          map_pdf_url:
            "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/pj17025_Norhill.pdf",
          surveyed_parcel_count: 2,
          matched_hcad_count: 2,
          status_counts: { Contributing: 2 },
        },
        "Old Sixth Ward Historic District": {
          designation_date: "1998-05-27",
          inventory_pdf_url:
            "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Old_Sixth_Ward_Inventory.pdf",
          guidelines_pdf_url:
            "https://www.houstontx.gov/planning/HistoricPres/guidelines/Design-Guidelines-Old-Sixth-Ward.pdf",
          designation_report_url: "",
          map_pdf_url: "",
          surveyed_parcel_count: 1,
          matched_hcad_count: 0,
          status_counts: { Contributing: 1 },
        },
        "Glenbrook Valley Historic District": {
          designation_date: "2011-06-29",
          inventory_pdf_url:
            "https://www.houstontx.gov/planning/HistoricPres/HistoricPreservationManual/docs_pdfs/Glenbrook_Valley_Inventory.pdf",
          designation_report_url:
            "https://www.houstontx.gov/planning/HistoricPres/HAHC_20101118/II_Glenbrook_Valley_Historic_District.pdf",
          guidelines_pdf_url: "",
          map_pdf_url: "",
          surveyed_parcel_count: 1,
          matched_hcad_count: 1,
          status_counts: { Contributing: 1 },
        },
      },
      items: {
        s1: {
          d: 0,
          a: "1119 N WALLING ST",
          st: "C",
          h: "0620820000016",
          y: 1924,
          yr: "c. 1924",
          sy: "Pediment Bungalow",
          lg: "LT 1 BLK 116 NORTH NORHILL",
          sec: [{ s: "PC", y: "1930", t: "Garage Apartment" }],
        },
        s2: {
          d: 1,
          a: "805 COLORADO ST",
          st: "C",
          lg: "LT 12 HEISS GREGOR",
        },
        s3: {
          d: 2,
          a: "8002 ARLETTA CT",
          st: "C",
          h: "0850120000014",
          y: 1968,
          sy: "Modern Ranch",
        },
        s4: {
          d: 0,
          a: "0 NORTH BLVD",
          st: "V",
        },
      },
      by_hcad: {
        "0620820000016": ["s1"],
        "0850120000014": ["s3"],
      },
      by_addr: {
        "1119 N WALLING": ["s1"],
        "1119 WALLING": ["s1"],
        "805 COLORADO": ["s2"],
        "0 NORTH": ["s4"],
      },
    };

    await t.test("resolves direct by_hcad and #fp_1 baseId matches", () => {
      const fpHit = resolveHpoParcelSurvey(hpoIndex, {
        id: "0620820000016#fp_1",
        year_built: 1924,
      });
      assert.ok(fpHit, "Should resolve by_hcad via #fp_1 baseId even when props.hcad_num is omitted");
      assert.equal(fpHit.id, "s1");
      assert.equal(fpHit.suppressKey, "0620820000016");
      assert.equal(fpHit.statusLabel, "Contributing");
      assert.equal(fpHit.usableStyle, "Pediment Bungalow");
      assert.equal(fpHit.isReplacedStructure, false);
      assert.equal(fpHit.secondaryStructures.length, 1);
    });

    await t.test("enforces by_addr directional fallbacks, opposing cardinal guards, and 0-address rejection", () => {
      const addrHit = resolveHpoParcelSurvey(hpoIndex, {
        id: "unmatched_bld_1",
        address: "1119 North Walling Street, Apt 2",
        year_built: 1926,
      });
      assert.ok(addrHit);
      assert.equal(addrHit.id, "s1");

      const omittedDirHit = resolveHpoParcelSurvey(hpoIndex, {
        id: "unmatched_omitted_dir",
        address: "1119 Walling St",
        year_built: 1926,
        historic_district: "Norhill",
      });
      assert.ok(omittedDirHit, "Short district name ('Norhill') and omitted directional must still match s1");
      assert.equal(omittedDirHit.id, "s1");

      assert.equal(
        resolveHpoParcelSurvey(hpoIndex, {
          id: "opposing_south_walling",
          address: "1119 S Walling St",
          year_built: 1926,
        }),
        null,
        "Opposing cardinal directional ('1119 S Walling St' vs '1119 N WALLING ST') must never cross-match via by_addr"
      );
      assert.equal(
        resolveHpoParcelSurvey(hpoIndex, {
          id: "0620820000016",
          hcad_num: "0620820000016",
          address: "1119 S Walling St",
          year_built: 1926,
        }),
        null,
        "Opposing cardinal directional must also reject stale by_hcad cross-parcel entries"
      );

      assert.equal(
        resolveHpoParcelSurvey(hpoIndex, {
          id: "0620820000016",
          hcad_num: "0620820000016",
          address: "1119 N Walling St",
          year_built: 1924,
          historic_district: "Outside Historic District",
        }),
        null,
        "Parcel explicitly marked 'Outside Historic District' must return null even when hcad_num is in by_hcad"
      );
      assert.equal(
        resolveHpoParcelSurvey(hpoIndex, {
          id: "0620820000016",
          hcad_num: "0620820000016",
          address: "1119 N Walling St",
          year_built: 1924,
          historic_district: "Old Sixth Ward Historic District",
        }),
        null,
        "by_hcad candidate from a different historic district must be rejected"
      );

      const ewCollisionIndex = {
        district_order: ["Houston Heights South Historic District"],
        districts: {
          "Houston Heights South Historic District": {
            designation_date: "2011-01-12",
            inventory_pdf_url: "https://www.houstontx.gov/planning/Heights_South_Inventory.pdf",
          },
        },
        items: {
          s1: { d: 0, a: "210 E 13TH ST", st: "C", y: 1920, sy: "Craftsman Bungalow" },
          s2: { d: 0, a: "210 W 13TH ST", st: "C", y: 1922, sy: "Queen Anne" },
        },
        by_hcad: {},
        by_addr: {
          "210 E 13TH": ["s1"],
          "210 W 13TH": ["s2"],
          "210 13TH": ["s1", "s2"],
        },
      };
      assert.equal(
        resolveHpoParcelSurvey(ewCollisionIndex, {
          id: "unmatched_directionless_13th",
          address: "210 13th St",
          year_built: 1920,
        }),
        null,
        "Directionless '210 13th St' must not arbitrarily match East or West when both exist in by_addr"
      );

      const hyphenRangeDirIndex = {
        district_order: ["Houston Heights South Historic District"],
        districts: {
          "Houston Heights South Historic District": {
            designation_date: "2011-01-12",
            inventory_pdf_url: "https://www.houstontx.gov/planning/Heights_South_Inventory.pdf",
          },
        },
        items: {
          s1: { d: 0, a: "120-122 E 11TH ST", st: "C", y: 1918, sy: "Duplex Bungalow" },
        },
        by_hcad: {},
        by_addr: {
          "120 E 11TH": ["s1"],
          "120 11TH": ["s1"],
        },
      };
      assert.equal(
        resolveHpoParcelSurvey(hyphenRangeDirIndex, {
          id: "opposing_west_11th_on_range",
          address: "120 W 11th St",
          year_built: 1918,
        }),
        null,
        "Hyphenated range '120-122 E 11TH ST' must preserve directional 'E' and reject '120 W 11th St'"
      );

      assert.equal(
        resolveHpoParcelSurvey(hpoIndex, {
          id: "unnumbered_0_bld",
          address: "0 North Blvd",
          year_built: 1925,
        }),
        null,
        "Unnumbered '0 <STREET>' building must never false-match a vacant lot via by_addr"
      );
    });

    await t.test("isolates lost_* demolished sites and #<slug> campus sub-buildings", () => {
      assert.equal(
        resolveHpoParcelSurvey(hpoIndex, {
          id: "lost_norhill_bungalow",
          hcad_num: "0620820000016",
          address: "1119 N WALLING ST",
          year_built: 1924,
        }),
        null
      );
      assert.equal(
        resolveHpoParcelSurvey(hpoIndex, {
          id: "0620820000016",
          is_lost_houston: true,
          hcad_num: "0620820000016",
        }),
        null
      );
      assert.equal(
        resolveHpoParcelSurvey(hpoIndex, {
          id: "0620820000016#north_wing",
          hcad_num: "0620820000016",
          address: "510 CORTLANDT ST",
          year_built: 1920,
        }),
        null,
        "#<slug> sub-building at a different street address must not inherit parent HCAD survey card"
      );
      assert.equal(
        resolveHpoParcelSurvey(hpoIndex, {
          id: "0620820000016#north_garage",
          hcad_num: "0620820000016",
          year_built: 1920,
        }),
        null,
        "Unaddressed #<slug> sub-building must not inherit parent HCAD survey card"
      );
      const matchingSubHit = resolveHpoParcelSurvey(hpoIndex, {
        id: "0620820000016#main_house",
        hcad_num: "0620820000016",
        address: "1119 N WALLING ST",
        year_built: 1924,
      });
      assert.ok(matchingSubHit);
      assert.equal(matchingSubHit.id, "s1");
    });

    await t.test("enforces post-1965 replacement-building guards across by_addr and by_hcad", () => {
      assert.equal(isPostSurveyReplacement(2008, 1925, "PC"), true);
      assert.equal(isPostSurveyReplacement(1992, 0, "C"), true);
      assert.equal(isPostSurveyReplacement(1968, 1968, "C"), false);
      assert.equal(isPostLandmarkReplacement(1968, 0), false);
      assert.equal(isPostLandmarkReplacement(2014, 0), false);
      assert.equal(isPostLandmarkReplacement(2008, 1925), true);

      assert.equal(
        resolveHpoParcelSurvey(hpoIndex, {
          id: "new_townhome_1985",
          address: "1119 N WALLING ST",
          year_built: 1985,
        }),
        null,
        "Post-1965 replacement structure (1985) must not match 1924 survey row via by_addr fallback"
      );
      assert.equal(
        resolveHpoParcelSurvey(hpoIndex, {
          id: "new_infill_1992",
          address: "805 COLORADO ST",
          year_built: 1992,
        }),
        null,
        "Post-1965 replacement structure (1992) must not match undated Old Sixth Ward C row via by_addr"
      );
      const gbHit = resolveHpoParcelSurvey(hpoIndex, {
        id: "0850120000014",
        hcad_num: "0850120000014",
        year_built: 1968,
      });
      assert.ok(gbHit);
      assert.equal(gbHit.isReplacedStructure, false);
      assert.equal(gbHit.usableStyle, "Modern Ranch");

      const replacedParcelHit = resolveHpoParcelSurvey(hpoIndex, {
        id: "0620820000016",
        hcad_num: "0620820000016",
        year_built: 1985,
      });
      assert.ok(replacedParcelHit);
      assert.equal(replacedParcelHit.isReplacedStructure, true);
      assert.equal(replacedParcelHit.usableStyle, "");

      const curatedReplacedHit = resolveHpoParcelSurvey(hpoIndex, {
        id: "0620820000016",
        hcad_num: "0620820000016",
        year_built: 2008,
        is_curated_override: true,
      });
      assert.ok(curatedReplacedHit);
      assert.equal(
        curatedReplacedHit.isReplacedStructure,
        true,
        "Curated modern replacement (year_built=2008, is_curated_override=true) must still flag isReplacedStructure=true"
      );
      assert.equal(curatedReplacedHit.usableStyle, "");
    });

    await t.test("suppresses disputed 'hpo_survey' claims via globalRejections and localStorage", () => {
      const globalRejections = {
        rejectedClaims: [
          {
            hcadNum: "0620820000016",
            field: "hpo_survey",
            disputedValue: "Norhill Historic District: 1119 N WALLING ST (Contributing)",
          },
        ],
      };
      assert.equal(
        isClaimSuppressed(
          {
            hcadNum: "0620820000016",
            field: "hpo_survey",
            value: "Norhill Historic District: 1119 N WALLING ST (Contributing)",
          },
          globalRejections
        ),
        true
      );
      assert.equal(
        resolveHpoParcelSurvey(
          hpoIndex,
          { id: "0620820000016#fp_1", year_built: 1924 },
          { globalRejections }
        ),
        null
      );

      hideDisputedClaimLocally({
        hcadNum: "0850120000014",
        field: "hpo_survey",
        value: "8002 ARLETTA CT",
      });
      assert.equal(
        resolveHpoParcelSurvey(hpoIndex, {
          id: "0850120000014",
          hcad_num: "0850120000014",
          year_built: 1968,
        }),
        null
      );
    });
  } finally {
    globalThis.localStorage = prevLocalStorage;
  }
});

test("UT-M12-4: buildHpoParcelSurveyCardHtml and buildHpoDistrictDossierCardHtml enforce XSS escaping, link deduplication, and zero forbidden dashes", () => {
  const xssParcelCard = buildHpoParcelSurveyCardHtml({
    id: "s99",
    suppressKey: '0620820000016" onclick="evil()',
    claimValue: 'Norhill: <img src=x onerror=alert(1)>',
    districtName: 'Norhill <script>alert("x")</script>',
    address: '1119 "Walling" & <Main>',
    altAddress: "1121 <Alt>",
    statusCode: "PC",
    statusLabel: "Potentially <Contributing>",
    surveyYear: 1924,
    surveyYearLabel: "c. 1924 <script>",
    surveyStyle: 'Craftsman "Bungalow" <svg>',
    usableStyle: 'Craftsman "Bungalow" <svg>',
    legalDesc: "LT 1 & <BLK 2>",
    secondaryStructures: [
      { statusCode: "C", statusLabel: "Contributing", year: "1930<x>", type: "Garage <apt>" },
    ],
    inventoryPdfUrl: 'https://www.houstontx.gov/inv.pdf?a=1&b="2"',
    guidelinesPdfUrl: "https://www.houstontx.gov/guide.pdf",
    designationReportUrl: 'https://www.houstontx.gov/inv.pdf?a=1&b="2"',
    mapPdfUrl: "https://www.houstontx.gov/map.pdf",
    isReplacedStructure: true,
  });

  assert.ok(xssParcelCard.includes('id="inspector-hpo-survey-card"'));
  assert.ok(xssParcelCard.includes("status-potentially"));
  assert.ok(xssParcelCard.includes("hpo-survey-note"));
  assert.ok(xssParcelCard.includes('data-flag-field="hpo_survey"'));
  assert.ok(xssParcelCard.includes("Boundary / Lot Map (PDF)"));
  assert.equal(xssParcelCard.includes("<script>"), false, "Raw <script> tag must be escaped");
  assert.equal(xssParcelCard.includes("<img"), false, "Raw <img> tag must be escaped");
  assert.equal(xssParcelCard.includes("<svg>"), false, "Raw <svg> tag must be escaped");
  assert.equal(xssParcelCard.includes('onclick="evil()'), false, "Attribute injection must be escaped");
  assert.equal(
    xssParcelCard.includes("Designation Report (PDF)"),
    false,
    "Identical designationReportUrl === inventoryPdfUrl must be deduplicated"
  );
  assert.equal(hasForbiddenDash(xssParcelCard), false);

  const dossierHtml = buildHpoDistrictDossierCardHtml('Norhill "<script>"', {
    districtName: "Norhill Historic District",
    designationDate: "2000-06-14<x>",
    surveyedParcelCount: 750,
    statusCounts: {
      Contributing: 600,
      "Potentially Contributing": 40,
      "Non-Contributing": 100,
      Vacant: 10,
    },
    inventoryPdfUrl: "https://www.houstontx.gov/inv.pdf",
    guidelinesPdfUrl: "https://www.houstontx.gov/guide.pdf",
    designationReportUrl: "https://www.houstontx.gov/report.pdf",
    mapPdfUrl: "https://www.houstontx.gov/map.pdf",
  });
  assert.ok(dossierHtml.includes('id="boundary-hpo-district-docs-card"'));
  assert.ok(dossierHtml.includes("District Inventory (PDF)"));
  assert.ok(dossierHtml.includes("Design Guidelines (PDF)"));
  assert.ok(dossierHtml.includes("Designation Report (PDF)"));
  assert.ok(dossierHtml.includes("Boundary / Lot Map (PDF)"));
  assert.equal(dossierHtml.includes("<script>"), false);
  assert.equal(hasForbiddenDash(dossierHtml), false);

  // HTTP(S) protocol allowlist on all PDF buttons (rejects javascript:, data:, vbscript:)
  assert.equal(sanitizeHttpUrl("javascript:alert(1)"), "");
  assert.equal(sanitizeHttpUrl("data:text/html,<script>alert(1)</script>"), "");
  assert.equal(sanitizeHttpUrl("vbscript:msgbox(1)"), "");
  assert.equal(
    sanitizeHttpUrl("https://www.houstontx.gov/planning/HistoricPres/inv.pdf"),
    "https://www.houstontx.gov/planning/HistoricPres/inv.pdf"
  );

  const unsafeLandmarkHtml = buildCohLandmarkDossierCardHtml({
    landmarkCode: "20PL182",
    reportPdfUrl: "javascript:alert(1)",
    secondaryPdfUrl: "data:text/html,<script>alert(1)</script>",
  });
  assert.equal(unsafeLandmarkHtml.includes("javascript:"), false);
  assert.equal(unsafeLandmarkHtml.includes("data:text/html"), false);
  assert.ok(unsafeLandmarkHtml.includes("coh-landmark-tpia-note"));

  const unsafeDistrictHtml = buildHpoDistrictDossierCardHtml("Norhill", {
    districtName: "Norhill Historic District",
    surveyedParcelCount: 10,
    statusCounts: { Contributing: 10 },
    inventoryPdfUrl: "javascript:alert(1)",
    guidelinesPdfUrl: "data:text/html,<script>alert(1)</script>",
    designationReportUrl: "vbscript:evil()",
    mapPdfUrl: "javascript:void(0)",
  });
  assert.equal(unsafeDistrictHtml.includes("javascript:"), false);
  assert.equal(unsafeDistrictHtml.includes("data:text/html"), false);
  assert.equal(unsafeDistrictHtml.includes("vbscript:"), false);
});

test("UT-M12-5: HoustonAtlasApp HPO style chip fallback and .coh-landmark-report-card NRHP fallback + TPIA note + normalized address lookup", () => {
  const hpoIndex = JSON.parse(fs.readFileSync(path.join(DATA_DIR, "hpo_district_surveys.json"), "utf8"));
  const overlays = JSON.parse(fs.readFileSync(path.join(DATA_DIR, "overlays.json"), "utf8"));
  const fakeApp = createFakeAtlasApp({
    hpoSurveyIndex: hpoIndex,
    mapController: { overlaysData: overlays },
  });

  // 1. Style chip fallback from HPO survey when HCAD style is structural 'Wood or Light Steel'
  const sampleHcad = Object.keys(hpoIndex.by_hcad).find((hc) => {
    const sid = hpoIndex.by_hcad[hc][0];
    const it = hpoIndex.items[sid];
    return it && it.y && it.y <= 1950 && isUsableHpoSurveyStyle(it.sy);
  });
  assert.ok(sampleHcad, "Expected at least one matched HCAD parcel with usable HPO survey style");
  const styleInfo = fakeApp._resolveStyleAndClassInfo({
    id: sampleHcad,
    hcad_num: sampleHcad,
    year_built: 1920,
    style: "wood or light steel",
    bld_style: "Wood or Light Steel",
    use_category: "Single-Family Residential",
  });
  assert.ok(styleInfo.displayStyle.length > 0);
  assert.notEqual(styleInfo.displayStyle.toLowerCase(), "wood or light steel");
  assert.ok(
    styleInfo.chips.some((c) => String(c.note || "").includes("COH HPO Survey")),
    "Expected primary style chip attributed to COH HPO Survey even when props.style is lowercase 'wood or light steel'"
  );
  const resPlaceholderInfo = fakeApp._resolveStyleAndClassInfo({
    id: sampleHcad,
    hcad_num: sampleHcad,
    year_built: 1920,
    style: "Residential",
    use_category: "Single-Family Residential",
  });
  assert.ok(
    resPlaceholderInfo.chips.some((c) => String(c.note || "").includes("COH HPO Survey")),
    "Expected generic 'Residential' placeholder to fall through to COH HPO Survey style"
  );
  for (const gradeTok of ["c", "B+", "F"]) {
    const gradeInfo = fakeApp._resolveStyleAndClassInfo({
      id: sampleHcad,
      hcad_num: sampleHcad,
      year_built: 1920,
      style: gradeTok,
      bld_style: gradeTok,
      use_category: "Single-Family Residential",
    });
    assert.ok(
      gradeInfo.chips.some((c) => String(c.note || "").includes("COH HPO Survey")),
      `Expected HCAD quality grade '${gradeTok}' to fall through to COH HPO Survey style`
    );
  }
  const tiltUpInfo = fakeApp._resolveStyleAndClassInfo({
    id: sampleHcad,
    hcad_num: sampleHcad,
    year_built: 1920,
    style: "Tilt-Up Concrete",
    bld_style: "Tilt-Up Concrete",
    use_category: "Commercial",
  });
  assert.ok(
    tiltUpInfo.chips.some((c) => String(c.note || "").includes("COH HPO Survey")),
    "Expected CAMA structural class 'Tilt-Up Concrete' to fall through to COH HPO Survey style"
  );
  const plantationStyleInfo = fakeApp._resolveStyleAndClassInfo({
    id: sampleHcad,
    hcad_num: sampleHcad,
    year_built: 1920,
    style: "Louisiana French Plantation style",
    use_category: "Single-Family Residential",
  });
  assert.equal(
    plantationStyleInfo.displayStyle,
    "Louisiana French Plantation style",
    "Legitimate architectural style containing 'Plantation' must stay primary Architectural Style"
  );

  // Verify demolished COH Landmark with stale hcad_num never leaks onto standing replacement building
  const demolishedOverlay = {
    landmarks: {
      features: [
        {
          properties: {
            hcad_num: "0990010000001",
            name: "Demolished Pioneer Cottage",
            address: "400 Old Market St",
            year_built: 1888,
            demolished: true,
            lm_num: "LM888",
            report_pdf_url: "https://www.houstontx.gov/planning/HistoricPres/LM888_Demolished.pdf",
          },
        },
      ],
    },
  };
  const standingReplacementMeta = resolveCohLandmarkOverlayMetadata(
    demolishedOverlay,
    { id: "0990010000001", hcad_num: "0990010000001", address: "400 Old Market St", year_built: 2015 },
    "400 Old Market St"
  );
  assert.equal(standingReplacementMeta.reportPdfUrl, "");
  assert.equal(standingReplacementMeta.landmarkCode, "");
  const lostSiteMeta = resolveCohLandmarkOverlayMetadata(
    demolishedOverlay,
    {
      id: "lost_pioneer_cottage",
      is_lost_houston: true,
      hcad_num: "0990010000001",
      address: "400 Old Market St",
    },
    "Demolished Pioneer Cottage"
  );
  assert.equal(lostSiteMeta.landmarkCode, "LM888");
  assert.equal(
    lostSiteMeta.reportPdfUrl,
    "https://www.houstontx.gov/planning/HistoricPres/LM888_Demolished.pdf"
  );

  // 2. Directly exercise resolveCohLandmarkOverlayMetadata + buildCohLandmarkDossierCardHtml on Isabella Court ('3917 Main Street, #16') and #<slug> sub-buildings
  const isabellaFeature = overlays.landmarks.features.find((f) =>
    String(f.properties?.name || "").includes("Isabella Court")
  );
  assert.ok(isabellaFeature, "Expected Isabella Court in overlays.landmarks");
  const isabellaCallerProps = Object.freeze({
    id: "isabella_unit_16",
    address: "3917 Main Street, #16",
    year_built: 1929,
  });
  const resolvedIsabella = resolveCohLandmarkOverlayMetadata(
    overlays,
    isabellaCallerProps,
    "Houston Commercial Building"
  );
  assert.equal(resolvedIsabella.reportPdfUrl, "");
  assert.ok(
    resolvedIsabella.secondaryPdfUrl.includes("atlas.thc.texas.gov"),
    "Isabella Court unit address must resolve NRHP secondary_pdf_url via normalizeHpoLookupAddress"
  );
  assert.ok(resolvedIsabella.landmarkCode.length > 0, "Expected HPO landmark code for Isabella Court");

  const isabellaCardHtml = buildCohLandmarkDossierCardHtml({
    ...resolvedIsabella,
    isCuratedOverride: false,
    sourceCitation: "",
  });
  assert.ok(isabellaCardHtml.includes("coh-landmark-report-card"));
  assert.ok(isabellaCardHtml.includes("Open National Register Nomination (PDF)"));
  assert.ok(isabellaCardHtml.includes("coh-landmark-tpia-note"));
  assert.equal(isabellaCardHtml.includes("Open Official Landmark Report (PDF)"), false);
  assert.equal(hasForbiddenDash(isabellaCardHtml), false);

  // Verify #<slug> sub-buildings (both non-matching and campus-shared address) and post-1965 #fp_2 replacements never inherit parent HCAD landmark or relocated_structures metadata
  const syntheticOverlays = {
    landmarks: {
      features: [
        {
          properties: {
            hcad_num: "0400030000014",
            name: "Kelloum-Noble House",
            address: "212 Dallas St",
            year_built: 1847,
            lm_num: "LM001",
            report_pdf_url: "https://www.houstontx.gov/planning/HistoricPres/Kelloum_Noble.pdf",
            builder: "Nathaniel Kellum",
          },
        },
      ],
    },
    relocated_structures: {
      features: [
        {
          properties: {
            hcad_num: "0400030000014",
            year_built: 1847,
            builder: "Relocated Park Builder",
            was_relocated: true,
          },
        },
      ],
    },
  };
  const subBldResolved = resolveCohLandmarkOverlayMetadata(
    syntheticOverlays,
    { id: "0400030000014#st_john_church", hcad_num: "0400030000014", address: "1100 Bagby St" },
    "St. John Church"
  );
  assert.equal(subBldResolved.reportPdfUrl, "");
  assert.equal(subBldResolved.landmarkCode, "");
  assert.equal(subBldResolved.builder, "");
  assert.equal(subBldResolved.wasRelocated, false);

  const sharedAddrSubBldResolved = resolveCohLandmarkOverlayMetadata(
    syntheticOverlays,
    {
      building_id: "0400030000014#science_annex",
      hcad_num: "0400030000014",
      address: "212 Dallas St",
      year_built: 1998,
    },
    "Science Annex"
  );
  assert.equal(sharedAddrSubBldResolved.reportPdfUrl, "");
  assert.equal(sharedAddrSubBldResolved.landmarkCode, "");
  assert.equal(sharedAddrSubBldResolved.builder, "");
  assert.equal(sharedAddrSubBldResolved.wasRelocated, false);

  const post1965Fp2Resolved = resolveCohLandmarkOverlayMetadata(
    syntheticOverlays,
    {
      building_id: "0400030000014#fp_2",
      hcad_num: "0400030000014",
      address: "212 Dallas St",
      year_built: 2019,
    },
    "212 Dallas St"
  );
  assert.equal(post1965Fp2Resolved.reportPdfUrl, "");
  assert.equal(post1965Fp2Resolved.landmarkCode, "");
  assert.equal(post1965Fp2Resolved.builder, "");
  assert.equal(post1965Fp2Resolved.wasRelocated, false);

  const historicFp1Resolved = resolveCohLandmarkOverlayMetadata(
    syntheticOverlays,
    {
      building_id: "0400030000014#fp_1",
      hcad_num: "0400030000014",
      address: "212 Dallas St",
      year_built: 1847,
    },
    "212 Dallas St"
  );
  assert.equal(
    historicFp1Resolved.reportPdfUrl,
    "https://www.houstontx.gov/planning/HistoricPres/Kelloum_Noble.pdf"
  );
  assert.equal(historicFp1Resolved.landmarkCode, "LM001");
  assert.equal(historicFp1Resolved.builder, "Nathaniel Kellum");
  assert.equal(historicFp1Resolved.wasRelocated, true);

  // Multi-landmark HCAD range/slash address lookup parity ('1414-1416 Austin St' & '802/804 Commerce St')
  const multiRangeOverlays = {
    landmarks: {
      features: [
        {
          properties: {
            hcad_num: "0010010000001",
            name: "Austin Duplex Landmark",
            address: "1414-1416 Austin St",
            year_built: 1910,
            lm_num: "LM701",
            report_pdf_url: "https://www.houstontx.gov/planning/HistoricPres/Austin_1414.pdf",
          },
        },
        {
          properties: {
            hcad_num: "0010010000001",
            name: "Commerce Corner Landmark",
            address: "802/804 Commerce St",
            year_built: 1895,
            lm_num: "LM702",
            report_pdf_url: "https://www.houstontx.gov/planning/HistoricPres/Commerce_804.pdf",
          },
        },
      ],
    },
  };
  assert.equal(
    resolveCohLandmarkOverlayMetadata(
      multiRangeOverlays,
      { hcad_num: "0010010000001", address: "1414 Austin St", year_built: 1910 },
      "1414 Austin St"
    ).landmarkCode,
    "LM701"
  );
  assert.equal(
    resolveCohLandmarkOverlayMetadata(
      multiRangeOverlays,
      { hcad_num: "0010010000001", address: "804 Commerce St", year_built: 1895 },
      "804 Commerce St"
    ).landmarkCode,
    "LM702"
  );

  // Post-1965 standing Modernist & rehabilitated COH Landmarks with lm_yr == 0 in overlays.json
  const zemanekMeta = resolveCohLandmarkOverlayMetadata(
    overlays,
    {
      id: "0522660000006",
      hcad_num: "0522660000006",
      address: "1723 COLQUITT ST",
      year_built: 1968,
    },
    "John E. Zemanek House"
  );
  assert.equal(zemanekMeta.landmarkCode, "19PL172");
  const zemanekCardHtml = buildCohLandmarkDossierCardHtml({
    ...zemanekMeta,
    isCuratedOverride: false,
    sourceCitation: "",
  });
  assert.ok(zemanekCardHtml.includes("coh-landmark-report-card"));
  assert.ok(zemanekCardHtml.includes("coh-landmark-tpia-note"));

  const dunbarMeta = resolveCohLandmarkOverlayMetadata(
    overlays,
    {
      id: "1168890000001",
      hcad_num: "1168890000001",
      address: "2000 HOLLY HALL ST",
      year_built: 2014,
    },
    "2000 HOLLY HALL ST"
  );
  assert.equal(dunbarMeta.landmarkCode, "19L328");
  assert.ok(dunbarMeta.reportPdfUrl.startsWith("https://www.houstontx.gov/"));

  // Dual-PDF landmark (0011820000001 Cheek-Neal Coffee Company Building) hydrates builder & originalCost even when both PDF URLs are pre-populated on props
  const cheekNealMeta = resolveCohLandmarkOverlayMetadata(
    overlays,
    {
      id: "0011820000001",
      hcad_num: "0011820000001",
      address: "2017 PRESTON ST",
      year_built: 1917,
      landmark_report_url:
        "https://www.houstontx.gov/planning/HistoricPres/landmarks/15PL126_2017_Preston_Cheek-Neal_Bldg.pdf",
      secondary_pdf_url:
        "https://www.houstontx.gov/planning/HistoricPres/landmarks/05L121_Cheek-Neal_Coffee_2017_Preston.pdf",
    },
    "Cheek-Neal Coffee Company Building"
  );
  assert.equal(cheekNealMeta.builder, "General Contracting");
  assert.equal(cheekNealMeta.originalCost, "$100,000");
  assert.equal(Object.keys(HCAD_GRADE_LABELS).length, 18);

  // Directional & distinct-HCAD isolation: 911 E WOODLAND ST (0620410000015) must NOT inherit 911 Woodland St (20PL182, 0513790000023)
  const eastWoodlandNonLandmark = resolveCohLandmarkOverlayMetadata(
    overlays,
    {
      id: "0620410000015",
      hcad_num: "0620410000015",
      address: "911 E WOODLAND ST",
      year_built: 1925,
    },
    "911 E Woodland St"
  );
  assert.equal(eastWoodlandNonLandmark.landmarkCode, "");
  assert.equal(eastWoodlandNonLandmark.reportPdfUrl, "");

  // Boundary URL sanitization inside resolveCohLandmarkOverlayMetadata
  const untrustedLmMeta = resolveCohLandmarkOverlayMetadata(
    {
      landmarks: {
        features: [
          {
            properties: {
              hcad_num: "0010010000999",
              name: "Untrusted URL Landmark",
              address: "999 Test Ave",
              year_built: 1915,
              lm_num: "LM999",
              report_pdf_url: "javascript:alert(1)",
              secondary_pdf_url: "https://atlas.thc.texas.gov/NR/pdfs/00000001/00000001.pdf",
            },
          },
        ],
      },
    },
    { hcad_num: "0010010000999", address: "999 Test Ave", year_built: 1915 },
    "Untrusted URL Landmark"
  );
  assert.equal(untrustedLmMeta.landmarkCode, "LM999");
  assert.equal(untrustedLmMeta.reportPdfUrl, "");
  assert.equal(
    untrustedLmMeta.secondaryPdfUrl,
    "https://atlas.thc.texas.gov/NR/pdfs/00000001/00000001.pdf"
  );

  // Verify app.js static imports & fetches use active ?v=20261109a cache-buster
  const appJsSrc = fs.readFileSync(path.join(REPO_ROOT, "app", "js", "app.js"), "utf8");
  assert.ok(appJsSrc.includes('from "./hpoSurveyHelpers.js?v=20261109a"'));
  assert.ok(appJsSrc.includes('fetch("public/data/hpo_district_surveys.json?v=20261109a")'));
});

test("UT-M12-6: Live hpo_district_surveys.json, overlays.json, curated_overrides.json, buildings.geojson, and search_index.json uphold referential integrity, multi-building HCAD isolation, URL validity, and 0 header/confidential/dash leaks", () => {
  const rawHpo = fs.readFileSync(path.join(DATA_DIR, "hpo_district_surveys.json"), "utf8");
  const rawOverlays = fs.readFileSync(path.join(DATA_DIR, "overlays.json"), "utf8");
  const rawOverrides = fs.readFileSync(path.join(DATA_DIR, "curated_overrides.json"), "utf8");
  const rawBuildings = fs.readFileSync(path.join(DATA_DIR, "buildings.geojson"), "utf8");
  const rawSearchIdx = fs.readFileSync(path.join(DATA_DIR, "search_index.json"), "utf8");
  const ownerHeaderLeakRe = /\b(?:PROPERTY\s+OWNERS?|CURRENT\s+OWNERS?|OWNERS?|APPLICANTS?)\s*:/i;

  // 1. Single-line compact JSON, zero forbidden Unicode dashes, and zero OWNER(S):/APPLICANT(S): header leaks
  for (const [label, raw] of [
    ["hpo_district_surveys.json", rawHpo],
    ["overlays.json", rawOverlays],
    ["curated_overrides.json", rawOverrides],
    ["buildings.geojson", rawBuildings],
    ["search_index.json", rawSearchIdx],
  ]) {
    assert.equal(raw.trimEnd().includes("\n"), false, `${label} must be single-line compact JSON`);
    assert.equal(hasForbiddenDash(raw), false, `${label} must contain zero U+2010/U+2013/U+2014 dashes`);
    assert.equal(ownerHeaderLeakRe.test(raw), false, `${label} leaked OWNERS:/APPLICANTS: personal header metadata`);
  }

  // 2. Milestone 12 HPO district survey structural invariants & coverage floors
  const hpo = JSON.parse(rawHpo);
  assert.equal(hpo.version, 1);
  assert.equal(hpo.district_count, 23, "Expected 23/23 COH Historic Districts");
  assert.equal(hpo.district_order.length, 23);
  assert.equal(Object.keys(hpo.districts).length, 23);
  assert.equal(
    typeof hpo.district_aliases === "object" && hpo.district_aliases !== null,
    true,
    "hpo_district_surveys.json must export canonical district_aliases map"
  );
  assert.equal(Object.keys(hpo.district_aliases).length, 21, "Expected 21 district alias mappings");
  for (const [aliasKey, canonName] of Object.entries(hpo.district_aliases)) {
    assert.ok(
      canonName in hpo.districts,
      `district_aliases[${aliasKey}] -> '${canonName}' must reference a valid district in hpo.districts`
    );
    assert.equal(
      resolveHpoDistrictDocs(hpo, aliasKey)?.districtName,
      canonName,
      `resolveHpoDistrictDocs must resolve district alias '${aliasKey}' -> '${canonName}'`
    );
  }
  const readmeText = fs.readFileSync(path.join(REPO_ROOT, "README.md"), "utf8");
  assert.equal(
    readmeText.includes(`${hpo.matched_hcad_parcels.toLocaleString("en-US")} HCAD parcels`),
    true,
    "README.md must stay synchronized with hpo_district_surveys.json matched_hcad_parcels"
  );
  assert.ok(hpo.total_surveyed_rows >= 6165, `Expected >= 6,165 HPO survey rows, got ${hpo.total_surveyed_rows}`);
  assert.ok(
    hpo.matched_hcad_parcels >= 5490,
    `Expected >= 5,490 matched HCAD parcels, got ${hpo.matched_hcad_parcels}`
  );
  assert.ok(
    hpo.matched_survey_rows >= hpo.matched_hcad_parcels,
    "matched_survey_rows must be >= matched_hcad_parcels"
  );

  const itemKeys = Object.keys(hpo.items);
  assert.equal(itemKeys.length, hpo.total_surveyed_rows);
  for (let i = 0; i < itemKeys.length; i += 1) {
    assert.equal(itemKeys[i], `s${i + 1}`, `Expected sequential key s${i + 1} at index ${i}`);
  }
  assert.equal(Object.keys(hpo.by_hcad).length, hpo.matched_hcad_parcels);

  // Zero orphan or dangling survey IDs + zero confidential owner tokens, Owner_Mailto leaks, or header-corrupted styles
  const referencedSids = new Set();
  for (const [hc, sids] of Object.entries(hpo.by_hcad)) {
    assert.match(hc, /^\d{13}$/, `Invalid 13-digit HCAD key in hpo.by_hcad: ${hc}`);
    assert.ok(Array.isArray(sids) && sids.length >= 1);
    for (const sid of sids) {
      assert.ok(sid in hpo.items, `Dangling sid ${sid} in by_hcad[${hc}]`);
      referencedSids.add(sid);
    }
  }
  for (const [ak, sids] of Object.entries(hpo.by_addr)) {
    for (const sid of sids) {
      assert.ok(sid in hpo.items, `Dangling sid ${sid} in by_addr[${ak}]`);
      if (ak.startsWith("0 ")) {
        assert.equal(
          hpo.items[sid].st,
          "V",
          `Any '0 <STREET>' key in hpo.by_addr (${ak}) must reference only Vacant ('V') rows`
        );
      }
      referencedSids.add(sid);
    }
  }
  assert.equal(
    resolveHpoParcelSurvey(hpo, {
      id: "unnumbered_live_check",
      address: "0 North Blvd",
      year_built: 1925,
    }),
    null,
    "resolveHpoParcelSurvey must block unnumbered '0 <STREET>' fallback matches on live bundle"
  );
  for (const [sid, item] of Object.entries(hpo.items)) {
    assert.equal(isConfidentialOwnerName(item.a), false, `Confidential token in item ${sid}.a`);
    assert.equal(isConfidentialOwnerName(item.lg || ""), false, `Confidential token in item ${sid}.lg`);
    assert.equal("owner" in item || "Owner_Mailto" in item, false);
    if (item.sy) {
      assert.equal(item.sy.includes("  "), false, `Item ${sid} has uncollapsed multi-column style '${item.sy}'`);
      assert.equal(
        /\b(?:year|circa)\s+bldg\b/i.test(item.sy),
        false,
        `Item ${sid} leaked PDF header into sy: '${item.sy}'`
      );
    }
  }

  let sumDistrictRows = 0;
  for (const [dIdx, dName] of hpo.district_order.entries()) {
    const dMeta = hpo.districts[dName];
    assert.ok(dMeta, `Missing district metadata for ${dName}`);
    sumDistrictRows += dMeta.surveyed_parcel_count;
    const uniqueDistrictHcads = new Set();
    for (const it of Object.values(hpo.items)) {
      if (it.d === dIdx && it.h) {
        uniqueDistrictHcads.add(it.h);
      }
    }
    assert.equal(
      dMeta.matched_hcad_count,
      uniqueDistrictHcads.size,
      `${dName} matched_hcad_count must equal unique matched HCAD accounts (${uniqueDistrictHcads.size})`
    );
    assert.ok(dMeta.inventory_pdf_url, `${dName} must carry a verified inventory_pdf_url`);
    for (const urlField of [
      "inventory_pdf_url",
      "designation_report_url",
      "guidelines_pdf_url",
      "map_pdf_url",
    ]) {
      const u = dMeta[urlField];
      if (u) {
        const res = validateExternalAtlasUrl(u);
        assert.equal(res.valid, true, `Invalid ${urlField} on ${dName}: ${u} (${res.reason})`);
      }
    }
  }
  assert.equal(sumDistrictRows, hpo.total_surveyed_rows);

  // 3. COH Landmarks structural invariants & monotonic PDF coverage floors (>= 482/509)
  const overlays = JSON.parse(rawOverlays);
  const hdFeatures = overlays.historic_districts?.features || [];
  assert.equal(hdFeatures.length, 23, "overlays.historic_districts must contain all 23 COH districts");
  for (const hf of hdFeatures) {
    const hp = hf.properties || {};
    assert.ok(hp.inventory_pdf_url, `Overlay district ${hp.name} missing inventory_pdf_url`);
    assert.equal(validateExternalAtlasUrl(hp.inventory_pdf_url).valid, true);
  }

  const lmFeatures = overlays.landmarks?.features || [];
  assert.ok(lmFeatures.length >= 509, `Expected >= 509 COH Landmarks in overlays.json, got ${lmFeatures.length}`);
  let withReportPdf = 0;
  let withoutReportPdf = 0;
  let withNrhpFallback = 0;
  for (const lf of lmFeatures) {
    const lp = lf.properties || {};
    assert.equal(isConfidentialOwnerName(lp.name || ""), false);
    assert.equal(ownerHeaderLeakRe.test(lp.pdf_summary || ""), false);
    if (lp.demolished || String(lp.name || "").toUpperCase().includes("DEMOLISHED")) {
      assert.equal(
        String(lp.hcad_num || ""),
        "",
        `Demolished COH Landmark '${lp.name}' must not retain a standing parcel hcad_num`
      );
    }
    if (lp.report_pdf_url) {
      withReportPdf += 1;
      const rCheck = validateExternalAtlasUrl(lp.report_pdf_url);
      assert.equal(rCheck.valid, true, `Invalid landmark report_pdf_url: ${lp.report_pdf_url} (${rCheck.reason})`);
    } else {
      withoutReportPdf += 1;
      assert.equal(String(lp.report_pdf_url || ""), "");
      if (lp.secondary_pdf_url) {
        withNrhpFallback += 1;
      }
    }
    if (lp.secondary_pdf_url) {
      const sCheck = validateExternalAtlasUrl(lp.secondary_pdf_url);
      assert.equal(sCheck.valid, true, `Invalid landmark secondary_pdf_url: ${lp.secondary_pdf_url} (${sCheck.reason})`);
    }
  }
  assert.equal(withReportPdf + withoutReportPdf, lmFeatures.length);
  assert.ok(withReportPdf >= 482, `Expected >= 482 COH Landmarks with verified report_pdf_url, got ${withReportPdf}`);
  assert.ok(
    withNrhpFallback >= 3,
    `Expected >= 3 NRHP secondary_pdf_url fallbacks on unpublished landmarks, got ${withNrhpFallback}`
  );

  // 4. Shared-HCAD & #<slug> sub-building isolation regressions + Sec. 25.025 confidential guards
  const overrides = JSON.parse(rawOverrides).overrides || {};
  assert.equal("0010020000001" in overrides, false);
  assert.equal(overrides["0010330000012"]?.landmark_name, "Ritz Theater");
  assert.notEqual(
    overrides["0010330000012"]?.architect,
    "Eugene T Heiner",
    "911 Preston Ave (Ritz Theater) on 0010330000012 must never be overwritten by 310 Main St (Eugene T Heiner)"
  );
  assert.notEqual(overrides["0010330000012"]?.landmark_code, "08PL63");

  const buildingsFc = JSON.parse(rawBuildings);
  const statsSummary = JSON.parse(fs.readFileSync(path.join(DATA_DIR, "stats_summary.json"), "utf8"));
  const sumDecades = Object.values(statsSummary.decade_counts || {}).reduce((a, b) => a + Number(b), 0);
  const sumYearSources = Object.values(statsSummary.year_source_counts || {}).reduce((a, b) => a + Number(b), 0);
  assert.equal(sumDecades, statsSummary.total_buildings, "stats_summary decade_counts must sum to total_buildings");
  assert.equal(
    sumYearSources,
    statsSummary.total_buildings,
    "stats_summary year_source_counts must sum to total_buildings"
  );
  const canonicalYearSources = new Set([
    "",
    "hcad_cama",
    "curated_landmark",
    "good_brick_award",
    "archival",
    "hcad_extra_feature",
    "hcad_tieback",
    "adjacent_owner",
    "campus_contiguity",
    "hcad_permit",
    "hcad_deed",
    "subdivision_median",
    "blockface_median",
  ]);
  for (const ysKey of Object.keys(statsSummary.year_source_counts || {})) {
    assert.ok(
      canonicalYearSources.has(ysKey),
      `Non-schema year_source '${ysKey}' in stats_summary.json`
    );
  }
  const legacyM7BuildingYearSources = new Set([
    ...canonicalYearSources,
    "Houston City Directory",
    "COH Landmark Designation Report",
    "HCAD",
  ]);
  for (const feat of buildingsFc.features || []) {
    const ys = String(feat.properties?.year_source || "");
    assert.ok(
      legacyM7BuildingYearSources.has(ys),
      `Non-schema year_source '${ys}' on building ${feat.properties?.id}`
    );
  }
  // 4b. Opposing cardinal E/W regression guard on live Heights Blvd cross-street parcels
  for (const [wrongHcad, wrongSid] of [
    ["0201960000011", "s2925"],
    ["0201820000029", "s2934"],
    ["0201790000034", "s2966"],
    ["0201270000049", "s2996"],
    ["0201960000001", "s3033"],
    ["1427070010001", "s3800"],
  ]) {
    assert.equal(
      (hpo.by_hcad[wrongHcad] || []).includes(wrongSid),
      false,
      `Opposing E/W parcel ${wrongHcad} must never link to ${wrongSid} (${hpo.items[wrongSid]?.a})`
    );
  }

  // 4c. Post-1965 modern replacement & accessory structure style guard (e.g. bld_007619 on 0530380000007 built 2008, 0370290000022 built 2003)
  const bld007619 = (buildingsFc.features || []).find(
    (f) => f.properties?.id === "bld_007619" && f.properties?.hcad_num === "0530380000007"
  );
  assert.ok(bld007619, "Expected bld_007619 (2008 structure on 0530380000007 / 1611 SOUTH BLVD) in buildings.geojson");
  assert.equal(bld007619.properties.year_built, 2008);
  assert.equal(bld007619.properties.is_curated_override, true);
  assert.notEqual(
    bld007619.properties.style,
    "English Picturesque",
    "2008 structure bld_007619 on 0530380000007 (1611 SOUTH BLVD) must never inherit 1925 English Picturesque style"
  );
  const bld007619Survey = resolveHpoParcelSurvey(hpo, bld007619.properties);
  assert.ok(bld007619Survey, "Expected HPO parcel survey match on 0530380000007");
  assert.equal(bld007619Survey.isReplacedStructure, true);
  assert.equal(bld007619Survey.usableStyle, "");

  const hawthorne2003 = (buildingsFc.features || []).find(
    (f) => f.properties?.id === "0370290000022"
  );
  if (hawthorne2003) {
    assert.notEqual(
      hawthorne2003.properties.style,
      "Queen Anne",
      "2003 garage footprint on 0370290000022 (428 HAWTHORNE ST) must never inherit 1915 Queen Anne style"
    );
  }
  for (const [hcKey, ovRec] of Object.entries(overrides)) {
    const ovYr = Number(ovRec?.year_built) || 0;
    if (ovYr <= 1965) continue;
    for (const sid of hpo.by_hcad[hcKey] || []) {
      const it = hpo.items[sid];
      if (it && Number(it.y) > 0 && ovYr > Number(it.y) + 25 && it.sy) {
        assert.notEqual(
          ovRec.bld_style,
          it.sy,
          `Post-1965 override ${hcKey} (built ${ovYr}) must not inherit ${it.y} survey style ${it.sy}`
        );
      }
    }
  }

  // 4d. Multi-footprint & Non-Contributing secondary garage year-promotion guard
  for (const multiBldHcad of [
    "0202000000016",
    "0370290000025",
    "0370290000021",
    "0370290000014",
    "0370290000023",
    "0370300000010",
  ]) {
    assert.equal(
      multiBldHcad in overrides,
      false,
      `Multi-building parcel ${multiBldHcad} with post-war or Non-Contributing secondary structure must not receive parcel-wide year override`
    );
  }
  const rutlandModern = (buildingsFc.features || []).find(
    (f) => f.properties?.id === "0202000000016"
  );
  if (rutlandModern) {
    assert.equal(
      rutlandModern.properties.year_built,
      2019,
      "2019 secondary structure on 0202000000016 (1109 Rutland St) must retain year_built=2019"
    );
  }
  const printShopFeat = (buildingsFc.features || []).find(
    (f) => f.properties?.id === "0210200000029#modern_print_shop"
  );
  if (printShopFeat) {
    assert.notEqual(
      printShopFeat.properties.style,
      "Queen Anne",
      "1927 commercial print shop sub-building must not inherit parent parcel's Queen Anne style"
    );
  }

  const searchIdx = JSON.parse(rawSearchIdx);
  const shpEntries = searchIdx.filter((e) => String(e.id || "").startsWith("0400030000014#shp_"));
  assert.ok(shpEntries.length >= 10, "Expected Sam Houston Park #shp_* entries in search_index.json");
  assert.equal(
    shpEntries.every((e) => e.style === "Colonial Revival"),
    false,
    "Sam Houston Park #shp_* museum structures must not all be stamped Colonial Revival"
  );
  const printShopIdx = searchIdx.find((e) => e.id === "0210200000029#modern_print_shop");
  if (printShopIdx) {
    assert.notEqual(printShopIdx.style, "Queen Anne");
  }
  const m12Entries = Object.entries(overrides).filter(
    ([, rec]) => rec && typeof rec === "object" && rec.verified_at === "2026-11-09"
  );
  assert.ok(m12Entries.length >= 105, `Expected >= 105 Milestone 12 curated overrides, got ${m12Entries.length}`);
  const citydirByHcad =
    JSON.parse(fs.readFileSync(path.join(DATA_DIR, "citydir_index.json"), "utf8")).by_hcad || {};
  for (const [hc, rec] of m12Entries) {
    if (rec.source_type === "COH HPO Historic District Survey") {
      const cdE = Number(citydirByHcad[hc]?.e) || 0;
      const ovYr = Number(rec.year_built) || 0;
      assert.equal(
        cdE >= 1866 && cdE < ovYr,
        false,
        `HPO survey override ${hc} (year_built=${ovYr}) must not post-date 1866-1926 City Directory occupant (${cdE})`
      );
    }
  }

  const byPfx = new Map();
  for (const [hc, rec] of m12Entries) {
    for (const forbiddenProp of ["owner", "owner_name", "mailto", "Owner_Mailto", "Mail_Addr_1"]) {
      assert.equal(forbiddenProp in rec, false, `Override ${hc} leaked forbidden property ${forbiddenProp}`);
    }
    const pfx = hc.slice(0, 3);
    if (!byPfx.has(pfx)) byPfx.set(pfx, []);
    byPfx.get(pfx).push([hc, rec]);
  }
  for (const [pfx, entries] of byPfx.entries()) {
    const gzPath = path.join(DATA_DIR, "permits", `permits_${pfx}.json.gz`);
    if (!fs.existsSync(gzPath)) continue;
    const shard = JSON.parse(zlib.gunzipSync(fs.readFileSync(gzPath)).toString("utf8"));
    for (const [hc, rec] of entries) {
      if (shard[hc]?.conf) {
        assert.equal("port_id" in rec, false, `Confidential account ${hc} must never carry port_id`);
        assert.ok(
          ["homestead", "anonymity_shield", "civic_nonprofit"].includes(rec.own_tier),
          `Confidential account ${hc} must use shielded/homestead own_tier, got ${rec.own_tier}`
        );
      }
    }
  }
});
