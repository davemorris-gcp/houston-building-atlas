import test from "node:test";
import assert from "node:assert/strict";

import {
  THC_DESIGNATION_TYPES,
  THC_FORM_VERSION,
  THC_FUNCTION_CATEGORIES,
  THC_HISTORICAL_CONTEXTS,
  THC_INTEGRITY_ASPECTS,
  THC_PROPERTY_TYPES,
  THC_STYLISTIC_INFLUENCES,
  buildThcSurveyFormModel,
  collectEditedThcSurveyFormModel,
  formatThcPrimaryImageId,
  mapStyleToThcInfluences,
  renderThcSurveyFormHtml,
  serializeThcSurveyFormToTsv,
} from "../../app/js/thcSurveyFormGenerator.js";
import {
  buildShareableUrl,
  createFilterStore,
  parseHashToState,
} from "../../app/js/filterStore.js";

test("UT-THC-1: official THC form version and checkbox catalogs match THC v3/2013 specification", () => {
  assert.equal(THC_FORM_VERSION, "3/2013");
  assert.equal(THC_PROPERTY_TYPES.length, 5);
  assert.equal(THC_DESIGNATION_TYPES.length, 9);
  assert.equal(THC_FUNCTION_CATEGORIES.length, 12);
  assert.equal(THC_STYLISTIC_INFLUENCES.length, 30);
  assert.equal(THC_HISTORICAL_CONTEXTS.length, 18);
  assert.equal(THC_INTEGRITY_ASPECTS.length, 7);
});

test("UT-THC-2: formatThcPrimaryImageId formats directional and non-directional Houston addresses in THC state syntax", () => {
  assert.equal(
    formatThcPrimaryImageId("3700 N MAIN ST", "0550140020008"),
    "TX-HA-Houston-MAIN-ST-N-3700-1"
  );
  assert.equal(
    formatThcPrimaryImageId("333 S JENSEN DR", "0371100000002"),
    "TX-HA-Houston-JENSEN-DR-S-333-1"
  );
  assert.equal(
    formatThcPrimaryImageId("6221 MAIN ST", "1233520010001"),
    "TX-HA-Houston-MAIN-ST-6221-1"
  );
  assert.equal(
    formatThcPrimaryImageId("Houston, TX", "0550140020008"),
    "TX-HA-Houston-0550140020008-1"
  );
});

test("UT-THC-3: mapStyleToThcInfluences maps Atlas styles into the 30 official THC checkboxes", () => {
  assert.deepEqual(mapStyleToThcInfluences("Spanish Colonial Revival", 1926).influences, [
    "Colonial Revival",
    "Spanish Colonial",
  ]);
  assert.deepEqual(mapStyleToThcInfluences("Craftsman Bungalow", 1922).influences, [
    "Craftsman",
  ]);
  assert.deepEqual(mapStyleToThcInfluences("Art Deco / Streamline Moderne", 1938).influences, [
    "Art Deco",
    "Moderne",
  ]);
  assert.deepEqual(mapStyleToThcInfluences("Queen Anne / Eastlake", 1895).influences, [
    "Queen Anne",
    "Eastlake",
  ]);
});

