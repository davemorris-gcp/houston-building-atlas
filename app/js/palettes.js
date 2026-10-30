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
  { key: "plm", label: "Protected Landmark (PLM: Ch. 33 COA)", color: "#E63946" },
  { key: "coh_hd_contributing", label: "Contributing in COH Historic District", color: "#2A9D8F" },
  { key: "lm_waiver", label: "90-Day Waiver Landmark (LM Outside HD)", color: "#F59E0B" },
  { key: "nrhp_eligible", label: "NRHP Tax-Credit Eligible (No Demo Ban)", color: "#C084FC" },
  { key: "ch42_deed_armor", label: "Ch. 42 SMLS/SMBL or Deed Armor Zone", color: "#38BDF8" },
  { key: "unprotected_pre1945", label: "Unprotected Pre-1946 Historic Structure", color: "#FB7185" },
  { key: "standard_or_post1945", label: "Post-1945 / Non-Contributing / Standard", color: "#475569" },
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
    "id": "good_brick_highlights",
    "name": "★ Good Brick Highlights",
    "era": "1979-2026",
    "center": [
      -95.36443,
      29.75998
    ],
    "zoom": 15.8,
    "pitch": 42,
    "walkMiles": 0.8,
    "description": "A 0.8-mile Downtown walking tour from Hermann Square to Courthouse Square visiting six Preservation Houston Good Brick Award recipients.",
    "stops": [
      {
        "title": "Julia Ideson Building (Houston Public Library)",
        "year": 1926,
        "hcad_num": "0011470000001",
        "building_id": "bld_003423",
        "lng": -95.36913,
        "lat": 29.75892,
        "zoom": 17.6,
        "pitch": 45,
        "story": "Designed by Cram & Ferguson with William Ward Watkin in Spanish Renaissance Revival style (1926). Restored in a landmark 2012 Good Brick project.",
        "street_lng": -95.36888,
        "street_lat": 29.75934
      },
      {
        "title": "Niels & Mellie Esperson Building",
        "year": 1927,
        "hcad_num": "0010910000015",
        "building_id": "osm_city_510322923#bldg_niels_esperson_building",
        "lng": -95.36504,
        "lat": 29.75886,
        "zoom": 17.6,
        "pitch": 48,
        "story": "Four blocks east on Travis Street, crowned by an Italian Renaissance tempietto designed by theatre architect John Eberson (1927; 1993 Good Brick Award).",
        "street_lng": -95.36485,
        "street_lat": 29.75871
      },
      {
        "title": "1929 Gulf Building (JPMorgan Chase Building)",
        "year": 1929,
        "hcad_num": "0010810000007",
        "building_id": "bld_004281",
        "lng": -95.36422,
        "lat": 29.75908,
        "zoom": 17.6,
        "pitch": 48,
        "story": "One block east at 712 Main Street, Houston's tallest skyscraper from 1929 to 1963 (Alfred C. Finn, Kenneth Franzheim & J.E.R. Carpenter; Good Brick Award 1987 & President's Award 2004).",
        "street_lng": -95.36367,
        "street_lat": 29.75865,
        "citations": [
          {
            "label": "COH Landmark Report 02L097 (Gulf Building)",
            "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/02L097_Gulf_Bldg_712_Main.pdf"
          }
        ]
      },
      {
        "title": "Magnolia Hotel (1926 Houston Post-Dispatch Building)",
        "year": 1926,
        "hcad_num": "0010700000006",
        "building_id": "bld_004289",
        "lng": -95.36161,
        "lat": 29.75906,
        "zoom": 17.6,
        "pitch": 46,
        "story": "Two blocks east at 1100 Texas Avenue, erected in 1926 by Sanguinet & Staats for the Houston Post-Dispatch and adaptively reused in a 2004 Good Brick Award restoration.",
        "street_lng": -95.36146,
        "street_lat": 29.7593
      },
      {
        "title": "Christ Church Cathedral (1893 Sanctuary)",
        "year": 1893,
        "hcad_num": "0010550000006",
        "building_id": "0010550000006",
        "lng": -95.36095,
        "lat": 29.75988,
        "zoom": 17.7,
        "pitch": 44,
        "story": "Directly across Texas Avenue on its original 1839 congregation site, anchored by an 1893 Gothic Revival sanctuary (1991, 1997 & 2020 Good Brick Awards).",
        "street_lng": -95.36117,
        "street_lat": 29.76005
      },
      {
        "title": "1910 Harris County Courthouse",
        "year": 1910,
        "hcad_num": "0010310000001",
        "building_id": "bld_003285",
        "lng": -95.35973,
        "lat": 29.7611,
        "zoom": 17.5,
        "pitch": 45,
        "story": "Two blocks north at 301 Fannin Street, Beaux-Arts Texas pink granite and terra-cotta courthouse by Lang & Witchell, returned to its 1910 grandeur (2012 Good Brick Award).",
        "street_lng": -95.36011,
        "street_lat": 29.7614
      }
    ],
    "routeCoords": [
      [
        -95.36888,
        29.75934
      ],
      [
        -95.36848,
        29.7591
      ],
      [
        -95.36838,
        29.75904
      ],
      [
        -95.3683,
        29.75899
      ],
      [
        -95.36762,
        29.75858
      ],
      [
        -95.36751,
        29.75852
      ],
      [
        -95.36745,
        29.75848
      ],
      [
        -95.36713,
        29.75829
      ],
      [
        -95.36675,
        29.75806
      ],
      [
        -95.36661,
        29.75808
      ],
      [
        -95.36631,
        29.75844
      ],
      [
        -95.36614,
        29.75867
      ],
      [
        -95.366,
        29.75869
      ],
      [
        -95.36531,
        29.75828
      ],
      [
        -95.36516,
        29.75831
      ],
      [
        -95.36485,
        29.75871
      ],
      [
        -95.36485,
        29.75871
      ],
      [
        -95.36468,
        29.75892
      ],
      [
        -95.36455,
        29.75894
      ],
      [
        -95.36424,
        29.75876
      ],
      [
        -95.36387,
        29.75853
      ],
      [
        -95.36371,
        29.75859
      ],
      [
        -95.36367,
        29.75865
      ],
      [
        -95.36367,
        29.75865
      ],
      [
        -95.36327,
        29.75915
      ],
      [
        -95.36304,
        29.75913
      ],
      [
        -95.36301,
        29.75912
      ],
      [
        -95.36253,
        29.75883
      ],
      [
        -95.36237,
        29.75873
      ],
      [
        -95.36222,
        29.75873
      ],
      [
        -95.36173,
        29.75934
      ],
      [
        -95.3616,
        29.75939
      ],
      [
        -95.36146,
        29.7593
      ],
      [
        -95.36146,
        29.7593
      ],
      [
        -95.36166,
        29.75943
      ],
      [
        -95.3616,
        29.7595
      ],
      [
        -95.36117,
        29.76005
      ],
      [
        -95.36117,
        29.76005
      ],
      [
        -95.36109,
        29.76016
      ],
      [
        -95.36104,
        29.76022
      ],
      [
        -95.36099,
        29.76028
      ],
      [
        -95.36076,
        29.76058
      ],
      [
        -95.36049,
        29.76092
      ],
      [
        -95.36044,
        29.76099
      ],
      [
        -95.36038,
        29.76106
      ],
      [
        -95.36011,
        29.7614
      ]
    ],
    "citations": [
      {
        "label": "Preservation Houston: Good Brick Awards (1979-Present)",
        "url": "https://www.preservationhouston.org/awards"
      },
      {
        "label": "COH Landmark Report 02L097 (Gulf Building)",
        "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/02L097_Gulf_Bldg_712_Main.pdf"
      }
    ]
  },
  {
    "id": "art_deco_skyline",
    "name": "Art Deco & Moderne",
    "era": "1926-1940",
    "center": [
      -95.36442,
      29.759
    ],
    "zoom": 15.8,
    "pitch": 48,
    "walkMiles": 0.9,
    "description": "A 0.9-mile Downtown walking tour from Hermann Square to Caroline Street showcasing Joseph Finger, Alfred C. Finn, and Alfred C. Bossom's Deco & Moderne towers.",
    "stops": [
      {
        "title": "Houston City Hall (901 Bagby St)",
        "year": 1939,
        "hcad_num": "0011490000001",
        "building_id": "bld_003326",
        "lng": -95.36938,
        "lat": 29.7602,
        "zoom": 17.5,
        "pitch": 48,
        "story": "Joseph Finger's 1939 PWA Moderne masterpiece clad in Cordova cream limestone overlooking Hermann Square, featuring sculpted reliefs by Herring Coe.",
        "street_lng": -95.36981,
        "street_lat": 29.76046
      },
      {
        "title": "Niels & Mellie Esperson Buildings (808-815 Travis St)",
        "year": 1927,
        "hcad_num": "0010910000015",
        "building_id": "osm_city_510322923#bldg_niels_esperson_building",
        "lng": -95.36504,
        "lat": 29.75886,
        "zoom": 17.6,
        "pitch": 48,
        "story": "Four blocks east on Travis Street, pairing John Eberson's 1927 tower with Douglas Orr's 1941 streamline limestone Mellie Esperson Building annex.",
        "street_lng": -95.36485,
        "street_lat": 29.75871
      },
      {
        "title": "1929 Gulf Building (712 Main St)",
        "year": 1929,
        "hcad_num": "0010810000007",
        "building_id": "bld_004281",
        "lng": -95.36422,
        "lat": 29.75908,
        "zoom": 17.6,
        "pitch": 50,
        "story": "One block east on Main Street, a 36-story Art Deco setback skyscraper modeled on Eliel Saarinen's Tribune Tower design with a soaring Deco banking hall.",
        "street_lng": -95.36367,
        "street_lat": 29.75865
      },
      {
        "title": "Texas State Hotel (720 Fannin St)",
        "year": 1929,
        "hcad_num": "0010800000001",
        "building_id": "bld_005307",
        "lng": -95.36289,
        "lat": 29.75833,
        "zoom": 17.6,
        "pitch": 46,
        "story": "One block southeast at Fannin and Rusk, designed by Joseph Finger in 1929 with early Art Deco massing and Spanish Colonial Revival ornament (2006 Good Brick Award).",
        "street_lng": -95.36267,
        "street_lat": 29.75816
      },
      {
        "title": "The Petroleum Building (1314 Texas Ave)",
        "year": 1927,
        "hcad_num": "0010720000010",
        "building_id": "bld_003241",
        "lng": -95.35955,
        "lat": 29.7578,
        "zoom": 17.6,
        "pitch": 48,
        "story": "Three blocks east on Texas Avenue, a 22-story stepped-pyramid Mayan Art Deco skyscraper designed by Anglo-American architect Alfred C. Bossom (2020 Good Brick Award).",
        "street_lng": -95.3594,
        "street_lat": 29.75805
      },
      {
        "title": "National Cash Register Building (515 Caroline St)",
        "year": 1929,
        "hcad_num": "0010530000008",
        "building_id": "bld_006804",
        "lng": -95.35945,
        "lat": 29.75865,
        "zoom": 17.8,
        "pitch": 42,
        "story": "One block north on Caroline Street, an intimate 1929 Art Deco commercial showroom designed by Joseph Finger and restored in a 2011 Good Brick project.",
        "street_lng": -95.35963,
        "street_lat": 29.7588
      }
    ],
    "routeCoords": [
      [
        -95.36981,
        29.76046
      ],
      [
        -95.36961,
        29.76072
      ],
      [
        -95.36943,
        29.76076
      ],
      [
        -95.36883,
        29.76042
      ],
      [
        -95.3688,
        29.7604
      ],
      [
        -95.36862,
        29.7603
      ],
      [
        -95.36798,
        29.75991
      ],
      [
        -95.36788,
        29.75985
      ],
      [
        -95.36779,
        29.75979
      ],
      [
        -95.3677,
        29.75974
      ],
      [
        -95.36702,
        29.75933
      ],
      [
        -95.36692,
        29.75926
      ],
      [
        -95.36685,
        29.75922
      ],
      [
        -95.36673,
        29.75914
      ],
      [
        -95.36629,
        29.75887
      ],
      [
        -95.36617,
        29.75879
      ],
      [
        -95.36608,
        29.75874
      ],
      [
        -95.366,
        29.75869
      ],
      [
        -95.36531,
        29.75828
      ],
      [
        -95.36516,
        29.75831
      ],
      [
        -95.36485,
        29.75871
      ],
      [
        -95.36485,
        29.75871
      ],
      [
        -95.36468,
        29.75892
      ],
      [
        -95.36455,
        29.75894
      ],
      [
        -95.36424,
        29.75876
      ],
      [
        -95.36387,
        29.75853
      ],
      [
        -95.36371,
        29.75859
      ],
      [
        -95.36367,
        29.75865
      ],
      [
        -95.36367,
        29.75865
      ],
      [
        -95.36362,
        29.7585
      ],
      [
        -95.36352,
        29.75832
      ],
      [
        -95.36298,
        29.75799
      ],
      [
        -95.3628,
        29.75799
      ],
      [
        -95.36267,
        29.75816
      ],
      [
        -95.36267,
        29.75816
      ],
      [
        -95.36234,
        29.75858
      ],
      [
        -95.36219,
        29.75862
      ],
      [
        -95.3618,
        29.75839
      ],
      [
        -95.3615,
        29.75821
      ],
      [
        -95.36141,
        29.75815
      ],
      [
        -95.36133,
        29.75811
      ],
      [
        -95.36055,
        29.75764
      ],
      [
        -95.36024,
        29.75746
      ],
      [
        -95.36014,
        29.75739
      ],
      [
        -95.35969,
        29.75712
      ],
      [
        -95.35958,
        29.75725
      ],
      [
        -95.35947,
        29.7574
      ],
      [
        -95.3594,
        29.75748
      ],
      [
        -95.35915,
        29.7578
      ],
      [
        -95.35918,
        29.75792
      ],
      [
        -95.3594,
        29.75805
      ],
      [
        -95.3594,
        29.75805
      ],
      [
        -95.35954,
        29.75813
      ],
      [
        -95.35987,
        29.75833
      ],
      [
        -95.35989,
        29.75848
      ],
      [
        -95.35963,
        29.7588
      ]
    ],
    "citations": [
      {
        "label": "NRHP Houston City Hall (#90001476)",
        "url": "https://atlas.thc.texas.gov/Details/2090001476"
      },
      {
        "label": "COH Landmark Report 02L097 (Gulf Building)",
        "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/02L097_Gulf_Bldg_712_Main.pdf"
      }
    ]
  },
  {
    "id": "old_sixth_ward",
    "name": "Old Sixth Ward",
    "era": "1850s-1912",
    "center": [
      -95.37795,
      29.76615
    ],
    "zoom": 16.6,
    "pitch": 35,
    "walkMiles": 0.4,
    "description": "A compact 0.4-mile walking loop through Houston's oldest intact historic neighborhood along Lubbock, Kane, and Decatur Streets.",
    "stops": [
      {
        "title": "Heinrich & Hannah Guese House (1819 Lubbock St)",
        "year": 1850,
        "hcad_num": "0052240000015",
        "building_id": "bld_003952",
        "lng": -95.37683,
        "lat": 29.76531,
        "zoom": 18,
        "pitch": 35,
        "story": "Dating to c. 1850-1860 at 1819 Lubbock Street, one of the earliest surviving pre-Civil War vernacular cottages in the Old Sixth Ward (Protected Landmark).",
        "street_lng": -95.37683,
        "street_lat": 29.7655,
        "citations": [
          {
            "label": "COH Protected Landmark Report 06PL23 (Guese House)",
            "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/06PL23_1819_Lubbock_Heinrich_%26_Hannah_Guese_House.pdf"
          }
        ]
      },
      {
        "title": "Kinney-Morrow 1876 Cottage (1817 Kane St)",
        "year": 1876,
        "hcad_num": "0052260000014",
        "building_id": "bld_003930",
        "lng": -95.37683,
        "lat": 29.76603,
        "zoom": 18,
        "pitch": 35,
        "story": "One block north on Kane Street, an authentic 1876 Victorian cottage with jigsaw porch brackets recognized with a 2011 Good Brick Award.",
        "street_lng": -95.37683,
        "street_lat": 29.76624
      },
      {
        "title": "McEvine House (1909 Decatur St)",
        "year": 1868,
        "hcad_num": "0052100000009",
        "building_id": "bld_004074",
        "lng": -95.37767,
        "lat": 29.76678,
        "zoom": 18,
        "pitch": 35,
        "story": "One block northwest on Decatur Street, built c. 1868 as one of the earliest surviving Reconstruction-era cottages in the district (1994 Good Brick Award).",
        "street_lng": -95.37767,
        "street_lat": 29.76697
      },
      {
        "title": "Openshaw-Hutton House (1920 Kane St)",
        "year": 1875,
        "hcad_num": "0052100000001",
        "building_id": "bld_004023",
        "lng": -95.37809,
        "lat": 29.76653,
        "zoom": 18,
        "pitch": 35,
        "story": "Just around the corner at 1920 Kane Street, an 1875 Victorian cottage designated as a City of Houston Protected Landmark.",
        "street_lng": -95.37808,
        "street_lat": 29.76623
      },
      {
        "title": "Dow Elementary School / MECA (1900 Kane St)",
        "year": 1912,
        "hcad_num": "1248130010001",
        "building_id": "bld_003866",
        "lng": -95.37765,
        "lat": 29.7658,
        "zoom": 17.8,
        "pitch": 35,
        "story": "Across Kane Street, designed in 1912 by architect C. H. Page as the civic heart of the Old Sixth Ward; now home to MECA arts center.",
        "street_lng": -95.37765,
        "street_lat": 29.76623
      },
      {
        "title": "The Lighthouse House (2018 Kane St)",
        "year": 1906,
        "hcad_num": "0052000000006",
        "building_id": "bld_004003",
        "lng": -95.37928,
        "lat": 29.76657,
        "zoom": 18,
        "pitch": 35,
        "story": "One block west at 2018 Kane Street, a 1906 turn-of-the-century residence honored with a 2025 Good Brick Award for sensitive historic rehabilitation.",
        "street_lng": -95.37928,
        "street_lat": 29.76622
      }
    ],
    "routeCoords": [
      [
        -95.37683,
        29.7655
      ],
      [
        -95.37708,
        29.7655
      ],
      [
        -95.37715,
        29.76558
      ],
      [
        -95.37716,
        29.76615
      ],
      [
        -95.37705,
        29.76624
      ],
      [
        -95.37683,
        29.76624
      ],
      [
        -95.37683,
        29.76624
      ],
      [
        -95.37705,
        29.76624
      ],
      [
        -95.37716,
        29.76624
      ],
      [
        -95.37717,
        29.76674
      ],
      [
        -95.37717,
        29.76697
      ],
      [
        -95.37723,
        29.76697
      ],
      [
        -95.37728,
        29.76697
      ],
      [
        -95.37756,
        29.76697
      ],
      [
        -95.37767,
        29.76697
      ],
      [
        -95.37767,
        29.76697
      ],
      [
        -95.3778,
        29.76697
      ],
      [
        -95.37794,
        29.76696
      ],
      [
        -95.37798,
        29.76696
      ],
      [
        -95.3781,
        29.76696
      ],
      [
        -95.37819,
        29.76696
      ],
      [
        -95.3782,
        29.76696
      ],
      [
        -95.37829,
        29.76696
      ],
      [
        -95.37828,
        29.76657
      ],
      [
        -95.37828,
        29.76656
      ],
      [
        -95.37827,
        29.76622
      ],
      [
        -95.37808,
        29.76623
      ],
      [
        -95.37808,
        29.76623
      ],
      [
        -95.37765,
        29.76623
      ],
      [
        -95.37765,
        29.76623
      ],
      [
        -95.37808,
        29.76623
      ],
      [
        -95.37827,
        29.76622
      ],
      [
        -95.37928,
        29.76622
      ]
    ],
    "citations": [
      {
        "label": "NRHP Old Sixth Ward Historic District (#78002944)",
        "url": "https://atlas.thc.texas.gov/Details/2078002944"
      },
      {
        "label": "COH Protected Landmark Report 06PL23 (Guese House)",
        "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/06PL23_1819_Lubbock_Heinrich_%26_Hannah_Guese_House.pdf"
      }
    ]
  },
  {
    "id": "houston_heights",
    "name": "Houston Heights",
    "era": "1891-1925",
    "center": [
      -95.39727,
      29.79651
    ],
    "zoom": 15.6,
    "pitch": 30,
    "walkMiles": 0.9,
    "description": "A 0.9-mile stroll north from 12th Street to 16th Street along the tree-lined esplanade of Heights Boulevard and Cortlandt Street.",
    "stops": [
      {
        "title": "Houston Heights City Hall & Fire Station (107 W 12th St)",
        "year": 1914,
        "hcad_num": "0201820000029",
        "building_id": "bld_001955",
        "lng": -95.39864,
        "lat": 29.7927,
        "zoom": 17.8,
        "pitch": 35,
        "story": "Built in 1914 by Alonza C. Pigg at 12th and Yale when Houston Heights was still an independent municipality prior to its 1918 annexation (1997 Good Brick Award).",
        "street_lng": -95.39864,
        "street_lat": 29.79251
      },
      {
        "title": "Heights Branch Library (1302 Heights Blvd)",
        "year": 1925,
        "hcad_num": "0201660000034",
        "building_id": "bld_002046",
        "lng": -95.39723,
        "lat": 29.79472,
        "zoom": 17.8,
        "pitch": 35,
        "story": "Two blocks northeast on the esplanade, an Italian Renaissance Revival branch library built in 1925 (opened March 1926) and designed by architect J. M. Glover.",
        "street_lng": -95.39763,
        "street_lat": 29.79471,
        "citations": [
          {
            "label": "COH Landmark Report 04L120 (Heights Branch Library)",
            "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/04L120_Heights_Library_1302_Heights_Blvd.pdf"
          }
        ]
      },
      {
        "title": "Borgstrom House (1401 Cortlandt St)",
        "year": 1902,
        "hcad_num": "0201540000011",
        "building_id": "bld_001124",
        "lng": -95.3959,
        "lat": 29.79648,
        "zoom": 17.9,
        "pitch": 35,
        "story": "Two blocks north and one block east on Cortlandt Street, an ornate 1902 Queen Anne cottage with Eastlake spindlework by architect R. D. Steele.",
        "street_lng": -95.39543,
        "street_lat": 29.79649
      },
      {
        "title": "O. S. Cummings House (1418 Heights Blvd)",
        "year": 1920,
        "hcad_num": "0201530000017",
        "building_id": "bld_001138",
        "lng": -95.39736,
        "lat": 29.79694,
        "zoom": 17.9,
        "pitch": 35,
        "story": "Back on the 1400 block of Heights Boulevard, a stately 1920 Prairie Style residence overlooking the landscaped esplanade trail.",
        "street_lng": -95.39769,
        "street_lat": 29.79693
      },
      {
        "title": "Houston Heights Church of Christ (1548 Heights Blvd)",
        "year": 1924,
        "hcad_num": "0201360000032",
        "building_id": "bld_001112",
        "lng": -95.39695,
        "lat": 29.79969,
        "zoom": 17.8,
        "pitch": 35,
        "story": "One block north at 16th and Heights Boulevard, a 1924-1925 Classical Revival sanctuary designed by Alfred C. Finn for Houston Heights Church of Christ (now The Church at 1548 Heights; 2025 Good Brick Award).",
        "street_lng": -95.39773,
        "street_lat": 29.79968,
        "citations": [
          {
            "label": "COH Protected Landmark Report 08PL65 (1548 Heights Blvd)",
            "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/08PL65_Hts_Church_of_Christ_1548_Heights.pdf"
          }
        ]
      },
      {
        "title": "1605 Heights Boulevard Historic Residence",
        "year": 1918,
        "hcad_num": "0201290000018",
        "building_id": "bld_001202",
        "lng": -95.39858,
        "lat": 29.80032,
        "zoom": 17.9,
        "pitch": 35,
        "story": "Directly across the Heights Boulevard esplanade at 16th Street, a restored 1918 residence honored with a 2005 Good Brick Award.",
        "street_lng": -95.39802,
        "street_lat": 29.80034
      }
    ],
    "routeCoords": [
      [
        -95.39864,
        29.79251
      ],
      [
        -95.39846,
        29.79251
      ],
      [
        -95.39796,
        29.79252
      ],
      [
        -95.39787,
        29.7926
      ],
      [
        -95.3979,
        29.79391
      ],
      [
        -95.39791,
        29.7943
      ],
      [
        -95.39779,
        29.79438
      ],
      [
        -95.39763,
        29.79446
      ],
      [
        -95.39763,
        29.79471
      ],
      [
        -95.39763,
        29.79471
      ],
      [
        -95.39763,
        29.79446
      ],
      [
        -95.39752,
        29.79438
      ],
      [
        -95.39703,
        29.79439
      ],
      [
        -95.39648,
        29.7944
      ],
      [
        -95.39594,
        29.7944
      ],
      [
        -95.39539,
        29.79441
      ],
      [
        -95.39542,
        29.79618
      ],
      [
        -95.39542,
        29.79619
      ],
      [
        -95.39542,
        29.79626
      ],
      [
        -95.39542,
        29.79636
      ],
      [
        -95.39543,
        29.79649
      ],
      [
        -95.39543,
        29.79649
      ],
      [
        -95.39542,
        29.79638
      ],
      [
        -95.39542,
        29.79636
      ],
      [
        -95.39542,
        29.79633
      ],
      [
        -95.39542,
        29.79626
      ],
      [
        -95.39598,
        29.79625
      ],
      [
        -95.39652,
        29.79624
      ],
      [
        -95.39708,
        29.79623
      ],
      [
        -95.39755,
        29.79623
      ],
      [
        -95.39768,
        29.79629
      ],
      [
        -95.39769,
        29.79693
      ],
      [
        -95.39769,
        29.79693
      ],
      [
        -95.39771,
        29.79807
      ],
      [
        -95.39772,
        29.79926
      ],
      [
        -95.39773,
        29.79959
      ],
      [
        -95.39773,
        29.79968
      ],
      [
        -95.39773,
        29.79968
      ],
      [
        -95.39773,
        29.79975
      ],
      [
        -95.39786,
        29.7999
      ],
      [
        -95.39791,
        29.7999
      ],
      [
        -95.39801,
        29.7999
      ],
      [
        -95.39802,
        29.80034
      ]
    ],
    "citations": [
      {
        "label": "TSHA Handbook of Texas: Houston Heights",
        "url": "https://www.tshaonline.org/handbook/entries/houston-heights-tx"
      },
      {
        "label": "COH Landmark Report 04L120 (Heights Branch Library)",
        "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/04L120_Heights_Library_1302_Heights_Blvd.pdf"
      },
      {
        "label": "COH Protected Landmark Report 08PL65 (1548 Heights Blvd)",
        "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/08PL65_Hts_Church_of_Christ_1548_Heights.pdf"
      }
    ]
  },
  {
    "id": "freedmens_town",
    "name": "Freedmen's Town",
    "era": "1865-1926",
    "center": [
      -95.3789,
      29.7555
    ],
    "zoom": 16.2,
    "pitch": 30,
    "walkMiles": 0.8,
    "description": "A 0.8-mile walking tour along the handmade brick streets of Fourth Ward, established by emancipated African Americans after Juneteenth 1865.",
    "stops": [
      {
        "title": "Bethel Park (Former Bethel Baptist Church, 801 Andrews St)",
        "year": 1923,
        "hcad_num": "0050060000015",
        "building_id": "bld_008961",
        "lng": -95.37625,
        "lat": 29.75562,
        "zoom": 17.9,
        "pitch": 35,
        "story": "Founded by Rev. Jack Yates in the 1890s; the surviving Gothic Revival brick walls by builders John Blount and James Thomas were preserved as a civic park (2014 Good Brick Award).",
        "street_lng": -95.37625,
        "street_lat": 29.75582
      },
      {
        "title": "1111 Saulnier Street Historic Cottage",
        "year": 1925,
        "hcad_num": "0090720000003",
        "building_id": "bld_004785",
        "lng": -95.37917,
        "lat": 29.75692,
        "zoom": 18,
        "pitch": 35,
        "story": "Three blocks west on Saulnier Street, an intact Fourth Ward historic cottage recognized with a Preservation Houston Good Brick Award in 2001.",
        "street_lng": -95.37917,
        "street_lat": 29.75706
      },
      {
        "title": "Freedmen's Town Protected Landmark Cottages (1110 Victor St)",
        "year": 1910,
        "hcad_num": "0050150000011",
        "building_id": "bld_004828",
        "lng": -95.37947,
        "lat": 29.75442,
        "zoom": 18,
        "pitch": 35,
        "story": "Two blocks south on Block 58 (1110 Victor, 1113 Cleveland, and 1505-1507 Cushing Streets), early-20th-century vernacular cottages and duplexes built c. 1910-1925 and preserved as City of Houston Protected Landmarks.",
        "street_lng": -95.37947,
        "street_lat": 29.75392,
        "citations": [
          {
            "label": "COH Protected Landmark Report 10PL95 (1110 Victor St)",
            "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/10PL95_House_at_1110_Victor_St.pdf"
          }
        ]
      },
      {
        "title": "The Gregory School (1300 Victor St)",
        "year": 1926,
        "hcad_num": "0050180000019",
        "building_id": "bld_004304",
        "lng": -95.38049,
        "lat": 29.75421,
        "zoom": 17.8,
        "pitch": 35,
        "story": "One block west on Victor Street, erected in 1926 (Hedrick & Gottlieb) for Gregory School, tracing its roots to the 1870 Freedmen's Bureau Gregory Institute (joined Houston public schools in 1876); now the African American Library at the Gregory School (2010 Good Brick Award).",
        "street_lng": -95.38049,
        "street_lat": 29.75391,
        "citations": [
          {
            "label": "TSHA Handbook of Texas: Fourth Ward, Houston",
            "url": "https://www.tshaonline.org/handbook/entries/fourth-ward-houston"
          }
        ]
      },
      {
        "title": "Rutherford B. H. Yates House (1314 Andrews St)",
        "year": 1912,
        "hcad_num": "0090780000008",
        "building_id": "bld_004891",
        "lng": -95.3809,
        "lat": 29.75596,
        "zoom": 18,
        "pitch": 35,
        "story": "One block north on handmade-brick Andrews Street, the c. 1912 home of printer and civic leader Rutherford B. H. Yates (son of Rev. Jack Yates; 1997 Good Brick Award).",
        "street_lng": -95.3809,
        "street_lat": 29.75577,
        "citations": [
          {
            "label": "Preservation Houston Good Brick Archive (1997 Award)",
            "url": "https://www.preservationhouston.org/awards"
          }
        ]
      },
      {
        "title": "Reverend Ned P. Pullum House (1319 Andrews St)",
        "year": 1898,
        "hcad_num": "0050210000005",
        "building_id": "bld_004904",
        "lng": -95.38121,
        "lat": 29.75565,
        "zoom": 18,
        "pitch": 35,
        "story": "Directly across Andrews Street, built c. 1898 for Fourth Ward minister, brickyard owner, pharmacist, and civic leader Rev. Ned P. Pullum (Protected Landmark).",
        "street_lng": -95.38121,
        "street_lat": 29.75577,
        "citations": [
          {
            "label": "COH Protected Landmark Report 07PL44 (Rev. Ned P. Pullum House)",
            "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/07PL44_Rev_Ned_P_Pullum_House_1319_Andrews.pdf"
          }
        ]
      }
    ],
    "routeCoords": [
      [
        -95.37625,
        29.75582
      ],
      [
        -95.37669,
        29.75581
      ],
      [
        -95.37692,
        29.75581
      ],
      [
        -95.37716,
        29.75581
      ],
      [
        -95.37732,
        29.75581
      ],
      [
        -95.37757,
        29.75581
      ],
      [
        -95.37781,
        29.7558
      ],
      [
        -95.37862,
        29.7558
      ],
      [
        -95.3787,
        29.75586
      ],
      [
        -95.3787,
        29.75615
      ],
      [
        -95.3787,
        29.75643
      ],
      [
        -95.3787,
        29.75672
      ],
      [
        -95.37871,
        29.75706
      ],
      [
        -95.37917,
        29.75706
      ],
      [
        -95.37917,
        29.75706
      ],
      [
        -95.37959,
        29.75706
      ],
      [
        -95.37959,
        29.75703
      ],
      [
        -95.37958,
        29.75642
      ],
      [
        -95.37958,
        29.75609
      ],
      [
        -95.37958,
        29.75579
      ],
      [
        -95.37958,
        29.75576
      ],
      [
        -95.37958,
        29.75551
      ],
      [
        -95.37957,
        29.75544
      ],
      [
        -95.37957,
        29.75516
      ],
      [
        -95.37957,
        29.75484
      ],
      [
        -95.37957,
        29.75454
      ],
      [
        -95.37956,
        29.7545
      ],
      [
        -95.37956,
        29.75392
      ],
      [
        -95.37947,
        29.75392
      ],
      [
        -95.37947,
        29.75392
      ],
      [
        -95.37961,
        29.75392
      ],
      [
        -95.37986,
        29.75391
      ],
      [
        -95.37998,
        29.75391
      ],
      [
        -95.38043,
        29.75391
      ],
      [
        -95.38049,
        29.75391
      ],
      [
        -95.38049,
        29.75391
      ],
      [
        -95.38131,
        29.7539
      ],
      [
        -95.38132,
        29.75448
      ],
      [
        -95.38132,
        29.75452
      ],
      [
        -95.38132,
        29.7547
      ],
      [
        -95.38132,
        29.75482
      ],
      [
        -95.38133,
        29.75511
      ],
      [
        -95.38133,
        29.75515
      ],
      [
        -95.38133,
        29.75544
      ],
      [
        -95.38133,
        29.75574
      ],
      [
        -95.38109,
        29.75577
      ],
      [
        -95.3809,
        29.75577
      ],
      [
        -95.3809,
        29.75577
      ],
      [
        -95.38109,
        29.75577
      ],
      [
        -95.38121,
        29.75577
      ]
    ],
    "citations": [
      {
        "label": "NRHP Freedmen's Town Historic District (#85000118)",
        "url": "https://atlas.thc.texas.gov/Details/2085000118"
      },
      {
        "label": "TSHA Handbook of Texas: Fourth Ward, Houston",
        "url": "https://www.tshaonline.org/handbook/entries/fourth-ward-houston"
      },
      {
        "label": "COH Protected Landmark Report 07PL44 (Rev. Ned P. Pullum House)",
        "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/07PL44_Rev_Ned_P_Pullum_House_1319_Andrews.pdf"
      }
    ]
  },
  {
    "id": "downtown_1836",
    "name": "1836 Townsite & Market Sq",
    "era": "1836-1917",
    "center": [
      -95.36125,
      29.76185
    ],
    "zoom": 16.4,
    "pitch": 45,
    "walkMiles": 0.6,
    "description": "A 0.6-mile walk through the Allen Brothers' original 1836 survey around Market Square, 19th-century cast-iron storefronts, and the 1837 Capitol site.",
    "stops": [
      {
        "title": "Market Square Park (1836 Congress Square, 301 Milam St)",
        "year": 1900,
        "hcad_num": "0010340000001",
        "building_id": "bld_004278",
        "lng": -95.36218,
        "lat": 29.76265,
        "zoom": 17.7,
        "pitch": 42,
        "story": "Platted as 'Congress Square' in the Allen Brothers' 1836 survey and site of four successive Houston City Halls and Market Houses between 1841 and 1904 (1996 & 2011 Good Brick Awards).",
        "street_lng": -95.36267,
        "street_lat": 29.76304
      },
      {
        "title": "Hermann Estate & W. L. Foley Buildings (204-218 Travis St)",
        "year": 1917,
        "hcad_num": "0010190000020",
        "building_id": "bld_003445",
        "lng": -95.36162,
        "lat": 29.76316,
        "zoom": 17.8,
        "pitch": 42,
        "story": "Facing Market Square on Travis Street, pairing Louis A. Glover's eight-story 1917 Neoclassical Hermann Estate Building (204 Travis; 1998 Good Brick Award) with Eugene T. Heiner's three-story 1889 Renaissance Revival W. L. Foley Dry Goods Building next door (214 Travis; 1996 Good Brick Award).",
        "street_lng": -95.36144,
        "street_lat": 29.76302,
        "citations": [
          {
            "label": "COH Protected Landmark Report 14PL118 (204 Travis St)",
            "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/14PL118_204_Travis.pdf"
          }
        ]
      },
      {
        "title": "Kiam Building (320 Main St)",
        "year": 1893,
        "hcad_num": "0010330000013",
        "building_id": "bld_006816",
        "lng": -95.36148,
        "lat": 29.76178,
        "zoom": 17.8,
        "pitch": 42,
        "story": "One block south at Main and Preston, erected in 1893 by merchant Ed Kiam (architect H. C. Holland) as a five-story Romanesque Revival masonry commercial landmark famed for early electric lighting and passenger elevator service (1981 Good Brick Award).",
        "street_lng": -95.3613,
        "street_lat": 29.76164
      },
      {
        "title": "Pillot Building (300 Fannin St)",
        "year": 1858,
        "hcad_num": "0010320000004",
        "building_id": "bld_010126",
        "lng": -95.36044,
        "lat": 29.76164,
        "zoom": 17.9,
        "pitch": 42,
        "story": "One block north and east at Fannin and Congress, originally erected c. 1857-1860 with an early cast-iron storefront and reconstructed in 1990 using its salvaged original cast-iron columns and lintels after a 1988 collapse (1984 & 1992 Good Brick Awards).",
        "street_lng": -95.36075,
        "street_lat": 29.76116
      },
      {
        "title": "1910 Harris County Courthouse (301 Fannin St)",
        "year": 1910,
        "hcad_num": "0010310000001",
        "building_id": "bld_003285",
        "lng": -95.35973,
        "lat": 29.7611,
        "zoom": 17.6,
        "pitch": 45,
        "story": "Directly across Fannin Street on Courthouse Square, where five successive county courthouses have stood on the original 1836 public reserve since 1838.",
        "street_lng": -95.36011,
        "street_lat": 29.7614
      },
      {
        "title": "The Rice Hotel (1837 Capitol Site, 909 Texas Ave)",
        "year": 1913,
        "hcad_num": "0010570000009",
        "building_id": "bld_003226",
        "lng": -95.36277,
        "lat": 29.76054,
        "zoom": 17.6,
        "pitch": 45,
        "story": "Three blocks west at Main and Texas, erected by William Marsh Rice's estate in 1913 on the exact corner where the 1837 Capitol of the Republic of Texas stood.",
        "street_lng": -95.36297,
        "street_lat": 29.76021
      }
    ],
    "routeCoords": [
      [
        -95.36267,
        29.76304
      ],
      [
        -95.36253,
        29.76322
      ],
      [
        -95.36239,
        29.76323
      ],
      [
        -95.36172,
        29.76283
      ],
      [
        -95.36157,
        29.76285
      ],
      [
        -95.36144,
        29.76302
      ],
      [
        -95.36144,
        29.76302
      ],
      [
        -95.36157,
        29.76285
      ],
      [
        -95.36157,
        29.76273
      ],
      [
        -95.36134,
        29.76259
      ],
      [
        -95.36084,
        29.7623
      ],
      [
        -95.36084,
        29.76222
      ],
      [
        -95.3613,
        29.76164
      ],
      [
        -95.3613,
        29.76164
      ],
      [
        -95.36124,
        29.76151
      ],
      [
        -95.36123,
        29.76141
      ],
      [
        -95.36089,
        29.76123
      ],
      [
        -95.36075,
        29.76116
      ],
      [
        -95.36075,
        29.76116
      ],
      [
        -95.36054,
        29.76104
      ],
      [
        -95.36038,
        29.76106
      ],
      [
        -95.36011,
        29.7614
      ],
      [
        -95.36011,
        29.7614
      ],
      [
        -95.35992,
        29.76165
      ],
      [
        -95.35996,
        29.76177
      ],
      [
        -95.36028,
        29.76196
      ],
      [
        -95.3604,
        29.76203
      ],
      [
        -95.36063,
        29.76217
      ],
      [
        -95.36076,
        29.76212
      ],
      [
        -95.36124,
        29.76151
      ],
      [
        -95.36146,
        29.76145
      ],
      [
        -95.3619,
        29.76089
      ],
      [
        -95.36192,
        29.76086
      ],
      [
        -95.36197,
        29.76079
      ],
      [
        -95.36202,
        29.76073
      ],
      [
        -95.36252,
        29.7601
      ],
      [
        -95.36268,
        29.76004
      ],
      [
        -95.36297,
        29.76021
      ]
    ],
    "citations": [
      {
        "label": "NRHP Main Street / Market Square Historic District (#83003796)",
        "url": "https://atlas.thc.texas.gov/Details/2083003796"
      },
      {
        "label": "COH Protected Landmark Report 14PL118 (204 Travis St)",
        "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/14PL118_204_Travis.pdf"
      }
    ]
  },
  {
    "id": "avondale_montrose",
    "name": "Avondale & Courtlandt",
    "era": "1906-1925",
    "center": [
      -95.38522,
      29.74475
    ],
    "zoom": 16.5,
    "pitch": 30,
    "walkMiles": 0.5,
    "description": "A 0.5-mile architectural walk along private gated Courtlandt Place (1906) and neighboring Avondale Street (1907).",
    "stops": [
      {
        "title": "C. L. Neuhaus House (6 Courtlandt Pl)",
        "year": 1909,
        "hcad_num": "0102490000003",
        "building_id": "bld_006128",
        "lng": -95.38282,
        "lat": 29.74396,
        "zoom": 17.9,
        "pitch": 35,
        "story": "Built in 1909 near the eastern gates of Courtlandt Place, Houston's premier turn-of-the-century private residential boulevard (2020 Good Brick Award).",
        "street_lng": -95.38281,
        "street_lat": 29.74364
      },
      {
        "title": "W. T. Carter Jr. House (18 Courtlandt Pl)",
        "year": 1912,
        "hcad_num": "0102490000010",
        "building_id": "bld_004332",
        "lng": -95.38483,
        "lat": 29.74389,
        "zoom": 17.9,
        "pitch": 35,
        "story": "Midway down the esplanade, a 1912 Prairie Style mansion designed by Olle Lorehn and Birdsall P. Briscoe.",
        "street_lng": -95.38482,
        "street_lat": 29.74359
      },
      {
        "title": "Baker-Jones House (22 Courtlandt Pl)",
        "year": 1917,
        "hcad_num": "0102490000012",
        "building_id": "bld_004330",
        "lng": -95.38541,
        "lat": 29.74385,
        "zoom": 17.9,
        "pitch": 35,
        "story": "Two doors west at 22 Courtlandt Place, a 1917 Georgian Revival residence designed by renowned Houston architect Birdsall P. Briscoe.",
        "street_lng": -95.3854,
        "street_lat": 29.74358
      },
      {
        "title": "Jones-Hunt House (24 Courtlandt Pl)",
        "year": 1919,
        "hcad_num": "0102490000013",
        "building_id": "bld_004331",
        "lng": -95.38579,
        "lat": 29.74386,
        "zoom": 17.9,
        "pitch": 35,
        "story": "Next door at 24 Courtlandt Place, a 1919 Tudor Revival mansion designed by Gulf Building architect Alfred C. Finn.",
        "street_lng": -95.38577,
        "street_lat": 29.74357
      },
      {
        "title": "Edward Weil House (308 Avondale St)",
        "year": 1917,
        "hcad_num": "0041400000004",
        "building_id": "bld_005454",
        "lng": -95.38561,
        "lat": 29.74519,
        "zoom": 17.9,
        "pitch": 35,
        "story": "One block north on Avondale Street between the Avondale East and Avondale West Historic Districts, a 1917 Prairie Style residence by the Russell Brown Company (Protected Landmark).",
        "street_lng": -95.38562,
        "street_lat": 29.74547,
        "citations": [
          {
            "label": "COH Protected Landmark Report 11PL100 (Edward Weil House)",
            "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/11PL100_Edward_Weil_House_308_Avondale.pdf"
          }
        ]
      },
      {
        "title": "Martha Perlitz House (503 Avondale St)",
        "year": 1912,
        "hcad_num": "0551960000005",
        "building_id": "bld_005505",
        "lng": -95.38763,
        "lat": 29.74564,
        "zoom": 17.9,
        "pitch": 35,
        "story": "Two blocks west on Avondale Street, a 1912 residence by Alonzo N. Dawson honored with a 2025 Preservation Houston Good Brick Award.",
        "street_lng": -95.38762,
        "street_lat": 29.74542
      }
    ],
    "routeCoords": [
      [
        -95.38281,
        29.74364
      ],
      [
        -95.38305,
        29.74363
      ],
      [
        -95.38399,
        29.74361
      ],
      [
        -95.38482,
        29.74359
      ],
      [
        -95.38482,
        29.74359
      ],
      [
        -95.38491,
        29.74359
      ],
      [
        -95.3854,
        29.74358
      ],
      [
        -95.3854,
        29.74358
      ],
      [
        -95.38553,
        29.74357
      ],
      [
        -95.38577,
        29.74357
      ],
      [
        -95.38577,
        29.74357
      ],
      [
        -95.38594,
        29.74356
      ],
      [
        -95.38595,
        29.74388
      ],
      [
        -95.38596,
        29.74424
      ],
      [
        -95.38596,
        29.7443
      ],
      [
        -95.38596,
        29.7445
      ],
      [
        -95.38596,
        29.74458
      ],
      [
        -95.38596,
        29.74465
      ],
      [
        -95.38597,
        29.74487
      ],
      [
        -95.38597,
        29.74498
      ],
      [
        -95.38598,
        29.74537
      ],
      [
        -95.3858,
        29.74546
      ],
      [
        -95.38562,
        29.74547
      ],
      [
        -95.38562,
        29.74547
      ],
      [
        -95.3858,
        29.74546
      ],
      [
        -95.38589,
        29.74546
      ],
      [
        -95.3859,
        29.74546
      ],
      [
        -95.38598,
        29.74546
      ],
      [
        -95.38605,
        29.74546
      ],
      [
        -95.38606,
        29.74546
      ],
      [
        -95.38725,
        29.74543
      ],
      [
        -95.38762,
        29.74542
      ]
    ],
    "citations": [
      {
        "label": "NRHP Courtlandt Place Historic District (#79002956)",
        "url": "https://atlas.thc.texas.gov/Details/2079002956"
      },
      {
        "label": "COH Protected Landmark Report 11PL100 (Edward Weil House)",
        "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/11PL100_Edward_Weil_House_308_Avondale.pdf"
      }
    ]
  },
  {
    "id": "boulevard_oaks",
    "name": "Broadacres & Shadow Lawn",
    "era": "1924-1968",
    "center": [
      -95.39587,
      29.72628
    ],
    "zoom": 16.3,
    "pitch": 30,
    "walkMiles": 1.2,
    "description": "A 1.2-mile walk beneath the live-oak canopies of Broadacres (North and South Boulevards), Institute Lane, Shadow Lawn, and Waverly Court.",
    "stops": [
      {
        "title": "Joseph A. Tennant House (1505 North Blvd)",
        "year": 1926,
        "hcad_num": "0530390000008",
        "building_id": "bld_007720",
        "lng": -95.39872,
        "lat": 29.72759,
        "zoom": 17.9,
        "pitch": 35,
        "story": "1926 Georgian Revival residence on the live-oak esplanade of North Boulevard, honored with a 2021 Good Brick Award.",
        "street_lng": -95.39873,
        "street_lat": 29.72789
      },
      {
        "title": "Ned A. Eppes House (5322 Institute Ln) & Paseur House (5306 Institute Ln)",
        "year": 1926,
        "hcad_num": "1240700020001",
        "building_id": "bld_007582",
        "lng": -95.39741,
        "lat": 29.72488,
        "zoom": 17.9,
        "pitch": 35,
        "story": "Two blocks south on Institute Lane, pairing the 1926 Renaissance Revival Ned A. Eppes House at 5322 Institute Lane (COH Landmark & NRHP) with CRS president Herb Paseur's 1968 modernist residence next door at 5306 Institute Lane (2008 Good Brick Award).",
        "street_lng": -95.39712,
        "street_lat": 29.72488,
        "citations": [
          {
            "label": "COH Landmark Report 96LD39 (Ned A. Eppes House, 5322 Institute Ln)",
            "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/96LD39_5322_Institute_Ned_A_Eppes_House.pdf"
          }
        ]
      },
      {
        "title": "1405 South Boulevard Historic Residence",
        "year": 1924,
        "hcad_num": "0530390000004",
        "building_id": "bld_007583",
        "lng": -95.39714,
        "lat": 29.7262,
        "zoom": 17.9,
        "pitch": 35,
        "story": "North onto tree-lined South Boulevard, an early 1924 Broadacres Historic District residence recognized with a 2008 Good Brick Award.",
        "street_lng": -95.39714,
        "street_lat": 29.72652
      },
      {
        "title": "C. Milby Dow House (1305 South Blvd)",
        "year": 1926,
        "hcad_num": "0530390000028",
        "building_id": "bld_007709",
        "lng": -95.39509,
        "lat": 29.72619,
        "zoom": 17.9,
        "pitch": 35,
        "story": "One block east under the live-oak arch of South Boulevard, a 1926 John F. Staub estate residence and historic carriage house honored with a 2014 Good Brick Award.",
        "street_lng": -95.39509,
        "street_lat": 29.72654
      },
      {
        "title": "Arthur J. Hurt House (4 Shadow Lawn St)",
        "year": 1929,
        "hcad_num": "0523230010001",
        "building_id": "bld_007704",
        "lng": -95.39391,
        "lat": 29.72563,
        "zoom": 17.9,
        "pitch": 35,
        "story": "Just off the eastern end of South Boulevard on tranquil Shadow Lawn Street in the Shadow Lawn Historic District, a 1929 Colonial Revival residence (2019 Good Brick Award).",
        "street_lng": -95.39434,
        "street_lat": 29.72535
      },
      {
        "title": "S.I. Morris House (2 Waverly Ct)",
        "year": 1952,
        "hcad_num": "0523010000002",
        "building_id": "bld_007594",
        "lng": -95.39289,
        "lat": 29.72582,
        "zoom": 17.9,
        "pitch": 35,
        "story": "One block east on Waverly Court, a 1952 modernist residence designed by Astrodome co-architect Seth Irvin (S.I.) Morris for his own family (2026 Good Brick Award).",
        "street_lng": -95.39325,
        "street_lat": 29.72581
      }
    ],
    "routeCoords": [
      [
        -95.39873,
        29.72789
      ],
      [
        -95.39893,
        29.72788
      ],
      [
        -95.39945,
        29.72788
      ],
      [
        -95.39945,
        29.72727
      ],
      [
        -95.39946,
        29.72669
      ],
      [
        -95.39944,
        29.72579
      ],
      [
        -95.39935,
        29.72571
      ],
      [
        -95.39889,
        29.72571
      ],
      [
        -95.39866,
        29.72572
      ],
      [
        -95.3984,
        29.72572
      ],
      [
        -95.39802,
        29.72573
      ],
      [
        -95.3978,
        29.72573
      ],
      [
        -95.39723,
        29.72574
      ],
      [
        -95.39712,
        29.72568
      ],
      [
        -95.39712,
        29.72488
      ],
      [
        -95.39712,
        29.72488
      ],
      [
        -95.39712,
        29.72568
      ],
      [
        -95.39712,
        29.7257
      ],
      [
        -95.39712,
        29.72574
      ],
      [
        -95.39626,
        29.72575
      ],
      [
        -95.39621,
        29.72575
      ],
      [
        -95.39534,
        29.72577
      ],
      [
        -95.39523,
        29.72578
      ],
      [
        -95.39512,
        29.7258
      ],
      [
        -95.39503,
        29.72583
      ],
      [
        -95.39493,
        29.72587
      ],
      [
        -95.39471,
        29.72598
      ],
      [
        -95.39464,
        29.72614
      ],
      [
        -95.39502,
        29.72654
      ],
      [
        -95.39506,
        29.72662
      ],
      [
        -95.39511,
        29.72669
      ],
      [
        -95.3966,
        29.72669
      ],
      [
        -95.3966,
        29.7266
      ],
      [
        -95.3966,
        29.72653
      ],
      [
        -95.39714,
        29.72652
      ],
      [
        -95.39714,
        29.72652
      ],
      [
        -95.3966,
        29.72653
      ],
      [
        -95.39509,
        29.72654
      ],
      [
        -95.39509,
        29.72654
      ],
      [
        -95.39502,
        29.72654
      ],
      [
        -95.39464,
        29.72614
      ],
      [
        -95.3946,
        29.7261
      ],
      [
        -95.3946,
        29.72603
      ],
      [
        -95.3946,
        29.72595
      ],
      [
        -95.3946,
        29.72593
      ],
      [
        -95.3946,
        29.72588
      ],
      [
        -95.3946,
        29.72575
      ],
      [
        -95.3946,
        29.72572
      ],
      [
        -95.39459,
        29.72543
      ],
      [
        -95.39454,
        29.72542
      ],
      [
        -95.39451,
        29.72541
      ],
      [
        -95.39444,
        29.72539
      ],
      [
        -95.39437,
        29.72537
      ],
      [
        -95.39434,
        29.72535
      ],
      [
        -95.39434,
        29.72535
      ],
      [
        -95.39376,
        29.72535
      ],
      [
        -95.39369,
        29.72535
      ],
      [
        -95.3937,
        29.72578
      ],
      [
        -95.3937,
        29.72604
      ],
      [
        -95.3937,
        29.72607
      ],
      [
        -95.39371,
        29.72612
      ],
      [
        -95.39326,
        29.72613
      ],
      [
        -95.39326,
        29.72608
      ],
      [
        -95.39325,
        29.72581
      ]
    ],
    "citations": [
      {
        "label": "NRHP Broadacres Historic District (#80004132)",
        "url": "https://atlas.thc.texas.gov/Details/2080004132"
      },
      {
        "label": "COH Landmark Report 96LD39 (Ned A. Eppes House, 5322 Institute Ln)",
        "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/96LD39_5322_Institute_Ned_A_Eppes_House.pdf"
      }
    ]
  },
  {
    "id": "glenbrook_valley",
    "name": "Glenbrook Valley",
    "era": "1953-1960",
    "center": [
      -95.27505,
      29.6763
    ],
    "zoom": 16.7,
    "pitch": 28,
    "walkMiles": 0.7,
    "description": "A 0.7-mile walk along Santa Elena Street, Glen Valley Drive, Stony Dell Court, and Glenview Drive in Texas's first post-WWII Mid-Century Modern municipal historic district.",
    "stops": [
      {
        "title": "Muscanere House (7843 Santa Elena St)",
        "year": 1956,
        "hcad_num": "0844820000006",
        "building_id": "bld_008259",
        "lng": -95.27626,
        "lat": 29.67724,
        "zoom": 18,
        "pitch": 32,
        "story": "1956 Mid-Century Modern Landmark designed by Norman Edwards for Salvatore and Lily Ann Muscanere in Glenbrook Valley.",
        "street_lng": -95.27626,
        "street_lat": 29.67706
      },
      {
        "title": "7846 Santa Elena Street Custom Ranch",
        "year": 1960,
        "hcad_num": "0844800000010",
        "building_id": "bld_008246",
        "lng": -95.27605,
        "lat": 29.6766,
        "zoom": 18,
        "pitch": 32,
        "story": "Just down curving Santa Elena Street, an expansive 1960 low-slung brick and stone ranch exemplifying Glenbrook Valley's horizontal mid-century aesthetic.",
        "street_lng": -95.27606,
        "street_lat": 29.67707
      },
      {
        "title": "8114 Stony Dell Court Mid-Century Ranch",
        "year": 1955,
        "hcad_num": "0812870000026",
        "building_id": "bld_008313",
        "lng": -95.2746,
        "lat": 29.67609,
        "zoom": 18,
        "pitch": 32,
        "story": "Just east off Santa Elena on Stony Dell Court, an intact 1955 contributing mid-century ranch house on one of the neighborhood's quiet courts.",
        "street_lng": -95.2743,
        "street_lat": 29.67611
      },
      {
        "title": "8111 Glen Valley Drive (1953 First-Section Ranch)",
        "year": 1953,
        "hcad_num": "0812870000020",
        "building_id": "bld_008956",
        "lng": -95.275,
        "lat": 29.67634,
        "zoom": 18,
        "pitch": 32,
        "story": "Around the corner on Glen Valley Drive, built in 1953 as developer Fred McManis Jr. opened Glenbrook Valley ahead of its acclaimed 1954 Houston Parade of Homes showcase.",
        "street_lng": -95.27526,
        "street_lat": 29.67633,
        "citations": [
          {
            "label": "COH Landmark Report 12L271 (Muscanere House & Glenbrook Valley)",
            "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/12L271_Muscanere_House_7843_Santa_Elena.pdf"
          }
        ]
      },
      {
        "title": "7919 Glenview Drive Good Brick Ranch",
        "year": 1954,
        "hcad_num": "0812870000012",
        "building_id": "bld_008232",
        "lng": -95.27383,
        "lat": 29.67536,
        "zoom": 18,
        "pitch": 32,
        "story": "At the southern end of the court on boulevard-wide Glenview Drive, a 1954 custom ranch home honored with a 2013 Good Brick Award for mid-century preservation.",
        "street_lng": -95.27382,
        "street_lat": 29.67497
      }
    ],
    "routeCoords": [
      [
        -95.27626,
        29.67706
      ],
      [
        -95.27606,
        29.67707
      ],
      [
        -95.27606,
        29.67707
      ],
      [
        -95.27522,
        29.67708
      ],
      [
        -95.2749,
        29.67709
      ],
      [
        -95.27462,
        29.6771
      ],
      [
        -95.27434,
        29.67714
      ],
      [
        -95.27406,
        29.67721
      ],
      [
        -95.27386,
        29.67679
      ],
      [
        -95.27406,
        29.67669
      ],
      [
        -95.27413,
        29.67663
      ],
      [
        -95.2742,
        29.67656
      ],
      [
        -95.27425,
        29.67648
      ],
      [
        -95.27429,
        29.67636
      ],
      [
        -95.27431,
        29.67621
      ],
      [
        -95.2743,
        29.67611
      ],
      [
        -95.2743,
        29.67611
      ],
      [
        -95.27431,
        29.67621
      ],
      [
        -95.27429,
        29.67636
      ],
      [
        -95.2742,
        29.67656
      ],
      [
        -95.27406,
        29.67669
      ],
      [
        -95.27386,
        29.67679
      ],
      [
        -95.27406,
        29.67721
      ],
      [
        -95.27411,
        29.67736
      ],
      [
        -95.27412,
        29.67739
      ],
      [
        -95.27418,
        29.67761
      ],
      [
        -95.27419,
        29.67764
      ],
      [
        -95.27422,
        29.6777
      ],
      [
        -95.2743,
        29.67779
      ],
      [
        -95.27444,
        29.67788
      ],
      [
        -95.27458,
        29.67794
      ],
      [
        -95.2753,
        29.67821
      ],
      [
        -95.2753,
        29.67767
      ],
      [
        -95.27527,
        29.67747
      ],
      [
        -95.27524,
        29.67726
      ],
      [
        -95.27522,
        29.67708
      ],
      [
        -95.27522,
        29.67693
      ],
      [
        -95.27524,
        29.67676
      ],
      [
        -95.27527,
        29.67656
      ],
      [
        -95.27527,
        29.67646
      ],
      [
        -95.27526,
        29.67633
      ],
      [
        -95.27526,
        29.67633
      ],
      [
        -95.27526,
        29.6762
      ],
      [
        -95.27525,
        29.67592
      ],
      [
        -95.27525,
        29.67589
      ],
      [
        -95.27525,
        29.67562
      ],
      [
        -95.27523,
        29.67493
      ],
      [
        -95.27382,
        29.67497
      ]
    ],
    "citations": [
      {
        "label": "COH Landmark Report 12L271 (Muscanere House & Glenbrook Valley)",
        "url": "https://www.houstontx.gov/planning/HistoricPres/landmarks/12L271_Muscanere_House_7843_Santa_Elena.pdf"
      }
    ]
  }
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
      ["==", ["get", "protection_tier"], "plm"],
      "#E63946",
      ["==", ["get", "landmark_type"], "Protected Landmark"],
      "#E63946",
      ["==", ["get", "protection_tier"], "coh_hd_contributing"],
      "#2A9D8F",
      ["==", ["get", "contributing"], "Contributing"],
      "#2A9D8F",
      ["==", ["get", "protection_tier"], "lm_waiver"],
      "#F59E0B",
      ["==", ["get", "landmark_type"], "Landmark"],
      "#F59E0B",
      ["==", ["get", "protection_tier"], "nrhp_eligible"],
      "#C084FC",
      ["==", ["get", "protection_tier"], "coh_hd_noncontributing"],
      "#64748B",
      ["==", ["get", "contributing"], "Non-Contributing"],
      "#64748B",
      [
        "all",
        [">=", ["to-number", ["get", "year_built"], 0], 1836],
        ["<=", ["to-number", ["get", "year_built"], 0], 1945],
      ],
      "#FB7185",
      "#475569",
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

