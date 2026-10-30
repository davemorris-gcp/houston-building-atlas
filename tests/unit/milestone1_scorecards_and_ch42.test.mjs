import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  CH42_BLOCKFACE_ROLE_PROP,
  COH_COUNCIL_ROSTER_2024_2028,
  ORGANIZER_CACHE_BUST,
  SCORECARD_FAMILIES,
  VALID_SCORECARD_LAYERS,
  assembleBlockfaceFromSeedParcel,
  axialAngleDiffDeg,
  buildChapter42BlockfaceGeoJson,
  classifyBlockfaceOwnerType,
  classifyParcelForChapter42,
  computeChapter42BlockEligibility,
  computeHoustonParcelAreaSqFt,
  computeLiveBoundaryThreats,
  computePolygonAreaSqftPlanar,
  decodeScorecardRecord,
  escapeOrganizerHtml,
  estimateParcelFrontSetbackFt,
  getHoustonSurveyFeetScale,
  loadNeighborhoodScorecards,
  matchCh42OverlaysToBlockface,
  normalizeBlockfaceParcel,
  normalizeThreatAddressKey,
  parseStreetAddressParts,
  renderBoundaryInlineScorecardHtml,
  renderChapter42ModalHtml,
  renderScorecardDetailHtml,
  renderScorecardHubTableRowsHtml,
  resetNeighborhoodScorecardsCache,
  resolveCh42OrdinanceLifecycle,
} from "../../app/js/preservationOrganizer.js";
import {
  buildShareableUrl,
  createFilterStore,
  parseHashToState,
  serializeStateToHash,
  toLayerStateKey,
  toOverlayDataKey,
} from "../../app/js/filterStore.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const SCORECARDS_PATH = path.join(
  REPO_ROOT,
  "app",
  "public",
  "data",
  "neighborhood_scorecards.json"
);

/**
 * Build a rectangular GeoJSON Polygon in WGS84 degrees sized to exact US Survey Feet
 * at a Houston reference coordinate so geometric tests are physically exact and readable.
 */
function makeRectPolygonFt(originLng, originLat, xMinFt, yMinFt, widthFt, depthFt) {
  const { ftPerDegLng, ftPerDegLat } = getHoustonSurveyFeetScale(originLat);
  const toLngLat = (xFt, yFt) => [
    Number((originLng + xFt / ftPerDegLng).toFixed(8)),
    Number((originLat + yFt / ftPerDegLat).toFixed(8)),
  ];
  return {
    type: "Polygon",
    coordinates: [
      [
        toLngLat(xMinFt, yMinFt),
        toLngLat(xMinFt + widthFt, yMinFt),
        toLngLat(xMinFt + widthFt, yMinFt + depthFt),
        toLngLat(xMinFt, yMinFt + depthFt),
        toLngLat(xMinFt, yMinFt),
      ],
    ],
  };
}

/**
 * Helper to manufacture an HCAD ArcGIS Feature matching public_query/MapServer/0.
 */
function makeHcadParcelFeature({
  index = 0,
  hcadNum = "",
  address = "",
  owner = "DOE JANE",
  subdivision = "NORHILL",
  stateClass = "A1",
  yrImpr = 1925,
  imprVal = 185000,
  landVal = 240000,
  shapeStArea = null,
  legalLines = "LT 1 BLK 4 NORHILL",
  geometry = null,
} = {}) {
  const acct =
    hcadNum || `061001001${String(index + 1).padStart(4, "0")}`;
  const geom =
    geometry ||
    makeRectPolygonFt(-95.385, 29.792, index * 50, 0, 50, 100);
  const props = {
    HCAD_NUM: acct,
    address: address || `${1102 + index * 2} PEDEN ST`,
    owner,
    subdivision,
    state_class: stateClass,
    yr_impr: yrImpr,
    impr_val: imprVal,
    land_val: landVal,
    appr_val: Number(imprVal || 0) + Number(landVal || 0),
    mkt_val: Number(imprVal || 0) + Number(landVal || 0),
    legal_lines: legalLines,
  };
  if (shapeStArea !== null && shapeStArea !== undefined) {
    props["Shape.STArea()"] = shapeStArea;
  }
  return {
    type: "Feature",
    properties: props,
    geometry: geom,
  };
}

function makeAssembledBlockfaceItem(parcelOpts = {}, itemOverrides = {}) {
  const feat = makeHcadParcelFeature(parcelOpts);
  const normOpts = { ...(itemOverrides.normalizeOpts || {}) };
  if (itemOverrides.footprints && !normOpts.buildingFootprints) {
    normOpts.buildingFootprints = itemOverrides.footprints;
  }
  const norm = normalizeBlockfaceParcel(feat, normOpts);
  const sb = estimateParcelFrontSetbackFt(norm, {
    bearingDeg: 0,
    frontNormal: [0, -1],
  });
  return {
    ...norm,
    frontageRole: itemOverrides.frontageRole || "interior",
    included: itemOverrides.included !== false,
    estimatedSetbackFt: sb.setbackFt,
    setbackFt: sb.setbackFt,
    setbackSource: sb.setbackSource,
    frontEdgeSegment: sb.frontEdgeSegment || feat.geometry.coordinates[0].slice(0, 2),
    isSeed: Boolean(itemOverrides.isSeed),
    ...itemOverrides,
  };
}

// ============================================================================
// UT-JS-1: Chapter 42 Statutory Cutoffs, Scaled-Integer Exactness & Fast-Track
// ============================================================================

test("UT-JS-1a: exact scaled-integer 70% vs 60% SMLS threshold boundary and IEEE-754 float trap immunity", () => {
  // 10 lots with areas [8500, 8000, 7500, 7000, 6500, 6000, 5500, 5000, 4500, 4000] summing to 62,500 sq ft:
  // - Top 5 sum = 37,500 = EXACT 60.00% of 62,500 (cutoff inside COH Historic District = 6500 sq ft)
  // - Top 7 sum = 49,000 = 78.40% >= 70% outside Historic District (top 6 = 43,500 = 69.60% < 70%, so cutoff = 5500 sq ft)
  const makeMix = (sfrCount, totalCount = 10) =>
    Array.from({ length: totalCount }, (_, i) => {
      const areaSqft = 4000 + i * 500;
      const isSfr = i < sfrCount;
      return makeAssembledBlockfaceItem({
        index: i,
        stateClass: isSfr ? "A1" : "B1",
        owner: isSfr ? `HOMEOWNER ${i}` : `APARTMENTS ${i}`,
        shapeStArea: areaSqft,
        geometry: makeRectPolygonFt(-95.39, 29.745, i * 60, 0, areaSqft / 100, 100),
      });
    });

  // 7 of 10 SFR outside COH Historic District -> 70.0% SFR >= 60% block gate; SMLS threshold = 70%
  const res7of10Outside = computeChapter42BlockEligibility(makeMix(7, 10), {
    historicDistrict: "",
  });
  assert.equal(res7of10Outside.isHistoricDistrict, false);
  assert.equal(res7of10Outside.thresholdPct, 70);
  assert.equal(res7of10Outside.eligibilityCheck.sfrCount, 7);
  assert.equal(res7of10Outside.eligibilityCheck.denominatorCount, 10);
  assert.equal(res7of10Outside.eligibilityCheck.passesBlock60Pct, true);
  assert.equal(res7of10Outside.eligibilityCheck.passesArea80Pct, false);
  assert.equal(res7of10Outside.smlsResult.effectiveMinLotSqft, 5500);

  // Exact 6 of 10 SFR = exact 60.00% boundary on Sec. 42-197(b)(4) block gate; 5 of 10 = 50% fails; 8 of 10 = exact 80% Area gate
  const res6of10Outside = computeChapter42BlockEligibility(makeMix(6, 10));
  assert.equal(res6of10Outside.eligibilityCheck.passesBlock60Pct, true);
  assert.equal(res6of10Outside.eligibilityCheck.passesArea80Pct, false);

  const res5of10Outside = computeChapter42BlockEligibility(makeMix(5, 10));
  assert.equal(res5of10Outside.eligibilityCheck.passesBlock60Pct, false);

  const res8of10Outside = computeChapter42BlockEligibility(makeMix(8, 10));
  assert.equal(res8of10Outside.eligibilityCheck.passesArea80Pct, true);

  // Inside COH Historic District: exact 60.00% lot-size cutoff hits index 4 (6,500 sq ft) because 37500 * 100 === 60 * 62500!
  // Mutating >= to > would drop effectiveMinLotSqft from 6500 down to 6000!
  const resInsideHd = computeChapter42BlockEligibility(makeMix(6, 10), {
    historicDistrict: "Norhill Historic District",
  });
  assert.equal(resInsideHd.isHistoricDistrict, true);
  assert.equal(resInsideHd.thresholdPct, 60);
  assert.equal(resInsideHd.smlsResult.effectiveMinLotSqft, 6500);
  assert.equal(resInsideHd.smlsResult.cutIndex, 4);

  // Exact IEEE-754 float-accumulation trap witness for 70.00% SMLS AND SMBL:
  // Areas [2320, 1810, 1800, 1790, 1780, 1770, 1230, 1220, 1210, 1170] sum to 16,100 sq ft.
  // Top 6 sum to 11,270 sq ft (11270 * 100 === 70 * 16100 = exact 70.00%).
  // In IEEE-754 floating point: 2320/16100 + 1810/16100 + 1800/16100 + 1790/16100 + 1780/16100 + 1770/16100
  // evaluates to 0.6999999999999998 (< 0.70)!
  // Exact scaled-integer cross-multiplication MUST lock cutoff at Lot #6 (1,770 sq ft / 17.7 ft setback), NEVER Lot #7 (1,230 sq ft / 12.3 ft)!
  const trapAreas = [2320, 1810, 1800, 1790, 1780, 1770, 1230, 1220, 1210, 1170];
  const trapItems = trapAreas.map((areaSqft, i) =>
    makeAssembledBlockfaceItem(
      {
        index: i,
        hcadNum: `078001001${String(i + 1).padStart(4, "0")}`,
        stateClass: "A1",
        shapeStArea: areaSqft,
      },
      {
        manualSetbackFt: areaSqft / 100, // [23.2, 18.1, 18.0, 17.9, 17.8, 17.7, 12.3, ...] ft
      }
    )
  );
  const trapRes = computeChapter42BlockEligibility(trapItems, { historicDistrict: "" });
  assert.equal(trapRes.smlsResult.totalAreaSqft, 16100);
  assert.equal(trapRes.smlsResult.effectiveMinLotSqft, 1770);
  assert.equal(trapRes.smlsResult.cutIndex, 5);
  assert.equal(trapRes.smblResult.effectiveMinSetbackFt, 17.7);
  assert.equal(trapRes.smblResult.cutIndex, 5);

  // Exact 51.00% boundary check on Sec. 42-200(e) Direct-to-Council fast track (5100 / 10000 sq ft vs 5000 / 10000 sq ft)
  const hundredItems = Array.from({ length: 100 }, (_, i) =>
    makeAssembledBlockfaceItem({
      index: i,
      hcadNum: `077001001${String(i + 1).padStart(4, "0")}`,
      stateClass: i < 60 ? "A1" : "B1",
      shapeStArea: 100,
    })
  );
  const signed51 = {};
  for (let i = 0; i < 51; i += 1) signed51[hundredItems[i].hcadNum] = true;
  const resExact51 = computeChapter42BlockEligibility(hundredItems, {
    supportSignedByHcad: signed51,
  });
  assert.equal(resExact51.petitionThresholds.meetsDirectToCouncilFastTrack, true);
  assert.equal(resExact51.petitionThresholds.remainingFastTrackSqft, 0);

  const signed50 = { ...signed51 };
  delete signed50[hundredItems[50].hcadNum];
  const resExact50 = computeChapter42BlockEligibility(hundredItems, {
    supportSignedByHcad: signed50,
  });
  assert.equal(resExact50.petitionThresholds.meetsDirectToCouncilFastTrack, false);
  assert.equal(resExact50.petitionThresholds.remainingFastTrackSqft, 100);
});

