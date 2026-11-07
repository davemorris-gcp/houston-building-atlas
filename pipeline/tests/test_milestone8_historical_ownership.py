"""Milestone 8 unit, modularity, determinism, and regression tests for 2005-2026 HCAD ownership chains."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zipfile

import pytest

from atlas_pipeline.build_historical_ownership_2005_2026 import (
    HCAD_MAINFRAME_SENTINEL,
    MAX_OWNER_TENURES_PER_PARCEL,
    ConsolidatedOwnerTenure,
    RollOwnerSnapshot,
    are_same_owner_tenure,
    build_owner_history_shards,
    compress_deterministic_gzip_json,
    consolidate_account_tenures,
    encode_compact_owner_history,
    maybe_rebuild_owner_history_for_staged_roll,
    normalize_acquisition_date_token,
    parse_single_roll_snapshot,
    resolve_annual_roll_zip_paths,
    sanitize_wire_owner_name,
)
from atlas_pipeline.build_ownership_and_history import (
    enrich_permit_shards_with_ownership_and_deeds,
)
from atlas_pipeline.schema import (
    ACTIVE_CACHE_BUST,
    ALL_PLACEHOLDER_OWNER_TOKENS,
    CONFIDENTIAL_OWNER_TOKENS,
    is_confidential_owner_token,
    is_placeholder_owner_token,
    sync_repo_tax_year_and_cache_bust,
    update_pmtiles_manifest_v3,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_custom_roll_zip(
    zip_path: Path,
    *,
    real_member_name: str = "real_acct.txt",
    owners_member_name: str = "owners.txt",
    deeds_member_name: str = "deeds.txt",
    real_header: list[str] | None = None,
    real_rows: list[list[str]] = (),
    owners_header: list[str] | None = None,
    owners_rows: list[list[str]] = (),
    deeds_header: list[str] | None = None,
    deeds_rows: list[list[str]] = (),
    permits_rows: list[list[str]] | None = None,
) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    r_hdr = real_header or [
        "acct",
        "yr",
        "mailto",
        "mail_addr_1",
        "mail_city",
        "mail_state",
        "mail_zip",
        "site_addr_1",
        "site_addr_3",
        "state_class",
        "new_own_dt",
    ]
    o_hdr = owners_header or ["acct", "ln_num", "name", "aka"]
    d_hdr = deeds_header or ["acct", "dos", "clerk_yr", "clerk_id", "deed_id"]

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        real_txt = "\n".join(["\t".join(r_hdr), *("\t".join(r) for r in real_rows)]) + "\n"
        zf.writestr(real_member_name, real_txt)
        if owners_rows or owners_member_name:
            own_txt = "\n".join(["\t".join(o_hdr), *("\t".join(r) for r in owners_rows)]) + "\n"
            zf.writestr(owners_member_name, own_txt)
        if deeds_rows or deeds_member_name:
            deeds_txt = "\n".join(["\t".join(d_hdr), *("\t".join(r) for r in deeds_rows)]) + "\n"
            zf.writestr(deeds_member_name, deeds_txt)
        if permits_rows is not None:
            p_hdr = ["acct", "id", "permit_type", "permit_tp_descr", "dscr", "issue_date", "yr"]
            p_txt = "\n".join(["\t".join(p_hdr), *("\t".join(r) for r in permits_rows)]) + "\n"
            zf.writestr("permits.txt", p_txt)
    return zip_path


def test_ut1_statutory_sec25025_active_retroactive_coowner_and_mailing_privacy(tmp_path: Path) -> None:
    """UT-1: Active confidential suppression, mid-window pre-election taint, ln_num>=2 co-owners, and zero mail_addr."""
    # 1. Active 2026 confidential accounts must return [] even if 2005-2025 had public personal names.
    public_history = [
        RollOwnerSnapshot(year=yr, owner_name="CARTER BENJAMIN", is_confidential=False, acq_dt="20040510")
        for yr in range(2005, 2026)
    ]
    for tok in sorted(CONFIDENTIAL_OWNER_TOKENS):
        snaps = [*public_history, RollOwnerSnapshot(year=2026, owner_name=tok, is_confidential=False, acq_dt="20040510")]
        assert consolidate_account_tenures(snaps, active_tax_year=2026) == [], f"Leaked active token {tok}"

    assert (
        consolidate_account_tenures(
            [*public_history, RollOwnerSnapshot(year=2026, owner_name="CARTER BENJAMIN", is_confidential=True)],
            active_tax_year=2026,
        )
        == []
    )
    # Even when 2005-2025 belonged to an unrelated prior seller separated by a 2025 recorded deed,
    # an active 2026 confidential election omits the ENTIRE parcel from owners_<prefix3>.json.gz:
    assert (
        consolidate_account_tenures(
            [*public_history, RollOwnerSnapshot(year=2026, owner_name="CURRENT OWNER", is_confidential=True, acq_dt="20251115")],
            deeds=[("20251115", "R2025-888777")],
            active_tax_year=2026,
        )
        == []
    )

    # 2. Mid-window confidential election (2011-2015 public -> 2016-2021 CURRENT OWNER -> 2022-2026 non-confidential sale):
    #    - 2005-2010 earlier unrelated owner ("ADAMS MARTHA") BEFORE 2011 deed IS preserved.
    #    - 2011-2015 pre-election snapshots of the protected individual ("MORRISON CLARA", acq 20110315) MUST be
    #      retroactively redacted along with 2016-2021, and their 2011 deed RP-2011-111222 MUST NOT mispair!
    #    - 2022-2026 subsequent buyer ("BAYOU CITY COTTAGES LLC", acq 20220620, RP-2022-77100) IS preserved.
    mid_window_snaps = [
        *[
            RollOwnerSnapshot(year=yr, owner_name="ADAMS MARTHA", acq_dt="19990412")
            for yr in range(2005, 2011)
        ],
        *[
            RollOwnerSnapshot(year=yr, owner_name="MORRISON CLARA", acq_dt="20110315")
            for yr in range(2011, 2016)
        ],
        *[
            RollOwnerSnapshot(year=yr, owner_name="CURRENT OWNER", is_confidential=True, acq_dt="20110315")
            for yr in range(2016, 2022)
        ],
        *[
            RollOwnerSnapshot(year=yr, owner_name="BAYOU CITY COTTAGES LLC", acq_dt="20220620")
            for yr in range(2022, 2027)
        ],
    ]
    mid_window_deeds = [
        ("20110315", "20110111222"),
        ("20220620", "R2022-77100"),
    ]
    res_mid = consolidate_account_tenures(mid_window_snaps, deeds=mid_window_deeds, active_tax_year=2026)
    owner_names_mid = [t.owner_name for t in res_mid]
    assert "MORRISON CLARA" not in owner_names_mid
    assert owner_names_mid == ["BAYOU CITY COTTAGES LLC", "ADAMS MARTHA"]
    assert res_mid[0].start_year == 2022 and res_mid[0].end_year == 2026
    assert res_mid[0].deed_token == "R2022-77100"
    assert res_mid[1].start_year == 2005 and res_mid[1].end_year == 2010
    assert res_mid[1].deed_token == ""  # Redacted 2011 deed was reserved, never attached to 2005-2010 seller!

    # 2b. Mid-window Sec. 25.025 election where HCAD resets confidential new_own_dt to '01/02/1988' ('<=1988')
    #     AND the protected owner acquired the parcel in the start year itself ('20150315'):
    sentinel_reset_snaps = [
        RollOwnerSnapshot(year=2014, owner_name="EARLIER SELLER", acq_dt="1999-04-12"),
        RollOwnerSnapshot(year=2015, owner_name="JANE ROE", acq_dt="20150315"),
        RollOwnerSnapshot(year=2016, owner_name="CONFIDENTIAL OWNER", is_confidential=True, acq_dt="01/02/1988"),
        RollOwnerSnapshot(year=2022, owner_name="LATER PUBLIC BUYER LLC", acq_dt="06/01/2022"),
    ]
    res_sentinel_reset = consolidate_account_tenures(
        sentinel_reset_snaps,
        deeds=[("20150315", "2015011111"), ("20220601", "2022099999")],
        active_tax_year=2026,
    )
    assert [t.owner_name for t in res_sentinel_reset] == ["LATER PUBLIC BUYER LLC", "EARLIER SELLER"]
    assert res_sentinel_reset[0].deed_token == "2022099999"
    assert res_sentinel_reset[1].deed_token == ""  # 20150315 reserved via normalized redacted_acq_dts!

    # 2c. Untokened conveyance date `(dos, "")` passed directly in `deeds` splits tenures without `all_deed_dates`:
    untokened_split = consolidate_account_tenures(
        [
            RollOwnerSnapshot(year=2015, owner_name="MORRIS LAURA"),
            RollOwnerSnapshot(year=2016, owner_name="MORRIS LAURA M"),
        ],
        deeds=[("20160301", "")],
        active_tax_year=2016,
    )
    assert len(untokened_split) == 2

    # 3. Secondary owners.txt co-owner (ln_num >= 2) confidential flag + placeholder filtering + zero mail_addr leak
    z2025 = _write_custom_roll_zip(
        tmp_path / "rolls" / "2025" / "Real_acct_owner.zip",
        real_rows=[
            # Account 1: mailto looks public, ln_num=1 looks public, but ln_num=2 is PROTECTED OWNER -> confidential!
            ["0620000000001", "2025", "HARPER DANIEL", "9999 SECRET LN", "HOUSTON", "TX", "77009", "101 KEY ST", "77009", "A1", "05/01/2018"],
            # Account 2: placeholder token only -> omitted
            ["0620000000002", "2025", "PUBLIC / UNLISTED", "500 MAIN ST", "HOUSTON", "TX", "77002", "103 KEY ST", "77009", "A1", ""],
            # Account 3: ordinary homestead with sensitive mailing address that must NEVER enter owner_history
            ["0620000000003", "2025", "DIAZ ELENA", "742 PRIVATE PO BOX", "AUSTIN", "TX", "78701", "105 KEY ST", "77009", "A1", "01/02/1988"],
        ],
        owners_rows=[
            ["0620000000001", "1", "HARPER DANIEL", ""],
            ["0620000000001", "2", "PROTECTED OWNER", ""],
            ["0620000000002", "1", "OWNER OF RECORD", ""],
            ["0620000000003", "1", "DIAZ ELENA", ""],
        ],
    )
    yr_parsed, cnt_parsed, snaps_map, _deeds_map = parse_single_roll_snapshot(
        z2025, 2025, min_parsed_accounts=3, active_tax_year=2025
    )
    assert yr_parsed == 2025
    assert cnt_parsed == 3
    assert snaps_map["0620000000001"] == ("", True, "20180501")
    assert "0620000000002" not in snaps_map
    assert snaps_map["0620000000003"] == ("DIAZ ELENA", False, "<=1988")

    out_data = tmp_path / "data"
    (out_data / "permits").mkdir(parents=True)
    (out_data / "permits" / "permits_062.json").write_text('{"0620000000003":{}}', encoding="utf-8")
    build_owner_history_shards(
        roll_zips_by_year={2025: z2025},
        data_dir=out_data,
        permits_dir=out_data / "permits",
        cache_dir=tmp_path / "cache",
        app_dir=None,
        target_hcads={"0620000000001", "0620000000002", "0620000000003"},
        shard_prefixes=["062"],
        active_tax_year=2025,
        min_parsed_accounts=3,
        update_manifest=False,
    )
    inflated = gzip.decompress((out_data / "owner_history" / "owners_062.json.gz").read_bytes()).decode("utf-8")
    shard_doc = json.loads(inflated)
    assert "0620000000001" not in shard_doc
    assert "0620000000002" not in shard_doc
    assert shard_doc["0620000000003"] == "2025~<=1988~~DIAZ ELENA"
    assert "9999 SECRET LN" not in inflated
    assert "742 PRIVATE PO BOX" not in inflated

    # 4. Cross-shard parity: permits_<prefix3>.json, buildings.geojson, and portfolios must mask
    #    owner_name, m, co, pid, and residential d when secondary owners.txt has PROTECTED OWNER
    from atlas_pipeline.build_ownership_and_history import (
        build_ownership_graph,
        stamp_ownership_onto_geojsonseq_and_core,
    )

    z2026_parity = _write_custom_roll_zip(
        tmp_path / "rolls" / "2026_parity" / "Real_acct_owner.zip",
        real_header=[
            "acct",
            "yr",
            "mailto",
            "mail_addr_1",
            "mail_addr_2",
            "mail_city",
            "mail_state",
            "mail_zip",
            "site_addr_1",
            "state_class",
            "lgl_1",
            "tot_mkt_val",
            "yr_impr",
        ],
        real_rows=[
            ["0620000000091", "2026", "HARPER DANIEL", "C/O PRIVATE AGENT", "9999 SECRET LN", "HOUSTON", "TX", "77009", "101 KEY ST", "F1", "NORHILL", "500000", "1930"],
            ["0620000000092", "2026", "HARPER DANIEL & PROTECTED OWNER", "C/O PRIVATE AGENT", "8888 SECRET LN", "HOUSTON", "TX", "77009", "103 KEY ST", "F1", "NORHILL", "500000", "1930"],
            ["0620000000093", "2026", "HARPER DANIEL", "7155 OLD KATY RD STE N260", "", "HOUSTON", "TX", "77024", "105 KEY ST", "A1", "NORHILL", "500000", "1930"],
            ["0620000000094", "2026", "PUBLIC HUB A LLC", "7155 OLD KATY RD STE N260", "", "HOUSTON", "TX", "77024", "107 KEY ST", "A1", "NORHILL", "500000", "1930"],
            ["0620000000095", "2026", "PUBLIC HUB B LLC", "7155 OLD KATY RD STE N260", "", "HOUSTON", "TX", "77024", "109 KEY ST", "A1", "NORHILL", "500000", "1930"],
            ["0620000000096", "2026", "PUBLIC HUB C LLC", "7155 OLD KATY RD STE N260", "", "HOUSTON", "TX", "77024", "111 KEY ST", "A1", "NORHILL", "500000", "1930"],
        ],
        owners_rows=[
            ["0620000000091", "1", "HARPER DANIEL", ""],
            ["0620000000091", "2", "PROTECTED OWNER", ""],
            ["0620000000092", "1", "HARPER DANIEL", ""],
            ["0620000000093", "1", "HARPER DANIEL", ""],
            ["0620000000093", "2", "PROTECTED OWNER", ""],
        ],
        deeds_rows=[
            ["0620000000093", "04/01/2020", "2020", "RP-2020-123456", "1"],
        ],
    )
    parity_data_dir = tmp_path / "parity_data"
    parity_permits_dir = parity_data_dir / "permits"
    parity_permits_dir.mkdir(parents=True)
    (parity_permits_dir / "permits_062.json").write_text(
        json.dumps(
            {
                "0620000000091": {"y": 1930},
                "0620000000092": {"y": 1930},
                "0620000000093": {"y": 1930},
            }
        ),
        encoding="utf-8",
    )
    (parity_data_dir / "buildings.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "hcad_num": "0620000000093",
                            "address": "105 KEY ST",
                            "owner": "HARPER DANIEL",
                            "own_tier": "regional_portfolio",
                            "use_category": "Residential",
                        },
                        "geometry": {"type": "Point", "coordinates": [-95.38, 29.79]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    p_graph = build_ownership_graph(
        z2026_parity,
        tax_year=2026,
        comptroller_cache_path=tmp_path / "parity_comptroller.json",
        include_owners_table=True,
        include_permits_table=False,
    )
    assert p_graph["records"]["0620000000091"].owner_name == "CONFIDENTIAL OWNER"
    assert p_graph["records"]["0620000000091"].own_tier == "anonymity_shield"
    assert p_graph["records"]["0620000000093"].owner_name == "CONFIDENTIAL OWNER"
    assert p_graph["records"]["0620000000093"].own_tier == "homestead"
    assert p_graph["records"]["0620000000093"].is_protected_homestead is True
    for port_v in p_graph["portfolios"].values():
        assert "0620000000093" not in port_v["accts"]
        assert "HARPER" not in port_v["primary_entity"]

    stamp_ownership_onto_geojsonseq_and_core(
        p_graph,
        aligned_dir=tmp_path / "empty_aligned",
        quadrant_dir=tmp_path / "empty_quad",
        data_dir=parity_data_dir,
    )
    stamped_blds = json.loads((parity_data_dir / "buildings.geojson").read_text(encoding="utf-8"))
    assert stamped_blds["features"][0]["properties"]["owner"] == "CONFIDENTIAL OWNER"
    assert stamped_blds["features"][0]["properties"]["own_tier"] == "homestead"

    enrich_permit_shards_with_ownership_and_deeds(
        z2026_parity,
        permits_dir=parity_permits_dir,
        data_dir=parity_data_dir,
        app_dir=tmp_path / "parity_app",
        tax_year=2026,
    )
    parity_doc = json.loads((parity_permits_dir / "permits_062.json").read_text(encoding="utf-8"))
    for p_ac in ("0620000000091", "0620000000092", "0620000000093"):
        assert "m" not in parity_doc[p_ac], f"Leaked m for {p_ac}: {parity_doc[p_ac]}"
        assert "co" not in parity_doc[p_ac], f"Leaked co for {p_ac}: {parity_doc[p_ac]}"
        assert "pid" not in parity_doc[p_ac], f"Leaked pid for {p_ac}: {parity_doc[p_ac]}"
    assert "d" not in parity_doc["0620000000093"]
    assert "ot" not in parity_doc["0620000000093"]


def test_ut3_entity_tenure_consolidation_jitter_vs_series_stopwords_and_conveyances() -> None:
    """UT-3: Merge clerical/truncation/DBA jitter while preserving series LLCs, stopwords, and real conveyances."""
    # 1. Clerical jitter: L.L.C. vs LLC, C/O & %, ET UX, middle initial, >=18-char truncation + DBA retention + earliest acq_dt
    jitter_llc = consolidate_account_tenures(
        [
            RollOwnerSnapshot(2005, "BAYOU PRESERVATION HERITA", acq_dt="20010914"),
            RollOwnerSnapshot(2006, "BAYOU PRESERVATION HERITAGE FOUNDATION (DBA HERITAGE HALL)", acq_dt="20010914"),
            RollOwnerSnapshot(2007, "BAYOU PRESERVATION HERITAGE FOUNDATION % TAX DEPT", acq_dt="20090101"),
        ],
        active_tax_year=2007,
    )
    assert len(jitter_llc) == 1
    assert jitter_llc[0].start_year == 2005 and jitter_llc[0].end_year == 2007
    # Earliest acq_dt <= start_year (20010914) preserved rather than impossible 20090101 > 2005, and DBA preserved!
    assert jitter_llc[0].acq_dt == "20010914"
    assert "(DBA HERITAGE HALL)" in jitter_llc[0].owner_name

    jitter_person = consolidate_account_tenures(
        [
            RollOwnerSnapshot(2010, "VANCE ELEANOR ET UX", acq_dt="20091103"),
            RollOwnerSnapshot(2011, "VANCE ELEANOR M", acq_dt="20091103"),
            RollOwnerSnapshot(2012, "VANCE ELEANOR M C/O PROPERTY MGMT", acq_dt="20091103"),
        ],
        active_tax_year=2012,
    )
    assert len(jitter_person) == 1
    assert jitter_person[0].start_year == 2010 and jitter_person[0].end_year == 2012
    assert jitter_person[0].acq_dt == "20091103"

    assert are_same_owner_tenure("ACME BAYOU L.L.C.", "20150101", 2015, "ACME BAYOU LLC", "20150101", 2016) is True

    # 2. Must NOT collapse numbered Series LLCs, Roman numerals, street-number LLCs, or stopword-only overlaps
    distinct_pairs = [
        ("TULANE EQUITY SERIES 1 LLC", "TULANE EQUITY SERIES 12 LLC"),
        ("TULANE EQUITY SERIES 1 LLC", "TULANE EQUITY SERIES 2 LLC"),
        ("TULANE EQUITY SERIES A LLC", "TULANE EQUITY SERIES B LLC"),
        ("TULANE EQUITY SERIES 1", "TULANE EQUITY SERIES 12"),
        ("HEIGHTS VENTURES I LP", "HEIGHTS VENTURES II LP"),
        ("HEIGHTS VENTURES PHASE I LP", "HEIGHTS VENTURES PHASE II LP"),
        ("BAYOU FUND I LLC", "BAYOU FUND II LLC"),
        ("BAYOU FUND I LLC", "BAYOU FUND V LLC"),
        ("101 MAIN STREET LLC", "105 MAIN STREET LLC"),
        ("HOUSTON HEIGHTS PROPERTIES LLC", "HOUSTON HEIGHTS HOLDINGS LLC"),
        ("TEXAS LIVING TRUST", "TEXAS FAMILY TRUST"),
        ("GARCIA CARLOS", "GARCIA CARLOS LLC"),
    ]
    for name_a, name_b in distinct_pairs:
        assert (
            are_same_owner_tenure(name_a, "20180101", 2019, name_b, "20180101", 2020) is False
        ), f"Falsely merged {name_a!r} and {name_b!r}"

    # 3. Deed-backed intra-family / trust conveyances remain separate tenures paired with their Clerk File Numbers
    family_chain = consolidate_account_tenures(
        [
            RollOwnerSnapshot(2014, "SMITH CHRISTOPHER", acq_dt="20020501"),
            RollOwnerSnapshot(2015, "SMITH CHRISTOPHER", acq_dt="20020501"),
            RollOwnerSnapshot(2016, "SMITH CHRISTOPHER JR", acq_dt="20151118"),
            RollOwnerSnapshot(2017, "SMITH CHRISTOPHER JR LIVING TRUST", acq_dt="20170210"),
        ],
        deeds=[
            ("20151118", "20150523456"),
            ("20170210", "R2017-065432"),
        ],
        active_tax_year=2017,
    )
    assert [(t.start_year, t.end_year, t.owner_name, t.deed_token) for t in family_chain] == [
        (2017, 2017, "SMITH CHRISTOPHER JR LIVING TRUST", "R2017-065432"),
        (2016, 2016, "SMITH CHRISTOPHER JR", "20150523456"),
        (2014, 2015, "SMITH CHRISTOPHER", ""),
    ]


def test_ut4_date_sentinel_normalization_and_exclusive_1to1_deed_pairing() -> None:
    """UT-4: 1988-01-02 -> '<=1988', 1899/1900 null sentinels -> '', genuine 1900 dates kept, and 1:1 deed pairing."""
    assert normalize_acquisition_date_token("01/02/1988") == HCAD_MAINFRAME_SENTINEL
    assert normalize_acquisition_date_token("1988-01-02 00:00:00") == HCAD_MAINFRAME_SENTINEL
    assert normalize_acquisition_date_token("19880102") == HCAD_MAINFRAME_SENTINEL
    # Adjacent genuine January 1988 dates are NOT the mainframe conversion sentinel
    assert normalize_acquisition_date_token("01/01/1988") == "19880101"
    assert normalize_acquisition_date_token("01/03/1988") == "19880103"

    for bad_dt in (
        "",
        "0",
        "00000000",
        "00/00/0000",
        "12/30/1899",
        "12/31/1899",
        "1899-12-30",
        "01/01/1900",
        "01/02/1900",
        "1900-01-02",
        "NULL",
        "N/A",
        "05/01/2099",
    ):
        assert normalize_acquisition_date_token(bad_dt, max_year=2026) == "", f"Failed sentinel {bad_dt!r}"

    assert normalize_acquisition_date_token("06/15/1900") == "19000615"
    assert normalize_acquisition_date_token("01/03/1900") == "19000103"

    # Partial YYYY0000 deeds (from null DOS + clerk_yr) + exact YYYYMMDD (+-120d) + 1:1 exclusive token assignment
    snaps = [
        RollOwnerSnapshot(2005, "ORIGINAL OWNER", acq_dt="01/02/1988"),
        RollOwnerSnapshot(2008, "SECOND BUYER", acq_dt=""),
        RollOwnerSnapshot(2009, "SECOND BUYER", acq_dt=""),
        RollOwnerSnapshot(2018, "THIRD BUYER", acq_dt="20180410"),
        RollOwnerSnapshot(2019, "FOURTH BUYER", acq_dt="20180615"),
    ]
    deeds = [
        ("20080000", "20080123456"),  # Partial YYYY0000 inside [2005, 2008] transition window
        ("20180501", "R2018-999111"),  # Within 120d of BOTH 20180410 (21d) and 20180615 (45d): must pair ONLY once!
    ]
    tenures = consolidate_account_tenures(snaps, deeds=deeds, active_tax_year=2019)
    assert len(tenures) == 4
    tokens = [t.deed_token for t in tenures if t.deed_token]
    assert len(tokens) == len(set(tokens)), f"Duplicate Clerk token assignment: {tenures}"
    by_owner = {t.owner_name: t for t in tenures}
    assert by_owner["ORIGINAL OWNER"].acq_dt == "<=1988"
    assert by_owner["ORIGINAL OWNER"].deed_token == ""
    assert by_owner["SECOND BUYER"].deed_token == "20080123456"
    assert by_owner["THIRD BUYER"].deed_token == "R2018-999111"  # Closer 21-day match wins exclusively
    assert by_owner["FOURTH BUYER"].deed_token == ""


def test_ut5_wire_encoding_delimiter_dash_sanitization_and_12_tenure_cap() -> None:
    """UT-5: Strip |, ~, control chars, U+2014/U+2013 dashes, format DBA, and cap at MAX_OWNER_TENURES_PER_PARCEL=12."""
    dirty = "BAYOU|OAKS~HOLDINGS\x07\x1f LLC\u2014SERIES A\u2013WEST % AGENT"
    clean = sanitize_wire_owner_name(dirty, "BAYOU|CAFE\u2014DOWNTOWN")
    assert "|" not in clean and "~" not in clean
    assert "\u2014" not in clean and "\u2013" not in clean
    assert "\x07" not in clean and "\x1f" not in clean
    assert clean == "BAYOU OAKS HOLDINGS LLC-SERIES A-WEST (DBA BAYOU CAFE-DOWNTOWN)"

    # Reject street-address / PO Box / placeholder aka values
    assert sanitize_wire_owner_name("ROSSI MARCO", "P.O. BOX 4412") == "ROSSI MARCO"
    assert sanitize_wire_owner_name("ROSSI MARCO", "901 COMMERCE ST") == "ROSSI MARCO"
    assert sanitize_wire_owner_name("PUBLIC / UNLISTED", "") == ""

    # 15 distinct annual owners -> capped at 12 newest-to-oldest
    many_snaps = [
        RollOwnerSnapshot(year=2010 + i, owner_name=f"DISTINCT STEWARD {chr(65 + i)} LLC", acq_dt=f"{2010 + i}0301")
        for i in range(15)
    ]
    capped = consolidate_account_tenures(many_snaps, active_tax_year=2025)
    assert len(capped) == MAX_OWNER_TENURES_PER_PARCEL == 12
    assert capped[0].start_year == 2024
    assert capped[-1].start_year == 2013

    wire = encode_compact_owner_history(
        [
            ConsolidatedOwnerTenure(2022, 2026, "ALPHA|BETA~CORP\u2014TX", "20220501", "R2022-100"),
            ConsolidatedOwnerTenure(2005, 2021, "OLDER OWNER", "<=1988", ""),
        ]
    )
    assert "\u2014" not in wire and "\u2013" not in wire
    assert wire == "2022-2026~20220501~R2022-100~ALPHA BETA CORP-TX|2005-2021~<=1988~~OLDER OWNER"


def test_mod1_mod2_tst2_single_writer_idempotency_token_parity_and_deterministic_gzip(tmp_path: Path) -> None:
    """MOD-1, MOD-2, REG-2 & TST-2: Cross-language token parity, deterministic RFC 1952 gzip, and single-writer idempotency."""
    # 1. MOD-2: 1:1 Python <-> JS confidential & placeholder token parity
    probe_samples = [
        *sorted(CONFIDENTIAL_OWNER_TOKENS),
        *sorted(ALL_PLACEHOLDER_OWNER_TOKENS),
        "CURRENT OWNER % TAX DEPT",
        "SMITH JANE & CONFIDENTIAL OWNER",
        "PROTECTED OWNER C/O ATTORNEY",
        "MORRISON CLARA",
        "PROTECTED LANDMARK PRESERVATION SOCIETY",  # Must NOT false-positive on "PROTECTED LANDMARK"!
        "CURRENT RIVER HOLDINGS LLC",
        "PUBLIC / UNLISTED",
        "Owner of Record (HCAD)",
    ]
    js_script = f"""
