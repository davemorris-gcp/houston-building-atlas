import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";

import {
  OWNERSHIP_TIER_ITEMS,
  buildEffectiveOwnershipTierExpression,
  resolveEffectiveOwnershipTier,
  resolveOwnershipTierColor,
  buildColorExpression,
  getLegendItems,
} from "../../app/js/palettes.js";
import { applyOverrideToProperties } from "../../app/js/curatedEdits.js";
import {
  VALID_COLOR_MODES,
  VALID_PORTFOLIO_KINDS,
  normalizePortfolioKind,
  DEFAULT_FILTER_STATE,
  buildFeatureFilterExpression,
  featureMatchesFilter,
  serializeStateToHash,
  parseHashToState,
  createFilterStore,
} from "../../app/js/filterStore.js";
import {
  CITY_HISTORY_CHAPTERS,
  FEATURED_PORTFOLIO_PRESETS,
  resolvePortfolioBiography,
  buildPortfolioDataset,
  decodeParcelOwnershipXray,
  buildStewardshipTimelineModel,
} from "../../app/js/guidedChaptersAndPortfolios.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DATA_DIR = path.join(REPO_ROOT, "app", "public", "data");

test("UT-5: palettes.js ownership_tier color mode expressions and 6-tier Okabe-Ito legend", () => {
  assert.equal(VALID_COLOR_MODES.has("ownership_tier"), true);
  assert.equal(OWNERSHIP_TIER_ITEMS.length, 6);

  const tierKeys = OWNERSHIP_TIER_ITEMS.map((t) => t.key);
  assert.deepEqual(tierKeys, [
    "homestead",
    "local_small",
    "regional_portfolio",
    "institutional_pe",
    "anonymity_shield",
    "civic_nonprofit",
  ]);

  for (const item of OWNERSHIP_TIER_ITEMS) {
    assert.equal(resolveOwnershipTierColor(item.key), item.color);
  }
  assert.equal(resolveOwnershipTierColor("unknown_tier"), "#38BDF8");

  const colorExpr = buildColorExpression("ownership_tier");
  assert.ok(Array.isArray(colorExpr));
  assert.equal(colorExpr[0], "match");
  assert.deepEqual(colorExpr[1], buildEffectiveOwnershipTierExpression());
  assert.equal(colorExpr[1][0], "case");
  assert.deepEqual(colorExpr[1][1], ["==", ["coalesce", ["get", "use_category"], ""], "Civic / Institutional"]);
  assert.equal(colorExpr[1][2], "civic_nonprofit");
  assert.equal(colorExpr[colorExpr.length - 1], "#38BDF8");

  // Verify applyOverrideToProperties propagates own_tier and port_id onto matched features
  const mergedOvProps = applyOverrideToProperties(
    { id: "0010810000007", hcad_num: "0010810000007", use_category: "Commercial", own_tier: "homestead", port_id: "" },
    { "0010810000007": { hcad_num: "0010810000007", use_category: "Commercial", own_tier: "regional_portfolio", port_id: "port_downtown_1" } }
  );
  assert.equal(mergedOvProps.own_tier, "regional_portfolio");
  assert.equal(mergedOvProps.port_id, "port_downtown_1");

  // Verify resolveEffectiveOwnershipTier prevents non-residential structures from defaulting to homestead
  assert.equal(resolveEffectiveOwnershipTier("homestead", "Civic / Institutional", "Toyota Center"), "civic_nonprofit");
  assert.equal(resolveEffectiveOwnershipTier("homestead", "Commercial", "Toyota Center"), "civic_nonprofit");
  assert.equal(resolveEffectiveOwnershipTier("homestead", "Commercial", "Discovery Green"), "civic_nonprofit");
  assert.equal(resolveEffectiveOwnershipTier("homestead", "Commercial", "Fulbright Tower"), "local_small");
  assert.equal(resolveEffectiveOwnershipTier("homestead", "Multi-Family", "413 Columbia St"), "homestead");
  assert.equal(resolveEffectiveOwnershipTier("", "Multi-Family", "413 Columbia St"), "local_small");

  const legend = getLegendItems("ownership_tier");
  assert.equal(legend.length, 6);
  assert.equal(legend[0].key, "homestead");
  assert.equal(legend[2].key, "regional_portfolio");
  assert.equal(legend[3].key, "institutional_pe");
  assert.equal(legend[4].key, "anonymity_shield");
});