test("UT-JS-1b: Sec. 42-197(b)(4) exclusions, vacant residential vs commercial, Sec. 42-173 SMBL standing-structure rule, and area-weighted 51% fast-track vs lot count", () => {
  const originLng = -95.392;
  const originLat = 29.748;

  const fpAtSetback = (xMinFt, widthFt, setbackFt) => [
    {
      type: "Feature",
      geometry: makeRectPolygonFt(
        originLng,
        originLat,
        xMinFt + 2,
        setbackFt,
        Math.max(15, widthFt - 4),
        35
      ),
      properties: {},
    },
  ];

  // 0: Large historic SFR home on 6,000 sq ft lot (setback 25 ft)
  const lot0 = makeAssembledBlockfaceItem(
    {
      index: 0,
      hcadNum: "0620000000001",
      owner: "ALICE PRESERVATIONIST",
      stateClass: "A1",
      yrImpr: 1922,
      imprVal: 220000,
      shapeStArea: 6000,
      geometry: makeRectPolygonFt(originLng, originLat, 0, 0, 60, 100),
    },
    { footprints: fpAtSetback(0, 60, 25), isSeed: true }
  );
  // 1..3: Three narrow townhome SFR lots on 1,500 sq ft each (setbacks 15, 18, 20 ft)
  const th1 = makeAssembledBlockfaceItem(
    {
      index: 1,
      hcadNum: "0620000000002",
      owner: "SMITH FAMILY LIVING TRUST",
      stateClass: "A1",
      yrImpr: 2019,
      imprVal: 350000,
      shapeStArea: 1500,
      geometry: makeRectPolygonFt(originLng, originLat, 60, 0, 20, 100),
    },
    { footprints: fpAtSetback(60, 20, 15) }
  );
  const th2 = makeAssembledBlockfaceItem(
    {
      index: 2,
      hcadNum: "0620000000003",
      owner: "BUYER TWO",
      stateClass: "A1",
      yrImpr: 2019,
      imprVal: 350000,
      shapeStArea: 1500,
      geometry: makeRectPolygonFt(originLng, originLat, 80, 0, 20, 100),
    },
    { footprints: fpAtSetback(80, 20, 18) }
  );
  const th3 = makeAssembledBlockfaceItem(
    {
      index: 3,
      hcadNum: "0620000000004",
      owner: "METRO INFILL HOMES LLC",
      stateClass: "A1",
      yrImpr: 2019,
      imprVal: 350000,
      shapeStArea: 1500,
      geometry: makeRectPolygonFt(originLng, originLat, 100, 0, 20, 100),
    },
    { footprints: fpAtSetback(100, 20, 20) }
  );
  // 4: Suppressed demolished vacant residential lot (suppress_only: true, impr_val=0, year_built=0, 5,000 sq ft)
  const vacDemo = makeAssembledBlockfaceItem(
    {
      index: 4,
      hcadNum: "0620000000005",
      owner: "TEARDOWN BUILDERS LLC",
      stateClass: "C1",
      yrImpr: 0,
      imprVal: 0,
      shapeStArea: 5000,
      geometry: makeRectPolygonFt(originLng, originLat, 120, 0, 50, 100),
    },
    {
      normalizeOpts: {
        curatedOverrides: { "0620000000005": { hcad_num: "0620000000005", suppress_only: true } },
      },
      footprints: [],
    }
  );
  // 5: Tax-exempt historic church with standing sanctuary (state_class=X3, impr_val=0, footprint present, setback 30 ft)
  const church = makeAssembledBlockfaceItem(
    {
      index: 5,
      hcadNum: "0620000000006",
      owner: "GRACE LUTHERAN CHURCH",
      stateClass: "X3",
      yrImpr: 1928,
      imprVal: 0,
      shapeStArea: 5000,
      geometry: makeRectPolygonFt(originLng, originLat, 170, 0, 50, 100),
    },
    {
      normalizeOpts: { buildingSummary: { year_built: 1928 } },
      footprints: fpAtSetback(170, 50, 30),
    }
  );
  // 6: City of Houston governmental/utility pump station lot (excluded from ALL 4 universes)
  const govPump = makeAssembledBlockfaceItem(
    {
      index: 6,
      hcadNum: "0620000000007",
      owner: "CITY OF HOUSTON PUBLIC WORKS LIFT STATION",
      stateClass: "XV",
      yrImpr: 0,
      imprVal: 0,
      shapeStArea: 4000,
      geometry: makeRectPolygonFt(originLng, originLat, 220, 0, 40, 100),
    },
    { footprints: [] }
  );
  // 7: Vacant commercial lot (state_class=C2, 4,000 sq ft)
  const vacComm = makeAssembledBlockfaceItem(
    {
      index: 7,
      hcadNum: "0620000000008",
      owner: "CORNER RETAIL HOLDINGS LP",
      stateClass: "C2",
      yrImpr: 0,
      imprVal: 0,
      shapeStArea: 4000,
      geometry: makeRectPolygonFt(originLng, originLat, 260, 0, 40, 100),
    },
    { footprints: [] }
  );

  // Verify individual Sec. 42-197(b)(4) / DF-6 parcel classification invariants
  assert.equal(classifyBlockfaceOwnerType(church.owner, church.stateClass), "individual");
  assert.equal(classifyBlockfaceOwnerType(govPump.owner, govPump.stateClass), "public_exempt");
  assert.equal(classifyBlockfaceOwnerType(th1.owner, th1.stateClass), "family_trust");
  assert.equal(classifyBlockfaceOwnerType(th3.owner, th3.stateClass), "corporate");

  const clsChurch = classifyParcelForChapter42(church);
  assert.equal(clsChurch.landUseCategory, "excluded_civic_religious");
  assert.equal(clsChurch.inSfrDenominator, false);
  assert.equal(church.hasStandingStructure, true);
  assert.equal(church.isVacant, false);
  assert.equal(clsChurch.inSmblUniverse, true);

  const clsGov = classifyParcelForChapter42(govPump);
  assert.equal(clsGov.landUseCategory, "utility_governmental");
  assert.equal(clsGov.inSfrDenominator, false);
  assert.equal(clsGov.inSmlsUniverse, false);
  assert.equal(clsGov.inSmblUniverse, false);
  assert.equal(clsGov.inPetitionAreaUniverse, false);

  const clsVacComm = classifyParcelForChapter42(vacComm);
  assert.equal(clsVacComm.landUseCategory, "vacant_other");
  assert.equal(clsVacComm.inSfrDenominator, true);
  assert.equal(clsVacComm.isSfrNumerator, false);

  // Vacant parcels MUST exclude inSmblUniverse AND return setbackFt: null (NEVER 0 ft!)
  assert.equal(classifyParcelForChapter42(vacDemo).inSmblUniverse, false);
  const vacSb = estimateParcelFrontSetbackFt(vacDemo);
  assert.equal(vacSb.setbackFt, null);
  assert.equal(vacSb.setbackSource, "unavailable");

  // A recently suppressed demolished lot carrying stale pre-suppression classification + setbackFt
  // MUST STILL be blocked by Sec. 42-173 defense-in-depth (!isVacant && !isSuppressedVacant && hasStandingStructure)
  const staleSuppressedLot = {
    ...makeAssembledBlockfaceItem({
      index: 8,
      hcadNum: "0620000000009",
      owner: "STALE ROLL OWNER",
      stateClass: "A1",
      yrImpr: 1925,
      imprVal: 150000,
      shapeStArea: 5000,
      geometry: makeRectPolygonFt(originLng, originLat, 300, 0, 50, 100),
    }),
    isSuppressedVacant: true,
    isVacant: true,
    hasStandingStructure: false,
    vacantWasSfrPast5Years: false,
    setbackFt: 11.0,
    setbackSource: "footprint_estimate",
    classification: {
      landUseCategory: "sfr",
      landUseLabel: "Single-Family Residential",
      inSfrDenominator: true,
      isSfrNumerator: true,
      inSmlsUniverse: true,
      inSmblUniverse: true,
      inPetitionAreaUniverse: true,
      warnings: [],
    },
  };

  const calc = computeChapter42BlockEligibility(
    [lot0, th1, th2, th3, vacDemo, church, govPump, vacComm, staleSuppressedLot],
    {
      historicDistrict: "",
      vacantWasSfrByHcad: { "0620000000005": true },
      manualSetbacksByHcad: { "0620000000002": 16.5, "0620000000005": 10.0 },
      supportSignedByHcad: {
        "0620000000001": true,
        "0620000000005": true,
        "0620000000006": true,
      },
    }
  );

  // SFR test: church and govPump excluded from denominator; 6 SFR out of 7 remaining lots (incl. staleSuppressedLot cached cls)
  assert.equal(calc.eligibilityCheck.sfrCount, 6);
  assert.equal(calc.eligibilityCheck.denominatorCount, 7);
  assert.equal(calc.eligibilityCheck.excludedCivicReligiousCount, 2);
  assert.equal(calc.eligibilityCheck.passesBlock60Pct, true);

  // SMBL universe MUST strictly contain ONLY the 5 developed standing-structure lots (lot0, th1, th2, th3, church)
  // and exclude vacDemo (even with accidental manual setback 10.0 ft), vacComm, staleSuppressedLot, and govPump!
  assert.equal(calc.smblResult.constructedLotCount, 5);
  assert.equal(calc.smblResult.excludedVacantOrDemolishedCount, 4);
  const smblHcads = calc.smblResult.rankedRows.map((r) => r.hcadNum).sort();
  assert.deepEqual(smblHcads, [
    "0620000000001",
    "0620000000002",
    "0620000000003",
    "0620000000004",
    "0620000000006",
  ]);
  const th1Item = calc.items.find((r) => r.hcadNum === "0620000000002");
  assert.equal(th1Item.effectiveSetbackFt, 16.5);
  assert.equal(th1Item.setbackSource, "manual_override");

  // Area-weighted 51% fast-track vs lot-count distinction (Sec. 42-200(e)):
  // Case A: 1 large 6,000 sq ft lot signed vs 3 small 1,500 sq ft lots unsigned -> 6000 / 10500 = 57.1% area (only 25% of lots!) -> TRUE!
  const areaOverCount = computeChapter42BlockEligibility([lot0, th1, th2, th3], {
    supportSignedByHcad: { "0620000000001": true },
  });
  assert.equal(areaOverCount.petitionThresholds.meetsDirectToCouncilFastTrack, true);
  assert.equal(areaOverCount.petitionThresholds.signedLandAreaPct, 57.1);
  assert.deepEqual(areaOverCount.petitionThresholds.ownershipBreakdown, {
    individualOwners: 2,
    familyTrustOwners: 1,
    corporateOwners: 1,
  });

  // Case B: 3 small 1,500 sq ft lots signed (4,500 sq ft, 75% of lot count!) vs 1 large 6,000 sq ft lot unsigned -> 42.9% area -> FALSE!
  const countUnderArea = computeChapter42BlockEligibility([lot0, th1, th2, th3], {
    supportSignedByHcad: {
      "0620000000002": true,
      "0620000000003": true,
      "0620000000004": true,
    },
  });
  assert.equal(countUnderArea.petitionThresholds.meetsDirectToCouncilFastTrack, false);
  assert.equal(countUnderArea.petitionThresholds.signedLandAreaPct, 42.9);

  // Degenerate all-exempt blockface never emits NaN or Infinity
  const allExempt = computeChapter42BlockEligibility([govPump]);
  assert.equal(allExempt.activeLotCount, 1);
  assert.equal(allExempt.eligibilityCheck.denominatorCount, 0);
  assert.equal(allExempt.eligibilityCheck.sfrPct, 0);
  assert.equal(allExempt.eligibilityCheck.passesBlock60Pct, false);
  assert.equal(allExempt.smlsResult.effectiveMinLotSqft, null);
  assert.equal(allExempt.smblResult.effectiveMinSetbackFt, null);
});

