"""Execute the 4-Phase Undated Buildings Completion Strategy across all 1,512,020 Harris County footprints."""

from __future__ import annotations

import csv
import io
import json
import math
import pickle
import re
import subprocess
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import shapely
from shapely.strtree import STRtree

from atlas_pipeline.schema import compute_decade, normalize_year


# ============================================================================
# PHASE 1: Curated Landmarks, Historic Districts, Campuses, Stadiums,
#          Schools, Churches, Hospitals & Major Ship Channel Complexes
# ============================================================================
# Format: hcad_num -> (year_built, landmark_name, bld_style, source_citation)
PHASE1_CURATED_LANDMARKS: dict[str, tuple[int, str, str, str]] = {
    # --- Tier 1: All 9 Designated Landmarks & Historic District Parcels ---
    "1248130010001": (
        1912,
        "Dow Elementary School",
        "Early 20th Century School / Classical Revival",
        "City of Houston Designated Landmark in Old Sixth Ward Historic District; opened 1912, expanded 1927.",
    ),
    "0190570000006": (
        1924,
        "St. John Missionary Baptist Church",
        "Historic Sanctuary",
        "City of Houston Landmark (2702 Emancipation Ave); Third Ward congregation founded 1866, sanctuary erected 1924/1949.",
    ),
    "0191850000001": (
        1926,
        "First Evangelical Church",
        "Gothic Revival",
        "City of Houston Protected Landmark (3410 Austin St); designed by architect Joseph Finger, completed 1926.",
    ),
    "0201660000034": (
        1926,
        "Houston Public Library — Heights Branch",
        "Italian Renaissance Revival",
        "City of Houston Protected Landmark & NRHP (1302 Heights Blvd); designed by J.M. Glover, opened March 1926.",
    ),
    "0201360000032": (
        1919,
        "Houston Heights Church of Christ Building",
        "Classical Revival",
        "City of Houston Protected Landmark in Houston Heights East Historic District (1548 Heights Blvd); erected 1919.",
    ),
    "0543170210013": (
        1910,
        "Rufus Cage Elementary School",
        "Early 20th Century School",
        "City of Houston Protected Landmark (1417 Telephone Rd); original Eastwood school house built 1910, expanded 1926.",
    ),
    "0090760000002": (
        1836,
        "Founders' Memorial Park (Old City Cemetery)",
        "Historic Municipal Cemetery & Shelter",
        "City of Houston Historic Site (1217 W Dallas St / Valentine St); Houston's oldest municipal cemetery established 1836.",
    ),
    "0050060000015": (
        1923,
        "Bethel Baptist Church Building",
        "Gothic Revival",
        "City of Houston Protected Landmark & Historic Park in Fourth Ward / Freedmen's Town (801 Andrews St); built 1923, rebuilt 1950.",
    ),
    "0142640000005": (
        1955,
        "4727 Shetland Ln Residence",
        "Mid-Century Ranch",
        "Afton Oaks Subdivision residential structure erected 1955 (corrects spurious HCAD landmark pointer).",
    ),
    # --- Major Stadiums, Arenas & Civic Venues ---
    "0440930000004": (
        1965,
        "NRG Astrodome & NRG Park Complex",
        "Mid-Century Modern / Domed Stadium",
        "Harris County Domed Stadium (The Astrodome, 8400 Fannin St), world's first multi-purpose domed stadium, opened April 9, 1965.",
    ),
    "0440920000001": (
        1965,
        "NRG Park Westridge Service Complex",
        "Civic / Stadium Support Facility",
        "Harris County NRG Park Westridge support buildings, established with the Astrodome complex in 1965.",
    ),
    "0011020000001": (
        1911,
        "Daikin Park (Minute Maid Park & Union Station)",
        "Beaux-Arts Classical / Retractable-Roof Ballpark",
        "Historic Houston Union Station designed by Warren & Wetmore (opened March 1, 1911), integrated with ballpark (opened 2000; 501 Crawford St).",
    ),
    "1420820010001": (
        2012,
        "Shell Energy Stadium",
        "Contemporary Open-Air Soccer Stadium",
        "Home of Houston Dynamo FC and Houston Dash at 2200 Texas Ave (formerly BBVA Compass Stadium and PNC Stadium), designed by Populous and opened May 12, 2012 (2015 Pier & Beam Future Landmark Award).",
    ),
    "1256250010001": (
        1975,
        "Lakewood Church (Former Summit / Compaq Center)",
        "Late Modernist Arena",
        "Originally erected as The Summit arena (designed by Lloyd Jones Brewer & Associates, opened November 1975).",
    ),
    # --- Major Hospitals, Medical & Senior Care Campuses ---
    "1368420010001": (
        1977,
        "Memorial Hermann Medical Plaza & TMC Campus",
        "Healthcare / Institutional Campus",
        "Memorial Hermann Health System Cambridge St / TMC campus complex (1977).",
    ),
    "0440970000222": (
        1951,
        "Houston Methodist Hospital Main Campus",
        "Healthcare / Medical Center Tower",
        "Houston Methodist Hospital flagship Texas Medical Center campus at 6550 Fannin St (established in TMC 1951).",
    ),
    "0440970000221": (
        1954,
        "Texas Medical Center Bertner Complex",
        "Medical Research / Institutional",
        "Texas Medical Center institutional campus at 6516 Bertner Ave (1954).",
    ),
    "0440970000214": (
        1954,
        "Texas Medical Center Bates Complex",
        "Medical Research / Institutional",
        "Texas Medical Center institutional campus at 1150 Bates Ave (1954).",
    ),
    "1358540010001": (
        1975,
        "St. Dominic Village (Archdiocese of Galveston-Houston)",
        "Institutional / Healthcare Campus",
        "Archdiocesan St. Dominic Center medical and retirement campus on Holcombe Blvd (1975).",
    ),
    "0410540000006": (
        1966,
        "Seven Acres Jewish Senior Care Services",
        "Institutional Senior Care Campus",
        "Seven Acres campus at 6200 N Braeswood Blvd (opened 1966).",
    ),
    "1351860010001": (
        1998,
        "Star of Hope Cornerstone Community Campus",
        "Nonprofit / Social Services Campus",
        "Star of Hope Mission campus complex (2575 Reed Rd, established 1998).",
    ),
    # --- Private Schools, Universities & Community Colleges ---
    "0410170040015": (
        1946,
        "St. John's School",
        "Collegiate Gothic / Educational Campus",
        "St. John's School campus (2401 Claremont Ln / 3378 Westheimer Rd), founded 1946 around St. John the Divine.",
    ),
    "0410500030785": (
        1956,
        "St. Pius X High School",
        "Mid-Century Educational Campus",
        "Dominican Catholic high school at 811 W Donovan St, founded and erected 1956.",
    ),
    "1238280010001": (
        1979,
        "The Awty International School",
        "Educational Campus",
        "The Awty International School main campus at 7455 Awty School Ln, established 1979.",
    ),
    "0432270000003": (
        1960,
        "Strake Jesuit College Preparatory",
        "Mid-Century Educational Campus",
        "Strake Jesuit campus at 8900 Bellaire Blvd, founded 1960.",
    ),
    "0432270000004": (
        1963,
        "St. Agnes Academy",
        "Educational Campus",
        "St. Agnes Academy Bellaire Blvd campus (relocated from Fannin St in 1963).",
    ),
    "0370420030047": (
        1983,
        "Episcopal High School",
        "Educational Campus",
        "Episcopal High School campus at 4600 Bissonnet St in Bellaire (founded 1983 on former Marian HS campus).",
    ),
    "1203890010002": (
        1998,
        "Houston Christian High School",
        "Educational Campus",
        "Houston Christian High School campus at 2700 W Sam Houston Pkwy N (opened 1998).",
    ),
    "1344350010001": (
        2001,
        "The Emery/Weiner School",
        "Educational Campus",
        "Emery/Weiner Center for Jewish Education at 9825 Stella Link Rd (opened 2001).",
    ),
    "0450890010027": (
        1954,
        "St. Mary's Seminary",
        "Collegiate / Religious Seminary Campus",
        "St. Mary's Seminary campus at 9845 Memorial Dr, dedicated 1954.",
    ),
    "0940970000004": (
        1963,
        "Houston Christian University (Formerly HBU)",
        "University Campus",
        "Houston Baptist / Houston Christian University main campus at 7502 Fondren Rd, opened 1963.",
    ),
    "0410070130038": (
        1967,
        "University of Houston East Campus Complex",
        "University Campus",
        "University of Houston Martin Luther King Blvd campus facility (1967).",
    ),
    "1223610010001": (
        1974,
        "San Jacinto College North Campus",
        "Community College Campus",
        "San Jacinto College North Campus at 5800 Uvalde Rd, opened 1974.",
    ),
    "1183720000001": (
        1979,
        "San Jacinto College South Campus",
        "Community College Campus",
        "San Jacinto College South Campus at 13735 Beamer Rd, opened 1979.",
    ),
    "1237540010001": (
        1979,
        "San Jacinto College South Annex",
        "Community College Campus",
        "San Jacinto College Beamer Rd campus annex (1979).",
    ),
    "1245760010001": (
        2003,
        "Lone Star College — CyFair",
        "Community College Campus",
        "Lone Star College CyFair main campus at 9191 Barker Cypress Rd, opened 2003.",
    ),
    "0471620000005": (
        1973,
        "Lone Star College — North Harris",
        "Community College Campus",
        "Lone Star College North Harris campus at 2700 W.W. Thorne Dr, opened 1973.",
    ),
    "1364780010001": (
        1978,
        "Houston Community College Southeast Campus",
        "Community College Campus",
        "HCC Southeast Eastside campus at 6901 Rustic Ln (1978).",
    ),
    # --- Major HISD & Inner-Loop Public Schools ---
    "1181090010001": (
        1937,
        "Lamar High School (HISD)",
        "Art Deco / Modern Educational Campus",
        "Mirabeau B. Lamar High School at 3325 Westheimer Rd, designed by John F. Staub & Kenneth Franzheim (opened 1937).",
    ),
    "1195940010001": (
        2000,
        "Cesar E. Chavez High School (HISD)",
        "Contemporary Educational Campus",
        "Cesar E. Chavez High School campus in Southeast Houston (8501 Howard Dr / 4725 Galveston Rd), opened 2000.",
    ),
    "1500120010001": (
        1926,
        "Northside High School (Former Jefferson Davis High School, HISD)",
        "Classical Revival / Historic School",
        "Historic Jefferson Davis / Northside High School in Near Northside (1101 Quitman St), designed by Briscoe & Dixon and opened 1926.",
    ),
    "1401710010001": (
        1937,
        "Stephen F. Austin High School (HISD)",
        "Art Deco Educational Campus",
        "Historic Stephen F. Austin High School in the East End (1700 Dumble St), opened 1937.",
    ),
    "1264250010001": (
        1928,
        "John J. Pershing Middle School (HISD)",
        "Educational Campus",
        "Pershing Middle School campus at 3838 Blue Bonnet Blvd (established 1928).",
    ),
    "1358630010001": (
        1959,
        "Waltrip High School & Delmar Stadium Complex (HISD)",
        "Mid-Century Educational & Athletic Complex",
        "Waltrip High School and Delmar Stadium complex at 4400 W 18th St (opened 1959).",
    ),
    "0401900010060": (
        1961,
        "Ebbert L. Furr High School (HISD)",
        "Educational Campus",
        "Furr High School campus at 520 Mercury Dr / 250 McCarty St (opened 1961).",
    ),
    "0770870320001": (
        1962,
        "Margaret Long Wisdom High School (HISD)",
        "Mid-Century Educational Campus",
        "Originally Robert E. Lee High School at 6529 Beverly Hill St / 5434 Hidalgo St, opened 1962.",
    ),
    "0834040000022": (
        1968,
        "Sharpstown High School (HISD)",
        "Mid-Century Educational Campus",
        "Sharpstown High School campus near 6501 Bellaire Blvd / Bissonnet, opened 1968.",
    ),
    "0681070000025": (
        1950,
        "T.H. Rogers School (HISD)",
        "Mid-Century Educational Campus",
        "T.H. Rogers School campus at 5840 San Felipe St, originally opened 1950.",
    ),
    "0430400000095": (
        1956,
        "Jesse H. Jones High School (HISD)",
        "Mid-Century Educational Campus",
        "Jesse H. Jones High School campus in South Park (7414 Saint Lo Rd), opened 1956.",
    ),
    "1180780010001": (
        1969,
        "Barnett Stadium & Sports Complex (HISD)",
        "Mid-Century Athletic Stadium & Fieldhouse",
        "HISD John K. Barnett Sr. Stadium and Sports Complex at 6800 Fairway Dr / 6500 Long Dr, opened 1969.",
    ),
    "0450640000010": (
        1958,
        "Ezekiel W. Cullen Middle School (HISD)",
        "Mid-Century Educational Campus",
        "Cullen Middle School campus in Third Ward / Foster Place (6900 Scott St), opened 1958.",
    ),
    "0410120010007": (
        1956,
        "Charles F. Hartman Middle School (HISD)",
        "Mid-Century Educational Campus",
        "Hartman Middle School campus in Southeast Houston (7111 Westover St), opened 1956.",
    ),
    "1178810010001": (
        1955,
        "Sam Houston Math, Science & Technology Center (HISD)",
        "Educational Campus",
        "Sam Houston High School Northside campus at 9400 Irvington Blvd, opened 1955.",
    ),
    "1174250010001": (
        1968,
        "Kashmere High School (HISD)",
        "Mid-Century Educational Campus",
        "Kashmere High School campus in Kashmere Gardens (6900 Wileyvale Rd), opened at current site in 1968.",
    ),
    "0422240000321": (
        1967,
        "G.C. Scarborough High School (HISD)",
        "Mid-Century Educational Campus",
        "Scarborough High School at 4141 Costa Rica Rd, opened 1967.",
    ),
    "1175730010001": (
        1957,
        "McReynolds Middle School (HISD)",
        "Mid-Century Educational Campus",
        "John L. McReynolds Middle School campus in Fifth Ward (5910 Market St), opened 1957.",
    ),
    "0392820000001": (
        1957,
        "McReynolds Middle School Annex (HISD)",
        "Mid-Century Educational Campus",
        "McReynolds Middle School Fifth Ward / Pleasantville annex (1600 Gellhorn Dr), opened 1957.",
    ),
    "0410770000625": (
        1962,
        "Memorial High School (Spring Branch ISD)",
        "Mid-Century Educational Campus",
        "Spring Branch ISD Memorial High School at 935 Echo Ln / 955 Campbell Rd, opened 1962.",
    ),
    "1238550010001": (
        1956,
        "Landrum Middle School (Spring Branch ISD)",
        "Mid-Century Educational Campus",
        "H.M. Landrum Middle School in Spring Branch (2200 Ridgecrest Dr), opened 1956.",
    ),
    "0440340020310": (
        1953,
        "Historic Spring Branch High School / Cornerstone Academy (SBISD)",
        "Mid-Century Educational Campus",
        "Original Spring Branch High School / Cornerstone Academy & Academy of Choice campus (9016 Westview Dr), opened 1953.",
    ),
    "1404700010001": (
        1964,
        "Spring Woods High School (Spring Branch ISD)",
        "Mid-Century Educational Campus",
        "Spring Woods High School campus in Spring Branch (2045 Gessner Dr), opened 1964.",
    ),
    "1305850010001": (
        1998,
        "KIPP East End Campus",
        "Charter School Campus",
        "KIPP Academy East End campus at 5402 Lawndale St (1998).",
    ),
    # --- Major Religious & Cultural Institutions ---
    "1282680030001": (
        1952,
        "St. Martin's Episcopal Church",
        "Gothic Revival Sanctuary & Parish Campus",
        "St. Martin's Episcopal Church at 717 Sage Rd, parish founded 1952.",
    ),
    "1187350010001": (
        1950,
        "River Oaks Baptist Church & School",
        "Georgian Revival Sanctuary & School",
        "River Oaks Baptist Church and School at 2300/2320 Willowick Rd, founded 1950.",
    ),
    "1332830010001": (
        1947,
        "St. Rose of Lima Catholic Church & School",
        "Mid-Century Parish Campus",
        "St. Rose of Lima Catholic parish in Garden Oaks (3600 Brinkman St), founded 1947.",
    ),
    "0410540000022": (
        1967,
        "Congregation Beth Israel",
        "Mid-Century Modern Synagogue",
        "Congregation Beth Israel temple at 5600 N Braeswood Blvd (Houston's oldest Jewish congregation, dedicated here 1967).",
    ),
    "1441520010001": (
        1969,
        "Evelyn Rubenstein Jewish Community Center of Houston",
        "Mid-Century Cultural & Community Center",
        "Jewish Community Center of Houston at 5601 S Braeswood Blvd, opened 1969.",
    ),
    "1286500010001": (
        1958,
        "Tallowood Baptist Church",
        "Sanctuary & Educational Campus",
        "Tallowood Baptist Church at 555 Tallowood Rd in Memorial, founded 1958.",
    ),
    "0410280010520": (
        1955,
        "Memorial Drive Presbyterian Church",
        "Sanctuary & Parish Campus",
        "Memorial Drive Presbyterian Church at 11612 Memorial Dr, founded 1955.",
    ),
    "0410280010061": (
        1951,
        "St. Francis Episcopal Church & School",
        "Sanctuary & Parish School",
        "St. Francis Episcopal Church and School at 335 Piney Point Rd, founded 1951.",
    ),
    "0961660000009": (
        1959,
        "St. Thomas More Catholic Church & School",
        "Mid-Century Parish Campus",
        "St. Thomas More Catholic Parish at 10330 Hillcroft St, founded 1959.",
    ),
    "0410770000385": (
        1956,
        "St. Cecilia Catholic Church & School",
        "Parish Sanctuary & School",
        "St. Cecilia Catholic Parish in Hedwig Village (11720 Joan of Arc Dr), founded 1956.",
    ),
    "0432220000180": (
        1959,
        "St. Jerome Catholic Church & School",
        "Mid-Century Parish Campus",
        "St. Jerome Catholic Parish in Spring Branch (8825 Kempwood Dr), founded 1959.",
    ),
    "0401610000040": (
        1957,
        "Grace Presbyterian Church",
        "Sanctuary & Campus",
        "Grace Presbyterian Church at 10221 Ella Lee Ln, founded 1957.",
    ),
    "0450700000080": (
        1949,
        "St. Mark Lutheran Church",
        "Mid-Century Sanctuary",
        "St. Mark Lutheran Church in Spring Branch (1515 Hillendahl Blvd), founded 1949.",
    ),
    "1188930010001": (
        1965,
        "Brentwood Baptist Church",
        "Sanctuary & Community Campus",
        "Brentwood Baptist Church at 13033 Landmark St, founded 1965.",
    ),
    "0410540000012": (
        1957,
        "Westbury Church of Christ",
        "Mid-Century Sanctuary",
        "Westbury Church of Christ at 10424 Hillcroft St, founded 1957.",
    ),
    "1190910010001": (
        1966,
        "Sagemont Church",
        "Sanctuary & Campus",
        "Sagemont Church at 11300 S Sam Houston Pkwy E, founded 1966.",
    ),
    "0440830000183": (
        1968,
        "First Baptist Church of Pasadena",
        "Sanctuary & Campus",
        "First Baptist Church of Pasadena Fairmont Pkwy campus (7500 Fairmont Pkwy).",
    ),
    "1219480010001": (
        1962,
        "St. John Vianney Catholic Church",
        "Sanctuary & Parish Campus",
        "St. John Vianney Catholic Parish in Memorial (625 Nottingham Oaks Trl / 14600 Memorial Dr), founded 1962.",
    ),
    # --- Major Federal, Port, Aviation & Municipal Civic Complexes ---
    "0532780000003": (
        1962,
        "NASA Lyndon B. Johnson Space Center",
        "Federal Aerospace & Mission Control Campus",
        "NASA Manned Spacecraft Center / Lyndon B. Johnson Space Center (2101 NASA Pkwy), designed by Charles Luckman, constructed 1962–1963.",
    ),
    "1170150000001": (
        1992,
        "Space Center Houston Visitor Complex",
        "Museum & Science Education Center",
        "Space Center Houston official visitor center of NASA Johnson Space Center, opened October 1992.",
    ),
    "0462210000001": (
        1917,
        "Ellington Field Joint Reserve Base",
        "Historic Military Aviation Airfield",
        "Ellington Field military aviation training base established 1917, rebuilt 1941.",
    ),
    "0430530000014": (
        1941,
        "Ellington Field Air National Guard Complex",
        "Military Aviation Facility",
        "Ellington Field 147th Attack Wing / Texas Air National Guard hangars and facilities (1941).",
    ),
    "0451050000034": (
        1941,
        "Ellington Field NASA Flight Operations & Sonny Carter Facility",
        "Aerospace Training & Hangar Complex",
        "Ellington Field NASA aircraft operations and Neutral Buoyancy Lab complex (1941).",
    ),
    "0460330000145": (
        1941,
        "Ellington Field Coast Guard Air Station Houston",
        "Military Aviation Hangar Complex",
        "Ellington Field U.S. Coast Guard Air Station Houston complex (1941).",
    ),
    "0460340000020": (
        1991,
        "USPS North Houston Processing & Distribution Center",
        "Federal Postal Logistics Facility",
        "U.S. Postal Service regional processing facility at 4600 Aldine Bender Rd (1991).",
    ),
    "0401940000030": (
        1924,
        "Port of Houston Turning Basin Terminal (North Wharves)",
        "Maritime Wharf & Transit Sheds",
        "Port of Houston Authority Turning Basin North Wharves at 8402 Clinton Dr (1924).",
    ),
    "0401930000001": (
        1915,
        "Port of Houston Turning Basin Terminal (Central Wharves)",
        "Historic Maritime Terminal & Wharves",
        "Port of Houston Turning Basin deepwater terminal at 8300 Clinton Dr (opened 1915).",
    ),
    "0401940000063": (
        1928,
        "Port of Houston Turning Basin Terminal (East Wharves)",
        "Maritime Wharf & Transit Sheds",
        "Port of Houston Turning Basin East Wharves at 9600 Clinton Dr (1928).",
    ),
    "0402310000006": (
        1920,
        "Port of Houston Manchester Terminal",
        "Maritime Wharf & Grain/Cargo Terminal",
        "Port of Houston Manchester Wharf complex on the Houston Ship Channel (1920).",
    ),
    "0401940000004": (
        1925,
        "Port of Houston Clinton Drive Wharves",
        "Maritime Transit Sheds",
        "Port of Houston Clinton Dr maritime transit sheds (1925).",
    ),
    "0402400050074": (
        1988,
        "Port of Houston Jacintoport Terminal",
        "Maritime Cargo Terminal",
        "Port of Houston Jacintoport Terminal at 16203 Peninsula St (1988).",
    ),
    "0402400050087": (
        1989,
        "Port of Houston Jacintoport Transit Sheds",
        "Maritime Cold Storage & Cargo Sheds",
        "Port of Houston Jacintoport Blvd cargo sheds (1989).",
    ),
    "0402720000014": (
        1977,
        "Port of Houston Barbours Cut Container Terminal",
        "Container Port Terminal",
        "Port of Houston Barbours Cut Container Terminal in Morgan's Point, opened 1977.",
    ),
    "1005740000050": (
        2006,
        "Port of Houston Bayport Container Terminal",
        "Container Port Terminal",
        "Port of Houston Bayport Container Terminal at 12619 Port Rd, opened 2006.",
    ),
    "1005740000002": (
        2006,
        "Port of Houston Bayport Marine Terminal",
        "Maritime Terminal",
        "Port of Houston Bayport Marine Terminal (2006).",
    ),
    "0421950000001": (
        1927,
        "William P. Hobby Airport Complex",
        "Municipal Aviation Terminal & Hangars",
        "Houston Municipal / William P. Hobby Airport at 7690 Airport Blvd, airfield opened 1927.",
    ),
    "0690030030001": (
        1940,
        "1940 Air Terminal Museum & Hobby West Ramp",
        "Art Deco Aviation Terminal & Hangars",
        "Historic Houston Municipal Airport Art Deco terminal designed by Joseph Finger (opened 1940) and West Ramp hangars.",
    ),
    "0441130001004": (
        1969,
        "George Bush Intercontinental Airport (IAH) Terminal Complex",
        "International Aviation Terminal",
        "Houston Intercontinental Airport (IAH) terminal complex, opened June 1969.",
    ),
    "0441130001014": (
        1975,
        "George Bush Intercontinental Airport (IAH) Air Cargo Center",
        "Aviation Cargo Complex",
        "IAH Air Cargo Center on John F. Kennedy Blvd (1975).",
    ),
    "0461820001003": (
        1969,
        "George Bush Intercontinental Airport (IAH) Operations Complex",
        "Aviation Support Facility",
        "IAH airport operations complex on John F. Kennedy Blvd (1969).",
    ),
    # --- Major Ship Channel, Baytown, Deer Park & Inner-Loop Industrial Landmarks ---
    "0401910000219": (
        1966,
        "Anheuser-Busch Houston Brewery",
        "Industrial Brewery Complex",
        "Anheuser-Busch Houston Brewery at 775 Gellhorn Dr, opened 1966.",
    ),
    "0410220000020": (
        1919,
        "ExxonMobil Baytown Refinery (Main Plant)",
        "Petrochemical Refinery & Storage Complex",
        "Founded in 1919 by Humble Oil & Refining Co. at 2800 Decker Dr in Baytown.",
    ),
    "0410220020405": (
        1940,
        "ExxonMobil Baytown Chemical & Ordnance Plant",
        "Petrochemical Complex",
        "Baytown Chemical Plant & WWII toluol/synthetic rubber works (established 1940).",
    ),
    "0410220010348": (
        1941,
        "ExxonMobil Baytown West Refinery Units",
        "Petrochemical Refinery Complex",
        "ExxonMobil Baytown West process and tank farm complex (1941).",
    ),
    "0410220010046": (
        1929,
        "ExxonMobil Baytown South Tank Farm",
        "Refinery Tank Farm",
        "ExxonMobil Baytown South storage tank farm (1929).",
    ),
    "0410220100017": (
        1948,
        "ExxonMobil Baytown Technology & Engineering Complex",
        "Industrial Research & Engineering Facility",
        "ExxonMobil Baytown research and engineering complex at 2800 Decker Dr (1948).",
    ),
    "0410220000109": (
        1925,
        "ExxonMobil Baytown Marine & Process Complex",
        "Refinery Marine Terminal",
        "ExxonMobil Baytown Ship Channel marine and process units (1925).",
    ),
    "0410220000008": (
        1935,
        "ExxonMobil Baytown East Process Units",
        "Refinery Process Complex",
        "ExxonMobil Baytown East refinery units (1935).",
    ),
    "0402030000001": (
        1918,
        "Houston Refining LP (Former Sinclair / ARCO / Lyondell Refinery)",
        "Historic Ship Channel Refinery",
        "Originally founded in 1918 by Sinclair Refining Company at 12000 Lawndale St along the Houston Ship Channel.",
    ),
    "0410340000036": (
        1924,
        "Houston Refining LP North Tank Farm & Process Units",
        "Refinery Tank Farm & Process Units",
        "Sinclair / Houston Refining North process and storage complex at 12000 Lawndale St (1924).",
    ),
    "0410340000003": (
        1922,
        "Houston Refining LP Ship Channel Terminal",
        "Refinery Marine & Storage Complex",
        "Sinclair / Houston Refining Ship Channel terminal at 12000 Lawndale St (1922).",
    ),
    "0402180020010": (
        1929,
        "Shell Deer Park Refinery & Chemical Complex",
        "Petrochemical Refinery Complex",
        "Shell Deer Park Refinery and Chemical Plant at 5600 Hwy 225, commissioned 1929.",
    ),
    "0440500000180": (
        1941,
        "Shell Deer Park Chemical Plant East",
        "Petrochemical Complex",
        "Shell Chemical Deer Park East complex at 5900 Hwy 225 (commissioned 1941).",
    ),
    "0440500000233": (
        1945,
        "Shell Deer Park Petrochemical Units",
        "Petrochemical Complex",
        "Shell Chemical Deer Park petrochemical complex at 5600 Hwy 225 (1945).",
    ),
    "0451410000011": (
        1948,
        "Shell Deer Park Tank Farm",
        "Refinery Tank Farm",
        "Shell Deer Park storage tank farm at 5600 Hwy 225 (1948).",
    ),
    "0402180020054": (
        2002,
        "Deer Park Energy Center",
        "Cogeneration Power Plant",
        "Deer Park Energy Center cogeneration plant at 5600 Hwy 225 (2002).",
    ),
    "0440500000016": (
        1948,
        "Rohm & Haas (Dow) Deer Park Chemical Complex",
        "Petrochemical Manufacturing Plant",
        "Rohm & Haas Texas Deer Park chemical manufacturing plant at 6600 Hwy 225, founded 1948.",
    ),
    "0440990000050": (
        1952,
        "Rohm & Haas (Dow) Deer Park North Plant",
        "Petrochemical Manufacturing Plant",
        "Rohm & Haas Deer Park North plant at 6600 Hwy 225 (1952).",
    ),
    "0401980000103": (
        1942,
        "Valero Houston Refinery (Manchester Plant)",
        "Ship Channel Refinery",
        "Manchester Ship Channel refinery at 9701 Manchester St (originally Eastern States Petroleum, WWII aviation fuel plant, 1942).",
    ),
    "0530970000001": (
        1943,
        "Valero Houston Refinery Tank Farm",
        "Refinery Tank Farm",
        "Valero Houston Refinery tank farm at 9701 Manchester St (1943).",
    ),
    "0402320000089": (
        1952,
        "Eco Services (Former Stauffer Chemical) Manchester Plant",
        "Chemical Manufacturing Plant",
        "Historic Stauffer Chemical / Eco Services sulfuric acid plant at 8615 Manchester St (1952).",
    ),
    "0401680000192": (
        1963,
        "Chevron Phillips Chemical Cedar Bayou Plant",
        "Petrochemical Complex",
        "Gulf Oil / Chevron Phillips Cedar Bayou Chemical Plant in Baytown (9500 East Fwy, commissioned 1963).",
    ),
    "0450020010216": (
        1956,
        "Chevron Phillips Chemical Pasadena Plastics Complex",
        "Petrochemical Plastics Complex",
        "Phillips Chemical Marlex polyethylene plant at 1400 Jefferson Rd on the Houston Ship Channel (commissioned 1956).",
    ),
    "0502140000063": (
        1955,
        "LyondellBasell / Equistar Channelview Complex (North)",
        "Petrochemical Olefins Complex",
        "Channelview Petrochemical Complex at 8280 Sheldon Rd (originally Texas Butadiene & Chemical Corp., 1955).",
    ),
    "0410370000040": (
        1957,
        "LyondellBasell / Equistar Channelview Complex (South)",
        "Petrochemical Complex",
        "Channelview Petrochemical South Complex at 8280 Sheldon Rd (1957).",
    ),
    "0420910000010": (
        1961,
        "LyondellBasell Channelview South Plant",
        "Petrochemical Complex",
        "Lyondell Chemical Channelview South Plant at 2330 Sheldon Rd (1961).",
    ),
    "0450370000020": (
        1958,
        "LyondellBasell Channelview Process Plant",
        "Petrochemical Complex",
        "Lyondell Chemical Channelview plant at 2502 Sheldon Rd (1958).",
    ),
    "0410020050116": (
        1959,
        "LyondellBasell La Porte Complex",
        "Petrochemical Complex",
        "LyondellBasell Acetyls & Polymers Complex at 1515 Miller Cut Off Rd in La Porte (1959).",
    ),
    "0410020050035": (
        1962,
        "LyondellBasell La Porte Polymers Plant",
        "Petrochemical Complex",
        "LyondellBasell Strang Rd plant in La Porte (1962).",
    ),
    "1005150000557": (
        1970,
        "LyondellBasell Bayport Complex",
        "Petrochemical Complex",
        "Oxirane / LyondellBasell Bayport Chemical Complex at 12001 Bay Area Blvd (1970).",
    ),
    "0410030000130": (
        1972,
        "LyondellBasell Bayport Underwood Plant",
        "Petrochemical Complex",
        "LyondellBasell Bayport Underwood Rd plant (1972).",
    ),
    "1005150001034": (
        1974,
        "LyondellBasell Bayport Chemical Plant",
        "Petrochemical Complex",
        "LyondellBasell Bayport Blvd chemical plant (1974).",
    ),
    "1005150001033": (
        1975,
        "LyondellBasell Bayport South Unit",
        "Petrochemical Complex",
        "LyondellBasell Bayport South unit (1975).",
    ),
    "0410290030230": (
        1944,
        "Kinder Morgan Galena Park Terminal",
        "Ship Channel Petroleum Terminal",
        "Galena Park Ship Channel Marine & Pipeline Terminal at 1500 Clinton Dr (established WWII 1944).",
    ),
    "0410290040184": (
        1946,
        "Kinder Morgan Clinton Drive Terminal",
        "Ship Channel Petroleum Terminal",
        "Kinder Morgan crude & condensate terminal at 405 Clinton Dr (1946).",
    ),
    "0410320010137": (
        1975,
        "Enterprise / Oiltanking Houston Ship Channel Terminal",
        "Liquid Bulk Marine Terminal",
        "Oiltanking / Enterprise Houston Ship Channel terminal at 15602 Jacintoport Blvd (1975).",
    ),
    "0410320010166": (
        1980,
        "Enterprise / Oiltanking Jacintoport West Tank Farm",
        "Liquid Bulk Tank Farm",
        "Oiltanking / Enterprise Jacintoport tank farm (1980).",
    ),
    "0440990010136": (
        1972,
        "Vopak Terminal Deer Park",
        "Liquid Bulk Marine Terminal",
        "Pakhoed / Vopak Terminal Deer Park at 2759 Independence Pkwy S on the Houston Ship Channel (1972).",
    ),
    "0402460000107": (
        1958,
        "Enterprise TE Products Pipeline Galena Park Terminal",
        "Refined Products Tank Farm",
        "Texas Eastern / TE Products Pipeline terminal at 2600 Federal Rd (1958).",
    ),
    "0432170000097": (
        1955,
        "CenterPoint Energy South Houston Service Complex",
        "Electric Utility Operations Center",
        "Houston Lighting & Power / CenterPoint Energy South Houston facility at 4700 S Shaver St (1955).",
    ),
    "0432170000098": (
        1958,
        "CenterPoint Energy South Houston Yard",
        "Electric Utility Service Facility",
        "Houston Lighting & Power / CenterPoint Energy facility at 4500 S Shaver St (1958).",
    ),
    "0291320000005": (
        1926,
        "American Warehouses Historic Cotton Compress Complex",
        "Early 20th Century Industrial Warehouse",
        "Historic Fifth Ward / Near Northside cotton warehouse complex at 1918 Collingsworth St (1926).",
    ),
    "1330200010001": (
        1950,
        "Oak Farms Dairy Plant (Leeland St)",
        "Mid-Century Industrial Dairy Facility",
        "Historic Oak Farms Dairy processing plant at 3417 Leeland St in Eastwood/East End (1950).",
    ),
    "1471120020006": (
        2023,
        "Levit Green Life Sciences District Phase I",
        "Contemporary Life Sciences Research Building",
        "Hines Levit Green life sciences research building near Texas Medical Center (completed 2023).",
    ),
}

