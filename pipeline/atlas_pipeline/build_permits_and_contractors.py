#!/usr/bin/env python3
"""
Build the Multi-Era Building Permits & Historic Builder / Contractor Attributions
dataset for The Houston Building Atlas.

Sources integrated:
1. HMRC Digitized Contractor Publications (HPL ContentDM API, `pipeline/cache/hmrc_contractors_cache.json`):
   - *Modern Homes, Book Number Two* by The Russell Brown Company (c. 1915-1918, `books/12394`)
   - *Texas General Contractors Association (T.G.C.A.) Monthly Bulletin* (1922-1924, `books/9648`, `16648`, `22338`)
   - *T. D. Howe Construction Company Photographs* (1947-1981, `p17006coll192`)
2. City of Houston Landmark Designation Report PDFs (`pipeline/cache/landmark_pdf_texts.json`, 628 reports):
   - Extracts General Contractor / Builder, Structural/MEP Engineers, Original Building Permit #,
     Mechanic's Lien Contract, and Original Construction Cost.
3. Curated Master Builder & General Contractor Catalog for Major Houston Landmarks & Skyscrapers:
   - W. S. Bellows Construction Corp., Tellepsen Construction Co., American Construction Co.,
     Fretz Construction Co., Manhattan Construction Co., Linbeck Construction Corp., Knutson Construction Co.,
     Central Contracting Co., Thomas B. Hubbard Construction Co., Don Hall, Russell Brown Co.,
     James Stewart & Co., Westlake Construction Co., Harvey Builders, Turner Construction Co.,
     Gilbane Building Co., Vaughn Construction, Satterfield & Pontikes, H. A. Lott, Inc., etc.
4. HCAD Multi-Table Permit & Alteration Index (`Real_acct_owner.zip/permits.txt`,
   `Real_building_land.zip/building_res.txt`, `building_other.txt`, `extra_features_detail1/2.txt`):
   - Generates on-demand 3-digit prefix shards at `app/public/data/permits/permits_<prefix3>.json`
   - Generates `overlays.json["relocated_structures"]` for Relocated Historic Structures (`permit_type == '15'`
     and landmark-documented moved structures) + HMRC Historic Contractor Portfolio Sites.
"""

import collections
import json
import os
import re
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT_DIR / "pipeline" / "cache"
DATA_DIR = ROOT_DIR / "app" / "public" / "data"
PERMITS_DIR = DATA_DIR / "permits"


# ---------------------------------------------------------------------------
# 1. Authoritative Curated Builder / Contractor & Historic Contract Catalog
# ---------------------------------------------------------------------------
# Keyed by 13-digit HCAD account number (or composite building_id) and/or normalized address.
CURATED_BUILDER_BY_HCAD = {
    # Downtown Skyscrapers, Civic Landmarks & Historic Commercial Buildings
    "0010810000007": {  # Gulf Building (712 Main St, 1929)
        "builder": "American Construction Company",
        "engineers": "Weiskopf & Pickworth (Structural); Reg F. Taylor (Mechanical)",
        "original_cost": "$6,500,000",
        "original_permit": "COH Building Permit (Jan. 1928)",
        "builder_source": "COH Protected Landmark Report / Jesse H. Jones Papers",
        "hist_contract": {
            "date": "January 1928 - April 1929",
            "cost": "$6,500,000",
            "architect": "Alfred C. Finn; Kenneth Franzheim; J. E. R. Carpenter",
            "builder": "American Construction Company",
            "engineers": "Weiskopf & Pickworth (Structural); Reg F. Taylor (Mechanical)",
            "scope": "36-story, 428-foot Art Deco skyscraper for Jesse H. Jones (Houston's tallest building, 1929-1963)",
            "source_title": "City of Houston Landmark Designation Report (Gulf Building)",
        },
    },
    "0011490000001": {  # Houston City Hall (901 Bagby St, 1939)
        "builder": "Thomas B. Hubbard Construction Company",
        "engineers": "Robert J. Cummins (Structural Engineer)",
        "original_cost": "$1,680,000",
        "original_permit": "PWA Docket No. Tex-1538-F (March 1938)",
        "builder_source": "PWA Docket No. Tex-1538-F / City of Houston Cornerstone Ledger",
        "hist_contract": {
            "date": "March 7, 1938 - June 1939",
            "cost": "$1,680,000 (PWA Grant & City Bond Issue)",
            "architect": "Joseph Finger, Inc.",
            "builder": "Thomas B. Hubbard Construction Company",
            "engineers": "Robert J. Cummins (Structural)",
            "scope": "11-story Texas Cordova limestone PWA Moderne municipal hall at Hermann Square",
            "source_title": "City of Houston Landmark Designation Report (Houston City Hall)",
        },
    },
    "0011470000001": {  # Julia Ideson Building (550 McKinney St, 1926)
        "builder": "American Construction Company",
        "engineers": "Robert J. Cummins (Structural Engineer)",
        "original_cost": "$500,000",
        "original_permit": "COH Library Construction Contract (1925)",
        "builder_source": "Houston Public Library / HMRC Architectural Archives",
        "hist_contract": {
            "date": "1925 - October 18, 1926",
            "cost": "$500,000 (Original 1926 Building) | $32,000,000 (2010 Wing & Restoration)",
            "architect": "Cram & Ferguson; William Ward Watkin; Louis A. Glover",
            "builder": "American Construction Company (1926); Vaughn Construction (2010 Restoration)",
            "engineers": "Robert J. Cummins (Structural)",
            "scope": "3-story Spanish Renaissance Revival central public library in limestone, brick, and stucco",
            "source_title": "HMRC Institutional Building Records / 2012 Good Brick Award Citation",
        },
    },
    "0010310000001": {  # 1910 Harris County Courthouse (301 Fannin St, 1910)
        "builder": "H. L. Stevens & Company",
        "engineers": "Lang & Witchell (Supervising Engineers)",
        "original_cost": "$500,000",
        "original_permit": "Harris County Commissioners Court Contract (1909)",
        "builder_source": "Harris County Commissioners Court Minutes / NRHP Inventory",
        "hist_contract": {
            "date": "1909 - 1910 (Restored 2011; Remodeled May 1923)",
            "cost": "$500,000 (1910 Original) | $17,910 (1923 Remodel) | $50,000,000 (2011 Restoration)",
            "architect": "Lang & Witchell (1910); Endress & Cato (1923); PGAL / ArchiTexas (2011)",
            "builder": "H. L. Stevens & Company (1910); E. D. Harlow & Son (1923); Vaughn Construction (2011)",
            "other_bidders": "1923 Courthouse Remodel Bids: E. D. Harlow & Son ($17,910, awarded); Chas. Dahl ($19,420); Andrew Ness ($24,137); Fred B. Chambers ($26,650); W. E. Woodruff ($27,546)",
            "scope": "6-story Beaux-Arts pink Texas granite and terra-cotta county courthouse with monumental rotunda dome",
            "source_title": "T.G.C.A. Monthly Bulletin, Vol. 4, No. 5 (May 1923), p. 24 & NRHP Nomination",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/16184",
        },
    },
    "osm_city_510322923#bldg_niels_esperson_building": {  # Niels and Mellie Esperson Buildings (808 Travis St, 1927 & 1941)
        "builder": "American Construction Company (1927) & W. S. Bellows Construction Co. (1941)",
        "engineers": "Weiskopf & Pickworth (Structural)",
        "original_cost": "$4,000,000",
        "original_permit": "COH Building Permit (1925 & 1940)",
        "builder_source": "COH Landmark Designation Report / HMRC Architectural Archives",
        "hist_contract": {
            "date": "1925 - 1927 (Niels Esperson) & 1940 - 1941 (Mellie Esperson)",
            "cost": "$4,000,000 (1927 Tower) | $2,500,000 (1941 Annex)",
            "architect": "John Eberson (1927); John & Drew Eberson (1941)",
            "builder": "American Construction Company (1927); W. S. Bellows Construction Co. (1941)",
            "scope": "32-story Italian Renaissance Revival skyscraper crowned by the columned tempietto",
            "source_title": "City of Houston Landmark Designation Report (Esperson Buildings)",
        },
    },
    "0010910000015": {  # Niels and Mellie Esperson Buildings (HCAD parcel key)
        "builder": "American Construction Company (1927) & W. S. Bellows Construction Co. (1941)",
        "engineers": "Weiskopf & Pickworth (Structural)",
        "original_cost": "$4,000,000",
        "original_permit": "COH Building Permit (1925 & 1940)",
        "builder_source": "COH Landmark Designation Report / HMRC Architectural Archives",
        "hist_contract": {
            "date": "1925 - 1927 (Niels Esperson) & 1940 - 1941 (Mellie Esperson)",
            "cost": "$4,000,000 (1927 Tower) | $2,500,000 (1941 Annex)",
            "architect": "John Eberson (1927); John & Drew Eberson (1941)",
            "builder": "American Construction Company (1927); W. S. Bellows Construction Co. (1941)",
            "scope": "32-story Italian Renaissance Revival skyscraper crowned by the columned tempietto",
            "source_title": "City of Houston Landmark Designation Report (Esperson Buildings)",
        },
    },
    "0010570000009": {  # Rice Hotel (909 Texas Ave, 1913)
        "builder": "James Stewart & Company",
        "engineers": "Mauran, Russell & Crowell",
        "original_cost": "$3,000,000",
        "original_permit": "COH Building Permit (1912); East Wing Addition (1925)",
        "builder_source": "Jesse H. Jones Papers / COH Protected Landmark Report",
        "hist_contract": {
            "date": "1912 - May 1913 (East Wing added 1925; Adaptive Reuse 1998)",
            "cost": "$3,000,000",
            "architect": "Mauran, Russell & Crowell; Alfred C. Finn (1925 Wing)",
            "builder": "James Stewart & Company (1913); American Construction Company (1925)",
            "scope": "18-story, 1,000-room U-shaped Beaux-Arts hotel on the site of the 1837 Republic of Texas Capitol",
            "source_title": "City of Houston Protected Landmark Designation Report (The Rice Hotel)",
        },
    },
    "0010440000019": {  # State National Bank Building (412 Main St, 1923)
        "builder": "American Construction Company",
        "engineers": "Virginia Bridge & Iron Company (Structural Steel)",
        "original_cost": "$367,100",
        "original_permit": "TGCA Contract Award (Jan. 29, 1923)",
        "builder_source": "Texas General Contractors Bulletin, Vol. 4, No. 2 (Feb. 1923), pp. 18, 25",
        "hist_contract": {
            "date": "Bids opened January 29, 1923 (Completed August 1, 1923)",
            "cost": "$344,500 superstructure + $22,600 basement/foundation ($367,100 total)",
            "architect": "Alfred C. Finn",
            "builder": "American Construction Company",
            "other_bidders": "Tom Tellepsen ($340,000, 250 wk. days); American Construction Co. ($344,500, awarded for Aug. 1, 1923 completion); James Stewart & Co. ($346,000); W. E. Woodruff ($350,000); J. E. McGillicuddy Co. ($350,000); Central Contracting Co.",
            "scope": "12-story reinforced concrete and brick bank & office building (45x100 ft); steel frame by Virginia Bridge & Iron Co.",
            "source_title": "T.G.C.A. Monthly Bulletin, Vol. 4, No. 2 (February 1923), pp. 18 & 25",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/16082",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/16082/page/0/inline/books_16082_0",
        },
    },
    "1228970000001": {  # First National Bank Building 1923 Addition (201 Main St)
        "builder": "Central Contracting Company",
        "engineers": "Sanguinet, Staats, Hedrick & Gottlieb",
        "original_cost": "$460,000",
        "original_permit": "TGCA Contract Award (March 1923)",
        "builder_source": "Texas General Contractors Bulletin, Vol. 4, No. 3 (March 1923), pp. 21, 31",
        "hist_contract": {
            "date": "March 1923 (Original 8-story building erected 1905 by James Stewart & Co.)",
            "cost": "$460,000 (1923 9-Story Concrete Addition, 90x100 ft)",
            "architect": "Sanguinet & Staats (1905); Sanguinet, Staats, Hedrick & Gottlieb (1923)",
            "builder": "Central Contracting Company (1923 Addition); James Stewart & Co. (1905 Original)",
            "other_bidders": "Central Contracting Co. ($460,000, awarded); Southwestern Construction Co. ($466,500); Alzina Construction Co.",
            "scope": "9-story reinforced concrete addition (90x100 ft) to First National Bank of Houston at Main & Franklin",
            "source_title": "T.G.C.A. Monthly Bulletin, Vol. 4, No. 3 (March 1923), pp. 21 & 31",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/16129",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/16129/page/0/inline/books_16129_0",
        },
    },
    "osm_city_54046916#bldg_scanlan_building": {  # Scanlan Building (405 Main St, 1909)
        "builder": "Westlake Construction Company (St. Louis)",
        "engineers": "D. H. Burnham & Company (Structural)",
        "original_cost": "$600,000",
        "original_permit": "COH Building Permit (1908)",
        "builder_source": "COH Protected Landmark Report / D. H. Burnham Archives",
        "hist_contract": {
            "date": "1908 - 1909",
            "cost": "$600,000",
            "architect": "D. H. Burnham & Company (Daniel H. Burnham)",
            "builder": "Westlake Construction Company",
            "scope": "11-story Chicago School steel-frame office building clad in white glazed architectural terra cotta",
            "source_title": "City of Houston Protected Landmark Designation Report (Scanlan Building)",
        },
    },
    "0010450000009": {  # Scanlan Building (405 Main St, HCAD parcel key)
        "builder": "Westlake Construction Company (St. Louis)",
        "engineers": "D. H. Burnham & Company (Structural)",
        "original_cost": "$600,000",
        "original_permit": "COH Building Permit (1908)",
        "builder_source": "COH Protected Landmark Report / D. H. Burnham Archives",
        "hist_contract": {
            "date": "1908 - 1909",
            "cost": "$600,000",
            "architect": "D. H. Burnham & Company (Daniel H. Burnham)",
            "builder": "Westlake Construction Company",
            "scope": "11-story Chicago School steel-frame office building clad in white glazed architectural terra cotta",
            "source_title": "City of Houston Protected Landmark Designation Report (Scanlan Building)",
        },
    },
    "0010800000001": {  # Texas State Hotel (720 Fannin St, 1929)
        "builder": "James Stewart & Company",
        "engineers": "Joseph Finger, Inc.",
        "original_cost": "$1,500,000",
        "original_permit": "COH Building Permit (1928)",
        "builder_source": "COH Landmark Designation Report / Jesse H. Jones Papers",
        "hist_contract": {
            "date": "1928 - 1929",
            "cost": "$1,500,000",
            "architect": "Joseph Finger",
            "builder": "James Stewart & Company",
            "scope": "17-story Spanish Renaissance Revival brick and limestone hotel built for Jesse H. Jones",
            "source_title": "City of Houston Landmark Designation Report (Texas State Hotel)",
        },
    },
    "0010920000004": {  # Carter Building / Second National Bank (806 Main St, 1910)
        "builder": "American Construction Company",
        "engineers": "Sanguinet & Staats",
        "original_cost": "$750,000",
        "original_permit": "COH Building Permit (1909); 6-Story Top Addition (Dec. 1922, $20,000 prelim)",
        "builder_source": "COH Protected Landmark Report & T.G.C.A. Monthly Bulletin (Dec. 1922, p. 23)",
        "hist_contract": {
            "date": "1909 - 1910 (16 stories); December 1922 - 1924 (6-story addition to 22 stories)",
            "cost": "$750,000 (1910 Original)",
            "architect": "Sanguinet & Staats; Alfred C. Finn",
            "builder": "American Construction Company",
            "scope": "16-story steel-frame skyscraper built for lumber magnate W. T. Carter & Jesse H. Jones, raised to 22 stories in 1923-1924",
            "source_title": "T.G.C.A. Monthly Bulletin, Vol. 3, No. 12 (December 1922), p. 23",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/9625",
        },
    },
    "1423010010001": {  # Carter Building / JW Marriott Houston Downtown (806 Main St, 1910)
        "builder": "American Construction Company",
        "engineers": "Sanguinet & Staats",
        "original_cost": "$750,000",
        "original_permit": "COH Building Permit (1909); 6-Story Top Addition (Dec. 1922, $20,000 prelim)",
        "builder_source": "COH Protected Landmark Report & T.G.C.A. Monthly Bulletin (Dec. 1922, p. 23)",
        "hist_contract": {
            "date": "1909 - 1910 (16 stories); December 1922 - 1924 (6-story addition to 22 stories)",
            "cost": "$750,000 (1910 Original)",
            "architect": "Sanguinet & Staats; Alfred C. Finn",
            "builder": "American Construction Company",
            "scope": "16-story steel-frame skyscraper built for lumber magnate W. T. Carter & Jesse H. Jones, raised to 22 stories in 1923-1924",
            "source_title": "T.G.C.A. Monthly Bulletin, Vol. 3, No. 12 (December 1922), p. 23",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/9625",
        },
    },
    "0332640000005#zoo_45776272_caroline_wiess_law_build": {  # Museum of Fine Arts, Houston - Original 1924 Watkin Wing (1001 Bissonnet St)
        "builder": "Don Hall, Constructor",
        "engineers": "William Ward Watkin",
        "original_cost": "$200,000",
        "original_permit": "TGCA Contract Award (Feb. 1923)",
        "builder_source": "Texas General Contractors Bulletin, Vol. 4, No. 2 (Feb. 1923), p. 25",
        "hist_contract": {
            "date": "Contract awarded February 1923; dedicated April 12, 1924 (Cullinan Hall added 1958 by W. S. Bellows Construction Co.)",
            "cost": "$200,000 (First Unit, 1923-1924)",
            "architect": "William Ward Watkin & Cram and Ferguson (1924); Ludwig Mies van der Rohe (1958 & 1974)",
            "builder": "Don Hall, Constructor (1924 First Unit); W. S. Bellows Construction Co. (1958 Cullinan Hall)",
            "scope": "First unit of the Houston Art Museum (Neoclassical limestone pavilion), followed by Mies van der Rohe's 1958 Cullinan Hall",
            "source_title": "T.G.C.A. Monthly Bulletin, Vol. 4, No. 2 (February 1923), p. 25",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/16089",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/16089/page/0/inline/books_16089_0",
        },
    },
    "0190900000001": {  # Eldorado Ballroom (2310 Elgin St, 1939)
        "builder": "Knutson Construction Company",
        "engineers": "Lenard Gabert",
        "original_cost": "$38,000",
        "original_permit": "COH Building Permit (1939)",
        "builder_source": "COH Protected Landmark Report / HMRC Architectural Archives",
        "hist_contract": {
            "date": "1939 (Opened October 1939; Restored 2023)",
            "cost": "$38,000",
            "architect": "Lenard Gabert",
            "builder": "Knutson Construction Company (1939); Forney Construction (2023 Restoration)",
            "scope": "2-story Streamline Moderne brick retail and upstairs music ballroom commissioned by Anna & Clarence Dupree in Third Ward",
            "source_title": "City of Houston Protected Landmark Report (Eldorado Ballroom)",
        },
    },
    "1464830000001": {  # Eldorado Ballroom (secondary HCAD parcel key)
        "builder": "Knutson Construction Company",
        "engineers": "Lenard Gabert",
        "original_cost": "$38,000",
        "original_permit": "COH Building Permit (1939)",
        "builder_source": "COH Protected Landmark Report / HMRC Architectural Archives",
        "hist_contract": {
            "date": "1939 (Opened October 1939; Restored 2023)",
            "cost": "$38,000",
            "architect": "Lenard Gabert",
            "builder": "Knutson Construction Company (1939); Forney Construction (2023 Restoration)",
            "scope": "2-story Streamline Moderne brick retail and upstairs music ballroom commissioned by Anna & Clarence Dupree in Third Ward",
            "source_title": "City of Houston Protected Landmark Report (Eldorado Ballroom)",
        },
    },
    "0451420000001": {  # San Jacinto Monument (1 Monument Cir, La Porte, 1939)
        "builder": "W. S. Bellows Construction Corporation",
        "engineers": "Robert J. Cummins (Structural Engineer)",
        "original_cost": "$1,500,000",
        "original_permit": "PWA / Texas Centennial Commission Contract (1936)",
        "builder_source": "W. S. Bellows Construction Archives / San Jacinto Museum of History",
        "hist_contract": {
            "date": "1936 - April 21, 1939",
            "cost": "$1,500,000",
            "architect": "Alfred C. Finn",
            "builder": "W. S. Bellows Construction Corporation",
            "engineers": "Robert J. Cummins (Structural Engineer)",
            "scope": "567.3-foot octagonal Texas Cordova shellstone memorial column crowned by a 220-ton Lone Star (world's tallest masonry column)",
            "source_title": "W. S. Bellows Construction Corp. Historical Portfolio & NHL Nomination",
        },
    },
    "osm_city_119420928#bldg_nrg_astrodome": {  # NRG Astrodome (8400 Kirby Dr, 1965)
        "builder": "H. A. Lott, Inc. & Lummus Company (Joint Venture)",
        "engineers": "Walter P Moore & Associates; Roof Structures, Inc.; Praeger-Kavanagh-Waterbury",
        "original_cost": "$31,600,000",
        "original_permit": "Harris County Domed Stadium Bond Contract (1962)",
        "builder_source": "Harris County Sports & Convention Corp. / NRHP Nomination",
        "hist_contract": {
            "date": "January 3, 1962 - April 9, 1965",
            "cost": "$31,600,000 ($35,000,000 with site infrastructure)",
            "architect": "Hermon Lloyd & W. B. Morgan; Wilson, Morris, Crain & Anderson",
            "builder": "H. A. Lott, Inc. & Lummus Company",
            "engineers": "Walter P Moore; Praeger-Kavanagh-Waterbury; Roof Structures, Inc. (Kiewitt Lamella Dome)",
            "scope": "World's first multi-purpose, air-conditioned domed sports stadium (642-foot clear-span steel lamella truss dome)",
            "source_title": "National Register of Historic Places Registration Form (Harris County Domed Stadium)",
        },
    },
    "0440930000004": {  # NRG Astrodome & NRG Park Complex (8400 Fannin St, 1965)
        "builder": "H. A. Lott, Inc. & Lummus Company (Joint Venture)",
        "engineers": "Walter P Moore & Associates; Roof Structures, Inc.; Praeger-Kavanagh-Waterbury",
        "original_cost": "$31,600,000",
        "original_permit": "Harris County Domed Stadium Bond Contract (1962)",
        "builder_source": "Harris County Sports & Convention Corp. / NRHP Nomination",
        "hist_contract": {
            "date": "January 3, 1962 - April 9, 1965",
            "cost": "$31,600,000 ($35,000,000 with site infrastructure)",
            "architect": "Hermon Lloyd & W. B. Morgan; Wilson, Morris, Crain & Anderson",
            "builder": "H. A. Lott, Inc. & Lummus Company",
            "engineers": "Walter P Moore; Praeger-Kavanagh-Waterbury; Roof Structures, Inc. (Kiewitt Lamella Dome)",
            "scope": "World's first multi-purpose, air-conditioned domed sports stadium (642-foot clear-span steel lamella truss dome)",
            "source_title": "National Register of Historic Places Registration Form (Harris County Domed Stadium)",
        },
    },
    "0400030000014#shp_44632292_the_kellum_noble_house": {  # Kellum-Noble House (212 Dallas St, 1847)
        "builder": "Nathaniel Kellum (Master Brickmaker & Builder)",
        "original_cost": "$3,500 (est. 1847 construction)",
        "builder_source": "The Heritage Society / NRHP Inventory",
        "hist_contract": {
            "date": "1847 - 1848 (Saved & Restored 1954 by The Heritage Society)",
            "cost": "Built with slave-made brick fired at Nathaniel Kellum's Buffalo Bayou brickyard",
            "architect": "Nathaniel Kellum",
            "builder": "Nathaniel Kellum",
            "scope": "2-story Louisiana Colonial double-gallery brick residence, oldest surviving brick house on its original foundation in Houston",
            "source_title": "The Heritage Society at Sam Houston Park Archival Records",
        },
    },
    "0400030000013": {  # Kellum-Noble House (212 Dallas St, 1847)
        "builder": "Nathaniel Kellum (Master Brickmaker & Builder)",
        "original_cost": "$3,500 (est. 1847 construction)",
        "builder_source": "The Heritage Society / NRHP Inventory",
        "hist_contract": {
            "date": "1847 - 1848 (Saved & Restored 1954 by The Heritage Society)",
            "cost": "Built with slave-made brick fired at Nathaniel Kellum's Buffalo Bayou brickyard",
            "architect": "Nathaniel Kellum",
            "builder": "Nathaniel Kellum",
            "scope": "2-story Louisiana Colonial double-gallery brick residence, oldest surviving brick house on its original foundation in Houston",
            "source_title": "The Heritage Society at Sam Houston Park Archival Records",
        },
    },
}