// ============================================================================
// UT-JS-2: Multi-Account Condo Collapsing, Shared-Driveway Reserves & Overrides
// ============================================================================

test("UT-JS-2: 20-unit stacked condo collapsing, zero-addressed shared-driveway reserve exclusion, and curated override merging", () => {
  const originLng = -95.394;
  const originLat = 29.744;

  const seedFeat = makeHcadParcelFeature({
    index: 0,
    hcadNum: "0630000000001",
    address: "1402 FAIRVIEW ST",
    owner: "HISTORIC BUNGALOW OWNER",
    stateClass: "A1",
    yrImpr: 1924,
    geometry: makeRectPolygonFt(originLng, originLat, 0, 0, 50, 100),
  });
  const sfr2 = makeHcadParcelFeature({
    index: 1,
    hcadNum: "0630000000002",
    address: "1406 FAIRVIEW ST",
    owner: "BUNGALOW TWO",
    stateClass: "A1",
    yrImpr: 1926,
    geometry: makeRectPolygonFt(originLng, originLat, 50, 0, 50, 100),
  });

  // 20 stacked condominium accounts sharing the exact same outer ring polygon
  const sharedCondoGeom = makeRectPolygonFt(originLng, originLat, 100, 0, 100, 100);
  const condoUnits = Array.from({ length: 20 }, (_, u) =>
    makeHcadParcelFeature({
      index: 10 + u,
      hcadNum: `063000009${String(u + 1).padStart(4, "0")}`,
      address: `1410 FAIRVIEW ST UNIT ${101 + u}`,
      owner: `CONDO OWNER ${u + 1}`,
      stateClass: "A4",
      yrImpr: 2016,
      imprVal: 295000,
      legalLines: `UNIT ${101 + u} BLDG A FAIRVIEW CONDOS`,
      geometry: JSON.parse(JSON.stringify(sharedCondoGeom)),
    })
  );

  // Zero-addressed shared-driveway PAE reserve sliver (6 ft x 100 ft = 600 sq ft)
  const drivewayReserve = makeHcadParcelFeature({
    index: 40,
    hcadNum: "0630000000099",
    address: "0 FAIRVIEW ST",
    owner: "FAIRVIEW TOWNHOMES HOA INC",
    stateClass: "C1",
    yrImpr: 0,
    imprVal: 0,
    legalLines: "RESERVE A BLK 1 SHARED DRIVEWAY PAE",
    geometry: makeRectPolygonFt(originLng, originLat, 200, 0, 6, 100),
  });

  // Suppressed recent demolition lot whose stale HCAD roll still says impr_val=120000
  const demoLot = makeHcadParcelFeature({
    index: 41,
    hcadNum: "0630000000050",
    address: "1420 FAIRVIEW ST",
    owner: "INFILL HOLDINGS LLC",
    stateClass: "A1",
    yrImpr: 1920,
    imprVal: 120000,
    geometry: makeRectPolygonFt(originLng, originLat, 206, 0, 50, 100),
  });

  const assembly = assembleBlockfaceFromSeedParcel(
    seedFeat,
    [seedFeat, sfr2, ...condoUnits, drivewayReserve, demoLot],
    {
      curatedOverrides: {
        "0630000000050": { hcad_num: "0630000000050", suppress_only: true },
        "0630000000002#rear_garage_apt": {
          hcad_num: "0630000000002",
          is_building_override: true,
          year_built: 1927,
        },
      },
      footprintsByHcad: new Map([
        [
          "0630000000001",
          [{ type: "Feature", geometry: makeRectPolygonFt(originLng, originLat, 8, 22, 34, 40) }],
        ],
        [
          "0630000000002",
          [{ type: "Feature", geometry: makeRectPolygonFt(originLng, originLat, 58, 24, 34, 40) }],
        ],
        [
          condoUnits[0].properties.HCAD_NUM,
          [{ type: "Feature", geometry: makeRectPolygonFt(originLng, originLat, 105, 12, 90, 70) }],
        ],
      ]),
    }
  );

  // 20 condo units MUST collapse into 1 parent parcel record (total = 5 items: seed, sfr2, 1 condo, 1 reserve, 1 demoLot)
  assert.equal(assembly.items.length, 5);
  const condoParent = assembly.items.find((it) => it.condoUnitCount > 1);
  assert.ok(condoParent, "Expected collapsed condominium parent parcel");
  assert.equal(condoParent.condoUnitCount, 20);
  assert.equal(condoParent.collapsedHcads.length, 20);
  // Area must be ~10,000 sq ft (100x100), NEVER 20x (200,000 sq ft)!
  assert.ok(condoParent.lotAreaSqft > 9950 && condoParent.lotAreaSqft < 10050);

  const reserveItem = assembly.items.find((it) => it.hcadNum === "0630000000099");
  assert.equal(reserveItem.included, false);
  assert.equal(reserveItem.frontageRole, "common_reserve");

  const demoItem = assembly.items.find((it) => it.hcadNum === "0630000000050");
  assert.equal(demoItem.isSuppressedVacant, true);
  assert.equal(demoItem.isVacant, true);
  assert.equal(demoItem.hasStandingStructure, false);

  const calc = computeChapter42BlockEligibility(assembly.items);
  assert.equal(calc.segregatedReserveCount, 1);
  const condoRow = calc.items.find((r) => r.hcadNum === condoParent.hcadNum);
  assert.equal(condoRow.classification.landUseCategory, "multifamily");
  assert.equal(condoRow.classification.inSfrDenominator, true);
  assert.equal(condoRow.classification.isSfrNumerator, false);

  assert.equal(CH42_BLOCKFACE_ROLE_PROP, "ch42_role");
  const geojson = buildChapter42BlockfaceGeoJson(assembly, calc);
  assert.equal(geojson.features.length, 5);
  for (const gf of geojson.features) {
    assert.equal(typeof gf.properties[CH42_BLOCKFACE_ROLE_PROP], "string");
    assert.equal(gf.properties[CH42_BLOCKFACE_ROLE_PROP], gf.properties.role);
  }
  assert.equal(
    geojson.features.find((f) => f.properties.hcad_num === "0630000000099")?.properties[
      CH42_BLOCKFACE_ROLE_PROP
    ],
    "common_reserve"
  );
  // Regression guard against MapLibre paint property schema drift in mapController.js
  const mapCtrlSrc = fs.readFileSync(
    path.join(REPO_ROOT, "app", "js", "mapController.js"),
    "utf8"
  );
  assert.equal(
    mapCtrlSrc.includes('["get", CH42_BLOCKFACE_ROLE_PROP]'),
    true,
    "mapController.js must reference shared CH42_BLOCKFACE_ROLE_PROP for ch42-blockface layers"
  );
});

