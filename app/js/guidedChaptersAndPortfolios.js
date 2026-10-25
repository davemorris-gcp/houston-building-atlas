/**
 * Guided Citywide Visual Chapters ("How Houston Was Built") and
 * Architect / Builder / Architectural Style Map Portfolio Engine (Priority #4).
 */

export const CITY_HISTORY_CHAPTERS = [
  {
    id: "bayous_and_two_grids",
    chapterNum: 1,
    era: "1836-1860",
    kicker: "Chapter 1 of 6 · Founding Geography",
    title: "Bayous, Buried Gullies & the Two Street Grids",
    narrative:
      "In August 1836, Augustus and John Kirby Allen commissioned Gail and Thomas H. Borden to plat 62 blocks at the navigable headwaters of Buffalo Bayou near White Oak Bayou. Because the Borden Survey aligned perpendicular to Buffalo Bayou (-34.65 degrees off cardinal North), Downtown streets tilt diagonally, whereas the January 28, 1840 Republic of Texas 3-mile square charter boundary shifted outward surveys to cardinal North-South, creating the street-grid bends still visible around Downtown.",
    keyTakeaway:
      "Turn on Historical Waterways & Gullies to see how buried ravines like Harris Gully, Slaughterpen Gully, and Pecore Gully shaped early bridge crossings and lot boundaries.",
    viewport: { lng: -95.3645, lat: 29.7608, zoom: 14.3, pitch: 0 },
    statePatch: {
      colorMode: "year_built",
      renderMode: "buildings",
      minYear: 1836,
      maxYear: 1899,
      selectedDecade: "all",
      historicalSnapshotMode: false,
      syncAnnexationToTime: true,
      historicMapId: "wood_1869",
      historicMapOpacity: 74,
      layers: {
        annexations: true,
        historicalWaterways: true,
        lostHouston: true,
        landmarks: true,
        historicMap: true,
        goodBrickAwards: false,
        historicDistricts: false,
        heritageDistricts: false,
        permitWatch: false,
      },
    },
    sites: [
      {
        label: "Kellum-Noble House (1847)",
        featureId: "0400030000014#shp_44632292_the_kellum_noble_house",
        hcadNum: "0400030000013",
        lng: -95.37226,
        lat: 29.75891,
        zoom: 17.4,
      },
      {
        label: "1910 Courthouse Square",
        hcadNum: "0010310000001",
        lng: -95.35973,
        lat: 29.76109,
        zoom: 17.2,
      },
      {
        label: "First Ward / Elder St (1866 Wards)",
        hcadNum: "0052380000026",
        lng: -95.368,
        lat: 29.76883,
        zoom: 16.5,
      },
    ],
  },
  {
    id: "railroads_and_aldermanic_wards",
    chapterNum: 2,
    era: "1853-1905",
    kicker: "Chapter 2 of 6 · Rail Hub & Ward System",
    title: "Railroads, Cotton Presses & the Six Aldermanic Wards",
    narrative:
      "Starting with the Buffalo Bayou, Brazos & Colorado Railway in 1853, Houston advertised itself as 'Where Seventeen Railroads Meet the Sea.' Trunk rail corridors, yards, and bayou meanders defined Houston's historic First through Sixth Aldermanic Wards until the 1905 Progressive-era Commission Charter abolished geographic ward representation in favor of at-large commissioners.",
    keyTakeaway:
      "Although municipal wards ceased to exist legally after 1905, ward identities endured across Old Sixth Ward, Fifth Ward, Third Ward, Fourth Ward, and Second Ward.",
    viewport: { lng: -95.3618, lat: 29.7642, zoom: 13.4, pitch: 0 },
    statePatch: {
      colorMode: "year_built",
      renderMode: "buildings",
      minYear: 1836,
      maxYear: 1915,
      selectedDecade: "all",
      historicalSnapshotMode: false,
      wardEra: 1903,
      layers: {
        historicWards: true,
        historicalRailroads: true,
        historicalWaterways: true,
        landmarks: true,
        historicDistricts: true,
        lostHouston: true,
        historicMap: false,
        annexations: false,
        goodBrickAwards: false,
        permitWatch: false,
      },
    },
    sites: [
      {
        label: "Union Station (1911)",
        hcadNum: "0010490000004",
        lng: -95.3574,
        lat: 29.75891,
        zoom: 16.8,
      },
      {
        label: "Old Sixth Ward District",
        boundaryLayer: "historicDistricts",
        boundaryName: "Old Sixth Ward",
        lng: -95.3778,
        lat: 29.7676,
        zoom: 15.6,
      },
      {
        label: "Antioch Baptist (1875)",
        hcadNum: "0321670000022",
        lng: -95.37209,
        lat: 29.75627,
        zoom: 17.3,
      },
    ],
  },
  {
    id: "streetcar_suburbs_and_deeds",
    chapterNum: 3,
    era: "1891-1938",
    kicker: "Chapter 3 of 6 · Streetcars & Private Covenants",
    title: "Streetcar Suburbs & Private Deed Restrictions",
    narrative:
      "After Houston electrified its streetcar network in June 1891, land companies platted leafy residential suburbs along streetcar lines: Omaha & South Texas Land Co. in Houston Heights (1891), William A. Wilson in Woodland Heights (1907) and Eastwood (1913), J. W. Link's Houston Land Corp. in Montrose (1911), and Russell Brown's Norhill (1920). In the absence of municipal zoning, developers relied on private deed covenants to govern land use, setbacks, and architectural scale.",
    keyTakeaway:
      "After 1965, Texas Legislature authorization allowed the City of Houston Legal Department to enforce residential use, setback, and lot-size deed restrictions directly.",
    viewport: { lng: -95.3915, lat: 29.7765, zoom: 13.0, pitch: 0 },
    statePatch: {
      colorMode: "year_built",
      renderMode: "buildings",
      minYear: 1890,
      maxYear: 1939,
      selectedDecade: "all",
      historicalSnapshotMode: false,
      layers: {
        historicalRailroads: true,
        landUseProtections: true,
        historicDistricts: true,
        goodBrickAwards: true,
        landmarks: false,
        historicWards: false,
        historicMap: false,
        annexations: false,
        lostHouston: false,
        permitWatch: false,
      },
    },
    sites: [
      {
        label: "Lund House, Heights (1896)",
        featureId: "bld_000475",
        hcadNum: "0210200000029",
        lng: -95.3948,
        lat: 29.7799,
        zoom: 17.3,
      },
      {
        label: "1127 Key St, Norhill (1928)",
        hcadNum: "0621100000014",
        lng: -95.3872,
        lat: 29.79173,
        zoom: 17.4,
      },
      {
        label: "Courtlandt Place (1909)",
        boundaryLayer: "historicDistricts",
        boundaryName: "Courtlandt Place",
        lng: -95.3845,
        lat: 29.7436,
        zoom: 16.4,
      },
    ],
  },
  {
    id: "red_book_redlining_and_freeways",
    chapterNum: 4,
    era: "1915-1975",
    kicker: "Chapter 4 of 6 · Black Heritage, Redlining & Erasure",
    title: "1915 Black Houston, 1937 Redlining & Freeway Trenches",
    narrative:
      "Published for the 50th anniversary of Juneteenth, the 1915 Red Book of Houston documented 970 Black churches, schools, businesses, physicians, and civic leaders across Freedmen's Town, Third Ward, and Fifth Ward. Twenty-two years later, the 1937 federal HOLC security map placed 77.4% of graded Red Book sites inside Grade D ('Hazardous') zones, starving historic Black neighborhoods of home-rehabilitation credit before postwar I-45, I-10, and US-59 freeway rights-of-way demolished 63.7% of those 1915 parcels.",
    keyTakeaway:
      "Even decades later, 1937 Grade D zones hold only 8 Chapter 42 lot-size/setback-protected blocks compared with 153 blocks in Grade A and 192 blocks in Grade B.",
    viewport: { lng: -95.3748, lat: 29.7562, zoom: 13.7, pitch: 0 },
    statePatch: {
      colorMode: "year_built",
      renderMode: "buildings",
      minYear: 1836,
      maxYear: 2026,
      selectedDecade: "all",
      historicalSnapshotMode: false,
      redBookCategory: "all",
      holcGradeFilter: "all",
      layers: {
        redBook1915: true,
        holc1937: true,
        lostHouston: true,
        heritageDistricts: true,
        landmarks: true,
        historicMap: false,
        historicDistricts: false,
        goodBrickAwards: false,
        permitWatch: false,
      },
    },
    sites: [
      {
        label: "Gregory School (1926)",
        hcadNum: "0050180000019",
        lng: -95.38044,
        lat: 29.75415,
        zoom: 17.2,
      },
      {
        label: "1937 HOLC Zone D-8",
        boundaryLayer: "holc1937",
        boundaryId: "holc_1937_d8",
        lng: -95.37826,
        lat: 29.75857,
        zoom: 14.3,
      },
      {
        label: "Antioch Baptist (1915 Red Book)",
        hcadNum: "0321670000022",
        lng: -95.37209,
        lat: 29.75627,
        zoom: 17.3,
      },
    ],
  },
  {
    id: "annexation_spokes_and_space_city",
    chapterNum: 5,
    era: "1945-2020",
    kicker: "Chapter 5 of 6 · Postwar Expansion & Space City",
    title: "Annexation Spokes, Ship Channel Industry & Space City",
    narrative:
      "Between 1948 and 1965, aggressive municipal annexations and the 1963 Municipal Annexation Act (coupled with Ordinance 63-1219's 10-foot-wide highway strips) expanded Houston past 600 square miles, preventing suburban encirclement while incorporating postwar Modernist neighborhoods like Glenbrook Valley, the Texas Medical Center, the 1965 Astrodome, and Clear Lake aerospace campuses.",
    keyTakeaway:
      "Glenbrook Valley (1953-1962) became Texas's first post-World War II Mid-Century Modern municipal Historic District in 2011.",
    viewport: { lng: -95.355, lat: 29.718, zoom: 11.1, pitch: 0 },
    statePatch: {
      colorMode: "year_built",
      renderMode: "buildings",
      minYear: 1836,
      maxYear: 2026,
      selectedDecade: "all",
      historicalSnapshotMode: false,
      syncAnnexationToTime: true,
      showAnnexationSpokes: true,
      layers: {
        annexations: true,
        historicDistricts: true,
        nrhpDistricts: true,
        goodBrickAwards: true,
        landmarks: false,
        redBook1915: false,
        holc1937: false,
        lostHouston: false,
        permitWatch: false,
      },
    },
    sites: [
      {
        label: "NRG Astrodome (1965)",
        featureId: "osm_city_119420928#bldg_nrg_astrodome",
        lng: -95.40773,
        lat: 29.68487,
        zoom: 15.8,
      },
      {
        label: "Glenbrook Valley Historic Dist.",
        boundaryLayer: "historicDistricts",
        boundaryName: "Glenbrook Valley",
        lng: -95.2845,
        lat: 29.6752,
        zoom: 14.6,
      },
      {
        label: "Julia Ideson Building (1926)",
        hcadNum: "0011470000001",
        lng: -95.36927,
        lat: 29.75883,
        zoom: 17.2,
      },
    ],
  },
  {
    id: "preservation_without_zoning",
    chapterNum: 6,
    era: "1979-2026",
    kicker: "Chapter 6 of 6 · Modern Preservation Toolkit",
    title: "Preservation Without Zoning: How Houston Protects Places",
    narrative:
      "Because Houston voters rejected comprehensive municipal zoning in 1948, 1962, and 1993, historic preservation operates through a layered toolkit: Chapter 33 Protected Landmarks (PLMs) and 22 Historic Districts reviewed monthly by HAHC (strengthened in 2010-2015 after regular Landmarks outside districts remained subject to 90-day demolition waivers), Chapter 42 Minimum Lot Size / Building Line blocks, 45% combined State + Federal rehab tax credits, and early-warning Permit, Plat, and HAHC monitoring.",
    keyTakeaway:
      "Switch the Cartographic Lens to 'Historic Status' to distinguish permanent PLMs and Historic District buildings from vulnerable 90-day-waiver Landmarks and 60 surveyed Potential Historic Districts.",
    viewport: { lng: -95.3785, lat: 29.7665, zoom: 13.1, pitch: 0 },
    statePatch: {
      colorMode: "preservation_status",
      renderMode: "buildings",
      minYear: 1836,
      maxYear: 2026,
      selectedDecade: "all",
      historicalSnapshotMode: false,
      landmarkFilter: "all",
      hahcFilter: "all",
      layers: {
        permitWatch: true,
        platWatch: true,
        hahcTracker: true,
        potentialHistoricDistricts: true,
        landmarks: true,
        historicDistricts: true,
        goodBrickAwards: true,
        annexations: false,
        redBook1915: false,
        holc1937: false,
        lostHouston: false,
      },
    },
    sites: [
      {
        label: "Kirby Mansion (1894 · Active Watch)",
        hcadNum: "0152300000001",
        lng: -95.37474,
        lat: 29.75147,
        zoom: 17.3,
      },
      {
        label: "1119 Walling St (Oct '26 HAHC COA)",
        hcadNum: "0620820000016",
        lng: -95.387,
        lat: 29.80082,
        zoom: 17.3,
      },
      {
        label: "1021 Main St (First City Nat'l PLM)",
        hcadNum: "0011360000001",
        lng: -95.3649,
        lat: 29.75616,
        zoom: 17.1,
      },
    ],
  },
];