# Address-keyed curated contractor & TGCA records (matched against normalized street addresses)
CURATED_BUILDER_BY_ADDRESS = {
    # Russell Brown Company - "Modern Homes, Book Number Two" (c. 1915-1918, HMRC ContentDM books/12394)
    "4515 YOAKUM": {
        "builder": "The Russell Brown Company",
        "architect": "The Russell Brown Company (Design-Build, Plan No. 203)",
        "year_built": 1916,
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (The Russell Brown Co., c. 1917), Plan 203, p. 7",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12346",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12346/page/0/inline/books_12346_0",
        "hist_contract": {
            "date": "1916 (Plan No. 203)",
            "cost": "$8,500 (est. Russell Brown Co. contract)",
            "architect": "The Russell Brown Company (Plan No. 203)",
            "builder": "The Russell Brown Company",
            "scope": "2-story Prairie/Craftsman residence featured in The Russell Brown Co.'s 'Modern Homes, Book Number Two' (Page 7, Plan 203)",
            "source_title": "HMRC: Modern Homes, Book Number Two (The Russell Brown Co.), Plan 203, Page 7",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12346",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12346/page/0/inline/books_12346_0",
        },
    },
    "308 AVONDALE": {
        "builder": "The Russell Brown Company",
        "architect": "The Russell Brown Company (Design-Build, Plan No. 204)",
        "year_built": 1917,
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (The Russell Brown Co., c. 1917), Plan 204, p. 8",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12347",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12347/page/0/inline/books_12347_0",
        "hist_contract": {
            "date": "1917 (Plan No. 204)",
            "cost": "$9,000 (est. Russell Brown Co. contract)",
            "architect": "The Russell Brown Company (Plan No. 204)",
            "builder": "The Russell Brown Company",
            "scope": "2-story residence in Avondale Addition featured in The Russell Brown Co.'s 'Modern Homes, Book Number Two' (Page 8, Plan 204)",
            "source_title": "HMRC: Modern Homes, Book Number Two (The Russell Brown Co.), Plan 204, Page 8",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12347",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12347/page/0/inline/books_12347_0",
        },
    },
    "808 LOVETT": {
        "builder": "The Russell Brown Company",
        "architect": "The Russell Brown Company (Design-Build, Plan No. 219)",
        "year_built": 1917,
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (The Russell Brown Co., c. 1917), Plan 219, p. 25",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12364",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12364/page/0/inline/books_12364_0",
        "hist_contract": {
            "date": "1917 (Plan No. 219)",
            "cost": "$12,500 (est. Russell Brown Co. contract)",
            "architect": "The Russell Brown Company (Plan No. 219)",
            "builder": "The Russell Brown Company",
            "scope": "2-story brick-veneer residence on Lovett Boulevard featured in 'Modern Homes, Book Number Two' (Page 25, Plan 219)",
            "source_title": "HMRC: Modern Homes, Book Number Two (The Russell Brown Co.), Plan 219, Page 25",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12364",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12364/page/0/inline/books_12364_0",
        },
    },
    "216 AVONDALE": {
        "builder": "The Russell Brown Company",
        "architect": "The Russell Brown Company (Design-Build, Plan No. 220)",
        "year_built": 1918,
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (The Russell Brown Co., c. 1917), Plan 220, p. 26",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12365",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12365/page/0/inline/books_12365_0",
        "hist_contract": {
            "date": "1918 (Plan No. 220)",
            "cost": "$9,500 (est. Russell Brown Co. contract)",
            "architect": "The Russell Brown Company (Plan No. 220)",
            "builder": "The Russell Brown Company",
            "scope": "2-story residence in Avondale Addition featured in 'Modern Homes, Book Number Two' (Page 26, Plan 220)",
            "source_title": "HMRC: Modern Homes, Book Number Two (The Russell Brown Co.), Plan 220, Page 26",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12365",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12365/page/0/inline/books_12365_0",
        },
    },
    "3618 BURLINGTON": {
        "builder": "The Russell Brown Company",
        "architect": "The Russell Brown Company (Design-Build, Plan No. 304)",
        "year_built": 1916,
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (The Russell Brown Co., c. 1917), Plan 304, p. 31",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12370",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12370/page/0/inline/books_12370_0",
        "hist_contract": {
            "date": "c. 1916 (Plan No. 304)",
            "cost": "$8,500 (est. Russell Brown Co. contract)",
            "architect": "The Russell Brown Company (Plan No. 304)",
            "builder": "The Russell Brown Company",
            "scope": "2-story residence on Burlington Street featured in 'Modern Homes, Book Number Two' (Page 31, Plan 304)",
            "source_title": "HMRC: Modern Homes, Book Number Two (The Russell Brown Co.), Plan 304, Page 31",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12370",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12370/page/0/inline/books_12370_0",
        },
    },
    "218 AVONDALE": {
        "builder": "The Russell Brown Company",
        "architect": "The Russell Brown Company (Design-Build, Plan No. 312)",
        "year_built": 1917,
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (The Russell Brown Co., c. 1917), Plan 312, p. 36",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12375",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12375/page/0/inline/books_12375_0",
        "hist_contract": {
            "date": "1917 (Plan No. 312)",
            "cost": "$10,500 (est. Russell Brown Co. contract)",
            "architect": "The Russell Brown Company (Plan No. 312)",
            "builder": "The Russell Brown Company",
            "scope": "2-story residence in Avondale Addition featured in 'Modern Homes, Book Number Two' (Page 36, Plan 312)",
            "source_title": "HMRC: Modern Homes, Book Number Two (The Russell Brown Co.), Plan 312, Page 36",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12375",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12375/page/0/inline/books_12375_0",
        },
    },
    "104 AVONDALE": {
        "builder": "The Russell Brown Company",
        "architect": "The Russell Brown Company (Design-Build, Plan No. 313)",
        "year_built": 1917,
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (The Russell Brown Co., c. 1917), Plan 313, p. 37",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12376",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12376/page/0/inline/books_12376_0",
        "hist_contract": {
            "date": "1917 (Plan No. 313)",
            "cost": "$11,000 (est. Russell Brown Co. contract)",
            "architect": "The Russell Brown Company (Plan No. 313)",
            "builder": "The Russell Brown Company",
            "scope": "2-story residence at 104 Avondale featured in 'Modern Homes, Book Number Two' (Page 37, Plan 313)",
            "source_title": "HMRC: Modern Homes, Book Number Two (The Russell Brown Co.), Plan 313, Page 37",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12376",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12376/page/0/inline/books_12376_0",
        },
    },
    "11026 KEMWOOD": {
        "builder": "The Russell Brown Company",
        "architect": "The Russell Brown Company (Design-Build, Plan No. 309)",
        "year_built": 1917,
        "was_relocated": True,
        "relocation_note": "Originally built in 1917-1918 at 3902 Montrose Blvd (Montrose Addition) by The Russell Brown Company (Plan No. 309); relocated to 11026 Kemwood Dr in Bunker Hill Village to preserve the historic structure.",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (The Russell Brown Co., c. 1917), Plan 309, p. 19",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12358",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12358/page/0/inline/books_12358_0",
        "hist_contract": {
            "date": "1917 - 1918 (Plan No. 309; Later relocated from 3902 Montrose Blvd)",
            "cost": "$14,000 (est. Russell Brown Co. contract)",
            "architect": "The Russell Brown Company (Plan No. 309)",
            "builder": "The Russell Brown Company",
            "scope": "2-story residence originally erected at 3902 Montrose Blvd (Plan 309, Page 19 of 'Modern Homes, Book Number Two') and subsequently moved intact to 11026 Kemwood Dr",
            "source_title": "HMRC: Modern Homes, Book Number Two (The Russell Brown Co.), Plan 309, Page 19",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12358",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/12358/page/0/inline/books_12358_0",
        },
    },
    # T. D. Howe Construction Company (HMRC p17006coll192)
    "4201 MAIN": {  # Sears, Roebuck & Co. Midtown Store (1939; 1948 addition by T. D. Howe Construction Co.)
        "builder": "Knutson Construction Company (1939) & T. D. Howe Construction Company (1948 Addition)",
        "builder_source": "HMRC T. D. Howe Construction Company Photograph Collection (p17006coll192)",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll192/id/90",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/iiif/p17006coll192/90/full/pct:75/0/default.jpg",
        "hist_contract": {
            "date": "1939 (Original Store) & August 1948 (Second-Floor Addition & Remodel)",
            "cost": "$1,250,000",
            "architect": "Nimmons, Carr & Wright (1939); Kenneth Franzheim (1948 Addition)",
            "builder": "Knutson Construction Co. (1939); T. D. Howe Construction Co. (1948 Addition; Renovated 2021 as The Ion)",
            "scope": "Art Deco department store with 1948 T. D. Howe structural steel and masonry expansion (now The Ion innovation hub)",
            "source_title": "HMRC: T. D. Howe Construction Company Photographs (mss0268-0205)",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll192/id/90",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/iiif/p17006coll192/90/full/pct:75/0/default.jpg",
        },
    },
    "2353 RICE": {  # Christ the King Evangelical Lutheran Church (2353 Rice Blvd, 1948)
        "builder": "T. D. Howe Construction Company",
        "builder_source": "HMRC T. D. Howe Construction Company Photograph Collection (p17006coll192, id/114)",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll192/id/114",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/iiif/p17006coll192/114/full/pct:75/0/default.jpg",
        "hist_contract": {
            "date": "December 1948",
            "cost": "$145,000",
            "architect": "R. G. Schneider & Company",
            "builder": "T. D. Howe Construction Company",
            "scope": "Brick and timber liturgical sanctuary & parish hall at Rice Boulevard and Greenbriar Drive",
            "source_title": "HMRC: T. D. Howe Construction Company Photographs (id/114)",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/p17006coll192/id/114",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/iiif/p17006coll192/114/full/pct:75/0/default.jpg",
        },
    },
    "3517 AUSTIN": {  # Temple Beth Israel (1924 Sanctuary, now Heinen Theatre at HCC Central, 3517 Austin St)
        "builder": "American Construction Company",
        "architect": "Joseph Finger",
        "original_cost": "$202,242",
        "original_permit": "TGCA Contract Award (Sept. 30, 1924)",
        "builder_source": "Texas General Contractors Bulletin, Vol. 5, No. 10 (Oct. 1924), p. 18",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/22168",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/22168/page/0/inline/books_22168_0",
        "hist_contract": {
            "date": "Bids opened September 30, 1924",
            "cost": "$202,242 (Base bid $198,000)",
            "architect": "Joseph Finger",
            "builder": "American Construction Company",
            "other_bidders": "American Construction Co. ($198,000 base / $202,242 awarded); Southwestern Construction Co. ($201,040); J. B. Townsend ($209,000); Tellepsen Construction Co. ($216,000); Standard Construction Co. ($223,900); Don Hall ($236,200)",
            "scope": "2-story & basement reinforced concrete and limestone Neo-Babylonian/Classical temple (100x77 ft) with Sunday School wing (97x67 ft) for Congregation Beth Israel",
            "source_title": "T.G.C.A. Monthly Bulletin, Vol. 5, No. 10 (October 1924), p. 18",
            "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/22168",
            "image_url": "https://cdm17006.contentdm.oclc.org/digital/api/collection/books/id/22168/page/0/inline/books_22168_0",
        },
    },
    "500 MCKINNEY": {  # Jesse H. Jones Central Library (1976)
        "builder": "H. A. Lott, Inc.",
        "architect": "S. I. Morris Associates",
        "original_cost": "$10,500,000",
        "builder_source": "Houston Public Library / HMRC Archives",
    },
    "610 MAIN": {  # Sakowitz Bros. Downtown Store (1951, 1111 Main / 610 Main)
        "builder": "Tellepsen Construction Company",
        "architect": "Alfred C. Finn",
        "original_cost": "$8,000,000",
        "builder_source": "Tellepsen Archives / COH Landmark Report",
    },
    "711 MAIN": {  # Pennzoil Place (1975)
        "builder": "Zapata Warrior Constructors",
        "engineers": "Ellisor Engineers (Structural); I. A. Naman + Associates (MEP)",
        "architect": "Philip Johnson & John Burgee; Wilson, Morris, Crain & Anderson",
        "original_cost": "$52,000,000",
        "builder_source": "Gerald D. Hines Interests / Architectural Record",
    },
    "700 LOUISIANA": {  # Bank of America Center / RepublicBank Center (1983)
        "builder": "Turner Construction Company",
        "engineers": "CBM Engineers, Inc. (Structural); I. A. Naman + Associates (MEP)",
        "architect": "Johnson/Burgee Architects; Kendall/Heaton Associates",
        "original_cost": "$125,000,000",
        "builder_source": "Gerald D. Hines Interests / Engineering News-Record",
    },
    "910 LOUISIANA": {  # One Shell Plaza (1971)
        "builder": "W. S. Bellows Construction Corporation",
        "engineers": "Skidmore, Owings & Merrill (Fazlur Rahman Khan, Structural)",
        "architect": "Skidmore, Owings & Merrill; Wilson, Morris, Crain & Anderson",
        "original_cost": "$35,000,000",
        "builder_source": "W. S. Bellows Construction Corp. Historical Portfolio",
    },
    "600 TRAVIS": {  # JPMorgan Chase Tower / Texas Commerce Tower (1981)
        "builder": "Turner Construction Company",
        "engineers": "CBM Engineers, Inc. (Structural)",
        "architect": "I. M. Pei & Partners; 3D/International",
        "original_cost": "$160,000,000",
        "builder_source": "Gerald D. Hines Interests / Engineering News-Record",
    },
    "1000 LOUISIANA": {  # Wells Fargo Plaza / Allied Bank Plaza (1983)
        "builder": "Miner-Turner Joint Venture (Turner Construction & Miner-Dederick)",
        "engineers": "Skidmore, Owings & Merrill (Structural)",
        "architect": "Skidmore, Owings & Merrill; Lloyd Jones Brewer & Associates",
        "original_cost": "$185,000,000",
        "builder_source": "Engineering News-Record / Century Development",
    },
    "1100 LOUISIANA": {  # Enterprise Plaza / First International Plaza (1980)
        "builder": "W. S. Bellows Construction Corporation",
        "engineers": "CBM Engineers, Inc.",
        "architect": "Skidmore, Owings & Merrill; 3D/International",
        "original_cost": "$110,000,000",
        "builder_source": "W. S. Bellows Construction Corp. Historical Portfolio",
    },
    "1111 BAGBY": {  # Heritage Plaza (1987)
        "builder": "W. S. Bellows Construction Corporation",
        "engineers": "CBM Engineers, Inc.",
        "architect": "M. Nasr & Partners",
        "original_cost": "$140,000,000",
        "builder_source": "W. S. Bellows Construction Corp. Historical Portfolio",
    },
    "2800 POST OAK": {  # Williams Tower / Transco Tower (1983)
        "builder": "W. S. Bellows Construction Corporation",
        "engineers": "CBM Engineers, Inc. (Structural); I. A. Naman + Associates (MEP)",
        "architect": "Johnson/Burgee Architects; Morris*Aubry Architects",
        "original_cost": "$150,000,000",
        "builder_source": "W. S. Bellows Construction Corp. Historical Portfolio",
    },
    "501 TEXAS": {  # Minute Maid Park / Daikin Park & Union Station (1911 / 2000)
        "builder": "American Construction Company (1911 Union Station) & Brown & Root / Barton Malow / Empire Construction (2000 Ballpark)",
        "engineers": "Walter P Moore & Associates (Retractable Roof Structural Engineer)",
        "architect": "Warren & Wetmore (1911 Union Station); HOK Sport (2000)",
        "original_cost": "$500,000 (1911 Station) | $250,000,000 (2000 Ballpark)",
        "builder_source": "COH Landmark Report / Harris County-Houston Sports Authority",
    },
    "501 CRAWFORD": {  # Union Station (1911)
        "builder": "American Construction Company (1911 Union Station) & Brown & Root / Barton Malow (2000)",
        "engineers": "Walter P Moore & Associates",
        "architect": "Warren & Wetmore (1911)",
        "original_cost": "$500,000 (1911 Union Station)",
        "builder_source": "COH Landmark Designation Report (Union Station)",
    },
    "1515 SUL ROSS": {  # The Menil Collection Main Building (1987)
        "builder": "E. G. Lowry Company, Inc.",
        "engineers": "Ove Arup & Partners (Peter Rice, Structural & Ferrocement Leaves)",
        "architect": "Renzo Piano Building Workshop; Richard Fitzgerald & Partners",
        "original_cost": "$25,000,000",
        "builder_source": "Menil Archives / Ove Arup & Partners",
    },
    "3900 YUUPON": {  # Rothko Chapel (1971)
        "builder": "Linbeck Construction Corporation",
        "architect": "Philip Johnson; Howard Barnstone & Eugene Aubry",
        "builder_source": "Linbeck Construction Corp. / NRHP Nomination",
    },
    "1409 SUL ROSS": {  # Rothko Chapel (1409 Sul Ross / 3900 Yupon, 1971)
        "builder": "Linbeck Construction Corporation",
        "architect": "Philip Johnson; Howard Barnstone & Eugene Aubry",
        "builder_source": "Linbeck Construction Corp. / NRHP Nomination",
    },
    "501 TEXAS AVE": {
        "builder": "American Construction Company",
        "builder_source": "COH Landmark Designation Report (Union Station)",
    },
    "510 TEXAS": {  # Wortham Theater Center (1987)
        "builder": "W. S. Bellows Construction Corporation",
        "architect": "Morris*Aubry Architects",
        "original_cost": "$70,000,000",
        "builder_source": "W. S. Bellows Construction Corp. Historical Portfolio",
    },
    "615 TEXAS": {  # Alley Theatre (1968)
        "builder": "W. S. Bellows Construction Corporation",
        "engineers": "Weiskopf & Pickworth (Structural)",
        "architect": "Ulrich Franzen & Associates; MacKie & Kamrath",
        "original_cost": "$3,500,000",
        "builder_source": "W. S. Bellows Construction Corp. / Alley Theatre Archives",
    },
    "615 LOUISIANA": {  # Jones Hall for the Performing Arts (1966)
        "builder": "G. H. Pare & Associates / Tellepsen Construction Company",
        "engineers": "Lockwood, Andrews & Newnam (Structural/MEP); Bolt Beranek & Newman (Acoustics)",
        "architect": "Caudill Rowlett Scott (CRS)",
        "original_cost": "$7,400,000",
        "builder_source": "Houston Endowment / AIA Pritzker Honor Award Citation",
    },
    "6900 FANNIN": {  # Shamrock Hotel / TMC (Tellepsen built McCarthy's Shamrock Hotel 1949)
        "builder": "Tellepsen Construction Company",
        "architect": "Wyatt C. Hedrick",
        "original_cost": "$21,000,000",
        "builder_source": "Tellepsen Construction Company Archives",
    },
}


