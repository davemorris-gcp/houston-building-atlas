/**
 * Cartographic palettes, MapLibre GL JS paint expressions, and curated neighborhood tours
 * for The Houston Building Atlas v2 (Preservation Houston).
 */

export const ARCHIVAL_YEAR_STOPS = [
  { year: 1836, maxYear: 1889, label: "1836–1889 (Pioneer & Victorian)", color: "#8B1E24" },
  { year: 1890, maxYear: 1899, label: "1890–1899 (Gilded Age)", color: "#B83B2A" },
  { year: 1900, maxYear: 1919, label: "1900–1919 (Early Streetcar Era)", color: "#D6642C" },
  { year: 1920, maxYear: 1929, label: "1920–1929 (Roaring Twenties)", color: "#E69238" },
  { year: 1930, maxYear: 1939, label: "1930–1939 (Depression & Deco)", color: "#EBC154" },
  { year: 1940, maxYear: 1959, label: "1940–1959 (Post-War Boom)", color: "#89B87A" },
  { year: 1960, maxYear: 1979, label: "1960–1979 (Space City Era)", color: "#429B8E" },
  { year: 1980, maxYear: 1999, label: "1980–1999 (Late 20th Century)", color: "#316B9C" },
  { year: 2000, maxYear: 2026, label: "2000–Present (21st Century)", color: "#26437E" },
];

export const CLASSIC_EE_YEAR_STOPS = [
  { year: 1836, maxYear: 1889, label: "Pre-1890 (Oldest)", color: "#800026" },
  { year: 1890, maxYear: 1909, label: "1890–1909", color: "#bd0026" },
  { year: 1910, maxYear: 1924, label: "1910–1924", color: "#e31a1c" },
  { year: 1925, maxYear: 1939, label: "1925–1939", color: "#fc4e2a" },
  { year: 1940, maxYear: 1954, label: "1940–1954", color: "#fd8d3c" },
  { year: 1955, maxYear: 1969, label: "1955–1969", color: "#feb24c" },
  { year: 1970, maxYear: 1984, label: "1970–1984", color: "#fed976" },
  { year: 1985, maxYear: 1999, label: "1985–1999", color: "#ffeda0" },
  { year: 2000, maxYear: 2026, label: "2000–Present (Newest)", color: "#ffffcc" },
];

export const UNKNOWN_YEAR_COLOR = "#524E4A";

export const PRESERVATION_STATUS_ITEMS = [
  { key: "Protected Landmark", label: "Protected Landmark (PLM)", color: "#E63946" },
  { key: "Landmark", label: "Designated Landmark (LM)", color: "#F4A261" },
  { key: "Contributing", label: "Contributing in Historic District", color: "#2A9D8F" },
  { key: "Non-Contributing", label: "Non-Contributing in District", color: "#E76F51" },
  { key: "Outside Historic District", label: "Outside Historic District", color: "#5E6472" },
];

export const USE_CATEGORY_ITEMS = [
  { key: "Residential", label: "Single-Family / Residential", color: "#E09F3E" },
  { key: "Multi-Family", label: "Multi-Family / Apartments", color: "#D66853" },
  { key: "Commercial", label: "Commercial / Office / Retail", color: "#3B8EA5" },
  { key: "Civic / Institutional", label: "Civic / Religious / Public", color: "#9B5DE5" },
  { key: "Industrial", label: "Industrial / Warehouse", color: "#7F8C8D" },
  { key: "Vacant / Exempt", label: "Vacant / Untaxed Parcel", color: "#4A4A48" },
];