import {{
  CONFIDENTIAL_OWNER_TOKENS,
  PLACEHOLDER_OWNER_TOKENS,
  isConfidentialOwnerName,
  isPlaceholderOwnerName,
}} from {(REPO_ROOT / "app/js/clerkSearchHelpers.js").as_uri()!r};
const samples = {json.dumps(probe_samples)};
console.log(JSON.stringify({{
  confTokens: [...CONFIDENTIAL_OWNER_TOKENS].sort(),
  placeTokens: [...PLACEHOLDER_OWNER_TOKENS].sort(),
  results: samples.map((s) => [s, isConfidentialOwnerName(s), isPlaceholderOwnerName(s)]),
}}));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", js_script],
        capture_output=True,
        text=True,
        check=True,
    )
    js_out = json.loads(proc.stdout)
    assert set(js_out["confTokens"]) == set(CONFIDENTIAL_OWNER_TOKENS)
    assert set(js_out["placeTokens"]) == set(ALL_PLACEHOLDER_OWNER_TOKENS)
    for sample, js_conf, js_place in js_out["results"]:
        assert js_conf is is_confidential_owner_token(sample), f"Confidential mismatch for {sample!r}"
        assert js_place is is_placeholder_owner_token(sample), f"Placeholder mismatch for {sample!r}"

    # 2. TST-2: Deterministic RFC 1952 header (mtime=0, no FNAME) & dash guard
    gz_a = compress_deterministic_gzip_json({"b": "2026~~~BETA", "a": "2026~~~ALPHA"})
    gz_b = compress_deterministic_gzip_json({"a": "2026~~~ALPHA", "b": "2026~~~BETA"})
    assert gz_a == gz_b
    assert gz_a[:2] == b"\x1f\x8b"
    flg = gz_a[3]
    mtime_u32 = struct.unpack("<I", gz_a[4:8])[0]
    assert mtime_u32 == 0
    assert (flg & 0x08) == 0  # FNAME bit unset
    with pytest.raises(ValueError, match="dash"):
        compress_deterministic_gzip_json({"0010000000001": "2026~~~BAD\u2014DASH"})

    # 3. MOD-1 & REG-2: Single-writer idempotency across permits_062.json, owners_062.json.gz, and pmtiles_manifest.json
    data_dir = tmp_path / "data"
    permits_dir = data_dir / "permits"
    oh_dir = data_dir / "owner_history"
    permits_dir.mkdir(parents=True)
    oh_dir.mkdir(parents=True)

    # Seed stale owners_999.json.gz to prove stale-prefix pruning
    (oh_dir / "owners_999.json.gz").write_bytes(compress_deterministic_gzip_json({"9990000000001": "2025~~~OLD"}))

    seed_permits = {
        "0620000000010": {
            "y": 1925,
            "p": [["2026-04-15", "Residential Demolition", "DEMO HOUSE (2 OF 2)", 0]],
        },
        "0620000000011": {
            "y": 1938,
            "p": [],
        },
    }
    (permits_dir / "permits_062.json").write_text(json.dumps(seed_permits, separators=(",", ":")), encoding="utf-8")
    update_pmtiles_manifest_v3(
        data_dir / "pmtiles_manifest.json",
        total_buildings=2,
        building_shards=["houston_buildings_nw_e.pmtiles"],
        data_tax_year=2026,
        cache_bust=ACTIVE_CACHE_BUST,
    )

    z2025 = _write_custom_roll_zip(
        tmp_path / "cache" / "rolls" / "2025" / "Real_acct_owner.zip",
        real_rows=[
            ["0000000000099", "2025", "CURRENT OWNER", "1 MAIN ST", "HOUSTON", "TX", "77002", "1 MAIN ST", "77002", "A1", "01/02/1988"],
            ["0620000000010", "2025", "PRIOR FAMILY", "120 KEY ST", "HOUSTON", "TX", "77009", "120 KEY ST", "77009", "A1", "03/10/1998"],
            ["0620000000011", "2025", "HOMESTEAD OWNER", "122 KEY ST", "HOUSTON", "TX", "77009", "122 KEY ST", "77009", "A1", "01/02/1988"],
        ],
        owners_rows=[
            ["0000000000099", "1", "CURRENT OWNER", ""],
            ["0620000000010", "1", "PRIOR FAMILY", ""],
            ["0620000000011", "1", "HOMESTEAD OWNER", ""],
        ],
    )
    z2026 = _write_custom_roll_zip(
        tmp_path / "cache" / "rolls" / "2026" / "Real_acct_owner.zip",
        real_header=[
            "acct",
            "yr",
            "mailto",
            "mail_addr_1",
            "mail_addr_2",
            "mail_city",
            "mail_state",
            "mail_zip",
            "site_addr_1",
            "site_addr_3",
            "state_class",
            "new_own_dt",
        ],
        real_rows=[
            ["0000000000099", "2026", "CURRENT OWNER", "1 MAIN ST", "", "HOUSTON", "TX", "77002", "1 MAIN ST", "77002", "A1", "01/02/1988"],
            ["0620000000010", "2026", "TEARDOWN BUILDERS LLC", "C/O ASSET MGMT", "800 CORP PKWY", "DALLAS", "TX", "75201", "120 KEY ST", "77009", "A1", "02/01/2026"],
            ["0620000000011", "2026", "HOMESTEAD OWNER", "122 KEY ST", "", "HOUSTON", "TX", "77009", "122 KEY ST", "77009", "A1", "01/02/2026"],
        ],
        owners_rows=[
            ["0000000000099", "1", "CURRENT OWNER", ""],
            ["0620000000010", "1", "TEARDOWN BUILDERS LLC", ""],
            ["0620000000011", "1", "HOMESTEAD OWNER", ""],
        ],
        deeds_rows=[
            ["0620000000010", "02/01/2026", "2026", "RP-2026-100200", "1"],
        ],
        permits_rows=[
            ["0620000000010", "1", "DM", "Residential Demolition", "DEMO HOUSE (2 OF 2)", "04/15/2026", "2026"],
        ],
    )

    permits_sha_before_oh = hashlib.sha256((permits_dir / "permits_062.json").read_bytes()).hexdigest()
    oh_res_1 = build_owner_history_shards(
        roll_zips_by_year={2025: z2025, 2026: z2026},
        data_dir=data_dir,
        permits_dir=permits_dir,
        cache_dir=tmp_path / "cache",
        app_dir=None,
        target_hcads={"0000000000099", "0620000000010", "0620000000011"},
        shard_prefixes=["000", "062"],
        active_tax_year=2026,
        min_parsed_accounts=2,
        update_manifest=True,
    )
    # permits_062.json was NOT touched by build_owner_history_shards, stale owners_999.json.gz was pruned,
    # and all-confidential prefix "000" produced zero empty shard files!
    assert hashlib.sha256((permits_dir / "permits_062.json").read_bytes()).hexdigest() == permits_sha_before_oh
    assert oh_res_1["pruned_stale_shards"] == 1
    assert oh_res_1["owner_history_prefixes"] == ["062"]
    assert not (oh_dir / "owners_999.json.gz").exists()
    assert not (oh_dir / "owners_000.json.gz").exists()
    oh_sha_1 = hashlib.sha256((oh_dir / "owners_062.json.gz").read_bytes()).hexdigest()

    # Re-running build_owner_history_shards produces identical SHA-256 bytes
    build_owner_history_shards(
        roll_zips_by_year={2025: z2025, 2026: z2026},
        data_dir=data_dir,
        permits_dir=permits_dir,
        cache_dir=tmp_path / "cache",
        app_dir=None,
        target_hcads={"0000000000099", "0620000000010", "0620000000011"},
        shard_prefixes=["000", "062"],
        active_tax_year=2026,
        min_parsed_accounts=2,
        update_manifest=True,
    )
    assert hashlib.sha256((oh_dir / "owners_062.json.gz").read_bytes()).hexdigest() == oh_sha_1

    # Running enrich_permit_shards_with_ownership_and_deeds twice + update_pmtiles_manifest_v3 preserves everything idempotently
    enrich_permit_shards_with_ownership_and_deeds(
        z2026, permits_dir=permits_dir, data_dir=data_dir, app_dir=tmp_path / "app", tax_year=2026
    )
    permits_after_1 = (permits_dir / "permits_062.json").read_bytes()
    enrich_permit_shards_with_ownership_and_deeds(
        z2026, permits_dir=permits_dir, data_dir=data_dir, app_dir=tmp_path / "app", tax_year=2026
    )
    permits_after_2 = (permits_dir / "permits_062.json").read_bytes()
    assert permits_after_1 == permits_after_2
    assert hashlib.sha256((oh_dir / "owners_062.json.gz").read_bytes()).hexdigest() == oh_sha_1

    p_doc = json.loads(permits_after_2.decode("utf-8"))
    # Homestead parcel 0620000000011 omits mailing address "m", while LLC teardown parcel computes flip days (73 days)
    assert "m" not in p_doc["0620000000011"]
    assert p_doc["0620000000010"]["flip"] == 73
    assert p_doc["0620000000010"]["m"] == "C/O ASSET MGMT - 800 CORP PKWY, DALLAS, TX 75201"
    assert "oh" not in p_doc["0620000000010"]

    # MOD-1 commutativity near PERMIT_ENRICHMENT_SOFT_MAX_BYTES: owner_history/*.json.gz inside app/public/data/
    # must NOT count against permit_budget_base_bytes or strip "m"/"co" differently based on build order.
    comm_app = tmp_path / "comm_app"
    comm_data = comm_app / "public" / "data"
    comm_permits = comm_data / "permits"
    comm_oh = comm_data / "owner_history"
    comm_permits.mkdir(parents=True)
    comm_oh.mkdir(parents=True)
    (comm_permits / "permits_062.json").write_text(
        json.dumps(seed_permits, separators=(",", ":")), encoding="utf-8"
    )
    import atlas_pipeline.build_ownership_and_history as _boh_mod

    orig_soft_cap = _boh_mod.PERMIT_ENRICHMENT_SOFT_MAX_BYTES
    try:
        # Set soft cap just above permits-only bytes (~350 B) so a 2 KB owner_history file would trip it if not subtracted
        _boh_mod.PERMIT_ENRICHMENT_SOFT_MAX_BYTES = 1200
        enrich_permit_shards_with_ownership_and_deeds(
            z2026, permits_dir=comm_permits, data_dir=comm_data, app_dir=comm_app, tax_year=2026
        )
        bytes_without_oh = (comm_permits / "permits_062.json").read_bytes()
        (comm_oh / "owners_062.json.gz").write_bytes(b"\x1f\x8b" + (b"x" * 2048))
        enrich_permit_shards_with_ownership_and_deeds(
            z2026, permits_dir=comm_permits, data_dir=comm_data, app_dir=comm_app, tax_year=2026
        )
        bytes_with_oh = (comm_permits / "permits_062.json").read_bytes()
        assert bytes_with_oh == bytes_without_oh
        assert json.loads(bytes_with_oh.decode("utf-8"))["0620000000010"]["m"] == "C/O ASSET MGMT - 800 CORP PKWY, DALLAS, TX 75201"
    finally:
        _boh_mod.PERMIT_ENRICHMENT_SOFT_MAX_BYTES = orig_soft_cap

    # Manifest update without owner_history kwargs preserves owner_history_* keys!
    m_after = update_pmtiles_manifest_v3(
        data_dir / "pmtiles_manifest.json",
        total_buildings=2,
        building_shards=["houston_buildings_nw_e.pmtiles"],
        data_tax_year=2026,
    )
    assert m_after["owner_history_prefixes"] == ["062"]
    assert m_after["owner_history_span"] == [2025, 2026]
    assert m_after["owner_history_accounts"] == 2

    # Dry-run AnchorRule sync on live repository reports zero out-of-sync files
    dry = sync_repo_tax_year_and_cache_bust(
        REPO_ROOT,
        tax_year=2026,
        cache_bust=ACTIVE_CACHE_BUST,
        dry_run=True,
    )
    assert dry["modified_files"] == []