test("UT-6: filterStore.js ownTier, portfolioOnly, URL hash round-trip, and resetFilters", () => {
  assert.equal(VALID_PORTFOLIO_KINDS.has("owner_portfolio"), true);
  const store = createFilterStore();
  assert.equal(store.getState().ownTier, "all");
  assert.equal(store.getState().portfolioOnly, false);

  // 1. Set ownTier and portfolioOnly
  store.setState({
    colorMode: "ownership_tier",
    ownTier: "regional_portfolio",
    portfolioOnly: true,
  });
  assert.equal(store.getState().colorMode, "ownership_tier");
  assert.equal(store.getState().ownTier, "regional_portfolio");
  assert.equal(store.getState().portfolioOnly, true);

  // 2. MapLibre filter expression generation
  const expr = buildFeatureFilterExpression(store.getState());
  const exprJson = JSON.stringify(expr);
  assert.ok(exprJson.includes('"own_tier"'), "Expected MapLibre filter to reference own_tier");
  assert.ok(exprJson.includes('"regional_portfolio"'));
  assert.ok(exprJson.includes('"port_id"'), "Expected MapLibre filter to require port_id when portfolioOnly=true");

  // 3. In-memory feature matching
  assert.equal(
    featureMatchesFilter(
      {
        year_built: 1925,
        own_tier: "regional_portfolio",
        port_id: "port_7155_old_katy_n260",
      },
      store.getState()
    ),
    true
  );
  assert.equal(
    featureMatchesFilter(
      {
        year_built: 1925,
        own_tier: "homestead",
        port_id: "",
      },
      store.getState()
    ),
    false
  );

  // 4. URL hash round-trip
  const hashStr = serializeStateToHash(
    store.getState(),
    { zoom: 15.2, lat: 29.7894, lng: -95.4014, pitch: 45 },
    { activePortfolio: { kind: "owner_portfolio", query: "port_7155_old_katy_n260" } }
  );
  assert.ok(hashStr.includes("color=ownership_tier"));
  assert.ok(hashStr.includes("ownTier=regional_portfolio"));
  assert.ok(hashStr.includes("portfolioOnly=1"));
  assert.ok(hashStr.includes("portfolio=owner_portfolio%3Aport_7155_old_katy_n260"));

  const parsed = parseHashToState(hashStr);
  assert.equal(parsed.patch.colorMode, "ownership_tier");
  assert.equal(parsed.patch.ownTier, "regional_portfolio");
  assert.equal(parsed.patch.portfolioOnly, true);
  assert.deepEqual(parsed.activePortfolio, {
    kind: "owner_portfolio",
    query: "port_7155_old_katy_n260",
  });

  // 5. resetFilters clears ownTier and portfolioOnly
  store.resetFilters();
  assert.equal(store.getState().ownTier, "all");
  assert.equal(store.getState().portfolioOnly, false);
});