export const CURATED_TOURS = [
  {
    id: "good_brick_highlights",
    name: "★ Good Brick Highlights",
    era: "1979–2026",
    center: [-95.3672, 29.7596],
    zoom: 15.1,
    pitch: 42,
    description:
      "A guided tour of iconic Preservation Houston Good Brick Award recipients featuring archival 'Then & Now' photography.",
    stops: [
      {
        title: "1929 Gulf Building (JPMorgan Chase Building)",
        year: 1929,
        hcad_num: "0010810000007",
        building_id: "bld_004281",
        lng: -95.36426,
        lat: 29.75906,
        zoom: 17.6,
        pitch: 48,
        story:
          "Houston's tallest skyscraper from 1929 to 1963 (Alfred C. Finn, Kenneth Franzheim & J.E.R. Carpenter). Honored with Good Brick Awards in 1991 and 2016.",
      },
      {
        title: "Niels & Mellie Esperson Building",
        year: 1927,
        hcad_num: "0010910000015",
        building_id: "osm_city_510322923#bldg_niels_esperson_building",
        lng: -95.36543,
        lat: 29.75892,
        zoom: 17.6,
        pitch: 48,
        story:
          "Crowned by a majestic Italian Renaissance tempietto designed by theatre architect John Eberson (1927), paired with the 1941 Mellie Esperson annex.",
      },
      {
        title: "Julia Ideson Building (Houston Public Library)",
        year: 1926,
        hcad_num: "0011470000001",
        building_id: "bld_003423",
        lng: -95.36914,
        lat: 29.75892,
        zoom: 17.6,
        pitch: 45,
        story:
          "Designed by Cram & Ferguson with William Ward Watkin in Spanish Renaissance Revival style (1926). Restored in a landmark 2012 Good Brick project.",
      },
      {
        title: "1910 Harris County Courthouse",
        year: 1910,
        hcad_num: "0010300000001",
        building_id: "0010300000001",
        lng: -95.35885,
        lat: 29.76061,
        zoom: 17.5,
        pitch: 45,
        story:
          "Beaux-Arts Texas pink granite and terra-cotta courthouse by Lang & Witchell, meticulously returned to its 1910 grandeur (2012 Good Brick Award).",
      },
      {
        title: "Lovett Hall (Rice University Administration)",
        year: 1912,
        hcad_num: "0421790000001",
        building_id: "0421790000001#rice_58_lovett_hall_administration_b",
        lng: -95.39783,
        lat: 29.71902,
        zoom: 17.5,
        pitch: 42,
        story:
          "Cornerstone of the Rice campus designed by Cram, Goodhue & Ferguson with William Ward Watkin (1912). Received a 2014 Good Brick Award for centennial masonry restoration.",
      },
      {
        title: "1940 Air Terminal Museum (Hobby Airport)",
        year: 1940,
        hcad_num: "0432070000001",
        building_id: "0690030030001",
        lng: -95.28629,
        lat: 29.64704,
        zoom: 17.2,
        pitch: 40,
        story:
          "Joseph Finger's streamlined Art Deco municipal air terminal (1940), saved and restored by the Houston Aeronautical Heritage Society (2005 & 2009 Good Brick Awards).",
      },
    ],
  },
  {
    id: "art_deco_skyline",
    name: "Art Deco & Moderne",
    era: "1926–1940",
    center: [-95.3678, 29.7598],
    zoom: 15.6,
    pitch: 48,
    description:
      "Explore Joseph Finger, Alfred C. Finn, and Kenneth Franzheim's Zigzag Deco and Texas Moderne landmarks across Houston.",
    stops: [
      {
        title: "Houston City Hall",
        year: 1939,
        hcad_num: "0012510000017",
        building_id: "0012510000017",
        lng: -95.37162,
        lat: 29.76066,
        zoom: 17.5,
        pitch: 48,
        story:
          "Joseph Finger's 1939 PWA Moderne masterpiece clad in Cordova cream limestone overlooking Hermann Square, featuring sculpted reliefs by Herring Coe.",
      },
      {
        title: "1929 Gulf Building",
        year: 1929,
        hcad_num: "0010810000007",
        building_id: "bld_004281",
        lng: -95.36426,
        lat: 29.75906,
        zoom: 17.6,
        pitch: 50,
        story:
          "36-story Art Deco setback skyscraper modeled on Eliel Saarinen's Tribune Tower design, with a soaring banking lobby chronicling Texas history.",
      },
      {
        title: "River Oaks Theatre",
        year: 1939,
        hcad_num: "0442250000170",
        building_id: "bld_011227",
        lng: -95.4074,
        lat: 29.75267,
        zoom: 17.6,
        pitch: 40,
        story:
          "Designed by Pettigrew & Worley in 1939 as the anchor of Hugh Potter's streamline-moderne River Oaks Shopping Center; honored with a 2026 Good Brick Award.",
      },
      {
        title: "1940 Air Terminal Museum",
        year: 1940,
        hcad_num: "0432070000001",
        building_id: "0690030030001",
        lng: -95.28629,
        lat: 29.64704,
        zoom: 17.3,
        pitch: 40,
        story:
          "One of America's finest surviving classical Art Deco commercial aviation terminals, designed by Joseph Finger for the dawn of DC-3 passenger flight.",
      },
    ],
  },
  {
    id: "old_sixth_ward",
    name: "Old Sixth Ward",
    era: "1870s–1900s",
    center: [-95.3772, 29.7664],
    zoom: 16.2,
    pitch: 35,
    description:
      "Houston's oldest intact historic neighborhood and first Protected Historic District, rich in Victorian cottages.",
    stops: [
      {
        title: "McEvine House (1909 Decatur St)",
        year: 1868,
        hcad_num: "0052100000009",
        building_id: "bld_004074",
        lng: -95.37766,
        lat: 29.76676,
        zoom: 18.0,
        pitch: 35,
        story:
          "Built c. 1868, one of the earliest surviving Reconstruction-era cottages in the Old Sixth Ward (1994 Good Brick Award recipient).",
      },
      {
        title: "Kinney-Morrow 1876 Cottage (1817 Kane St)",
        year: 1876,
        hcad_num: "0052260000014",
        building_id: "bld_003930",
        lng: -95.37682,
        lat: 29.76601,
        zoom: 18.0,
        pitch: 35,
        story:
          "Authentic 1876 Victorian cottage with jigsaw porch brackets on Kane Street, recognized with a 2011 Good Brick Award.",
      },
      {
        title: "The Lighthouse House (2018 Kane St)",
        year: 1906,
        hcad_num: "0052000000006",
        building_id: "bld_004003",
        lng: -95.37927,
        lat: 29.76656,
        zoom: 18.0,
        pitch: 35,
        story:
          "1906 turn-of-the-century residence in Old Sixth Ward honored with a 2025 Good Brick Award for sensitive historic rehabilitation.",
      },
      {
        title: "Dow Elementary School (1900 Kane St)",
        year: 1912,
        hcad_num: "1248130010001",
        building_id: "",
        lng: -95.37769,
        lat: 29.76591,
        zoom: 17.7,
        pitch: 35,
        story:
          "Designed in 1912 by architect C. H. Page as the civic heart of the Old Sixth Ward community.",
      },
    ],
  },
  {
    id: "houston_heights",
    name: "Houston Heights",
    era: "1891–1920s",
    center: [-95.3978, 29.7935],
    zoom: 15.3,
    pitch: 30,
    description:
      "Founded in 1891 as one of Texas's earliest planned streetcar suburbs along grand Heights Boulevard.",
    stops: [
      {
        title: "Houston Heights City Hall & Fire Station",
        year: 1914,
        hcad_num: "0201820000029",
        building_id: "",
        lng: -95.39869,
        lat: 29.79283,
        zoom: 17.8,
        pitch: 35,
        story:
          "Built in 1914 by Alonza C. Pigg when Houston Heights was still an independent municipality prior to its 1918 annexation into Houston.",
      },
      {
        title: "Heights Branch Library (1302 Heights Blvd)",
        year: 1925,
        hcad_num: "0201660000034",
        building_id: "",
        lng: -95.39733,
        lat: 29.79472,
        zoom: 17.8,
        pitch: 35,
        story:
          "1925 Italian Renaissance Revival neighborhood library designed by architect James M. Glover facing the esplanade of Heights Boulevard.",
      },
      {
        title: "Heights First Baptist Sanctuary (1548 Heights Blvd)",
        year: 1924,
        hcad_num: "0201360000032",
        building_id: "bld_001112",
        lng: -95.39693,
        lat: 29.7997,
        zoom: 17.8,
        pitch: 35,
        story:
          "1924 Classical Revival sanctuary on Heights Boulevard, recipient of a 2025 Good Brick Award for exterior and stained-glass restoration.",
      },
      {
        title: "Lund House & Modern Print Shop (2728 Columbia St)",
        year: 1896,
        hcad_num: "0341880010001",
        building_id: "0350800200001",
        lng: -95.39338,
        lat: 29.812,
        zoom: 17.8,
        pitch: 35,
        story:
          "1896 Victorian residence and historic print shop in the Heights, honored with a 2026 Good Brick Award.",
      },
    ],
  },
  {
    id: "freedmens_town",
    name: "Freedmen's Town",
    era: "1865–1920s",
    center: [-95.3792, 29.7555],
    zoom: 16.1,
    pitch: 30,
    description:
      "Historic Fourth Ward community established by emancipated African Americans after Juneteenth 1865.",
    stops: [
      {
        title: "Rutherford B. H. Yates House (1314 Andrews St)",
        year: 1912,
        hcad_num: "0090780000008",
        building_id: "",
        lng: -95.3809,
        lat: 29.75596,
        zoom: 18.0,
        pitch: 35,
        story:
          "1912 home of printer and civic leader Rutherford B. H. Yates (son of Reverend Jack Yates), bordering the handmade brick streets of Freedmen's Town.",
      },
      {
        title: "The Gregory School (1300 Victor St)",
        year: 1926,
        hcad_num: "0050180000019",
        building_id: "",
        lng: -95.38049,
        lat: 29.75421,
        zoom: 17.7,
        pitch: 35,
        story:
          "Built in 1926 (Hedrick & Gottlieb) as Houston's first public school for African American students; now the African American Library at the Gregory School.",
      },
      {
        title: "1111 Saulnier Street Historic Cottage",
        year: 1925,
        hcad_num: "0090720000003",
        building_id: "bld_004785",
        lng: -95.37917,
        lat: 29.75692,
        zoom: 18.0,
        pitch: 35,
        story:
          "Intact Fourth Ward historic cottage recognized with a Preservation Houston Good Brick Award in 2001.",
      },
    ],
  },
  {
    id: "downtown_1836",
    name: "1836 Townsite & Market Sq",
    era: "1836–1930s",
    center: [-95.3622, 29.7615],
    zoom: 15.8,
    pitch: 45,
    description:
      "The Allen Brothers' original 1836 survey on Buffalo Bayou, cast-iron commercial blocks, and early skyscrapers.",
    stops: [
      {
        title: "The Kellum-Noble House (1847)",
        year: 1847,
        hcad_num: "0400030000014",
        building_id: "0400030000014#shp_44632292_the_kellum_noble_house",
        lng: -95.37231,
        lat: 29.7589,
        zoom: 17.8,
        pitch: 40,
        story:
          "Built by Nathaniel Kellum in 1847 from handmade Houston Bayou clay brick—the oldest surviving brick building on its original site in Houston.",
      },
      {
        title: "Christ Church Cathedral (1893)",
        year: 1893,
        hcad_num: "0010550000006",
        building_id: "0010550000006",
        lng: -95.36095,
        lat: 29.75988,
        zoom: 17.7,
        pitch: 42,
        story:
          "Houston's oldest congregation on its original 1839 Texas Avenue parcel, anchored by an 1893 Gothic Revival sanctuary.",
      },
      {
        title: "The Rice Hotel (1913 Capitol Site)",
        year: 1913,
        hcad_num: "0010570000009",
        building_id: "bld_003226",
        lng: -95.36277,
        lat: 29.76054,
        zoom: 17.6,
        pitch: 45,
        story:
          "Erected by William Marsh Rice's estate in 1913 on the exact corner where the 1837 Capitol of the Republic of Texas stood.",
      },
      {
        title: "Houston Union Station (1911)",
        year: 1911,
        hcad_num: "1420820010001",
        building_id: "1420820010001",
        lng: -95.3563,
        lat: 29.7568,
        zoom: 17.4,
        pitch: 45,
        story:
          "Designed in 1911 by Warren & Wetmore (architects of NYC's Grand Central Terminal) and adaptively reused as the entrance to Daikin Park.",
      },
    ],
  },
  {
    id: "avondale_montrose",
    name: "Avondale & Courtlandt",
    era: "1902–1925",
    center: [-95.3845, 29.7445],
    zoom: 15.9,
    pitch: 30,
    description:
      "Turn-of-the-century streetcar subdivisions featuring Prairie, Craftsman, and Birdsall Briscoe residences.",
    stops: [
      {
        title: "W. T. Carter Jr. House (18 Courtlandt Pl)",
        year: 1912,
        hcad_num: "0102490000010",
        building_id: "",
        lng: -95.38475,
        lat: 29.74387,
        zoom: 17.9,
        pitch: 35,
        story:
          "1912 mansion on private gated Courtlandt Place designed by Olle Lorehn and Birdsall P. Briscoe.",
      },
      {
        title: "Baker-Jones House (22 Courtlandt Pl)",
        year: 1917,
        hcad_num: "0102490000012",
        building_id: "",
        lng: -95.38542,
        lat: 29.74397,
        zoom: 17.9,
        pitch: 35,
        story:
          "Designed in 1917 by renowned Houston residential architect Birdsall P. Briscoe in the National Register-listed Courtlandt Place enclave.",
      },
      {
        title: "Martha Perlitz House (503 Avondale St)",
        year: 1912,
        hcad_num: "0551960000005",
        building_id: "bld_005505",
        lng: -95.38762,
        lat: 29.74566,
        zoom: 17.9,
        pitch: 35,
        story:
          "1912 Avondale residence honored with a 2025 Preservation Houston Good Brick Award.",
      },
    ],
  },
  {
    id: "boulevard_oaks",
    name: "Boulevard Oaks & Rice",
    era: "1912–1938",
    center: [-95.3965, 29.7265],
    zoom: 15.5,
    pitch: 30,
    description:
      "Canopied live-oak boulevards with historic homes by Birdsall Briscoe, John Staub, and William Ward Watkin.",
    stops: [
      {
        title: "Lovett Hall (Rice University, 1912)",
        year: 1912,
        hcad_num: "0421790000001",
        building_id: "0421790000001#rice_58_lovett_hall_administration_b",
        lng: -95.39783,
        lat: 29.71902,
        zoom: 17.5,
        pitch: 40,
        story:
          "1912 Byzantine-Romanesque flagship of Rice University by Cram, Goodhue & Ferguson and supervising architect William Ward Watkin.",
      },
      {
        title: "C. Milby Dow House (1305 South Blvd)",
        year: 1926,
        hcad_num: "0530390000028",
        building_id: "bld_007709",
        lng: -95.39526,
        lat: 29.72591,
        zoom: 17.9,
        pitch: 35,
        story:
          "1926 residence under the live-oak canopy of South Boulevard, recipient of a 2014 Good Brick Award.",
      },
      {
        title: "S.I. Morris House (2 Waverly Ct)",
        year: 1952,
        hcad_num: "0523010000002",
        building_id: "bld_007594",
        lng: -95.39289,
        lat: 29.72582,
        zoom: 17.9,
        pitch: 35,
        story:
          "1952 modernist residence designed by Astrodome co-architect Seth Irvin (S.I.) Morris for his own family; 2026 Good Brick Award recipient.",
      },
    ],
  },
  {
    id: "glenbrook_valley",
    name: "Glenbrook Valley",
    era: "1953–1962",
    center: [-95.2645, 29.6718],
    zoom: 15.3,
    pitch: 25,
    description:
      "Texas's first and largest post-WWII Mid-Century Modern & American Ranch historic district.",
    stops: [
      {
        title: "7919 Glenview Drive Mid-Century Ranch",
        year: 1954,
        hcad_num: "0812870000012",
        building_id: "bld_008232",
        lng: -95.27382,
        lat: 29.67537,
        zoom: 17.8,
        pitch: 30,
        story:
          "1954 custom ranch home in Glenbrook Valley Historic District, honored with a 2013 Good Brick Award for mid-century preservation.",
      },
      {
        title: "Muscanere House (7843 Santa Elena St)",
        year: 1956,
        hcad_num: "0844820000006",
        building_id: "",
        lng: -95.27627,
        lat: 29.67731,
        zoom: 17.8,
        pitch: 30,
        story:
          "1956 Mid-Century Modern Protected Landmark designed by Norman Edwards in Glenbrook Valley.",
      },
    ],
  },
];