def test_it1_and_reg5_case_insensitive_archives_account_floor_and_staged_annual_refresh(tmp_path: Path) -> None:
    """IT-1 & REG-5: Case-insensitive ZIP members/headers, floor failure gate, and staged annual-refresh hook."""
    # 1. Nested case-insensitive member names & mixed-case headers
    nested_zip = _write_custom_roll_zip(
        tmp_path / "nested" / "Real_acct_owner.zip",
        real_member_name="Export_2007/Real_Acct.TXT",
        owners_member_name="Export_2007/OWNERS.TXT",
        deeds_member_name="Export_2007/Deeds.TXT",
        real_header=["Acct", "YR", "MailTo", "New_Own_Dt"],
        real_rows=[
            ["0010020000016", "2007", "COMMERCE TOWER LP", "04/18/2006"],
            ["0152300000001", "2007", "SMITH TOWER CORP", "01/02/1988"],
        ],
        owners_header=["ACCT", "LN_NUM", "NAME", "AKA"],
        owners_rows=[
            ["0010020000016", "1", "COMMERCE TOWER LP", "COMMERCE BLDG"],
            ["0152300000001", "1", "SMITH TOWER CORP", ""],
        ],
        deeds_header=["Acct", "DOS", "Clerk_Yr", "Clerk_ID"],
        deeds_rows=[
            ["0010020000016", "04/18/2006", "2006", "20060198765"],
        ],
    )
    yr, total_cnt, snaps, deeds = parse_single_roll_snapshot(
        nested_zip, 2007, min_parsed_accounts=2, active_tax_year=2026
    )
    assert yr == 2007 and total_cnt == 2
    assert snaps["0010020000016"] == ("COMMERCE TOWER LP (DBA COMMERCE BLDG)", False, "20060418")
    assert snaps["0152300000001"] == ("SMITH TOWER CORP", False, "<=1988")
    assert deeds["0010020000016"] == [("20060418", "20060198765")]

    with pytest.raises(ValueError, match="fell below floor"):
        parse_single_roll_snapshot(nested_zip, 2007, min_parsed_accounts=10, active_tax_year=2026)

    # 2. REG-5a: Single-year environment after 2027 promotion (cache/Real_acct_owner.zip == rolls/2027/Real_acct_owner.zip)
    single_cache = tmp_path / "single_cache"
    z2027_single = _write_custom_roll_zip(
        single_cache / "rolls" / "2027" / "Real_acct_owner.zip",
        real_header=["acct", "yr", "mailto", "new_own_dt"],
        real_rows=[["0010020000016", "2027", "NEW TOWER LLC", "03/01/2027"]],
    )
    # Simulate promoted top-level copy of 2027 archive in cache_dir/Real_acct_owner.zip
    (single_cache / "Real_acct_owner.zip").write_bytes(z2027_single.read_bytes())
    resolved_single = resolve_annual_roll_zip_paths(single_cache, start_year=2005, end_year=2027)
    assert list(resolved_single.keys()) == [2027]  # Never misattributed to 2026!

    staged_single = tmp_path / "staged_single"
    staged_single.mkdir()
    skip_res = maybe_rebuild_owner_history_for_staged_roll(
        cache_dir=single_cache,
        staged_roll_zip=z2027_single,
        tax_year=2027,
        staged_data_dir=staged_single,
        min_parsed_accounts=1,
    )
    assert skip_res["skipped"] is True
    assert skip_res["reason"] == "historical_roll_archives_absent"
    assert skip_res["available_rolls"] == [2027]

    # 3. REG-5b: Production cache layout (2005 under rolls/2005 + active 2026 at top-level cache/Real_acct_owner.zip
    #    + staged 2027 in rolls/2027) via StagedRollPromotion.populate_cache_view preserves 2026!
    from atlas_pipeline.schema import RollContext
    from atlas_pipeline.update_annual_hcad_roll import StagedRollPromotion

    multi_cache = tmp_path / "multi_cache"
    _write_custom_roll_zip(
        multi_cache / "rolls" / "2005" / "Real_acct_owner.zip",
        real_header=["acct", "yr", "mailto", "new_own_dt"],
        real_rows=[["0010020000016", "2005", "LEGACY LANDMARK LP", "01/02/1988"]],
    )
    _write_custom_roll_zip(
        multi_cache / "Real_acct_owner.zip",
        real_header=["acct", "yr", "mailto", "new_own_dt"],
        real_rows=[["0010020000016", "2026", "INTERIM 2026 STEWARD LP", "03/10/2026"]],
        deeds_rows=[["0010020000016", "03/10/2026", "2026", "RP-2026-100200", "1"]],
    )
    z2027_multi = _write_custom_roll_zip(
        multi_cache / "rolls" / "2027" / "Real_acct_owner.zip",
        real_header=["acct", "yr", "mailto", "new_own_dt"],
        real_rows=[["0010020000016", "2027", "MODERN STEWARD LLC", "05/12/2027"]],
        deeds_rows=[["0010020000016", "05/12/2027", "2027", "RP-2027-888111", "1"]],
    )
    live_out_multi = tmp_path / "live_out_multi"
    live_out_multi.mkdir(parents=True)
    ctx_2027 = RollContext(
        tax_year=2027,
        cache_bust="20270901a",
        cache_dir=multi_cache,
        output_dir=live_out_multi,
    )
    promoter = StagedRollPromotion(ctx_2027)
    promoter.reset_staging_dirs()
    try:
        promoter.populate_cache_view(set())
        (promoter.staged_data / "permits").mkdir(parents=True, exist_ok=True)
        (promoter.staged_data / "permits" / "permits_001.json").write_text(
            '{"0010020000016":{}}', encoding="utf-8"
        )
        update_pmtiles_manifest_v3(
            promoter.staged_data / "pmtiles_manifest.json",
            total_buildings=1,
            building_shards=["houston_buildings_nw_e.pmtiles"],
            data_tax_year=2027,
            cache_bust="20270901a",
        )
        built_res = maybe_rebuild_owner_history_for_staged_roll(
            cache_dir=promoter.staged_cache_view,
            staged_roll_zip=z2027_multi,
            tax_year=2027,
            staged_data_dir=promoter.staged_data,
            min_parsed_accounts=1,
        )
        assert built_res.get("skipped") is not True
        assert built_res["years"] == [2005, 2026, 2027]
        assert built_res["owner_history_span"] == [2005, 2027]
        assert built_res["owner_history_prefixes"] == ["001"]
        built_shard = json.loads(
            gzip.decompress(
                (promoter.staged_data / "owner_history" / "owners_001.json.gz").read_bytes()
            ).decode("utf-8")
        )
        assert built_shard["0010020000016"] == (
            "2027~20270512~R2027-888111~MODERN STEWARD LLC|"
            "2026~20260310~R2026-100200~INTERIM 2026 STEWARD LP|"
            "2005~<=1988~~LEGACY LANDMARK LP"
        )
        staged_manifest = json.loads(
            (promoter.staged_data / "pmtiles_manifest.json").read_text(encoding="utf-8")
        )
        assert staged_manifest["owner_history_span"] == [2005, 2027]
        assert staged_manifest["owner_history_prefixes"] == ["001"]

        # Verify empty owner_history_prefixes=[] serializes cleanly while preserving total_buildings/building_shards
        empty_m = update_pmtiles_manifest_v3(
            promoter.staged_data / "pmtiles_manifest.json",
            owner_history_prefixes=[],
            owner_history_accounts=0,
        )
        assert empty_m["owner_history_prefixes"] == []
        assert empty_m["owner_history_accounts"] == 0
        assert empty_m["total_buildings"] == 1
        assert empty_m["building_shards"] == ["houston_buildings_nw_e.pmtiles"]
    finally:
        if promoter.staged_root.exists():
            import shutil

            shutil.rmtree(promoter.staged_root, ignore_errors=True)