test("UT-THC-4: buildThcSurveyFormModel synthesizes Granada Theater, TBH (City-owned PUBLIC), and Palmer Chapel / Autry House", () => {
  // 1. Granada Theater (3700 N Main St, 1926, Spanish Colonial / Art Deco commercial theater)
  const granada = buildThcSurveyFormModel(
    {
      hcad_num: "0550140020008",
      address: "3700 N MAIN ST",
      landmark_name: "Granada Theater",
      year_built: 1926,
      bld_style: "Spanish Colonial Revival",
      use_category: "Commercial",
      stories: 2,
      bld_area: 14800,
      neighborhood: "Greater Heights",
      source_citation: "Opened 1926 as Granada Theater on North Main Street.",
    },
    {
      hcadNum: "0550140020008",
      title: "Granada Theater",
      ownerDisplay: "GRANADA MAIN LLC",
      lat: 29.798512,
      lng: -95.386412,
      shareUrl: "https://davemorris-gcp.github.io/houston-building-atlas/#hcad=0550140020008&thcSurvey=1",
    }
  );
  assert.equal(granada.localId, "0550140020008");
  assert.equal(granada.section1.currentName, "Granada Theater");
  assert.equal(granada.section1.ownershipStatus, "PRIVATE");
  assert.equal(granada.section1.constructionDate, "1926");
  assert.equal(granada.section1.constructionDateType, "Actual");
  assert.ok(granada.section1.currentUseCategories.includes("Recreation/culture"));
  assert.ok(granada.section2.stylisticInfluences.includes("Spanish Colonial"));
  assert.match(granada.section2.generalArchitecturalDescription, /3700 N MAIN ST \(Granada Theater\)/);
  assert.ok(granada.section3.historicalContexts.includes("Arts"));
  assert.ok(granada.section3.nrCriteria.includes("A"));
  assert.ok(granada.section3.nrCriteria.includes("C"));

  // 2. Talento Bilingue de Houston (333 S Jensen Dr, City of Houston PUBLIC ownership)
  const tbh = buildThcSurveyFormModel(
    {
      hcad_num: "0371100000002",
      address: "333 S JENSEN DR",
      landmark_name: "Talento Bilingue de Houston",
      year_built: 1984,
      own_tier: "civic_nonprofit",
    },
    {
      hcadNum: "0371100000002",
      title: "Talento Bilingue de Houston",
      ownerDisplay: "CITY OF HOUSTON",
    }
  );
  assert.equal(tbh.section1.ownershipStatus, "PUBLIC");
  assert.equal(tbh.section1.ownerName, "CITY OF HOUSTON");
  assert.match(tbh.section3.priorityExplanation, /Public\/civic stewardship/i);

  // 3. Palmer Memorial Chapel & Autry House (6221 Main St, NRHP + Good Brick Award + John F. Staub)
  const palmer = buildThcSurveyFormModel(
    {
      hcad_num: "1233520010001",
      address: "6221 MAIN ST",
      landmark_name: "Edward Albert Palmer Memorial Chapel & Autry House",
      year_built: 1927,
      architect: "William Ward Watkin; John F. Staub",
      bld_style: "Italian Renaissance Revival",
      is_nrhp: true,
      nrhp_refnum: "84001815",
    },
    {
      hcadNum: "1233520010001",
      title: "Edward Albert Palmer Memorial Chapel & Autry House",
      goodBrickAwards: [{ award_year: 1995, recipient: "Palmer Memorial Episcopal Church" }],
    }
  );
  assert.ok(palmer.section1.currentDesignations.includes("NR"));
  assert.ok(palmer.section1.currentDesignations.includes("Other"));
  assert.equal(palmer.section3.individuallyEligible, "Yes");
  assert.equal(palmer.section3.priority, "High");
  assert.deepEqual(palmer.section3.levelOfSignificance, ["National", "State", "Local"]);
});