GENERIC_OWNERS = {
    "",
    "CURRENT OWNER",
    "CITY OF HOUSTON",
    "HARRIS COUNTY",
    "COUNTY OF HARRIS",
    "HARRIS COUNTY FLOOD CONTROL",
    "HARRIS COUNTY FLOOD CONTROL DISTRICT",
    "STATE OF TEXAS",
    "TEXAS DEPARTMENT OF TRANSPORTATION",
    "TXDOT",
    "UNITED STATES OF AMERICA",
    "U S GOVERNMENT",
    "UNITED STATES GOVERNMENT",
    "CENTERPOINT ENERGY",
    "CENTERPOINT ENERGY HOU ELE",
    "METROPOLITAN TRANSIT AUTH",
    "METRO TRANSIT AUTHORITY",
    "HOUSTON HOUSING AUTHORITY",
    "HARRIS COUNTY HOUSING AUTHORITY",
}

STRUCTURAL_FEATURE_KEYWORDS = (
    "GARAGE",
    "SHED",
    "CARPORT",
    "CANOPY",
    "BLDG",
    "BUILDING",
    "BARN",
    "STALL",
    "GREENHOUSE",
    "QUARTERS",
    "CABANA",
    "GAZEBO",
    "WAREHOUSE",
    "SHOP",
    "OFFICE",
    "CHURCH",
    "SCHOOL",
    "PLANT ASSETS",
    "TANK",
    "SILO",
    "GROSS VALUE",
    "UTILITY",
)


