/**
 * Texas Historical Commission (THC) Historic Resources Survey Form Auto-Generator
 * (Official THC Form Version 3/2013 + HHM/Waxahachie Narrative Synthesis Standard)
 *
 * Pre-populates all 3 official sections of the Texas Historical Commission
 * Historic Resources Survey Form from the Houston Building Atlas's 32 municipal
 * and archival datasets (HCAD CAMA/GIS, 1905-2026 COH Building Permits, 1866-1926
 * City Directories, 1885-1951 Sanborn Maps, NRHP, 1915 Red Book, Good Brick Awards,
 * 23 COH HPO Historic District Surveys, TARO/HMRC Finding Aids, and 1988-2026
 * Ownership & County Clerk Deed Chains), plus live Walk-the-Block field survey
 * observations when open.
 *
 * Strictly enforces TX Tax Code Sec. 25.025 homeowner confidentiality fail-closed.
 */

import { decodeAppraiserNote, explainPermitRecord } from "./permitPlainEnglish.js?v=20261113b";

export const THC_FORM_VERSION = "3/2013";

export const THC_PROPERTY_TYPES = [
  "Building",
  "Structure",
  "Object",
  "Site",
  "District",
];

export const THC_DESIGNATION_TYPES = [
  "NR District",
  "NHL",
  "NR",
  "RTHL",
  "OTHM",
  "HTC",
  "SAL",
  "Local",
  "Other",
];

export const THC_FUNCTION_CATEGORIES = [
  "Agriculture",
  "Commerce/trade",
  "Defense",
  "Domestic",
  "Educational",
  "Government",
  "Healthcare",
  "Industry/processing",
  "Recreation/culture",
  "Religious",
  "Social",
  "Other",
];

export const THC_STYLISTIC_INFLUENCES = [
  "Log traditional",
  "Greek Revival",
  "Italianate",
  "Second Empire",
  "Eastlake",
  "Queen Anne",
  "Shingle",
  "Romanesque Revival",
  "Folk Victorian",
  "Colonial Revival",
  "Renaissance Revival",
  "Exotic Revival",
  "Gothic Revival",
  "Tudor Revival",
  "Neo-Classical",
  "Beaux Arts",
  "Mission",
  "Monterey",
  "Pueblo Revival",
  "Spanish Colonial",
  "Prairie",
  "Craftsman",
  "Art Deco",
  "Moderne",
  "International",
  "Post-war Modern",
  "Ranch",
  "Commercial Style",
  "No Style",
  "Other",
];

export const THC_ROOF_FORMS = [
  "Gable",
  "Hipped",
  "Gambrel",
  "Shed",
  "Flat w/ parapet",
  "Mansard",
  "Pyramid",
  "Other",
];

export const THC_ROOF_MATERIALS = [
  "Wood shingles",
  "Tile",
  "Composition Shingles",
  "Metal",
  "Other",
];

export const THC_WALL_MATERIALS = [
  "Brick",
  "Stucco",
  "Stone",
  "Wood shingles",
  "Log",
  "Terra Cotta",
  "Concrete",
  "Metal",
  "Wood Siding",
  "Siding: Other",
  "Glass",
  "Asbestos",
  "Vinyl",
  "Other",
];

export const THC_WINDOWS = [
  "Fixed",
  "Wood sash",
  "Double hung",
  "Casement",
  "Metal sash",
  "Decorative Screenwork",
  "Other",
];

export const THC_DOORS = [
  "Single door",
  "Double door",
  "With transom",
  "With sidelights",
  "Other",
];

export const THC_PLANS = [
  "L-plan",
  "T-plan",
  "Modified L-plan",
  "2-room",
  "Open",
  "Center Passage",
  "Bungalow",
  "Shotgun",
  "Irregular",
  "Four Square",
  "Rectangular",
  "Other",
];

export const THC_CHIMNEY_FEATURES = [
  "Brick",
  "Stone",
  "Stucco",
  "Interior",
  "Exterior",
  "Corbelled Caps",
  "Other",
];

export const THC_PORCH_FORMS = [
  "Shed Roof",
  "Hipped Roof",
  "Gable Roof",
  "Flat Roof",
  "Inset",
  "Other",
];

export const THC_PORCH_SUPPORTS = [
  "Wood posts (plain)",
  "Wood posts (turned)",
  "Masonry pier",
  "Fabricated metal",
  "Box columns",
  "Classical columns",
  "Tapered box supports",
  "Suspension rods",
  "Suspension cables",
  "Spindlework",
  "Jigsawn trim",
  "Other",
];

export const THC_PORCH_MATERIALS = ["Metal", "Wood", "Fabric", "Other"];

export const THC_BASEMENT_OPTIONS = ["None", "Partial", "Full"];

export const THC_LANDSCAPE_FEATURES = [
  "Sidewalks",
  "Terracing",
  "Drives",
  "Well/cistern",
  "Gardens",
  "Other",
];

export const THC_LANDSCAPE_MATERIALS = [
  "Stone",
  "Wood",
  "Concrete",
  "Brick",
  "Other materials",
];

export const THC_HISTORICAL_CONTEXTS = [
  "Commerce",
  "Communication",
  "Agriculture",
  "Architecture",
  "Arts",
  "Immigration/Settlement",
  "Law/Government",
  "Education",
  "Exploration",
  "Health",
  "Religion/Spirituality",
  "Science/Technology",
  "Military",
  "Social/Cultural",
  "Natural Resources",
  "Planning/Development",
  "Transportation",
  "Other",
];

export const THC_NR_CRITERIA = [
  {
    code: "A",
    description:
      "Associated with events that have made a significant contribution to the broad pattern of our history",
  },
  {
    code: "B",
    description: "Associated with the lives of persons significant in our past",
  },
  {
    code: "C",
    description:
      "Embodies the distinctive characteristics of a type, period or method of construction or represents the work of a master, or possesses high artistic value, or represents a significant and distinguishable entity whose components lack individual distinctions",
  },
  {
    code: "D",
    description:
      "Has yielded, or is likely to yield, information important in prehistory or history",
  },
];

export const THC_SIGNIFICANCE_LEVELS = ["National", "State", "Local"];

export const THC_PRIORITY_LEVELS = ["High", "Medium", "Low"];

export const THC_INTEGRITY_ASPECTS = [
  "Location",
  "Design",
  "Materials",
  "Workmanship",
  "Setting",
  "Feeling",
  "Association",
];