// ============================================================================
// UT-JS-3: Chapter 42 Ordinance Lifecycle & Sunset Normalization (DF-3)
// ============================================================================

test("UT-JS-3: resolveCh42OrdinanceLifecycle enforces July 24, 2015 Ord. 2015-639 20-year vs 40-year sunset boundary", () => {
  // 1. Pre-July 24, 2015 ordinance with null Expiration_Date -> 20-year sunset derived from ordinance year
  const pre2015Null = resolveCh42OrdinanceLifecycle(
    {
      protection_type: "smls",
      ordinance: "2008-0412",
      status: "Approved",
      effective_date: null,
      expiration_date: null,
    },
    2026
  );
  assert.equal(pre2015Null.statutory_term_years, 20);
  assert.equal(pre2015Null.expiration_year, 2028);
  assert.equal(pre2015Null.expiration_date, "2028-12-31");
  assert.equal(pre2015Null.is_estimated_sunset, true);
  assert.equal(pre2015Null.isDerivedFromOrdinanceNumber, true);
  assert.equal(pre2015Null.sunset_status, "expiring_soon");

  // 2. Exact calendar day before Ord. 2015-639 cutoff (2015-07-23 -> 20 yr -> 2035) vs on cutoff (2015-07-24 -> 40 yr -> 2055)
  const dayBefore = resolveCh42OrdinanceLifecycle(
    {
      protection_type: "smbl",
      ordinance: "2015-0638",
      effective_date: "2015-07-23",
      expiration_date: "",
    },
    2026
  );
  assert.equal(dayBefore.statutory_term_years, 20);
  assert.equal(dayBefore.expiration_year, 2035);
  assert.equal(dayBefore.expiration_date, "2035-07-23");
  assert.equal(dayBefore.sunset_status, "active");

  const dayOfCutoff = resolveCh42OrdinanceLifecycle(
    {
      protection_type: "smls",
      ordinance: "2015-0639",
      effective_date: "2015-07-24",
      expiration_date: "",
    },
    2026
  );
  assert.equal(dayOfCutoff.statutory_term_years, 40);
  assert.equal(dayOfCutoff.expiration_year, 2055);
  assert.equal(dayOfCutoff.expiration_date, "2055-07-24");
  assert.equal(dayOfCutoff.sunset_status, "active");

  // 3. Ordinance-number fallback when effective_date is absent in 2015
  const ordBefore = resolveCh42OrdinanceLifecycle({ ordinance: "2015-638" }, 2026);
  const ordAfter = resolveCh42OrdinanceLifecycle({ ordinance: "2015-639" }, 2026);
  assert.equal(ordBefore.expiration_year, 2035);
  assert.equal(ordAfter.expiration_year, 2055);

  // 4. Leap-day enactment date safely normalizes month/day to 02-28 so non-leap sunset years remain valid ISO dates
  const leapEnact = resolveCh42OrdinanceLifecycle(
    { ordinance: "2007-0100", effective_date: "2008-02-29", expiration_date: "" },
    2026
  );
  assert.equal(leapEnact.expiration_date, "2028-02-28");

  // 5. Unrenewed pre-2006 ordinance AND explicit Expired status show expired status
  const expired2004 = resolveCh42OrdinanceLifecycle(
    { ordinance: "2004-0222", expiration_date: null },
    2026
  );
  assert.equal(expired2004.expiration_year, 2024);
  assert.equal(expired2004.sunset_status, "expired");

  const explicitExpired = resolveCh42OrdinanceLifecycle({ status: "Expired" }, 2026);
  assert.equal(explicitExpired.sunset_status, "expired");

  // 6. Pending application, Chapter 33 Conservation District, and Denied/Withdrawn statuses
  for (const pendStr of ["Pending City Council Review", "Application Applied", "Draft Under Review"]) {
    const pendingApp = resolveCh42OrdinanceLifecycle(
      { ordinance: "2026-0001", effective_date: "2026-02-01", status: pendStr },
      2026
    );
    assert.equal(pendingApp.sunset_status, "pending");
    assert.equal(pendingApp.effective_date, "");
    assert.equal(pendingApp.expiration_year, 0);
    assert.equal(pendingApp.statutory_term_years, 40);
  }
  const conservation = resolveCh42OrdinanceLifecycle({ protection_type: "conservation" }, 2026);
  assert.equal(conservation.sunset_status, "permanent");
  assert.equal(conservation.statutory_term_years, 0);
  for (const rejStr of ["Withdrawn by Applicant", "Application Denied", "Ordinance Rescinded"]) {
    const denied = resolveCh42OrdinanceLifecycle({ status: rejStr }, 2026);
    assert.equal(denied.sunset_status, "inactive");
    assert.equal(denied.statutory_term_years, 0);
  }
});

// ============================================================================
// UT-JS-4: Planar Geometry, Block-Face Front-Edge Selection & SMBL Ribbons
// ============================================================================

