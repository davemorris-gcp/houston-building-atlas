import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { fileURLToPath } from "node:url";

import {
  buildAddressShardUrl,
  buildResearchKitDeedChainView,
  decodeCompactDeedChain,
  decodeCompactDeedOfTrustLenders,
  decodeCompactMechanicsLiens,
  decodeGzipJsonResponse,
  decompressAddressShardBytes,
  decompressPermitShardBytes,
  normalizeAddressShardPrefix,
  parseCompactDeedWireEntries,
} from "../../app/js/clerkSearchHelpers.js";
import { buildStewardshipTimelineModel } from "../../app/js/guidedChaptersAndPortfolios.js";
import { HoustonAtlasApp } from "../../app/js/app.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const APP_DIR = path.join(REPO_ROOT, "app");
const DATA_DIR = path.join(APP_DIR, "public", "data");

function sumDirectoryBytes(dirPath) {
  let total = 0;
  for (const entry of fs.readdirSync(dirPath, { withFileTypes: true })) {
    const full = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      total += sumDirectoryBytes(full);
    } else if (entry.isFile()) {
      total += fs.statSync(full).size;
    }
  }
  return total;
}

test("UT-M9-1: decompressPermitShardBytes, decompressAddressShardBytes, and decodeGzipJsonResponse handle raw RFC 1952 gzip, transport-gunzipped JSON, and malformed inputs", async () => {
  const samplePermits = {
    "0620820000016": {
      yr: 1926,
      d: "19940615:P812345::D:JONES MARY:SMITH ROBERT",
      ml: "19260512~VP:642/118~RUSSELL BROWN CO~JONES MARY~14500",
    },
  };
  const sampleAddr = {
    "WALLING ST": [["1119", -95.387, 29.80082, 1926, "0620820000016"]],
  };

  // 1. Raw RFC 1952 gzip (0x1f 0x8b) via Uint8Array, ArrayBuffer, and sliced Buffer view
  const gzPermitBuf = zlib.gzipSync(Buffer.from(JSON.stringify(samplePermits), "utf8"));
  assert.equal(gzPermitBuf[0], 0x1f);
  assert.equal(gzPermitBuf[1], 0x8b);
  assert.deepEqual(await decompressPermitShardBytes(new Uint8Array(gzPermitBuf)), samplePermits);
  assert.deepEqual(
    await decompressPermitShardBytes(
      gzPermitBuf.buffer.slice(gzPermitBuf.byteOffset, gzPermitBuf.byteOffset + gzPermitBuf.byteLength)
    ),
    samplePermits
  );

  const padded = Buffer.concat([Buffer.from([0xaa, 0xbb, 0xcc]), zlib.gzipSync(Buffer.from(JSON.stringify(sampleAddr), "utf8"))]);
  const slicedView = new DataView(padded.buffer, padded.byteOffset + 3, padded.byteLength - 3);
  assert.deepEqual(await decompressAddressShardBytes(slicedView), sampleAddr);

  // 2. Transport-gunzipped plain UTF-8 JSON (0x7b '{') + optional UTF-8 BOM & leading whitespace
  const plainPermitBytes = new TextEncoder().encode(JSON.stringify(samplePermits));
  assert.equal(plainPermitBytes[0], 0x7b);
  assert.deepEqual(await decompressPermitShardBytes(plainPermitBytes), samplePermits);

  const bomWhitespaceAddrBytes = Buffer.concat([
    Buffer.from([0xef, 0xbb, 0xbf]),
    Buffer.from("  \r\n\t ", "utf8"),
    Buffer.from(JSON.stringify(sampleAddr), "utf8"),
  ]);
  assert.deepEqual(await decompressAddressShardBytes(bomWhitespaceAddrBytes), sampleAddr);

  // 3. decodeGzipJsonResponse across arrayBuffer(), transport-gunzipped arrayBuffer(), json() stub, and non-OK responses
  assert.deepEqual(await decodeGzipJsonResponse(null, decompressPermitShardBytes), {});
  assert.deepEqual(await decodeGzipJsonResponse({ ok: false, status: 404 }, decompressPermitShardBytes), {});
  assert.deepEqual(
    await decodeGzipJsonResponse(
      {
        ok: true,
        arrayBuffer: async () =>
          gzPermitBuf.buffer.slice(gzPermitBuf.byteOffset, gzPermitBuf.byteOffset + gzPermitBuf.byteLength),
      },
      decompressPermitShardBytes
    ),
    samplePermits
  );
  assert.deepEqual(
    await decodeGzipJsonResponse(
      {
        ok: true,
        arrayBuffer: async () =>
          plainPermitBytes.buffer.slice(
            plainPermitBytes.byteOffset,
            plainPermitBytes.byteOffset + plainPermitBytes.byteLength
          ),
      },
      decompressPermitShardBytes
    ),
    samplePermits
  );
  assert.deepEqual(
    await decodeGzipJsonResponse(
      { ok: true, json: async () => ({ "0010010000001": { yr: 1912 } }) },
      decompressPermitShardBytes
    ),
    { "0010010000001": { yr: 1912 } }
  );
  assert.deepEqual(
    await decodeGzipJsonResponse({ ok: true, json: async () => [1, 2, 3] }, decompressPermitShardBytes),
    {}
  );

  // 4. Malformed & non-object payload rejections
  await assert.rejects(() => decompressPermitShardBytes(new Uint8Array(0)), /payload is empty/i);
  await assert.rejects(() => decompressAddressShardBytes(null), /payload is empty/i);
  await assert.rejects(
    () => decompressPermitShardBytes(new TextEncoder().encode("   \r\n\t  ")),
    /contains only whitespace/i
  );
  await assert.rejects(
    () => decompressAddressShardBytes(new TextEncoder().encode("<!DOCTYPE html><html>404</html>")),
    /returned HTML \(<\)/i
  );
  await assert.rejects(
    () => decompressPermitShardBytes(new TextEncoder().encode("[1,2,3]")),
    /Unexpected leading byte 0x5b/i
  );
  await assert.rejects(
    () => decompressPermitShardBytes(zlib.gzipSync(Buffer.from("[1,2,3]", "utf8"))),
    /root must be a plain JSON object/i
  );
  await assert.rejects(
    () => decompressAddressShardBytes(new Uint8Array([0x1f, 0x8b, 0x08, 0x00, 0xff, 0xff, 0x01])),
    /Failed to gunzip address shard/i
  );
});