function escapeHtml(val) {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Format an official THC photo identifier following the state municipal convention:
 * `TX-HA-Houston-<StreetName>-<Dir>-<HouseNum>-1`
 */
export function formatThcPrimaryImageId(address = "", hcadNum = "") {
  const raw = String(address || "").trim();
  if (!raw || /^houston\b/i.test(raw)) {
    const cleanHcad = String(hcadNum || "").replace(/[^0-9A-Za-z]/g, "");
    return `TX-HA-Houston-${cleanHcad || "Parcel"}-1`;
  }
  const tokens = raw
    .replace(/[,#.]/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  let houseNum = "";
  if (tokens.length > 0 && /^\d+[A-Za-z]?$/.test(tokens[0])) {
    houseNum = tokens.shift();
  }
  let dir = "";
  if (tokens.length > 0 && /^(N|S|E|W|NE|NW|SE|SW|NORTH|SOUTH|EAST|WEST)$/i.test(tokens[0])) {
    dir = tokens.shift().toUpperCase().slice(0, 2);
  }
  const streetBody =
    tokens
      .map((t) =>
        t
          .replace(/[^A-Za-z0-9]/g, "")
          .replace(/^[a-z]/, (c) => c.toUpperCase())
      )
      .filter(Boolean)
      .join("-") || "Street";
  const parts = ["TX", "HA", "Houston", streetBody];
  if (dir) parts.push(dir);
  if (houseNum) parts.push(houseNum);
  parts.push("1");
  return parts.join("-");
}

/**
 * Map Atlas style strings (`bld_style`, `displayStyle`, HPO survey style) to
 * the 30 official THC Stylistic Influence checkboxes.
 */
export function mapStyleToThcInfluences(styleRaw = "", yearBuilt = 0, useCategory = "") {
  const s = String(styleRaw || "").trim();
  const low = s.toLowerCase();
  const checked = new Set();
  let otherStyleText = "";

  if (low.includes("queen anne")) checked.add("Queen Anne");
  if (low.includes("eastlake")) checked.add("Eastlake");
  if (low.includes("folk victorian") || (low.includes("victorian") && !checked.size)) {
    checked.add("Folk Victorian");
  }
  if (low.includes("italianate")) checked.add("Italianate");
  if (low.includes("greek revival")) checked.add("Greek Revival");
  if (low.includes("second empire")) checked.add("Second Empire");
  if (low.includes("shingle")) checked.add("Shingle");
  if (low.includes("romanesque")) checked.add("Romanesque Revival");
  if (
    low.includes("colonial revival") ||
    low.includes("georgian") ||
    low.includes("dutch colonial") ||
    low.includes("federal")
  ) {
    checked.add("Colonial Revival");
  }
  if (low.includes("renaissance")) checked.add("Renaissance Revival");
  if (low.includes("exotic") || low.includes("moorish") || low.includes("egyptian")) {
    checked.add("Exotic Revival");
  }
  if (low.includes("gothic")) checked.add("Gothic Revival");
  if (low.includes("tudor") || low.includes("english vernacular") || low.includes("cotswold")) {
    checked.add("Tudor Revival");
  }
  if (
    low.includes("neo-classical") ||
    low.includes("neoclassical") ||
    low.includes("classical revival")
  ) {
    checked.add("Neo-Classical");
  }
  if (low.includes("beaux arts") || low.includes("beaux-arts")) checked.add("Beaux Arts");
  if (low.includes("mission")) checked.add("Mission");
  if (low.includes("monterey")) checked.add("Monterey");
  if (low.includes("pueblo")) checked.add("Pueblo Revival");
  if (
    low.includes("spanish") ||
    low.includes("mediterranean") ||
    low.includes("churrigueresque")
  ) {
    checked.add("Spanish Colonial");
  }
  if (low.includes("prairie") || low.includes("foursquare") || low.includes("four-square")) {
    checked.add("Prairie");
  }
  if (low.includes("craftsman") || low.includes("bungalow") || low.includes("arts & crafts")) {
    checked.add("Craftsman");
  }
  if (low.includes("art deco") || low.includes("deco") || low.includes("zigzag")) {
    checked.add("Art Deco");
  }
  if (low.includes("moderne") || low.includes("streamline") || low.includes("art moderne")) {
    checked.add("Moderne");
  }
  if (low.includes("international") || low.includes("miesian") || low.includes("bauhaus")) {
    checked.add("International");
  }
  if (
    low.includes("mid-century") ||
    low.includes("midcentury") ||
    low.includes("post-war") ||
    low.includes("postwar") ||
    low.includes("contemporary") ||
    low.includes("modern") ||
    low.includes("brutalist") ||
    low.includes("new formalism") ||
    low.includes("googie")
  ) {
    if (!checked.has("Art Deco") && !checked.has("Moderne") && !checked.has("International")) {
      checked.add("Post-war Modern");
    }
  }
  if (low.includes("ranch")) checked.add("Ranch");
  if (
    low.includes("commercial") ||
    low.includes("1-part") ||
    low.includes("2-part") ||
    low.includes("storefront") ||
    low.includes("warehouse") ||
    low.includes("industrial")
  ) {
    checked.add("Commercial Style");
  }
  if (low.includes("log")) checked.add("Log traditional");

  if (checked.size === 0) {
    const useLow = String(useCategory || "").toLowerCase();
    if (useLow.includes("commercial") || useLow.includes("retail") || useLow.includes("office")) {
      checked.add("Commercial Style");
    } else if (yearBuilt >= 1945 && yearBuilt <= 1975) {
      checked.add("Ranch");
    } else if (yearBuilt >= 1910 && yearBuilt <= 1932) {
      checked.add("Craftsman");
    } else if (yearBuilt >= 1880 && yearBuilt <= 1909) {
      checked.add("Folk Victorian");
    } else if (s && !/^(unknown|unclassified|not listed|n\/a|-)$/i.test(s)) {
      checked.add("Other");
      otherStyleText = s;
    } else {
      checked.add("No Style");
    }
  } else if (
    s &&
    !/^(unknown|unclassified|not listed|n\/a|-)$/i.test(s) &&
    (low.includes("shotgun") || low.includes("vernacular") || low.includes("minimal traditional"))
  ) {
    checked.add("Other");
    otherStyleText = s;
  }

  return {
    influences: Array.from(checked),
    otherStyleText,
  };
}

/**
 * Map building use/class and title into official THC Function categories and human label.
 */
export function resolveThcFunctionCategories(props = {}, title = "") {
  const rawUse = String(
    props.use_category || props.land_use || props.state_class_desc || ""
  ).toLowerCase();
  const rawTitle = String(title || props.landmark_name || props.building_name || "").toLowerCase();
  const stateClass = String(props.state_class || "").toUpperCase();

  const currentBoxes = new Set();
  const historicBoxes = new Set();
  let currentUseLabel = "Residential";
  let historicUseLabel = "Residential";

  if (
    rawTitle.includes("theater") ||
    rawTitle.includes("theatre") ||
    rawTitle.includes("talento biling") ||
    rawTitle.includes("museum") ||
    rawTitle.includes("auditorium") ||
    rawTitle.includes("music hall") ||
    rawTitle.includes("cultural")
  ) {
    currentBoxes.add("Recreation/culture");
    historicBoxes.add("Recreation/culture");
    currentUseLabel = "Performing arts / cultural center";
    historicUseLabel = rawTitle.includes("theater")
      ? "Historic motion picture & vaudeville theater"
      : "Civic / cultural arts center";
  } else if (
    rawTitle.includes("chapel") ||
    rawTitle.includes("church") ||
    rawTitle.includes("cathedral") ||
    rawTitle.includes("synagogue") ||
    rawTitle.includes("temple") ||
    rawUse.includes("relig")
  ) {
    currentBoxes.add("Religious");
    historicBoxes.add("Religious");
    currentUseLabel = "Religious facility / sanctuary";
    historicUseLabel = "Religious facility / sanctuary";
  } else if (
    rawTitle.includes("school") ||
    rawTitle.includes("university") ||
    rawTitle.includes("college") ||
    rawTitle.includes("academy") ||
    rawTitle.includes("library") ||
    rawUse.includes("educ") ||
    rawUse.includes("school")
  ) {
    currentBoxes.add("Educational");
    historicBoxes.add("Educational");
    currentUseLabel = "Educational / campus building";
    historicUseLabel = "Educational / campus building";
  } else if (
    rawTitle.includes("lulac") ||
    rawTitle.includes("lodge") ||
    rawTitle.includes("club") ||
    rawTitle.includes("hall") ||
    rawTitle.includes("ymca") ||
    rawTitle.includes("ywca")
  ) {
    currentBoxes.add("Social");
    historicBoxes.add("Social");
    currentUseLabel = "Civic / social organization headquarters";
    historicUseLabel = "Civic / social organization headquarters";
  } else if (
    rawTitle.includes("city hall") ||
    rawTitle.includes("courthouse") ||
    rawTitle.includes("fire station") ||
    rawTitle.includes("post office") ||
    rawUse.includes("gov") ||
    rawUse.includes("municipal")
  ) {
    currentBoxes.add("Government");
    historicBoxes.add("Government");
    currentUseLabel = "Government / municipal facility";
    historicUseLabel = "Government / municipal facility";
  } else if (rawTitle.includes("hospital") || rawTitle.includes("clinic") || rawUse.includes("med")) {
    currentBoxes.add("Healthcare");
    historicBoxes.add("Healthcare");
    currentUseLabel = "Healthcare / medical facility";
    historicUseLabel = "Healthcare / medical facility";
  } else if (
    rawUse.includes("ind") ||
    rawUse.includes("warehouse") ||
    rawUse.includes("manufacturing") ||
    stateClass.startsWith("F2")
  ) {
    currentBoxes.add("Industry/processing");
    historicBoxes.add("Industry/processing");
    currentUseLabel = "Industrial / warehouse";
    historicUseLabel = "Industrial / warehouse";
  } else if (
    rawUse.includes("comm") ||
    rawUse.includes("retail") ||
    rawUse.includes("office") ||
    rawUse.includes("mixed") ||
    stateClass.startsWith("F1")
  ) {
    currentBoxes.add("Commerce/trade");
    historicBoxes.add("Commerce/trade");
    currentUseLabel = "Commercial / retail or office building";
    historicUseLabel = "Commercial / trade building";
  } else {
    currentBoxes.add("Domestic");
    historicBoxes.add("Domestic");
    const isMulti =
      rawUse.includes("multi") ||
      rawUse.includes("duplex") ||
      rawUse.includes("apartment") ||
      stateClass.startsWith("B");
    currentUseLabel = isMulti ? "Multi-family residential" : "Single-family house";
    historicUseLabel = currentUseLabel;
  }

  return {
    currentUseCategories: Array.from(currentBoxes),
    historicUseCategories: Array.from(historicBoxes),
    currentUseLabel,
    historicUseLabel,
  };
}

/**
 * Infer structural details (Roof, Walls, Windows, Doors, Plan, Porch, Ancillary)
 * from HCAD attributes, architectural style, extra features (`xf`), and live
 * Walk-the-Block field survey draft (`surveyDraft`).
 */
export function inferThcStructuralDetails(
  props = {},
  styleInfluences = [],
  extraFeatures = [],
  surveyDraft = null
) {
  const styleSet = new Set(styleInfluences);
  const rawStyle = String(props.bld_style || props.style || "").toLowerCase();
  const storiesNum = Number(props.stories) > 0 ? Number(props.stories) : 1;

  // 1. Roof Form & Materials
  const roofForms = new Set();
  const roofMaterials = new Set();
  if (
    styleSet.has("Commercial Style") ||
    styleSet.has("Art Deco") ||
    styleSet.has("Moderne") ||
    styleSet.has("International")
  ) {
    roofForms.add("Flat w/ parapet");
    roofMaterials.add("Other");
  } else if (
    styleSet.has("Prairie") ||
    styleSet.has("Colonial Revival") ||
    styleSet.has("Neo-Classical") ||
    styleSet.has("Italianate") ||
    rawStyle.includes("foursquare")
  ) {
    roofForms.add("Hipped");
    roofMaterials.add("Composition Shingles");
  } else if (styleSet.has("Spanish Colonial") || styleSet.has("Mission")) {
    roofForms.add("Hipped");
    roofForms.add("Gable");
    roofMaterials.add("Tile");
  } else if (styleSet.has("Queen Anne")) {
    roofForms.add("Hipped");
    roofForms.add("Gable");
    roofMaterials.add("Composition Shingles");
  } else {
    roofForms.add("Gable");
    roofMaterials.add("Composition Shingles");
  }

  // 2. Wall Materials (merge Walk-the-Block surveyDraft.cladding_material if present)
  const wallMaterials = new Set();
  const survCladding = String(surveyDraft?.cladding_material || "").toLowerCase();
  if (survCladding.includes("wood") || survCladding.includes("weatherboard")) {
    wallMaterials.add("Wood Siding");
  } else if (survCladding.includes("brick")) {
    wallMaterials.add("Brick");
  } else if (survCladding.includes("stucco")) {
    wallMaterials.add("Stucco");
  } else if (survCladding.includes("vinyl") || survCladding.includes("aluminum")) {
    wallMaterials.add("Vinyl");
  } else if (survCladding.includes("asbestos")) {
    wallMaterials.add("Asbestos");
  } else if (survCladding.includes("fiber-cement") || survCladding.includes("hardie")) {
    wallMaterials.add("Siding: Other");
  }

  if (wallMaterials.size === 0) {
    if (
      styleSet.has("Tudor Revival") ||
      styleSet.has("Commercial Style") ||
      styleSet.has("Neo-Classical") ||
      styleSet.has("Beaux Arts") ||
      styleSet.has("Gothic Revival") ||
      rawStyle.includes("brick") ||
      rawStyle.includes("masonry")
    ) {
      wallMaterials.add("Brick");
    } else if (
      styleSet.has("Spanish Colonial") ||
      styleSet.has("Mission") ||
      styleSet.has("Art Deco") ||
      styleSet.has("Moderne")
    ) {
      wallMaterials.add("Stucco");
      wallMaterials.add("Brick");
    } else if (styleSet.has("International") || styleSet.has("Post-war Modern")) {
      wallMaterials.add("Brick");
      wallMaterials.add("Glass");
    } else {
      wallMaterials.add("Wood Siding");
    }
  }

  // 3. Windows (merge Walk-the-Block surveyDraft.window_material if present)
  const windows = new Set();
  const survWin = String(surveyDraft?.window_material || "").toLowerCase();
  if (survWin.includes("original wood") || survWin.includes("wood")) {
    windows.add("Wood sash");
    windows.add("Double hung");
  } else if (survWin.includes("metal") || survWin.includes("aluminum") || survWin.includes("steel")) {
    windows.add("Metal sash");
    windows.add("Casement");
  } else if (survWin.includes("vinyl")) {
    windows.add("Double hung");
    windows.add("Other");
  } else if (
    styleSet.has("Tudor Revival") ||
    styleSet.has("Spanish Colonial") ||
    styleSet.has("Art Deco") ||
    styleSet.has("International")
  ) {
    windows.add("Casement");
    windows.add("Metal sash");
  } else if (styleSet.has("Commercial Style")) {
    windows.add("Fixed");
    windows.add("Metal sash");
  } else {
    windows.add("Wood sash");
    windows.add("Double hung");
  }

  // 4. Doors
  const doors = new Set(["Single door"]);
  if (
    styleSet.has("Colonial Revival") ||
    styleSet.has("Neo-Classical") ||
    styleSet.has("Queen Anne") ||
    styleSet.has("Eastlake")
  ) {
    doors.add("With sidelights");
    doors.add("With transom");
  } else if (styleSet.has("Commercial Style") || styleSet.has("Art Deco") || styleSet.has("Beaux Arts")) {
    doors.delete("Single door");
    doors.add("Double door");
    doors.add("With transom");
  }

  // 5. Plan
  const plans = new Set();
  if (rawStyle.includes("shotgun")) {
    plans.add("Shotgun");
  } else if (styleSet.has("Craftsman") || rawStyle.includes("bungalow")) {
    plans.add("Bungalow");
  } else if (styleSet.has("Prairie") || rawStyle.includes("foursquare")) {
    plans.add("Four Square");
  } else if (styleSet.has("Queen Anne") || styleSet.has("Tudor Revival")) {
    plans.add("Irregular");
  } else {
    plans.add("Rectangular");
  }

  // 6. Chimneys
  const chimneys = new Set();
  let chimneysCount = "";
  const xfList = Array.isArray(extraFeatures) ? extraFeatures : [];
  const hasFireplaceXf = xfList.some((x) =>
    /fireplace|chimney|fp\b/i.test(String(x?.d || x?.c || ""))
  );
  if (
    hasFireplaceXf ||
    styleSet.has("Tudor Revival") ||
    styleSet.has("Colonial Revival") ||
    styleSet.has("Craftsman") ||
    styleSet.has("Queen Anne")
  ) {
    chimneys.add("Brick");
    chimneys.add("Exterior");
    chimneysCount = "1";
  }

  // 7. Porches / Canopies
  const porchForms = new Set();
  const porchSupports = new Set();
  const porchMaterials = new Set();
  const survPorch = String(surveyDraft?.porch_alteration || "").trim();
  if (styleSet.has("Commercial Style") || styleSet.has("Art Deco") || styleSet.has("Moderne")) {
    porchForms.add("Flat Roof");
    porchSupports.add("Suspension rods");
    porchMaterials.add("Metal");
  } else if (styleSet.has("Colonial Revival") || styleSet.has("Neo-Classical")) {
    porchForms.add("Hipped Roof");
    porchSupports.add("Classical columns");
    porchMaterials.add("Wood");
  } else if (styleSet.has("Craftsman")) {
    porchForms.add("Gable Roof");
    porchSupports.add("Tapered box supports");
    porchSupports.add("Masonry pier");
    porchMaterials.add("Wood");
  } else if (styleSet.has("Queen Anne") || styleSet.has("Folk Victorian") || styleSet.has("Eastlake")) {
    porchForms.add("Hipped Roof");
    porchSupports.add("Wood posts (turned)");
    porchSupports.add("Spindlework");
    porchMaterials.add("Wood");
  } else {
    porchForms.add("Shed Roof");
    porchSupports.add("Wood posts (plain)");
    porchMaterials.add("Wood");
  }

  // 8. Ancillary Buildings (from HCAD extraFeatures `xf` + `aux_count`)
  let garageCount = "";
  let shedCount = "";
  let otherAuxText = "";
  const garageHits = xfList.filter((x) => /gar|carport|cp\b|detached/i.test(String(x?.d || x?.c || "")));
  const shedHits = xfList.filter((x) => /shed|utl|utility|barn/i.test(String(x?.d || x?.c || "")));
  const poolOrQtrHits = xfList.filter((x) =>
    /pool|quarters|apt|guest|canopy|gazebo/i.test(String(x?.d || x?.c || ""))
  );
  if (garageHits.length > 0) {
    garageCount = `${garageHits.length} (Detached garage / carport)`;
  } else if (Number(props.aux_count) > 0) {
    garageCount = `${props.aux_count} (Secondary parcel structure)`;
  }
  if (shedHits.length > 0) {
    shedCount = `${shedHits.length}`;
  }
  if (poolOrQtrHits.length > 0) {
    otherAuxText = poolOrQtrHits
      .slice(0, 2)
      .map((x) => `${x.d || x.c}${x.yr ? ` (${x.yr})` : ""}`)
      .join("; ");
  }

  return {
    roofForms: Array.from(roofForms),
    roofMaterials: Array.from(roofMaterials),
    wallMaterials: Array.from(wallMaterials),
    windows: Array.from(windows),
    doors: Array.from(doors),
    plans: Array.from(plans),
    chimneysCount,
    chimneys: Array.from(chimneys),
    porchForms: Array.from(porchForms),
    porchSupports: Array.from(porchSupports),
    porchMaterials: Array.from(porchMaterials),
    porchNotes: survPorch || "Open front entry porch / canopy",
    stories: String(storiesNum),
    basement: "None",
    ancillaryGarage: garageCount,
    ancillaryBarn: "",
    ancillaryShed: shedCount,
    ancillaryOther: otherAuxText,
    landscapeFeatures: ["Sidewalks", "Drives"],
    landscapeMaterials: ["Concrete"],
    landscapeNotes: props.historic_district &&
      props.historic_district !== "Outside City District" &&
      props.historic_district !== "Outside Historic District"
      ? `Situated within ${props.historic_district} streetscape with pedestrian sidewalk and driveway.`
      : "Urban lot with public street frontage, walkway, and driveway.",
  };
}

/**
 * Synthesize a complete, publication-grade General Architectural Description paragraph
 * modeled directly on the completed Waxahachie HHM THC Survey Form (`45_HHM2019_Forms.pdf`).
 */
export function buildGeneralArchitecturalDescription({
  address = "",
  title = "",
  yearBuilt = 0,
  dateType = "Estimated",
  dateSource = "HCAD Real Property Roll",
  stories = "1",
  currentUseLabel = "Single-family house",
  historicUseLabel = "Single-family house",
  displayStyle = "",
  stylisticInfluences = [],
  structural = {},
  architect = "",
  builder = "",
  bldAreaSqft = 0,
  neighborhood = "",
  historicDistrict = "",
}) {
  const cleanAddr = String(address || "this parcel").trim();
  const hasDistinctName =
    title &&
    title !== "Historic Property" &&
    title.toLowerCase() !== cleanAddr.toLowerCase();
  const subj = hasDistinctName
    ? `The building at ${cleanAddr} (${title})`
    : `The building at ${cleanAddr}`;
  const storyWord =
    Number(stories) === 1
      ? "one-story"
      : Number(stories) === 2
      ? "two-story"
      : Number(stories) === 3
      ? "three-story"
      : `${stories}-story`;
  const yrClause =
    Number(yearBuilt) >= 1836
      ? `built in ${yearBuilt}, as ${
          dateType === "Actual" ? "documented" : "estimated"
        } via ${dateSource}`
      : "of historic construction";

  const sentences = [
    `${subj} is a ${storyWord} ${currentUseLabel.toLowerCase()} ${yrClause}.`,
  ];

  if (architect && !/^(unknown|not listed|n\/a|-)/i.test(architect)) {
    const bldClause =
      builder && !/^(unknown|not listed|n\/a|-)/i.test(builder) && builder !== architect
        ? ` and constructed by ${builder}`
        : "";
    sentences.push(`The structure was designed by ${architect}${bldClause}.`);
  } else if (builder && !/^(unknown|not listed|n\/a|-)/i.test(builder)) {
    sentences.push(`Historical records attribute construction to builder/contractor ${builder}.`);
  }

  if (currentUseLabel.toLowerCase() === historicUseLabel.toLowerCase()) {
    sentences.push(
      `The property was originally constructed for ${historicUseLabel.toLowerCase()} purposes, and its primary function remains consistent.`
    );
  } else {
    sentences.push(
      `Originally constructed as a ${historicUseLabel.toLowerCase()}, the property currently serves as a ${currentUseLabel.toLowerCase()}.`
    );
  }

  const wallList = (structural.wallMaterials || []).join(" and ").toLowerCase() || "wood siding";
  const primaryStyle =
    (stylisticInfluences && stylisticInfluences[0]) || displayStyle || "vernacular";
  const areaClause =
    Number(bldAreaSqft) > 0
      ? ` enclosing approximately ${Number(bldAreaSqft).toLocaleString()} square feet of gross building area`
      : "";
  sentences.push(
    `The primary exterior wall material is ${wallList}${areaClause}. The building's form and detailing reflect ${primaryStyle} stylistic influences.`
  );

  const planLabel = ((structural.plans || [])[0] || "Rectangular").toLowerCase();
  const roofFormLabel = ((structural.roofForms || [])[0] || "Gable").toLowerCase();
  const roofMatLabel = ((structural.roofMaterials || [])[0] || "composition shingles").toLowerCase();
  const winLabel = (structural.windows || []).join(", ").toLowerCase() || "double-hung sash";
  const doorLabel = (structural.doors || []).join(", ").toLowerCase() || "single-door";
  const porchSupp = (structural.porchSupports || []).join(", ").toLowerCase() || "wood posts";

  sentences.push(
    `The structure features a ${planLabel} plan topped by a ${roofFormLabel} roof clad in ${roofMatLabel}. Fenestration includes ${winLabel} windows and a ${doorLabel} primary entrance, complemented by a porch/canopy supported by ${porchSupp}.`
  );

  const distValid =
    historicDistrict &&
    historicDistrict !== "Outside City District" &&
    historicDistrict !== "Outside Historic District";
  if (distValid) {
    sentences.push(
      `The resource contributes to the historic streetscape of the ${historicDistrict} Historic District${
        neighborhood ? ` in ${neighborhood}` : ""
      }.`
    );
  } else if (neighborhood) {
    sentences.push(
      `The parcel is situated within the ${neighborhood} neighborhood of Houston, Harris County, Texas.`
    );
  }

  return sentences.join(" ");
}

/**
 * Build the complete 3-Section Texas Historical Commission (THC) Historic Resources
 * Survey Form data model for any building or parcel in the Houston Building Atlas.
 */
export function buildThcSurveyFormModel(props = {}, context = {}) {
  const hcadAcct = String(context.hcadNum || props.hcad_num || "").trim();
  const hasRealHcad = Boolean(
    hcadAcct && !hcadAcct.startsWith("PH-") && !hcadAcct.startsWith("DEMO-")
  );
  const surveyDraft = context.surveyDraft || null;
  const liveRec = context.liveHcadRec || null;
  const permitRec = context.permitShardRec || null;
  const hpoSurveyRec = context.hpoSurveyRec || null;

  const year =
    Number(surveyDraft?.observed_year) >= 1836
      ? Number(surveyDraft.observed_year)
      : Number(props.year_built || permitRec?.yr) || 0;
  const address = String(
    props.address || surveyDraft?.address || liveRec?.siteAddress || "Houston, TX"
  ).trim();
  const title = String(
    context.title ||
      props.landmark_name ||
      props.building_name ||
      props.name ||
      address ||
      "Historic Property"
  ).trim();
  const altNames = Array.isArray(context.altNames) ? context.altNames : [];
  const historicName = String(
    props.historic_name ||
      props.landmark_name ||
      context.redBookHit?.title ||
      context.nrhpHit?.resname ||
      (altNames.length > 0 ? altNames[0] : "") ||
      (title !== address ? title : "")
  ).trim();

  // Enforce TX Tax Code Sec. 25.025 statutory confidentiality fail-closed
  const ownerCandidates = [
    context.ownerDisplay,
    liveRec?.owner,
    props.owner,
    props.owner_name,
  ].map((v) => String(v || "").trim());
  const isConfidential = Boolean(
    context.isConfidential ||
      props.is_confidential ||
      props.conf ||
      permitRec?.conf ||
      ownerCandidates.some((c) => /confidential|withheld|sec\.\s*25\.025/i.test(c))
  );
  const rawOwner = isConfidential
    ? "Confidential Homeowner (TX Tax Code Sec. 25.025)"
    : String(
        context.ownerDisplay || liveRec?.owner || props.owner || props.owner_name || "Public / Unlisted"
      ).trim();

  const isPublicEntity =
    !isConfidential &&
    (/\b(CITY OF HOUSTON|HARRIS COUNTY|STATE OF TEXAS|UNITED STATES|HOUSTON ISD|INDEPENDENT SCHOOL DISTRICT|UNIVERSITY OF HOUSTON|TEXAS SOUTHERN UNIVERSITY|METRO|PORT OF HOUSTON|PARKS BOARD)\b/i.test(
      rawOwner
    ) ||
      String(props.own_tier || "").toLowerCase() === "civic_nonprofit");

  const lat = Number.isFinite(Number(context.lat ?? props.lat))
    ? Number(context.lat ?? props.lat)
    : 29.7604;
  const lng = Number.isFinite(Number(context.lng ?? props.lon ?? props.lng))
    ? Number(context.lng ?? props.lon ?? props.lng)
    : -95.3698;
  const latStr = lat.toFixed(7);
  const lngStr = lng.toFixed(7);
  const onlineMapUrl = `https://www.google.com/maps/place/${latStr},${lngStr}`;

  const legalDescription = String(
    context.legalSummary ||
      liveRec?.legalDescription ||
      props.legal_description ||
      props.subdivision ||
      "See HCAD Real Property Record"
  ).trim();
  const additionSubdivision = String(
    context.subdivisionName ||
      props.subdivision ||
      liveRec?.legalSubdivision ||
      liveRec?.subdivision ||
      ""
  ).trim();
  const subdivisionYear = String(
    context.plattedYear || props.plat_year || props.subdivision_year || ""
  ).trim();

  // Current Designations
  const designations = new Set();
  const lmType = String(props.landmark_type || props.designation || "").toLowerCase();
  const distName = String(props.historic_district || "").trim();
  const inCohDistrict = Boolean(
    distName &&
      distName !== "Outside City District" &&
      distName !== "Outside Historic District"
  );
  const isNrhp = Boolean(
    props.is_nrhp || props.nrhp_refnum || context.nrhpHit || lmType.includes("national register")
  );
  const isRthl = Boolean(
    props.is_rthl || lmType.includes("rthl") || lmType.includes("recorded texas historic landmark")
  );
  const isLocalLandmark = Boolean(
    lmType.includes("protected landmark") ||
      lmType.includes("landmark") ||
      props.coh_landmark_id
  );
  const goodBrickAwards = Array.isArray(context.goodBrickAwards)
    ? context.goodBrickAwards
    : Array.isArray(props.good_brick_awards)
    ? props.good_brick_awards
    : [];
  const hasRedBook = Boolean(context.redBookHit || props.red_book_id || props.is_red_book_1915);

  if (isNrhp) designations.add("NR");
  if (context.nrhpHit?.is_district || lmType.includes("nrhp district")) {
    designations.add("NR District");
  }
  if (isRthl) designations.add("RTHL");
  if (isLocalLandmark || inCohDistrict) designations.add("Local");
  if (goodBrickAwards.length > 0 || hasRedBook) designations.add("Other");

  const contribRaw = String(
    surveyDraft?.contributing_status ||
      hpoSurveyRec?.classification ||
      props.contributing_status ||
      ""
  ).toLowerCase();
  const isNonContrib = contribRaw.includes("non-contributing") || contribRaw.includes("noncontributing");
  const nrDistrictContributing =
    designations.has("NR District") || inCohDistrict
      ? isNonContrib
        ? "No"
        : "Yes"
      : "";

  const architect = String(
    props.architect || hpoSurveyRec?.architect || "Unknown or N/A"
  ).trim();
  const builder = String(
    props.builder ||
      hpoSurveyRec?.builder ||
      props.historic_contractor ||
      "Unknown or N/A"
  ).trim();

  const isCurated = Boolean(
    props.is_curated_override ||
      props.curated_override ||
      props.source_citation ||
      props.original_permit ||
      (props.source && String(props.source).includes("Curated"))
  );
  const constructionDateType =
    isCurated ||
    Boolean(props.original_permit) ||
    Boolean(context.cityDirData?.auditHit) ||
    Boolean(hpoSurveyRec?.year_built)
      ? "Actual"
      : "Estimated";
  const constructionDateSource = String(
    props.source_citation
      ? "Preservation Houston Archival Override"
      : props.original_permit
      ? `COH Historic Building Permit (${props.original_permit})`
      : context.cityDirData?.auditHit
      ? `HPL Houston City Directory (${context.cityDirData.auditHit.e})`
      : hpoSurveyRec?.year_built
      ? `COH HPO Historic District Survey (${distName || "HPO"})`
      : surveyDraft?.observed_year
      ? "In-field architectural survey estimate"
      : props.source || "HCAD Real Property Roll"
  ).trim();

  const funcInfo = resolveThcFunctionCategories(props, title);
  const primaryImageId =
    surveyDraft?.photo_filename?.replace(/\.[a-z0-9]+$/i, "") ||
    formatThcPrimaryImageId(address, hcadAcct);
  const photos = Array.isArray(context.photos) ? context.photos : [];
  const primaryPhoto = surveyDraft?._photoPreviewUrl
    ? {
        url: surveyDraft._photoPreviewUrl,
        caption: `${address} (Field Survey Capture: ${surveyDraft.photo_filename || primaryImageId})`,
        credit: surveyDraft.surveyor_name || "Field Survey Team",
      }
    : photos[0]
    ? {
        url: photos[0].image_url || photos[0].url || "",
        caption: photos[0].caption || title,
        credit:
          photos[0].source_credit || photos[0].credit || "Preservation Houston Archive",
      }
    : null;

  const displayStyle = String(
    context.displayStyle || hpoSurveyRec?.style || props.bld_style || props.style || ""
  ).trim();
  const styleMapped = mapStyleToThcInfluences(displayStyle, year, props.use_category);

  const permitsList = Array.isArray(permitRec?.p) ? permitRec.p : [];
  const extraFeatures = Array.isArray(permitRec?.xf) ? permitRec.xf : [];
  const structural = inferThcStructuralDetails(
    props,
    styleMapped.influences,
    extraFeatures,
    surveyDraft
  );

  const bldAreaSqft =
    Number(props.bld_area || liveRec?.bldgSqft) > 0
      ? Number(props.bld_area || liveRec?.bldgSqft)
      : 0;

  const generalArchitecturalDescription = buildGeneralArchitecturalDescription({
    address,
    title,
    yearBuilt: year,
    dateType: constructionDateType,
    dateSource: constructionDateSource,
    stories: structural.stories,
    currentUseLabel: funcInfo.currentUseLabel,
    historicUseLabel: funcInfo.historicUseLabel,
    displayStyle,
    stylisticInfluences: styleMapped.influences,
    structural,
    architect,
    builder,
    bldAreaSqft,
    neighborhood: props.neighborhood || "",
    historicDistrict: distName,
  });

  // Additions, modifications, specify dates
  const modItems = [];
  const remodYr = Number(permitRec?.remod || props.yr_remodel || props.remodel_year) || 0;
  const effYr = Number(permitRec?.eff || props.eff_year_built) || 0;
  if (remodYr > 1836 && remodYr !== year) {
    modItems.push(`HCAD Recorded Remodel Year: ${remodYr}`);
  }
  if (effYr > 1836 && effYr !== year && effYr !== remodYr) {
    modItems.push(`HCAD Effective Year Adjusted: ${effYr}`);
  }
  if (permitRec?.note) {
    const decodedNote = decodeAppraiserNote(permitRec.note);
    if (decodedNote?.hasShorthand && decodedNote.entries?.length > 0) {
      const plainSummary = decodedNote.entries
        .map((e) => `${e.date ? `${e.date} ` : ""}${e.kind ? `(${e.kind}) ` : ""}${e.text}`)
        .join(" ");
      modItems.push(`HCAD Appraiser Field Note: ${plainSummary} (Raw: ${permitRec.note})`);
    } else {
      modItems.push(`HCAD Appraiser Field Note: ${permitRec.note}`);
    }
  }
  for (const p of permitsList.slice(0, 5)) {
    if (!p) continue;
    const dt = p.dt || p.yr || "Undated";
    const explained = explainPermitRecord(p);
    const desc = explained
      ? `${p.d || p.t || "Permit"} (${explained})`
      : p.d || p.t || "Building Permit";
    modItems.push(`${dt}: ${desc}${p.no || p.id ? ` [#${p.no || p.id}]` : ""}`);
  }
  for (const xf of extraFeatures.slice(0, 4)) {
    if (!xf || !xf.yr) continue;
    modItems.push(`${xf.yr}: Added ${xf.d || xf.c}${xf.sqft ? ` (${xf.sqft} sq ft)` : ""}`);
  }
  if (surveyDraft?.window_material && /replacement|vinyl|aluminum/i.test(surveyDraft.window_material)) {
    modItems.push(`Field Survey Window Note: ${surveyDraft.window_material}`);
  }
  if (surveyDraft?.porch_alteration && !/intact|original/i.test(surveyDraft.porch_alteration)) {
    modItems.push(`Field Survey Porch Note: ${surveyDraft.porch_alteration}`);
  }

  const relocNote = String(
    permitRec?.reloc || props.relocation_note || props.moved_from || ""
  ).trim();
  const relocatedHas = Boolean(relocNote);

  // Section 3: Associated Historical Context & NR Criteria
  const histContexts = new Set(["Architecture", "Planning/Development"]);
  if (funcInfo.historicUseCategories.includes("Commerce/trade")) histContexts.add("Commerce");
  if (funcInfo.historicUseCategories.includes("Recreation/culture")) {
    histContexts.add("Arts");
    histContexts.add("Social/Cultural");
  }
  if (funcInfo.historicUseCategories.includes("Religious")) {
    histContexts.add("Religion/Spirituality");
  }
  if (funcInfo.historicUseCategories.includes("Educational")) histContexts.add("Education");
  if (funcInfo.historicUseCategories.includes("Social")) histContexts.add("Social/Cultural");
  if (funcInfo.historicUseCategories.includes("Government")) histContexts.add("Law/Government");
  if (funcInfo.historicUseCategories.includes("Healthcare")) histContexts.add("Health");
  if (hasRedBook || (year > 0 && year <= 1900)) {
    histContexts.add("Immigration/Settlement");
    histContexts.add("Social/Cultural");
  }

  const nrCriteria = new Set(["C"]);
  if (
    isNrhp ||
    hasRedBook ||
    (year > 0 && year <= 1905) ||
    histContexts.has("Social/Cultural") ||
    histContexts.has("Commerce") ||
    inCohDistrict
  ) {
    nrCriteria.add("A");
  }
  if (props.notable_person || /house|residence|home|memorial/i.test(historicName)) {
    if (isNrhp || isLocalLandmark || goodBrickAwards.length > 0 || hasRedBook) {
      nrCriteria.add("B");
    }
  }

  // Synthesize Archival & History Notes for Section 3
  const historyBullets = [];
  if (props.source_citation) {
    historyBullets.push(`Archival Provenance: ${props.source_citation}`);
  }
  if (props.notes) {
    historyBullets.push(`Curator Notes: ${props.notes}`);
  }
  if (isNrhp) {
    const ref = props.nrhp_refnum || context.nrhpHit?.refnum || "";
    historyBullets.push(
      `National Register of Historic Places (NRHP): Listed property${
        ref ? ` (NRIS Ref #${ref})` : ""
      }${context.nrhpHit?.certdate ? `, certified ${context.nrhpHit.certdate}` : ""}.`
    );
  }
  if (hasRedBook) {
    const rbTitle = context.redBookHit?.title || historicName || title;
    const rbPage = context.redBookHit?.page ? `, p. ${context.redBookHit.page}` : "";
    historyBullets.push(
      `1915 Red Book of Houston (African American Heritage Citation): Documented as "${rbTitle}"${rbPage}.`
    );
  }
  if (goodBrickAwards.length > 0) {
    for (const gb of goodBrickAwards) {
      historyBullets.push(
        `Preservation Houston Good Brick Award (${gb.award_year}): ${
          gb.recipient ? `${gb.recipient} - ` : ""
        }${gb.reason || gb.raw_description || "Recognized for excellence in historic preservation."}`
      );
    }
  }
  if (hpoSurveyRec) {
    historyBullets.push(
      `City of Houston HPO Historic District Survey (${
        hpoSurveyRec.district_name || distName
      }): Classified as "${hpoSurveyRec.classification || "Contributing"}"${
        hpoSurveyRec.year_built ? `, survey date ${hpoSurveyRec.year_built}` : ""
      }${hpoSurveyRec.style ? `, style "${hpoSurveyRec.style}"` : ""}.`
    );
  }
  const archivalCitations = Array.isArray(context.archivalCitations)
    ? context.archivalCitations
    : [];
  if (archivalCitations.length > 0) {
    const topArch = archivalCitations
      .slice(0, 3)
      .map((c) => `${c.repository || "Archive"} (${c.collection_id || c.id || ""}): ${c.title || ""}`)
      .join("; ");
    historyBullets.push(`TARO / HMRC / Woodson Archival Finding Aids: ${topArch}`);
  }
  if (context.cityDirSummary) {
    historyBullets.push(`1866-1926 Houston City Directories: ${context.cityDirSummary}`);
  }
  if (context.sanbornCoverage?.summaryText) {
    historyBullets.push(`Sanborn Fire Insurance Atlas Coverage: ${context.sanbornCoverage.summaryText}`);
  }
  if (props.original_permit || props.original_cost) {
    historyBullets.push(
      `Original Building Permit Record: ${props.original_permit || "On file"}${
        props.original_cost ? ` (Reported Cost: ${props.original_cost})` : ""
      }`
    );
  }
  if (surveyDraft?.field_notes) {
    historyBullets.push(`Walk-the-Block Field Survey Notes: ${surveyDraft.field_notes}`);
  }

  // Integrity (7 aspects)
  const integritySet = new Set(THC_INTEGRITY_ASPECTS);
  if (relocatedHas) {
    integritySet.delete("Location");
    integritySet.delete("Setting");
  }
  const survIntegrity = String(surveyDraft?.integrity_grade || "").toLowerCase();
  if (survIntegrity.includes("altered") || survIntegrity.includes("low")) {
    integritySet.delete("Materials");
    integritySet.delete("Workmanship");
  }
  const integrityNotes = surveyDraft?.integrity_grade
    ? `Field Survey Integrity Rating: ${surveyDraft.integrity_grade} (Condition: ${
        surveyDraft.condition_rating || "Good"
      }).`
    : relocatedHas
    ? `Relocated historic structure (${relocNote}); retains architectural design, materials, and workmanship.`
    : `Retains historic location, massing, fenestration pattern, and neighborhood setting (${
        permitRec?.qual ? `HCAD Quality Grade: ${permitRec.qual}` : "Verified via Atlas GIS & Street View"
      }).`;

  // Eligibility & Priority
  const hasHighDesignCredentials =
    isNrhp ||
    isRthl ||
    isLocalLandmark ||
    goodBrickAwards.length > 0 ||
    hasRedBook ||
    (architect !== "Unknown or N/A" && Boolean(architect)) ||
    (year > 0 && year < 1900);
  const individuallyEligible = hasHighDesignCredentials ? "Yes" : "Undetermined";
  const withinPotentialDistrict =
    inCohDistrict || designations.has("NR District") || Boolean(additionSubdivision)
      ? "Yes"
      : "Undetermined";
  const isContributing = isNonContrib
    ? "No"
    : inCohDistrict || hasHighDesignCredentials || (year > 0 && year <= 1975)
    ? "Yes"
    : "Undetermined";

  const watchlistAlerts = Array.isArray(context.watchlistAlerts) ? context.watchlistAlerts : [];
  const hasActiveThreat = Boolean(
    watchlistAlerts.length > 0 ||
      props.has_active_demo_permit ||
      props.permit_watch_status
  );
  const priority =
    hasActiveThreat || (hasHighDesignCredentials && !lmType.includes("protected landmark"))
      ? "High"
      : year > 0 && year <= 1945
      ? "Medium"
      : "Low";

  const priorityReasons = [];
  if (hasActiveThreat) {
    priorityReasons.push("Active COH/DSHS/Plat regulatory watch flag detected");
  }
  if (isNrhp && !isLocalLandmark) {
    priorityReasons.push("NRHP-listed property lacking local COH Protected Landmark protection");
  }
  if (goodBrickAwards.length > 0 && !lmType.includes("protected landmark")) {
    priorityReasons.push("Good Brick Award recipient eligible for local landmark designation");
  }
  if (hasRedBook) {
    priorityReasons.push("1915 Red Book African American cultural heritage site");
  }
  if (isPublicEntity) {
    priorityReasons.push("Public/civic stewardship ready for municipal landmark nomination");
  }
  if (priorityReasons.length === 0) {
    priorityReasons.push(
      year > 0 && year <= 1945
        ? `Intact ${year} historic resource contributing to ${
            distName || additionSubdivision || props.neighborhood || "Houston"
          } context`
        : "Standard neighborhood survey inventory record"
    );
  }

  const priorDocTypes = new Set(["Survey"]);
  if (archivalCitations.length > 0 || hasRedBook || goodBrickAwards.length > 0) {
    priorDocTypes.add("Other");
  }
  const priorDocDetailsList = [
    hasRealHcad ? `HCAD Real Property Account #${hcadAcct}` : "",
    isNrhp ? `NRHP Nomination (${props.nrhp_refnum || "Listed"})` : "",
    hpoSurveyRec ? `COH HPO ${distName} Survey Inventory` : "",
    context.cityDirData?.auditHit
      ? `HPL 1866-1926 City Directories (Earliest: ${context.cityDirData.auditHit.e})`
      : "",
    context.sanbornCoverage?.summaryText ? `Sanborn Atlas (${context.sanbornCoverage.summaryText})` : "",
    archivalCitations.length > 0 ? `${archivalCitations.length} TARO/HMRC Finding Aid(s)` : "",
  ].filter(Boolean);

  const todayIso = new Date().toISOString().slice(0, 10);
  const periodStart = year >= 1836 ? year : 1900;
  const periodEnd = Math.min(1976, Math.max(periodStart, periodStart + 35));

  return {
    formVersion: THC_FORM_VERSION,
    projectNumber:
      surveyDraft?.district && surveyDraft.district !== "Outside Historic District"
        ? `PH-UH-2026 (${surveyDraft.district})`
        : "PH-UH-COH-2026 (Houston Historic Resources & Landmark Survey)",
    localId: hasRealHcad ? hcadAcct : String(props.building_id || props.id || primaryImageId),
    appraisalDistrictId: hasRealHcad ? hcadAcct : "Exempt / Unassigned",
    county: "Harris",
    city: String(props.city || "Houston").trim(),
    address,
    shareUrl: String(context.shareUrl || "").trim(),

    // Section 1: Basic Inventory Information
    section1: {
      currentName: title,
      historicName: historicName || title,
      ownerName: rawOwner,
      ownerAddress: address,
      ownerCity: String(props.city || "Houston").trim(),
      ownerState: "TX",
      ownerZip: String(props.zip || props.zip_code || liveRec?.zip || "").trim(),
      ownershipStatus: isPublicEntity ? "PUBLIC" : "PRIVATE",
      isConfidential,
      latitude: latStr,
      longitude: lngStr,
      onlineMapUrl,
      legalDescription,
      additionSubdivision: additionSubdivision || "See Legal Description",
      subdivisionYear,
      propertyType: "Building",
      currentDesignations: Array.from(designations),
      nrDistrictContributing,
      otherDesignationText: [
        goodBrickAwards.length > 0
          ? `Good Brick Award (${goodBrickAwards.map((a) => a.award_year).join(", ")})`
          : "",
        hasRedBook ? "1915 Red Book of Houston" : "",
        inCohDistrict ? `COH District: ${distName}` : "",
      ]
        .filter(Boolean)
        .join("; "),
      architect,
      builder,
      constructionDate: year >= 1836 ? String(year) : "Undated",
      constructionDateType,
      constructionDateSource,
      currentUseCategories: funcInfo.currentUseCategories,
      currentUseLabel: funcInfo.currentUseLabel,
      historicUseCategories: funcInfo.historicUseCategories,
      historicUseLabel: funcInfo.historicUseLabel,
      recordedBy: String(
        surveyDraft?.surveyor_name ||
          "Preservation Houston & UH Historic Resources Survey Team"
      ).trim(),
      dateRecorded: todayIso,
      primaryImageId,
      photoDataRange: `${primaryImageId} to ${Math.max(1, photos.length || 1)}`,
      primaryPhoto,
    },

    // Section 2: Architectural Description
    section2: {
      generalArchitecturalDescription,
      additionsModificationsHas: modItems.length > 0,
      additionsModificationsText:
        modItems.length > 0
          ? modItems.join(" | ")
          : "No major exterior permit alterations indexed; verify in field.",
      relocatedHas,
      relocatedText: relocNote || "",
      stylisticInfluences: styleMapped.influences,
      otherStyleText: styleMapped.otherStyleText,
      displayStyle: displayStyle || styleMapped.influences.join(", "),
      structural,
    },

    // Section 3: Historical Information
    section3: {
      historicalContexts: Array.from(histContexts),
      historyNotes:
        historyBullets.length > 0
          ? historyBullets.join("\n")
          : `Constructed ca. ${
              year || "historic period"
            } at ${address}. Indexed in The Houston Building Atlas (${
              hasRealHcad ? `HCAD #${hcadAcct}` : "Civic Parcel"
            }).`,
      nrCriteria: Array.from(nrCriteria),
      areasOfSignificance: Array.from(histContexts).join("; "),
      periodsOfSignificance: year >= 1836 ? `${periodStart} - ${periodEnd}` : "20th Century",
      levelOfSignificance: isNrhp
        ? ["National", "State", "Local"]
        : isRthl
        ? ["State", "Local"]
        : ["Local"],
      integrity: Array.from(integritySet),
      integrityNotes,
      individuallyEligible,
      withinPotentialDistrict,
      potentialDistrictName: distName || additionSubdivision || props.neighborhood || "",
      isContributing,
      priority,
      priorityExplanation: priorityReasons.join("; "),
      priorDocumentationAvailable: priorDocDetailsList.length > 0 ? "Yes" : "Not known",
      priorDocumentationTypes: Array.from(priorDocTypes),
      priorDocumentationDetails: priorDocDetailsList.join(" | "),
    },
  };
}

function renderCheckboxGroupHtml(options, selectedValues = [], groupName = "") {
  const flatVals = (Array.isArray(selectedValues) ? selectedValues.flat() : [selectedValues]).filter(
    Boolean
  );
  const selSet = new Set(flatVals);
  return options
    .map((opt) => {
      const val = typeof opt === "string" ? opt : opt.code;
      const label = typeof opt === "string" ? opt : `${opt.code} - ${opt.description}`;
      const checked = selSet.has(val) ? "checked" : "";
      return `<label class="thc-cb-label">
        <input type="checkbox" class="thc-cb-input" data-thc-group="${escapeHtml(
          groupName
        )}" value="${escapeHtml(val)}" ${checked} />
        <span>${escapeHtml(label)}</span>
      </label>`;
    })
    .join("");
}

/**
 * Render the complete 3-Section Official Texas Historical Commission (THC)
 * Historic Resources Survey Form HTML for `#dossier-modal-body`.
 */
export function renderThcSurveyFormHtml(model, options = {}) {
  const s1 = model.section1 || {};
  const s2 = model.section2 || {};
  const s3 = model.section3 || {};
  const st = s2.structural || {};
  const qrSvg = options.qrSvg || "";

  const renderSectionHeader = (sectionNum, sectionTitle) => {
    const bindAttr = (field) =>
      sectionNum === 1 ? `data-thc-field="${field}"` : `data-thc-repeat-field="${field}" readonly`;
    return `
    <header class="thc-page-header">
      <div class="thc-agency-banner">
        <div>
          <div class="thc-agency-name">TEXAS HISTORICAL COMMISSION</div>
          <div class="thc-form-title">HISTORIC RESOURCES SURVEY FORM</div>
        </div>
        <div class="thc-section-pill">
          <span class="thc-sec-num">SECTION ${sectionNum}</span>
          <span class="thc-sec-sub">${escapeHtml(sectionTitle)}</span>
        </div>
      </div>
      <div class="thc-meta-bar">
        <div class="thc-meta-cell thc-meta-wide">
          <span class="thc-k">PROJECT #</span>
          <input type="text" class="thc-line-input" ${bindAttr("projectNumber")} value="${escapeHtml(
            model.projectNumber
          )}" />
        </div>
        <div class="thc-meta-cell">
          <span class="thc-k">Local ID / HCAD</span>
          <input type="text" class="thc-line-input mono" ${bindAttr("localId")} value="${escapeHtml(
            model.localId
          )}" />
        </div>
        <div class="thc-meta-cell">
          <span class="thc-k">County</span>
          <input type="text" class="thc-line-input" ${bindAttr("county")} value="${escapeHtml(
            model.county
          )}" />
        </div>
        <div class="thc-meta-cell">
          <span class="thc-k">City</span>
          <input type="text" class="thc-line-input" ${bindAttr("city")} value="${escapeHtml(
            model.city
          )}" />
        </div>
        <div class="thc-meta-cell thc-meta-wide">
          <span class="thc-k">Address</span>
          <input type="text" class="thc-line-input thc-bold-input" ${bindAttr("address")} value="${escapeHtml(
            model.address
          )}" />
        </div>
      </div>
    </header>
  `;
  };

  return `
    <article class="dossier-sheet thc-survey-sheet" id="printable-thc-survey-sheet" data-local-id="${escapeHtml(
      model.localId
    )}">
      <!-- ==================== PAGE 1: SECTION 1 ==================== -->
      <section class="thc-form-page" data-thc-page="1">
        ${renderSectionHeader(1, "Basic Inventory Information")}

        <div class="thc-grid-2col">
          <div class="thc-field-box">
            <span class="thc-k">Current Name</span>
            <input type="text" class="thc-line-input" data-thc-field="section1.currentName" value="${escapeHtml(
              s1.currentName
            )}" />
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Historic Name</span>
            <input type="text" class="thc-line-input" data-thc-field="section1.historicName" value="${escapeHtml(
              s1.historicName
            )}" />
          </div>
        </div>

        <div class="thc-grid-owner">
          <div class="thc-field-box">
            <span class="thc-k">Owner / Address ${
              s1.isConfidential ? "(Protected by TX Tax Code Sec. 25.025)" : ""
            }</span>
            <input type="text" class="thc-line-input" data-thc-field="section1.ownerName" value="${escapeHtml(
              s1.ownerName
            )}" />
            <div class="thc-subline-muted">${escapeHtml(
              `${s1.ownerAddress}, ${s1.ownerCity}, ${s1.ownerState} ${s1.ownerZip}`.trim()
            )}</div>
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Ownership Status</span>
            <div class="thc-cb-row">
              ${renderCheckboxGroupHtml(
                ["PRIVATE", "PUBLIC"],
                [s1.ownershipStatus || "PRIVATE"],
                "section1.ownershipStatus"
              )}
            </div>
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Geographic Location (WGS84)</span>
            <div class="thc-latlng-row mono">
              <span>Lat: <strong>${escapeHtml(s1.latitude)}</strong></span>
              <span>Lng: <strong>${escapeHtml(s1.longitude)}</strong></span>
            </div>
            <div class="thc-subline-muted mono">${escapeHtml(s1.onlineMapUrl)}</div>
          </div>
        </div>

        <div class="thc-grid-3col">
          <div class="thc-field-box">
            <span class="thc-k">Legal Description (Lot / Block)</span>
            <input type="text" class="thc-line-input" data-thc-field="section1.legalDescription" value="${escapeHtml(
              s1.legalDescription
            )}" />
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Addition / Subdivision</span>
            <input type="text" class="thc-line-input" data-thc-field="section1.additionSubdivision" value="${escapeHtml(
              s1.additionSubdivision
            )}" />
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Platted Year &amp; Appraisal ID</span>
            <input type="text" class="thc-line-input mono" data-thc-field="section1.subdivisionYear" placeholder="Platted year" value="${escapeHtml(
              s1.subdivisionYear
            )}" />
            <div class="thc-subline-muted mono">HCAD: ${escapeHtml(model.appraisalDistrictId)}</div>
          </div>
        </div>

        <div class="thc-field-box">
          <span class="thc-k">Property Type</span>
          <div class="thc-cb-row">
            ${renderCheckboxGroupHtml(
              THC_PROPERTY_TYPES,
              [s1.propertyType || "Building"],
              "section1.propertyType"
            )}
          </div>
        </div>

        <div class="thc-field-box">
          <span class="thc-k">Current Designations</span>
          <div class="thc-cb-grid thc-cb-grid-5">
            ${renderCheckboxGroupHtml(
              THC_DESIGNATION_TYPES,
              s1.currentDesignations,
              "section1.currentDesignations"
            )}
          </div>
          <div class="thc-inline-subrow">
            <span>If within NR / Local District, is property contributing?</span>
            ${renderCheckboxGroupHtml(
              ["Yes", "No"],
              s1.nrDistrictContributing ? [s1.nrDistrictContributing] : [],
              "section1.nrDistrictContributing"
            )}
            <span class="thc-inline-label">Designation Details:</span>
            <input type="text" class="thc-line-input" data-thc-field="section1.otherDesignationText" value="${escapeHtml(
              s1.otherDesignationText
            )}" />
          </div>
        </div>

        <div class="thc-grid-3col">
          <div class="thc-field-box">
            <span class="thc-k">Architect</span>
            <input type="text" class="thc-line-input" data-thc-field="section1.architect" value="${escapeHtml(
              s1.architect
            )}" />
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Builder / Contractor</span>
            <input type="text" class="thc-line-input" data-thc-field="section1.builder" value="${escapeHtml(
              s1.builder
            )}" />
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Construction Date &amp; Provenance</span>
            <div class="thc-inline-subrow">
              <input type="text" class="thc-line-input mono thc-year-input" data-thc-field="section1.constructionDate" value="${escapeHtml(
                s1.constructionDate
              )}" />
              ${renderCheckboxGroupHtml(
                ["Actual", "Estimated"],
                [s1.constructionDateType || "Estimated"],
                "section1.constructionDateType"
              )}
            </div>
            <input type="text" class="thc-line-input thc-source-input" data-thc-field="section1.constructionDateSource" placeholder="Date source" value="${escapeHtml(
              s1.constructionDateSource
            )}" />
          </div>
        </div>

        <div class="thc-grid-2col">
          <div class="thc-field-box">
            <span class="thc-k">Function - Current Use (${escapeHtml(s1.currentUseLabel)})</span>
            <div class="thc-cb-grid thc-cb-grid-3">
              ${renderCheckboxGroupHtml(
                THC_FUNCTION_CATEGORIES,
                s1.currentUseCategories,
                "section1.currentUseCategories"
              )}
            </div>
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Function - Historic Use (${escapeHtml(s1.historicUseLabel)})</span>
            <div class="thc-cb-grid thc-cb-grid-3">
              ${renderCheckboxGroupHtml(
                THC_FUNCTION_CATEGORIES,
                s1.historicUseCategories,
                "section1.historicUseCategories"
              )}
            </div>
          </div>
        </div>

        <div class="thc-image-and-recorder-row">
          <div class="thc-recorder-col">
            <div class="thc-k">Image &amp; Survey Recording Information</div>
            <div class="thc-kv-pair">
              <label>Recorded by:</label>
              <input type="text" class="thc-line-input" data-thc-field="section1.recordedBy" value="${escapeHtml(
                s1.recordedBy
              )}" />
            </div>
            <div class="thc-kv-pair">
              <label>Date Recorded:</label>
              <input type="text" class="thc-line-input mono" data-thc-field="section1.dateRecorded" value="${escapeHtml(
                s1.dateRecorded
              )}" />
            </div>
            <div class="thc-kv-pair">
              <label>Primary Image ID:</label>
              <input type="text" class="thc-line-input mono" data-thc-field="section1.primaryImageId" value="${escapeHtml(
                s1.primaryImageId
              )}" />
            </div>
            <div class="thc-kv-pair">
              <label>Photo Data ID# Range:</label>
              <input type="text" class="thc-line-input mono" data-thc-field="section1.photoDataRange" value="${escapeHtml(
                s1.photoDataRange
              )}" />
            </div>
          </div>
          <div class="thc-image-preview-col">
            ${
              s1.primaryPhoto && s1.primaryPhoto.url
                ? `<figure class="thc-photo-figure">
                    <img src="${escapeHtml(s1.primaryPhoto.url)}" alt="${escapeHtml(
                    s1.currentName
                  )}" loading="eager" />
                    <figcaption>${escapeHtml(s1.primaryPhoto.caption)} (${escapeHtml(
                    s1.primaryPhoto.credit
                  )})</figcaption>
                  </figure>`
                : `<div class="thc-photo-placeholder">
                    <div class="thc-photo-placeholder-title">PRIMARY RESOURCE IMAGE</div>
                    <div class="mono">${escapeHtml(s1.primaryImageId)}</div>
                    <div class="thc-subline-muted">Attach field photograph or use Street View / Archival view in Atlas</div>
                  </div>`
            }
          </div>
        </div>
      </section>

      <!-- ==================== PAGE 2: SECTION 2 ==================== -->
      <section class="thc-form-page thc-page-break" data-thc-page="2">
        ${renderSectionHeader(2, "Architectural Description")}

        <div class="thc-field-box">
          <span class="thc-k">General Architectural Description (Synthesized Narrative)</span>
          <textarea class="thc-textarea" rows="5" data-thc-field="section2.generalArchitecturalDescription">${escapeHtml(
            s2.generalArchitecturalDescription
          )}</textarea>
        </div>

        <div class="thc-grid-2col">
          <div class="thc-field-box">
            <label class="thc-cb-label thc-k-cb">
              <input type="checkbox" class="thc-cb-input" data-thc-field="section2.additionsModificationsHas" ${
                s2.additionsModificationsHas ? "checked" : ""
              } />
              <span>Additions, modifications, specify dates (COH Permits &amp; HCAD Roll):</span>
            </label>
            <textarea class="thc-textarea" rows="3" data-thc-field="section2.additionsModificationsText">${escapeHtml(
              s2.additionsModificationsText
            )}</textarea>
          </div>
          <div class="thc-field-box">
            <label class="thc-cb-label thc-k-cb">
              <input type="checkbox" class="thc-cb-input" data-thc-field="section2.relocatedHas" ${
                s2.relocatedHas ? "checked" : ""
              } />
              <span>Relocated, specify date, former location and information of interest:</span>
            </label>
            <textarea class="thc-textarea" rows="3" data-thc-field="section2.relocatedText" placeholder="Remaining on original site (no relocation record indexed).">${escapeHtml(
              s2.relocatedText || ""
            )}</textarea>
          </div>
        </div>

        <div class="thc-field-box">
          <span class="thc-k">Stylistic Influence(s) (Official THC 30-Style Taxonomy • Atlas Style: ${escapeHtml(
            s2.displayStyle || "Vernacular"
          )})</span>
          <div class="thc-cb-grid thc-cb-grid-5">
            ${renderCheckboxGroupHtml(
              THC_STYLISTIC_INFLUENCES,
              s2.stylisticInfluences,
              "section2.stylisticInfluences"
            )}
          </div>
          ${
            s2.otherStyleText
              ? `<div class="thc-inline-subrow">
                  <span class="thc-inline-label">Other Style Details:</span>
                  <input type="text" class="thc-line-input" data-thc-field="section2.otherStyleText" value="${escapeHtml(
                    s2.otherStyleText
                  )}" />
                </div>`
              : ""
          }
        </div>

        <div class="thc-subbanner">STRUCTURAL DETAILS</div>

        <div class="thc-grid-2col">
          <div class="thc-field-box">
            <span class="thc-k">Roof Form</span>
            <div class="thc-cb-grid thc-cb-grid-4">
              ${renderCheckboxGroupHtml(THC_ROOF_FORMS, st.roofForms, "structural.roofForms")}
            </div>
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Roof Materials</span>
            <div class="thc-cb-grid thc-cb-grid-3">
              ${renderCheckboxGroupHtml(
                THC_ROOF_MATERIALS,
                st.roofMaterials,
                "structural.roofMaterials"
              )}
            </div>
          </div>
        </div>

        <div class="thc-field-box">
          <span class="thc-k">Wall Materials</span>
          <div class="thc-cb-grid thc-cb-grid-5">
            ${renderCheckboxGroupHtml(
              THC_WALL_MATERIALS,
              st.wallMaterials,
              "structural.wallMaterials"
            )}
          </div>
        </div>

        <div class="thc-grid-2col">
          <div class="thc-field-box">
            <span class="thc-k">Windows</span>
            <div class="thc-cb-grid thc-cb-grid-3">
              ${renderCheckboxGroupHtml(THC_WINDOWS, st.windows, "structural.windows")}
            </div>
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Doors (Primary Entrance)</span>
            <div class="thc-cb-grid thc-cb-grid-3">
              ${renderCheckboxGroupHtml(THC_DOORS, st.doors, "structural.doors")}
            </div>
          </div>
        </div>

        <div class="thc-grid-2col">
          <div class="thc-field-box">
            <span class="thc-k">Plan</span>
            <div class="thc-cb-grid thc-cb-grid-4">
              ${renderCheckboxGroupHtml(THC_PLANS, st.plans, "structural.plans")}
            </div>
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Chimneys (Specify #: ${escapeHtml(st.chimneysCount || "0")})</span>
            <div class="thc-cb-grid thc-cb-grid-4">
              ${renderCheckboxGroupHtml(
                THC_CHIMNEY_FEATURES,
                st.chimneys,
                "structural.chimneys"
              )}
            </div>
          </div>
        </div>

        <div class="thc-field-box">
          <span class="thc-k">Porches / Canopies</span>
          <div class="thc-inline-subrow">
            <strong>Form:</strong>
            ${renderCheckboxGroupHtml(THC_PORCH_FORMS, st.porchForms, "structural.porchForms")}
          </div>
          <div class="thc-cb-grid thc-cb-grid-4" style="margin-top:4px;">
            ${renderCheckboxGroupHtml(
              THC_PORCH_SUPPORTS,
              st.porchSupports,
              "structural.porchSupports"
            )}
          </div>
          <div class="thc-inline-subrow" style="margin-top:4px;">
            <strong>Materials:</strong>
            ${renderCheckboxGroupHtml(
              THC_PORCH_MATERIALS,
              st.porchMaterials,
              "structural.porchMaterials"
            )}
            <span class="thc-inline-label"># of Stories:</span>
            <input type="text" class="thc-line-input mono" style="max-width:56px;" data-thc-field="structural.stories" value="${escapeHtml(
              st.stories
            )}" />
            <span class="thc-inline-label">Basement:</span>
            ${renderCheckboxGroupHtml(
              THC_BASEMENT_OPTIONS,
              [st.basement || "None"],
              "structural.basement"
            )}
          </div>
        </div>

        <div class="thc-grid-2col">
          <div class="thc-field-box">
            <span class="thc-k">Ancillary Buildings (Specify # and type)</span>
            <div class="thc-inline-subrow">
              <span>Garage: <strong>${escapeHtml(st.ancillaryGarage || "None")}</strong></span>
              <span>Shed: <strong>${escapeHtml(st.ancillaryShed || "None")}</strong></span>
              <span>Other: <strong>${escapeHtml(st.ancillaryOther || "None")}</strong></span>
            </div>
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Landscape / Site Features</span>
            <div class="thc-cb-grid thc-cb-grid-3">
              ${renderCheckboxGroupHtml(
                THC_LANDSCAPE_FEATURES,
                st.landscapeFeatures,
                "structural.landscapeFeatures"
              )}
            </div>
            <div class="thc-inline-subrow" style="margin-top:4px;">
              <strong>Materials:</strong>
              ${renderCheckboxGroupHtml(
                THC_LANDSCAPE_MATERIALS,
                st.landscapeMaterials,
                "structural.landscapeMaterials"
              )}
            </div>
            <div class="thc-subline-muted">${escapeHtml(st.landscapeNotes)}</div>
          </div>
        </div>
      </section>

      <!-- ==================== PAGE 3: SECTION 3 ==================== -->
      <section class="thc-form-page thc-page-break" data-thc-page="3">
        ${renderSectionHeader(3, "Historical Information & NRHP / Local Eligibility")}

        <div class="thc-field-box">
          <span class="thc-k">Associated Historical Context</span>
          <div class="thc-cb-grid thc-cb-grid-4">
            ${renderCheckboxGroupHtml(
              THC_HISTORICAL_CONTEXTS,
              s3.historicalContexts,
              "section3.historicalContexts"
            )}
          </div>
        </div>

        <div class="thc-field-box">
          <span class="thc-k">Historical &amp; Archival Research Notes (Multi-Source Synthesis)</span>
          <textarea class="thc-textarea" rows="6" data-thc-field="section3.historyNotes">${escapeHtml(
            s3.historyNotes
          )}</textarea>
        </div>

        <div class="thc-field-box">
          <span class="thc-k">Applicable National Register (NR) &amp; HAHC Criteria</span>
          <div class="thc-criteria-list">
            ${THC_NR_CRITERIA.map((c) => {
              const checked = (s3.nrCriteria || []).includes(c.code) ? "checked" : "";
              return `<label class="thc-cb-label thc-criterion-item">
                <input type="checkbox" class="thc-cb-input" data-thc-group="section3.nrCriteria" value="${escapeHtml(
                  c.code
                )}" ${checked} />
                <span><strong>Criterion ${escapeHtml(c.code)}:</strong> ${escapeHtml(
                c.description
              )}</span>
              </label>`;
            }).join("")}
          </div>
        </div>

        <div class="thc-grid-3col">
          <div class="thc-field-box">
            <span class="thc-k">Areas of Significance</span>
            <input type="text" class="thc-line-input" data-thc-field="section3.areasOfSignificance" value="${escapeHtml(
              s3.areasOfSignificance
            )}" />
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Period(s) of Significance</span>
            <input type="text" class="thc-line-input mono" data-thc-field="section3.periodsOfSignificance" value="${escapeHtml(
              s3.periodsOfSignificance
            )}" />
          </div>
          <div class="thc-field-box">
            <span class="thc-k">Level of Significance</span>
            <div class="thc-cb-row">
              ${renderCheckboxGroupHtml(
                THC_SIGNIFICANCE_LEVELS,
                s3.levelOfSignificance,
                "section3.levelOfSignificance"
              )}
            </div>
          </div>
        </div>

        <div class="thc-field-box">
          <span class="thc-k">Integrity (7 Aspects)</span>
          <div class="thc-cb-grid thc-cb-grid-4">
            ${renderCheckboxGroupHtml(
              THC_INTEGRITY_ASPECTS,
              s3.integrity,
              "section3.integrity"
            )}
          </div>
          <div class="thc-inline-subrow" style="margin-top:4px;">
            <span class="thc-inline-label">Integrity Notes:</span>
            <input type="text" class="thc-line-input" data-thc-field="section3.integrityNotes" value="${escapeHtml(
              s3.integrityNotes
            )}" />
          </div>
        </div>

        <div class="thc-grid-2col">
          <div class="thc-field-box">
            <div class="thc-elig-row">
              <strong>Individually Eligible?</strong>
              ${renderCheckboxGroupHtml(
                ["Yes", "No", "Undetermined"],
                [s3.individuallyEligible || "Undetermined"],
                "section3.individuallyEligible"
              )}
            </div>
            <div class="thc-elig-row">
              <strong>Within Potential NR / Local District?</strong>
              ${renderCheckboxGroupHtml(
                ["Yes", "No", "Undetermined"],
                [s3.withinPotentialDistrict || "Undetermined"],
                "section3.withinPotentialDistrict"
              )}
            </div>
            <div class="thc-elig-row">
              <strong>Is Property Contributing?</strong>
              ${renderCheckboxGroupHtml(
                ["Yes", "No", "Undetermined"],
                [s3.isContributing || "Yes"],
                "section3.isContributing"
              )}
            </div>
          </div>

          <div class="thc-field-box">
            <div class="thc-elig-row">
              <strong>Priority (see manual):</strong>
              ${renderCheckboxGroupHtml(
                THC_PRIORITY_LEVELS,
                [s3.priority || "Medium"],
                "section3.priority"
              )}
            </div>
            <div class="thc-inline-subrow">
              <span class="thc-inline-label">Explain:</span>
              <input type="text" class="thc-line-input" data-thc-field="section3.priorityExplanation" value="${escapeHtml(
                s3.priorityExplanation
              )}" />
            </div>
            <div class="thc-elig-row" style="margin-top:4px;">
              <strong>Prior Documentation Available?</strong>
              ${renderCheckboxGroupHtml(
                ["Yes", "No", "Not known"],
                [s3.priorDocumentationAvailable || "Yes"],
                "section3.priorDocumentationAvailable"
              )}
              <span>Type:</span>
              ${renderCheckboxGroupHtml(
                ["HABS", "Survey", "Other"],
                s3.priorDocumentationTypes,
                "section3.priorDocumentationTypes"
              )}
            </div>
            <div class="thc-subline-muted">${escapeHtml(s3.priorDocumentationDetails)}</div>
          </div>
        </div>

        <footer class="dossier-sheet-footer thc-form-footer">
          <div class="dossier-footer-meta">
            <div><strong>Texas Historical Commission Historic Resources Survey Form (version ${escapeHtml(
              model.formVersion
            )})</strong> • Pre-populated by The Houston Building Atlas</div>
            <div>Questions? History Programs Division, Texas Historical Commission • 512/463-5853 • history@thc.state.tx.us</div>
            ${
              model.shareUrl
                ? `<div class="dossier-share-url mono">${escapeHtml(model.shareUrl)}</div>`
                : ""
            }
          </div>
          ${
            qrSvg
              ? `<div class="dossier-qr-box">
                  ${qrSvg}
                  <span class="dossier-qr-caption">Interactive THC Form</span>
                </div>`
              : ""
          }
        </footer>
      </section>
    </article>
  `;
}