test("UT-JS-4: US Survey Feet planar area, corner house perpendicular street filtering, opposing side parity, and SMBL ribbon bearing guard", () => {
  const originLng = -95.388;
  const originLat = 29.789;

  // 1. Exact 50 ft x 120 ft rectangular lot = 6,000 sq ft within < 0.05% planar precision
  const rect6000 = makeRectPolygonFt(originLng, originLat, 0, 0, 50, 120);
  const area = computePolygonAreaSqftPlanar(rect6000);
  assert.ok(Math.abs(area - 6000) < 2, `Expected ~6000 sq ft, got ${area}`);

  // HCAD computeHoustonParcelAreaSqFt honors positive ArcGIS Shape.STArea() hint and falls back to planar projection otherwise
  assert.equal(computeHoustonParcelAreaSqFt(rect6000, 6125.5), 6125.5);
  assert.ok(Math.abs(computeHoustonParcelAreaSqFt(rect6000, null) - 6000) < 2);

  // 2. Concave L-shaped building footprint: 40x40 minus 20x20 corner = 1,200 sq ft
  const { ftPerDegLng, ftPerDegLat } = getHoustonSurveyFeetScale(originLat);
  const pt = (x, y) => [originLng + x / ftPerDegLng, originLat + y / ftPerDegLat];
  const lShape = {
    type: "Polygon",
    coordinates: [[pt(0, 0), pt(40, 0), pt(40, 20), pt(20, 20), pt(20, 40), pt(0, 40), pt(0, 0)]],
  };
  assert.ok(Math.abs(computePolygonAreaSqftPlanar(lShape) - 1200) < 2);

  // Polygon with inner courtyard hole subtracts hole area cleanly (100x100 minus 20x20 = 9,600 sq ft)
  const doughnut = {
    type: "Polygon",
    coordinates: [
      [pt(0, 0), pt(100, 0), pt(100, 100), pt(0, 100), pt(0, 0)],
      [pt(40, 40), pt(60, 40), pt(60, 60), pt(40, 60), pt(40, 40)],
    ],
  };
  assert.ok(Math.abs(computePolygonAreaSqftPlanar(doughnut) - 9600) < 3);

  // 3. Blockface assembly along an East-West street corridor (y = -60..0 is 60 ft / 18.3m street ROW):
  // - North side (even house numbers 1102..1106 W 8TH ST, y = 0..100)
  // - South opposing side (odd house number 1103 W 8TH ST, y = -160..-60)
  // - Corner lot sitting at east end of north side addressed to perpendicular N-S side street ("802 STUDEWOOD ST")
  // - Next block across Studewood St ROW > 16m away ("1204 W 8TH ST", x = 230..280)
  const seed = makeHcadParcelFeature({
    index: 0,
    hcadNum: "0640000000001",
    address: "1102 W 8TH ST",
    geometry: makeRectPolygonFt(originLng, originLat, 0, 0, 50, 100),
  });
  const north2 = makeHcadParcelFeature({
    index: 1,
    hcadNum: "0640000000002",
    address: "1106 W 8TH ST",
    geometry: makeRectPolygonFt(originLng, originLat, 50, 0, 50, 100),
  });
  const cornerPerp = makeHcadParcelFeature({
    index: 2,
    hcadNum: "0640000000003",
    address: "802 STUDEWOOD ST",
    geometry: makeRectPolygonFt(originLng, originLat, 100, 0, 50, 100),
  });
  const southOpp1 = makeHcadParcelFeature({
    index: 3,
    hcadNum: "0640000000004",
    address: "1103 W 8TH ST",
    geometry: makeRectPolygonFt(originLng, originLat, 0, -160, 50, 100),
  });
  const nextBlock = makeHcadParcelFeature({
    index: 4,
    hcadNum: "0640000000005",
    address: "1204 W 8TH ST",
    geometry: makeRectPolygonFt(originLng, originLat, 230, 0, 50, 100),
  });
  // Opposing lot across perpendicular Studewood St ROW (x = 230..280, y = -160..-60) MUST NOT bleed into opposing side
  const southOppNextBlock = makeHcadParcelFeature({
    index: 5,
    hcadNum: "0640000000006",
    address: "1203 W 8TH ST",
    geometry: makeRectPolygonFt(originLng, originLat, 230, -160, 50, 100),
  });
  // Parallel back-to-back lot behind the rear fence facing parallel W 7th St (y = 100..200) MUST NOT become a corner_candidate
  const rearFenceParallelLot = makeHcadParcelFeature({
    index: 6,
    hcadNum: "0640000000007",
    address: "1103 W 7TH ST",
    geometry: makeRectPolygonFt(originLng, originLat, 0, 100, 50, 100),
  });

  const candidatePool = [
    seed,
    north2,
    cornerPerp,
    southOpp1,
    nextBlock,
    southOppNextBlock,
    rearFenceParallelLot,
  ];

  const singleSide = assembleBlockfaceFromSeedParcel(seed, candidatePool, {
    includeOpposingBlockface: false,
  });
  const singleHcads = singleSide.items.filter((i) => i.included).map((i) => i.hcadNum);
  assert.deepEqual(singleHcads, ["0640000000001", "0640000000002"]);
  // Corner lot on perpendicular side street is flagged as corner_candidate (opt-in, NOT auto-included),
  // whereas rear-fence parallel neighbor is strictly excluded from assembly items
  const cornerItem = singleSide.items.find((i) => i.hcadNum === "0640000000003");
  assert.equal(cornerItem?.frontageRole, "corner_candidate");
  assert.equal(cornerItem?.included, false);
  assert.equal(singleSide.items.find((i) => i.hcadNum === "0640000000007"), undefined);

  const bothSides = assembleBlockfaceFromSeedParcel(seed, candidatePool, {
    includeOpposingBlockface: true,
  });
  const bothHcads = bothSides.items.filter((i) => i.included).map((i) => i.hcadNum).sort();
  assert.deepEqual(bothHcads, ["0640000000001", "0640000000002", "0640000000004"]);
  assert.equal(bothSides.items.find((i) => i.hcadNum === "0640000000006"), undefined);

  // 4. Unhydrated standing lot gives frontEdgeSegment cleanly; <400 sq ft backyard shed returns setbackFt: null (never poisoning SMBL)
  const unhydratedStanding = normalizeBlockfaceParcel(seed, { buildingFootprints: [] });
  const unhydratedSb = estimateParcelFrontSetbackFt(unhydratedStanding, {
    bearingDeg: 0,
    frontNormal: [0, -1],
  });
  assert.equal(unhydratedSb.setbackFt, null);
  assert.ok(Array.isArray(unhydratedSb.frontEdgeSegment) && unhydratedSb.frontEdgeSegment.length === 2);

  const shedOnlyParcel = normalizeBlockfaceParcel(north2, {
    buildingFootprints: [
      // 18 ft x 18 ft = 324 sq ft (< 400 sq ft threshold) rear shed sitting 75 ft back
      { type: "Feature", geometry: makeRectPolygonFt(originLng, originLat, 60, 75, 18, 18) },
    ],
  });
  const shedSb = estimateParcelFrontSetbackFt(shedOnlyParcel, {
    bearingDeg: 0,
    frontNormal: [0, -1],
  });
  assert.equal(shedSb.setbackFt, null);
  assert.equal(shedSb.setbackSource, "unavailable");
  assert.match(String(shedSb.warnings?.[0] || ""), /400 sq ft/i);

  // 5. SMBL thin centerline ribbon matching (accepts BOTH raw Array AND GeoJSON FeatureCollection):
  // - Parallel 2-ft wide ribbon in W 8th St frontage (y = -18..-16, x = 0..100, 16 ft from front lot line <= 26 ft buffer) MUST match seed + north2
  // - Perpendicular 2-ft wide ribbon in Studewood St cross-street (x = 165..167, y = -80..120) MUST BE REJECTED by bearing guard!
  const protectionFeatures = [
    {
      type: "Feature",
      properties: {
        protection_type: "smbl",
        ordinance: "2009-0555",
        status: "Approved",
        min_ft: 20,
        name: "1100 W 8th St SMBL",
      },
      geometry: makeRectPolygonFt(originLng, originLat, 0, -18, 100, 2),
    },
    {
      type: "Feature",
      properties: {
        protection_type: "smbl",
        ordinance: "2011-0999",
        status: "Approved",
        min_ft: 25,
        name: "800 Studewood St Cross-Street SMBL",
      },
      geometry: makeRectPolygonFt(originLng, originLat, 165, -80, 2, 200),
    },
  ];

  const matched = matchCh42OverlaysToBlockface(singleSide, protectionFeatures, 2026);
  const matchedFromFC = matchCh42OverlaysToBlockface(
    singleSide,
    { type: "FeatureCollection", features: protectionFeatures },
    2026
  );
  assert.equal(matched.length, 1);
  assert.deepEqual(matchedFromFC, matched);
  assert.equal(matched[0].ordinance, "2009-0555");
  assert.equal(matched[0].minFt, 20);
  assert.equal(matched[0].isRibbonGeometry, true);
  assert.equal(matched[0].lifecycle.expiration_year, 2029);
  assert.equal(matched[0].lifecycle.sunset_status, "expiring_soon");
  assert.deepEqual(matched[0].matchedHcads.sort(), ["0640000000001", "0640000000002"]);

  // Verify production min_lot_sqft / min_bldg_line_ft field support, bare items[] signature, & post-toggle exclusion
  const prodOverlay = [
    {
      type: "Feature",
      properties: {
        protection_type: "smls",
        ordinance: "2024-0984",
        status: "Approved",
        min_lot_sqft: 5000,
        name: "North Norhill SMLS",
      },
      geometry: makeRectPolygonFt(originLng, originLat, 45, -5, 60, 120),
    },
  ];
  const matchedBareItems = matchCh42OverlaysToBlockface(singleSide.items, prodOverlay, 2026);
  assert.equal(matchedBareItems.length, 1);
  assert.equal(matchedBareItems[0].minSqft, 5000);
  assert.deepEqual(matchedBareItems[0].matchedHcads, ["0640000000002"]);
  const afterUncheckNorth2 = matchCh42OverlaysToBlockface(
    {
      ...singleSide,
      items: singleSide.items.map((it) =>
        it.hcadNum === "0640000000002" ? { ...it, included: false } : it
      ),
    },
    prodOverlay,
    2026
  );
  assert.equal(afterUncheckNorth2.length, 0);

  // Verify axial angle diff boundary helper wraps across 0/180 cleanly and address normalization parses 1/2 + APT
  assert.ok(Math.abs(axialAngleDiffDeg(2, 178) - 4) < 1e-6);
  const parsedAddr = parseStreetAddressParts("1102 1/2 W  8TH STREET APT B");
  assert.equal(parsedAddr.houseNumber, 1102);
  assert.equal(parsedAddr.houseSuffix, "1/2");
  assert.equal(parsedAddr.parity, "even");
  assert.equal(parsedAddr.hundredBlock, 1100);
  assert.equal(parsedAddr.streetKey, "W 8TH ST");
  assert.equal(parsedAddr.isZeroAddress, false);
});

// ============================================================================
// UT-JS-5: Live Boundary Threats Deduplication & Inactive Exclusion (DF-10)
// ============================================================================