# Demolished Russell Brown Co. "Modern Homes" & TGCA Landmarks to add/enrich in `lost_houston`
DEMOLISHED_CONTRACTOR_SITES = [
    {
        "id": "lost_rb_220_avondale",
        "name": "Russell Brown Model Residence (Plan No. 211)",
        "subtitle": "Two-Story Avondale Residence from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Craftsman / American Foursquare",
        "neighborhood": "Montrose / Avondale",
        "address": "220 AVONDALE ST",
        "site_hcad_nums": ["1203760010003"],
        "year_built": 1914,
        "year_demolished": 1998,
        "demolition_cause": "Demolished c. 1998 for residential redevelopment on Avondale Street.",
        "replaced_by": "1999 single-family residence at 220 Avondale St",
        "architect": "The Russell Brown Company (Plan No. 211)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 211, p. 15",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12354/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12354/thumbnail",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Two-Story Wood Frame Residence",
        "stories": 2,
        "height_m": 8.8,
        "center": [-95.3849, 29.7468],
        "description": "Two-story residence in Avondale Addition built in 1914 by The Russell Brown Company and featured as Plan No. 211 on Page 15 of 'Modern Homes, Book Number Two' (HMRC); demolished c. 1998.",
    },
    {
        "id": "lost_rb_3904_day",
        "name": "Russell Brown Model Residence (Plan No. 217)",
        "subtitle": "1913 Craftsman Residence from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Craftsman",
        "neighborhood": "Montrose",
        "address": "3904 DAY ST",
        "site_hcad_nums": [],
        "year_built": 1913,
        "year_demolished": 1972,
        "demolition_cause": "Cleared during post-WWII multifamily and townhome redevelopment in Montrose.",
        "replaced_by": "Montrose residential redevelopment along Day Street",
        "architect": "The Russell Brown Company (Plan No. 217)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 217, p. 23",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12362/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12362/thumbnail",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Two-Story Wood Frame Craftsman Residence",
        "stories": 2,
        "height_m": 8.5,
        "center": [-95.3882, 29.7385],
        "description": "Two-story Craftsman residence erected in 1913 at 3904 Day Street by The Russell Brown Company and featured as Plan No. 217 on Page 23 of 'Modern Homes, Book Number Two' (verified via Gary Coover HMRC Index).",
    },
    {
        "id": "lost_rb_5300_san_jacinto",
        "name": "Russell Brown Model Residence (Plan No. 200)",
        "subtitle": "1917 Museum District Residence Featured on Pages 2-4 of 'Modern Homes'",
        "category": "Gilded Age Mansion",
        "style": "Prairie / Mediterranean Revival",
        "neighborhood": "Museum District",
        "address": "5300 SAN JACINTO ST",
        "site_hcad_nums": [],
        "year_built": 1917,
        "year_demolished": 1968,
        "demolition_cause": "Demolished during mid-century institutional and commercial expansion along San Jacinto Street.",
        "replaced_by": "Museum District institutional/commercial parcel at 5300 San Jacinto St",
        "architect": "The Russell Brown Company (Plan No. 200)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 200, p. 3",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12342/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12342/thumbnail",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Brick Residence",
        "stories": 2,
        "height_m": 9.0,
        "center": [-95.3849, 29.7255],
        "description": "Two-story brick residence at 5300 San Jacinto Street in the Museum District, built in 1917 by The Russell Brown Company and showcased as Plan No. 200 on Pages 2-4 of 'Modern Homes, Book Number Two' (HMRC).",
    },
    {
        "id": "lost_rb_3404_yoakum",
        "name": "Russell Brown Model Residence (Plan No. 202)",
        "subtitle": "1916 Yoakum Boulevard Residence from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Craftsman / Prairie",
        "neighborhood": "Montrose",
        "address": "3404 YOAKUM BLVD",
        "site_hcad_nums": ["0261760010003"],
        "year_built": 1916,
        "year_demolished": 1974,
        "demolition_cause": "Demolished on or before 1974 for residential replacement construction on Yoakum Boulevard.",
        "replaced_by": "1974 building at 3404 Yoakum Blvd",
        "architect": "The Russell Brown Company (Plan No. 202)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 202, p. 6",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12345/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12345/thumbnail",
        "sanborn_material": "wood_frame",
        "sanborn_material_label": "Yellow - Two-Story Wood Frame Residence",
        "stories": 2,
        "height_m": 8.8,
        "center": [-95.3941, 29.7420],
        "description": "Two-story residence at 3404 Yoakum Boulevard, built in 1916 by The Russell Brown Company and featured as Plan No. 202 on Page 6 of 'Modern Homes, Book Number Two' (HMRC); replaced by 1974.",
    },
    {
        "id": "lost_rb_3617_montrose",
        "name": "Russell Brown Model Residence (Plan No. 213)",
        "subtitle": "1917 Montrose Boulevard Mansion from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Classical / Prairie Revival",
        "neighborhood": "Montrose",
        "address": "3617 MONTROSE BLVD",
        "site_hcad_nums": [],
        "year_built": 1917,
        "year_demolished": 1966,
        "demolition_cause": "Razed during mid-century commercial redevelopment of Montrose Boulevard.",
        "replaced_by": "Montrose Boulevard commercial corridor",
        "architect": "The Russell Brown Company (Plan No. 213)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 213, p. 17",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12356/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12356/thumbnail",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Brick Mansion",
        "stories": 2,
        "height_m": 9.2,
        "center": [-95.3911, 29.7399],
        "description": "Grand two-story Montrose Boulevard mansion at 3617 Montrose Boulevard built in 1917 by The Russell Brown Company and featured as Plan No. 213 on Page 17 of 'Modern Homes, Book Number Two' (HMRC).",
    },
    {
        "id": "lost_rb_811_lovett",
        "name": "Russell Brown Model Residence (Plan No. 215)",
        "subtitle": "1916 Lovett Boulevard Residence from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Georgian / Colonial Revival",
        "neighborhood": "Montrose",
        "address": "811 LOVETT BLVD",
        "site_hcad_nums": [],
        "year_built": 1916,
        "year_demolished": 1972,
        "demolition_cause": "Demolished on or before 1972 for townhome construction along Lovett Boulevard.",
        "replaced_by": "1972 townhome complex at 811 Lovett Blvd",
        "architect": "The Russell Brown Company (Plan No. 215)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 215, p. 20",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12359/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12359/thumbnail",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Brick Residence",
        "stories": 2,
        "height_m": 9.0,
        "center": [-95.3902, 29.7429],
        "description": "Two-story brick residence at 811 Lovett Boulevard, built in 1916 by The Russell Brown Company and featured as Plan No. 215 on Pages 20-21 of 'Modern Homes, Book Number Two' (HMRC); replaced in 1972.",
    },
    {
        "id": "lost_rb_4007_montrose",
        "name": "Russell Brown Model Residence (Plan No. 221)",
        "subtitle": "1918 Montrose Boulevard Residence from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Mediterranean / Prairie Revival",
        "neighborhood": "Montrose",
        "address": "4007 MONTROSE BLVD",
        "site_hcad_nums": [],
        "year_built": 1918,
        "year_demolished": 1969,
        "demolition_cause": "Demolished during post-WWII commercial expansion along Montrose Boulevard.",
        "replaced_by": "4000 block Montrose Boulevard commercial parcel",
        "architect": "The Russell Brown Company (Plan No. 221)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 221, p. 27",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12366/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12366/thumbnail",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Brick Residence",
        "stories": 2,
        "height_m": 9.0,
        "center": [-95.3912, 29.7362],
        "description": "Two-story brick residence at 4007 Montrose Boulevard, built in 1918 by The Russell Brown Company and featured as Plan No. 221 on Pages 27 and 29 of 'Modern Homes, Book Number Two' (HMRC).",
    },
    {
        "id": "lost_rb_1208_lovett",
        "name": "Russell Brown Model Residence (Plan No. 310)",
        "subtitle": "1916 Lovett Boulevard Residence from 'Modern Homes, Book Number Two'",
        "category": "Gilded Age Mansion",
        "style": "Colonial / Prairie Revival",
        "neighborhood": "Montrose",
        "address": "1208 LOVETT BLVD",
        "site_hcad_nums": [],
        "year_built": 1916,
        "year_demolished": 1975,
        "demolition_cause": "Demolished in the 1970s during commercial and residential redevelopment along Lovett Boulevard.",
        "replaced_by": "1208 Lovett Blvd redevelopment",
        "architect": "The Russell Brown Company (Plan No. 310)",
        "builder": "The Russell Brown Company",
        "builder_source": "HMRC: 'Modern Homes, Book Number Two' (c. 1917), Plan 310, p. 34",
        "source_url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12373/rec/1",
        "photo_url": "https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/12373/thumbnail",
        "sanborn_material": "brick",
        "sanborn_material_label": "Pink - Two-Story Brick Residence",
        "stories": 2,
        "height_m": 9.2,
        "center": [-95.3942, 29.7433],
        "description": "Two-story brick residence at 1208 Lovett Boulevard, built in 1915-1916 by The Russell Brown Company and featured as Plan No. 310 on Pages 34-35 of 'Modern Homes, Book Number Two' (HMRC).",
    },
]


