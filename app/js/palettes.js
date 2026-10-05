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
    id: "old_sixth_ward",
    name: "Old Sixth Ward",
    era: "1870s–1900s",
    center: [-95.3772, 29.7664],
    zoom: 16.2,
    pitch: 35,
    description: "Houston's oldest intact historic neighborhood and first Protected Historic District, rich in Victorian cottages.",
  },
  {
    id: "houston_heights",
    name: "Houston Heights",
    era: "1891–1920s",
    center: [-95.3978, 29.7935],
    zoom: 15.3,
    pitch: 30,
    description: "Founded in 1891 as one of Texas's earliest planned streetcar suburbs along grand Heights Boulevard.",
  },
  {
    id: "freedmens_town",
    name: "Freedmen's Town",
    era: "1865–1920s",
    center: [-95.3792, 29.7555],
    zoom: 16.1,
    pitch: 30,
    description: "Historic Fourth Ward community established by emancipated African Americans after Juneteenth 1865.",
  },
  {
    id: "downtown_1836",
    name: "1836 Townsite & Market Sq",
    era: "1836–1930s",
    center: [-95.3622, 29.7615],
    zoom: 15.8,
    pitch: 45,
    description: "The Allen Brothers' original 1836 survey on Buffalo Bayou, cast-iron commercial blocks, and Art Deco towers.",
  },
  {
    id: "avondale_montrose",
    name: "Avondale & Westmoreland",
    era: "1902–1925",
    center: [-95.3845, 29.7445],
    zoom: 15.9,
    pitch: 30,
    description: "Turn-of-the-century streetcar subdivisions featuring Prairie, Craftsman, and American Foursquare residences.",
  },
  {
    id: "boulevard_oaks",
    name: "Boulevard Oaks & Broadacres",
    era: "1923–1938",
    center: [-95.3965, 29.7265],
    zoom: 15.5,
    pitch: 30,
    description: "Canopied live-oak boulevards with historic homes by Birdsall Briscoe, John Staub, and William Ward Watkin.",
  },
  {
    id: "glenbrook_valley",
    name: "Glenbrook Valley",
    era: "1953–1962",
    center: [-95.2645, 29.6718],
    zoom: 15.3,
    pitch: 25,
    description: "Texas's first and largest post-WWII Mid-Century Modern & American Ranch historic district.",
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