test("UT-JS-5: computeLiveBoundaryThreats deduplicates 4 streams into 1 property row and guards null/(0,0)/out/archived inputs", () => {
  const boundaryFeat = {
    type: "Feature",
    properties: { id: "nbhd_test", name: "Norhill" },
    geometry: makeRectPolygonFt(-95.388, 29.79, -500, -500, 1000, 1000),
  };

  const targetHcad = "0610550020012";
  const insideLng = -95.388;
  const insideLat = 29.79;
  const outsideLng = -95.55;
  const outsideLat = 29.95;

  const result = computeLiveBoundaryThreats(boundaryFeat, {
    boundaryLayer: "neighborhoods",
    boundaryName: "Norhill",
    activePermitAlerts: {
      alerts: [
        // 1. Same property in stream #1 (permits)
        {
          hcad_num: targetHcad,
          address: "1122 PEDEN ST",
          lng: insideLng,
          lat: insideLat,
          severity: "critical",
          headline: "Demolition permit issued",
          latest_filing_date: "2026-10-15",
        },
        // Null coordinates MUST NOT coerce via Number(null) === 0
        {
          hcad_num: "0610550020099",
          address: "NULL COORD HOUSE",
          lng: null,
          lat: null,
        },
        // Explicit (0, 0) Null Island MUST be rejected
        {
          hcad_num: "0610550020098",
          address: "ZERO ISLAND HOUSE",
          lng: 0,
          lat: 0,
        },
        // Outside boundary point MUST be rejected
        {
          hcad_num: "0610550020097",
          address: "9999 OUTSIDE FWY",
          lng: outsideLng,
          lat: outsideLat,
        },
      ],
    },
    platAgendaMonitor: {
      reviews: {
        "2026-1001": {
          hcad: targetHcad,
          address: "1122 PEDEN ST",
          tier: "1",
          yearBuilt: 1924,
          why: "Replat subdividing 1924 bungalow lot into 3 flag lots",
        },
        "2026-1002": {
          tier: "3",
          threatCategory: "No Historic Impact",
        },
      },
      items: [
        // 2. Same property in stream #2 (plats)
        {
          app: "2026-1001",
          hcad: targetHcad,
          name: "PEDEN GARDEN REPLAT",
          hearingDateIso: "2026-10-20",
          atlas: { lng: insideLng + 0.00004, lat: insideLat + 0.00003, coordSource: "hcad_parcel" },
          _c: { group: "inner" },
        },
        // Out-of-territory ETJ plat MUST be excluded even if coords fall inside box!
        {
          app: "2026-1099",
          hcad: "0610550020077",
          name: "EXCLUDED ETJ PLAT",
          lng: insideLng,
          lat: insideLat,
          _c: { group: "out" },
        },
        // Tier 3 / No Historic Impact reviewed plat MUST be excluded!
        {
          app: "2026-1002",
          hcad: "0610550020078",
          name: "TIER 3 COMMERCIAL PLAT",
          lng: insideLng,
          lat: insideLat,
          _c: { group: "inner" },
        },
      ],
    },
    propertyWatchlist: {
      watchedProperties: [
        // 3. Same property in stream #3 (watchlist) matched by HCAD
        {
          id: "watch-peden",
          hcadAccount: targetHcad,
          address: "1122 PEDEN ST",
          lon: insideLng,
          lat: insideLat,
          trackerStatus: "Active / High Priority",
          updatedAt: "2026-10-25",
        },
        // Tombstone & Archived Tier 3 watchlist rows MUST be suppressed!
        {
          id: "watch-removed",
          hcadAccount: "0610550020066",
          address: "1140 PEDEN ST",
          lon: insideLng,
          lat: insideLat,
          trackerStatus: "Removed from Watchlist",
        },
        {
          id: "watch-archived",
          hcadAccount: "0610550020065",
          address: "1144 PEDEN ST",
          lon: insideLng,
          lat: insideLat,
          trackerStatus: "Archived (Tier 3)",
        },
        // Unconfirmed plat auto-sweep seed MUST be excluded!
        {
          id: "plat-unconfirmed",
          hcadAccount: "0610550020064",
          address: "1148 PEDEN ST",
          lon: insideLng,
          lat: insideLat,
          tierConfirmed: false,
          trackerStatus: "Monitoring",
        },
        // Un-geocoded Watchlist entry (lon: null, lat: null) tagged to Norhill MUST NOT coerce to [0, 0]
        // (which would fail PIP AND skip the neighborhood fallback!), and MUST keep lng/lat === null!
        {
          id: "watch-ungeocoded-norhill",
          hcadAccount: "0610550020088",
          address: "1199 PEDEN ST",
          neighborhood: "Norhill",
          lon: null,
          lat: null,
          trackerStatus: "Active",
          updatedAt: "2026-10-21",
        },
      ],
    },
    hahcCoaTracker: {
      filings: [
        // 4. Same property in stream #4 (HAHC active demolition COA)
        {
          hcad_num: targetHcad,
          address: "1122 PEDEN ST",
          lng: insideLng,
          lat: insideLat,
          app_category: "demolition_relocation",
          app_type: "COA - Demolition of Residence",
          is_active_agenda: true,
          meeting_date: "2026-10-08",
        },
        // Routine non-active alteration COA MUST NOT count as a live threat!
        {
          hcad_num: "0610550020044",
          address: "1150 PEDEN ST",
          lng: insideLng,
          lat: insideLat,
          app_category: "addition_new",
          app_type: "Alteration - Addition",
          outcome: "Approved",
          is_active_agenda: false,
          meeting_date: "2025-05-10",
        },
      ],
    },
  });

  assert.equal(result.totalUniqueThreats, 2);
  assert.equal(result.items.length, 2);
  assert.deepEqual(result.sourceCounts, {
    permits: 1,
    plats: 1,
    watchlist: 2,
    hahc: 1,
  });
  const merged = result.items.find((r) => r.hcadNum === targetHcad);
  assert.ok(merged);
  assert.equal(merged.sources.length, 4);
  assert.equal(merged.severityRank, 0);
  assert.equal(merged.latestDate, "2026-10-25");

  const ungeocoded = result.items.find((r) => r.hcadNum === "0610550020088");
  assert.ok(ungeocoded, "Un-geocoded Norhill watchlist item must match neighborhood fallback");
  assert.equal(ungeocoded.lng, null);
  assert.equal(ungeocoded.lat, null);

  // Even against a synthetic globe-spanning polygon enclosing (0, 0), null/empty/(0, 0) coordinates MUST NEVER leak through!
  const worldBoundary = {
    type: "Feature",
    properties: { id: "globe_test", name: "Globe" },
    geometry: {
      type: "Polygon",
      coordinates: [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]]],
    },
  };
  const nullIslandCheck = computeLiveBoundaryThreats(worldBoundary, {
    activePermitAlerts: {
      alerts: [
        { hcad_num: "0610550020099", address: "NULL COORD", lng: null, lat: null },
        { hcad_num: "0610550020098", address: "EMPTY COORD", lng: "", lat: "" },
        { hcad_num: "0610550020097", address: "ZERO ISLAND", lng: 0, lat: 0 },
      ],
    },
  });
  assert.equal(nullIslandCheck.totalUniqueThreats, 0);

  // Verify directional prefix + unit stripping deduplicates non-HCAD records on the same address
  assert.equal(
    normalizeThreatAddressKey("1112 NORTH BOULEVARD, HOUSTON TX 77006"),
    "1112 N BLVD"
  );
  assert.equal(normalizeThreatAddressKey("1112 N BLVD #2"), "1112 N BLVD");
  const dirDedup = computeLiveBoundaryThreats(boundaryFeat, {
    boundaryLayer: "neighborhoods",
    boundaryName: "Norhill",
    activePermitAlerts: {
      alerts: [
        { address: "1112 NORTH BOULEVARD", lng: insideLng, lat: insideLat, headline: "Demo" },
      ],
    },
    propertyWatchlist: {
      watchedProperties: [
        {
          id: "watch-dir-dedup",
          address: "1112 N BLVD #A",
          lon: insideLng,
          lat: insideLat,
          trackerStatus: "Active",
        },
      ],
    },
  });
  assert.equal(dirDedup.totalUniqueThreats, 1);
  assert.equal(dirDedup.items[0].sources.length, 2);

  // Cross-feed deduplication when one feed has single/multi-HCAD + multi-address and another has
  // blank HCAD or a secondary parcel HCAD, while NEVER collapsing distinct 13-digit HCAD parcels
  // (including comma-delimited multi-HCAD watchlist and permit related_hcads records).
  const crossFeedAddrDedup = computeLiveBoundaryThreats(boundaryFeat, {
    boundaryLayer: "council_districts",
    boundaryName: "District C",
    activePermitAlerts: {
      alerts: [
        {
          hcad_num: "0611390420051",
          related_hcads: ["0611390420052"],
          address: "235 W 20TH ST",
          badge: "Pre-Demolition Sewer Disconnect",
          severity: "critical",
          lng: insideLng,
          lat: insideLat,
        },
      ],
    },
    platAgendaMonitor: {
      reviews: {
        "2026-9901": {
          tier: "2",
          hcad: "0611390420001, 0611390420051",
          address: "239 W 20TH ST; 235 W 20TH ST",
          why: "Townhome replat",
        },
      },
      items: [
        {
          app: "2026-9901",
          hcad: "0611390420001",
          name: "Heights partial replat no 33",
          atlas: { lng: insideLng, lat: insideLat, coordSource: "hcad_parcel" },
        },
      ],
    },
    propertyWatchlist: {
      watchedProperties: [
        {
          id: "watch-no-hcad-239",
          address: "235 West 20th Street",
          lon: insideLng,
          lat: insideLat,
          trackerStatus: "Monitoring",
        },
        {
          id: "watch-secondary-hcad-235",
          hcadAccount: "0611390420052",
          address: "233 W 20TH ST",
          lon: insideLng,
          lat: insideLat,
          trackerStatus: "Active",
        },
        {
          id: "watch-distinct-multi-hcad",
          hcadAccount: "0611390420999, 0611390420998",
          address: "239 W 20TH ST",
          lon: insideLng,
          lat: insideLat,
          trackerStatus: "For Sale",
        },
      ],
    },
  });
  assert.equal(crossFeedAddrDedup.totalUniqueThreats, 2);
  const replatMerged = crossFeedAddrDedup.items.find((r) =>
    ["0611390420051", "0611390420001"].includes(r.hcadNum)
  );
  const distinctMulti = crossFeedAddrDedup.items.find((r) => r.hcadNum === "0611390420999");
  assert.ok(replatMerged, "Expected multi-parcel permit + plat + watchlist rows to merge");
  assert.ok(distinctMulti, "Expected multi-HCAD watchlist record to keep non-empty hcadNum and avoid collapsing");
  assert.deepEqual(replatMerged.sources.slice().sort(), ["permits", "plats", "watchlist"]);
  assert.deepEqual(distinctMulti.sources, ["watchlist"]);
});