export const SANBORN_MATERIAL_ITEMS = [
  {
    key: "wood_frame",
    code: "wood_frame",
    color: "#FACC15",
    fillColor: "rgba(250, 204, 21, 0.28)",
    strokeColor: "#FACC15",
    stroke: "#B88B14",
    label: "Yellow - Wood Frame / Timber",
  },
  {
    key: "brick",
    code: "brick",
    color: "#F472B6",
    fillColor: "rgba(244, 114, 182, 0.28)",
    strokeColor: "#F472B6",
    stroke: "#A8384B",
    label: "Pink - Brick / Masonry",
  },
  {
    key: "stone",
    code: "stone",
    color: "#60A5FA",
    fillColor: "rgba(96, 165, 250, 0.28)",
    strokeColor: "#60A5FA",
    stroke: "#2B6CB0",
    label: "Blue - Cut Stone / Concrete Block",
  },
  {
    key: "iron_metal",
    code: "iron_metal",
    color: "#CBD5E1",
    fillColor: "rgba(203, 213, 225, 0.28)",
    strokeColor: "#CBD5E1",
    stroke: "#5E548E",
    label: "Gray - Iron / Clad Metal / Steel",
  },
  {
    key: "fireproof",
    code: "fireproof",
    color: "#A3B18A",
    fillColor: "rgba(163, 177, 138, 0.30)",
    strokeColor: "#A3B18A",
    stroke: "#3F6A34",
    label: "Olive - Fireproof Steel & Concrete",
  },
];