test("UT-M9-2: HoustonAtlasApp _fetchPermitRecord and _fetchAddressShard short-circuit absent manifest prefixes with zero network fetches", async () => {
  const prevFetch = globalThis.fetch;
  const fetchedUrls = [];

  const gzPermit062 = zlib.gzipSync(
    Buffer.from(JSON.stringify({ "0620820000016": { yr: 1926, d: "20210401:R2021-10001" } }), "utf8")
  );
  const gzAddr76 = zlib.gzipSync(
    Buffer.from(
      JSON.stringify({ "N ELDRIDGE PKWY": [["7600", -95.60348, 29.88821, 2002, "1223580020001"]] }),
      "utf8"
    )
  );

  globalThis.fetch = async (url) => {
    fetchedUrls.push(String(url));
    if (String(url).includes("permits_062.json.gz")) {
      return {
        ok: true,
        arrayBuffer: async () =>
          gzPermit062.buffer.slice(gzPermit062.byteOffset, gzPermit062.byteOffset + gzPermit062.byteLength),
      };
    }
    if (String(url).includes("addr_76.json.gz")) {
      return {
        ok: true,
        arrayBuffer: async () =>
          gzAddr76.buffer.slice(gzAddr76.byteOffset, gzAddr76.byteOffset + gzAddr76.byteLength),
      };
    }
    throw new Error(`Unexpected fetch URL: ${url}`);
  };

  try {
    const activeCb = JSON.parse(fs.readFileSync(path.join(DATA_DIR, "pmtiles_manifest.json"), "utf8")).cache_bust;
    const fakeApp = {
      _permitShardCache: new Map(),
      _addressShardCache: {},
      _addressShardPending: {},
      mapController: {
        pmtilesManifest: {
          cache_bust: activeCb,
          permit_shard_prefixes: ["001", "062"],
          address_shard_prefixes: ["10", "76"],
        },
      },
    };

    // Absent permit prefix ("999") + short invalid HCAD ("123") -> 0 network calls
    const missPermit = await HoustonAtlasApp.prototype._fetchPermitRecord.call(fakeApp, "9990010000001");
    assert.equal(missPermit, null);
    const shortPermit = await HoustonAtlasApp.prototype._fetchPermitRecord.call(fakeApp, "12345");
    assert.equal(shortPermit, null);
    assert.equal(fetchedUrls.length, 0);

    // Shared address shard prefix normalizer & URL builder (used by both app.js and plat-monitor.html)
    assert.equal(normalizeAddressShardPrefix("5"), "50");
    assert.equal(normalizeAddressShardPrefix("5", ["10", "50"]), "50");
    assert.equal(normalizeAddressShardPrefix("5", ["10", "76"]), "");
    assert.equal(normalizeAddressShardPrefix("05"), "");
    assert.equal(normalizeAddressShardPrefix("00"), "");
    assert.equal(normalizeAddressShardPrefix("7654", ["10", "76"]), "76");
    assert.equal(
      buildAddressShardUrl("50", activeCb),
      `public/data/address_shards/addr_50.json.gz?v=${activeCb}`
    );
    assert.equal(
      buildAddressShardUrl("50"),
      `public/data/address_shards/addr_50.json.gz?v=${activeCb}`
    );

    // Absent address prefix ("88"), 1-digit absent ("5" -> "50"), + leading-zero key ("05") -> 0 network calls
    const missAddr = await HoustonAtlasApp.prototype._fetchAddressShard.call(fakeApp, "88");
    assert.deepEqual(missAddr, {});
    const singleDigitMiss = await HoustonAtlasApp.prototype._fetchAddressShard.call(fakeApp, "5");
    assert.deepEqual(singleDigitMiss, {});
    const zeroAddr = await HoustonAtlasApp.prototype._fetchAddressShard.call(fakeApp, "05");
    assert.deepEqual(zeroAddr, {});
    assert.equal(fetchedUrls.length, 0);

    // Present permit prefix ("062") -> fetches versioned .json.gz once, caches subsequent parcel lookups
    const hitPermit = await HoustonAtlasApp.prototype._fetchPermitRecord.call(fakeApp, "0620820000016");
    assert.deepEqual(hitPermit, { yr: 1926, d: "20210401:R2021-10001" });
    const cachedOtherParcel = await HoustonAtlasApp.prototype._fetchPermitRecord.call(fakeApp, "0620820000099");
    assert.equal(cachedOtherParcel, null);
    assert.deepEqual(fetchedUrls, [`public/data/permits/permits_062.json.gz?v=${activeCb}`]);

    // Present address prefix ("76") -> fetches versioned .json.gz once, caches subsequent calls
    const hitAddr = await HoustonAtlasApp.prototype._fetchAddressShard.call(fakeApp, "76");
    assert.equal(hitAddr["N ELDRIDGE PKWY"][0][0], "7600");
    const hitAddrAgain = await HoustonAtlasApp.prototype._fetchAddressShard.call(fakeApp, "76");
    assert.equal(hitAddrAgain["N ELDRIDGE PKWY"][0][4], "1223580020001");
    assert.deepEqual(fetchedUrls, [
      `public/data/permits/permits_062.json.gz?v=${activeCb}`,
      `public/data/address_shards/addr_76.json.gz?v=${activeCb}`,
    ]);
  } finally {
    globalThis.fetch = prevFetch;
  }
});