def test_commercial_pobox_cluster_confidential_a1_retains_anonymity_shield(tmp_path: Path) -> None:
    """Verify CURRENT OWNER A1 parcels in >=3-parcel non-situs PO BOX clusters retain anonymity_shield when owners.txt repeats CURRENT OWNER."""
    from atlas_pipeline.build_ownership_and_history import build_ownership_graph

    roll_zip = _write_custom_roll_zip(
        tmp_path / "Real_acct_owner.zip",
        real_header=[
            "acct",
            "yr",
            "mailto",
            "mail_addr_1",
            "mail_addr_2",
            "mail_city",
            "mail_state",
            "mail_zip",
            "site_addr_1",
            "state_class",
            "lgl_1",
            "tot_mkt_val",
            "yr_impr",
        ],
        real_rows=[
            [
                "0373010000002",
                "2026",
                "CURRENT OWNER",
                "PO BOX 7135",
                "",
                "HOUSTON",
                "TX",
                "77248-7135",
                "710 WOODLAND ST",
                "A1",
                "LT 2 BLK 36",
                "505000",
                "1930",
            ],
            [
                "0200890000041",
                "2026",
                "CURRENT OWNER",
                "PO BOX 7135",
                "",
                "HOUSTON",
                "TX",
                "77248-7135",
                "900 HEIGHTS BLVD",
                "F1",
                "TR 1",
                "950000",
                "1965",
            ],
            [
                "0201950000025",
                "2026",
                "CURRENT OWNER",
                "PO BOX 7135",
                "",
                "HOUSTON",
                "TX",
                "77248-7135",
                "902 HEIGHTS BLVD",
                "F1",
                "TR 2",
                "910000",
                "1968",
            ],
        ],
        owners_rows=[
            ["0373010000002", "1", "CURRENT OWNER", "", "1.0000"],
            ["0200890000041", "1", "CURRENT OWNER", "", "1.0000"],
            ["0201950000025", "1", "CURRENT OWNER", "", "1.0000"],
        ],
    )
    graph = build_ownership_graph(
        roll_zip,
        tax_year=2026,
        comptroller_cache_path=tmp_path / "comptroller_empty.json",
        include_owners_table=True,
        include_permits_table=False,
    )
    from atlas_pipeline.build_ownership_and_history import (
        AccountOwnershipRecord,
        build_account_enrichment_fields,
    )

    rec = graph["records"]["0373010000002"]
    assert rec.own_tier == "anonymity_shield"
    assert rec.is_sec_25_025_confidential is True
    assert rec.owner_name == "CONFIDENTIAL OWNER"
    assert rec.billing_key == ""
    assert rec.display_mail == ""
    assert rec.co_owners == []

    rec.latest_demo_permit_date = "20220301"
    enr = build_account_enrichment_fields(rec, [("R2022-49567", "20220120")])
    assert enr.get("conf") == 1
    assert enr.get("ot") == "anonymity_shield"
    for forbidden_key in ("d", "flip", "pid", "m", "co"):
        assert forbidden_key not in enr, f"Leaked {forbidden_key!r} on confidential anonymity_shield account"

    homestead_conf = AccountOwnershipRecord(
        acct="0071300000001",
        own_tier="homestead",
        port_id="port_should_omit",
        owner_name="CONFIDENTIAL OWNER",
        is_protected_homestead=True,
        is_sec_25_025_confidential=True,
        latest_demo_permit_date="20100515",
    )
    enr_hs = build_account_enrichment_fields(homestead_conf, [("20100120999", "20100225")])
    assert enr_hs == {"conf": 1}