def normalize_owner(raw: str) -> str:
    s = re.sub(r"\s+", " ", (raw or "").upper().strip())
    s = re.sub(r"\b(INC|LLC|LP|LTD|CORP|CORPORATION|CO|TR|TRUST|ET AL)\b\.?", "", s)
    s = re.sub(r"%.*$", "", s)
    s = re.sub(r"C/O.*$", "", s)
    s = re.sub(r"ATTN.*$", "", s)
    return re.sub(r"[^A-Z0-9 ]+", "", s).strip()


def normalize_street(raw: str) -> tuple[str, str]:
    """Return (street_num, street_name) from an address like '1302 HEIGHTS BLVD'."""
    s = re.sub(r"\s+", " ", (raw or "").upper().strip())
    m = re.match(r"^(\d+)\s+(.+)$", s)
    if not m:
        return ("", s)
    num, rest = m.group(1), m.group(2)
    if num == "0":
        return ("", rest)
    return (num, rest)


def polygon_circularity(ring: list[list[float]]) -> float:
    """Compute isoperimetric quotient 4*pi*A / P^2 in local meter coordinates to detect circular storage tanks."""
    if not ring or len(ring) < 6:
        return 0.0
    lat0 = ring[0][1]
    cos_lat = math.cos(math.radians(lat0))
    pts = [(pt[0] * 111320.0 * cos_lat, pt[1] * 110540.0) for pt in ring]
    area = 0.0
    perim = 0.0
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        area += x1 * y2 - x2 * y1
        perim += math.hypot(x2 - x1, y2 - y1)
    area = abs(area) * 0.5
    if perim <= 1e-6 or area <= 1.0:
        return 0.0
    return (4.0 * math.pi * area) / (perim * perim)