export const FEATURED_PORTFOLIO_PRESETS = [
  { kind: "architect", query: "Alfred C. Finn", label: "Alfred C. Finn" },
  { kind: "architect", query: "John Staub", label: "John F. Staub" },
  { kind: "architect", query: "Birdsall P. Briscoe", label: "Birdsall P. Briscoe" },
  { kind: "architect", query: "John S. Chase", label: "John S. Chase" },
  { kind: "architect", query: "Joseph Finger", label: "Joseph Finger" },
  { kind: "architect", query: "MacKie & Kamrath", label: "MacKie & Kamrath" },
  { kind: "architect", query: "Sanguinet & Staats", label: "Sanguinet & Staats" },
  { kind: "architect", query: "Russell Brown Company", label: "Russell Brown Co." },
  { kind: "architect", query: "Philip Johnson", label: "Philip Johnson" },
  { kind: "style", query: "Art Deco", label: "Art Deco Style" },
  { kind: "style", query: "Tudor Revival", label: "Tudor Revival Style" },
  { kind: "style", query: "Craftsman", label: "Craftsman Style" },
];

export const ARCHITECT_AND_STYLE_BIOGRAPHIES = {
  "alfred c. finn": {
    title: "Alfred Charles Finn, FAIA (1883-1964)",
    role: "Civic & Commercial Skyscraper Architect",
    summary:
      "Born in Bellville, Texas, Finn trained under Sanguinet & Staats before opening his Houston practice in 1913. Working closely with Jesse H. Jones, he shaped Houston's early-to-mid-20th-century skyline with the Gulf Building (1929), San Jacinto Monument (1939), Ezekiel W. Cullen Building at UH (1939), and City National Bank (1947).",
  },
  "john staub": {
    title: "John Fanz Staub, FAIA (1892-1981)",
    role: "Master Residential & Cultural Architect",
    summary:
      "MIT-trained architect who arrived in Houston in 1921 for Harrie T. Lindeberg and established his independent practice in 1923. Renowned for Bayou Bend (1928) and refined Georgian, Colonial Revival, and Regional houses in Shadyside, Broadacres, and River Oaks, as well as buildings for Rice University and the University of Houston.",
  },
  "john f. staub": {
    title: "John Fanz Staub, FAIA (1892-1981)",
    role: "Master Residential & Cultural Architect",
    summary:
      "MIT-trained architect renowned for Bayou Bend (1928) and historically sensitive residential architecture across Shadyside, Broadacres, Courtlandt Place, and River Oaks.",
  },
  "birdsall p. briscoe": {
    title: "Birdsall Parmenas Briscoe, FAIA (1876-1971)",
    role: "Residential Architect & Texas Architectural Preservationist",
    summary:
      "Partnered with Maurice J. Sullivan (1912-1917) and collaborated frequently with John F. Staub across Courtlandt Place, Shadyside, Broadacres, and River Oaks. In the 1930s Briscoe served as South Texas district officer for the Historic American Buildings Survey (HABS).",
  },
  "william ward watkin": {
    title: "William Ward Watkin, FAIA (1886-1952)",
    role: "Founding Chair of Rice Architecture & Civic Designer",
    summary:
      "Sent to Houston at age 23 by Cram, Goodhue & Ferguson to supervise construction of Rice Institute's Lovett Hall (1910-1912), Watkin founded Rice's Department of Architecture and designed the Julia Ideson Building (1926), MFAH original neoclassical wing (1924), and Trinity Episcopal Church.",
  },
  "joseph finger": {
    title: "Joseph Finger (1887-1953)",
    role: "Art Deco & Civic Moderne Pioneer",
    summary:
      "Austrian-born architect who settled in Houston in 1908 and became the city's foremost exponent of Art Deco and PWA Moderne civic and commercial architecture, designing Houston City Hall (1939), the 1940 Houston Municipal Airport Terminal, Jefferson Davis Hospital, Beth Israel Temple (1925), and Texas State Hotel (1929).",
  },
  "john s. chase": {
    title: "John Saunders Chase Jr., FAIA (1925-2012)",
    role: "Trailblazing Modernist & First Licensed Black Architect in Texas",
    summary:
      "In 1950 Chase became the first African American admitted to the University of Texas School of Architecture, and in 1952 the first licensed African American architect in Texas. He co-founded the National Organization of Minority Architects (NOMA) and designed landmark Modernist churches, residences, and campus buildings at Texas Southern University and downtown Houston.",
  },
  "lamar q. cato": {
    title: "Lamar Q. Cato (1888-1960)",
    role: "Educational & Institutional Architect",
    summary:
      "Prolific Houston institutional architect who designed major mid-century academic halls and master-planned campus buildings at Texas Southern University (established 1947), Houston ISD schools, and civic facilities across Harris County.",
  },
  "mackie & kamrath": {
    title: "MacKie & Kamrath (Fred J. MacKie Jr. & Karl Kamrath)",
    role: "Organic Modernist Architecture Firm (Est. 1937)",
    summary:
      "Deeply influenced by Frank Lloyd Wright's Usonian and Taliesin principles, MacKie & Kamrath brought bold geometric Organic Modernism to Houston through Temple Emanu El (1949), the M. D. Anderson Cancer Center, contemporary residences, and commercial buildings.",
  },
  "sanguinet & staats": {
    title: "Sanguinet & Staats (Marshall R. Sanguinet & Carl G. Staats)",
    role: "Early-20th-Century Texas Skyscraper & Mansion Firm",
    summary:
      "Operating a major Houston office managed by Alfred C. Finn and R. D. Gottlieb, Sanguinet & Staats pioneered steel-frame commercial high-rises in Downtown Houston alongside grand residential mansions in Courtlandt Place and Montrose.",
  },
  "russell brown company": {
    title: "The Russell Brown Company",
    role: "Early-20th-Century Design-Build Residential Firm",
    summary:
      "Prominent Houston residential design-build firm known for published pattern-book plans (such as Model Residences No. 203, 204, and 211) and finely crafted Craftsman, Prairie, and Colonial Revival homes across Montrose, Avondale, Courtlandt Place, and Woodland Heights.",
  },
  "the russell brown company": {
    title: "The Russell Brown Company",
    role: "Early-20th-Century Design-Build Residential Firm",
    summary:
      "Prominent Houston residential design-build firm known for published pattern-book residences and finely detailed Craftsman, Prairie, and Colonial Revival homes across Houston's streetcar suburbs.",
  },
  "philip johnson": {
    title: "Philip Cortelyou Johnson, FAIA (1906-2005)",
    role: "International Style & Postmodern Master",
    summary:
      "Working extensively with Houston patrons Dominique and John de Menil and Gerald D. Hines, Johnson made Houston a showcase of late-20th-century architecture through the University of St. Thomas Academic Mall (1957-1959) and Chapel of St. Basil (1997), Pennzoil Place (1975), Bank of America Center (1983), Williams Tower (1983), and Waterwall.",
  },
  "cameron fairchild": {
    title: "Cameron Douglas Fairchild (1899-1985)",
    role: "Regional Revival & Commercial Architect",
    summary:
      "Houston architect celebrated for sensitive Texas Regionalist, Colonial Revival, and Regency residences in River Oaks as well as refined commercial and institutional landmarks.",
  },
  "james ruskin bailey": {
    title: "James Ruskin Bailey (1882-1954)",
    role: "Eclectic Revival & Residential Architect",
    summary:
      "Designed prominent Period Revival residences and remodels for Houston civic leaders, including the 1926 Tudor Revival transformation of the John Henry Kirby Mansion at 2000 Smith Street.",
  },
  "wilson, morris, crain & anderson": {
    title: "Wilson, Morris, Crain & Anderson (WMCA)",
    role: "Mid-Century Corporate & Civic Modernist Firm",
    summary:
      "Co-architects (with Lloyd & Morgan) of the 1965 Astrodome and designers of major Houston Modernist corporate towers, museum wings, and civic libraries.",
  },
  "george e. dickey": {
    title: "George E. Dickey (1840-1915)",
    role: "Victorian & Gilded Age Commercial Architect",
    summary:
      "Leading late-19th-century Houston architect whose Romanesque Revival and Victorian brick landmarks defined 1880s-1900s Main Street, Market Square, and Quality Hill.",
  },
  "eugene t. heiner": {
    title: "Eugene T. Heiner (1852-1901)",
    role: "19th-Century Civic & Mercantile Architect",
    summary:
      "German-trained Victorian architect who designed cotton exchanges, breweries, courthouses, and brick commercial blocks across 1870s-1890s Houston and Texas.",
  },
  "olle j. lorehn": {
    title: "Olle J. Lorehn (1864-1938)",
    role: "Turn-of-the-Century Civic & Commercial Architect",
    summary:
      "Swedish-born Houston architect who designed Houston's 1904 Market House / City Hall, breweries, schools, and early masonry commercial blocks.",
  },
  "charles oliver": {
    title: "Charles W. Oliver (1888-1971)",
    role: "Period Revival & Montrose/River Oaks Residential Architect",
    summary:
      "Prolific residential architect whose brick Tudor Revival, English Cottage, and Colonial Revival homes define blocks of River Oaks, Montrose, and Boulevard Oaks.",
  },
  "art deco": {
    title: "Art Deco Architecture in Houston (1925-1942)",
    role: "Architectural Style Portfolio",
    summary:
      "Characterized by stepped vertical piers, cast-stone or terra-cotta botanical and zigzag relief panels, limestone spandrels, and monumental setbacks, Art Deco flourished in Houston during the late-1920s oil boom and 1930s PWA civic building program.",
  },
  "tudor revival": {
    title: "Tudor Revival Architecture in Houston (1915-1940)",
    role: "Architectural Style Portfolio",
    summary:
      "Marked by steeply pitched cross gables, patterned brick and half-timbering, tall chimney pots, and leaded casement windows, Tudor Revival became a signature residential style across River Oaks, Boulevard Oaks, Montrose, and Woodland Heights.",
  },
  "craftsman": {
    title: "Craftsman & Bungalow Architecture in Houston (1905-1935)",
    role: "Architectural Style Portfolio",
    summary:
      "Defined by low-pitched gabled roofs, deep overhanging eaves with exposed rafter tails, tapered porch columns on brick piers, and double-hung multi-over-one wood windows across Norhill, Houston Heights, Woodland Heights, and Eastwood.",
  },
  "colonial revival": {
    title: "Colonial Revival Architecture in Houston (1895-1955)",
    role: "Architectural Style Portfolio",
    summary:
      "Drawing on Georgian, Federal, and Southern classical precedents with symmetrical facades, pedimented entries, and multi-pane sash windows, Colonial Revival spans Houston's streetcar suburbs and garden estates.",
  },
  "queen anne": {
    title: "Queen Anne Victorian Architecture in Houston (1880-1910)",
    role: "Architectural Style Portfolio",
    summary:
      "Distinguished by asymmetrical massing, wraparound spindlework porches, corner turrets, imbricated wood shingles, and steeply pitched hipped-and-gabled roofs, best preserved in Old Sixth Ward and Houston Heights.",
  },
  "mid-century modern": {
    title: "Mid-Century Modern Architecture in Houston (1946-1972)",
    role: "Architectural Style Portfolio",
    summary:
      "Emphasizing horizontal rooflines, Roman brick, floor-to-ceiling glass walls, carports, and indoor-outdoor courtyards across Glenbrook Valley, Memorial, Meyerland, and Houston's university campuses.",
  },
  "gothic revival": {
    title: "Gothic Revival Architecture in Houston (1870-1940)",
    role: "Architectural Style Portfolio",
    summary:
      "Pointed-arch lancet windows, buttressed brick or limestone walls, and crenellated or spired bell towers found on historic sanctuaries such as Antioch Missionary Baptist Church (1875) and early downtown churches.",
  },
};