test("UT-M9-3: decodeCompactDeedChain parses 6-part TPIA Grantor -> Grantee conveyances while keeping 4-part and 2-part legacy object keys unchanged", () => {
  const legacyWire = "20190415:R2019-149789:1:D|20120820:20120430232";
  const legacyDecoded = decodeCompactDeedChain(legacyWire);
  assert.equal(legacyDecoded.length, 2);
  assert.deepEqual(Object.keys(legacyDecoded[0]), [
    "date",
    "kind",
    "clerkBoxLabel",
    "copyValue",
    "displayValue",
    "portal",
    "matchKey",
  ]);
  assert.equal("grantor" in legacyDecoded[0], false);
  assert.equal("grantee" in legacyDecoded[0], false);
  assert.equal("grantor" in legacyDecoded[1], false);
  assert.equal("grantee" in legacyDecoded[1], false);

  const tpiaWire = [
    "19940615:P812345::D:JONES MARY:SMITH ROBERT",
    "19780310:FC:123-45-6789:99:O:DOE JANE:JONES MARY",
    "19550401:VP:2850/112::D:ACME DEV CO:DOE JANE",
  ].join("|");

  const rawEntries = parseCompactDeedWireEntries(tpiaWire);
  assert.equal(rawEntries.length, 3);
  assert.equal(rawEntries[1].token, "FC:123-45-6789");
  assert.equal(rawEntries[1].deedId, "99");
  assert.equal(rawEntries[1].typeCode, "O");
  assert.equal(rawEntries[1].grantor, "DOE JANE");
  assert.equal(rawEntries[1].grantee, "JONES MARY");

  const decoded = decodeCompactDeedChain(tpiaWire);
  assert.equal(decoded.length, 3);
  assert.equal(decoded[0].copyValue, "P812345");
  assert.equal(decoded[0].grantor, "JONES MARY");
  assert.equal(decoded[0].grantee, "SMITH ROBERT");
  assert.equal(decoded[1].kind, "film_code");
  assert.equal(decoded[1].copyValue, "123-45-6789");
  assert.equal(decoded[1].grantor, "DOE JANE");
  assert.equal(decoded[1].grantee, "JONES MARY");
  assert.equal(decoded[2].kind, "vol_page");
  assert.equal(decoded[2].copyValue, "2850");
  assert.equal(decoded[2].pageValue, "112");
  assert.equal(decoded[2].grantor, "ACME DEV CO");
  assert.equal(decoded[2].grantee, "DOE JANE");
});