test("UT-THC-5: TX Tax Code Sec. 25.025 confidential owners fail closed and Walk-the-Block surveyDraft merges cleanly", () => {
  const confModel = buildThcSurveyFormModel(
    {
      hcad_num: "0621100000014",
      address: "1127 KEY ST",
      year_built: 1928,
      owner: "SHOULD_NEVER_LEAK_NAME",
      is_confidential: true,
    },
    {
      hcadNum: "0621100000014",
      isConfidential: true,
      surveyDraft: {
        surveyor_name: "UH Architecture Field Team",
        district: "Norhill",
        observed_year: "1926",
        condition_rating: "Excellent",
        integrity_grade: "Intact",
        contributing_status: "Contributing",
        window_material: "Original wood 1/1 sash",
        cladding_material: "Wood weatherboard siding",
        porch_alteration: "Original tapered Craftsman piers on brick pedestals",
        field_notes: "Corner bungalow retaining original porte-cochere.",
      },
    }
  );

  assert.equal(confModel.section1.isConfidential, true);
  assert.equal(
    confModel.section1.ownerName,
    "Confidential Homeowner (TX Tax Code Sec. 25.025)"
  );
  assert.equal(confModel.section1.ownerName.includes("SHOULD_NEVER_LEAK_NAME"), false);
  assert.equal(confModel.section1.constructionDate, "1926");
  assert.equal(confModel.section1.recordedBy, "UH Architecture Field Team");
  assert.ok(confModel.section2.structural.wallMaterials.includes("Wood Siding"));
  assert.ok(confModel.section2.structural.windows.includes("Wood sash"));
  assert.match(confModel.section3.historyNotes, /Corner bungalow retaining original porte-cochere/);

  const html = renderThcSurveyFormHtml(confModel);
  assert.equal(html.includes("SHOULD_NEVER_LEAK_NAME"), false);
  assert.match(html, /TEXAS HISTORICAL COMMISSION/);
  assert.match(html, /HISTORIC RESOURCES SURVEY FORM/);
  assert.match(html, /SECTION 1/);
  assert.match(html, /SECTION 2/);
  assert.match(html, /SECTION 3/);
  assert.equal(html.includes("\u2014"), false, "Zero em-dashes in rendered THC Survey Form HTML");

  const tsv = serializeThcSurveyFormToTsv(confModel);
  const lines = tsv.split("\n");
  assert.equal(lines.length, 2);
  assert.equal(lines[0].split("\t").length, lines[1].split("\t").length);
  assert.equal(tsv.includes("SHOULD_NEVER_LEAK_NAME"), false);

  // Verify props.conf = 1 and owner_name = "CONFIDENTIAL" also fail closed
  const confTile = buildThcSurveyFormModel(
    { hcad_num: "0621100000014", conf: 1, owner: "SHOULD_NEVER_LEAK_TILE" },
    { ownerDisplay: "SHOULD_NEVER_LEAK_DISPLAY" }
  );
  assert.equal(confTile.section1.isConfidential, true);
  assert.equal(
    confTile.section1.ownerName,
    "Confidential Homeowner (TX Tax Code Sec. 25.025)"
  );

  // Verify TSV formula-trigger sanitization while keeping negative longitudes unquoted
  const formulaTsv = serializeThcSurveyFormToTsv({
    ...confModel,
    section1: { ...confModel.section1, currentName: "=HYPERLINK(\"https://example.com\")" },
    section3: { ...confModel.section3, historyNotes: "- 1926 Sanborn shows 1-story dwelling" },
  });
  assert.match(formulaTsv, /\t'=HYPERLINK\("https:\/\/example\.com"\)\t/);
  assert.match(formulaTsv, /\t'- 1926 Sanborn shows 1-story dwelling$/);
  assert.match(formulaTsv, /\t-95\.3698000\t/);
});

test("UT-THC-6: parseHashToState and buildShareableUrl round-trip #hcad=<id>&thcSurvey=1 and #thcSurvey=<id>", () => {
  const parsed1 = parseHashToState("#hcad=0550140020008&thcSurvey=1");
  assert.equal(parsed1.thcSurvey, true);
  assert.equal(parsed1.selection?.hcadNum, "0550140020008");

  const parsed2 = parseHashToState("#thcSurvey=0371100000002");
  assert.equal(parsed2.thcSurvey, true);
  assert.equal(parsed2.selection?.hcadNum, "0371100000002");

  const store = createFilterStore();
  const shareUrl = buildShareableUrl(store.getState(), null, {
    baseOriginPath: "https://davemorris-gcp.github.io/houston-building-atlas/",
    includeViewport: false,
    selectedHcad: "0550140020008",
    thcSurvey: true,
    useQueryString: false,
  });
  assert.match(shareUrl, /hcad=0550140020008/);
  assert.match(shareUrl, /thcSurvey=1/);

  // Verify collectEditedThcSurveyFormModel safely returns baseModel when rootEl is null
  const model = buildThcSurveyFormModel({ hcad_num: "0550140020008", address: "3700 N MAIN ST" });
  const collected = collectEditedThcSurveyFormModel(model, null);
  assert.equal(collected.localId, "0550140020008");

  // Verify non-null rootEl updates nested text fields, booleans, array checkbox groups, and scalar checkbox groups
  const stubRoot = {
    querySelectorAll(sel) {
      if (sel === "[data-thc-field]") {
        return [
          { getAttribute: () => "projectNumber", type: "text", value: "PH-CUSTOM-2026" },
          { getAttribute: () => "structural.stories", type: "text", value: "3" },
          { getAttribute: () => "section2.relocatedHas", type: "checkbox", checked: true },
        ];
      }
      if (sel === "[data-thc-group]") {
        return [
          { getAttribute: () => "section3.nrCriteria", checked: true, value: "A" },
          { getAttribute: () => "section3.nrCriteria", checked: false, value: "B" },
          { getAttribute: () => "section1.ownershipStatus", checked: true, value: "PUBLIC" },
          { getAttribute: () => "structural.basement", checked: true, value: "Partial" },
        ];
      }
      return [];
    },
  };
  const edited = collectEditedThcSurveyFormModel(model, stubRoot);
  assert.equal(edited.projectNumber, "PH-CUSTOM-2026");
  assert.equal(edited.section2.structural.stories, "3");
  assert.equal(edited.section2.relocatedHas, true);
  assert.deepEqual(edited.section3.nrCriteria, ["A"]);
  assert.equal(edited.section1.ownershipStatus, "PUBLIC");
  assert.equal(edited.section2.structural.basement, "Partial");
});