// ============================================================================
// UT-JS-6: Compact Scorecard Schema Decoder & 4,242-Boundary Lookup Integrity
// ============================================================================

test("UT-JS-6: neighborhood_scorecards.json decodes all boundaries across 9 families with zero NaN fields", async () => {
  const rawPayload = JSON.parse(fs.readFileSync(SCORECARDS_PATH, "utf8"));
  const overlaysPayload = JSON.parse(
    fs.readFileSync(path.join(REPO_ROOT, "app", "public", "data", "overlays.json"), "utf8")
  );
  assert.ok(ORGANIZER_CACHE_BUST >= "20261030a");
  resetNeighborhoodScorecardsCache();
  const ds = await loadNeighborhoodScorecards({
    url: `public/data/neighborhood_scorecards.json?v=${ORGANIZER_CACHE_BUST}`,
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => rawPayload }),
  });

  assert.ok(ds.records.length >= 4200);
  assert.equal(ds.byKey.size, ds.records.length);
  assert.equal(SCORECARD_FAMILIES.length, 9);
  assert.equal(VALID_SCORECARD_LAYERS.size, 9);
  assert.equal(ds.councilTermExpires, "2028-01");
  assert.equal(ds.minSubdivisionStandingStructures, 10);

  const exactFamilyCounts = {
    council_districts: 11,
    super_neighborhoods: 88,
    historic_districts: 23,
    potential_historic_districts: 60,
    heritage_districts: 1,
    holc_1937_zones: 47,
  };
  for (const [layer, expectedCount] of Object.entries(exactFamilyCounts)) {
    const famRows = ds.byLayer.get(layer) || [];
    assert.equal(
      famRows.length,
      expectedCount,
      `Expected ${expectedCount} scorecards in ${layer}, found ${famRows.length}`
    );
  }
  assert.ok((ds.byLayer.get("nrhp_districts") || []).length >= 12);
  assert.ok((ds.byLayer.get("neighborhoods") || []).length >= 1380);
  assert.ok((ds.byLayer.get("platted_subdivisions") || []).length >= 2500);

  // Exact 1:1 ID-set parity against overlays.json for all 8 non-subdivision boundary families
  for (const famLayer of [
    "council_districts",
    "historic_districts",
    "heritage_districts",
    "nrhp_districts",
    "potential_historic_districts",
    "super_neighborhoods",
    "holc_1937_zones",
    "neighborhoods",
  ]) {
    const scIds = new Set((ds.byLayer.get(famLayer) || []).map((r) => r.id));
    const ovIds = new Set(
      (overlaysPayload[famLayer]?.features || []).map((f) => String(f.properties.id))
    );
    assert.deepEqual(scIds, ovIds, `Scorecard ID drift against overlays.json[${famLayer}]`);
  }

  // Verify COH_COUNCIL_ROSTER_2024_2028 matches all 11 Council District scorecards and District C metadata
  assert.equal(Object.keys(COH_COUNCIL_ROSTER_2024_2028).length, 11);
  for (const [letter, cm] of Object.entries(COH_COUNCIL_ROSTER_2024_2028)) {
    const scRow = ds.byKey.get(`council_districts:council_dist_${letter.toLowerCase()}`);
    assert.ok(scRow, `Missing council_dist_${letter.toLowerCase()}`);
    assert.equal(scRow.councilMemberMeta?.member, cm.member);
  }
  const distC = ds.byKey.get("council_districts:council_dist_c");
  assert.ok(distC, "council_districts:council_dist_c must exist");
  assert.equal(distC.councilMemberMeta?.member, "Joe Panzarella");
  assert.equal(distC.councilMemberMeta?.email, "districtc@houstontx.gov");
  assert.equal(distC.councilMemberMeta?.url, "https://www.houstontx.gov/council/c/index.html");
  assert.equal(distC.councilMemberMeta?.term_expires, "2028-01");
  assert.ok(distC.protectedLandmarkCount > 50, "District C must record standing Protected Landmarks");

  // Every single one of the 4,242 decoded records must have finite numbers and mathematically bounded invariants
  for (const rec of ds.records) {
    for (const numField of [
      "buildingCount",
      "pre1920Count",
      "pre1940Count",
      "pre1960Count",
      "pre1975Count",
      "post2020InfillCount",
      "pre1940Pct",
      "medianYear",
      "landmarkCount",
      "protectedLandmarkCount",
      "waiverLandmarkCount",
      "goodBrickCount",
      "cohDistrictProtectedBuildings",
      "cohChapter33Pct",
      "parcelCount",
      "residentialParcelCount",
      "singleFamilyParcelCount",
      "ownerOccupiedCount",
      "ownerOccupiedPct",
      "familyTrustCount",
      "familyTrustPct",
      "corporateLlcCount",
      "corporateLlcPct",
      "ch42ProtectedCount",
      "ch42ProtectedPct",
      "smlsProtectedCount",
      "smblProtectedCount",
      "expiringCh42ParcelCount",
      "smlsOrdinanceCount",
      "smblOrdinanceCount",
      "demolitionsTotal2020To2026",
      "demolitionsPre1940Count",
    ]) {
      assert.equal(
        Number.isFinite(rec[numField]),
        true,
        `Non-finite ${numField} on ${rec.key}: ${rec[numField]}`
      );
    }
    assert.ok(rec.cohChapter33Pct >= 0 && rec.cohChapter33Pct <= 100);
    assert.ok(rec.ch42ProtectedPct >= 0 && rec.ch42ProtectedPct <= 100);
    assert.ok(rec.ownerOccupiedPct >= 0 && rec.ownerOccupiedPct <= 100);
    assert.ok(rec.corporateLlcPct >= 0 && rec.corporateLlcPct <= 100);
    assert.ok(rec.ch42ProtectedCount <= rec.residentialParcelCount);
    assert.ok(rec.protectedLandmarkCount + rec.waiverLandmarkCount <= rec.landmarkCount);
    assert.equal(rec.decadeHistogram.length, 20);
    const binSum = rec.decadeHistogram.reduce((a, b) => a + b.count, 0);
    assert.equal(
      binSum,
      rec.buildingCount,
      `${rec.key} decadeHistogram sum (${binSum}) != buildingCount (${rec.buildingCount})`
    );
    assert.equal(rec.demolitionsByYear.length, 7);
    const demoSum = rec.demolitionsByYear.reduce((a, b) => a + b.count, 0);
    // Annual demo counts deduplicate by (hcad, year) while dmt deduplicates distinct parcels across 2020-2026
    assert.ok(
      rec.demolitionsTotal2020To2026 <= demoSum,
      `${rec.key} dmt (${rec.demolitionsTotal2020To2026}) exceeds annual sum (${demoSum})`
    );
    for (const eo of rec.expiringOrdinances) {
      assert.ok(
        eo.expirationYear >= 2026 && eo.expirationYear <= 2031,
        `Expiring ordinance year ${eo.expirationYear} outside 2026-2031 window on ${rec.key}`
      );
    }
    if (rec.layer === "platted_subdivisions") {
      assert.ok(
        rec.buildingCount >= 10,
        `Sparse subdivision ${rec.key} has only ${rec.buildingCount} structures (< 10)`
      );
    }
  }

  // Explicitly check sparse/zero-footprint decoding safety (no NaN / division by zero)
  const emptySparse = decodeScorecardRecord({
    ly: "platted_subdivisions",
    id: "empty_test",
    nm: "Empty Plat",
    bc: 0,
    rpc: 0,
  });
  assert.equal(emptySparse.buildingCount, 0);
  assert.equal(emptySparse.ownerOccupiedPct, 0);
  assert.equal(emptySparse.ch42ProtectedPct, 0);
  assert.equal(emptySparse.cohChapter33Pct, 0);
  resetNeighborhoodScorecardsCache();
});

// ============================================================================
// UT-JS-7: HTML Injection Escaping Across Organizer Renderers
// ============================================================================