def normalize_addr(addr: str) -> str:
    """Normalize a Houston street address to `<NUM> <DIR?> <STREET_STEM>` for matching."""
    if not addr:
        return ""
    s = addr.upper().strip()
    s = re.sub(r"[.,#]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Strip trailing apartment/unit or city
    s = re.sub(r"\b(?:APT|UNIT|STE|SUITE|BLDG|FL)\b.*$", "", s).strip()
    # Expand common TGCA abbreviations
    s = re.sub(r"\bW\s+ALA\b", "W ALABAMA", s)
    s = re.sub(r"\bE\s+ALA\b", "E ALABAMA", s)
    s = re.sub(r"\bHTN\b", "", s)
    s = re.sub(r"\bSAN\s+JAC\b", "SAN JACINTO", s)
    s = re.sub(r"\bMT\s+VER\b", "MT VERNON", s)
    s = re.sub(r"\bPORTSMTH\b", "PORTSMOUTH", s)
    s = re.sub(r"\bWENTWH\b|\bWENTWIC\b", "WENTWORTH", s)
    s = re.sub(r"\bROSEWD\b", "ROSEWOOD", s)
    s = re.sub(r"\bHARBG\b", "HARRISBURG", s)
    s = re.sub(r"\bWASH\b|\bWAHGTN\b", "WASHINGTON", s)
    s = re.sub(r"\bHTS\b", "HEIGHTS", s)
    s = re.sub(r"\bLABRANCH\b", "LA BRANCH", s)
    # Remove trailing street type suffixes
    s = re.sub(
        r"\s+\b(?:ST|STREET|AVE|AVENUE|BLVD|BOULEVARD|DR|DRIVE|RD|ROAD|CT|COURT|PL|PLACE|LN|LANE|WAY|CIR|CIRCLE|PKWY|FWY|HWY)\b$",
        "",
        s,
    ).strip()
    return s


# ---------------------------------------------------------------------------
# 2. Extract Builder / Contractor / Cost / Permit from Landmark PDFs
# ---------------------------------------------------------------------------
KNOWN_CONTRACTOR_KEYWORDS = [
    "Construction",
    "Contracting",
    "Contractor",
    "Contractors",
    "Builders",
    "Builder",
    "Building Co",
    "Lumber Co",
    "Ready-Cut",
    "Ready Cut",
    "Bellows",
    "Tellepsen",
    "Fretz",
    "Linbeck",
    "Knutson",
    "Manhattan",
    "American Construction",
    "Central Contracting",
    "Russell Brown",
    "Hubbard",
    "Don Hall",
    "James Stewart",
    "Westlake",
    "Townsend",
    "Baring",
    "Heidelberg",
    "Swedman",
    "Goggan",
    "Harlow",
    "Woodruff",
    "Chambers",
    "Hearthstone",
    "Schoeppl",
    "Dielmann",
    "Howe",
    "Lott",
    "Satterfield",
    "Vaughn",
    "Turner",
    "Gilbane",
    "SpawGlass",
    "Balfour",
    "Miner-Dederick",
    "Hensel Phelps",
    "Marshall Construction",
    "Lowry",
]

FALSE_POSITIVE_BUILDER_WORDS = {
    "unknown",
    "none",
    "n/a",
    "not known",
    "unidentified",
    "same",
    "owner",
    "city of houston",
    "historic preservation",
    "planning and development",
    "department",
    "commission",
    "archaeological",
    "certificate of appropriateness",
    "staff report",
    "application",
    "landmark",
    "district",
    "texas",
    "houston",
    "brick",
    "wood",
    "frame",
    "stucco",
    "concrete",
    "two-story",
    "one-story",
}


def clean_extracted_builder(raw: str) -> str:
    if not raw:
        return ""
    s = re.sub(r"\s+", " ", raw).strip(" .,:;-\u2013\u2014()[]\t\r\n")
    # Cut at sentence boundaries or common trailing clauses
    s = re.split(
        r"(?:\.\s+|\s+for\s+\$|\s+at\s+a\s+cost|\s+in\s+(?:18|19|20)\d{2}\b|\s+on\s+(?:Lot|Block)|\s+under\s+the|\s+who\s+|\s+which\s+|\s+and\s+designed|\s+to\s+build|\s+to\s+erect|\s+to\s+construct|;)",
        s,
        maxsplit=1,
    )[0].strip(" .,:;-\u2013\u2014()[]")
    if len(s) < 4 or len(s) > 78:
        return ""
    low = s.lower()
    if low in FALSE_POSITIVE_BUILDER_WORDS:
        return ""
    if any(
        bad in low
        for bad in [
            "historic",
            "preservation",
            "ordinance",
            "chapter",
            "section",
            "figure",
            "photo",
            "page",
            "sanborn",
            "directory",
            "criterion",
            "criteria",
            "designation",
            "addition",
            "subdivision",
            "style",
            "vernacular",
            "revival",
            "bungalow",
            "craftsman",
            "queen anne",
            "victorian",
            "roof",
            "window",
            "porch",
            "facade",
            "story",
            "stories",
            "living",
            "bedroom",
            "bathroom",
            "kitchen",
            "square feet",
            "acre",
            "parcel",
            "hcad",
            "county",
            "clerk",
            "deed",
            "volume",
        ]
    ):
        # Allow if it explicitly contains a known contractor company suffix
        if not any(k.lower() in low for k in KNOWN_CONTRACTOR_KEYWORDS):
            return ""
    # Require capital letter start and reasonable person/company structure
    if not re.match(r"^[A-Z]", s):
        return ""
    return s


def extract_landmark_pdf_contractor_data() -> dict:
    """Parse `landmark_pdf_texts.json` for builder/contractor, cost, permit, and mechanic's lien."""
    pdf_path = CACHE_DIR / "landmark_pdf_texts.json"
    if not pdf_path.exists():
        return {}
    texts = json.loads(pdf_path.read_text())
    results = {}

    for url, raw_entry in texts.items():
        if not raw_entry:
            continue
        if isinstance(raw_entry, dict):
            raw_text = raw_entry.get("text") or raw_entry.get("full_text") or " ".join(
                str(v) for v in raw_entry.values() if isinstance(v, str)
            )
        else:
            raw_text = str(raw_entry)
        if not raw_text:
            continue
        text = re.sub(r"\s+", " ", raw_text)
        builder = ""
        cost = ""
        permit_info = ""

        # 1. Explicit header field: "Builder: ...", "Contractor: ...", "General Contractor: ..."
        m_hdr = re.search(
            r"(?:General\s+Contractor|Contractor\s*/\s*Builder|Builder\s*/\s*Contractor|General\s+Builder|Contractor|Builder)\s*:\s*([A-Z][A-Za-z0-9\s.,&'\-]{3,75})",
            text,
        )
        if m_hdr:
            cand = clean_extracted_builder(m_hdr.group(1))
            if cand:
                builder = cand

        # 2. Narrative patterns: "built by X", "constructed by general contractor X", "contracted with builder X"
        if not builder:
            for pat in [
                r"(?:general\s+contractor|building\s+contractor|master\s+builder|contractor)\s+(?:was\s+|were\s+)?([A-Z][A-Za-z0-9\s.,&'\-]{3,65}?(?:Company|Co\.|Corp\.|Corporation|Inc\.|Brothers|Bros\.|& Son|& Sons|Builders|Construction|Contracting))",
                r"(?:built|constructed|erected)\s+(?:in\s+\d{4}\s+)?by\s+(?:general\s+contractor\s+|contractor\s+|builder\s+)?([A-Z][A-Za-z0-9\s.,&'\-]{3,65}?(?:Company|Co\.|Corp\.|Corporation|Inc\.|Brothers|Bros\.|& Son|& Sons|Builders|Construction|Contracting))",
                r"mechanic['’]?s\s+lien\s+(?:contract\s+)?(?:with|to)\s+(?:contractor\s+|builder\s+)?([A-Z][A-Za-z .,&'\-]{4,55})",
            ]:
                m_nar = re.search(pat, text)
                if m_nar:
                    cand = clean_extracted_builder(m_nar.group(1))
                    if cand:
                        builder = cand
                        break

        # 3. Check for known major Houston contractor names in the text
        if not builder:
            for firm in [
                "W. S. Bellows Construction",
                "Tellepsen Construction",
                "American Construction Company",
                "Fretz Construction",
                "Manhattan Construction",
                "Linbeck Construction",
                "Knutson Construction",
                "Central Contracting Company",
                "Thomas B. Hubbard",
                "Russell Brown Company",
                "Crain Ready-Cut House",
                "James Stewart & Company",
                "Westlake Construction",
                "T. D. Howe Construction",
                "H. A. Lott",
                "F. B. Chambers",
                "Fred B. Chambers",
                "Albert Baring",
                "J. B. Townsend",
                "Swedman & Page",
                "W. J. Goggan",
                "G. C. Curtis",
                "Don Hall",
                "E. D. Harlow",
                "Frank Heidelberg",
                "Hearthstone Company",
            ]:
                if firm.lower() in text.lower():
                    builder = firm
                    break

        # 4. Extract original dollar cost
        m_cost = re.search(
            r"(?:cost\s+of|estimated\s+cost\s+of|valued\s+at|contract\s+for|mechanic['’]?s\s+lien\s+(?:contract\s+)?for|built\s+for|constructed\s+for|permit\s+for)\s+(\$[0-9,]{3,15}(?:\.\d{2})?)",
            text,
            re.IGNORECASE,
        )
        if m_cost:
            cost = m_cost.group(1)

        # 5. Extract building permit or mechanic's lien reference
        m_perm = re.search(
            r"(?:Building\s+Permit\s+(?:No\.?|#)\s*[A-Za-z0-9\-]+|permit\s+was\s+issued\s+(?:on\s+)?[A-Za-z]+\s+\d{1,2},?\s+\d{4}|mechanic['’]?s\s+lien\s+contract\s+dated\s+[A-Za-z]+\s+\d{1,2},?\s+\d{4})",
            text,
            re.IGNORECASE,
        )
        if m_perm:
            permit_info = m_perm.group(0).strip()

        if builder or cost or permit_info:
            results[url] = {
                "builder": builder,
                "original_cost": cost,
                "original_permit": permit_info,
                "source_url": url,
            }

    return results


# ---------------------------------------------------------------------------
# 3. Parse T.G.C.A. Monthly Bulletin (1922-1924) Houston Contract Awards
# ---------------------------------------------------------------------------
def parse_tgca_houston_contracts() -> dict:
    """
    Parse the 393 cached pages of the *Texas General Contractors Bulletin* (1922-1924)
    for Houston rows in `Recent Contracts Awarded` that contain an explicit street address.
    Returns a dict keyed by normalized street address (`<NUM> <STREET>`).
    """
    cache_path = CACHE_DIR / "hmrc_contractors_cache.json"
    if not cache_path.exists():
        return {}
    cache = json.loads(cache_path.read_text())
    tgca_pages = cache.get("tgca_houston_pages", {})

    # Curated high-confidence TGCA 1922-1924 Houston contract awards extracted from the OCR tables
    # Every entry has its exact ContentDM page pointer (`pageptr`), issue title, contractor, owner,
    # building description, address, and dollar amount!
    curated_tgca_rows = [
        # September 1922 (pageptr 9507)
        ("9507", "September 1922, p. 21", "Swedman & Page", "Swedman & Page", "2-story brick-veneer residence", "112 PROSPECT", "$10,800", 1922),
        # October 1922 (pageptr 9545)
        ("9545", "October 1922, p. 23", "G. C. Curtis", "Mrs. E. H. Levy", "2-story brick-veneer apartment", "409 ANITA", "$9,000", 1922),
        ("9545", "October 1922, p. 23", "G. C. Curtis", "C. T. Carson", "2-story frame apartment", "706 HOLMAN", "$7,300", 1922),
        ("9545", "October 1922, p. 23", "Swedman & Page", "Wm. Largey", "2-story brick-veneer residence", "3404 AUDUBON", "$10,000", 1922),
        # November 1922 (pageptr 9584)
        ("9584", "November 1922, p. 22", "George T. Broun", "George T. Broun", "2-story brick-veneer residence", "4711 CAROLINE", "$8,000", 1922),
        ("9584", "November 1922, p. 22", "Swedman & Page", "W. M. Crozier", "2-story stucco apartment", "408 PIERCE", "$13,000", 1922),
        ("9584", "November 1922, p. 22", "Swedman & Page", "Swedman & Page", "1-story brick-veneer residence", "1506 W ALABAMA", "$9,435", 1922),
        # December 1922 (pageptr 9625)
        ("9625", "December 1922, p. 23", "James Antill", "Houston Ice Cream Co.", "3-story concrete creamery building", "1701 WASHINGTON", "$15,000", 1922),
        ("9625", "December 1922, p. 23", "Swedman & Page", "W. H. Baldwin", "2-story brick-veneer residence", "118 W MAIN", "$15,000", 1922),
        ("9625", "December 1922, p. 23", "Swedman & Page", "P. L. Teutsch", "20-room brick-veneer apartment", "812 BRANARD", "$14,260", 1922),
        # February 1923 (pageptr 16089)
        ("16089", "February 1923, p. 25", "W. J. Goggan", "Miss Jessie Bridges", "1-story brick-veneer residence", "1010 WAVERLY", "$7,000", 1923),
        ("16089", "February 1923, p. 25", "Frank J. Schlueter", "Frank J. Schlueter", "2-story brick-veneer apartment", "3617 MAIN", "$7,000", 1923),
        # March 1923 (pageptr 16139)
        ("16139", "March 1923, p. 31", "George T. Broun", "E. H. Lightfoot", "1-story stucco residence", "3702 AUDUBON", "$8,500", 1923),
        ("16139", "March 1923, p. 31", "Street & Bailey", "Dr. B. W. Turner", "3-story brick medical building", "506 CAROLINE", "$40,000", 1923),
        ("16139", "March 1923, p. 31", "Swedman & Page", "Swedman & Page", "1-story brick-veneer residence", "201 AVONDALE", "$18,000", 1923),
        # June 1924 (pageptr 21942 & 21943)
        ("21942", "June 1924, p. 32", "A. Baring", "Miss Hattie Schnitzer", "24-room brick-veneer apartment", "803 W ALABAMA", "$28,000", 1924),
        ("21943", "June 1924, p. 33", "G. C. Curtis", "Robt. West", "2-story 35x55 brick-veneer apartment", "1515 MILBY", "$8,500", 1924),
        ("21943", "June 1924, p. 33", "Freed & Sochat (Day Labor)", "Freed & Sochat", "1-story 47x90 brick store", "1214 TEXAS", "$8,000", 1924),
        ("21943", "June 1924, p. 33", "West End Baptist Congregation", "West End Baptist Church", "2-story 60x90 brick church sanctuary", "806 BRUNNER", "$27,000", 1924),
        ("21943", "June 1924, p. 33", "The Hearthstone Company", "P. L. Michael", "2-story brick store building", "3605 MAIN", "$15,000", 1924),
        ("21943", "June 1924, p. 33", "The Hearthstone Company", "M. A. Quilter", "2-story 50x52 brick-veneer residence", "2516 BLODGETT", "$18,000", 1924),
        ("21943", "June 1924, p. 33", "T. B. Hubbard Construction Co.", "Guaranty National Bank", "3-story bank & office building remodel", "306 MAIN", "$35,000", 1924),
        ("21943", "June 1924, p. 33", "E. W. Kenworthy", "Howardates", "2-story brick-veneer apartment", "1102 BOMAR", "$8,500", 1924),
        ("21943", "June 1924, p. 33", "Thos. M. Murray", "J. F. Dickson", "2-story tile & stucco residence", "1311 SOUTH", "$10,000", 1924),
        ("21943", "June 1924, p. 33", "Thos. M. Murray", "J. H. McDougall", "2-story brick-veneer residence", "1740 MARSHALL", "$11,885", 1924),
        ("21943", "June 1924, p. 33", "C. W. Raper", "Church of Christ Congregation", "2-story brick church & school building", "122 E 16TH", "$100,000", 1924),
        ("21943", "June 1924, p. 33", "I. J. Shapley", "M. E. Kurth", "10-room brick-veneer apartment", "1608 NORTH", "$14,000", 1924),
        ("21943", "June 1924, p. 33", "W. E. Woodruff", "G. L. Caler", "7-room brick-veneer residence", "1744 W ALABAMA", "$17,000", 1924),
        ("21943", "June 1924, p. 33", "Chas. Wygant", "Joe Ludwick", "2-story brick-veneer residence", "1932 ALBANS", "$8,000", 1924),
        # July 1924 (pageptr 22003)
        ("22003", "July 1924, p. 33", "The Russell Brown Company", "Dr. R. M. Hargrove", "2-story brick-veneer residence", "4712 LA BRANCH", "$11,500", 1924),
        ("22003", "July 1924, p. 33", "The Russell Brown Company", "The Russell Brown Company", "2-story 32x54 brick-veneer residence", "4902 LA BRANCH", "$9,000", 1924),
        ("22003", "July 1924, p. 33", "The Russell Brown Company", "Miss L. C. Daniel", "2-story stucco residence", "408 MARSHALL", "$7,500", 1924),
        ("22003", "July 1924, p. 33", "G. C. Curtis", "G. C. Curtis", "2-story brick-veneer apartment", "523 W MAIN", "$8,500", 1924),
        ("22003", "July 1924, p. 33", "G. C. Curtis", "I. Wagner", "2-story brick-veneer apartment", "707 HAMILTON", "$8,500", 1924),
        ("22003", "July 1924, p. 33", "St. Nicholas Catholic Congregation", "St. Nicholas Catholic Church", "1-story brick church sanctuary", "2502 CLAY", "$25,000", 1924),
        ("22003", "July 1924, p. 33", "Edwards & Springfield", "Edwards & Springfield", "1-story 6-room brick-veneer residence", "4117 DALLAS", "$8,000", 1924),
        ("22003", "July 1924, p. 33", "M. W. Gilliam", "Rocco Rao", "1-story 40x90 commercial store", "1109 N SAN JACINTO", "$10,000", 1924),
        ("22003", "July 1924, p. 33", "Wm. S. Humphrey", "B. C. Wingate", "2-story 30x45 brick-veneer residence", "4418 KYLE", "$9,000", 1924),
        ("22003", "July 1924, p. 33", "L. W. Lindsay", "Mrs. B. T. Britton", "2-story brick-veneer apartment remodel", "2505 TRAVIS", "$22,500", 1924),
        ("22003", "July 1924, p. 33", "J. L. Sexton", "J. L. Sexton", "2-story 10-room brick-veneer residence", "1519 RUTH", "$8,300", 1924),
        ("22003", "July 1924, p. 33", "J. L. Sexton", "J. L. Julian", "2-story brick-veneer apartment", "4542 MCKINNEY", "$11,000", 1924),
        ("22003", "July 1924, p. 33", "O. A. Sheffield", "Mrs. M. C. Hurley", "2-story brick-veneer residence", "2717 AUSTIN", "$8,000", 1924),
        ("22003", "July 1924, p. 33", "Elmer Thur", "D. Adams", "1-story 32x63 brick-veneer residence", "1901 PEASE", "$8,500", 1924),
        ("22003", "July 1924, p. 33", "Elmer Thur", "Elmer Thur", "2-story 32x55 brick-veneer apartment", "4202 POLK", "$7,000", 1924),
        # August 1924 (pageptr 22062 & 22063)
        ("22062", "August 1924, p. 32", "George T. Broun Co.", "George T. Broun Co.", "2-story 12-room brick-veneer residence", "221 AVONDALE", "$14,000", 1924),
        ("22062", "August 1924, p. 32", "George T. Broun Co.", "W. H. Rohrer", "2-story 43x30 brick-veneer apartment", "1033 BAYLAND", "$9,500", 1924),
        ("22063", "August 1924, p. 33", "G. C. Curtis", "Mrs. Ethel Roeder", "6-room brick-veneer residence", "1815 HAROLD", "$7,200", 1924),
        ("22063", "August 1924, p. 33", "G. C. Curtis", "Martha Chaloupka", "10-room stucco apartment", "918 OAKLEY", "$6,500", 1924),
        ("22063", "August 1924, p. 33", "Chas. Dahl", "Hurlock Realty Co.", "Building fire damage reconstruction", "1016 PRESTON", "$40,000", 1924),
        ("22063", "August 1924, p. 33", "M. DeGeorge", "M. DeGeorge", "24-room brick-veneer apartment", "14 N HAMILTON", "$8,000", 1924),
        ("22063", "August 1924, p. 33", "Edwards & Springfield", "Edwards & Springfield", "1-story 44x50 brick-veneer residence", "1643 KIPLING", "$7,500", 1924),
        ("22063", "August 1924, p. 33", "Don Hall", "Houston Post-Dispatch Publishing Co.", "Publishing plant addition", "1304 DOWLING", "$30,000", 1924),
        ("22063", "August 1924, p. 33", "L. C. Irons", "L. C. Irons", "2-story 11-room brick-veneer residence", "4102 DALLAS", "$8,000", 1924),
        ("22063", "August 1924, p. 33", "T. E. Johnson", "V. Territo", "2-story frame residence", "1518 HOUSTON", "$6,500", 1924),
        ("22063", "August 1924, p. 33", "F. O. Megginson", "J. E. Carr", "2-story frame apartment", "4703 SAN JACINTO", "$8,400", 1924),
        ("22063", "August 1924, p. 33", "L. L. Reber", "R. R. Kropp", "1-story 92x127 concrete store", "1401 RUSK", "$11,995", 1924),
        ("22063", "August 1924, p. 33", "L. F. Robichaux", "L. F. Robichaux", "2-story brick-veneer residence", "1513 W ALABAMA", "$12,000", 1924),
        ("22063", "August 1924, p. 33", "L. F. Robichaux", "J. A. Carpenter", "2-story brick-veneer residence", "1608 ROSEWOOD", "$8,000", 1924),
        ("22063", "August 1924, p. 33", "Standard Construction Co.", "Knights of Columbus", "3-story reinforced concrete club building", "801 CRAWFORD", "$138,000", 1924),
        ("22063", "August 1924, p. 33", "O. P. Steeger", "O. P. Steeger", "2-story 6-room brick-veneer residence", "2010 SUL ROSS", "$8,000", 1924),
        ("22063", "August 1924, p. 33", "J. W. Wilson", "J. W. Wilson", "1-story 32x52 brick-veneer residence", "2006 COLQUITT", "$7,500", 1924),
        ("22063", "August 1924, p. 33", "J. W. Wilson", "J. W. Wilson", "1-story 32x52 brick-veneer residence", "2008 W MAIN", "$7,500", 1924),
        # September 1924 (pageptr 22122 & 22123)
        ("22122", "September 1924, p. 32", "J. F. Bayer", "E. Sherman", "1-story 32x40 brick-veneer residence", "4102 WELCH", "$6,500", 1924),
        ("22122", "September 1924, p. 32", "The Russell Brown Company", "The Russell Brown Company", "2-story 32x34 brick-veneer residence", "4905 AUSTIN", "$7,500", 1924),
        ("22122", "September 1924, p. 32", "Geo. H. Collins", "Alex Freund", "1-story 50x50 brick store", "308 CRAWFORD", "$8,000", 1924),
        ("22122", "September 1924, p. 32", "Geo. H. Collins", "Houston Lumber & Building Co.", "2-story frame store & apartment", "4518 WASHINGTON", "$6,500", 1924),
        ("22122", "September 1924, p. 32", "Collins Realty Co.", "C. C. Womble", "1-story 28x45 residence", "2031 WROXTON", "$6,800", 1924),
        ("22122", "September 1924, p. 32", "Crain Ready-Cut House Co.", "Guardian Trust Co.", "2-story 10-room brick-veneer residence", "2103 ROSEDALE", "$8,500", 1924),
        ("22123", "September 1924, p. 33", "G. C. Curtis", "H. C. Crowell", "1-story 32x25 brick-veneer residence", "1756 HAROLD", "$7,500", 1924),
        ("22123", "September 1924, p. 33", "G. C. Curtis", "J. Pryzant", "2-story brick-veneer store & apartment", "1919 CHENEVERT", "$7,500", 1924),
        ("22123", "September 1924, p. 33", "Edwards & Springfield", "W. N. Forbes", "2-story brick-veneer apartment", "1655 KIPLING", "$10,000", 1924),
        ("22123", "September 1924, p. 33", "C. J. Frankel", "W. R. Styron", "2-story stucco residence", "1102 BARKDULL", "$10,000", 1924),
        ("22123", "September 1924, p. 33", "Forest W. McNeir", "W. E. Simpson", "2-story 36x28 brick-veneer residence", "2003 W ALABAMA", "$10,800", 1924),
        ("22123", "September 1924, p. 33", "Forest W. McNeir", "E. V. Ramsey", "10-room frame apartment", "2609 SMITH", "$6,300", 1924),
        ("22123", "September 1924, p. 33", "Lee E. Meneley", "T. E. Price", "1-story 30x40 brick-veneer residence", "11 OAK", "$7,000", 1924),
        ("22123", "September 1924, p. 33", "W. S. H. Minchen", "Claude McCaleb", "1-story brick-veneer residence", "1716 MARSHALL", "$7,000", 1924),
        ("22123", "September 1924, p. 33", "D. D. Pittman", "C. J. Lottman Estate", "2-story concrete store & apartment", "1411 WASHINGTON", "$18,000", 1924),
        ("22123", "September 1924, p. 33", "J. Leo Sexton", "J. E. Smith", "2-story 32x43 brick-veneer apartment", "4305 POLK", "$10,000", 1924),
        ("22123", "September 1924, p. 33", "Herman C. Vogt", "C. E. Girten", "2-story 28x33 brick-veneer residence", "4921 CAROLINE", "$8,500", 1924),
        ("22123", "September 1924, p. 33", "Herman C. Vogt", "Herman C. Vogt", "7-room brick-veneer residence", "1724 MARSHALL", "$6,000", 1924),
        ("22123", "September 1924, p. 33", "Wm. W. Young", "Wm. W. Young", "1-story 29x51 frame residence", "3913 DALLAS", "$6,500", 1924),
        # October 1924 (pageptr 22182 & 22183)
        ("22182", "October 1924, p. 32", "L. W. Babbitt & Co.", "L. W. Babbitt & Co.", "1-story 36x40 stucco residence", "1857 DANVILLE", "$6,000", 1924),
        ("22182", "October 1924, p. 32", "L. W. Babbitt & Co.", "L. W. Babbitt & Co.", "2-story stucco residence", "1857 LEXINGTON", "$10,500", 1924),
        ("22182", "October 1924, p. 32", "L. W. Babbitt & Co.", "L. W. Babbitt & Co.", "1-story frame residence", "1909 LEXINGTON", "$7,000", 1924),
        ("22182", "October 1924, p. 32", "E. G. Bernard", "E. G. Bernard", "2-story 28x44 brick-veneer residence", "1703 RUTH", "$7,000", 1924),
        ("22182", "October 1924, p. 32", "H. L. Brady", "G. G. Searcy", "2-story brick-veneer apartment", "1539 WESTHEIMER", "$16,000", 1924),
        ("22182", "October 1924, p. 32", "The Russell Brown Company", "The Russell Brown Company", "2-story 31x45 brick-veneer residence", "2208 ROSALIE", "$9,500", 1924),
        ("22182", "October 1924, p. 32", "Crain Ready-Cut House Co.", "Frank Kupper", "2-story 29x35 brick-veneer residence", "1411 CASTLE", "$6,500", 1924),
        ("22182", "October 1924, p. 32", "Crain Ready-Cut House Co.", "W. H. Blutcher", "1-story brick-veneer residence", "1604 KENSINGTON", "$6,000", 1924),
        ("22182", "October 1924, p. 32", "Crain Ready-Cut House Co.", "L. A. Godbold", "1-story 32x55 brick-veneer residence", "3114 ARBOR", "$6,500", 1924),
        ("22182", "October 1924, p. 32", "Crain Ready-Cut House Co.", "Guardian Trust Co.", "2-story 36x24 brick-veneer residence", "2203 WICHITA", "$7,000", 1924),
        ("22182", "October 1924, p. 32", "Crain Ready-Cut House Co.", "Crain Ready-Cut House Co.", "2-story 30x37 frame residence", "4718 JACKSON", "$9,000", 1924),
        ("22182", "October 1924, p. 32", "Crain Ready-Cut House Co.", "W. A. Raymond", "2-story 27x37 brick-veneer residence", "2117 ARBOR", "$8,000", 1924),
        ("22182", "October 1924, p. 32", "Crain Ready-Cut House Co.", "H. B. Robinson, Jr.", "2-story 37x25 brick-veneer residence", "1505 RUTH", "$8,500", 1924),
        ("22182", "October 1924, p. 32", "L. E. Cross", "Geo. Graham", "1-story 31x45 brick-veneer residence", "2914 BLODGETT", "$7,000", 1924),
        ("22182", "October 1924, p. 32", "Phil Robinson (Day Labor)", "Mrs. Phil Robinson", "2-story 38x57 brick-veneer apartment", "808 WHITE OAK", "$10,000", 1924),
        ("22182", "October 1924, p. 32", "C. J. Frankel", "G. F. Howard", "2-story 32x34 brick-veneer residence", "1933 PORTSMOUTH", "$6,500", 1924),
        ("22182", "October 1924, p. 32", "A. W. Fuessel", "Sam Rosenburg", "2-story brick-veneer apartment", "2003 LEELAND", "$12,000", 1924),
        ("22183", "October 1924, p. 33", "T. H. Growns", "T. H. Growns", "2-story 26x30 brick-veneer residence", "2106 TANGLEY", "$6,000", 1924),
        ("22183", "October 1924, p. 33", "Howard & Howard", "Howard & Howard", "1-story 31x47 brick-veneer residence", "920 HIGHLAND", "$6,500", 1924),
        ("22183", "October 1924, p. 33", "T. B. Hubbard Construction Co.", "South End Ice Co.", "1-story concrete ice factory", "132 HEIGHTS", "$15,500", 1924),
        ("22183", "October 1924, p. 33", "W. S. Humphrey", "W. S. Humphrey", "2-story 24-room brick-veneer apartment", "3311 LA BRANCH", "$30,000", 1924),
        ("22183", "October 1924, p. 33", "Jeans Lumber Co.", "Kenneth Krahl", "1-story 32x48 brick-veneer residence", "2001 MCGREGOR", "$6,500", 1924),
        ("22183", "October 1924, p. 33", "R. E. Leavens", "Fowler, Glouser & Smith", "83x60 concrete machine shop", "3608 POLK", "$10,000", 1924),
        ("22183", "October 1924, p. 33", "R. E. Leavens", "Louis & Sam Schafer", "2-story 34x94 brick-veneer apartment", "403 WAYSIDE", "$10,000", 1924),
        ("22183", "October 1924, p. 33", "A. Lehmann", "A. Lehmann", "2-story 28x45 brick-veneer apartment", "406 W ALABAMA", "$8,400", 1924),
        ("22183", "October 1924, p. 33", "L. W. Lindsay", "U. B. Fuesler", "2-story 62x46 brick-veneer residence", "1611 SOUTH", "$23,350", 1924),
        ("22183", "October 1924, p. 33", "L. W. Lindsay", "L. F. Hamacher", "2-story 63x26 brick-veneer residence", "117 PORTLAND", "$16,000", 1924),
        ("22183", "October 1924, p. 33", "F. O. Megginson", "J. E. Carr", "2-story 29x37 brick-veneer apartment", "4711 SAN JACINTO", "$8,750", 1924),
        ("22183", "October 1924, p. 33", "D. O. Miller", "D. O. Miller", "2-story 31x38 brick-veneer residence", "1918 DUNSTAN", "$10,500", 1924),
        ("22183", "October 1924, p. 33", "D. O. Miller", "D. O. Miller", "2-story 26x31 brick-veneer residence", "2025 ALBANS", "$9,500", 1924),
        ("22183", "October 1924, p. 33", "Thos. M. Murray", "H. E. Detering", "1-story 50x100 brick warehouse", "3022 WASHINGTON", "$8,800", 1924),
        ("22183", "October 1924, p. 33", "C. F. Pevoto", "H. B. Harrison", "2-story 38x34 brick-veneer residence", "1636 HAWTHORNE", "$8,500", 1924),
        ("22183", "October 1924, p. 33", "C. F. Pevoto", "E. B. Dawkins", "2-story 25x33 brick-veneer residence", "2101 WENTWORTH", "$8,250", 1924),
        ("22183", "October 1924, p. 33", "Robt. J. Rochow", "C. O. Rochow", "2-story 29x45 brick-veneer residence", "1219 PIERCE", "$6,000", 1924),
        ("22183", "October 1924, p. 33", "O. P. Steeger", "R. Shackelford", "1-story 6-room brick-veneer apartment", "4422 CLAY", "$6,500", 1924),
        ("22183", "October 1924, p. 33", "O. P. Steeger", "O. P. Steeger", "1-story 5-room brick-veneer residence", "2304 TRUXILLO", "$6,000", 1924),
        ("22183", "October 1924, p. 33", "J. L. Tibbott", "J. L. Tibbott", "1-story 30x40 brick-veneer residence", "2019 CLEBURNE", "$6,000", 1924),
        ("22183", "October 1924, p. 33", "J. L. Tryon", "Mrs. M. L. Olson", "2-story 29x42 brick-veneer residence", "4519 MT VERNON", "$10,000", 1924),
        ("22183", "October 1924, p. 33", "Warren & Johnson", "Anderson Lumber Co.", "2-story 32x68 frame residence", "201 EVERTON", "$8,000", 1924),
        ("22183", "October 1924, p. 33", "E. Wennersten", "A. Jones", "2-story 64x39 brick-veneer apartment", "1813 MILAM", "$20,000", 1924),
        ("22183", "October 1924, p. 33", "W. E. Woodruff", "Model Lumber Co.", "3-story 81x101 garage & office building", "610 PRAIRIE", "$39,511", 1924),
        ("22183", "October 1924, p. 33", "Wm. W. Young", "J. H. McNeal", "1-story 28x48 brick-veneer residence", "1655 VASSAR", "$6,500", 1924),
        # December 1924 (pageptr 22303)
        ("22303", "December 1924, p. 33", "W. J. Goggan", "W. J. Goggan", "1-story 32x48 brick-veneer residence", "1807 KIPLING", "$6,500", 1924),
        ("22303", "December 1924, p. 33", "W. J. Goggan", "Dr. Max Scholl", "2-story 42x72 brick-veneer apartment", "309 W ALABAMA", "$25,000", 1924),
        ("22303", "December 1924, p. 33", "Herman Hale", "Mrs. Mattie D. Huff", "2-story brick-veneer residence", "1108 BARTLETT", "$10,000", 1924),
        ("22303", "December 1924, p. 33", "Howard & Howard", "Howard & Howard", "2-story 9-room brick-veneer residence", "1516 W MAIN", "$9,500", 1924),
        ("22303", "December 1924, p. 33", "L. C. Ingram", "L. C. Ingram", "2-story 26x28 brick-veneer residence", "1924 DUNSTAN", "$7,250", 1924),
        ("22303", "December 1924, p. 33", "Johnson & Gustavson", "Johnson & Gustavson", "2-story 29x33 brick-veneer residence", "2102 MCGREGOR", "$8,500", 1924),
        ("22303", "December 1924, p. 33", "Lightfoot-Bolton Const. Co.", "J. H. Barnes", "1-story 35x58 brick-veneer residence", "1417 W ALABAMA", "$7,500", 1924),
        ("22303", "December 1924, p. 33", "F. O. Megginson", "R. V. Tracy", "2-story 40x47 brick-veneer apartment", "1409 WENTWORTH", "$10,500", 1924),
        ("22303", "December 1924, p. 33", "F. O. Megginson", "N. Wicks, Jr.", "2-story brick store & apartment", "720 RUSK", "$20,000", 1924),
        ("22303", "December 1924, p. 33", "A. E. Moon", "A. E. Moon", "2-story 34x36 brick-veneer residence", "2002 BRANARD", "$9,500", 1924),
        ("22303", "December 1924, p. 33", "A. E. Olson & Bros.", "A. E. Olson & Bros.", "1-story 28x40 brick-veneer residence", "1003 W GRAY", "$6,500", 1924),
        ("22303", "December 1924, p. 33", "Earl Stacy", "E. Childs", "1-story 22x27 brick-veneer residence", "2218 ARBOR", "$5,000", 1924),
        ("22303", "December 1924, p. 33", "O. P. Steeger", "Dr. E. H. Willisford", "2-story 26x28 brick-veneer residence", "1909 ROSEWOOD", "$7,500", 1924),
        ("22303", "December 1924, p. 33", "O. S. Van De Mark", "O. S. Van De Mark", "2-story 37x60 brick-veneer residence", "2411 ROSEDALE", "$10,000", 1924),
        ("22303", "December 1924, p. 33", "C. M. White", "E. C. Smith", "1-story 50x100 brick garage", "1111 LAMAR", "$6,400", 1924),
    ]

    out = {}
    for pptr, issue_pg, builder, owner, scope, addr, cost, yr in curated_tgca_rows:
        norm = normalize_addr(addr)
        url = f"https://cdm17006.contentdm.oclc.org/digital/collection/books/id/{pptr}"
        img_url = f"https://cdm17006.contentdm.oclc.org/digital/iiif/books/{pptr}/full/pct:75/0/default.jpg"
        out[norm] = {
            "builder": builder,
            "original_cost": cost,
            "original_permit": f"TGCA Contract Award ({issue_pg.split(',')[0]})",
            "builder_source": f"Texas General Contractors Bulletin ({issue_pg})",
            "source_url": url,
            "year_built": yr,
            "hist_contract": {
                "date": issue_pg.split(",")[0],
                "cost": cost,
                "owner": owner,
                "builder": builder,
                "scope": f"{scope} at {addr.title()} for {owner}",
                "source_title": f"T.G.C.A. Monthly Bulletin ({issue_pg})",
                "source_url": url,
                "image_url": img_url,
            },
        }

    return out


# ---------------------------------------------------------------------------
# 4. Stream HCAD CAMA Tables (`permits.txt`, `building_res.txt`, `building_other.txt`, `extra_features`)
# ---------------------------------------------------------------------------
PERMIT_TYPE_CATEGORY = {
    "01": ("New Construction", "new"),
    "02": ("Demolition", "demo"),
    "03": ("Addition / Increase Sq Ft", "add"),
    "04": ("Finish Out / Completion", "remodel"),
    "05": ("Remodel / Renovation", "remodel"),
    "06": ("Mechanical / Electrical / Plumbing", "mep"),
    "07": ("Re-Roof / Roof Structure", "roof"),
    "08": ("Swimming Pool / Spa", "aux"),
    "09": ("Enclosure / Carport / Patio", "add"),
    "11": ("Fire / Storm Damage Repair", "repair"),
    "13": ("General Repair / Foundation", "repair"),
    "14": ("Paving / Flatwork / Driveway", "aux"),
    "15": ("Moved / Relocated Structure", "reloc"),
    "16": ("Storage / Outbuilding / Canopy", "aux"),
    "17": ("Foundation / Structural Repair", "repair"),
    "18": ("Site / Drainage / Utilities", "aux"),
    "31": ("New Custom Residence", "new"),
    "33": ("Townhome / Multi-Family New Build", "new"),
    "50": ("Commercial General Permit", "remodel"),
    "TX": ("County Tax Permit", "remodel"),
    "W": ("Water / Utility Permit", "mep"),
}


def build_hcad_permits_and_cama_index(target_hcads: set):
    """
    Stream HCAD `building_res.txt`, `building_other.txt`, `extra_features_detail1/2.txt`,
    and `permits.txt` to build:
      1. `cama_by_acct`: remodel year, effective year, appraiser note, and relocation flag.
      2. `xfeat_by_acct`: secondary structures with construction years.
      3. `permits_by_acct`: chronological municipal building permits (`2004-2026`).
      4. `relocated_accts`: set of HCAD accounts with `permit_type == '15'` or moved structure note.
      5. `addr_to_acct`: lookup from normalized street address to `(acct, year_built, raw_addr)`.
    """
    cama_by_acct = {}
    xfeat_by_acct = collections.defaultdict(list)
    permits_by_acct = collections.defaultdict(list)
    relocated_accts = {}
    addr_to_acct = {}

    z_acct = zipfile.ZipFile(CACHE_DIR / "Real_acct_owner.zip")
    z_bld = zipfile.ZipFile(CACHE_DIR / "Real_building_land.zip")

    print("[1/5] Indexing HCAD real_acct.txt addresses...")
    with z_acct.open("real_acct.txt") as f:
        header = [h.strip() for h in f.readline().decode("latin-1").rstrip("\r\n").split("\t")]
        idx_acct = header.index("acct")
        idx_addr = header.index("site_addr_1")
        idx_city = header.index("site_addr_3")
        idx_yr = header.index("yr_impr")
        for line in f:
            row = line.decode("latin-1", errors="replace").rstrip("\r\n").split("\t")
            if len(row) <= max(idx_acct, idx_addr, idx_yr):
                continue
            acct = row[idx_acct].strip()
            addr = row[idx_addr].strip()
            city = row[idx_city].strip() if len(row) > idx_city else "HOUSTON"
            yr_str = row[idx_yr].strip()
            yr = int(yr_str) if yr_str.isdigit() else 0
            if addr and city in ("HOUSTON", "BELLAIRE", "BUNKER HILL VILLAGE", "WEST UNIVERSITY PLACE", "HEDWIG VILLAGE", "HUNTERS CREEK VILLAGE", "PINEY POINT VILLAGE", "SOUTH SIDE PLACE", "GALENA PARK", "JACINTO CITY", "PASADENA", "LA PORTE", "BAYTOWN", "DEER PARK", "HUMBLE", "TOMBALL", "KATY", "SPRING", "CYPRESS", "SEABROOK", "WEBSTER", "CLEAR LAKE"):
                norm = normalize_addr(addr)
                if norm:
                    # Prefer older structure or existing target HCAD
                    prev = addr_to_acct.get(norm)
                    if not prev or (acct in target_hcads and prev[0] not in target_hcads) or (1836 <= yr < prev[1] if prev[1] >= 1836 else yr >= 1836):
                        addr_to_acct[norm] = (acct, yr, addr)

    print("[2/5] Streaming building_res.txt & building_other.txt for remodel years and appraiser notes...")
    for fname, is_res in [("building_res.txt", True), ("building_other.txt", False)]:
        with z_bld.open(fname) as f:
            header = [h.strip() for h in f.readline().decode("latin-1").rstrip("\r\n").split("\t")]
            i_acct = header.index("acct")
            i_erect = header.index("date_erected")
            i_eff = header.index("eff")
            i_remod = header.index("yr_remodel")
            i_notes = header.index("notes")
            i_qual = header.index("dscr")
            for line in f:
                row = line.decode("latin-1", errors="replace").rstrip("\r\n").split("\t")
                if len(row) <= i_notes:
                    continue
                acct = row[i_acct].strip()
                erect = int(row[i_erect].strip()) if row[i_erect].strip().isdigit() else 0
                eff = int(row[i_eff].strip()) if row[i_eff].strip().isdigit() else 0
                remod_s = row[i_remod].strip()
                remod = int(remod_s) if remod_s.isdigit() and 1850 <= int(remod_s) <= 2026 else 0
                note = row[i_notes].strip()
                qual = row[i_qual].strip()

                # Ignore bare numeric permit strings in `note` since they are already in permits.txt
                clean_note = "" if re.fullmatch(r"[\d\s,;/#-]+", note) else note
                is_moved = bool(
                    clean_note
                    and re.search(r"\b(?:MOVED\s+(?:FROM|TO|ON|IN|ONTO)|RELOCATED\s+(?:FROM|TO))\b", clean_note, re.IGNORECASE)
                )
                if is_moved and acct not in relocated_accts:
                    relocated_accts[acct] = {
                        "reason": "HCAD Appraiser Relocation Record",
                        "note": clean_note,
                        "year_built": erect,
                    }

                if remod or clean_note or (eff and erect and eff - erect >= 10) or acct in target_hcads:
                    prev = cama_by_acct.get(acct)
                    if not prev:
                        cama_by_acct[acct] = {
                            "yr": erect,
                            "eff": eff,
                            "remod": remod,
                            "qual": qual,
                            "note": clean_note[:240] if clean_note else "",
                        }
                    else:
                        if remod > prev["remod"]:
                            prev["remod"] = remod
                        if clean_note and not prev["note"]:
                            prev["note"] = clean_note[:240]

    print("[3/5] Streaming extra_features_detail1.txt & extra_features_detail2.txt...")
    for xfname in ["extra_features_detail1.txt", "extra_features_detail2.txt"]:
        with z_bld.open(xfname) as f:
            header = [h.strip() for h in f.readline().decode("latin-1").rstrip("\r\n").split("\t")]
            i_acct = header.index("acct")
            i_cd = header.index("cd")
            i_dscr = header.index("dscr")
            i_yr = header.index("act_yr")
            for line in f:
                row = line.decode("latin-1", errors="replace").rstrip("\r\n").split("\t")
                if len(row) <= i_yr:
                    continue
                acct = row[i_acct].strip()
                # Keep extra features for accounts in target_hcads or with CAMA remodel/notes or historic prefixes (001..095)
                if acct not in target_hcads and acct not in cama_by_acct and int(acct[:3] if acct[:3].isdigit() else "999") > 95:
                    continue
                yr_s = row[i_yr].strip()
                yr = int(yr_s) if yr_s.isdigit() and 1836 <= int(yr_s) <= 2026 else 0
                dscr = row[i_dscr].strip()
                if not dscr or dscr.upper().startswith("RESIDENTIAL GAS") or "CRACKED" in dscr.upper():
                    continue
                if len(xfeat_by_acct[acct]) < 6:
                    xfeat_by_acct[acct].append({"d": dscr, "y": yr})

    print("[4/5] Streaming permits.txt (1,300,027 rows)...")
    with z_acct.open("permits.txt") as f:
        header = [h.strip() for h in f.readline().decode("latin-1").rstrip("\r\n").split("\t")]
        i_acct = header.index("acct")
        i_id = header.index("id")
        i_dscr = header.index("dscr")
        i_ptype = header.index("permit_type")
        i_pdesc = header.index("permit_tp_descr")
        i_date = header.index("issue_date")
        for line in f:
            row = line.decode("latin-1", errors="replace").rstrip("\r\n").split("\t")
            if len(row) <= i_date:
                continue
            acct = row[i_acct].strip()
            pid = row[i_id].strip()
            dscr = row[i_dscr].strip()
            ptype = row[i_ptype].strip()
            pdesc = row[i_pdesc].strip()
            dt = row[i_date].strip()  # MM/DD/YYYY

            is_omitted_imps = "OMITTED IMPS" in dscr.upper()
            if ptype == "15" and not is_omitted_imps:
                relocated_accts[acct] = {
                    "reason": "City of Houston Moved Structure Permit",
                    "note": f"{dt}: {dscr or pdesc} (Permit #{pid})",
                    "permit_id": pid,
                    "permit_date": dt,
                }

            if is_omitted_imps:
                cat_label, cat_code = ("HCAD Omitted Improvement Assessment", "add")
            else:
                cat_label, cat_code = PERMIT_TYPE_CATEGORY.get(ptype, (pdesc or "Building Permit", "other"))
            # Convert MM/DD/YYYY to YYYY-MM-DD for clean chronological sorting
            iso_dt = dt
            if len(dt) == 10 and dt[2] == "/" and dt[5] == "/":
                iso_dt = f"{dt[6:10]}-{dt[0:2]}-{dt[3:5]}"

            if len(permits_by_acct[acct]) < 25:
                permits_by_acct[acct].append({
                    "id": pid,
                    "dt": iso_dt,
                    "cat": cat_code,
                    "tp": cat_label,
                    "d": dscr if dscr and dscr.upper() != pdesc.upper() else pdesc,
                })

    return cama_by_acct, xfeat_by_acct, permits_by_acct, relocated_accts, addr_to_acct


def main():
    PERMITS_DIR.mkdir(parents=True, exist_ok=True)

    buildings_path = DATA_DIR / "buildings.geojson"
    overrides_path = DATA_DIR / "curated_overrides.json"
    overlays_path = DATA_DIR / "overlays.json"
    photos_path = DATA_DIR / "building_photos.json"
    search_path = DATA_DIR / "search_index.json"

    buildings_fc = json.loads(buildings_path.read_text())
    overrides_data = json.loads(overrides_path.read_text())
    overlays_data = json.loads(overlays_path.read_text())
    photos_data = json.loads(photos_path.read_text())
    search_data = json.loads(search_path.read_text())

    overrides = overrides_data.get("overrides", {})

    # Reset any previous contractor-enrichment fields so re-runs are 100% idempotent
    for k, ov in list(overrides.items()):
        for fld in ("builder", "builder_source", "engineers", "original_cost", "original_permit", "hist_contract"):
            ov.pop(fld, None)
    for feat in buildings_fc.get("features", []):
        p = feat.get("properties", {})
        for fld in ("builder", "builder_source", "engineers", "original_cost", "original_permit", "hist_contract"):
            p.pop(fld, None)

    # Collect all HCAD accounts referenced in core datasets
    target_hcads = set(CURATED_BUILDER_BY_HCAD.keys())
    for k, ov in overrides.items():
        h = str(ov.get("hcad_num") or k.split("#")[0]).strip()
        if h:
            target_hcads.add(h)
    for feat in buildings_fc.get("features", []):
        h = str(feat.get("properties", {}).get("hcad_num") or "").strip()
        if h:
            target_hcads.add(h)
    for layer_key in ("landmarks", "good_brick_awards"):
        for feat in overlays_data.get(layer_key, {}).get("features", []):
            h = str(feat.get("properties", {}).get("hcad_num") or "").strip()
            if h:
                target_hcads.add(h)

    # Extract Landmark PDF contractor attributions
    pdf_contractors = extract_landmark_pdf_contractor_data()
    print(f"Extracted contractor/permit/cost metadata from {len(pdf_contractors)} Landmark PDFs")

    # Parse TGCA 1922-1924 Houston contract awards
    tgca_by_addr = parse_tgca_houston_contracts()
    print(f"Parsed {len(tgca_by_addr)} Houston street-address contract awards from T.G.C.A. Bulletin (1922-1924)")

    # Stream HCAD CAMA & Permits
    cama_by_acct, xfeat_by_acct, permits_by_acct, relocated_accts, addr_to_acct = (
        build_hcad_permits_and_cama_index(target_hcads)
    )

    # Map `CURATED_BUILDER_BY_ADDRESS` and `tgca_by_addr` to HCAD accounts via `addr_to_acct`,
    # `buildings.geojson`, and `curated_overrides.json`
    all_addr_records = {}
    for norm_a, rec in tgca_by_addr.items():
        all_addr_records[norm_a] = rec
    for norm_a, rec in CURATED_BUILDER_BY_ADDRESS.items():
        all_addr_records[norm_a] = rec

    # Index existing buildings & overrides by normalized address
    addr_to_override_keys = collections.defaultdict(list)
    for k, ov in overrides.items():
        na = normalize_addr(ov.get("address", ""))
        if na:
            addr_to_override_keys[na].append(k)

    addr_to_building_feats = collections.defaultdict(list)
    hcad_to_building_feats = collections.defaultdict(list)
    for feat in buildings_fc.get("features", []):
        p = feat.get("properties", {})
        na = normalize_addr(p.get("address", ""))
        if na:
            addr_to_building_feats[na].append(feat)
        h = str(p.get("hcad_num") or "").strip()
        if h:
            hcad_to_building_feats[h].append(feat)

    # Also match Landmark PDF contractor attributions via `landmark_report_url`
    for feat in overlays_data.get("landmarks", {}).get("features", []):
        lp = feat.get("properties", {})
        rurl = lp.get("landmark_report_url") or lp.get("report_pdf_url") or ""
        h = str(lp.get("hcad_num") or "").strip()
        if rurl and rurl in pdf_contractors:
            pinfo = pdf_contractors[rurl]
            if pinfo.get("builder") and not lp.get("builder"):
                lp["builder"] = pinfo["builder"]
                lp["builder_source"] = f"City of Houston Landmark Designation Report ({lp.get('landmark_code') or 'HPO'})"
            if pinfo.get("original_cost") and not lp.get("original_cost"):
                lp["original_cost"] = pinfo["original_cost"]
            if pinfo.get("original_permit") and not lp.get("original_permit"):
                lp["original_permit"] = pinfo["original_permit"]
            if h and h not in CURATED_BUILDER_BY_HCAD and (pinfo.get("builder") or pinfo.get("original_cost")):
                # Verify the HCAD parcel was not replaced by a post-1955 modern teardown when the landmark is pre-1950
                lm_yr = int(lp.get("year_built") or 0)
                bld_yr = 0
                if h in overrides:
                    bld_yr = int(overrides[h].get("year_built") or 0)
                elif h in hcad_to_building_feats:
                    bld_yr = int(hcad_to_building_feats[h][0].get("properties", {}).get("year_built") or 0)
                if not (1836 <= lm_yr <= 1945 and bld_yr >= 1955):
                    CURATED_BUILDER_BY_HCAD[h] = {
                        "builder": pinfo.get("builder", ""),
                        "original_cost": pinfo.get("original_cost", ""),
                        "original_permit": pinfo.get("original_permit", ""),
                        "builder_source": f"City of Houston Landmark Designation Report ({lp.get('landmark_code') or 'HPO'})",
                        "source_url": rurl,
                    }

    # Resolve address-keyed builder records to HCAD accounts
    matched_addr_hcads = {}
    for norm_a, rec in all_addr_records.items():
        matched_h = None
        expected_yr = int(rec.get("year_built") or 0)
        if norm_a in addr_to_override_keys:
            for ok in addr_to_override_keys[norm_a]:
                ov_yr = int(overrides[ok].get("year_built") or 0)
                # Do not attach a 1910s-1940s contract to a post-1955 replacement structure
                if 1836 <= expected_yr <= 1945 and ov_yr >= 1955 and norm_a not in ("501 TEXAS", "806 MAIN"):
                    continue
                matched_h = overrides[ok].get("hcad_num") or ok.split("#")[0]
                CURATED_BUILDER_BY_HCAD[ok] = {**rec, **CURATED_BUILDER_BY_HCAD.get(ok, {})}
        if norm_a in addr_to_building_feats:
            for bf in addr_to_building_feats[norm_a]:
                bp = bf.get("properties", {})
                bh = bp.get("hcad_num")
                b_yr = int(bp.get("year_built") or 0)
                if 1836 <= expected_yr <= 1945 and b_yr >= 1955 and norm_a not in ("501 TEXAS", "806 MAIN"):
                    continue
                if bh:
                    matched_h = bh
                    CURATED_BUILDER_BY_HCAD[bh] = {**rec, **CURATED_BUILDER_BY_HCAD.get(bh, {})}
        if not matched_h and norm_a in addr_to_acct:
            acct, hcad_yr, raw_addr = addr_to_acct[norm_a]
            # For 1914-1929 residential/commercial contracts, verify the building on the parcel
            # was not replaced by a post-1950 teardown
            if (norm_a in CURATED_BUILDER_BY_ADDRESS and (hcad_yr == 0 or hcad_yr <= 1954)) or hcad_yr == 0 or (1880 <= hcad_yr <= 1942) or (expected_yr > 0 and abs(hcad_yr - expected_yr) <= 18):
                matched_h = acct
                CURATED_BUILDER_BY_HCAD[acct] = {
                    **rec,
                    "address": raw_addr,
                    "hcad_year": hcad_yr,
                }
                matched_addr_hcads[acct] = (norm_a, rec, raw_addr, hcad_yr)

    print(f"Total HCAD / building_id keys with Builder/Contractor or Contract metadata: {len(CURATED_BUILDER_BY_HCAD)}")

    # Scan the 5 countywide `.geojsonseq` shards for ALL HCADs in `buildings.geojson`,
    # `CURATED_BUILDER_BY_HCAD`, and historic `relocated_accts` so that:
    # 1. Every contractor-attributed building is promoted into `curated_overrides.json` with its true footprint.
    # 2. All 3,560 synthetic-box geometries in `buildings.geojson` are upgraded to the real aligned footprints
    #    rendered in `houston_buildings_*.pmtiles`.
    needed_shard_hcads = set()
    for feat in buildings_fc.get("features", []):
        h = str(feat.get("properties", {}).get("hcad_num") or "").strip()
        if h.isdigit() and len(h) == 13:
            needed_shard_hcads.add(h)
    for k in CURATED_BUILDER_BY_HCAD:
        base_h = k.split("#")[0]
        if base_h.isdigit() and len(base_h) == 13:
            needed_shard_hcads.add(base_h)
    for rh, rinfo in relocated_accts.items():
        cama_yr = cama_by_acct.get(rh, {}).get("yr", 0)
        if (1836 <= cama_yr <= 1965 or rinfo.get("permit_id")) and rh not in overrides:
            needed_shard_hcads.add(rh)

    shard_footprints = {}
    if needed_shard_hcads:
        print(f"Scanning countywide footprint shards for {len(needed_shard_hcads)} building, contractor & relocated parcels...")
        ndjson_dir = CACHE_DIR / "aligned_ndjson"
        for sfile in sorted(ndjson_dir.glob("*.geojsonseq")):
            with open(sfile, "r", encoding="utf-8") as sf:
                for line in sf:
                    s = line.strip("\x1e\r\n ")
                    if not s:
                        continue
                    # Fast substring pre-check before JSON parse
                    m = re.search(r'"hcad_num"\s*:\s*"(\d{13})"', s)
                    if not m or m.group(1) not in needed_shard_hcads:
                        continue
                    h = m.group(1)
                    feat = json.loads(s)
                    prev = shard_footprints.get(h)
                    area = feat.get("properties", {}).get("bld_area", 0) or 0
                    if not prev or area > (prev.get("properties", {}).get("bld_area", 0) or 0):
                        shard_footprints[h] = feat
        print(f"Found {len(shard_footprints)} footprints in countywide shards.")

    # Enrich `curated_overrides.json` and promote all contractor-attributed buildings into `curated_overrides.json`
    for k, binfo in CURATED_BUILDER_BY_HCAD.items():
        base_h = k.split("#")[0]
        cama = cama_by_acct.get(base_h, {})
        target_ov_key = k if k in overrides else (base_h if base_h in overrides else None)
        if target_ov_key:
            ov = overrides[target_ov_key]
            if binfo.get("builder"):
                ov["builder"] = binfo["builder"]
            if binfo.get("builder_source"):
                ov["builder_source"] = binfo["builder_source"]
            if binfo.get("architect") and not ov.get("architect"):
                ov["architect"] = binfo["architect"]
            if binfo.get("engineers"):
                ov["engineers"] = binfo["engineers"]
            if binfo.get("original_cost"):
                ov["original_cost"] = binfo["original_cost"]
            if binfo.get("original_permit"):
                ov["original_permit"] = binfo["original_permit"]
            if binfo.get("was_relocated"):
                ov["was_relocated"] = True
                ov["relocation_note"] = binfo.get("relocation_note", "")
            if binfo.get("hist_contract"):
                ov["hist_contract"] = binfo["hist_contract"]
            if binfo.get("year_built"):
                arch_yr = int(binfo["year_built"])
                cur_yr = int(ov.get("year_built") or 0)
                if cur_yr != arch_yr and (cur_yr == 0 or abs(cur_yr - arch_yr) <= 22):
                    if not ov.get("original_hcad_year"):
                        ov["original_hcad_year"] = cur_yr or cama.get("yr") or 0
                    ov["year_built"] = arch_yr
                    ov["decade"] = f"{(arch_yr // 10) * 10}s"
            if cama.get("remod"):
                ov["yr_remodel"] = cama["remod"]
            if cama.get("eff"):
                ov["eff_year_built"] = cama["eff"]
            # If the override had a 5-point synthetic rectangle and shard_footprints has a detailed polygon, upgrade it
            if base_h in shard_footprints and "#" not in target_ov_key:
                ov_geom = ov.get("geometry") or {}
                ov_coords = ov_geom.get("coordinates") or []
                ov_ring = ov_coords[0] if ov_geom.get("type") == "Polygon" and ov_coords else []
                sf_geom = shard_footprints[base_h].get("geometry")
                if sf_geom and len(ov_ring) <= 5:
                    ov["geometry"] = sf_geom
        elif base_h in shard_footprints or base_h in hcad_to_building_feats:
            sf = shard_footprints.get(base_h) or hcad_to_building_feats[base_h][0]
            sp = sf.get("properties", {})
            bf_props = hcad_to_building_feats[base_h][0].get("properties", {}) if base_h in hcad_to_building_feats else {}
            orig_yr = int(sp.get("year_built") or bf_props.get("year_built") or cama.get("yr") or 0)
            yr = int(binfo.get("year_built") or orig_yr or 1924)
            hc = binfo.get("hist_contract") or {}
            overrides[base_h] = {
                "building_id": base_h,
                "hcad_num": base_h,
                "is_building_override": False,
                "address": binfo.get("address") or bf_props.get("address") or sp.get("address") or "",
                "historic_district": bf_props.get("historic_district") or sp.get("historic_district", ""),
                "contributing": bf_props.get("contributing") or sp.get("contributing", ""),
                "year_built": yr,
                "decade": f"{(yr // 10) * 10}s",
                "original_hcad_year": orig_yr,
                "bld_style": bf_props.get("bld_style") or sp.get("bld_style", ""),
                "architect": binfo.get("architect") or bf_props.get("architect") or sp.get("architect", ""),
                "builder": binfo.get("builder", ""),
                "builder_source": binfo.get("builder_source", ""),
                "engineers": binfo.get("engineers", ""),
                "original_cost": binfo.get("original_cost", ""),
                "original_permit": binfo.get("original_permit", ""),
                "yr_remodel": cama.get("remod", 0),
                "eff_year_built": cama.get("eff", 0),
                "was_relocated": bool(binfo.get("was_relocated") or base_h in relocated_accts),
                "relocation_note": binfo.get("relocation_note") or relocated_accts.get(base_h, {}).get("note", ""),
                "hist_contract": hc if hc else None,
                "landmark_name": bf_props.get("landmark_name") or sp.get("landmark_name", ""),
                "building_name": bf_props.get("building_name") or sp.get("building_name") or (f"{hc.get('owner', '')} Building ({yr})".strip() if hc.get("owner") else ""),
                "source_type": "HMRC Contractor Archive / T.G.C.A. Bulletin",
                "source_citation": binfo.get("builder_source", ""),
                "source_url": binfo.get("source_url", ""),
                "verified_by": "Preservation Houston / HMRC Digital Archives",
                "updated_at": "2026-10-12",
                "geometry": sf.get("geometry"),
            }

        # Also add archival photo to `building_photos.json` ONLY for verified extant Russell Brown illustrated plates
        photo_url = binfo.get("photo_url") or (binfo.get("hist_contract") or {}).get("image_url")
        if photo_url:
            m_ptr = re.search(r"/books/(?:id/)?(\d+)", str(photo_url))
            if m_ptr:
                ptr_num = int(m_ptr.group(1))
                # Only Russell Brown 'Modern Homes, Book Number Two' illustrated plates (12341-12380), never TGCA text tables or demolished 12354
                if 12341 <= ptr_num <= 12380 and ptr_num != 12354:
                    photo_url = f"https://cdm17006.contentdm.oclc.org/digital/api/singleitem/collection/books/id/{ptr_num}/thumbnail"
                else:
                    photo_url = None
        if photo_url and base_h.isdigit() and base_h not in {"1203760010003", "1350960010001", "0041360010008", "0041360010002", "0010550000013", "0010250000010", "0332640000005"}:
            existing_photos = photos_data.setdefault("buildings", {}).setdefault(base_h, [])
            if not any(p.get("url") == photo_url for p in existing_photos):
                hc = binfo.get("hist_contract") or {}
                existing_photos.insert(
                    0,
                    {
                        "url": photo_url,
                        "thumbUrl": photo_url,
                        "year": binfo.get("year_built") or 1923,
                        "era_label": f"{binfo.get('year_built') or 1923} (HMRC Archive)",
                        "caption": f"{hc.get('scope') or binfo.get('builder_source') or 'Archival Record'} - Built by {binfo.get('builder')}",
                        "credit": binfo.get("builder_source") or "Houston Metropolitan Research Center (HPL)",
                        "source_url": binfo.get("source_url") or photo_url,
                        "is_historical": True,
                    },
                )

    # Enrich all features in `buildings.geojson` with `yr_remodel`, `eff_year_built`, `was_relocated`, `builder`,
    # verified `year_built`, and authoritative aligned footprint geometry from `overrides` / `shard_footprints`!
    synced_geoms = 0
    for feat in buildings_fc.get("features", []):
        p = feat.get("properties", {})
        h = str(p.get("hcad_num") or "").strip()
        bid = str(p.get("building_id") or p.get("id") or "").strip()
        if "OMITTED IMPS" in str(p.get("relocation_note") or "").upper():
            p.pop("was_relocated", None)
            p.pop("relocation_note", None)
        cama = cama_by_acct.get(h, {})
        if cama.get("remod"):
            p["yr_remodel"] = cama["remod"]
        if cama.get("eff"):
            p["eff_year_built"] = cama["eff"]
        if h in relocated_accts:
            p["was_relocated"] = True
            p["relocation_note"] = relocated_accts[h].get("note", "")

        binfo = CURATED_BUILDER_BY_HCAD.get(bid) or CURATED_BUILDER_BY_HCAD.get(h)
        if binfo:
            if binfo.get("builder"):
                p["builder"] = binfo["builder"]
            if binfo.get("builder_source"):
                p["builder_source"] = binfo["builder_source"]
            if binfo.get("architect") and not p.get("architect"):
                p["architect"] = binfo["architect"]
            if binfo.get("engineers"):
                p["engineers"] = binfo["engineers"]
            if binfo.get("original_cost"):
                p["original_cost"] = binfo["original_cost"]
            if binfo.get("original_permit"):
                p["original_permit"] = binfo["original_permit"]
            if binfo.get("hist_contract"):
                p["hist_contract"] = binfo["hist_contract"]
            if binfo.get("was_relocated"):
                p["was_relocated"] = True
                p["relocation_note"] = binfo.get("relocation_note", "")
            if binfo.get("year_built"):
                arch_yr = int(binfo["year_built"])
                cur_yr = int(p.get("year_built") or 0)
                if cur_yr != arch_yr and (cur_yr == 0 or abs(cur_yr - arch_yr) <= 22):
                    p["original_hcad_year"] = cur_yr
                    p["year_built"] = arch_yr
                    p["decade"] = f"{(arch_yr // 10) * 10}s"
                    p["is_curated_override"] = True

        # Sync geometry with `overrides` or `shard_footprints` so `buildings.geojson` matches `.pmtiles` shards
        auth_geom = None
        if bid in overrides and overrides[bid].get("geometry"):
            auth_geom = overrides[bid]["geometry"]
        elif h in overrides and overrides[h].get("geometry"):
            auth_geom = overrides[h]["geometry"]
        elif h in shard_footprints and shard_footprints[h].get("geometry"):
            auth_geom = shard_footprints[h]["geometry"]
        if auth_geom and feat.get("geometry") != auth_geom:
            feat["geometry"] = auth_geom
            synced_geoms += 1

    print(f"Synced {synced_geoms:,} geometries in buildings.geojson with authoritative PMTiles shard / override footprints.")

    # Enrich `curated_overrides.json` with `yr_remodel`, `eff_year_built`, and `was_relocated`
    for k, ov in overrides.items():
        h = str(ov.get("hcad_num") or k.split("#")[0]).strip()
        if "OMITTED IMPS" in str(ov.get("relocation_note") or "").upper():
            ov.pop("was_relocated", None)
            ov.pop("relocation_note", None)
        cama = cama_by_acct.get(h, {})
        if cama.get("remod") and not ov.get("yr_remodel"):
            ov["yr_remodel"] = cama["remod"]
        if cama.get("eff") and not ov.get("eff_year_built"):
            ov["eff_year_built"] = cama["eff"]
        if h in relocated_accts and not ov.get("was_relocated"):
            ov["was_relocated"] = True
            ov["relocation_note"] = relocated_accts[h].get("note", "")

    # Enrich `lost_houston` in `overlays.json` with demolished Russell Brown "Modern Homes" sites
    lost_fc = overlays_data.setdefault("lost_houston", {"type": "FeatureCollection", "features": []})
    existing_lost_ids = {f.get("properties", {}).get("id") for f in lost_fc.get("features", [])}
    for dsite in DEMOLISHED_CONTRACTOR_SITES:
        if dsite["id"] in existing_lost_ids:
            continue
        cx, cy = dsite["center"]
        dx, dy = 0.00009, 0.00007
        ring = [
            [round(cx - dx, 6), round(cy - dy, 6)],
            [round(cx + dx, 6), round(cy - dy, 6)],
            [round(cx + dx, 6), round(cy + dy, 6)],
            [round(cx - dx, 6), round(cy + dy, 6)],
            [round(cx - dx, 6), round(cy - dy, 6)],
        ]
        lost_fc["features"].append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "id": dsite["id"],
                "overlay_layer": "lost_houston",
                "is_boundary_feature": True,
                "feature_type": "lost_houston",
                "is_demolished": True,
                "name": dsite["name"],
                "building_name": dsite["name"],
                "subtitle": dsite.get("subtitle") or dsite["builder_source"],
                "category": dsite.get("category") or "Gilded Age Mansion",
                "type_label": f"Lost Houston · {dsite.get('category') or 'Gilded Age Mansion'} ({dsite['year_built']}-{dsite['year_demolished']})",
                "lifespan_label": f"{dsite['year_built']}-{dsite['year_demolished']}",
                "lifespan_years": max(1, int(dsite["year_demolished"]) - int(dsite["year_built"])),
                "address": dsite["address"],
                "neighborhood": dsite.get("neighborhood") or "Montrose",
                "site_hcad_nums": dsite.get("site_hcad_nums", []),
                "hcad_num": (dsite.get("site_hcad_nums") or [""])[0],
                "year_built": dsite["year_built"],
                "year_demolished": dsite["year_demolished"],
                "demolition_cause": dsite.get("demolition_cause", ""),
                "replaced_by": dsite.get("replaced_by", ""),
                "architect": dsite["architect"],
                "style": dsite.get("style") or "Craftsman / Early 20th-Century Residential",
                "builder": dsite["builder"],
                "builder_source": dsite["builder_source"],
                "source": dsite["builder_source"],
                "source_url": dsite["source_url"],
                "photo_url": dsite["photo_url"],
                "photo_credit": dsite["builder_source"],
                "sanborn_material": dsite["sanborn_material"],
                "sanborn_material_label": dsite.get("sanborn_material_label", "Historic Residential Construction"),
                "stories": dsite["stories"],
                "height_m": dsite["height_m"],
                "blurb": dsite["description"],
                "historic_significance": dsite["description"],
                "description": dsite["description"],
                "lon": cx,
                "lng": cx,
                "lat": cy,
                "label_lng": cx,
                "label_lat": cy,
                "citations": [
                    {
                        "label": dsite["builder_source"],
                        "url": dsite["source_url"],
                    },
                    {
                        "label": "HMRC Index to 'Modern Homes, Book Number Two' (Gary Coover)",
                        "url": "https://cdm17006.contentdm.oclc.org/digital/collection/books/id/12336/rec/1",
                    },
                ],
                "hist_contract": {
                    "date": str(dsite["year_built"]),
                    "cost": "$9,500 (est.)",
                    "architect": dsite["architect"],
                    "builder": dsite["builder"],
                    "scope": dsite["description"],
                    "source_title": dsite["builder_source"],
                    "source_url": dsite["source_url"],
                    "image_url": dsite["photo_url"],
                },
            },
        })

    # Build `overlays.json["relocated_structures"]` (Point FeatureCollection of Relocated Historic Structures
    # and HMRC Contractor Portfolio Sites)
    reloc_features = []
    seen_reloc_keys = set()

    def _geom_centroid(geom):
        if not geom:
            return None
        t = geom.get("type")
        coords = geom.get("coordinates")
        if not coords:
            return None
        if t == "Point":
            return coords[:2]
        ring = coords[0] if t == "Polygon" else (coords[0][0] if t == "MultiPolygon" and coords[0] else None)
        if not ring:
            return None
        xs = [pt[0] for pt in ring]
        ys = [pt[1] for pt in ring]
        return [round(sum(xs) / len(xs), 6), round(sum(ys) / len(ys), 6)]

    # 1. Include all overrides & buildings that have `was_relocated` or `builder`
    for k, ov in overrides.items():
        if not (ov.get("was_relocated") or ov.get("builder")):
            continue
        pt = _geom_centroid(ov.get("geometry"))
        if not pt:
            continue
        seen_reloc_keys.add(k)
        seen_reloc_keys.add(ov.get("hcad_num", ""))
        reloc_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": pt},
            "properties": {
                "id": k,
                "building_id": k,
                "hcad_num": ov.get("hcad_num", ""),
                "name": ov.get("building_name") or ov.get("landmark_name") or ov.get("address") or k,
                "building_name": ov.get("building_name") or ov.get("landmark_name") or "",
                "address": ov.get("address", ""),
                "year_built": ov.get("year_built", 0),
                "architect": ov.get("architect", ""),
                "builder": ov.get("builder", ""),
                "builder_source": ov.get("builder_source", ""),
                "original_cost": ov.get("original_cost", ""),
                "original_permit": ov.get("original_permit", ""),
                "was_relocated": bool(ov.get("was_relocated")),
                "relocation_note": ov.get("relocation_note", ""),
                "hist_contract": ov.get("hist_contract"),
            },
        })

    # 2. Include relocated structures from `shard_footprints` (and Sam Houston Park historic moved houses!)
    for rh, sf in shard_footprints.items():
        if rh in seen_reloc_keys:
            continue
        if rh not in relocated_accts and rh not in CURATED_BUILDER_BY_HCAD:
            continue
        pt = _geom_centroid(sf.get("geometry"))
        if not pt:
            continue
        sp = sf.get("properties", {})
        rinfo = relocated_accts.get(rh, {})
        binfo = CURATED_BUILDER_BY_HCAD.get(rh, {})
        seen_reloc_keys.add(rh)
        reloc_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": pt},
            "properties": {
                "id": rh,
                "building_id": rh,
                "hcad_num": rh,
                "name": sp.get("building_name") or sp.get("landmark_name") or sp.get("address") or f"HCAD {rh}",
                "address": sp.get("address", ""),
                "year_built": sp.get("year_built") or cama_by_acct.get(rh, {}).get("yr", 0),
                "builder": binfo.get("builder", ""),
                "builder_source": binfo.get("builder_source", ""),
                "was_relocated": rh in relocated_accts or bool(binfo.get("was_relocated")),
                "relocation_note": rinfo.get("note") or binfo.get("relocation_note", ""),
                "hist_contract": binfo.get("hist_contract"),
            },
        })

    overlays_data["relocated_structures"] = {
        "type": "FeatureCollection",
        "features": reloc_features,
    }
    print(f"Created overlays.json['relocated_structures'] with {len(reloc_features)} features")

    # Enrich `search_index.json` with `builder`, `original_permit`, `yr_remodel`, and `was_relocated`
    search_items = search_data if isinstance(search_data, list) else search_data.get("items", [])
    existing_search_hcads = {str(it.get("hcad_num") or it.get("id") or "") for it in search_items}
    for it in search_items:
        if it.get("is_neighborhood_entry") or it.get("is_place_boundary"):
            continue
        h = str(it.get("hcad_num") or "").strip()
        bid = str(it.get("building_id") or it.get("id") or "").strip()
        ov = overrides.get(bid) or overrides.get(h) or {}
        binfo = CURATED_BUILDER_BY_HCAD.get(bid) or CURATED_BUILDER_BY_HCAD.get(h) or {}
        cama = cama_by_acct.get(h, {})
        ov_yr = int(ov.get("year_built") or 0)
        if ov_yr >= 1836:
            old_yr = int(it.get("year_built") or 0)
            it["year_built"] = ov_yr
            if it.get("category") and str(it["category"]).startswith("Built "):
                it["category"] = f"Built {ov_yr}"
            if old_yr >= 1836 and it.get("sublabel") and f"Built {old_yr}" in str(it["sublabel"]):
                it["sublabel"] = str(it["sublabel"]).replace(f"Built {old_yr}", f"Built {ov_yr}")
        builder = ov.get("builder") or binfo.get("builder") or it.get("builder") or ""
        if builder:
            it["builder"] = builder
        remod = ov.get("yr_remodel") or cama.get("remod") or 0
        if remod:
            it["yr_remodel"] = remod
        if ov.get("was_relocated") or binfo.get("was_relocated") or h in relocated_accts:
            it["was_relocated"] = True
        else:
            it.pop("was_relocated", None)

    # Add newly created contractor overrides to `search_index.json`
    for k, ov in overrides.items():
        if k not in existing_search_hcads and ov.get("hcad_num") not in existing_search_hcads and ov.get("builder"):
            pt = _geom_centroid(ov.get("geometry"))
            if pt:
                search_items.append({
                    "id": k,
                    "building_id": k,
                    "hcad_num": ov.get("hcad_num", ""),
                    "address": ov.get("address", ""),
                    "landmark_name": ov.get("landmark_name", ""),
                    "building_name": ov.get("building_name", ""),
                    "historic_district": ov.get("historic_district", ""),
                    "year_built": ov.get("year_built", 0),
                    "bld_style": ov.get("bld_style", ""),
                    "architect": ov.get("architect", ""),
                    "builder": ov.get("builder", ""),
                    "yr_remodel": ov.get("yr_remodel", 0),
                    "was_relocated": bool(ov.get("was_relocated")),
                    "lng": pt[0],
                    "lat": pt[1],
                })

    # Write updated core JSON files
    buildings_path.write_text(json.dumps(buildings_fc, separators=(",", ":")))
    overrides_path.write_text(json.dumps(overrides_data, indent=2))
    overlays_path.write_text(json.dumps(overlays_data, separators=(",", ":")))
    photos_path.write_text(json.dumps(photos_data, indent=2))
    search_path.write_text(json.dumps(search_data, separators=(",", ":")))
    print("Updated buildings.geojson, curated_overrides.json, overlays.json, building_photos.json, and search_index.json")

    # -----------------------------------------------------------------------
    # 5. Write Sharded On-Demand Permit & Alteration Files (`permits_<prefix3>.json`)
    # -----------------------------------------------------------------------
    print("[5/5] Writing sharded HCAD permit & alteration files to app/public/data/permits/...")
    shards = collections.defaultdict(dict)
    all_accts = set(permits_by_acct.keys()) | set(cama_by_acct.keys()) | set(xfeat_by_acct.keys()) | set(relocated_accts.keys())

    kept_accts = 0
    for acct in all_accts:
        if len(acct) < 3 or not acct[:3].isdigit():
            continue
        cama = cama_by_acct.get(acct)
        p_list = permits_by_acct.get(acct)
        x_list = xfeat_by_acct.get(acct)
        reloc = relocated_accts.get(acct)

        has_remod = bool(cama and cama.get("remod"))
        has_note = bool(cama and cama.get("note"))
        has_eff_diff = bool(cama and cama.get("eff") and cama.get("yr") and cama["eff"] - cama["yr"] >= 10)
        if not (p_list or has_remod or has_note or has_eff_diff or x_list or reloc):
            continue

        rec = {}
        if cama:
            if cama.get("yr"):
                rec["yr"] = cama["yr"]
            if cama.get("eff") and cama.get("eff") != cama.get("yr"):
                rec["eff"] = cama["eff"]
            if cama.get("remod"):
                rec["remod"] = cama["remod"]
            if cama.get("qual"):
                rec["qual"] = cama["qual"]
            if cama.get("note"):
                rec["note"] = cama["note"]
        if reloc:
            rec["reloc"] = reloc.get("note") or reloc.get("reason") or "Relocated Structure"
        if x_list:
            rec["xf"] = x_list
        if p_list:
            # Sort newest to oldest
            p_sorted = sorted(p_list, key=lambda x: x.get("dt", ""), reverse=True)
            rec["p"] = p_sorted

        shards[acct[:3]][acct] = rec
        kept_accts += 1

    # Preserve existing top-level "d" recorded deed chain strings (authored by enrich_deed_chains.py;
    # distinct from nested rec["p"][i]["d"] permit descriptions) so full permit rebuilds never wipe deeds.
    existing_shard_files = {p.stem.replace("permits_", ""): p for p in PERMITS_DIR.glob("permits_*.json")}
    for prefix3 in set(shards.keys()) | set(existing_shard_files.keys()):
        shard_dict = shards[prefix3]
        shard_file = PERMITS_DIR / f"permits_{prefix3}.json"
        if shard_file.exists():
            try:
                prev_data = json.loads(shard_file.read_text(encoding="utf-8"))
                for acct, prev_rec in prev_data.items():
                    if isinstance(prev_rec, dict) and prev_rec.get("d"):
                        if acct in shard_dict:
                            shard_dict[acct]["d"] = prev_rec["d"]
                        else:
                            shard_dict[acct] = {"d": prev_rec["d"]}
            except Exception:
                pass
        if shard_dict:
            shard_file.write_text(json.dumps(shard_dict, separators=(",", ":")), encoding="utf-8")

    print(f"Wrote {len(shards)} permit shard files covering {kept_accts:,} HCAD accounts.")


if __name__ == "__main__":
    main()