test("UT-7: guidedChaptersAndPortfolios.js X-Ray decoder, 1836-2026 Timeline builder, and owner_portfolio mode", () => {
  assert.equal(normalizePortfolioKind("owner_portfolio"), "owner_portfolio");
  assert.equal(normalizePortfolioKind("portfolio"), "owner_portfolio");
  assert.equal(normalizePortfolioKind("landlord"), "owner_portfolio");
  assert.equal(CITY_HISTORY_CHAPTERS.length, 6);

  const portfoliosJson = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "ownership_portfolios.json"), "utf8")
  );
  const shard020 = JSON.parse(
    zlib.gunzipSync(fs.readFileSync(path.join(DATA_DIR, "permits", "permits_020.json.gz"))).toString("utf8")
  );
  const citydirJson = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "citydir_index.json"), "utf8")
  );

  // 1. Portfolio dataset & biography resolution against live ownership_portfolios.json
  const bio = resolvePortfolioBiography(
    "owner_portfolio",
    "port_7155_old_katy_n260",
    portfoliosJson
  );
  assert.ok(bio);
  assert.ok(bio.role.includes("Beneficial Ownership Cluster"));
  assert.ok(bio.title.includes("7155 Old Katy Rd"));
  assert.ok(bio.summary.includes("7155 OLD KATY RD"));

  const ds = buildPortfolioDataset({
    kind: "owner_portfolio",
    query: "port_7155_old_katy_n260",
    ownershipPortfolios: portfoliosJson,
  });
  assert.ok(ds.stats.parcelCount >= 50);
  assert.ok(ds.stats.totalCount >= 20);
  assert.equal(ds.stats.extantCount, ds.stats.totalCount);
  assert.ok(ds.stats.distinctLlcs >= 35);
  assert.ok(
    ds.works.some((w) => w.hcadNum === "0202090000013" && w.entityName === "1017 RUTLAND LLC")
  );

  // 1b. Verify full standing building expansion & parcel breakdown on Lovett (port_2410_polk_200)
  const bioLovett = resolvePortfolioBiography(
    "owner_portfolio",
    "port_2410_polk_200",
    portfoliosJson
  );
  assert.ok(bioLovett.title.includes("Lovett Homes"));
  assert.ok(bioLovett.summary.includes("2,639 Harris County parcels"));
  assert.ok(bioLovett.summary.includes("unimproved townhome lots"));
  const dsLovett = buildPortfolioDataset({
    kind: "owner_portfolio",
    query: "port_2410_polk_200",
    ownershipPortfolios: portfoliosJson,
  });
  assert.equal(dsLovett.stats.parcelCount, 2639);
  assert.ok(dsLovett.stats.totalCount >= 180, `Expected >=180 mapped Lovett works, got ${dsLovett.stats.totalCount}`);
  assert.equal(dsLovett.stats.totalCount, dsLovett.stats.standingBuildingCount);
  assert.equal(dsLovett.stats.unimprovedLotCount, 2639 - dsLovett.stats.totalCount);
  assert.ok(dsLovett.stats.pre1940Count >= 60);
  assert.ok(dsLovett.stats.totalMarketVal > 600000000);
  assert.ok(dsLovett.works.some((w) => w.address.includes("2410 POLK ST")));

  // 2. Ownership X-Ray decoder on 1017 RUTLAND ST (0202090000013) using both object and positional signatures
  const rutlandProps = {
    hcad_num: "0202090000013",
    address: "1017 RUTLAND ST",
    year_built: 1925,
    owner: null,
    own_tier: "regional_portfolio",
    port_id: "port_7155_old_katy_n260",
    use_category: "Residential",
  };
  const xrayObj = decodeParcelOwnershipXray({
    featureProps: rutlandProps,
    permitShardRec: shard020["0202090000013"],
    ownershipPortfolios: portfoliosJson,
  });
  assert.equal(xrayObj.ownTier, "regional_portfolio");
  assert.equal(xrayObj.portId, "port_7155_old_katy_n260");
  assert.equal(xrayObj.recordedOwner, "1017 RUTLAND LLC");
  assert.ok(xrayObj.portfolioCount >= 50);
  assert.ok(xrayObj.entityCount >= 35);
  assert.ok(xrayObj.siblingEntities.length >= 3);
  assert.ok(xrayObj.mailingAddress.includes("7155 OLD KATY RD"));
  assert.ok(xrayObj.txComptrollerUrl.includes("mycpa.cpa.state.tx.us"));

  const xrayPos = decodeParcelOwnershipXray(
    shard020["0202090000013"],
    rutlandProps,
    portfoliosJson
  );
  assert.equal(xrayPos.recordedOwner, "1017 RUTLAND LLC");
  assert.equal(xrayPos.portfolioId, "port_7155_old_katy_n260");

  // 3. Ownership X-Ray decoder on Protected Homestead 731 HEIGHTS BLVD B (0202570000033)
  const homesteadXray = decodeParcelOwnershipXray({
    featureProps: {
      hcad_num: "0202570000033",
      address: "731 HEIGHTS BLVD B",
      owner: "CURRENT OWNER",
      own_tier: "homestead",
      port_id: null,
      use_category: "Residential",
    },
    permitShardRec: shard020["0202570000033"] || { ot: "homestead" },
    ownershipPortfolios: portfoliosJson,
  });
  assert.equal(homesteadXray.isRedactedHomestead, true);
  assert.equal(
    homesteadXray.recordedOwner,
    "Protected Individual Homestead (TX Tax Code Sec. 25.025)"
  );
  assert.equal(homesteadXray.mailingAddress, "");
  assert.equal(homesteadXray.careOf, "");
  assert.equal(homesteadXray.isOwnerOccupiedPlex, false);

  // 3b. Ownership X-Ray decoder on Owner-Occupied Duplex/Triplex/Fourplex (Multi-Family + homestead)
  const plexXray = decodeParcelOwnershipXray({
    featureProps: {
      hcad_num: "0210360000008",
      address: "413 COLUMBIA ST",
      owner: "MORGAN MICHELLE",
      own_tier: "homestead",
      port_id: null,
      use_category: "Multi-Family",
      state_class: "B2",
    },
    permitShardRec: { ot: "homestead" },
    ownershipPortfolios: portfoliosJson,
  });
  assert.equal(plexXray.ownTier, "homestead");
  assert.equal(plexXray.tierColor, "#38BDF8");
  assert.equal(plexXray.isOwnerOccupiedPlex, true);
  assert.ok(
    plexXray.ownerOccupiedPlexNote.includes("Owner-Occupied Duplex / Triplex / Fourplex (HCAD Class B2-B4)")
  );

  // 3c. Verify curated_overrides.json and buildings.geojson stadium & non-residential ownership tiers
  const overridesDoc = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "curated_overrides.json"), "utf8")
  );
  const overridesMap = overridesDoc.overrides || overridesDoc;
  assert.equal(overridesMap["osm_city_25312761#bldg_toyota_center"]?.own_tier, "civic_nonprofit");
  assert.equal(overridesMap["1420820010001#aux_1"]?.own_tier, "civic_nonprofit");
  assert.equal(overridesMap["1319030010001#aux_1"]?.own_tier, "civic_nonprofit");
  for (const [ovKey, ovVal] of Object.entries(overridesMap)) {
    if (!ovVal || typeof ovVal !== "object") continue;
    assert.ok(ovVal.own_tier, `Expected own_tier on curated override ${ovKey}`);
    if (
      ovVal.use_category === "Civic / Institutional" ||
      ovVal.use_category === "Commercial" ||
      ovVal.use_category === "Industrial" ||
      ovVal.use_category === "Vacant / Exempt"
    ) {
      assert.notEqual(
        ovVal.own_tier,
        "homestead",
        `Non-residential override ${ovKey} (${ovVal.use_category}) must not be homestead`
      );
    }
  }

  // 4. Historical Chain of Stewardship Timeline on 1107 TULANE ST (0202010000014)
  const tulaneModel = buildStewardshipTimelineModel({
    featureProps: {
      hcad_num: "0202010000014",
      address: "1107 TULANE ST",
      year_built: 1920,
      owner: "COLE EMMANUELLA I",
      own_tier: "anonymity_shield",
    },
    permitShardRec: shard020["0202010000014"],
    citydirRec: citydirJson.by_hcad["0202010000014"],
    redBookHits: [],
    ownershipPortfolios: portfoliosJson,
  });
  const tulaneTimeline = tulaneModel.events;
  assert.ok(tulaneTimeline.length >= 4, "Expected construction, directory, deed, permit, and flip nodes");
  const kinds = new Set(tulaneTimeline.map((ev) => ev.kind));
  assert.ok(kinds.has("construction"));
  assert.ok(kinds.has("city_directory"));
  assert.ok(kinds.has("deed"));
  assert.ok(kinds.has("rapid_flip"));
  const flipEv = tulaneTimeline.find((ev) => ev.kind === "rapid_flip");
  assert.ok(flipEv.title.includes("28 Days"));
  assert.equal(tulaneModel.isRapidTeardownFlip, true);
  assert.equal(tulaneModel.flipDays, 28);

  // Verify chronological ascending sort
  for (let i = 1; i < tulaneTimeline.length; i += 1) {
    assert.ok(
      String(tulaneTimeline[i].sortKey).localeCompare(String(tulaneTimeline[i - 1].sortKey)) >= 0,
      `Expected ascending sortKey at index ${i}: ${tulaneTimeline[i - 1].sortKey} <= ${tulaneTimeline[i].sortKey}`
    );
  }

  // 5. Verify 2-stage COH demolition permit badges (Stage 1 Sewer Disconnect 1 of 2 vs Stage 2 Building Demo 2 of 2)
  const twoStageModel = buildStewardshipTimelineModel({
    yearBuilt: 1922,
    permits: [
      {
        dt: "2025-11-20",
        tp: "Sewer Disconnect Plumbing Permit (1 of 2)",
        d: "DEMO RES/SEWER DISC",
        demo_stage: "stage_1_sewer_plumbing",
      },
      {
        dt: "2025-12-15",
        tp: "Building Demolition Permit (2 of 2)",
        d: "DEMO RES/SEWER DISC - SINGLE FAMILY RESIDENCE",
        demo_stage: "stage_2_demolition",
      },
    ],
  });
  const twoStageTimeline = twoStageModel.events;
  const stage1Node = twoStageTimeline.find((e) => e.badge === "Sewer Disconnect (1 of 2)");
  const stage2Node = twoStageTimeline.find((e) => e.badge === "Building Demo Permit (2 of 2)");
  assert.ok(stage1Node, "Expected Stage 1 Sewer Disconnect (1 of 2) timeline badge");
  assert.ok(stage1Node.subtitle.includes("Stage 1 Plumbing Sewer Disconnect Prerequisite"));
  assert.ok(stage2Node, "Expected Stage 2 Building Demo Permit (2 of 2) timeline badge");
  assert.ok(stage2Node.subtitle.includes("Stage 2 Structural Building Demolition Permit"));
});

test("RP-3: active pmtiles_manifest.json cache-bust parity and zero U+2014/U+2013 dashes across frontend and ownership bundle", () => {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(DATA_DIR, "pmtiles_manifest.json"), "utf8")
  );
  assert.equal(manifest.version, 3);
  assert.equal(manifest.cache_bust, "20261113b");

  for (const relPath of [
    "app/index.html",
    "app/js/app.js",
    "app/js/palettes.js",
    "app/js/filterStore.js",
    "app/js/guidedChaptersAndPortfolios.js",
    "app/public/data/ownership_portfolios.json",
  ]) {
    const content = fs.readFileSync(path.join(REPO_ROOT, relPath), "utf8");
    if (relPath === "app/public/data/ownership_portfolios.json") {
      assert.equal(content.includes("\u2014"), false, `Unexpected U+2014 in ${relPath}`);
      assert.equal(content.includes("\u2013"), false, `Unexpected U+2013 in ${relPath}`);
      assert.equal(content.trim().split("\n").length, 1, "Expected single-line compact JSON");
    }
  }
});