test("UT-JS-7: all preservationOrganizer HTML renderers neutralize XSS payloads in names, owners, plats, and ordinances", () => {
  const xssImg = '<img src=x onerror="alert(1)">';
  const xssScript = '"><script>window.__pwned=1</script>';
  const ampOwner = "O'Connor & Sons <Holdings LLC>";

  assert.equal(
    escapeOrganizerHtml(`${xssImg}${xssScript}`),
    "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&quot;&gt;&lt;script&gt;window.__pwned=1&lt;/script&gt;"
  );

  const advScorecard = decodeScorecardRecord({
    ly: "neighborhoods",
    id: xssScript,
    nm: `Montrose ${xssImg}`,
    tp: `Neighborhood ${xssScript}`,
    sn: `Neartown ${xssScript}`,
    cd: `C ${xssImg}`,
    cc: `Civic ${xssScript}`,
    cm: {
      letter: `C ${xssImg}`,
      member: `Member ${xssImg}`,
      phone: `832-393-3004 ${xssScript}`,
      email: "districtc@houstontx.gov",
      url: "https://www.houstontx.gov/council/c/index.html",
    },
    pl: {
      year_label: `1911 ${xssImg}`,
      citation: `Vol 3 Pg 12 ${xssScript}`,
      deed_citation: `Clerk ${xssImg}`,
    },
    hl: {
      grade: `C ${xssImg}`,
      code: `C12 ${xssScript}`,
      temp_f: 96.4,
      delta_vs_a: 3.2,
    },
    cx: -95.39,
    cy: 29.74,
    bc: 120,
    p40: 80,
    ey: 1912,
    my: 1928,
    dh: [0, 0, 0, 0, 0, 0, 5, 10, 20, 30, 15, 10, 10, 5, 5, 5, 3, 1, 1, 0],
    exo: [{ t: `SMLS ${xssImg}`, o: `2008-001 ${xssScript}`, y: 2028, est: true }],
    dm: [1, 2, 1, 0, 3, 2, 1],
    dmt: 10,
  });

  const advThreats = {
    totalUniqueThreats: 1,
    sourceCounts: { permits: 1, plats: 1, watchlist: 0, hahc: 0 },
    items: [
      {
        hcadNum: `0610010010001${xssScript}`,
        address: `1200 ${xssImg} ST`,
        title: `Plat ${xssScript}`,
        badges: [`DEMO ${xssImg}`],
        details: [{ source: "permits", summary: `Alert ${xssScript}`, date: "2026-10-01" }],
        latestDate: "2026-10-01",
        severityRank: 0,
      },
    ],
  };

  const inlineHtml = renderBoundaryInlineScorecardHtml(advScorecard, advThreats);
  const hubHtml = renderScorecardHubTableRowsHtml([advScorecard], advScorecard.key);
  const detailHtml = renderScorecardDetailHtml(advScorecard, advThreats, {
    shareUrl: `https://example.org/#scorecard=${xssScript}`,
    generatedAt: `Oct 2026 ${xssImg}`,
  });

  const seed = makeHcadParcelFeature({
    index: 0,
    hcadNum: "0650000000001",
    address: `1502 ${xssImg} AVE`,
    owner: `${ampOwner} ${xssScript}`,
    legalLines: `LT 1 BLK 2 ${xssImg}`,
  });
  const assembly = assembleBlockfaceFromSeedParcel(seed, [seed]);
  const calc = computeChapter42BlockEligibility(assembly.items, {
    historicDistrict: `HD ${xssImg}`,
  });
  const ch42Html = renderChapter42ModalHtml(
    assembly,
    calc,
    [
      {
        key: "smls:1",
        protectionType: `SMLS ${xssImg}`,
        ordinance: `2008-1 ${xssImg}`,
        name: `Block ${xssScript}`,
        minSqft: 5000,
        minFt: null,
        isRibbonGeometry: false,
        matchedHcads: ["0650000000001"],
        lifecycle: resolveCh42OrdinanceLifecycle({ ordinance: "2008-0001" }, 2026),
      },
    ],
    { shareUrl: `https://example.org/?ch42=0650000000001&x=${xssScript}` }
  );

  for (const [label, html] of [
    ["inlineHtml", inlineHtml],
    ["hubHtml", hubHtml],
    ["detailHtml", detailHtml],
    ["ch42Html", ch42Html],
  ]) {
    assert.equal(html.includes("<script"), false, `${label} leaked unescaped <script tag`);
    assert.equal(html.includes("<img src=x"), false, `${label} leaked unescaped <img tag`);
    assert.equal(html.includes('onerror="alert'), false, `${label} leaked unescaped event attribute`);
  }
  assert.ok(ch42Html.includes("O&#39;Connor &amp; Sons &lt;Holdings LLC&gt;"));
  assert.ok(ch42Html.includes("(832) 393-6637"));
  assert.ok(ch42Html.includes("Sec. 42-173(b)"));
  assert.ok(ch42Html.includes("Sec. 42-171(a)(4)"));
});

// ============================================================================
// UT-JS-8: URL State Contract & Deep-Link Parameter Coexistence (DF-11)
// ============================================================================

test("UT-JS-8: filterStore round-trips #council=1, #bnd=council_districts:..., #scorecard=..., and #ch42=...&ch42opp=1 alongside existing state", () => {
  const store = createFilterStore();
  assert.equal(store.getState().layers.councilDistricts, false);
  assert.equal(store.getState().activeScorecard, null);
  assert.equal(store.getState().activeCh42, null);

  // 1. Full coexistence parse + serialize round-trip
  const complexHash =
    "#minY=1890&maxY=1945&council=1&districts=1&scorecard=snbr:sn_22&ch42=0610550020012&ch42opp=1&bnd=cd:council_dist_c&isolate=neighborhoods:nbhd_0001_montrose&sidebar=0&lat=29.78912&lng=-95.38845&z=16.50";
  const parsed = parseHashToState(complexHash);

  assert.equal(parsed.patch.minYear, 1890);
  assert.equal(parsed.patch.maxYear, 1945);
  assert.equal(parsed.patch.layers.councilDistricts, true);
  assert.equal(parsed.patch.layers.historicDistricts, true);
  assert.deepEqual(parsed.activeScorecard, {
    layer: "super_neighborhoods",
    id: "sn_22",
  });
  assert.deepEqual(parsed.activeCh42, {
    hcad: "0610550020012",
    includeOpposing: true,
  });
  assert.equal(parsed.selection?.boundaryLayer, "councilDistricts");
  assert.equal(parsed.selection?.boundaryId, "council_dist_c");
  assert.equal(parsed.collapseSidebar, true);

  store.setState(parsed.patch);
  const reserialized = serializeStateToHash(store.getState(), parsed.viewport, {
    selectedBoundary: { layerKey: "council_districts", id: "council_dist_c" },
    collapseSidebar: true,
  });
  assert.match(reserialized, /(?:^|&)scorecard=super_neighborhoods%3Asn_22(?:&|$)/);
  assert.match(reserialized, /(?:^|&)ch42=0610550020012(?:&|$)/);
  assert.match(reserialized, /(?:^|&)ch42opp=1(?:&|$)/);
  assert.match(reserialized, /(?:^|&)layers=[^&]*council_districts(?:%2C|,|&|$)/);
  assert.match(reserialized, /(?:^|&)bnd=council_districts%3Acouncil_dist_c(?:&|$)/);

  // 2. buildShareableUrl respects baseOriginPath in 3rd-arg options object and preserves legacy opposing alias
  const scorecardShare = buildShareableUrl(store.getState(), parsed.viewport, {
    baseOriginPath: "https://davemorris-gcp.github.io/houston-building-atlas/",
    includeViewport: false,
    activeScorecard: { layer: "council_districts", id: "council_dist_c" },
    activeCh42: null,
  });
  assert.ok(scorecardShare.startsWith("https://davemorris-gcp.github.io/houston-building-atlas/?"));
  assert.ok(scorecardShare.includes("scorecard=council_districts%3Acouncil_dist_c"));
  assert.equal(scorecardShare.includes("ch42="), false);

  const ch42LegacyAliasUrl = buildShareableUrl(store.getState(), parsed.viewport, {
    baseOriginPath: "https://davemorris-gcp.github.io/houston-building-atlas/",
    includeViewport: true,
    activeScorecard: null,
    activeCh42: { hcad: "0610550020012", opposing: true },
  });
  assert.ok(ch42LegacyAliasUrl.includes("ch42=0610550020012"));
  assert.ok(ch42LegacyAliasUrl.includes("ch42opp=1"));

  // 3. Malformed / hostile inputs reject cleanly without throwing
  const malformed = parseHashToState(
    "#ch42=12345&scorecard=neighborhoods:<script>alert(1)</script>&ch42opp=1"
  );
  assert.equal(malformed.activeCh42, null);
  assert.equal(malformed.activeScorecard, null);

  const allZeroHcad = parseHashToState("#ch42=0000000000000");
  assert.equal(allZeroHcad.activeCh42, null);

  const unknownLayer = parseHashToState("#scorecard=unknown_layer:foo");
  assert.equal(unknownLayer.activeScorecard, null);

  // 4. Plural #scorecards=... alias and unified boundary layer key mapping helpers
  assert.deepEqual(parseHashToState("#scorecards=1").activeScorecard, {
    layer: "council_districts",
    id: "__hub__",
  });
  assert.deepEqual(parseHashToState("#scorecards=cd:council_dist_c").activeScorecard, {
    layer: "council_districts",
    id: "council_dist_c",
  });
  assert.equal(toOverlayDataKey("neighborhoods", "council_dist_c"), "council_districts");
  assert.equal(toOverlayDataKey("", "cd_c"), "council_districts");
  assert.equal(toOverlayDataKey("neighborhoods", "holc_1937_d1"), "holc_1937_zones");
  assert.equal(toOverlayDataKey("potentialHistoricDistricts"), "potential_historic_districts");
  assert.equal(toLayerStateKey("council_districts"), "councilDistricts");
  assert.equal(toLayerStateKey("holc_1937_zones"), "holc1937");
  assert.equal(toLayerStateKey("platted_subdivisions"), "plattedSubdivisions");
});
