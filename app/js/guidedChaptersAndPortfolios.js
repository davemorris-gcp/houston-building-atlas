/**
 * Guided Citywide Visual Chapters ("How Houston Was Built") and
 * Architect / Builder / Architectural Style Map Portfolio Engine (Priority #4).
 */

import {
  getAtlasMaxYear,
  VALID_PORTFOLIO_KINDS,
  normalizePortfolioKind,
} from "./filterStore.js?v=20261109a";
import {
  OWNERSHIP_TIER_ITEMS,
  resolveEffectiveOwnershipTier,
  resolveOwnershipTierColor,
} from "./palettes.js?v=20261109a";
import {
  buildTxComptrollerSearchUrl,
  buildHarrisCountyClerkRpUrl,
} from "./urlValidators.js?v=20261109a";
import {
  decodeCompactMechanicsLiens,
  decodeCompactOwnerHistory,
  formatClerkInstrumentToken,
  isConfidentialOwnerName,
  isEntityOrTrustOwnerName,
  isPlaceholderOwnerName,
  parseCompactDeedWireEntries,
} from "./clerkSearchHelpers.js?v=20261109a";

export { VALID_PORTFOLIO_KINDS, normalizePortfolioKind };

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
    citations: [
      {
        label: "TSHA Handbook of Texas: Houston, TX",
        url: "https://www.tshaonline.org/handbook/entries/houston-tx",
      },
      {
        label: "COH Landmark Report 98L057 (Kellum-Noble House)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/98L057_Kellum-Noble_House_212_Dallas.pdf",
      },
    ],
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
    citations: [
      {
        label: "TSHA Handbook of Texas: BBB&C Railway",
        url: "https://www.tshaonline.org/handbook/entries/buffalo-bayou-brazos-and-colorado-railway",
      },
      {
        label: "NRHP Union Station (#77001448)",
        url: "https://atlas.thc.texas.gov/Details/2077001448",
      },
    ],
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
        featureId: "bld_003217",
        hcadNum: "0011020000001",
        lng: -95.35535,
        lat: 29.75725,
        zoom: 16.8,
      },
      {
        label: "Old Sixth Ward District",
        boundaryLayer: "historicDistricts",
        boundaryName: "Old Sixth Ward Historic District",
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
      "After Houston electrified its streetcar network in June 1891, land companies platted leafy residential suburbs along streetcar lines: Omaha & South Texas Land Co. in Houston Heights (1891), William A. Wilson in Woodland Heights (1907) and Eastwood (1913), J. W. Link's Houston Land Corp. in Montrose (1911), and Will Hogg and the Stude family's Varner Realty Co. in Norhill (1920). In the absence of municipal zoning, developers relied on private deed covenants to govern land use, setbacks, and architectural scale.",
    keyTakeaway:
      "After 1965, Texas Legislature authorization allowed the City of Houston Legal Department to enforce residential use, setback, and lot-size deed restrictions directly.",
    citations: [
      {
        label: "COH HPO: Norhill Historic District",
        url: "https://www.houstontx.gov/planning/HistoricPres/norhill/index.html",
      },
      {
        label: "TSHA Handbook of Texas: Houston Heights",
        url: "https://www.tshaonline.org/handbook/entries/houston-heights-tx",
      },
      {
        label: "COH Landmark Report 09L218 (Courtlandt Place)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/09L218_24_Courtlandt_Pl.pdf",
      },
    ],
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
        label: "Courtlandt Place (1906)",
        boundaryLayer: "historicDistricts",
        boundaryName: "Courtlandt Place Historic District",
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
      "Published for the 50th anniversary of Juneteenth, the 1915 Red Book of Houston documented 970 Black churches, schools, businesses, physicians, and civic leaders across Freedmen's Town, Third Ward, and Fifth Ward. Twenty-two years later, the 1937 federal HOLC security map placed 77.4% of graded Red Book sites inside Grade D ('Hazardous') zones, starving historic Black neighborhoods of home-rehabilitation credit before postwar I-45, I-10, and US-59 freeway trenches, urban renewal, and surface-parking clearances erased 63.7% of those 1915 parcels (leaving just 3.0% with surviving era structures).",
    keyTakeaway:
      "Even decades later, 1937 Grade D zones hold only 8 Chapter 42 Special Minimum Lot Size blocks (and 4 minimum setback blocks), compared with 153 lot-size blocks in Grade A and 192 in Grade B.",
    citations: [
      {
        label: "1915 Red Book of Houston (HPL Digital Archives)",
        url: "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll18/id/297",
      },
      {
        label: "Mapping Inequality: 1937 HOLC Houston Security Map",
        url: "https://dsl.richmond.edu/panorama/redlining/map/TX/Houston/context",
      },
    ],
    viewport: { lng: -95.3748, lat: 29.7562, zoom: 13.7, pitch: 0 },
    statePatch: {
      colorMode: "year_built",
      renderMode: "buildings",
      minYear: 1836,
      get maxYear() {
        return getAtlasMaxYear();
      },
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
        boundaryName: "1937 HOLC D-8 (Hazardous): Freedmen's Town / Fourth Ward",
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
      "Starting with dramatic 1948-1965 annexations and the 1963 Municipal Annexation Act (coupled with Ordinance 63-1219's narrow corridor strips), postwar boundary expansions quadrupled Houston past 430 square miles by 1965 and past 600 square miles after the 1996 Kingwood annexation, preventing suburban encirclement while incorporating postwar Modernist neighborhoods like Glenbrook Valley, the 1965 Astrodome, and Clear Lake aerospace campuses.",
    keyTakeaway:
      "Glenbrook Valley (1953-1962) became Texas's first post-World War II Mid-Century Modern municipal Historic District in 2011.",
    citations: [
      {
        label: "TSHA Handbook of Texas: Houston, TX (Postwar Growth)",
        url: "https://www.tshaonline.org/handbook/entries/houston-tx",
      },
      {
        label: "COH Landmark Report 12L271 (Glenbrook Valley)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/12L271_Muscanere_House_7843_Santa_Elena.pdf",
      },
      {
        label: "TSHA Handbook of Texas: Astrodome",
        url: "https://www.tshaonline.org/handbook/entries/astrodome",
      },
    ],
    viewport: { lng: -95.355, lat: 29.718, zoom: 11.1, pitch: 0 },
    statePatch: {
      colorMode: "year_built",
      renderMode: "buildings",
      minYear: 1836,
      get maxYear() {
        return getAtlasMaxYear();
      },
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
        boundaryName: "Glenbrook Valley Historic District",
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
    get era() {
      return `1979-${getAtlasMaxYear()}`;
    },
    kicker: "Chapter 6 of 6 · Modern Preservation Toolkit",
    title: "Preservation Without Zoning: How Houston Protects Places",
    narrative:
      "Because Houston voters rejected comprehensive municipal zoning in 1948, 1962, and 1993, historic preservation operates through a layered toolkit: Chapter 33 Protected Landmarks (PLMs) and 23 Historic Districts reviewed monthly by HAHC (strengthened in 2010-2015 after regular Landmarks outside districts remained subject to 90-day demolition waivers), Chapter 42 Minimum Lot Size / Building Line blocks, 45% combined State + Federal rehab tax credits, and early-warning Permit, Plat, and HAHC monitoring.",
    keyTakeaway:
      "Switch the Cartographic Lens to 'Historic Status' to distinguish permanent PLMs and Historic District buildings from vulnerable 90-day-waiver Landmarks and 60 surveyed potential historic districts and eligible sites.",
    citations: [
      {
        label: "COH HPO: Chapter 33 Districts & Landmarks Reference",
        url: "https://www.houstontx.gov/planning/HistoricPres/District_Reference.html",
      },
      {
        label: "Preservation Houston: Good Brick Awards",
        url: "https://www.preservationhouston.org/awards",
      },
    ],
    viewport: { lng: -95.3785, lat: 29.7665, zoom: 13.1, pitch: 0 },
    statePatch: {
      colorMode: "preservation_status",
      renderMode: "buildings",
      minYear: 1836,
      get maxYear() {
        return getAtlasMaxYear();
      },
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
      "Born in Bellville, Texas, Finn trained under Sanguinet & Staats before opening his Houston practice in 1913. Working closely with Jesse H. Jones, he shaped Houston's early-to-mid-20th-century skyline with the Gulf Building (1929), San Jacinto Monument (1939), Roy G. Cullen Building (1939) and Ezekiel W. Cullen Building (1950) at UH, and City National Bank (1947).",
    citations: [
      {
        label: "TSHA Handbook of Texas: Finn, Alfred Charles",
        url: "https://www.tshaonline.org/handbook/entries/finn-alfred-charles",
      },
    ],
  },
  "john staub": {
    title: "John Fanz Staub, FAIA (1892-1981)",
    role: "Master Residential & Cultural Architect",
    summary:
      "MIT-trained architect who arrived in Houston in 1921 for Harrie T. Lindeberg and established his independent practice in 1923. Renowned for Bayou Bend (1928) and refined Georgian, Colonial Revival, and Regional houses in Shadyside, Broadacres, and River Oaks, as well as buildings for Rice University and the University of Houston.",
    citations: [
      {
        label: "TSHA Handbook of Texas: Staub, John Fanz",
        url: "https://www.tshaonline.org/handbook/entries/staub-john-fanz",
      },
    ],
  },
  "john f. staub": {
    title: "John Fanz Staub, FAIA (1892-1981)",
    role: "Master Residential & Cultural Architect",
    summary:
      "MIT-trained architect renowned for Bayou Bend (1928) and historically sensitive residential architecture across Shadyside, Broadacres, Courtlandt Place, and River Oaks.",
    citations: [
      {
        label: "TSHA Handbook of Texas: Staub, John Fanz",
        url: "https://www.tshaonline.org/handbook/entries/staub-john-fanz",
      },
    ],
  },
  "birdsall p. briscoe": {
    title: "Birdsall Parmenas Briscoe, FAIA (1876-1971)",
    role: "Residential Architect & Texas Architectural Preservationist",
    summary:
      "Partnered with Maurice J. Sullivan (1912-1917) and collaborated frequently with John F. Staub across Courtlandt Place, Shadyside, Broadacres, and River Oaks. In the 1930s Briscoe served as South Texas district officer for the Historic American Buildings Survey (HABS).",
    citations: [
      {
        label: "TSHA Handbook of Texas: Briscoe, Birdsall Parmenas",
        url: "https://www.tshaonline.org/handbook/entries/briscoe-birdsall-parmenas",
      },
    ],
  },
  "william ward watkin": {
    title: "William Ward Watkin, FAIA (1886-1952)",
    role: "Founding Chair of Rice Architecture & Civic Designer",
    summary:
      "Sent to Houston at age 23 by Cram, Goodhue & Ferguson to supervise construction of Rice Institute's Lovett Hall (1910-1912), Watkin founded Rice's Department of Architecture and designed the Julia Ideson Building (1926), MFAH original neoclassical wing (1924), and Trinity Episcopal Church.",
    citations: [
      {
        label: "TSHA Handbook of Texas: Watkin, William Ward",
        url: "https://www.tshaonline.org/handbook/entries/watkin-william-ward",
      },
    ],
  },
  "joseph finger": {
    title: "Joseph Finger (1887-1953)",
    role: "Art Deco & Civic Moderne Pioneer",
    summary:
      "Austrian-born architect who settled in Houston in 1908 and became the city's foremost exponent of Art Deco and PWA Moderne civic and commercial architecture, designing Houston City Hall (1939), the 1940 Houston Municipal Airport Terminal, the 1937 Jefferson Davis Hospital (with Alfred C. Finn, demolished), Temple Beth Israel (1925), and Texas State Hotel (1929).",
    citations: [
      {
        label: "TSHA Handbook of Texas: Finger, Joseph",
        url: "https://www.tshaonline.org/handbook/entries/finger-joseph",
      },
    ],
  },
  "john s. chase": {
    title: "John Saunders Chase Jr., FAIA (1925-2012)",
    role: "Trailblazing Modernist & First Licensed Black Architect in Texas",
    summary:
      "In 1950 Chase became the first African American admitted to the University of Texas School of Architecture (earning his M.Arch. in 1952), and in 1954 became the first licensed African American architect in Texas. He co-founded the National Organization of Minority Architects (NOMA) and designed landmark Modernist churches, residences, and campus buildings at Texas Southern University and downtown Houston.",
    citations: [
      {
        label: "TSHA Handbook of Texas: Chase, John Saunders",
        url: "https://www.tshaonline.org/handbook/entries/chase-john-saunders",
      },
    ],
  },
  "lamar q. cato": {
    title: "Lamar Quintus Cato (1888-1948)",
    role: "Educational & Institutional Architect",
    summary:
      "Former Houston City Architect who partnered with Joseph Finger (1920-1923) and designed major educational and civic landmarks including the Roy G. Cullen Building at the University of Houston (1939), initial 1946-1947 campus buildings for what became Texas Southern University, Houston ISD schools, and county courthouses.",
    citations: [
      {
        label: "COH Landmark Report 16L315 (2115 Glen Haven Blvd)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/16L315_2115_Glen_Haven.pdf",
      },
    ],
  },
  "mackie & kamrath": {
    title: "MacKie & Kamrath (Fred J. MacKie Jr. & Karl Kamrath)",
    role: "Organic Modernist Architecture Firm (Est. 1937)",
    summary:
      "Deeply influenced by Frank Lloyd Wright's Usonian and Taliesin principles, MacKie & Kamrath brought bold geometric Organic Modernism to Houston through Temple Emanu El (1949), the M. D. Anderson Cancer Center, contemporary residences, and commercial buildings.",
    citations: [
      {
        label: "TSHA Handbook of Texas: Kamrath, Karl Fred",
        url: "https://www.tshaonline.org/handbook/entries/kamrath-karl-fred",
      },
    ],
  },
  "sanguinet & staats": {
    title: "Sanguinet & Staats (Marshall R. Sanguinet & Carl G. Staats)",
    role: "Early-20th-Century Texas Skyscraper & Mansion Firm",
    summary:
      "Operating a major Houston office managed by Alfred C. Finn and R. D. Gottlieb, Sanguinet & Staats pioneered steel-frame commercial high-rises in Downtown Houston alongside grand residential mansions in Courtlandt Place and Montrose.",
    citations: [
      {
        label: "TSHA Handbook of Texas: Sanguinet and Staats",
        url: "https://www.tshaonline.org/handbook/entries/sanguinet-and-staats",
      },
    ],
  },
  "russell brown company": {
    title: "The Russell Brown Company",
    role: "Early-20th-Century Design-Build Residential Firm",
    summary:
      "Prominent Houston residential design-build firm known for published pattern-book plans (such as Model Residences No. 203, 204, and 211) and finely crafted Craftsman, Prairie, and Colonial Revival homes across Montrose, Avondale, Courtlandt Place, and Woodland Heights.",
    citations: [
      {
        label: "COH Protected Landmark Report 06PL21 (Connelly House)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/06PL21_Connelly_House_218_Avondale.pdf",
      },
    ],
  },
  "the russell brown company": {
    title: "The Russell Brown Company",
    role: "Early-20th-Century Design-Build Residential Firm",
    summary:
      "Prominent Houston residential design-build firm known for published pattern-book residences and finely detailed Craftsman, Prairie, and Colonial Revival homes across Houston's streetcar suburbs.",
    citations: [
      {
        label: "COH Protected Landmark Report 06PL21 (Connelly House)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/06PL21_Connelly_House_218_Avondale.pdf",
      },
    ],
  },
  "philip johnson": {
    title: "Philip Cortelyou Johnson, FAIA (1906-2005)",
    role: "International Style & Postmodern Master",
    summary:
      "Working extensively with Houston patrons Dominique and John de Menil and developer Gerald D. Hines, Johnson (and partner John Burgee on his 1975-1985 towers) made Houston a showcase of late-20th-century architecture through the University of St. Thomas Academic Mall (1957-1959), Pennzoil Place (1975-1976), Bank of America Center (1983), Williams Tower (1983), the Waterwall (1985), and Chapel of St. Basil (1997).",
    citations: [
      {
        label: "Pritzker Architecture Prize: Philip Johnson (1979)",
        url: "https://www.pritzkerprize.com/laureates/1979",
      },
    ],
  },
  "cameron fairchild": {
    title: "Cameron Douglas Fairchild (1902-1985)",
    role: "Regional Revival & Commercial Architect",
    summary:
      "Houston architect celebrated for sensitive Texas Regionalist, Colonial Revival, and Regency residences in River Oaks as well as refined commercial and institutional landmarks.",
    citations: [
      {
        label: "COH Landmark Report 13L276 (Bland House, 3262 Ella Lee Ln)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/13L276_3262_Ella_Lee_Bland_House.pdf",
      },
    ],
  },
  "james ruskin bailey": {
    title: "James Ruskin Bailey (1889-1959)",
    role: "Eclectic Revival & Residential Architect",
    summary:
      "Designed prominent Period Revival residences and remodels for Houston civic leaders, including the 1926 Tudor Revival transformation of the John Henry Kirby Mansion at 2000 Smith Street.",
    citations: [
      {
        label: "COH Landmark Report 00L085 (506 Main St)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/00L085_506_Main_Barringer-Norton.pdf",
      },
    ],
  },
  "wilson, morris, crain & anderson": {
    title: "Wilson, Morris, Crain & Anderson (WMCA)",
    role: "Mid-Century Corporate & Civic Modernist Firm",
    summary:
      "Co-architects (with Lloyd & Morgan) of the 1965 Astrodome and designers of major Houston Modernist corporate towers, museum wings, and civic libraries.",
    citations: [
      {
        label: "COH Landmark Report 06L170 (Audrey Jones Beck House)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/06L170_Audrey_Jones_Beck_House_3223_Inwood.pdf",
      },
    ],
  },
  "george e. dickey": {
    title: "George E. Dickey (1840-after 1906)",
    role: "Victorian & Gilded Age Commercial Architect",
    summary:
      "Leading late-19th-century Houston architect whose Romanesque Revival and Victorian brick landmarks defined 1880s-1900s Main Street, Market Square, and Quality Hill.",
    citations: [
      {
        label: "TSHA Handbook of Texas: Dickey, George E.",
        url: "https://www.tshaonline.org/handbook/entries/dickey-george-e",
      },
    ],
  },
  "eugene t. heiner": {
    title: "Eugene T. Heiner (1852-1901)",
    role: "19th-Century Civic & Mercantile Architect",
    summary:
      "German-trained Victorian architect who designed cotton exchanges, breweries, courthouses, and brick commercial blocks across 1870s-1890s Houston and Texas.",
    citations: [
      {
        label: "TSHA Handbook of Texas: Heiner, Eugene T.",
        url: "https://www.tshaonline.org/handbook/entries/heiner-eugene-t",
      },
    ],
  },
  "olle j. lorehn": {
    title: "Olle J. Lorehn (1864-1939)",
    role: "Turn-of-the-Century Civic & Commercial Architect",
    summary:
      "Swedish-born Houston architect and the second architect licensed in Texas; designed the six-story Binz Building (1895, demolished), Fire Station No. 7 (1899, now Houston Fire Museum), Sacred Heart Church (1912), and early civic and commercial landmarks.",
    citations: [
      {
        label: "COH Landmark Report 03L110 (Fire Station No. 3)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/03L110_Fire_Station_No3_1919_Houston_Ave.pdf",
      },
    ],
  },
  "charles oliver": {
    title: "Charles W. Oliver (1892-1960)",
    role: "Period Revival & Montrose/River Oaks Residential Architect",
    summary:
      "Prolific residential architect whose brick Tudor Revival, English Cottage, and Colonial Revival homes define blocks of River Oaks, Montrose, and Boulevard Oaks.",
    citations: [
      {
        label: "COH Protected Landmark Report 06PL32 (1915 Bellmeade Rd)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/06PL32_Hal_and_Iley_Nunn_Davis_House_1915_Bellmeade.pdf",
      },
    ],
  },
  "art deco": {
    title: "Art Deco Architecture in Houston (1925-1942)",
    role: "Architectural Style Portfolio",
    summary:
      "Characterized by stepped vertical piers, cast-stone or terra-cotta botanical and zigzag relief panels, limestone spandrels, and monumental setbacks, Art Deco flourished in Houston during the late-1920s oil boom and 1930s PWA civic building program.",
    citations: [
      {
        label: "COH Landmark Report 02L097 (Gulf Building)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/02L097_Gulf_Bldg_712_Main.pdf",
      },
    ],
  },
  "tudor revival": {
    title: "Tudor Revival Architecture in Houston (1915-1940)",
    role: "Architectural Style Portfolio",
    summary:
      "Marked by steeply pitched cross gables, patterned brick and half-timbering, tall chimney pots, and leaded casement windows, Tudor Revival became a signature residential style across River Oaks, Boulevard Oaks, Montrose, and Woodland Heights.",
    citations: [
      {
        label: "COH Historic Preservation Manual",
        url: "https://www.houstontx.gov/planning/HistoricPres/historicmanual.html",
      },
    ],
  },
  "craftsman": {
    title: "Craftsman & Bungalow Architecture in Houston (1905-1935)",
    role: "Architectural Style Portfolio",
    summary:
      "Defined by low-pitched gabled roofs, deep overhanging eaves with exposed rafter tails, tapered porch columns on brick piers, and double-hung multi-over-one wood windows across Norhill, Houston Heights, Woodland Heights, and Eastwood.",
    citations: [
      {
        label: "COH HPO: Norhill Historic District Bungalow Design",
        url: "https://www.houstontx.gov/planning/HistoricPres/norhill/index.html",
      },
    ],
  },
  "colonial revival": {
    title: "Colonial Revival Architecture in Houston (1895-1955)",
    role: "Architectural Style Portfolio",
    summary:
      "Drawing on Georgian, Federal, and Southern classical precedents with symmetrical facades, pedimented entries, and multi-pane sash windows, Colonial Revival spans Houston's streetcar suburbs and garden estates.",
    citations: [
      {
        label: "COH Historic Preservation Manual",
        url: "https://www.houstontx.gov/planning/HistoricPres/historicmanual.html",
      },
    ],
  },
  "queen anne": {
    title: "Queen Anne Victorian Architecture in Houston (1880-1910)",
    role: "Architectural Style Portfolio",
    summary:
      "Distinguished by asymmetrical massing, wraparound spindlework porches, corner turrets, imbricated wood shingles, and steeply pitched hipped-and-gabled roofs, best preserved in Old Sixth Ward and Houston Heights.",
    citations: [
      {
        label: "COH HPO: Old Sixth Ward Historic District",
        url: "https://www.houstontx.gov/planning/HistoricPres/oldsixthward/index.html",
      },
    ],
  },
  "mid-century modern": {
    title: "Mid-Century Modern Architecture in Houston (1946-1972)",
    role: "Architectural Style Portfolio",
    summary:
      "Emphasizing horizontal rooflines, Roman brick, floor-to-ceiling glass walls, carports, and indoor-outdoor courtyards across Glenbrook Valley, Memorial, Meyerland, and Houston's university campuses.",
    citations: [
      {
        label: "COH Landmark Report 12L271 (Muscanere House)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/12L271_Muscanere_House_7843_Santa_Elena.pdf",
      },
    ],
  },
  "gothic revival": {
    title: "Gothic Revival Architecture in Houston (1870-1940)",
    role: "Architectural Style Portfolio",
    summary:
      "Pointed-arch lancet windows, buttressed brick or limestone walls, and crenellated or spired bell towers found on historic sanctuaries such as Antioch Missionary Baptist Church (1875) and early downtown churches.",
    citations: [
      {
        label: "COH Landmark Report 96L037 (Antioch Baptist Church)",
        url: "https://www.houstontx.gov/planning/HistoricPres/landmarks/96L037_Antioch_Missionary_Baptist_Church_500_Clay.pdf",
      },
    ],
  },
};

function normText(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

export function resolvePortfolioBiography(kind, query, ownershipPortfolios = null) {
  const normKind = normalizePortfolioKind(kind);
  const qRaw = String(query || "").trim();
  const qNorm = normText(qRaw);
  if (!qNorm) return null;

  if (normKind === "owner_portfolio") {
    const portMap =
      ownershipPortfolios && typeof ownershipPortfolios === "object"
        ? ownershipPortfolios.portfolios || ownershipPortfolios
        : null;
    let matchedPort = null;
    let matchedPid = qRaw;
    if (portMap && typeof portMap === "object") {
      if (portMap[qRaw]) {
        matchedPort = portMap[qRaw];
      } else {
        for (const [pid, pObj] of Object.entries(portMap)) {
          if (!pObj || typeof pObj !== "object") continue;
          const ents = Array.isArray(pObj.entity_samples)
            ? pObj.entity_samples
            : Array.isArray(pObj.entities)
            ? pObj.entities
            : [];
          if (
            normText(pid) === qNorm ||
            normText(pObj.label) === qNorm ||
            normText(pObj.label).includes(qNorm) ||
            normText(pObj.primary_entity) === qNorm ||
            ents.some((e) => normText(e).includes(qNorm))
          ) {
            matchedPort = pObj;
            matchedPid = pid;
            break;
          }
        }
      }
    }
    if (matchedPort) {
      const pTier = matchedPort.own_tier || matchedPort.tier || "regional_portfolio";
      const tierItem =
        OWNERSHIP_TIER_ITEMS.find((t) => t.key === pTier) || OWNERSHIP_TIER_ITEMS[2];
      const ents = Array.isArray(matchedPort.entity_samples)
        ? matchedPort.entity_samples
        : Array.isArray(matchedPort.entities)
        ? matchedPort.entities
        : [];
      const entitySample = ents.slice(0, 4).join(", ");
      const offs = Array.isArray(matchedPort.tx_sos_officers)
        ? matchedPort.tx_sos_officers
        : Array.isArray(matchedPort.officers)
        ? matchedPort.officers
        : [];
      const officerSample = offs.slice(0, 4).join(", ");
      const hoods = Array.isArray(matchedPort.top_neighborhoods)
        ? matchedPort.top_neighborhoods
            .slice(0, 3)
            .map((h) => (Array.isArray(h) ? `${h[0]} (${h[1]})` : String(h)))
            .join(", ")
        : "";
      const pCount =
        Number(matchedPort.parcel_count ?? matchedPort.c) ||
        (Array.isArray(matchedPort.works) ? matchedPort.works.length : 0);
      const eCount =
        Number(matchedPort.distinct_entity_count ?? matchedPort.distinct_llcs ?? matchedPort.ec) ||
        Math.max(1, ents.length);
      return {
        title: `${matchedPort.label || matchedPid} (${pCount} Parcels · ${eCount} Entities)`,
        role: `${tierItem.label} · Beneficial Ownership Cluster`,
        summary: `Tax-billing and Texas Comptroller entity resolution links ${pCount} Harris County parcels across ${eCount} legal names sharing billing address "${
          matchedPort.billing_address || "On File"
        }".${entitySample ? ` Shell / entity names include: ${entitySample}.` : ""}${
          officerSample ? ` Controlling officers / principals: ${officerSample}.` : ""
        }${hoods ? ` Top neighborhoods: ${hoods}.` : ""}`,
        citations: [
          {
            label: "TX Comptroller Franchise Tax Entity Search",
            url: buildTxComptrollerSearchUrl(
              ents[0] || matchedPort.primary_entity || matchedPort.label || ""
            ),
          },
        ],
      };
    }
    return {
      title: qRaw,
      role: "Beneficial Ownership Portfolio Cluster",
      summary: `Beneficial ownership and tax-billing portfolio cluster for ${qRaw} across Harris County appraisal and deed records.`,
    };
  }

  if (ARCHITECT_AND_STYLE_BIOGRAPHIES[qNorm]) {
    return ARCHITECT_AND_STYLE_BIOGRAPHIES[qNorm];
  }
  for (const [k, bio] of Object.entries(ARCHITECT_AND_STYLE_BIOGRAPHIES)) {
    if (qNorm.includes(k) || k.includes(qNorm)) {
      return bio;
    }
  }
  return {
    title: qRaw,
    role: normKind === "style" ? "Architectural Style Portfolio" : "Architect / Builder Portfolio",
    summary:
      normKind === "style"
        ? `Chronological portfolio of extant and demolished structures documented in the "${qRaw}" architectural style across Harris County.`
        : `Chronological portfolio of extant and demolished structures attributed to ${qRaw} across Harris County archival, landmark, and curated survey records.`,
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
 * works matching an architect/builder (`kind = "architect"`), style (`kind = "style"`),
 * or beneficial ownership portfolio (`kind = "owner_portfolio"`).
 */
export function buildPortfolioDataset({
  kind = "architect",
  query = "",
  searchIndex = [],
  overlaysData = null,
  curatedOverrides = null,
  ownershipPortfolios = null,
}) {
  const normKind = normalizePortfolioKind(kind);
  const cleanQuery = String(query || "").trim();
  const qNorm = normText(cleanQuery);
  if (!qNorm) {
    return {
      kind: normKind,
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
      ? `lost:${entry.featureId || entry.hcadNum || entry.title}`
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

  if (normKind === "owner_portfolio") {
    const portMap =
      ownershipPortfolios && typeof ownershipPortfolios === "object"
        ? ownershipPortfolios.portfolios || ownershipPortfolios
        : null;
    let matchedPort = null;
    let matchedPid = cleanQuery;
    if (portMap && typeof portMap === "object") {
      if (portMap[cleanQuery]) {
        matchedPort = portMap[cleanQuery];
      } else {
        for (const [pid, pObj] of Object.entries(portMap)) {
          if (!pObj || typeof pObj !== "object") continue;
          const ents = Array.isArray(pObj.entity_samples)
            ? pObj.entity_samples
            : Array.isArray(pObj.entities)
            ? pObj.entities
            : [];
          if (
            normText(pid) === qNorm ||
            normText(pObj.label) === qNorm ||
            normText(pObj.label).includes(qNorm) ||
            normText(pObj.primary_entity) === qNorm ||
            ents.some((e) => normText(e).includes(qNorm))
          ) {
            matchedPort = pObj;
            matchedPid = pid;
            break;
          }
        }
      }
    }

    const lostByHcad = new Map();
    for (const lf of overlaysData?.lost_houston?.features || []) {
      const lp = lf?.properties || {};
      const hc = String(lp.hcad_num || lp.modern_hcad_num || "").trim();
      if (/^\d{13}$/.test(hc)) {
        lostByHcad.set(hc, lp);
      }
    }

    const rawWorks = Array.isArray(matchedPort?.works) ? matchedPort.works : [];
    for (const wRow of rawWorks) {
      if (Array.isArray(wRow)) {
        // [acct, address, lat, lng, year_built, style_or_use, entity_name, status]
        const [acct, addr, lat, lng, yr, styleOrUse, entityName, statusFlag] = wRow;
        const cleanAcct = String(acct || "").trim();
        const lostRec = lostByHcad.get(cleanAcct);
        const isLost = statusFlag === "demolished" || Boolean(lostRec);
        upsertWork({
          isDemolished: isLost,
          isLost,
          hcadNum: cleanAcct,
          featureId: isLost && lostRec?.id ? String(lostRec.id) : cleanAcct,
          lostHoustonId: isLost && lostRec?.id ? String(lostRec.id) : "",
          title: String(addr || entityName || cleanAcct || "Portfolio Parcel"),
          address: String(addr || ""),
          yearBuilt: Number(yr) || 0,
          yearDemolished: isLost && lostRec?.year_demolished ? Number(lostRec.year_demolished) : null,
          architect: String(entityName || matchedPort?.label || ""),
          style: String(styleOrUse || ""),
          district: String(matchedPort?.label || matchedPid),
          entityName: String(entityName || ""),
          portfolioId: matchedPid,
          isLandmark: false,
          isGoodBrick: false,
          lat: Number(lat),
          lng: Number(lng),
        });
      } else if (wRow && typeof wRow === "object") {
        const cleanAcct = String(wRow.acct || wRow.hcad_num || "").trim();
        const isLost = Boolean(wRow.status === "demolished" || wRow.isDemolished);
        upsertWork({
          isDemolished: isLost,
          isLost,
          hcadNum: cleanAcct,
          featureId: String(wRow.featureId || cleanAcct),
          title: String(wRow.address || wRow.title || cleanAcct || "Portfolio Parcel"),
          address: String(wRow.address || ""),
          yearBuilt: Number(wRow.year_built ?? wRow.yearBuilt) || 0,
          yearDemolished: Number(wRow.year_demolished ?? wRow.yearDemolished) || null,
          architect: String(wRow.entity_name || wRow.entityName || matchedPort?.label || ""),
          style: String(wRow.style_or_use || wRow.style || ""),
          district: String(matchedPort?.label || matchedPid),
          entityName: String(wRow.entity_name || wRow.entityName || ""),
          portfolioId: matchedPid,
          isLandmark: Boolean(wRow.isLandmark),
          isGoodBrick: Boolean(wRow.isGoodBrick),
          lat: Number(wRow.lat),
          lng: Number(wRow.lng ?? wRow.lon),
        });
      }
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

    for (const w of works) {
      if (w.lng < minLng) minLng = w.lng;
      if (w.lat < minLat) minLat = w.lat;
      if (w.lng > maxLng) maxLng = w.lng;
      if (w.lat > maxLat) maxLat = w.lat;
      if (w.yearBuilt >= 1800 && w.yearBuilt <= Math.max(getAtlasMaxYear(), 2035)) {
        if (w.yearBuilt < earliestYear) earliestYear = w.yearBuilt;
        if (w.yearBuilt > latestYear) latestYear = w.yearBuilt;
      }
      if (w.isDemolished) demolishedCount += 1;
      else extantCount += 1;
    }

    return {
      kind: "owner_portfolio",
      query: matchedPid,
      portfolioMeta: matchedPort || null,
      biography: resolvePortfolioBiography("owner_portfolio", matchedPid, ownershipPortfolios),
      works,
      stats: {
        totalCount: works.length,
        extantCount,
        demolishedCount,
        landmarkCount: 0,
        goodBrickCount: 0,
        distinctLlcs:
          Number(
            matchedPort?.distinct_entity_count ?? matchedPort?.distinct_llcs ?? matchedPort?.ec
          ) || 1,
        parcelCount: Number(matchedPort?.parcel_count ?? matchedPort?.c) || works.length,
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

  const demolishedLandmarkHcads = new Set();
  for (const lf of overlaysData?.lost_houston?.features || []) {
    const lp = lf?.properties || {};
    if (lp.source_cohort === "coh_landmark_demolished" || lp.coh_landmark_id) {
      const candList = [
        lp.hcad_num,
        ...(Array.isArray(lp.site_hcad_nums) ? lp.site_hcad_nums : []),
      ];
      for (const rawH of candList) {
        const lh = String(rawH || "").trim();
        if (/^\d{13}$/.test(lh)) demolishedLandmarkHcads.add(lh);
      }
    }
  }
  for (const mf of overlaysData?.landmarks?.features || []) {
    const mp = mf?.properties || {};
    if (mp.demolished === true) {
      for (const rawH of [mp.site_hcad_num, mp.hcad_num]) {
        const mh = String(rawH || "").trim();
        if (/^\d{13}$/.test(mh)) demolishedLandmarkHcads.add(mh);
      }
    }
  }

  // 1. Scan enriched searchIndex
  if (Array.isArray(searchIndex)) {
    for (const row of searchIndex) {
      if (!checkMatch(row)) continue;
      const lng = Number(row.lon ?? row.lng);
      const lat = Number(row.lat);
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) continue;
      const rowHcad = String(row.hcad_num || "").trim();
      if (
        row.kind === "lost_houston" ||
        row.type === "lost_houston" ||
        row.demolished === true ||
        row.hcad_num === "Demolished" ||
        row.overlay_layer ||
        row.year_demolished ||
        row.type === "historical_waterway" ||
        row.type === "historical_railroad" ||
        row.type === "district" ||
        row.type === "red_book_1915" ||
        (rowHcad && demolishedLandmarkHcads.has(rowHcad))
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
      const rawHcad = String(rec.hcad_num || (ovKey.includes("#") ? ovKey.split("#")[0] : ovKey)).trim();
      if (/^\d{13}$/.test(rawHcad) && demolishedLandmarkHcads.has(rawHcad)) continue;
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

  // 3. Scan COH Landmarks (skipping demolished entries so they appear strictly via Lost Houston)
  const lmFeatures = overlaysData?.landmarks?.features || [];
  for (const feat of lmFeatures) {
    const p = feat?.properties || {};
    if (p.demolished === true || p.hcad_num === "Demolished" || p.USER_HCAD_NUM === "Demolished") {
      continue;
    }
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
    const canonicalHcad = String(
      p.hcad_num ||
        p.modern_hcad_num ||
        (Array.isArray(p.site_hcad_nums) && p.site_hcad_nums.length ? p.site_hcad_nums[0] : "") ||
        ""
    ).trim();
    const demoYr = Number(p.year_demolished) || 0;
    const lostCandidateHcads = [
      canonicalHcad,
      ...(Array.isArray(p.site_hcad_nums) ? p.site_hcad_nums.map((x) => String(x || "").trim()) : []),
    ];
    for (const hc of lostCandidateHcads) {
      if (!hc || !byKey.has(`hcad:${hc}`)) continue;
      const ext = byKey.get(`hcad:${hc}`);
      if (ext && !ext.isDemolished && ((demoYr > 0 && ext.yearBuilt >= demoYr - 1) || demolishedLandmarkHcads.has(hc))) {
        byKey.delete(`hcad:${hc}`);
      }
    }
    upsertWork({
      isDemolished: true,
      isLost: true,
      hcadNum: canonicalHcad,
      featureId: String(p.id || ""),
      lostHoustonId: String(p.id || ""),
      title: String(p.name || "Lost Houston Structure"),
      address: String(p.historic_address_1915 || p.historic_address || p.address || ""),
      yearBuilt: Number(p.year_built) || 0,
      yearDemolished: Number(p.year_demolished) || null,
      lifespanLabel: String(p.lifespan_label || ""),
      sourceCohort: String(p.source_cohort || ""),
      sourceCohortLabel: String(p.source_cohort_label || ""),
      architect: String(p.architect || p.builder || ""),
      style: String(p.style || ""),
      district: String(p.neighborhood || "Demolished Landmark"),
      isLandmark: Boolean(p.coh_landmark_code || p.nrhp_refnum),
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
    if (w.yearBuilt >= 1800 && w.yearBuilt <= Math.max(getAtlasMaxYear(), 2035)) {
      if (w.yearBuilt < earliestYear) earliestYear = w.yearBuilt;
      if (w.yearBuilt > latestYear) latestYear = w.yearBuilt;
    }
    if (w.isDemolished) demolishedCount += 1;
    else extantCount += 1;
    if (w.isLandmark) landmarkCount += 1;
    if (w.isGoodBrick) goodBrickCount += 1;
  }

  return {
    kind: normKind,
    query: cleanQuery,
    biography: resolvePortfolioBiography(normKind, cleanQuery, ownershipPortfolios),
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

/**
 * Pure, DOM-free wire decoder that transforms a compact `permits_<prefix3>.json` shard record,
 * PMTiles feature properties, and `ownership_portfolios.json` catalog into a normalized
 * Ownership Portfolio X-Ray & Entity Unmasking view-model (`DF-5`).
 *
 * Accepts either:
 *   - Options object: `decodeParcelOwnershipXray({ featureProps, permitShardRec, ownershipPortfolios })`
 *   - Positional arguments: `decodeParcelOwnershipXray(permitShardRec, featureProps, portfolioCatalog)`
 */
export function decodeParcelOwnershipXray(
  arg0 = null,
  featurePropsArg = null,
  portfolioCatalogArg = null
) {
  let shard = {};
  let props = {};
  let portfolioCatalog = null;

  if (
    arg0 &&
    typeof arg0 === "object" &&
    !Array.isArray(arg0) &&
    ("featureProps" in arg0 ||
      "permitShardRec" in arg0 ||
      "ownershipPortfolios" in arg0 ||
      "portfolioCatalog" in arg0)
  ) {
    shard = arg0.permitShardRec && typeof arg0.permitShardRec === "object" ? arg0.permitShardRec : {};
    props = arg0.featureProps && typeof arg0.featureProps === "object" ? arg0.featureProps : {};
    portfolioCatalog = arg0.ownershipPortfolios || arg0.portfolioCatalog || null;
  } else {
    shard = arg0 && typeof arg0 === "object" ? arg0 : {};
    props = featurePropsArg && typeof featurePropsArg === "object" ? featurePropsArg : {};
    portfolioCatalog = portfolioCatalogArg || null;
  }

  const rawTier = resolveEffectiveOwnershipTier(
    shard.ot || props.own_tier,
    props.use_category,
    props.landmark_name || props.bld_name || props.owner || props.owner_name || shard.owner
  );
  const tierObj =
    OWNERSHIP_TIER_ITEMS.find((item) => item.key === rawTier) || OWNERSHIP_TIER_ITEMS[0];
  const ownTier = tierObj.key;
  const directOwnerProbe = String(props.owner || props.owner_name || shard.owner || "").trim();
  const earlyConfidential = Boolean(
    shard.conf ||
      props.conf ||
      isConfidentialOwnerName(props.owner) ||
      isConfidentialOwnerName(props.owner_name) ||
      isConfidentialOwnerName(shard.owner)
  );
  const rawPortfolioId = earlyConfidential ? "" : String(shard.pid || props.port_id || "").trim();

  const portMap =
    !earlyConfidential && portfolioCatalog && typeof portfolioCatalog === "object"
      ? portfolioCatalog.portfolios || portfolioCatalog
      : null;
  const rawPortfolioMeta =
    rawPortfolioId && portMap && typeof portMap === "object" && portMap[rawPortfolioId]
      ? portMap[rawPortfolioId]
      : null;

  const hcadNum = String(props.hcad_num || shard.hcad_num || "").trim();
  let workEntityName = "";
  if (hcadNum && Array.isArray(rawPortfolioMeta?.works)) {
    const matchedWork = rawPortfolioMeta.works.find(
      (w) => (Array.isArray(w) && String(w[0] || "") === hcadNum) || (w && w.acct === hcadNum)
    );
    if (Array.isArray(matchedWork) && matchedWork[6]) {
      workEntityName = String(matchedWork[6]).trim();
    } else if (matchedWork && typeof matchedWork === "object" && matchedWork.entity_name) {
      workEntityName = String(matchedWork.entity_name).trim();
    }
  }

  const candidateOwnerName = String(
    directOwnerProbe || workEntityName || rawPortfolioMeta?.primary_entity || ""
  ).trim();
  const isConfidential = Boolean(earlyConfidential || isConfidentialOwnerName(candidateOwnerName));
  const portfolioId = isConfidential ? "" : rawPortfolioId;
  const portfolioMeta = isConfidential ? null : rawPortfolioMeta;
  const rawOwnerName =
    !isConfidential && candidateOwnerName && !isPlaceholderOwnerName(candidateOwnerName)
      ? candidateOwnerName
      : "";
  const rawBillingAddress = String(
    shard.m || props.mail_addr || portfolioMeta?.billing_address || ""
  ).trim();
  const coOwners = isConfidential
    ? []
    : Array.isArray(shard.co)
    ? shard.co.map((item) => String(item || "").trim()).filter(Boolean)
    : Array.isArray(props.co_owners)
    ? props.co_owners.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const careOfMatch = /^C\/O\s+([^-]+?)\s+-\s+(.+)$/i.exec(rawBillingAddress);
  const rawCareOf = String(
    (!Array.isArray(shard.co) && shard.co) ||
      props.care_of ||
      portfolioMeta?.care_of ||
      (careOfMatch ? careOfMatch[1] : "") ||
      ""
  ).trim();
  const rawFlip = shard.flip ?? props.flip_days ?? null;
  const flipDays =
    !isConfidential &&
    rawFlip !== null &&
    rawFlip !== undefined &&
    rawFlip !== "" &&
    Number.isFinite(Number(rawFlip))
      ? Number(rawFlip)
      : null;

  const isRedactedHomestead = Boolean(
    isConfidential || (ownTier === "homestead" && (!rawBillingAddress && !rawCareOf))
  );
  const billingAddress = isRedactedHomestead ? "" : rawBillingAddress;
  const careOfAgent = isRedactedHomestead ? "" : rawCareOf;

  // Decode 22-year (2005-2026) HCAD certified-roll ownership tenures gated strictly on `isConfidential`
  // (NEVER on `isRedactedHomestead`, which is true for ~650,000+ public owner-occupied homesteads whose mailing address is omitted) (`DF-1`).
  const ownerHistoryTenures = isConfidential
    ? []
    : decodeCompactOwnerHistory(shard.oh || props.owner_history || "", rawOwnerName);
  const priorOwners = ownerHistoryTenures.slice(1).map((t) => ({
    ownerName: t.ownerName,
    spanLabel: t.spanLabel,
    startYear: t.startYear,
    endYear: t.endYear,
    acqDate: t.acqDate,
    deed: t.deed,
  }));
  const recordedTenureSince =
    !isConfidential && ownerHistoryTenures.length > 0
      ? ownerHistoryTenures[0].acqDate === "<=1988"
        ? "<=1988 (HCAD Mainframe Baseline)"
        : ownerHistoryTenures[0].acqDate || String(ownerHistoryTenures[0].startYear)
      : "";

  const activeRollOwner =
    !isConfidential &&
    ownerHistoryTenures.length > 0 &&
    ownerHistoryTenures[0].endYear >= getAtlasMaxYear() &&
    !isPlaceholderOwnerName(ownerHistoryTenures[0].ownerName)
      ? ownerHistoryTenures[0].ownerName
      : "";

  const recordedOwner = isConfidential
    ? "Protected Individual Homestead (TX Tax Code Sec. 25.025)"
    : rawOwnerName || activeRollOwner || "Owner of Record (HCAD)";

  const siblingEntities = isConfidential
    ? []
    : Array.isArray(portfolioMeta?.entity_samples)
    ? portfolioMeta.entity_samples
    : Array.isArray(portfolioMeta?.entities)
    ? portfolioMeta.entities
    : [];
  const officers = isConfidential
    ? []
    : Array.isArray(portfolioMeta?.tx_sos_officers)
    ? portfolioMeta.tx_sos_officers
    : Array.isArray(portfolioMeta?.officers)
    ? portfolioMeta.officers
    : [];
  const portfolioCount = isConfidential
    ? 0
    : Number(portfolioMeta?.parcel_count ?? portfolioMeta?.c) ||
      (Array.isArray(portfolioMeta?.works) ? portfolioMeta.works.length : 0) ||
      (portfolioId ? 1 : 0);
  const entityCount = isConfidential
    ? 0
    : Number(
        portfolioMeta?.distinct_entity_count ?? portfolioMeta?.distinct_llcs ?? portfolioMeta?.ec
      ) || Math.max(1, siblingEntities.length);

  let beneficialSteward = "";
  if (isConfidential) {
    beneficialSteward = "";
  } else if (portfolioMeta) {
    beneficialSteward = String(portfolioMeta.label || portfolioMeta.primary_entity || "").trim();
  } else if (ownTier === "anonymity_shield") {
    beneficialSteward = careOfAgent
      ? `${careOfAgent} (Registered-Agent / Nominee Shield)`
      : billingAddress
      ? `Registered-Agent Hub (${billingAddress})`
      : "Registered-Agent / Nominee Trust Shield";
  }

  const isCorporateOrTrust = !isConfidential && isEntityOrTrustOwnerName(rawOwnerName);

  const searchTarget = isConfidential
    ? ""
    : siblingEntities[0] ||
      portfolioMeta?.primary_entity ||
      (isCorporateOrTrust ? rawOwnerName : "") ||
      portfolioMeta?.label ||
      rawOwnerName ||
      "";

  const parsedDeedEntries = !isConfidential
    ? parseCompactDeedWireEntries(String(shard.d || props.deed_chain || "").replace(/;/g, "|"))
    : [];
  const latestFormattedDeed =
    parsedDeedEntries.length > 0
      ? formatClerkInstrumentToken(parsedDeedEntries[0].token, parsedDeedEntries[0].isoDate)
      : ownerHistoryTenures[0]?.deed || null;
  const latestClerkFileNum =
    latestFormattedDeed && latestFormattedDeed.portal === "rp"
      ? latestFormattedDeed.copyValue
      : "";
  const clerkTarget =
    latestClerkFileNum || (!isConfidential && rawOwnerName ? rawOwnerName : hcadNum || "HOUSTON");

  const useCatClean = String(props.use_category || "").trim();
  const stateClassClean = String(props.state_class || shard.sc || "")
    .trim()
    .toUpperCase();
  const styleTextClean = String(
    props.bld_style || props.building_style || props.structure_type || ""
  ).trim();
  const isOwnerOccupiedPlex = Boolean(
    ownTier === "homestead" &&
      (useCatClean === "Multi-Family" ||
        /^B[234]/.test(stateClassClean) ||
        /\b(?:DUPLEX|TRIPLEX|FOURPLEX|QUADPLEX|TWO-FAMILY|THREE-FAMILY|FOUR-FAMILY|2-FAMILY|3-FAMILY|4-FAMILY)\b/i.test(
          styleTextClean
        ))
  );
  const ownerOccupiedPlexNote = isOwnerOccupiedPlex
    ? "Owner-Occupied Duplex / Triplex / Fourplex (HCAD Class B2-B4): Owner of record shares situs and tax-billing address with this 2-to-4 unit structure while leasing adjacent units; colored light-blue Homestead on the map."
    : "";

  return {
    ownTier,
    tierLabel: tierObj.label,
    tierShortLabel: tierObj.shortLabel || tierObj.label,
    tierColor: resolveOwnershipTierColor(ownTier, useCatClean),
    portfolioId,
    portId: portfolioId,
    portfolioMeta,
    ownerName: recordedOwner,
    recordedOwner,
    beneficialSteward,
    portfolioCount,
    entityCount,
    siblingEntities,
    officers,
    coOwners,
    billingAddress,
    mailingAddress: billingAddress,
    careOfAgent,
    careOf: careOfAgent,
    flipDays,
    isConfidential,
    isRedactedHomestead,
    ownerHistoryTenures,
    priorOwners,
    recordedTenureSince,
    isOwnerOccupiedPlex,
    ownerOccupiedPlexNote,
    isCorporateOrTrust,
    latestClerkFileNum,
    txComptrollerUrl: buildTxComptrollerSearchUrl(searchTarget),
    clerkUrl: buildHarrisCountyClerkRpUrl(clerkTarget),
  };
}

/**
 * Pure, DOM-free wire decoder that merges `1836-2026` deed chains (`d`), pre-demolition flip velocity (`flip`),
 * historical City Directory occupants (`r`), 1915 Red Book entries, original builder/architect citations,
 * and COH building/demolition permits (`p`) into a single chronological Chain of Stewardship model (`DF-5`).
 */
export function buildStewardshipTimelineModel({
  deeds = null,
  flipDays = null,
  permits = null,
  directoryOccupants = null,
  yearBuilt = 0,
  remodelYear = 0,
  redBookRecord = null,
  historicContractor = "",
  landmarkInfo = null,
  featureProps = null,
  permitShardRec = null,
  citydirRec = null,
  redBookHits = null,
  ownershipPortfolios = null,
} = {}) {
  const events = [];
  const decodedDeeds = [];
  const decodedOccupants = [];

  const props = featureProps && typeof featureProps === "object" ? featureProps : {};
  const shard = permitShardRec && typeof permitShardRec === "object" ? permitShardRec : {};
  const cdRec = citydirRec && typeof citydirRec === "object" ? citydirRec : null;

  const yrBuilt = Number(yearBuilt || props.year_built || shard.yr) || 0;
  const effContractor = String(
    historicContractor || props.architect || props.builder || props.contractor || shard.c || ""
  ).trim();

  if (yrBuilt >= 1836 && yrBuilt <= 2035) {
    events.push({
      kind: "construction",
      category: "construction",
      year: yrBuilt,
      dateLabel: String(yrBuilt),
      sortKey: `${yrBuilt}-01-01`,
      title: `Original Structure Built (${yrBuilt})`,
      subtitle: effContractor
        ? `Original Architect / Builder: ${effContractor}`
        : "Recorded on Harris County CAMA / Archival Survey Roll",
      citation: String(props.source_citation || props.year_source || ""),
      badge: "Construction",
      url: null,
    });
  }

  const rbList = [];
  if (redBookRecord && typeof redBookRecord === "object") {
    rbList.push(redBookRecord.properties || redBookRecord);
  }
  if (Array.isArray(redBookHits)) {
    for (const h of redBookHits) {
      if (h && typeof h === "object") {
        rbList.push(h.properties || h);
      }
    }
  }
  for (const rb of rbList) {
    const rbTitle = String(
      rb.title || rb.name || rb.owner_or_business || "1915 Red Book of Houston Entry"
    ).trim();
    const rbDesc = String(
      rb.occupation_or_type || rb.category || rb.subtitle || "Black Houston Resident / Business"
    ).trim();
    events.push({
      kind: "red_book_1915",
      category: "red_book_1915",
      year: 1915,
      dateLabel: "1915",
      sortKey: "1915-06-19",
      title: `1915 Red Book of Houston: ${rbTitle}`,
      subtitle: rbDesc,
      citation: "1915 Red Book of Houston (Rice Woodson Research Center / HPL)",
      badge: "1915 Red Book",
      url: "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll18/id/297",
    });
  }

  const effOccs =
    Array.isArray(directoryOccupants) && directoryOccupants.length > 0
      ? directoryOccupants
      : Array.isArray(cdRec?.r)
      ? cdRec.r
      : [];

  if (Array.isArray(effOccs) && effOccs.length > 0) {
    for (const row of effOccs) {
      if (Array.isArray(row) && row.length >= 2) {
        const yr = Number(row[0]) || 0;
        const name = String(row[1] || "").trim();
        const role = String(row[2] || "").trim();
        if (!name) continue;
        const occ = { year: yr, name, role };
        decodedOccupants.push(occ);
        events.push({
          kind: "city_directory",
          category: "city_directory",
          year: yr || 1930,
          dateLabel: yr >= 1836 ? String(yr) : "Historic",
          sortKey: yr >= 1836 ? `${yr}-06-01` : "1930-06-01",
          title: `City Directory Occupant: ${name}`,
          subtitle: role
            ? `${role} (Morrison & Fourmy / Polk Houston City Directory)`
            : "Morrison & Fourmy / Polk Houston City Directory",
          citation: String(cdRec?.c || ""),
          badge: "Directory Occupant",
          url: null,
        });
      } else if (row && typeof row === "object") {
        const yr = Number(row.year) || 0;
        const name = String(row.name || "").trim();
        const role = String(row.role || "").trim();
        if (!name) continue;
        const occ = { year: yr, name, role };
        decodedOccupants.push(occ);
        events.push({
          kind: "city_directory",
          category: "city_directory",
          year: yr || 1930,
          dateLabel: yr >= 1836 ? String(yr) : "Historic",
          sortKey: yr >= 1836 ? `${yr}-06-01` : "1930-06-01",
          title: `City Directory Occupant: ${name}`,
          subtitle: role
            ? `${role} (Morrison & Fourmy / Polk Houston City Directory)`
            : "Morrison & Fourmy / Polk Houston City Directory",
          citation: String(cdRec?.c || ""),
          badge: "Directory Occupant",
          url: null,
        });
      }
    }
  } else if (cdRec && Number(cdRec.e) >= 1836) {
    const cdYr = Number(cdRec.e);
    events.push({
      kind: "city_directory",
      category: "city_directory",
      year: cdYr,
      dateLabel: String(cdYr),
      sortKey: `${cdYr}-06-01`,
      title: `Houston City Directory Citation (${cdYr})`,
      subtitle: "Morrison & Fourmy / Polk Houston City Directory",
      citation: String(cdRec.c || ""),
      badge: "Directory Occupant",
      url: null,
    });
  }

  const yrRemodel =
    Number(remodelYear || props.remodel_year || props.yr_remodel || shard.remod) || 0;
  if (yrRemodel >= 1850 && yrRemodel !== yrBuilt) {
    events.push({
      kind: "remodel",
      category: "remodel",
      year: yrRemodel,
      dateLabel: String(yrRemodel),
      sortKey: `${yrRemodel}-07-01`,
      title: `Major Remodel / Rehabilitation (${yrRemodel})`,
      subtitle: "Recorded on HCAD Building Improvement History",
      citation: "",
      badge: "Rehabilitation",
      url: null,
    });
  }

  const effLandmark =
    landmarkInfo && typeof landmarkInfo === "object"
      ? landmarkInfo
      : props.landmark_name || props.landmark_type
      ? {
          title: props.landmark_name || props.landmark_type,
          subtitle: props.historic_district || "City of Houston / National Register Designation",
          year: Number(props.designation_year) || 2005,
        }
      : null;

  if (effLandmark && effLandmark.title) {
    const lmYear = Number(effLandmark.year) || 2005;
    events.push({
      kind: "landmark",
      category: "landmark",
      year: lmYear,
      dateLabel: effLandmark.year ? String(effLandmark.year) : "Designated",
      sortKey: `${lmYear}-09-01`,
      title: String(effLandmark.title),
      subtitle: String(effLandmark.subtitle || "City of Houston / National Register Designation"),
      citation: "",
      badge: "Preservation Milestone",
      url: effLandmark.url || null,
    });
  }

  const rawTimelineOwner = String(props.owner || props.owner_name || shard.owner || "").trim();
  const isConfidentialTimeline = Boolean(
    shard.conf ||
      props.conf ||
      isConfidentialOwnerName(props.owner) ||
      isConfidentialOwnerName(props.owner_name) ||
      isConfidentialOwnerName(shard.owner)
  );
  const ohTenures = isConfidentialTimeline
    ? []
    : decodeCompactOwnerHistory(shard.oh || props.owner_history || "", rawTimelineOwner);

  // Index `ohTenures` by canonical Clerk instrument key and acquisition date so deed nodes receive
  // Grantee names without duplicating conveyance rows (`DF-6`, `UT-7`).
  const tenureByClerkKey = new Map();
  const tenureByAcqDate = new Map();
  const consumedTenureIdx = new Set();
  ohTenures.forEach((t, idx) => {
    if (t.deed && t.deed.matchKey && !tenureByClerkKey.has(t.deed.matchKey)) {
      tenureByClerkKey.set(t.deed.matchKey, { tenure: t, idx });
    }
    if (t.acqDate && t.acqDate !== "<=1988" && !tenureByAcqDate.has(t.acqDate)) {
      tenureByAcqDate.set(t.acqDate, { tenure: t, idx });
    }
  });

  let effDeeds = [];
  if (!isConfidentialTimeline) {
    if (Array.isArray(deeds) && deeds.length > 0) {
      effDeeds = deeds;
    } else {
      const rawDeedStr = typeof deeds === "string" ? deeds : String(shard.d || props.deed_chain || "");
      if (rawDeedStr.trim()) {
        for (const e of parseCompactDeedWireEntries(rawDeedStr)) {
          if (!e.isoDate) continue;
          effDeeds.push([e.isoDate, e.token, e.deedId, e.typeCode, e.grantor, e.grantee]);
        }
      }
    }
  }

  for (const dRow of effDeeds) {
    let dt = "";
    let clerkNumRaw = "";
    let deedId = "";
    let code = "D";
    let rowGrantor = "";
    let rowGrantee = "";
    if (Array.isArray(dRow)) {
      dt = String(dRow[0] || "").trim();
      clerkNumRaw = String(dRow[1] || "").trim();
      deedId = String(dRow[2] || "").trim();
      code = String(dRow[3] || "D").trim().toUpperCase();
      rowGrantor = String(dRow[4] || "").trim();
      rowGrantee = String(dRow[5] || "").trim();
    } else if (dRow && typeof dRow === "object") {
      dt = String(dRow.date || dRow.dt || "").trim();
      clerkNumRaw = String(dRow.clerk_num || dRow.clerkFileNum || "").trim();
      deedId = String(dRow.deed_id || dRow.deedId || "").trim();
      code = String(dRow.type || dRow.code || "D").trim().toUpperCase();
      rowGrantor = String(dRow.grantor || "").trim();
      rowGrantee = String(dRow.grantee || "").trim();
    }
    if (!dt) continue;
    const normInst = clerkNumRaw ? formatClerkInstrumentToken(clerkNumRaw, dt) : null;
    const clerkNum = normInst ? normInst.copyValue : clerkNumRaw;
    const clerkDisplay = normInst ? normInst.displayValue : clerkNumRaw;

    let matchedEntry =
      (normInst && normInst.matchKey ? tenureByClerkKey.get(normInst.matchKey) : null) ||
      tenureByAcqDate.get(dt) ||
      null;
    if (matchedEntry && consumedTenureIdx.has(matchedEntry.idx)) {
      matchedEntry = null;
    }
    if (matchedEntry) {
      consumedTenureIdx.add(matchedEntry.idx);
    }
    const granteeName = matchedEntry ? matchedEntry.tenure.ownerName : rowGrantee;
    const granteeSpan = matchedEntry ? matchedEntry.tenure.spanLabel : "";
    const grantorName = rowGrantor;

    const typeLabel =
      code === "S"
        ? "Subdivision Plat / Replat Conveyance"
        : code === "O"
        ? "Official Public Record Transfer"
        : "Warranty / Grant Deed Transfer";
    const clerkUrl = clerkNum ? buildHarrisCountyClerkRpUrl(clerkNum) : null;
    const deedObj = {
      date: dt,
      clerkFileNum: clerkNum,
      deedId,
      typeCode: code,
      typeLabel,
      grantor: grantorName,
      grantee: granteeName,
      tenureSpan: granteeSpan,
      clerkUrl,
    };
    decodedDeeds.push(deedObj);
    const conveyanceTitle =
      grantorName && granteeName
        ? `${typeLabel}: ${grantorName} -> ${granteeName}`
        : granteeName
        ? `${typeLabel} to ${granteeName}`
        : grantorName
        ? `${typeLabel} from ${grantorName}`
        : typeLabel;
    events.push({
      kind: "deed",
      category: "deed",
      year: parseInt(dt.slice(0, 4), 10) || 2000,
      dateLabel: dt,
      sortKey: dt.length === 4 ? `${dt}-01-01` : dt,
      title: conveyanceTitle,
      subtitle: clerkDisplay
        ? `${normInst?.kind === "vol_page" ? "Harris County Deed " : "Harris County Clerk File #"}${clerkDisplay}${
            granteeSpan ? ` · HCAD Roll (${granteeSpan})` : ""
          }${deedId ? ` · HCAD Deed ID ${deedId}` : ""}`
        : deedId
        ? `HCAD Deed Transfer ID ${deedId}`
        : "Recorded Harris County Real Property Conveyance",
      citation: clerkDisplay ? `RP Clerk #${clerkDisplay}` : "",
      badge: code === "S" ? "Plat / Replat" : "Deed Transfer",
      clerkFileNum: clerkNum,
      grantor: grantorName,
      grantee: granteeName,
      url: clerkUrl,
    });
  }

  if (!isConfidentialTimeline && shard.ml) {
    for (const ml of decodeCompactMechanicsLiens(shard.ml)) {
      const mlDt = ml.date || String(yrBuilt || 1990);
      const mlYear = parseInt(mlDt.slice(0, 4), 10) || yrBuilt || 1990;
      const clerkNum = ml.instrument.copyValue;
      const clerkDisplay = ml.instrument.displayValue;
      const amtSuffix = ml.amount ? ` ($${ml.amount})` : "";
      events.push({
        kind: "mechanics_lien",
        category: "construction",
        year: mlYear,
        dateLabel: mlDt,
        sortKey: mlDt.length === 4 ? `${mlDt}-05-01` : mlDt,
        title: `Recorded Mechanic's Lien / Builder Contract: ${ml.contractor}${amtSuffix}`,
        subtitle: `${
          ml.instrument.kind === "vol_page" ? "Harris County Deed " : "Harris County Clerk File #"
        }${clerkDisplay}${ml.ownerName ? ` · Owner: ${ml.ownerName}` : ""}`,
        citation: `RP Clerk #${clerkDisplay} (M/L)`,
        badge: "Mechanic's Lien (M/L)",
        clerkFileNum: clerkNum,
        contractor: ml.contractor,
        url: clerkNum ? buildHarrisCountyClerkRpUrl(clerkNum) : null,
      });
    }
  }

  // Emit `owner_tenure` provenance nodes for un-deeded historical HCAD roll tenures and pre-2005 acquisitions (`DF-6`, `UT-7`).
  ohTenures.forEach((t, idx) => {
    if (consumedTenureIdx.has(idx)) return;
    let sortKey = `${t.startYear}-01-01`;
    let dateLabel = t.spanLabel;
    let nodeYear = t.startYear;
    if (t.acqDate === "<=1988") {
      // Map `"<=1988"` to `1988-01-02` so ASCII `'<'` (`0x3c`) never sorts ahead of `'1'` (`0x31`, e.g. 1915/1928) (`DF-6`).
      sortKey = "1988-01-02";
      dateLabel = "<=1988";
      nodeYear = 1988;
    } else if (/^\d{4}-\d{2}-\d{2}$/.test(t.acqDate)) {
      sortKey = t.acqDate;
      dateLabel = t.acqDate;
      nodeYear = parseInt(t.acqDate.slice(0, 4), 10) || t.startYear;
    } else if (/^\d{4}$/.test(t.acqDate)) {
      sortKey = `${t.acqDate}-01-01`;
      dateLabel = `${t.acqDate} (${t.spanLabel})`;
      nodeYear = parseInt(t.acqDate, 10) || t.startYear;
    }
    const subtitle = t.isPreMainframe
      ? `Continuous HCAD roll tenure (${t.spanLabel}) recorded at or before January 2, 1988 mainframe baseline`
      : t.acqDate
      ? `HCAD certified appraisal roll tenure (${t.spanLabel}) · Recorded acquisition ${t.acqDate}`
      : `Recorded on ${t.spanLabel} Harris County certified appraisal rolls`;
    events.push({
      kind: "owner_tenure",
      category: "deed",
      year: nodeYear,
      dateLabel,
      sortKey,
      title: `HCAD Roll Owner (${t.spanLabel}): ${t.ownerName}`,
      subtitle,
      citation: t.deed ? `RP Clerk #${t.deed.displayValue}` : `HCAD Certified Roll (${t.spanLabel})`,
      badge: "HCAD Roll Tenure",
      ownerName: t.ownerName,
      tenureSpan: t.spanLabel,
      url: t.deed ? buildHarrisCountyClerkRpUrl(t.deed.copyValue) : null,
    });
  });

  const effPermits =
    Array.isArray(permits) && permits.length > 0
      ? permits
      : Array.isArray(shard.p)
      ? shard.p
      : [];

  if (Array.isArray(effPermits)) {
    for (const pRow of effPermits) {
      let dt = "";
      let pType = "";
      let desc = "";
      let val = 0;
      let explicitStage = "";
      let explicitBadge = "";
      if (Array.isArray(pRow)) {
        dt = String(pRow[0] || "").trim();
        pType = String(pRow[1] || "").trim();
        desc = String(pRow[2] || "").trim();
        val = Number(pRow[3]) || 0;
      } else if (pRow && typeof pRow === "object") {
        dt = String(pRow.dt || pRow.date || pRow.permit_date || "").trim();
        pType = String(
          pRow.tp || pRow.type || pRow.permit_type_label || pRow.permit_type || ""
        ).trim();
        desc = String(
          pRow.d || pRow.desc || pRow.description || pRow.use_desc || pRow.permit_desc || ""
        ).trim();
        val = Number(pRow.val ?? pRow.value ?? pRow.declared_value) || 0;
        explicitStage = String(pRow.demo_stage || "").trim();
        explicitBadge = String(pRow.demo_stage_badge || "").trim();
      }
      if (!dt && !pType && !desc) continue;
      const u = `${pType} ${desc} ${explicitBadge}`.toUpperCase();
      const ptUpper = pType.toUpperCase();
      const isExplicitStage2 =
        explicitStage === "stage_2_demolition" ||
        explicitStage === "building_demo_2_of_2" ||
        u.includes("2 OF 2") ||
        (ptUpper.includes("BUILDING") && ptUpper.includes("DEMO") && !u.includes("1 OF 2"));
      const isSewerDisc =
        !isExplicitStage2 &&
        (explicitStage === "stage_1_sewer_plumbing" ||
          explicitStage === "sewer_disconnect_1_of_2" ||
          u.includes("1 OF 2") ||
          u.includes("SEWER DISC") ||
          u.includes("SEWER DISCONNECT") ||
          u.includes("SW DISC") ||
          (u.includes("PLUMB") && (u.includes("DEMO") || u.includes("DISC"))));
      const isDemo =
        !isSewerDisc &&
        (isExplicitStage2 ||
          u.includes("DEMO") ||
          u.includes("TEARDOWN") ||
          u.includes("RAZING"));
      const badge =
        explicitBadge ||
        (isDemo
          ? "Building Demo Permit (2 of 2)"
          : isSewerDisc
          ? "Sewer Disconnect (1 of 2)"
          : "COH Permit");
      events.push({
        kind: isDemo ? "demo_permit" : isSewerDisc ? "sewer_disc_permit" : "permit",
        category: isDemo ? "demolition" : "permit",
        year: parseInt(String(dt).slice(0, 4), 10) || 2020,
        dateLabel: dt || "Recorded",
        sortKey: dt || "2020-01-01",
        title: `${pType || "COH Permit"}${desc ? `: ${desc}` : ""}`,
        subtitle:
          val > 0
            ? `Declared Valuation: $${val.toLocaleString()}`
            : isSewerDisc
            ? "Stage 1 Plumbing Sewer Disconnect Prerequisite (Not Final Building Demolition)"
            : isDemo
            ? "Stage 2 Structural Building Demolition Permit"
            : "City of Houston Permit Record",
        citation: "",
        badge,
        url: null,
      });
    }
  }

  const rawFlip = isConfidentialTimeline ? null : flipDays ?? shard.flip ?? props.flip_days ?? null;
  const cleanFlip =
    rawFlip !== null && rawFlip !== undefined && rawFlip !== "" && Number.isFinite(Number(rawFlip))
      ? Number(rawFlip)
      : null;

  if (cleanFlip !== null && cleanFlip >= 0 && cleanFlip <= 730) {
    const latestDeedSort = decodedDeeds[0]?.date || "2020-01-02";
    events.push({
      kind: "rapid_flip",
      category: "demolition",
      year: parseInt(String(latestDeedSort).slice(0, 4), 10) || 2020,
      dateLabel: `${cleanFlip} days`,
      sortKey: `${latestDeedSort}_flip`,
      title: `Pre-Demolition Flip Velocity: ${cleanFlip} Days`,
      subtitle: `Property deed transferred ${cleanFlip} days prior to main-structure demolition permit filing.`,
      citation: "HCAD Deed History x COH Demolition Permit Cross-Check",
      badge: `Flip Alert (${cleanFlip} days)`,
      url: null,
    });
  }

  events.sort((a, b) => String(a.sortKey).localeCompare(String(b.sortKey)));

  return {
    events,
    deeds: decodedDeeds,
    ownerTenures: ohTenures,
    directoryOccupants: decodedOccupants,
    flipDays: cleanFlip,
    isRapidTeardownFlip: cleanFlip !== null && cleanFlip >= 0 && cleanFlip <= 730,
  };
}