/**
 * Read back any interactive edits made by the user in `#printable-thc-survey-sheet`
 * and return an updated model snapshot for TSV / JSON export.
 */
export function collectEditedThcSurveyFormModel(baseModel, rootEl) {
  if (!baseModel || !rootEl || typeof rootEl.querySelectorAll !== "function") {
    return baseModel;
  }
  const cloned = JSON.parse(JSON.stringify(baseModel));
  const normalizeParts = (pathStr) => {
    const parts = String(pathStr || "").split(".").filter(Boolean);
    return parts[0] === "structural" ? ["section2", ...parts] : parts;
  };
  const getByPath = (obj, pathStr) =>
    normalizeParts(pathStr).reduce((acc, k) => acc?.[k], obj);
  const setByPath = (obj, pathStr, val) => {
    const parts = normalizeParts(pathStr);
    if (!parts.length) return;
    const last = parts.pop();
    const target = parts.reduce((acc, k) => acc?.[k], obj);
    if (target && typeof target === "object") target[last] = val;
  };

  rootEl.querySelectorAll("[data-thc-field]").forEach((el) => {
    const key = el.getAttribute("data-thc-field");
    if (!key) return;
    if (el.type === "checkbox") {
      setByPath(cloned, key, Boolean(el.checked));
    } else {
      setByPath(cloned, key, String(el.value ?? "").trim());
    }
  });

  const groups = new Map();
  rootEl.querySelectorAll("[data-thc-group]").forEach((cb) => {
    const g = cb.getAttribute("data-thc-group");
    if (!g) return;
    if (!groups.has(g)) groups.set(g, []);
    if (cb.checked) groups.get(g).push(cb.value);
  });
  for (const [g, vals] of groups.entries()) {
    const origVal = getByPath(baseModel, g);
    setByPath(cloned, g, Array.isArray(origVal) ? vals : vals[0] || "");
  }
  return cloned;
}