/**
 * Return a hex color for a single building year (or decade) under the chosen palette style.
 */
export function getYearColorHex(year, paletteStyle = "archival") {
  const yr = Number(year) || 0;
  if (yr < 1836) {
    return UNKNOWN_YEAR_COLOR;
  }
  const stops = paletteStyle === "classic_ee" ? CLASSIC_EE_YEAR_STOPS : ARCHIVAL_YEAR_STOPS;
  for (const s of stops) {
    if (yr >= s.year && yr <= s.maxYear) {
      return s.color;
    }
  }
  return stops[stops.length - 1].color;
}

/**
 * Build the MapLibre GL JS data-driven `fill-color` / `fill-extrusion-color` expression
 * based on active `colorMode` and `paletteStyle`.
 */
export function buildColorExpression(colorMode = "year_built", paletteStyle = "archival") {
  if (colorMode === "preservation_status") {
    return [
      "case",
      ["==", ["get", "landmark_type"], "Protected Landmark"],
      "#E63946",
      ["==", ["get", "landmark_type"], "Landmark"],
      "#F4A261",
      ["==", ["get", "contributing"], "Contributing"],
      "#2A9D8F",
      ["==", ["get", "contributing"], "Non-Contributing"],
      "#E76F51",
      "#5E6472",
    ];
  }

  if (colorMode === "use_category") {
    return [
      "match",
      ["get", "use_category"],
      "Residential",
      "#E09F3E",
      "Multi-Family",
      "#D66853",
      "Commercial",
      "#3B8EA5",
      "Civic / Institutional",
      "#9B5DE5",
      "Industrial",
      "#7F8C8D",
      "Vacant / Exempt",
      "#4A4A48",
      "#524E4A",
    ];
  }

  const stops = paletteStyle === "classic_ee" ? CLASSIC_EE_YEAR_STOPS : ARCHIVAL_YEAR_STOPS;
  const stepArgs = ["step", ["get", "year_built"], UNKNOWN_YEAR_COLOR];
  for (const s of stops) {
    stepArgs.push(s.year, s.color);
  }
  return [
    "case",
    ["<", ["to-number", ["get", "year_built"], 0], 1836],
    UNKNOWN_YEAR_COLOR,
    stepArgs,
  ];
}

/**
 * Return legend descriptors for the currently active color mode and palette style.
 */
export function getLegendItems(colorMode = "year_built", paletteStyle = "archival") {
  if (colorMode === "preservation_status") {
    return PRESERVATION_STATUS_ITEMS.map((item) => ({
      label: item.label,
      color: item.color,
    }));
  }
  if (colorMode === "use_category") {
    return USE_CATEGORY_ITEMS.map((item) => ({
      label: item.label,
      color: item.color,
    }));
  }
  const stops = paletteStyle === "classic_ee" ? CLASSIC_EE_YEAR_STOPS : ARCHIVAL_YEAR_STOPS;
  return [
    ...stops.map((s) => ({ label: s.label, color: s.color })),
    { label: "Unknown / Vacant / Exempt", color: UNKNOWN_YEAR_COLOR },
  ];
}