function normText(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

export function resolvePortfolioBiography(kind, query) {
  const qNorm = normText(query);
  if (!qNorm) return null;
  if (ARCHITECT_AND_STYLE_BIOGRAPHIES[qNorm]) {
    return ARCHITECT_AND_STYLE_BIOGRAPHIES[qNorm];
  }
  for (const [k, bio] of Object.entries(ARCHITECT_AND_STYLE_BIOGRAPHIES)) {
    if (qNorm.includes(k) || k.includes(qNorm)) {
      return bio;
    }
  }
  return {
    title: String(query || "").trim(),
    role: kind === "style" ? "Architectural Style Portfolio" : "Architect / Builder Portfolio",
    summary:
      kind === "style"
        ? `Chronological portfolio of extant and demolished structures documented in the "${String(
            query || ""
          ).trim()}" architectural style across Harris County.`
        : `Chronological portfolio of extant and demolished structures attributed to ${String(
            query || ""
          ).trim()} across Harris County archival, landmark, and curated survey records.`,
  };
}

function matchesPortfolioField(candidateValue, queryNorm) {
  if (!candidateValue || !queryNorm) return false;
  const cNorm = normText(candidateValue);
  if (!cNorm) return false;
  if (cNorm === queryNorm) return true;
  if (cNorm.includes(queryNorm)) return true;
  // Handle "John F. Staub" <-> "John Staub"
  const qTokens = queryNorm
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((t) => t.length > 1);
  if (qTokens.length >= 2 && qTokens.every((t) => cNorm.includes(t))) {
    return true;
  }
  return false;
}

function geomCentroid(geom) {
  if (!geom || !geom.coordinates) return [NaN, NaN];
  if (geom.type === "Point") {
    return [Number(geom.coordinates[0]), Number(geom.coordinates[1])];
  }
  const ring =
    geom.type === "Polygon"
      ? geom.coordinates[0]
      : geom.type === "MultiPolygon" && Array.isArray(geom.coordinates[0])
      ? geom.coordinates[0][0]
      : null;
  if (!Array.isArray(ring) || !ring.length) return [NaN, NaN];
  let sx = 0;
  let sy = 0;
  let n = 0;
  for (const pt of ring) {
    if (Array.isArray(pt) && Number.isFinite(pt[0]) && Number.isFinite(pt[1])) {
      sx += pt[0];
      sy += pt[1];
      n += 1;
    }
  }
  return n > 0 ? [sx / n, sy / n] : [NaN, NaN];
}

/**
 * Deterministically collect and deduplicate all extant + demolished (`Lost Houston`)
 * works matching an architect/builder (`kind = "architect"`) or style (`kind = "style"`).
 */
export function buildPortfolioDataset({
  kind = "architect",
  query = "",
  searchIndex = [],
  overlaysData = null,
  curatedOverrides = null,
}) {
  const cleanQuery = String(query || "").trim();
  const qNorm = normText(cleanQuery);
  if (!qNorm) {
    return {
      kind,
      query: cleanQuery,
      biography: null,
      works: [],
      stats: {
        totalCount: 0,
        extantCount: 0,
        demolishedCount: 0,
        landmarkCount: 0,
        goodBrickCount: 0,
        earliestYear: null,
        latestYear: null,
      },
      bounds: null,
    };
  }

  const byKey = new Map();

  const upsertWork = (entry) => {
    const lng = Number(entry.lng);
    const lat = Number(entry.lat);
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) return;
    const dedupKey = entry.isDemolished
      ? `lost:${entry.featureId || entry.title}`
      : entry.featureId && String(entry.featureId).includes("#")
      ? `bld:${entry.featureId}`
      : entry.hcadNum
      ? `hcad:${entry.hcadNum}`
      : `pt:${lng.toFixed(4)}_${lat.toFixed(4)}`;

    const existing = byKey.get(dedupKey);
    if (!existing) {
      byKey.set(dedupKey, entry);
      return;
    }
    // Merge enrichment flags onto existing work
    if (!existing.title && entry.title) existing.title = entry.title;
    if (!existing.address && entry.address) existing.address = entry.address;
    if ((!existing.yearBuilt || existing.yearBuilt < 1800) && entry.yearBuilt >= 1800) {
      existing.yearBuilt = entry.yearBuilt;
    }
    if (!existing.architect && entry.architect) existing.architect = entry.architect;
    if (!existing.style && entry.style) existing.style = entry.style;
    if (!existing.district && entry.district) existing.district = entry.district;
    existing.isLandmark = Boolean(existing.isLandmark || entry.isLandmark);
    existing.isGoodBrick = Boolean(existing.isGoodBrick || entry.isGoodBrick);
  };

  const checkMatch = (obj) => {
    if (!obj) return false;
    if (kind === "style") {
      return (
        matchesPortfolioField(obj.style, qNorm) ||
        matchesPortfolioField(obj.bld_style, qNorm) ||
        matchesPortfolioField(obj.USER_STYLE || obj.arch_style, qNorm)
      );
    }
    return (
      matchesPortfolioField(obj.architect || obj.USER_ARCHITECT, qNorm) ||
      matchesPortfolioField(obj.builder || obj.contractor, qNorm)
    );
  };

  // 1. Scan enriched searchIndex
  if (Array.isArray(searchIndex)) {
    for (const row of searchIndex) {
      if (!checkMatch(row)) continue;
      const lng = Number(row.lon ?? row.lng);
      const lat = Number(row.lat);
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) continue;
      if (
        row.kind === "lost_houston" ||
        row.type === "lost_houston" ||
        row.overlay_layer ||
        row.year_demolished ||
        row.type === "historical_waterway" ||
        row.type === "historical_railroad" ||
        row.type === "district" ||
        row.type === "red_book_1915"
      ) {
        continue;
      }
      upsertWork({
        isDemolished: false,
        isLost: false,
        hcadNum: String(row.hcad_num || ""),
        featureId: String(row.building_id || row.id || ""),
        title: String(row.name || row.building_name || row.address || "Historic Structure"),
        address: String(row.address || ""),
        yearBuilt: Number(row.year_built) || 0,
        yearDemolished: null,
        architect: String(row.architect || row.builder || ""),
        style: String(row.style || ""),
        district: String(row.district || row.neighborhood || ""),
        isLandmark: Boolean(row.landmark_name || row.landmark_type),
        isGoodBrick: Boolean(row.good_brick_years || row.award_year),
        lng,
        lat,
      });
    }
  }

  // 2. Scan curatedOverrides directly (catches multi-building campus overrides with polygon geometry)
  const ovMap =
    curatedOverrides && typeof curatedOverrides === "object"
      ? curatedOverrides.overrides || curatedOverrides
      : null;
  if (ovMap && typeof ovMap === "object") {
    for (const [ovKey, rec] of Object.entries(ovMap)) {
      if (!rec || typeof rec !== "object" || !checkMatch(rec)) continue;
      const [lng, lat] = geomCentroid(rec.geometry);
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) continue;
      const rawHcad = String(rec.hcad_num || (ovKey.includes("#") ? ovKey.split("#")[0] : ovKey));
      upsertWork({
        isDemolished: false,
        isLost: false,
        hcadNum: /^\d{13}$/.test(rawHcad) ? rawHcad : "",
        featureId: ovKey.includes("#") ? ovKey : String(rec.building_id || ""),
        title: String(rec.building_name || rec.address || ovKey),
        address: String(rec.address || ""),
        yearBuilt: Number(rec.year_built) || 0,
        yearDemolished: null,
        architect: String(rec.architect || rec.builder || ""),
        style: String(rec.style || ""),
        district: String(rec.historic_district || ""),
        isLandmark: Boolean(rec.landmark_type || rec.landmark_code),
        isGoodBrick: Boolean(rec.good_brick_awards && rec.good_brick_awards.length),
        lng,
        lat,
      });
    }
  }

  // 3. Scan COH Landmarks
  const lmFeatures = overlaysData?.landmarks?.features || [];
  for (const feat of lmFeatures) {
    const p = feat?.properties || {};
    if (!checkMatch(p)) continue;
    const [lng, lat] = geomCentroid(feat.geometry);
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) continue;
    upsertWork({
      isDemolished: false,
      isLost: false,
      hcadNum: String(p.hcad_num || p.USER_HCAD_NUM || ""),
      featureId: String(p.id || ""),
      title: String(p.name || p.USER_LANDMARK || p.address || "Designated Landmark"),
      address: String(p.address || p.USER_ADDRESS || ""),
      yearBuilt: Number(p.year_built || p.USER_YR_BUILT) || 0,
      yearDemolished: null,
      architect: String(p.architect || p.USER_ARCHITECT || ""),
      style: String(p.style || p.USER_STYLE || ""),
      district: String(p.historic_district || ""),
      isLandmark: true,
      isGoodBrick: false,
      lng,
      lat,
    });
  }

  // 4. Scan Demolished `Lost Houston` archive (`overlaysData.lost_houston`)
  const lostFeatures = overlaysData?.lost_houston?.features || [];
  for (const feat of lostFeatures) {
    const p = feat?.properties || {};
    if (!checkMatch(p)) continue;
    const [lng, lat] = geomCentroid(feat.geometry);
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) continue;
    upsertWork({
      isDemolished: true,
      isLost: true,
      hcadNum: String(p.modern_hcad_num || ""),
      featureId: String(p.id || ""),
      lostHoustonId: String(p.id || ""),
      title: String(p.name || "Lost Houston Structure"),
      address: String(p.historic_address || p.address || ""),
      yearBuilt: Number(p.year_built) || 0,
      yearDemolished: Number(p.year_demolished) || null,
      architect: String(p.architect || ""),
      style: String(p.style || ""),
      district: String(p.neighborhood || "Demolished Landmark"),
      isLandmark: false,
      isGoodBrick: false,
      lng,
      lat,
    });
  }

  const works = Array.from(byKey.values()).sort((a, b) => {
    const ya = a.yearBuilt >= 1800 ? a.yearBuilt : 9999;
    const yb = b.yearBuilt >= 1800 ? b.yearBuilt : 9999;
    if (ya !== yb) return ya - yb;
    return String(a.title).localeCompare(String(b.title));
  });

  works.forEach((w, idx) => {
    w.portfolioIndex = idx + 1;
  });

  let minLng = Infinity;
  let minLat = Infinity;
  let maxLng = -Infinity;
  let maxLat = -Infinity;
  let earliestYear = Infinity;
  let latestYear = -Infinity;
  let extantCount = 0;
  let demolishedCount = 0;
  let landmarkCount = 0;
  let goodBrickCount = 0;

  for (const w of works) {
    if (w.lng < minLng) minLng = w.lng;
    if (w.lat < minLat) minLat = w.lat;
    if (w.lng > maxLng) maxLng = w.lng;
    if (w.lat > maxLat) maxLat = w.lat;
    if (w.yearBuilt >= 1800 && w.yearBuilt <= 2030) {
      if (w.yearBuilt < earliestYear) earliestYear = w.yearBuilt;
      if (w.yearBuilt > latestYear) latestYear = w.yearBuilt;
    }
    if (w.isDemolished) demolishedCount += 1;
    else extantCount += 1;
    if (w.isLandmark) landmarkCount += 1;
    if (w.isGoodBrick) goodBrickCount += 1;
  }

  return {
    kind,
    query: cleanQuery,
    biography: resolvePortfolioBiography(kind, cleanQuery),
    works,
    stats: {
      totalCount: works.length,
      extantCount,
      demolishedCount,
      landmarkCount,
      goodBrickCount,
      earliestYear: Number.isFinite(earliestYear) ? earliestYear : null,
      latestYear: Number.isFinite(latestYear) ? latestYear : null,
    },
    bounds:
      works.length > 0
        ? [
            [minLng, minLat],
            [maxLng, maxLat],
          ]
        : null,
  };
}