/**
 * Serialize a THC Survey Form model into a 2-row (headers + values) TSV string
 * ready to paste directly into Google Sheets or Excel.
 */
export function serializeThcSurveyFormToTsv(model) {
  const s1 = model?.section1 || {};
  const s2 = model?.section2 || {};
  const s3 = model?.section3 || {};
  const st = s2.structural || {};
  const fmtList = (v, sep = "; ", fallback = "") =>
    Array.isArray(v) ? v.join(sep) : String(v || fallback);
  const cols = [
    ["project_number", model?.projectNumber || ""],
    ["local_id", model?.localId || ""],
    ["appraisal_district_id", model?.appraisalDistrictId || ""],
    ["county", model?.county || "Harris"],
    ["city", model?.city || "Houston"],
    ["address", model?.address || ""],
    ["current_name", s1.currentName || ""],
    ["historic_name", s1.historicName || ""],
    ["owner_name", s1.ownerName || ""],
    ["ownership_status", fmtList(s1.ownershipStatus, ", ")],
    ["latitude", s1.latitude || ""],
    ["longitude", s1.longitude || ""],
    ["legal_description", s1.legalDescription || ""],
    ["addition_subdivision", s1.additionSubdivision || ""],
    ["subdivision_year", s1.subdivisionYear || ""],
    ["property_type", fmtList(s1.propertyType, ", ", "Building")],
    ["current_designations", fmtList(s1.currentDesignations)],
    ["architect", s1.architect || ""],
    ["builder", s1.builder || ""],
    ["construction_date", s1.constructionDate || ""],
    ["construction_date_type", fmtList(s1.constructionDateType, ", ")],
    ["construction_date_source", s1.constructionDateSource || ""],
    ["current_use", fmtList(s1.currentUseCategories)],
    ["historic_use", fmtList(s1.historicUseCategories)],
    ["primary_image_id", s1.primaryImageId || ""],
    ["recorded_by", s1.recordedBy || ""],
    ["date_recorded", s1.dateRecorded || ""],
    ["stylistic_influences", fmtList(s2.stylisticInfluences)],
    ["general_architectural_description", s2.generalArchitecturalDescription || ""],
    ["additions_modifications", s2.additionsModificationsText || ""],
    ["relocated", s2.relocatedText || ""],
    ["roof_form", fmtList(st.roofForms)],
    ["wall_materials", fmtList(st.wallMaterials)],
    ["windows", fmtList(st.windows)],
    ["plan", fmtList(st.plans)],
    ["stories", st.stories || "1"],
    ["historical_contexts", fmtList(s3.historicalContexts)],
    ["nr_criteria", fmtList(s3.nrCriteria, ", ")],
    ["integrity", fmtList(s3.integrity)],
    ["individually_eligible", fmtList(s3.individuallyEligible, ", ")],
    ["is_contributing", fmtList(s3.isContributing, ", ")],
    ["priority", fmtList(s3.priority, ", ")],
    ["priority_explanation", s3.priorityExplanation || ""],
    ["history_notes", String(s3.historyNotes || "").replace(/\s*\n\s*/g, " | ")],
  ];
  const sanitizeCell = (v) => {
    const clean = String(v ?? "").replace(/[\t\r\n]+/g, " ").trim();
    if (/^[=+\-@]/.test(clean) && !/^[+-]?\d+(\.\d+)?$/.test(clean)) {
      return `'${clean}`;
    }
    return clean;
  };
  const headerLine = cols.map(([k]) => k).join("\t");
  const valueLine = cols.map(([, v]) => sanitizeCell(v)).join("\t");
  return `${headerLine}\n${valueLine}`;
}