def test_pass3_qa_regressions_forward_taint_portfolios_soft_prune_and_acyclic_imports(
    tmp_path: Path,
) -> None:
    """Verify QA Pass 3 regressions: forward <=1988/blank taint, F1 confidential portfolios, Pass 4 dissolution, soft-prune obsolete_live_rels, and acyclic imports."""
    import ast
    from atlas_pipeline.build_ownership_and_history import (
        build_account_enrichment_fields,
        build_and_write_ownership_portfolios_catalog,
        build_ownership_graph,
    )
    from atlas_pipeline.update_annual_hcad_roll import (
        _compute_projected_app_size_bytes,
        prune_optional_enrichment_for_soft_budget,
    )

    # 1. Finding 2: Forward intra-tenure taint when confidential year resets new_own_dt to <=1988 or ''
    #    followed by clerical unmasking before a later bona fide resale.
    for unmasked_acq in ("20150315", ""):
        for conf_reset_acq in ("01/02/1988", ""):
            snaps = [
                RollOwnerSnapshot(year=2015, owner_name="JANE ROE", is_confidential=False, acq_dt="03/15/2015"),
                RollOwnerSnapshot(year=2016, owner_name="", is_confidential=True, acq_dt=conf_reset_acq),
                RollOwnerSnapshot(year=2017, owner_name="JANE ROE", is_confidential=False, acq_dt=unmasked_acq),
                RollOwnerSnapshot(year=2022, owner_name="MODERN BUYER LLC", is_confidential=False, acq_dt="06/01/2022"),
            ]
            tenures = consolidate_account_tenures(
                snaps,
                deeds=[("20150315", "RP-2015-100200"), ("20220601", "RP-2022-999999")],
                active_tax_year=2026,
            )
            assert [(t.start_year, t.end_year, t.owner_name, t.deed_token) for t in tenures] == [
                (2022, 2022, "MODERN BUYER LLC", "RP-2022-999999"),
            ], f"Leaked tainted tenure for conf_reset_acq={conf_reset_acq!r}, unmasked_acq={unmasked_acq!r}: {tenures}"

    # 2. Finding 3, Finding 5 & Finding 7b:
    #    (a) 5 commercial F1 accounts with 'HARPER DANIEL & PROTECTED OWNER' never leak into ownership_portfolios.json
    #    (b) 6 parcels owned by 'PROTECTED LANDMARK PRESERVATION SOCIETY' are NOT false-positively treated as confidential
    #    (c) 3-parcel 2-LLC billing portfolio dissolved by owners.txt ln_num=2 'CONFIDENTIAL OWNER' clears port_id on remaining accounts
    #    (d) Nested mixed-case Export_2026/Real_Acct.TXT, Owners.TXT, Deeds.TXT lookup succeeds (and missing real_acct.txt raises ValueError)
    missing_real_zip = tmp_path / "missing_real_acct.zip"
    with zipfile.ZipFile(missing_real_zip, "w") as zf_empty:
        zf_empty.writestr("owners.txt", "acct\tln_num\tname\taka\n")
    with pytest.raises(ValueError, match="real_acct.txt"):
        build_ownership_graph(
            missing_real_zip,
            tax_year=2026,
            comptroller_cache_path=tmp_path / "comptroller_empty.json",
            include_owners_table=True,
            include_permits_table=False,
        )

    real_hdr = [
        "ACCT",
        "YR",
        "MAILTO",
        "MAIL_ADDR_1",
        "MAIL_ADDR_2",
        "MAIL_CITY",
        "MAIL_STATE",
        "MAIL_ZIP",
        "SITE_ADDR_1",
        "STATE_CLASS",
        "LGL_1",
        "TOT_MKT_VAL",
        "YR_IMPR",
    ]
    real_rows: list[list[str]] = []
    # (a) 5 commercial F1 accounts sharing suite with compound confidential marker
    for i in range(1, 6):
        real_rows.append(
            [
                f"045100000000{i}",
                "2026",
                "HARPER DANIEL & PROTECTED OWNER",
                "1200 MAIN ST STE 400",
                "",
                "HOUSTON",
                "TX",
                "77002",
                f"{100 + i} COMMERCE ST",
                "F1",
                f"TR {i}",
                "800000",
                "1925",
            ]
        )
    # (b) 6 civic/preservation society accounts with distinct billing addresses ('PROTECTED LANDMARK' non-false-positive)
    for i in range(1, 7):
        real_rows.append(
            [
                f"055200000000{i}",
                "2026",
                "PROTECTED LANDMARK PRESERVATION SOCIETY",
                f"{200 + i} TRAVIS ST",
                "",
                "HOUSTON",
                "TX",
                "77002",
                f"{200 + i} TRAVIS ST",
                "F1",
                f"LT {i} BLK 4",
                "600000",
                "1912",
            ]
        )
    # (c) 3 commercial parcels (2 distinct LLCs) sharing one billing suite; parcel 1 has ln_num=2 CONFIDENTIAL OWNER
    for idx_ac, corp_nm in (
        ("0663000000001", "ALPHA HISTORIC LOFTS LLC"),
        ("0663000000002", "BETA HISTORIC LOFTS LLC"),
        ("0663000000003", "BETA HISTORIC LOFTS LLC"),
    ):
        real_rows.append(
            [
                idx_ac,
                "2026",
                corp_nm,
                "800 MILAM ST STE 900",
                "",
                "HOUSTON",
                "TX",
                "77002",
                f"{300 + int(idx_ac[-1])} MILAM ST",
                "F1",
                "LT 1 BLK 9",
                "750000",
                "1922",
            ]
        )

    nested_zip = _write_custom_roll_zip(
        tmp_path / "Nested_Mixed_Case.zip",
        real_member_name="Export_2026/Real_Acct.TXT",
        owners_member_name="Export_2026/Owners.TXT",
        deeds_member_name="Export_2026/Deeds.TXT",
        real_header=real_hdr,
        real_rows=real_rows,
        owners_header=["ACCT", "LN_NUM", "NAME", "AKA"],
        owners_rows=[
            ["0663000000001", "1", "ALPHA HISTORIC LOFTS LLC", ""],
            ["0663000000001", "2", "CONFIDENTIAL OWNER", ""],
            ["0663000000002", "1", "BETA HISTORIC LOFTS LLC", ""],
            ["0663000000003", "1", "BETA HISTORIC LOFTS LLC", ""],
        ],
    )
    nested_graph = build_ownership_graph(
        nested_zip,
        tax_year=2026,
        comptroller_cache_path=tmp_path / "comptroller_empty.json",
        include_owners_table=True,
        include_permits_table=False,
    )
    n_records = nested_graph["records"]
    for i in range(1, 6):
        c_rec = n_records[f"045100000000{i}"]
        assert c_rec.is_sec_25_025_confidential is True
        assert c_rec.port_id == ""
    for i in range(1, 7):
        soc_rec = n_records[f"055200000000{i}"]
        assert soc_rec.is_sec_25_025_confidential is False
        assert soc_rec.owner_name == "PROTECTED LANDMARK PRESERVATION SOCIETY"
    # Dissolved 3->2 portfolio must clear port_id on BOTH remaining non-confidential accounts!
    assert n_records["0663000000001"].is_sec_25_025_confidential is True
    assert n_records["0663000000001"].port_id == ""
    assert n_records["0663000000002"].port_id == ""
    assert n_records["0663000000003"].port_id == ""
    assert "pid" not in build_account_enrichment_fields(n_records["0663000000002"], [])

    port_out_path = tmp_path / "ownership_portfolios.json"
    port_doc = build_and_write_ownership_portfolios_catalog(
        nested_graph,
        aligned_dir=tmp_path / "no_aligned",
        output_path=port_out_path,
    )
    serialized_port_doc = json.dumps(port_doc)
    assert "HARPER" not in serialized_port_doc
    assert "1200 MAIN" not in serialized_port_doc
    assert "800 MILAM" not in serialized_port_doc
    assert "PROTECTED LANDMARK PRESERVATION SOCIETY" in serialized_port_doc

    # 3. Finding 4: prune_optional_enrichment_for_soft_budget adds unlinked owner_history/<shard>.json.gz to obsolete_live_rels
    live_data = tmp_path / "live_app" / "public" / "data"
    live_oh = live_data / "owner_history"
    live_oh.mkdir(parents=True)
    staged_data = tmp_path / "staged_data"
    staged_oh = staged_data / "owner_history"
    staged_oh.mkdir(parents=True)
    vacant_gz = compress_deterministic_gzip_json(
        {"9700000000001": "2010-2026~20100501~R2010-111222~" + ("VACANT LOT STEWARD LLC " * 20).strip()}
    )
    (live_oh / "owners_970.json.gz").write_bytes(vacant_gz)
    (staged_oh / "owners_970.json.gz").write_bytes(vacant_gz)
    obsolete_rels: set[str] = set()
    prune_stats = prune_optional_enrichment_for_soft_budget(
        staged_data,
        live_data,
        standing_hcads=set(),
        soft_max_bytes=10,
        obsolete_live_rels=obsolete_rels,
    )
    assert not (staged_oh / "owners_970.json.gz").exists()
    assert "owner_history/owners_970.json.gz" in obsolete_rels
    assert prune_stats["projected_bytes_after"] == 0
    projected_after, _ = _compute_projected_app_size_bytes(
        live_data,
        staged_data,
        obsolete_live_rels=obsolete_rels,
    )
    assert projected_after == 0

    # 4. Finding 6: Acyclic stdlib-only layering on build_historical_ownership_2005_2026.py
    hist_src = (
        REPO_ROOT / "pipeline" / "atlas_pipeline" / "build_historical_ownership_2005_2026.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(hist_src)
    imported_modules = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    ] + [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    internal_imports = sorted({mod for mod in imported_modules if mod.startswith("atlas_pipeline")})
    assert internal_imports == ["atlas_pipeline.schema"], (
        f"build_historical_ownership_2005_2026.py must depend strictly downward on atlas_pipeline.schema, got: {internal_imports}"
    )


def test_pass4_qa_regressions_annual_refresh_conf_taint_and_deed_reservation(
    tmp_path: Path,
) -> None:
    """Verify QA Pass 4 regressions: <=1988/admin intra-tenure taint, pre-redaction buyer deed reservation, 0000000000000 rejection, and annual refresh confidential gating."""
    from atlas_pipeline.build_ownership_and_history import build_ownership_graph
    from atlas_pipeline.update_annual_hcad_roll import (
        ArtifactSyncDelta,
        HcadAccountSnapshot,
        _stream_targeted_enrichment_records,
        reconcile_derived_building_indexes,
    )

    # 1a. Forward intra-tenure taint when unmasked post-election snapshot resets to '<=1988' (01/02/1988) or admin date without conveyance deed
    for post_unmasked_acq in ("01/02/1988", "09/10/2017", ""):
        snaps_fwd = [
            RollOwnerSnapshot(year=2015, owner_name="JANE ROE", is_confidential=False, acq_dt="03/15/2015"),
            RollOwnerSnapshot(year=2016, owner_name="", is_confidential=True, acq_dt="03/15/2015"),
            RollOwnerSnapshot(year=2017, owner_name="JANE ROE", is_confidential=False, acq_dt=post_unmasked_acq),
            RollOwnerSnapshot(year=2022, owner_name="MODERN BUYER LLC", is_confidential=False, acq_dt="06/01/2022"),
        ]
        res_fwd = consolidate_account_tenures(
            snaps_fwd,
            deeds=[("20150315", "RP-2015-100200"), ("20220601", "RP-2022-999999")],
            active_tax_year=2026,
        )
        assert [(t.start_year, t.end_year, t.owner_name, t.deed_token) for t in res_fwd] == [
            (2022, 2022, "MODERN BUYER LLC", "RP-2022-999999"),
        ], f"Forward taint leaked JANE ROE for post_unmasked_acq={post_unmasked_acq!r}: {res_fwd}"

    # 1b. Backward intra-tenure taint across '<=1988' and intra-tenure spousal/trust date updates, while preserving distinct prior seller
    snaps_bwd = [
        RollOwnerSnapshot(year=2010, owner_name="BOB SELLER", is_confidential=False, acq_dt="01/02/1988"),
        RollOwnerSnapshot(year=2012, owner_name="JANE ROE", is_confidential=False, acq_dt="01/02/1988"),
        RollOwnerSnapshot(year=2015, owner_name="JANE ROE", is_confidential=False, acq_dt="08/01/2015"),
        RollOwnerSnapshot(year=2016, owner_name="", is_confidential=True, acq_dt="08/01/2015"),
        RollOwnerSnapshot(year=2022, owner_name="MODERN BUYER LLC", is_confidential=False, acq_dt="06/01/2022"),
    ]
    res_bwd = consolidate_account_tenures(
        snaps_bwd,
        deeds=[("20150801", "RP-2015-777777"), ("20220601", "RP-2022-999999")],
        active_tax_year=2026,
    )
    assert [(t.start_year, t.end_year, t.owner_name, t.deed_token) for t in res_bwd] == [
        (2022, 2022, "MODERN BUYER LLC", "RP-2022-999999"),
        (2010, 2010, "BOB SELLER", ""),
    ]

    # 1c. Confidential buyer post-Jan-1 purchase deed (20101115) is reserved from Tier-3 pairing onto outgoing seller ALICE SELLER,
    #     while an explicit Tier-1 public match (20100301 <= 120d of ALICE SELLER's 20100215 acq_dt) IS still paired cleanly!
    snaps_deed_res = [
        RollOwnerSnapshot(year=2009, owner_name="BUILDER INC", is_confidential=False, acq_dt="01/01/2008"),
        RollOwnerSnapshot(year=2010, owner_name="ALICE SELLER", is_confidential=False, acq_dt=""),
        RollOwnerSnapshot(year=2011, owner_name="", is_confidential=True, acq_dt=""),
        RollOwnerSnapshot(year=2021, owner_name="", is_confidential=True, acq_dt=""),
        RollOwnerSnapshot(year=2022, owner_name="MODERN BUYER LLC", is_confidential=False, acq_dt="06/01/2022"),
    ]
    res_deed_res = consolidate_account_tenures(
        snaps_deed_res,
        deeds=[("20101115", "20100499999"), ("20220601", "R2022-111111")],
        active_tax_year=2026,
    )
    assert [(t.start_year, t.end_year, t.owner_name, t.deed_token, t.acq_dt) for t in res_deed_res] == [
        (2022, 2022, "MODERN BUYER LLC", "R2022-111111", "20220601"),
        (2010, 2010, "ALICE SELLER", "", ""),
        (2009, 2009, "BUILDER INC", "", "20080101"),
    ]

    snaps_tier1_pub = [
        RollOwnerSnapshot(year=2009, owner_name="BUILDER INC", is_confidential=False, acq_dt="01/01/2008"),
        RollOwnerSnapshot(year=2010, owner_name="ALICE SELLER", is_confidential=False, acq_dt="02/15/2010"),
        RollOwnerSnapshot(year=2011, owner_name="", is_confidential=True, acq_dt=""),
        RollOwnerSnapshot(year=2022, owner_name="MODERN BUYER LLC", is_confidential=False, acq_dt="06/01/2022"),
    ]
    res_tier1_pub = consolidate_account_tenures(
        snaps_tier1_pub,
        deeds=[("20100301", "20100111222"), ("20101115", "20100499999"), ("20220601", "R2022-111111")],
        active_tax_year=2026,
    )
    assert [(t.start_year, t.owner_name, t.deed_token) for t in res_tier1_pub] == [
        (2022, "MODERN BUYER LLC", "R2022-111111"),
        (2010, "ALICE SELLER", "20100111222"),
        (2009, "BUILDER INC", ""),
    ]

    # 2. All-zero sentinel account '0000000000000' rejected by build_ownership_graph
    zero_zip = _write_custom_roll_zip(
        tmp_path / "zero_acct.zip",
        real_header=[
            "acct",
            "yr",
            "mailto",
            "mail_addr_1",
            "mail_addr_2",
            "mail_city",
            "mail_state",
            "mail_zip",
            "site_addr_1",
            "state_class",
            "lgl_1",
            "tot_mkt_val",
            "yr_impr",
        ],
        real_rows=[
            ["0000000000000", "2026", "SENTINEL ROW", "1 MAIN ST", "", "HOUSTON", "TX", "77002", "1 MAIN ST", "A1", "LT 1", "100", "1920"],
            ["0071270000013", "2026", "CURRENT OWNER", "PO BOX 1", "", "HOUSTON", "TX", "77002", "2 MAIN ST", "A1", "LT 2", "500000", "1930"],
        ],
    )
    zero_graph = build_ownership_graph(
        zero_zip,
        tax_year=2026,
        comptroller_cache_path=tmp_path / "comptroller_empty.json",
        include_owners_table=False,
        include_permits_table=False,
    )
    assert "0000000000000" not in zero_graph["records"]
    assert "0071270000013" in zero_graph["records"]

    # 3. Annual refresh _stream_targeted_enrichment_records + reconcile_derived_building_indexes with nested mixed-case ZIP members
    staged_data = tmp_path / "staged_refresh_data"
    permits_dir = staged_data / "permits"
    permits_dir.mkdir(parents=True)
    # Seed pre-refresh shards:
    # - 0072000000001 was public in 2026 (has d, m, co, pid, flip) and newly elects Sec. 25.025 confidentiality in 2027 without CAMA building change
    # - 0081000000001 was confidential in 2026 (has conf: 1) in a prefix with zero 2027 confidential accounts, and is sold to a public LLC in 2027
    (permits_dir / "permits_007.json").write_text(
        json.dumps(
            {
                "0072000000001": {
                    "yr": 1926,
                    "d": "20200115:R2020-55555",
                    "ot": "regional_portfolio",
                    "pid": "port_old",
                    "m": "100 OLD ST, HOUSTON, TX 77002",
                    "co": ["OLD PARTNER"],
                    "flip": 42,
                }
            }
        ),
        encoding="utf-8",
    )
    (permits_dir / "permits_008.json").write_text(
        json.dumps({"0081000000001": {"yr": 1932, "conf": 1}}),
        encoding="utf-8",
    )

    acct_zip_2027 = tmp_path / "Acct_2027.zip"
    with zipfile.ZipFile(acct_zip_2027, "w") as zf_a:
        zf_a.writestr(
            "Export_2027/Permits.TXT",
            "\n".join(
                [
                    "ACCT\tID\tDSCR\tPERMIT_TYPE\tPERMIT_TP_DESCR\tISSUE_DATE",
                    "0071300000001\t9001\tDEMO RES/SEWER DISC\t13\tResidential Demolition\t03/01/2027",
                    "0071270000013\t9002\tDEMO RES/SEWER DISC\t13\tResidential Demolition\t03/05/2027",
                ]
            )
            + "\n",
        )
        zf_a.writestr(
            "Export_2027/Deeds.TXT",
            "\n".join(
                [
                    "ACCT\tDOS\tCLERK_YR\tCLERK_ID",
                    "0071300000001\t01/20/2027\t2027\tRP-2027-11111",
                    "0071270000013\t01/25/2027\t2027\tRP-2027-22222",
                    "0072000000001\t01/15/2020\t2020\tRP-2020-55555",
                    "0081000000001\t02/10/2027\t2027\tRP-2027-88888",
                ]
            )
            + "\n",
        )

    bldg_zip_2027 = tmp_path / "Bldg_2027.zip"
    with zipfile.ZipFile(bldg_zip_2027, "w") as zf_b:
        zf_b.writestr(
            "Export_2027/Extra_Features_Detail1.TXT",
            "ACCT\tDSCR\tACT_YR\n0081000000001\tFrame Detached Garage\t1935\n",
        )

    new_accounts = {
        "0071300000001": HcadAccountSnapshot(
            acct="0071300000001",
            earliest_year=1928,
            owner="CONFIDENTIAL OWNER",
            own_tier="homestead",
            is_protected_homestead=True,
            is_sec_25_025_confidential=True,
            has_main_demo_permit=True,
            demo_permit_date="20270301",
        ),
        "0071270000013": HcadAccountSnapshot(
            acct="0071270000013",
            earliest_year=1930,
            owner="CONFIDENTIAL OWNER",
            own_tier="anonymity_shield",
            display_mail="PO BOX 999, HOUSTON, TX 77002",
            is_protected_homestead=False,
            is_sec_25_025_confidential=True,
            has_main_demo_permit=True,
            demo_permit_date="20270305",
        ),
        "0072000000001": HcadAccountSnapshot(
            acct="0072000000001",
            earliest_year=1926,
            owner="CONFIDENTIAL OWNER",
            own_tier="homestead",
            is_protected_homestead=True,
            is_sec_25_025_confidential=True,
        ),
        "0081000000001": HcadAccountSnapshot(
            acct="0081000000001",
            earliest_year=1932,
            owner="HERITAGE LOFTS LLC",
            own_tier="corporate_llc",
            display_mail="500 MAIN ST STE 200, HOUSTON, TX 77002",
            care_of="AGENT CO",
            is_protected_homestead=False,
            is_sec_25_025_confidential=False,
        ),
    }

    streamed = _stream_targeted_enrichment_records(
        {"0071300000001", "0071270000013"},
        new_accounts,
        bldg_zip_2027,
        acct_zip_2027,
        tax_year=2027,
    )
    for conf_ac in ("0071300000001", "0071270000013"):
        assert streamed[conf_ac].get("conf") == 1
        assert len(streamed[conf_ac].get("p") or []) == 1
        for forbidden_k in ("d", "flip", "pid", "m", "co"):
            assert forbidden_k not in streamed[conf_ac], f"Leaked {forbidden_k!r} on refreshed {conf_ac}"

    delta = ArtifactSyncDelta()
    delta.upserted_features_by_hcad["0071300000001"].append(
        {"type": "Feature", "properties": {"hcad_num": "0071300000001", "year_built": 1928}, "geometry": None}
    )
    delta.upserted_features_by_hcad["0071270000013"].append(
        {"type": "Feature", "properties": {"hcad_num": "0071270000013", "year_built": 1930}, "geometry": None}
    )
    # Intentionally omit 0072000000001 and 0081000000001 from delta.upserted_features_by_hcad & touched_permit_prefixes!
    reconcile_derived_building_indexes(
        staged_data,
        {},
        delta,
        new_accounts,
        parcel_geoms_2278=[],
        parcel_hcad_to_idx={},
        bldg_zip_path=bldg_zip_2027,
        acct_zip_path=acct_zip_2027,
        tax_year=2027,
        cache_bust="20270501a",
        shard_filenames=[],
        total_pmtiles_bytes=0,
        compile_core_pmtiles=False,
    )
    p007 = json.loads((permits_dir / "permits_007.json").read_text(encoding="utf-8"))
    assert p007["0072000000001"] == {"yr": 1926, "conf": 1}
    assert p007["0071300000001"].get("conf") == 1 and "d" not in p007["0071300000001"] and "flip" not in p007["0071300000001"]
    assert p007["0071270000013"].get("conf") == 1 and "d" not in p007["0071270000013"] and "flip" not in p007["0071270000013"]

    p008 = json.loads((permits_dir / "permits_008.json").read_text(encoding="utf-8"))
    assert "conf" not in p008["0081000000001"]
    assert p008["0081000000001"]["d"] == "20270210:R2027-88888"
    assert p008["0081000000001"]["ot"] == "corporate_llc"
    assert p008["0081000000001"]["xf"] == [{"d": "Frame Detached Garage", "y": 1935}]