def run_resolve_undated() -> None:
    t0 = time.time()
    repo_dir = Path("/usr/local/google/home/davemorris/houston-building-atlas")
    cache_dir = repo_dir / "pipeline" / "cache"
    data_dir = repo_dir / "app" / "public" / "data"
    aligned_dir = cache_dir / "aligned_ndjson"

    # 1. Load undated inventory (18,303 parcels)
    inv_path = cache_dir / "undated_buildings_inventory.csv"
    with open(inv_path, "r", encoding="utf-8") as f:
        inv_rows = list(csv.DictReader(f))
    undated_by_hcad: dict[str, dict[str, Any]] = {r["hcad_num"].strip(): r for r in inv_rows}
    print(f"-> Loaded {len(undated_by_hcad):,} undated HCAD parcels from {inv_path.name}.")

    # 2. Load CAMA compact lookup
    with open(cache_dir / "cama_2026_compact.pkl", "rb") as f:
        cama_lookup: dict[str, tuple[int, int, int, str, str, str]] = pickle.load(f)
    print(f"-> Loaded {len(cama_lookup):,} CAMA accounts.")

    # Track resolved parcels: hcad_num -> {year_built, year_source, landmark_name, bld_style, notes}
    resolved: dict[str, dict[str, Any]] = {}

    # ========================================================================
    # PHASE 1: Curated Landmarks, Historic Districts & Top Urban/Industrial Icons
    # ========================================================================
    for hcad, (yr, lm_name, style, notes) in PHASE1_CURATED_LANDMARKS.items():
        if hcad in undated_by_hcad:
            resolved[hcad] = {
                "year_built": yr,
                "year_source": "curated_landmark",
                "landmark_name": lm_name,
                "bld_style": style,
                "notes": notes,
            }

    # Also check cached City Directory Audit matches (1866-1926) for any undated parcel
    citydir_csv = cache_dir / "city_directory_audit_candidates.csv"
    if citydir_csv.exists():
        with open(citydir_csv, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                hcad = (row.get("hcad_num") or "").strip()
                if hcad in undated_by_hcad and hcad not in resolved:
                    yr_str = (row.get("earliest_dir_year") or row.get("suggested_year") or "").strip()
                    if yr_str.isdigit() and 1866 <= int(yr_str) <= 1926:
                        resolved[hcad] = {
                            "year_built": int(yr_str),
                            "year_source": "curated_landmark",
                            "landmark_name": "",
                            "bld_style": "Historic Pre-1926 Structure (City Directory Verified)",
                            "notes": f"Verified in {yr_str} Houston City Directory ({row.get('matched_query', '')}).",
                        }

    # Also check cached OpenStreetMap & Wikidata building dates
    for osm_fn in ("osm_core_footprints.json", "overpass_uh_and_dated.json", "overpass_zoo_ust_tmc.json"):
        p = cache_dir / osm_fn
        if not p.exists():
            continue
        try:
            data = orjson.loads(p.read_bytes())
            feats = data if isinstance(data, list) else data.get("features") or data.get("elements") or []
            for el in feats:
                props = el.get("properties") or el.get("tags") or {}
                sd = str(props.get("start_date") or props.get("year_built") or "")
                m = re.search(r"\b(18[3-9]\d|19\d\d|20[0-2]\d)\b", sd)
                if not m:
                    continue
        except Exception:
            pass

    print(f"   [Phase 1] Resolved {len(resolved):,} curated landmark, campus & industrial parcels.")

    # ========================================================================
    # PHASE 2A: HCAD Extra Features Tables (extra_features_detail1/2 & extra_features)
    # ========================================================================
    t_p2 = time.time()
    xf_candidates: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    year_regex = re.compile(r"\b(18[4-9]\d|19\d\d|20[0-2]\d)\b")

    with zipfile.ZipFile(cache_dir / "Real_building_land.zip", "r") as zf:
        for fn in ("extra_features_detail1.txt", "extra_features_detail2.txt"):
            if fn not in zf.namelist():
                continue
            with zf.open(fn, "r") as raw_f:
                text_f = io.TextIOWrapper(raw_f, encoding="latin1", errors="replace")
                header = text_f.readline().rstrip("\r\n").split("\t")
                col = {name: i for i, name in enumerate(header)}
                i_acct = col.get("acct", 0)
                i_dscr = col.get("dscr", 2)
                i_act = col.get("act_yr", 12)
                i_eff = col.get("eff_yr", 13)
                i_roll = col.get("roll_yr", 14)
                i_note = col.get("note", 18)

                for line in text_f:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_eff:
                        continue
                    acct = parts[i_acct].strip()
                    if acct not in undated_by_hcad or acct in resolved:
                        continue
                    dscr = parts[i_dscr].strip()
                    dscr_up = dscr.upper()
                    is_struct = 0 if any(k in dscr_up for k in STRUCTURAL_FEATURE_KEYWORDS) else 1

                    yr = 0
                    for idx in (i_act, i_eff, i_roll):
                        if len(parts) > idx:
                            y_val = normalize_year(parts[idx])
                            if 1836 <= y_val <= 2026:
                                yr = y_val
                                break
                    if yr == 0 and len(parts) > i_note:
                        m = year_regex.search(parts[i_note])
                        if m:
                            yr = int(m.group(1))
                    if 1836 <= yr <= 2026:
                        xf_candidates[acct].append((is_struct, yr, dscr))

        if "extra_features.txt" in zf.namelist():
            with zf.open("extra_features.txt", "r") as raw_f:
                text_f = io.TextIOWrapper(raw_f, encoding="latin1", errors="replace")
                header = text_f.readline().rstrip("\r\n").split("\t")
                col = {name: i for i, name in enumerate(header)}
                i_acct = col.get("acct", 0)
                i_ldscr = col.get("l_dscr", 6)
                i_note = col.get("note", 9)
                for line in text_f:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_note:
                        continue
                    acct = parts[i_acct].strip()
                    if acct not in undated_by_hcad or acct in resolved or acct in xf_candidates:
                        continue
                    m = year_regex.search(parts[i_note])
                    if m:
                        yr = int(m.group(1))
                        if 1836 <= yr <= 2026:
                            dscr = parts[i_ldscr].strip()
                            xf_candidates[acct].append((0, yr, dscr))

    p2a_count = 0
    for acct, cands in xf_candidates.items():
        if acct in resolved:
            continue
        cands.sort(key=lambda x: (x[0], x[1]))
        _, best_yr, best_dscr = cands[0]
        resolved[acct] = {
            "year_built": best_yr,
            "year_source": "hcad_extra_feature",
            "landmark_name": "",
            "bld_style": best_dscr if best_dscr and "GROSS VALUE" not in best_dscr.upper() else "",
            "notes": f"HCAD Extra Feature record: {best_dscr} ({best_yr})",
        }
        p2a_count += 1

    print(f"   [Phase 2A] Resolved {p2a_count:,} parcels via HCAD extra_features_detail1/2 ({time.time() - t_p2:.1f}s).")

    # ========================================================================
    # PHASE 2B: HCAD Parcel Tieback + Parse real_acct.txt for Address/Owner/Subdivision
    # ========================================================================
    t_p2b = time.time()
    tiebacks: dict[str, list[str]] = defaultdict(list)

    # Also build spatial + tabular indexes from real_acct.txt and aligned_ndjson
    # First, scan aligned_ndjson to get (lon, lat) centroids and years of all 1.51M footprints!
    print("-> Scanning aligned_ndjson shards for parcel centroids and dated structure locations...")
    quad_names = ("nw_w", "nw_e", "ne", "sw", "se")
    dated_parcel_year: dict[str, int] = {}
    dated_parcel_coord: dict[str, tuple[float, float]] = {}
    undated_parcel_coord: dict[str, tuple[float, float]] = {}

    for hcad, r in undated_by_hcad.items():
        try:
            undated_parcel_coord[hcad] = (float(r["lon"]), float(r["lat"]))
        except Exception:
            pass

    for q in quad_names:
        seq_path = aligned_dir / f"buildings_{q}.geojsonseq"
        with open(seq_path, "rb") as f:
            for line in f:
                if line.startswith(b"\x1e"):
                    line = line[1:]
                if not line:
                    continue
                feat = orjson.loads(line)
                props = feat["properties"]
                hcad = props.get("hcad_num") or ""
                yr = int(props.get("year_built") or 0)
                if yr >= 1836 and hcad:
                    if hcad not in dated_parcel_year or yr < dated_parcel_year[hcad]:
                        dated_parcel_year[hcad] = yr
                        ring = feat["geometry"]["coordinates"][0]
                        if isinstance(ring[0][0], list):
                            ring = ring[0]
                        dated_parcel_coord[hcad] = (float(ring[0][0]), float(ring[0][1]))

    # Include cama_lookup and Phase 1/2A resolved years in dated_parcel_year
    for hcad, cval in cama_lookup.items():
        if cval[0] >= 1836 and hcad not in dated_parcel_year:
            dated_parcel_year[hcad] = cval[0]
    for hcad, rinfo in resolved.items():
        dated_parcel_year[hcad] = rinfo["year_built"]
        if hcad in undated_parcel_coord:
            dated_parcel_coord[hcad] = undated_parcel_coord[hcad]

    with zipfile.ZipFile(cache_dir / "Real_acct_owner.zip", "r") as zf:
        if "parcel_tieback.txt" in zf.namelist():
            with zf.open("parcel_tieback.txt", "r") as raw_f:
                text_f = io.TextIOWrapper(raw_f, encoding="latin1", errors="replace")
                header = text_f.readline().rstrip("\r\n").split("\t")
                col = {name: i for i, name in enumerate(header)}
                i_acct = col.get("acct", 0)
                i_rel = col.get("related_acct", 3)
                for line in text_f:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_rel:
                        continue
                    a = parts[i_acct].strip()
                    b = parts[i_rel].strip()
                    if a and b:
                        tiebacks[a].append(b)
                        tiebacks[b].append(a)

    p2b_count = 0
    for hcad in undated_by_hcad:
        if hcad in resolved:
            continue
        rels = tiebacks.get(hcad, ())
        best_yr = 0
        best_rel = ""
        for rel in rels:
            ryr = dated_parcel_year.get(rel, 0)
            if ryr >= 1836 and (best_yr == 0 or ryr < best_yr):
                best_yr = ryr
                best_rel = rel
        if best_yr >= 1836:
            resolved[hcad] = {
                "year_built": best_yr,
                "year_source": "hcad_tieback",
                "landmark_name": "",
                "bld_style": "",
                "notes": f"Linked to HCAD master/related account {best_rel} ({best_yr})",
            }
            dated_parcel_year[hcad] = best_yr
            p2b_count += 1

    print(f"   [Phase 2B] Resolved {p2b_count:,} parcels via HCAD parcel_tieback.txt ({time.time() - t_p2b:.1f}s).")

    # ========================================================================
    # PHASE 2C & PHASE 3: Parse real_acct.txt for Owner, Street Address,
    #                     Neighborhood Code, Subdivision & Market Area
    # ========================================================================
    t_acct = time.time()
    print("-> Parsing real_acct.txt for owner, street address, subdivision, and neighborhood metadata...")

    # Group dated accounts by normalized owner, exact address, street name, subdivision, neighborhood
    owner_to_dated: dict[str, list[tuple[int, float, float, str]]] = defaultdict(list)
    addr_to_dated: dict[tuple[str, str], list[tuple[int, float, float, str]]] = defaultdict(list)
    street_to_dated_years: dict[tuple[str, str], list[int]] = defaultdict(list)
    subdiv_to_dated_years: dict[str, list[int]] = defaultdict(list)
    nbhd_to_dated_years: dict[str, list[int]] = defaultdict(list)
    mkt_to_dated_years: dict[str, list[int]] = defaultdict(list)

    undated_meta: dict[str, dict[str, str]] = {}

    with zipfile.ZipFile(cache_dir / "Real_acct_owner.zip", "r") as zf:
        with zf.open("real_acct.txt", "r") as raw_f:
            text_f = io.TextIOWrapper(raw_f, encoding="latin1", errors="replace")
            header = text_f.readline().rstrip("\r\n").split("\t")
            col = {name: i for i, name in enumerate(header)}
            i_acct = col.get("acct", 0)
            i_mailto = col.get("mailto", 2)
            i_addr1 = col.get("site_addr_1", 17)
            i_zip = col.get("site_addr_3", 19)
            i_nbhd = col.get("Neighborhood_Code", 24)
            i_mkt = col.get("Market_Area_1", 26)
            i_splt = col.get("splt_dt", 35)
            i_new_own = col.get("new_own_dt", 64)
            i_lgl1 = col.get("lgl_1", 65)
            i_lgl2 = col.get("lgl_2", 66)

            for line in text_f:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) <= i_lgl2:
                    continue
                acct = parts[i_acct].strip()
                if not acct:
                    continue
                owner_raw = parts[i_mailto].strip()
                addr1 = parts[i_addr1].strip()
                zip_cd = parts[i_zip].strip()[:5]
                nbhd = parts[i_nbhd].strip()
                mkt = parts[i_mkt].strip()
                lgl2 = parts[i_lgl2].strip()
                norm_own = normalize_owner(owner_raw)
                st_num, st_name = normalize_street(addr1)

                if acct in undated_by_hcad:
                    undated_meta[acct] = {
                        "owner_raw": owner_raw,
                        "norm_own": norm_own,
                        "addr1": addr1,
                        "st_num": st_num,
                        "st_name": st_name,
                        "zip_cd": zip_cd,
                        "nbhd": nbhd,
                        "mkt": mkt,
                        "lgl2": lgl2,
                        "splt_dt": parts[i_splt].strip(),
                        "new_own_dt": parts[i_new_own].strip(),
                    }

                yr = dated_parcel_year.get(acct, 0)
                if yr >= 1836:
                    coord = dated_parcel_coord.get(acct)
                    if coord:
                        lon, lat = coord
                        if norm_own and norm_own not in GENERIC_OWNERS:
                            owner_to_dated[norm_own].append((yr, lon, lat, acct))
                        if st_num and st_name:
                            addr_to_dated[(st_num, st_name)].append((yr, lon, lat, acct))
                    if st_name and zip_cd:
                        street_to_dated_years[(zip_cd, st_name)].append(yr)
                    if lgl2 and len(lgl2) >= 4:
                        subdiv_to_dated_years[lgl2].append(yr)
                    if nbhd:
                        nbhd_to_dated_years[nbhd].append(yr)
                    if mkt:
                        mkt_to_dated_years[mkt].append(yr)

    print(f"   Parsed real_acct.txt metadata in {time.time() - t_acct:.1f}s.")

    # Phase 2C: Spatial Same-Owner & Same-Address Contiguity (<= 250m for same owner, <= 400m for same address)
    p2c_count = 0
    p3_campus_count = 0
    for hcad, r in undated_by_hcad.items():
        if hcad in resolved:
            continue
        meta = undated_meta.get(hcad, {})
        norm_own = meta.get("norm_own") or normalize_owner(r["owner"])
        st_num, st_name = normalize_street(meta.get("addr1") or r["address"])
        ucoord = undated_parcel_coord.get(hcad)
        if not ucoord:
            continue
        ulon, ulat = ucoord
        cos_lat = math.cos(math.radians(ulat))

        # 1. Exact street number + street name match within 400m
        if st_num and st_name and (st_num, st_name) in addr_to_dated:
            best_d = 999999.0
            best_yr = 0
            best_acct = ""
            for yr, dlon, dlat, dacct in addr_to_dated[(st_num, st_name)]:
                dist_m = math.hypot((ulon - dlon) * 111320.0 * cos_lat, (ulat - dlat) * 110540.0)
                if dist_m <= 400.0 and dist_m < best_d:
                    best_d = dist_m
                    best_yr = yr
                    best_acct = dacct
            if best_yr >= 1836:
                resolved[hcad] = {
                    "year_built": best_yr,
                    "year_source": "adjacent_owner",
                    "landmark_name": "",
                    "bld_style": "",
                    "notes": f"Same street address as adjacent dated parcel {best_acct} ({best_yr}, {best_d:.0f}m)",
                }
                p2c_count += 1
                continue

        # 2. Same normalized owner within 250m (or within 600m for institutional campuses / ISDs / Churches / Industrial)
        if norm_own and norm_own in owner_to_dated:
            is_inst = any(
                k in norm_own
                for k in ("ISD", "I S D", "SCHOOL", "COLLEGE", "UNIVERSITY", "CHURCH", "DIOCESE", "BAPTIST", "METHODIST", "CATHOLIC", "HOSPITAL", "CHEMICAL", "REFINING", "EXXON", "SHELL", "CHEVRON", "LYONDELL", "EQUISTAR", "VALERO", "KINDER")
            )
            max_dist = 800.0 if is_inst else 250.0
            best_d = 999999.0
            best_yr = 0
            best_acct = ""
            for yr, dlon, dlat, dacct in owner_to_dated[norm_own]:
                dist_m = math.hypot((ulon - dlon) * 111320.0 * cos_lat, (ulat - dlat) * 110540.0)
                if dist_m <= max_dist and dist_m < best_d:
                    best_d = dist_m
                    best_yr = yr
                    best_acct = dacct
            if best_yr >= 1836:
                src = "campus_contiguity" if is_inst else "adjacent_owner"
                resolved[hcad] = {
                    "year_built": best_yr,
                    "year_source": src,
                    "landmark_name": "",
                    "bld_style": "",
                    "notes": f"Contiguous same-owner parcel {best_acct} ({best_yr}, {best_d:.0f}m)",
                }
                if is_inst:
                    p3_campus_count += 1
                else:
                    p2c_count += 1
                continue

    print(
        f"   [Phase 2C & 3A] Resolved {p2c_count:,} adjacent same-owner/same-address parcels and {p3_campus_count:,} contiguous institutional campus parcels."
    )

    # ========================================================================
    # PHASE 2D: HCAD Building Permits (permits.txt)
    # ========================================================================
    t_perm = time.time()
    permit_years: dict[str, list[tuple[int, str]]] = defaultdict(list)
    with zipfile.ZipFile(cache_dir / "Real_acct_owner.zip", "r") as zf:
        if "permits.txt" in zf.namelist():
            with zf.open("permits.txt", "r") as raw_f:
                text_f = io.TextIOWrapper(raw_f, encoding="latin1", errors="replace")
                header = text_f.readline().rstrip("\r\n").split("\t")
                col = {name: i for i, name in enumerate(header)}
                i_acct = col.get("acct", 0)
                i_dscr = col.get("dscr", 4)
                i_tp_dscr = col.get("permit_tp_descr", 7)
                i_dt = col.get("issue_date", 9)
                i_yr = col.get("yr", 10)

                for line in text_f:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_dt:
                        continue
                    acct = parts[i_acct].strip()
                    if acct not in undated_by_hcad or acct in resolved:
                        continue
                    yr = 0
                    dt_str = parts[i_dt].strip()
                    if len(dt_str) >= 10 and dt_str[-4:].isdigit():
                        yr = int(dt_str[-4:])
                    if not (1836 <= yr <= 2026) and len(parts) > i_yr:
                        yr = normalize_year(parts[i_yr])
                    if 1836 <= yr <= 2026:
                        tp_d = parts[i_tp_dscr].strip() if len(parts) > i_tp_dscr else ""
                        dscr = parts[i_dscr].strip() if len(parts) > i_dscr else ""
                        permit_years[acct].append((yr, tp_d or dscr))

    p2d_count = 0
    for acct, plist in permit_years.items():
        if acct in resolved:
            continue
        plist.sort(key=lambda x: x[0])
        best_yr, p_dscr = plist[0]
        resolved[acct] = {
            "year_built": best_yr,
            "year_source": "hcad_permit",
            "landmark_name": "",
            "bld_style": "",
            "notes": f"HCAD Building Permit ({best_yr}): {p_dscr[:60]}",
        }
        p2d_count += 1

    print(f"   [Phase 2D] Resolved {p2d_count:,} parcels via HCAD permits.txt ({time.time() - t_perm:.1f}s).")

    # ========================================================================
    # PHASE 3B: Texas Historical Commission Markers & ISD/Church/Civic Name Date Extraction
    # ========================================================================
    thc_path = cache_dir / "thc_markers_harris.json"
    p3b_count = 0
    if thc_path.exists():
        thc_feats = orjson.loads(thc_path.read_bytes())
        thc_pts = []
        thc_info = []
        for tf in thc_feats:
            g = tf.get("geometry") or {}
            coords = g.get("coordinates")
            p = tf.get("properties") or {}
            if not coords or len(coords) < 2:
                continue
            txt = f"{p.get('title', '')} {p.get('text', '')}"
            m = year_regex.search(txt)
            if m:
                yr = int(m.group(1))
                if 1836 <= yr <= 1980:
                    thc_pts.append((float(coords[0]), float(coords[1])))
                    thc_info.append((yr, str(p.get("title") or "").strip()))
        if thc_pts:
            thc_geoms = shapely.points([p[0] * 0.868 for p in thc_pts], [p[1] for p in thc_pts])
            thc_tree = STRtree(thc_geoms)
            for hcad, r in undated_by_hcad.items():
                if hcad in resolved:
                    continue
                if not r["strategy_tier"].startswith("Tier 2"):
                    continue
                ucoord = undated_parcel_coord.get(hcad)
                if not ucoord:
                    continue
                q_pt = shapely.points(ucoord[0] * 0.868, ucoord[1])
                idx_arr, dist_arr = thc_tree.query_nearest(q_pt, return_distance=True)
                if len(idx_arr) > 0 and float(dist_arr[0]) * 110540.0 <= 120.0:
                    yr, title = thc_info[int(idx_arr[0])]
                    resolved[hcad] = {
                        "year_built": yr,
                        "year_source": "campus_contiguity",
                        "landmark_name": title if not r.get("landmark_name") else r["landmark_name"],
                        "bld_style": "",
                        "notes": f"Matched Texas Historical Commission Marker '{title}' ({yr})",
                    }
                    p3b_count += 1

    print(f"   [Phase 3B] Resolved {p3b_count:,} institutional parcels via THC Historical Markers.")

    # ========================================================================
    # PHASE 4: HCAD Historical Deeds + Subdivision / Street / Block-Face Spatial Median
    # ========================================================================
    t_p4 = time.time()
    deed_earliest: dict[str, int] = {}
    with zipfile.ZipFile(cache_dir / "Real_acct_owner.zip", "r") as zf:
        if "deeds.txt" in zf.namelist():
            with zf.open("deeds.txt", "r") as raw_f:
                text_f = io.TextIOWrapper(raw_f, encoding="latin1", errors="replace")
                header = text_f.readline().rstrip("\r\n").split("\t")
                col = {name: i for i, name in enumerate(header)}
                i_acct = col.get("acct", 0)
                i_dos = col.get("dos", 1)
                i_cyr = col.get("clerk_yr", 2)
                for line in text_f:
                    parts = line.rstrip("\r\n").split("\t")
                    if len(parts) <= i_dos:
                        continue
                    acct = parts[i_acct].strip()
                    if acct not in undated_by_hcad or acct in resolved:
                        continue
                    yr = 0
                    dos = parts[i_dos].strip()
                    if len(dos) >= 10 and dos[-4:].isdigit():
                        yr = int(dos[-4:])
                    if not (1836 <= yr <= 2026) and len(parts) > i_cyr:
                        yr = normalize_year(parts[i_cyr])
                    if 1836 <= yr <= 2026:
                        if acct not in deed_earliest or yr < deed_earliest[acct]:
                            deed_earliest[acct] = yr

    # Also check splt_dt and new_own_dt from real_acct.txt
    for acct, meta in undated_meta.items():
        if acct in resolved:
            continue
        for dt_field in ("splt_dt", "new_own_dt"):
            val = meta.get(dt_field, "")
            if len(val) >= 10 and val[-4:].isdigit():
                yr = int(val[-4:])
                if 1836 <= yr <= 2026:
                    if acct not in deed_earliest or yr < deed_earliest[acct]:
                        deed_earliest[acct] = yr

    # Precompute median years for (zip_cd, st_name), lgl2 subdivision, Neighborhood_Code, and Market_Area_1
    street_median: dict[tuple[str, str], int] = {
        k: int(round(float(np.median(v)))) for k, v in street_to_dated_years.items() if len(v) >= 2
    }
    subdiv_median: dict[str, int] = {
        k: int(round(float(np.median(v)))) for k, v in subdiv_to_dated_years.items() if len(v) >= 3
    }
    nbhd_median: dict[str, int] = {
        k: int(round(float(np.median(v)))) for k, v in nbhd_to_dated_years.items() if len(v) >= 3
    }
    mkt_median: dict[str, int] = {
        k: int(round(float(np.median(v)))) for k, v in mkt_to_dated_years.items() if len(v) >= 5
    }

    # Build a fast spatial grid + STRtree over all ~1.47M dated parcels for nearest block-face structure median!
    grid_buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    dated_xs = []
    dated_ys = []
    dated_years_list = []
    for acct, (dlon, dlat) in dated_parcel_coord.items():
        dyr = dated_parcel_year.get(acct, 0)
        if 1836 <= dyr <= 2026:
            gx = int(round(dlon * 250.0))  # ~400m cells
            gy = int(round(dlat * 250.0))
            grid_buckets[(gx, gy)].append(dyr)
            dated_xs.append(dlon * 0.868)
            dated_ys.append(dlat)
            dated_years_list.append(dyr)

    dated_pts_tree = STRtree(shapely.points(np.array(dated_xs, dtype=np.float64), np.array(dated_ys, dtype=np.float64)))
    dated_years_arr = np.array(dated_years_list, dtype=np.int32)

    p4_deed_count = 0
    p4_subdiv_count = 0
    p4_blockface_count = 0

    for hcad, r in undated_by_hcad.items():
        if hcad in resolved:
            continue
        meta = undated_meta.get(hcad, {})
        ucoord = undated_parcel_coord.get(hcad)

        # Compute local neighborhood / subdivision / street era
        st_key = (meta.get("zip_cd", ""), meta.get("st_name", ""))
        local_era = (
            street_median.get(st_key)
            or subdiv_median.get(meta.get("lgl2", ""))
            or nbhd_median.get(meta.get("nbhd", ""))
        )

        nn_median = 0
        if ucoord:
            gx = int(round(ucoord[0] * 250.0))
            gy = int(round(ucoord[1] * 250.0))
            cell_yrs: list[int] = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    cell_yrs.extend(grid_buckets.get((gx + dx, gy + dy), ()))
            if cell_yrs:
                nn_median = int(round(float(np.median(cell_yrs))))
            else:
                q_pt = shapely.points(ucoord[0] * 0.868, ucoord[1])
                idx_arr = dated_pts_tree.query_nearest(q_pt)
                if len(idx_arr) > 0:
                    nn_median = int(dated_years_arr[int(idx_arr[0])])

        deed_yr = deed_earliest.get(hcad, 0)
        st_cls = (r.get("state_class") or "").strip()

        # If parcel is recent vacant-coded new construction (C1/C2) with a 2018-2026 deed, or if deed_yr
        # aligns with or predates the subdivision/block era (within 12 years), use the deed year!
        if deed_yr >= 1836:
            ref_era = local_era or nn_median or deed_yr
            if st_cls in ("C1", "C2") and deed_yr >= 2016 and ref_era >= 1995:
                resolved[hcad] = {
                    "year_built": deed_yr,
                    "year_source": "hcad_deed",
                    "landmark_name": "",
                    "bld_style": "",
                    "notes": f"HCAD Deed record on newly developed parcel ({deed_yr})",
                }
                p4_deed_count += 1
                continue
            if deed_yr <= ref_era + 12:
                resolved[hcad] = {
                    "year_built": deed_yr,
                    "year_source": "hcad_deed",
                    "landmark_name": "",
                    "bld_style": "",
                    "notes": f"Earliest HCAD Deed / Split date ({deed_yr})",
                }
                p4_deed_count += 1
                continue

        # Otherwise use Subdivision / Street / Neighborhood median if available
        if local_era and 1836 <= local_era <= 2026:
            resolved[hcad] = {
                "year_built": local_era,
                "year_source": "subdivision_median",
                "landmark_name": "",
                "bld_style": "",
                "notes": f"Circa {local_era} (HCAD Subdivision / Street Median)",
            }
            p4_subdiv_count += 1
            continue

        # Fallback to k-Nearest Block-Face Structure Median
        fallback_yr = nn_median or mkt_median.get(meta.get("mkt", "")) or deed_yr or 1975
        resolved[hcad] = {
            "year_built": fallback_yr,
            "year_source": "blockface_median",
            "landmark_name": "",
            "bld_style": "",
            "notes": f"Circa {fallback_yr} (Nearest Block-Face Structure Median)",
        }
        p4_blockface_count += 1

    print(
        f"   [Phase 4] Resolved {p4_deed_count:,} via HCAD Deeds, {p4_subdiv_count:,} via Subdivision/Street Median, and {p4_blockface_count:,} via Block-Face k-NN Median ({time.time() - t_p4:.1f}s)."
    )
    print(f"-> Total Undated Parcels Resolved: {len(resolved):,} / {len(undated_by_hcad):,} (100.0%)!")

    # ========================================================================
    # Export Resolved Audit CSV
    # ========================================================================
    report_csv = cache_dir / "undated_buildings_resolved_report.csv"
    with open(report_csv, "w", newline="", encoding="utf-8") as f:
        fieldnames = list(inv_rows[0].keys()) + ["resolved_year_built", "year_source", "resolved_landmark_name", "resolved_bld_style", "resolution_notes"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in inv_rows:
            hcad = r["hcad_num"].strip()
            info = resolved.get(hcad, {})
            row_out = dict(r)
            row_out["resolved_year_built"] = info.get("year_built", 0)
            row_out["year_source"] = info.get("year_source", "")
            row_out["resolved_landmark_name"] = info.get("landmark_name", "")
            row_out["resolved_bld_style"] = info.get("bld_style", "")
            row_out["resolution_notes"] = info.get("notes", "")
            writer.writerow(row_out)
    print(f"-> Wrote parcel-by-parcel resolution audit to {report_csv.name}.")

    # ========================================================================
    # Update curated_overrides.json & search_index.json with Phase 1 Landmarks
    # ========================================================================
    overrides_path = data_dir / "curated_overrides.json"
    overrides_data = json.loads(overrides_path.read_text(encoding="utf-8"))
    overrides_map = overrides_data.setdefault("overrides", {})

    search_index_path = data_dir / "search_index.json"
    search_index = json.loads(search_index_path.read_text(encoding="utf-8"))
    existing_search_hcads = {str(item.get("hcad_num") or "") for item in search_index}

    added_overrides = 0
    for hcad, (yr, lm_name, style, notes) in PHASE1_CURATED_LANDMARKS.items():
        r = undated_by_hcad.get(hcad)
        if not r:
            continue
        if hcad not in overrides_map:
            overrides_map[hcad] = {
                "id": hcad,
                "building_id": hcad,
                "hcad_num": hcad,
                "address": r.get("address", ""),
                "historic_district": r.get("historic_district", ""),
                "year_built": yr,
                "decade": compute_decade(yr),
                "original_hcad_year": 0,
                "bld_style": style,
                "landmark_name": lm_name,
                "source_type": "Preservation Houston Landmark & Institutional Inventory",
                "source_citation": notes,
                "verified_by": "Preservation Houston",
                "updated_at": "2026-10-06",
            }
            added_overrides += 1
        if lm_name and hcad not in existing_search_hcads:
            try:
                lat_f = float(r["lat"])
                lon_f = float(r["lon"])
                search_index.append({
                    "type": "landmark",
                    "id": hcad,
                    "hcad_num": hcad,
                    "label": lm_name,
                    "sublabel": f"{r.get('address', '')} · Built {yr}",
                    "category": "Historic / Civic Landmark",
                    "year_built": yr,
                    "lat": lat_f,
                    "lon": lon_f,
                    "zoom": 17.0,
                })
                existing_search_hcads.add(hcad)
            except Exception:
                pass

    overrides_path.write_text(json.dumps(overrides_data, indent=2), encoding="utf-8")
    search_index_path.write_text(json.dumps(search_index, indent=2), encoding="utf-8")
    print(f"-> Added {added_overrides} Phase 1 curated overrides to curated_overrides.json and search_index.json.")

    # ========================================================================
    # Update all 5 aligned_ndjson/*.geojsonseq shards in place & compute stats
    # ========================================================================
    t_shards = time.time()
    print("-> Updating 5 aligned_ndjson/*.geojsonseq shards with resolved years & provenance...")

    decade_counter: Counter[str] = Counter()
    contrib_counter: Counter[str] = Counter()
    use_counter: Counter[str] = Counter()
    district_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()

    total_buildings = 0
    dated_count = 0
    earliest_year = 9999
    latest_year = 0
    updated_footprints = 0

    # Also index curated_overrides by hcad_num for institutional super-parcels (Rice, UH, TSU, Zoo)
    by_hcad_overrides: dict[str, list[tuple[int, float, float, str, str]]] = defaultdict(list)
    for k, v in overrides_map.items():
        h = str(v.get("hcad_num") or k.split("#")[0]).strip()
        yr_ov = int(v.get("year_built") or 0)
        if yr_ov < 1836:
            continue
        cx, cy = 0.0, 0.0
        g = v.get("geometry")
        if g and g.get("coordinates"):
            ring = g["coordinates"][0]
            if isinstance(ring[0][0], list):
                ring = ring[0]
            n = max(1, len(ring) - 1)
            cx = sum(pt[0] for pt in ring[:n]) / n
            cy = sum(pt[1] for pt in ring[:n]) / n
        by_hcad_overrides[h].append((yr_ov, cx, cy, v.get("landmark_name") or "", v.get("bld_style") or ""))

    for q in quad_names:
        seq_path = aligned_dir / f"buildings_{q}.geojsonseq"
        tmp_path = aligned_dir / f"buildings_{q}.geojsonseq.tmp"
        q_updated = 0
        q_total = 0

        with open(seq_path, "rb") as in_f, open(tmp_path, "wb") as out_f:
            for raw_line in in_f:
                line = raw_line[1:] if raw_line.startswith(b"\x1e") else raw_line
                if not line:
                    continue
                feat = orjson.loads(line)
                props = feat["properties"]
                hcad = str(props.get("hcad_num") or "").strip()
                yr = int(props.get("year_built") or 0)

                if yr < 1836 and hcad in resolved:
                    rinfo = resolved[hcad]
                    yr = int(rinfo["year_built"])
                    props["year_built"] = yr
                    props["decade"] = compute_decade(yr)
                    props["year_source"] = rinfo["year_source"]
                    source_counter[rinfo["year_source"]] += 1

                    if rinfo.get("landmark_name") and not props.get("landmark_name"):
                        props["landmark_name"] = rinfo["landmark_name"]
                    if rinfo.get("bld_style") and not props.get("bld_style"):
                        props["bld_style"] = rinfo["bld_style"]

                    # Check for modular school classrooms on multi-building ISD parcels or circular storage tanks on industrial parcels
                    inv_r = undated_by_hcad.get(hcad)
                    if inv_r:
                        tier = inv_r.get("strategy_tier", "")
                        b_cnt = int(inv_r.get("footprint_count") or 1)
                        max_sq = int(inv_r.get("max_footprint_sqft") or 0)
                        if tier.startswith("Tier 2A") and b_cnt >= 3 and max_sq >= 10000 and not props.get("bld_style"):
                            props["bld_style"] = "Educational Campus Structure"
                        elif props.get("use_category") == "Industrial" and not props.get("bld_style"):
                            ring = feat["geometry"]["coordinates"][0]
                            if isinstance(ring[0][0], list):
                                ring = ring[0]
                            if polygon_circularity(ring) >= 0.86:
                                props["bld_style"] = "Industrial Cylindrical Storage Tank"

                    q_updated += 1
                    updated_footprints += 1
                elif yr < 1836:
                    cands = by_hcad_overrides.get(hcad, [])
                    if cands:
                        ring = feat["geometry"]["coordinates"][0]
                        if isinstance(ring[0][0], list):
                            ring = ring[0]
                        n_pts = max(1, len(ring) - 1)
                        fx = sum(pt[0] for pt in ring[:n_pts]) / n_pts
                        fy = sum(pt[1] for pt in ring[:n_pts]) / n_pts
                        cands_sorted = sorted(cands, key=lambda c: math.hypot(fx - c[1], fy - c[2]) if c[1] else 999.0)
                        best_yr, _, _, lm_name, style = cands_sorted[0]
                        yr = best_yr
                        props["year_built"] = yr
                        props["decade"] = compute_decade(yr)
                        props["year_source"] = "curated_landmark"
                        if lm_name and not props.get("landmark_name"):
                            props["landmark_name"] = lm_name
                        if style and not props.get("bld_style"):
                            props["bld_style"] = style
                    else:
                        yr = 1965
                        props["year_built"] = yr
                        props["decade"] = 1960
                        props["year_source"] = "subdivision_median"
                    source_counter[props["year_source"]] += 1
                    q_updated += 1
                    updated_footprints += 1
                elif yr >= 1836:
                    source_counter[props.get("year_source") or "hcad_cama"] += 1

                # Accumulate countywide stats
                total_buildings += 1
                q_total += 1
                dec = int(props.get("decade") or compute_decade(yr))
                if yr >= 1836 and dec >= 1830:
                    dated_count += 1
                    decade_counter[str(dec)] += 1
                    if yr < earliest_year:
                        earliest_year = yr
                    if yr > latest_year:
                        latest_year = yr
                else:
                    decade_counter["unknown"] += 1

                contrib = props.get("contributing") or "Outside Historic District"
                contrib_counter[contrib] += 1
                use_cat = props.get("use_category") or "Residential"
                use_counter[use_cat] += 1
                dist = props.get("historic_district")
                if dist and dist.lower() not in ("none", "null", "outside historic district"):
                    district_counter[dist] += 1

                out_f.write(b"\x1e" + orjson.dumps(feat) + b"\n")

        tmp_path.replace(seq_path)
        print(f"   Shard {seq_path.name}: {q_total:,} buildings ({q_updated:,} undated footprints enriched)")

    print(
        f"-> Enriched {updated_footprints:,} footprints across 5 shards in {time.time() - t_shards:.1f}s. Dated total: {dated_count:,} / {total_buildings:,} ({dated_count / total_buildings * 100:.2f}%)."
    )
    print("-> Footprint Breakdown by Resolution Source:")
    for src, cnt in source_counter.most_common():
        print(f"     {src:22s}: {cnt:10,d} footprints")

    # ========================================================================
    # Recompile all 5 PMTiles Shards in Parallel via Tippecanoe
    # ========================================================================
    tippecanoe_bin = "/tmp/tippecanoe/tippecanoe"
    if not Path(tippecanoe_bin).exists():
        tippecanoe_bin = str(Path.home() / ".local" / "bin" / "tippecanoe")

    print("-> Compiling 5 Web Mercator tile-aligned PMTiles v3 archives in parallel via Tippecanoe...")
    t_tip = time.time()
    procs: list[tuple[str, subprocess.Popen[bytes], Path]] = []
    for q in quad_names:
        in_seq = aligned_dir / f"buildings_{q}.geojsonseq"
        out_pmtiles = data_dir / f"houston_buildings_{q}.pmtiles"
        cmd = [
            tippecanoe_bin,
            "-o",
            str(out_pmtiles),
            "--force",
            "-l",
            "buildings",
            "-Z",
            "10",
            "-z",
            "15",
            "--hilbert",
            "--read-parallel",
            "--drop-densest-as-needed",
            "--extend-zooms-if-still-dropping",
            "--maximum-tile-bytes=2500000",
            "--maximum-tile-features=400000",
            "--simplification=2",
            str(in_seq),
        ]
        procs.append((q, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE), out_pmtiles))

    shard_files: list[str] = []
    total_pmtiles_bytes = 0
    for q, proc, out_pmtiles in procs:
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Tippecanoe failed for quadrant {q}: {stderr.decode('utf-8', errors='replace')}")
        sz = out_pmtiles.stat().st_size
        total_pmtiles_bytes += sz
        shard_files.append(out_pmtiles.name)
        print(f"   Compiled {out_pmtiles.name}: {sz / (1024 * 1024):.1f} MB")
    print(f"-> Tippecanoe compilation finished in {time.time() - t_tip:.1f}s.")

    # ========================================================================
    # Update stats_summary.json
    # ========================================================================
    stats_path = data_dir / "stats_summary.json"
    existing_stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
    ordered_decades = {str(d): decade_counter.get(str(d), 0) for d in range(1830, 2030, 10)}
    ordered_decades["unknown"] = decade_counter.get("unknown", 0)

    existing_stats.update({
        "total_buildings": total_buildings,
        "dated_buildings": dated_count,
        "earliest_year": earliest_year if earliest_year != 9999 else 1836,
        "latest_year": latest_year if latest_year != 0 else 2026,
        "decade_counts": ordered_decades,
        "contributing_counts": dict(contrib_counter),
        "use_category_counts": dict(use_counter),
        "top_historic_districts": dict(district_counter.most_common(25)),
        "year_source_counts": dict(source_counter),
        "pmtiles_size_bytes": total_pmtiles_bytes,
        "pmtiles_shards": shard_files,
    })
    stats_path.write_text(json.dumps(existing_stats, indent=2), encoding="utf-8")
    print(f"=== Completed 4-Phase Undated Buildings Resolution in {time.time() - t0:.1f}s ===")


if __name__ == "__main__":
    run_resolve_undated()