export function resolveSanbornMaterialColors(materialKey) {
  const raw = String(materialKey || "brick")
    .trim()
    .toLowerCase();
  const norm =
    raw === "frame"
      ? "wood_frame"
      : raw === "stone_concrete"
      ? "stone"
      : raw === "iron_steel" || raw === "iron"
      ? "iron_metal"
      : raw;
  const found = SANBORN_MATERIAL_ITEMS.find((m) => m.key === norm);
  return (
    found || {
      key: "brick",
      code: "brick",
      color: "#F472B6",
      fillColor: "rgba(244, 114, 182, 0.28)",
      strokeColor: "#F472B6",
      stroke: "#A8384B",
      label: "Pink - Brick / Masonry",
    }
  );
}

export const HISTORICAL_MAP_CATALOG = [
  {
    id: "sanborn_1907",
    label: "1907 Houston Sanborn Fire Insurance Mosaic (Vols. 1 & 2)",
    shortLabel: "1907 Sanborn Mosaic",
    year: 1907,
    category: "Sanborn Fire Insurance Atlases",
    isSanborn: true,
    tileUrl: "https://tiles.arcgis.com/tiles/zmk8NL9pBXO4ybJQ/arcgis/rest/services/Houston_Sandborn_Map_1907/MapServer/tile/{z}/{y}/{x}",
    minzoom: 12,
    maxzoom: 17,
    minZoom: 12,
    maxZoom: 17,
    attribution: "Sanborn Map Co. (1907) / Library of Congress & Rice University GIS",
    description: "Georeferenced composite of the 1907 Houston Sanborn Fire Insurance Atlas (Volumes 1 & 2, 195 sheets) covering Downtown, Wards 1-6, and Houston Heights.",
  },
  {
    id: "sanborn_1907_downtown_hires",
    label: "1907 Sanborn Downtown & East End Ultra-High-Res (Zoom 14-21)",
    shortLabel: "1907 Sanborn Ultra-HiRes",
    year: 1907,
    category: "Sanborn Fire Insurance Atlases",
    isSanborn: true,
    tileUrl: "https://tiles.arcgis.com/tiles/EEOLw7FvbUFCdg7T/arcgis/rest/services/Houston_1907/MapServer/tile/{z}/{y}/{x}",
    minzoom: 14,
    maxzoom: 21,
    minZoom: 14,
    maxZoom: 21,
    attribution: "Sanborn Map Co. (1907) / UT Austin Perry-Castaneda Library",
    description: "Ultra-high-resolution georeferenced 1907 Sanborn sheets allowing street-level inspection of skylights, firewalls, and building stories up to zoom 21.",
  },
  {
    id: "wood_1869",
    label: "1869 W. E. Wood Map of Houston (Reconstruction Era Wards)",
    shortLabel: "1869 W. E. Wood Map",
    year: 1869,
    category: "19th & Early 20th Century City Maps",
    isSanborn: false,
    tileUrl: "https://mycity2.houstontx.gov/pubgis04/rest/services/HPL/HistoricHouston/MapServer/export?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=512,512&format=png32&transparent=true&layers=show:9&f=image",
    minzoom: 11,
    maxzoom: 18,
    minZoom: 11,
    maxZoom: 18,
    attribution: "W. E. Wood Civil Engineer (1869) / Houston Public Library HMRC",
    description: "Surveyed by civil engineer W. E. Wood in 1869, showing Reconstruction-era Houston lots, early railroads, Quality Hill, Frost Town, and the original Ward boundaries.",
  },
  {
    id: "whitty_1913",
    label: "1913 Official Map of the City of Houston",
    shortLabel: "1913 Houston City Map",
    year: 1913,
    category: "19th & Early 20th Century City Maps",
    isSanborn: false,
    tileUrl: "https://tiles.arcgis.com/tiles/lqRTrQp2HrfnJt8U/arcgis/rest/services/1913_Houston_Map/MapServer/tile/{z}/{y}/{x}",
    minzoom: 11,
    maxzoom: 18,
    minZoom: 11,
    maxZoom: 18,
    attribution: "City of Houston (1913) / Rice University Spatial Studies Lab",
    description: "Detailed 1913 street, ward, and subdivision map produced on the eve of the Houston Ship Channel deep-water opening.",
  },
  {
    id: "comey_1913",
    label: "1913 Arthur Comey Park & Parkway Plan for Houston",
    shortLabel: "1913 Comey Park Plan",
    year: 1913,
    category: "19th & Early 20th Century City Maps",
    isSanborn: false,
    tileUrl: "https://tiles.arcgis.com/tiles/lqRTrQp2HrfnJt8U/arcgis/rest/services/Y1913_Comey_HoustonPlan_2/MapServer/tile/{z}/{y}/{x}",
    minzoom: 11,
    maxzoom: 18,
    minZoom: 11,
    maxZoom: 18,
    attribution: "Arthur Coleman Comey (1913) / Houston Park Commission & Rice GIS",
    description: "Landscape planner Arthur C. Comey's visionary 1913 master plan proposing continuous linear parks along Buffalo, White Oak, and Brays Bayous.",
  },
  {
    id: "hpl_1917",
    label: "1917 Houston Street & Streetcar Directory Map (HPL)",
    shortLabel: "1917 HPL City Map",
    year: 1917,
    category: "Houston Public Library / HMRD Maps (1917-1950)",
    isSanborn: false,
    tileUrl: "https://mycity2.houstontx.gov/pubgis04/rest/services/HPL/HistoricHouston/MapServer/export?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=512,512&format=png32&transparent=true&layers=show:8&f=image",
    minzoom: 11,
    maxzoom: 18,
    minZoom: 11,
    maxZoom: 18,
    attribution: "Houston Public Library / Houston Metropolitan Research Center (1917)",
    description: "World War I-era Houston map showing Camp Logan, newly annexed Houston Heights, and the electric streetcar network.",
  },
  {
    id: "hpl_1921",
    label: "1921 Greater Houston Addition & Subdivision Map (HPL)",
    shortLabel: "1921 HPL City Map",
    year: 1921,
    category: "Houston Public Library / HMRD Maps (1917-1950)",
    isSanborn: false,
    tileUrl: "https://mycity2.houstontx.gov/pubgis04/rest/services/HPL/HistoricHouston/MapServer/export?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=512,512&format=png32&transparent=true&layers=show:7&f=image",
    minzoom: 11,
    maxzoom: 18,
    minZoom: 11,
    maxZoom: 18,
    attribution: "Houston Public Library / HMRC (1921)",
    description: "Early 1920s Houston map illustrating rapid streetcar-suburb growth across Montrose, Eastwood, Magnolia Park, and Woodland Heights.",
  },
  {
    id: "planning_1929",
    label: "1929 Report of the Houston City Planning Commission Map",
    shortLabel: "1929 City Planning Map",
    year: 1929,
    category: "Houston Public Library / HMRD Maps (1917-1950)",
    isSanborn: false,
    tileUrl: "https://tiles.arcgis.com/tiles/lqRTrQp2HrfnJt8U/arcgis/rest/services/1929_SSID35647838/MapServer/tile/{z}/{y}/{x}",
    minzoom: 11,
    maxzoom: 19,
    minZoom: 11,
    maxZoom: 19,
    attribution: "Houston City Planning Commission / Hare & Hare (1929) & Rice GIS",
    description: "Hare & Hare's landmark 1929 major street, civic center, and parkway plan prepared for the City Planning Commission.",
  },
  {
    id: "hpl_1930",
    label: "1930 Houston Census & Street Atlas Map (HPL)",
    shortLabel: "1930 HPL City Map",
    year: 1930,
    category: "Houston Public Library / HMRD Maps (1917-1950)",
    isSanborn: false,
    tileUrl: "https://mycity2.houstontx.gov/pubgis04/rest/services/HPL/HistoricHouston/MapServer/export?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=512,512&format=png32&transparent=true&layers=show:6&f=image",
    minzoom: 11,
    maxzoom: 18,
    minZoom: 11,
    maxZoom: 18,
    attribution: "Houston Public Library / HMRC (1930)",
    description: "1930 Houston street map showing River Oaks, Southampton, Garden Villas, and the pre-freeway rail network.",
  },
  {
    id: "holc_1937",
    label: "1937 HOLC Federal 'Residential Security' Redlining Map",
    shortLabel: "1937 HOLC Redlining Map",
    year: 1937,
    category: "Houston Public Library / HMRD Maps (1917-1950)",
    isSanborn: false,
    tileUrl: "https://tiles.arcgis.com/tiles/lqRTrQp2HrfnJt8U/arcgis/rest/services/HOLC_Houston_Redlining_Map/MapServer/tile/{z}/{y}/{x}",
    minzoom: 10,
    maxzoom: 17,
    minZoom: 10,
    maxZoom: 17,
    attribution: "Home Owners' Loan Corporation (1937) / National Archives & Rice GIS",
    description: "Original 1937 Home Owners' Loan Corporation (HOLC) raster map grading Houston neighborhoods from A ('Best', green) to D ('Hazardous', red).",
  },
  {
    id: "hpl_1941",
    label: "1941 Pre-WWII Houston Street & Industrial Map (HPL)",
    shortLabel: "1941 HPL City Map",
    year: 1941,
    category: "Houston Public Library / HMRD Maps (1917-1950)",
    isSanborn: false,
    tileUrl: "https://mycity2.houstontx.gov/pubgis04/rest/services/HPL/HistoricHouston/MapServer/export?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=512,512&format=png32&transparent=true&layers=show:5&f=image",
    minzoom: 11,
    maxzoom: 18,
    minZoom: 11,
    maxZoom: 18,
    attribution: "Houston Public Library / HMRC (1941)",
    description: "1941 Houston map capturing the city right before World War II petrochemical mobilization and postwar freeway construction.",
  },
  {
    id: "hpl_1950",
    label: "1950 Postwar Boom & Gulf Freeway Map (HPL)",
    shortLabel: "1950 HPL City Map",
    year: 1950,
    category: "Houston Public Library / HMRD Maps (1917-1963)",
    isSanborn: false,
    tileUrl: "https://mycity2.houstontx.gov/pubgis04/rest/services/HPL/HistoricHouston/MapServer/export?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=512,512&format=png32&transparent=true&layers=show:4&f=image",
    minzoom: 11,
    maxzoom: 18,
    minZoom: 11,
    maxZoom: 18,
    attribution: "Houston Public Library / HMRC (1950)",
    description: "1950 Houston street map documenting the opening of the Gulf Freeway (Texas's first freeway) and the 1949 annexation boom.",
  },
  {
    id: "hpl_1963",
    label: "1963 Houston Mid-Century Street & Freeway Loop Map (HPL)",
    shortLabel: "1963 HPL City Map",
    year: 1963,
    category: "Houston Public Library / HMRD Maps (1917-1963)",
    isSanborn: false,
    tileUrl: "https://mycity2.houstontx.gov/pubgis04/rest/services/HPL/HistoricHouston/MapServer/export?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=512,512&format=png32&transparent=true&layers=show:3&f=image",
    minzoom: 11,
    maxzoom: 18,
    minZoom: 11,
    maxZoom: 18,
    attribution: "Houston Public Library / HMRC (1963)",
    description: "1963 Space City-era Houston map showing the emerging Loop 610 corridor, Colt Stadium / Astrodome site, and mid-century subdivisions.",
  },
  {
    id: "aerial_1944",
    label: "1944 Donald Buckner Illustrated Map of Rice Institute & Hermann Park",
    shortLabel: "1944 Rice Campus Map",
    year: 1944,
    category: "Rice University, Hermann Park & South Main Archives",
    isSanborn: false,
    tileUrl: "https://tiles.arcgis.com/tiles/lqRTrQp2HrfnJt8U/arcgis/rest/services/1944_18846774/MapServer/tile/{z}/{y}/{x}",
    minzoom: 12,
    maxzoom: 19,
    minZoom: 12,
    maxZoom: 19,
    bounds: [[-95.4061, 29.7099], [-95.3890, 29.7240]],
    focusCenter: [-95.3982, 29.7172, 15.6],
    attribution: "Donald Buckner / Rice Institute Woodson Research Center & Rice GIS (1944)",
    description: "Georeferenced 1944 student-drawn pictorial map of Rice Institute, Main Street, Autry House, the west-campus 'Cornfield' (pre-Rice Stadium), and Hermann Park ('thrown in to take up space').",
  },
  {
    id: "aerial_rice_1930",
    label: "1930 Aerial Photograph: Rice Institute, Hermann Park & South Main",
    shortLabel: "1930 Rice & South Main Aerial",
    year: 1930,
    category: "Rice University, Hermann Park & South Main Archives",
    isSanborn: false,
    tileUrl: "https://tiles.arcgis.com/tiles/lqRTrQp2HrfnJt8U/arcgis/rest/services/1930_24900750/MapServer/tile/{z}/{y}/{x}",
    minzoom: 12,
    maxzoom: 19,
    minZoom: 12,
    maxZoom: 19,
    bounds: [[-95.4194, 29.7022], [-95.3878, 29.7266]],
    focusCenter: [-95.4018, 29.7165, 15.2],
    attribution: "Woodson Research Center / Rice University GIS (1930)",
    description: "Georeferenced 1930 aerial photograph of Rice Institute, Hermann Park, Boulevard Oaks, and South Main before the Texas Medical Center.",
  },
  {
    id: "aerial_rice_1947",
    label: "1947 Aerial Photograph: Rice Institute, Hermann Park & Shamrock Site",
    shortLabel: "1947 Rice & South Main Aerial",
    year: 1947,
    category: "Rice University, Hermann Park & South Main Archives",
    isSanborn: false,
    tileUrl: "https://tiles.arcgis.com/tiles/lqRTrQp2HrfnJt8U/arcgis/rest/services/1947_18846940/MapServer/tile/{z}/{y}/{x}",
    minzoom: 12,
    maxzoom: 19,
    minZoom: 12,
    maxZoom: 19,
    bounds: [[-95.4162, 29.7049], [-95.3859, 29.7337]],
    focusCenter: [-95.4018, 29.7165, 15.2],
    attribution: "Woodson Research Center / Rice University GIS (1947)",
    description: "Georeferenced 1947 postwar aerial photograph capturing Rice Institute, Hermann Park, and the Main & Holcombe site during construction of the Shamrock Hotel.",
  },
  {
    id: "aerial_1953",
    label: "1953 Aerial Photograph: Rice University, Shamrock Hotel & Medical Center",
    shortLabel: "1953 Rice & South Main Aerial",
    year: 1953,
    category: "Rice University, Hermann Park & South Main Archives",
    isSanborn: false,
    tileUrl: "https://tiles.arcgis.com/tiles/lqRTrQp2HrfnJt8U/arcgis/rest/services/1953_24900752/MapServer/tile/{z}/{y}/{x}",
    minzoom: 12,
    maxzoom: 19,
    minZoom: 12,
    maxZoom: 19,
    bounds: [[-95.4194, 29.7022], [-95.3878, 29.7267]],
    focusCenter: [-95.4018, 29.7145, 15.2],
    attribution: "Woodson Research Center / Rice University GIS (1953)",
    description: "High-resolution 1953 aerial photograph showing the newly completed 1950 Rice Stadium, the Shamrock Hotel & Pool at Main & Holcombe, Hermann Park, and the early Texas Medical Center.",
  },
  {
    id: "usgs_topo",
    label: "USGS Historical Topographic Quad Mosaic (1915-1955)",
    shortLabel: "USGS Historic Topo",
    year: 1922,
    category: "Historic Aerial Photography & Topographic Quads",
    isSanborn: false,
    tileUrl: "https://services.arcgisonline.com/ArcGIS/rest/services/USA_Topo_Maps/MapServer/tile/{z}/{y}/{x}",
    minzoom: 9,
    maxzoom: 16,
    minZoom: 9,
    maxZoom: 16,
    attribution: "U.S. Geological Survey Historical Topographic Map Collection",
    description: "Classic USGS 1:24,000 topographic quadrangle mosaic showing natural bayou meanders, elevation contours, and historic rail grades.",
  },
];