test("UT-M9-4: decodeCompactMechanicsLiens, decodeCompactDeedOfTrustLenders, and buildResearchKitDeedChainView render M/L + D/T sub-blocks and enforce TX Tax Code Sec. 25.025 suppression", () => {
  const mlWire =
    "19260512~VP:642/118~RUSSELL BROWN CO~JONES MARY~14500|20210901~R2021-334455~HEIGHTS RESTORATION LLC~~85000";
  const dtWire =
    "20190415~R2019-149790~FIRST NATIONAL BANK|19980601~T123456~HEIGHTS SAVINGS ASSN";
  const mlItems = decodeCompactMechanicsLiens(mlWire);
  assert.equal(mlItems.length, 2);
  assert.equal(mlItems[0].date, "1926-05-12");
  assert.equal(mlItems[0].contractor, "RUSSELL BROWN CO");
  assert.equal(mlItems[0].ownerName, "JONES MARY");
  assert.equal(mlItems[0].amount, "14500");
  assert.equal(mlItems[0].instrument.kind, "vol_page");
  assert.equal(mlItems[1].instrument.copyValue, "RP-2021-334455");

  const dtItems = decodeCompactDeedOfTrustLenders(dtWire);
  assert.equal(dtItems.length, 2);
  assert.equal(dtItems[0].date, "2019-04-15");
  assert.equal(dtItems[0].lender, "FIRST NATIONAL BANK");
  assert.equal(dtItems[0].instrument.copyValue, "RP-2019-149790");
  assert.equal(dtItems[1].lender, "HEIGHTS SAVINGS ASSN");
  assert.equal(dtItems[1].instrument.copyValue, "T123456");

  // Integrated oh + 6-part d + ml + dt_lender view
  const fullView = buildResearchKitDeedChainView(
    "0620820000016",
    "0620820000016",
    {
      oh: "2005-2026~19940615~P812345~SMITH ROBERT|1988-2004~<=1988~~JONES MARY",
      d: "19940615:P812345::D:JONES MARY:SMITH ROBERT|19780310:FC:123-45-6789::D:DOE JANE:JONES MARY",
      ml: mlWire,
      dt_lender: dtWire,
    },
    "SMITH ROBERT"
  );
  assert.equal(fullView.ignore, false);
  assert.equal(fullView.hidden, false);
  assert.equal(fullView.isEmptyCama, false);
  // 2 tenures + 1 unpaired deed + 2 mechanic's liens + 2 deeds of trust = 7
  assert.equal(fullView.count, 7);
  assert.match(fullView.html, /HCAD Roll Ownership &amp; Deed Chain \(1988-2026\)/);
  assert.match(fullView.html, /DOE JANE -&gt; JONES MARY/);
  assert.match(fullView.html, /Recorded Mechanic&#39;s Liens|Recorded Mechanic's Liens/);
  assert.match(fullView.html, /RUSSELL BROWN CO \(\$14500\) for JONES MARY/);
  assert.match(fullView.html, /RP-2021-334455/);
  assert.match(fullView.html, /Recorded Deeds of Trust &amp; Institutional Financing \(D\/T\)/);
  assert.match(fullView.html, /FIRST NATIONAL BANK/);
  assert.match(fullView.html, /RP-2019-149790/);

  // ml-only record (without d or oh) still renders recorded lien sub-block cleanly
  const mlOnlyView = buildResearchKitDeedChainView(
    "0620820000016",
    "0620820000016",
    { ml: "19260512~VP:642/118~RUSSELL BROWN CO~JONES MARY~14500" },
    ""
  );
  assert.equal(mlOnlyView.isEmptyCama, false);
  assert.equal(mlOnlyView.count, 1);
  assert.match(mlOnlyView.html, /1 Recorded Lien/);
  assert.match(mlOnlyView.html, /RUSSELL BROWN CO \(\$14500\)/);

  // dt_lender-only record (without d, oh, or ml) renders institutional financing sub-block cleanly
  const dtOnlyView = buildResearchKitDeedChainView(
    "0620820000016",
    "0620820000016",
    { dt_lender: "20190415~R2019-149790~FIRST NATIONAL BANK" },
    ""
  );
  assert.equal(dtOnlyView.isEmptyCama, false);
  assert.equal(dtOnlyView.count, 1);
  assert.match(dtOnlyView.html, /1 Recorded Filing/);
  assert.match(dtOnlyView.html, /FIRST NATIONAL BANK/);

  // Confidential suppression via conf: 1 AND via confidential owner token
  for (const [rec, owner] of [
    [
      {
        conf: 1,
        oh: "2005-2026~20100101~R2010-1~SECRET",
        d: "20100101:R2010-1::D:A:B",
        ml: mlWire,
        dt_lender: dtWire,
      },
      "",
    ],
    [
      {
        oh: "2005-2026~20100101~R2010-1~SECRET",
        d: "20100101:R2010-1::D:A:B",
        ml: mlWire,
        dt_lender: dtWire,
      },
      "CONFIDENTIAL OWNER",
    ],
  ]) {
    const confView = buildResearchKitDeedChainView("0620820000016", "0620820000016", rec, owner);
    assert.equal(confView.count, 0);
    assert.match(confView.html, /TX Tax Code Sec\. 25\.025/);
    assert.doesNotMatch(confView.html, /RUSSELL BROWN|FIRST NATIONAL BANK|R2010-1|SECRET/);
  }
});

test("UT-M9-5: buildStewardshipTimelineModel enriches Grantor -> Grantee deeds, emits mechanics_lien events, and suppresses confidential records", () => {
  const model = buildStewardshipTimelineModel({
    yearBuilt: 1926,
    permitShardRec: {
      yr: 1926,
      oh: "2005-2026~19940615~P812345~SMITH ROBERT",
      d: [
        "20080501:20080222333::S::HEIGHTS LOFTS LLC",
        "19940615:P812345::D:JONES MARY:SMITH ROBERT",
        "19680910:C123456::D:BROWN HENRY:",
      ].join("|"),
      ml: "19260512~VP:642/118~RUSSELL BROWN CO~JONES MARY~14500",
    },
  });

  const mlEvents = model.events.filter((e) => e.kind === "mechanics_lien");
  assert.equal(mlEvents.length, 1);
  assert.equal(mlEvents[0].year, 1926);
  assert.equal(mlEvents[0].category, "construction");
  assert.match(mlEvents[0].title, /RUSSELL BROWN CO \(\$14500\)/);
  assert.match(mlEvents[0].subtitle, /Vol 642, Pg 118/);
  assert.match(mlEvents[0].subtitle, /Owner: JONES MARY/);

  const deedTitles = model.events.filter((e) => e.kind === "deed").map((e) => e.title);
  assert.ok(deedTitles.includes("Warranty / Grant Deed Transfer: JONES MARY -> SMITH ROBERT"));
  assert.ok(deedTitles.includes("Warranty / Grant Deed Transfer from BROWN HENRY"));
  assert.ok(deedTitles.includes("Subdivision Plat / Replat Conveyance to HEIGHTS LOFTS LLC"));
  // Paired 1994 tenure was consumed by P812345 deed -> zero duplicate owner_tenure nodes
  assert.equal(model.events.filter((e) => e.kind === "owner_tenure").length, 0);

  // Confidential timeline check
  const confModel = buildStewardshipTimelineModel({
    yearBuilt: 1926,
    permitShardRec: {
      conf: 1,
      yr: 1926,
      oh: "2005-2026~19940615~P812345~SMITH ROBERT",
      d: "19940615:P812345::D:JONES MARY:SMITH ROBERT",
      ml: "19260512~VP:642/118~RUSSELL BROWN CO~JONES MARY~14500",
      flip: 45,
    },
  });
  assert.deepEqual(
    confModel.events.map((e) => e.kind),
    ["construction"]
  );
});

test("IT-M9-6: Live countywide bundle ships 152 permit .json.gz + 90 address .json.gz shards with 0 legacy .json and app/ under 700 MiB", async () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(DATA_DIR, "pmtiles_manifest.json"), "utf8"));
  const permitsDir = path.join(DATA_DIR, "permits");
  const addrDir = path.join(DATA_DIR, "address_shards");

  const permitEntries = fs.readdirSync(permitsDir).filter((f) => !f.startsWith(".")).sort();
  const permitGz = permitEntries.filter((f) => /^permits_\d{3}\.json\.gz$/.test(f));
  const permitLegacyJson = permitEntries.filter((f) => f.endsWith(".json"));
  assert.equal(permitGz.length, 152);
  assert.deepEqual(permitLegacyJson, []);
  assert.deepEqual(
    permitGz.map((f) => f.slice("permits_".length, "permits_".length + 3)),
    manifest.permit_shard_prefixes
  );

  const addrEntries = fs.readdirSync(addrDir).filter((f) => !f.startsWith(".")).sort();
  const addrGz = addrEntries.filter((f) => /^addr_\d{2}\.json\.gz$/.test(f));
  const addrLegacyJson = addrEntries.filter((f) => f.endsWith(".json"));
  assert.equal(addrGz.length, 90);
  assert.deepEqual(addrLegacyJson, []);
  assert.deepEqual(
    addrGz.map((f) => f.slice("addr_".length, "addr_".length + 2)),
    manifest.address_shard_prefixes
  );

  // Decompress live countywide shards via browser Web Streams DecompressionStream("gzip")
  const liveP001 = await decompressPermitShardBytes(fs.readFileSync(path.join(permitsDir, "permits_001.json.gz")));
  assert.ok(Object.keys(liveP001).length > 100);
  const liveAddr10 = await decompressAddressShardBytes(fs.readFileSync(path.join(addrDir, "addr_10.json.gz")));
  assert.ok(Object.keys(liveAddr10).length > 50);

  const totalAppBytes = sumDirectoryBytes(APP_DIR);
  const totalAppMiB = totalAppBytes / (1024 * 1024);
  assert.ok(
    totalAppBytes < 700 * 1024 * 1024,
    `Expected app/ bundle < 700 MiB after Milestone 9 shard compression, got ${totalAppMiB.toFixed(2)} MiB`
  );
});
