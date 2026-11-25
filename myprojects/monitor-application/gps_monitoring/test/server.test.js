import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { EventEmitter } from "node:events";
import { isUnifiedPayload, LocationService, PostgresLocationStore, SqliteLocationStore, testMqttConnection, testPostgresConnection, validateConfig, validateDatabaseConfig, validateLocation } from "../server.js";
import { CellGeolocator, isCellReportPayload, testCellGeolocation, validateCellGeoConfig, validateCellReport } from "../cell-geolocation.js";

test("validates and normalizes a tracker location", () => {
  const result = validateLocation('{"lat":13.846494,"lon":100.568683,"alt":0.9,"acc":22.4,"time":"2026-08-08T11:24:09Z"}', "asset/7/location");
  assert.equal(result.lat, 13.846494);
  assert.equal(result.lon, 100.568683);
  assert.equal(result.deviceTime, "2026-08-08T11:24:09.000Z");
  assert.equal(result.topic, "asset/7/location");
  assert.ok(result.receivedAt);
});

test("rejects malformed or unsafe location values", () => {
  assert.throws(() => validateLocation("not json"), /valid JSON/);
  assert.throws(() => validateLocation({ lat: 91, lon: 0, alt: 0, acc: 1, time: new Date().toISOString() }), /lat/);
  assert.throws(() => validateLocation({ lat: 1, lon: 2, alt: 0, acc: -1, time: new Date().toISOString() }), /acc/);
});

test("validates MQTT configuration and preserves an existing password", () => {
  const result = validateConfig({
    protocol: "mqtts", host: "broker.example.com", port: 8883, topic: "assets/+/location",
    clientId: "dashboard", username: "operator", password: "", qos: 1, rejectUnauthorized: true,
  }, { password: "saved-secret" });
  assert.equal(result.password, "saved-secret");
  assert.equal(result.qos, 1);
  assert.throws(() => validateConfig({ ...result, host: "mqtt://bad" }, result), /Host/);
});

test("stores, loads, imports legacy JSONL, and clears locations in SQLite", async () => {
  const directory = await mkdtemp(join(tmpdir(), "gos-monitoring-"));
  const dbFile = join(directory, "locations.sqlite");
  const jsonlFile = join(directory, "locations.jsonl");
  await writeFile(jsonlFile, [
    JSON.stringify({ id: "one", lat: 13.8, lon: 100.5, alt: 1, acc: 5, deviceTime: "2026-08-08T11:24:09.000Z", receivedAt: "2026-08-08T11:24:10.000Z", topic: "test/nrf91" }),
    "malformed",
    JSON.stringify({ id: "two", lat: 13.81, lon: 100.51, alt: 2, acc: 6, deviceTime: "2026-08-08T11:25:09.000Z", receivedAt: "2026-08-08T11:25:10.000Z", topic: "test/nrf91" }),
  ].join("\n") + "\n");

  const store = new SqliteLocationStore(dbFile);
  store.open();
  try {
    const imported = await store.importJsonlIfEmpty(jsonlFile);
    assert.equal(imported, 2);
    assert.deepEqual(store.loadRecent(10).map((entry) => entry.id), ["one", "two"]);

    const extra = validateLocation('{"lat":13.82,"lon":100.52,"alt":3,"acc":4,"time":"2026-08-08T11:26:09Z"}', "test/nrf91");
    store.insert(extra);
    assert.deepEqual(store.loadRecent(2).map((entry) => entry.id), ["two", extra.id]);

    store.clear();
    assert.deepEqual(store.loadRecent(10), []);
  } finally {
    store.close();
  }
});

test("inserts locations with parameterized PostgreSQL values", async () => {
  const calls = [];
  const store = new PostgresLocationStore({ host: "db.example.com", port: 5432, database: "gps", username: "operator", password: "secret", ssl: true }, () => ({
    query: async (...args) => calls.push(args),
    end: async () => {},
  }));
  await store.configure(store.config);
  calls.length = 0;
  const location = validateLocation('{"lat":13.8,"lon":100.5,"alt":2,"acc":5,"time":"2026-08-08T11:24:09Z"}', "test/nrf91");
  await store.insert(location);
  assert.match(calls[0][0], /INSERT INTO gps_locations/);
  assert.deepEqual(calls[0][1], [location.id, 13.8, 100.5, 2, 5, location.deviceTime, location.receivedAt, "test/nrf91"]);
});

test("validates PostgreSQL GUI configuration and preserves its password", () => {
  const result = validateDatabaseConfig({ dbHost: "db.example.com", dbPort: 5432, dbDatabase: "gps", dbUsername: "operator", dbPassword: "", dbSsl: true }, { password: "saved-secret" });
  assert.equal(result.password, "saved-secret");
  assert.equal(result.ssl, true);
  assert.throws(() => validateDatabaseConfig({ ...result, dbHost: "postgres://bad" }, result), /host/);
});

test("tests the PostgreSQL destination without changing the schema", async () => {
  const calls = [];
  const result = await testPostgresConnection({ host: "db.example.com", port: 5432, database: "gps", username: "operator", password: "secret", ssl: true }, () => ({
    query: async (...args) => calls.push(args),
    end: async () => {},
  }));
  assert.equal(result.ok, true);
  assert.equal(calls[0][0], "SELECT 1");
});

test("tests MQTT with a temporary client without subscribing", async () => {
  const client = new EventEmitter();
  let closed = false;
  client.end = () => { closed = true; };
  const result = testMqttConnection({ protocol: "mqtts", host: "broker.example.com", port: 8883, clientId: "dashboard", username: "operator", password: "secret", rejectUnauthorized: true }, () => client);
  queueMicrotask(() => client.emit("connect"));
  assert.equal((await result).ok, true);
  assert.equal(closed, true);
});

test("clears local SQLite logs even when optional PostgreSQL is unreachable", async () => {
  const directory = await mkdtemp(join(tmpdir(), "gos-monitoring-clear-"));
  const localStore = new SqliteLocationStore(join(directory, "locations.sqlite"));
  localStore.open();
  const sample = validateLocation('{"lat":13.8,"lon":100.5,"alt":2,"acc":5,"time":"2026-08-08T11:24:09Z"}', "test/nrf91");
  localStore.insert(sample);
  const failingDb = {
    status: () => ({ enabled: true, lastError: "connect ECONNREFUSED" }),
    clear: async () => { throw new AggregateError([new Error("connect ECONNREFUSED")], ""); },
  };
  const service = new LocationService(
    { protocol: "mqtt", host: "broker.hivemq.com", port: 1883, topic: "test/nrf91", clientId: "c", username: "", password: "", qos: 0, rejectUnauthorized: true },
    [sample],
    { host: "localhost", port: 5432, database: "location_logs", username: "admin", password: "", ssl: false },
    failingDb,
    localStore,
  );
  try {
    const nextStatus = await service.clearHistory();
    assert.equal(nextStatus.pointsStored, 0);
    assert.deepEqual(localStore.loadRecent(10), []);
  } finally {
    localStore.close();
  }
});

test("validates cell geolocation config and preserves saved API key", () => {
  const result = validateCellGeoConfig(
    { cellGeoProvider: "unwiredlabs", cellGeoApiKey: "", cellGeoEndpoint: "https://ap1.unwiredlabs.com/v2/process.php" },
    { apiKey: "saved-token" },
  );
  assert.equal(result.provider, "unwiredlabs");
  assert.equal(result.apiKey, "saved-token");
  assert.equal(result.endpoint, "https://ap1.unwiredlabs.com/v2/process.php");

  assert.throws(
    () => validateCellGeoConfig({ cellGeoProvider: "google", cellGeoApiKey: "" }, {}),
    /API key/,
  );
  assert.throws(
    () => validateCellGeoConfig({ cellGeoProvider: "opencellid_local", cellGeoLocalDbPath: "" }, {}),
    /local CSV or SQLite/,
  );
  assert.throws(
    () => validateCellGeoConfig({ cellGeoProvider: "nrfcloud", cellGeoApiKey: "k", cellGeoEndpoint: "ftp://bad" }, {}),
    /http or https/,
  );
});

test("detects and validates LTE cell report payloads from sleep_mode trackers", () => {
  const payload = {
    mcc: 520, mnc: 3, tac: 12345, cid: 67890123, earfcn: 3600, pci: 123,
    rsrp: -95, rsrq: null, uptime_ms: 12345, meas_time_ms: 12000,
    ncells: [{ earfcn: 3600, pci: 124, rsrp: -102, rsrq: 10 }],
  };
  assert.equal(isCellReportPayload(payload), true);
  assert.equal(isCellReportPayload({ lat: 13.8, lon: 100.5, alt: 0, acc: 5, time: new Date().toISOString() }), false);

  const report = validateCellReport(payload);
  assert.equal(report.mcc, 520);
  assert.equal(report.mnc, 3);
  assert.equal(report.cid, 67890123);
  assert.equal(report.rsrq, null);
  assert.equal(report.ncells.length, 1);
  assert.equal(report.ncells[0].pci, 124);
});

test("resolves LTE cell reports via Unwired Labs and caches repeated lookups", async () => {
  const requests = [];
  const mockFetch = async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ status: "ok", lat: 13.846494, lon: 100.568683, accuracy: 450 }),
    };
  };
  const locator = new CellGeolocator({ provider: "unwiredlabs", apiKey: "tok-123" }, { fetchImpl: mockFetch });
  const cell = { mcc: 520, mnc: 3, tac: 12345, cid: 67890123, rsrp: -95, pci: 123, ncells: [] };

  const first = await locator.resolve(cell, "test/nrf91");
  const second = await locator.resolve(cell, "test/nrf91");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].body.token, "tok-123");
  assert.equal(requests[0].body.cells[0].lac, 12345);
  assert.equal(requests[0].body.cells[0].cid, 67890123);
  assert.equal(first.lat, 13.846494);
  assert.equal(first.lon, 100.568683);
  assert.equal(first.acc, 450);
  assert.equal(second.lat, 13.846494);
});

test("resolves LTE cell reports via Google Geolocation API", async () => {
  const requests = [];
  const mockFetch = async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ location: { lat: 13.7563, lng: 100.5018 }, accuracy: 620 }),
    };
  };
  const locator = new CellGeolocator({ provider: "google", apiKey: "g-key" }, { fetchImpl: mockFetch });
  const result = await locator.resolve({ mcc: 520, mnc: 3, tac: 12345, cid: 67890123, rsrp: -92 }, "test/nrf91");
  assert.match(requests[0].url, /\?key=g-key$/);
  assert.equal(requests[0].body.cellTowers[0].cellId, 67890123);
  assert.equal(requests[0].body.cellTowers[0].locationAreaCode, 12345);
  assert.equal(result.lat, 13.7563);
  assert.equal(result.lon, 100.5018);
  assert.equal(result.acc, 620);
});

test("resolves LTE cell reports including neighbor cells via nRF Cloud", async () => {
  const requests = [];
  const mockFetch = async (url, options) => {
    requests.push({ url, headers: options.headers, body: JSON.parse(options.body) });
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ lat: 13.85, lon: 100.57, uncertainty: 310 }),
    };
  };
  const locator = new CellGeolocator({ provider: "nrfcloud", apiKey: "nrf-jwt" }, { fetchImpl: mockFetch });
  const result = await locator.resolve({
    mcc: 520, mnc: 3, tac: 12345, cid: 67890123, earfcn: 3600, pci: 123, rsrp: -95, rsrq: -10,
    ncells: [{ earfcn: 3600, pci: 124, rsrp: -102, rsrq: -12 }],
  }, "test/nrf91");

  assert.equal(requests[0].headers.Authorization, "Bearer nrf-jwt");
  assert.equal(requests[0].body.lte[0].eci, 67890123);
  assert.equal(requests[0].body.lte[0].nmr.length, 1);
  assert.equal(requests[0].body.lte[0].nmr[0].pci, 124);
  assert.equal(result.lat, 13.85);
  assert.equal(result.acc, 310);
});

test("resolves LTE cell reports from a local OpenCellID CSV file", async () => {
  const directory = await mkdtemp(join(tmpdir(), "gos-monitoring-cellid-"));
  const csvFile = join(directory, "520.csv");
  await writeFile(csvFile, [
    "radio,mcc,net,area,cell,unit,lon,lat,range,samples,changeable,created,updated,averageSignal",
    "LTE,520,3,12345,67890123,123,100.568683,13.846494,850,12,1,1700000000,1700000000,0",
  ].join("\n") + "\n");

  const check = await testCellGeolocation(
    { provider: "opencellid_local", localDbPath: csvFile },
    { cacheDir: directory },
  );
  assert.equal(check.ok, true);
  assert.match(check.message, /1 cells/);

  const locator = new CellGeolocator(
    { provider: "opencellid_local", localDbPath: csvFile },
    { cacheDir: directory },
  );
  try {
    const resolved = await locator.resolve({ mcc: 520, mnc: 3, tac: 12345, cid: 67890123 }, "test/nrf91");
    assert.equal(resolved.lat, 13.846494);
    assert.equal(resolved.lon, 100.568683);
    assert.equal(resolved.acc, 850);
  } finally {
    locator.close();
  }
});

test("LocationService resolves LTE cell MQTT messages and stores them in SQLite", async () => {
  const directory = await mkdtemp(join(tmpdir(), "gos-monitoring-lte-"));
  const localStore = new SqliteLocationStore(join(directory, "locations.sqlite"));
  localStore.open();
  const mockFetch = async () => ({
    ok: true,
    status: 200,
    text: async () => JSON.stringify({ status: "ok", lat: 13.846494, lon: 100.568683, accuracy: 500 }),
  });
  const cellGeoConfig = { provider: "unwiredlabs", apiKey: "tok-123", endpoint: "", localDbPath: "" };
  const locator = new CellGeolocator(cellGeoConfig, { fetchImpl: mockFetch, cacheDir: directory });
  const noopDb = { status: () => ({ enabled: false, lastError: "" }), insert: async () => {} };
  const service = new LocationService(
    { protocol: "mqtt", host: "broker.hivemq.com", port: 1883, topic: "test/nrf91", clientId: "c", username: "", password: "", qos: 0, rejectUnauthorized: true },
    [],
    { host: "", port: 5432, database: "", username: "", password: "", ssl: true },
    noopDb,
    localStore,
    cellGeoConfig,
    locator,
  );
  try {
    const cellPayload = JSON.stringify({
      mcc: 520, mnc: 3, tac: 12345, cid: 67890123, earfcn: 3600, pci: 123,
      rsrp: -95, rsrq: 12, uptime_ms: 12345, meas_time_ms: 12000, ncells: [],
    });
    const saved = await service.handleMqttMessage("test/nrf91", Buffer.from(cellPayload));
    assert.ok(saved);
    assert.equal(saved.lat, 13.846494);
    assert.equal(saved.lon, 100.568683);
    assert.equal(service.history.length, 1);
    assert.equal(localStore.loadRecent(10).length, 1);
  } finally {
    locator.close();
    localStore.close();
  }
});

test("detects unified schema payloads and routes cell detection by resolution rule", () => {
  const unifiedLte = {
    device_id: "WLTK01",
    uptime_ms: 12637,
    source: "lte",
    gnss: null,
    lte: {
      mcc: 520, mnc: 3, tac: 950, cid: 159260272, earfcn: 3600, pci: 242, rsrp: -80, rsrq: 12,
      ncells: [{ earfcn: 3600, pci: 243, rsrp: -92, rsrq: 10 }],
    },
  };
  const unifiedGnss = {
    device_id: "WLTK01",
    uptime_ms: 48210,
    source: "gnss",
    gnss: { lat: 13.756331, lon: 100.501765, alt: 12.5, acc: 3.2, time: "2026-11-25T13:30:00Z" },
    lte: null,
  };
  const unifiedHybrid = {
    device_id: "WLTK01",
    uptime_ms: 51400,
    source: "hybrid",
    gnss: { lat: 13.756331, lon: 100.501765, alt: 12.5, acc: 3.2, time: "2026-11-25T13:30:00Z" },
    lte: {
      mcc: 520, mnc: 3, tac: 950, cid: 159260272, earfcn: 3600, pci: 242, rsrp: -78, rsrq: 11,
      ncells: [],
    },
  };

  assert.equal(isUnifiedPayload(unifiedLte), true);
  assert.equal(isUnifiedPayload(unifiedGnss), true);
  assert.equal(isUnifiedPayload(unifiedHybrid), true);
  assert.equal(isUnifiedPayload({ lat: 13.8, lon: 100.5, alt: 0, acc: 5, time: "2026-11-25T13:30:00Z" }), false);

  assert.equal(isCellReportPayload(unifiedLte), true);
  assert.equal(isCellReportPayload(unifiedGnss), false);
  assert.equal(isCellReportPayload(unifiedHybrid), false);

  const cell = validateCellReport(unifiedLte);
  assert.equal(cell.mcc, 520);
  assert.equal(cell.cid, 159260272);
  assert.equal(cell.ncells.length, 1);

  const gnssLoc = validateLocation(unifiedGnss, "test/nrf91");
  assert.equal(gnssLoc.lat, 13.756331);
  assert.equal(gnssLoc.lon, 100.501765);
  assert.equal(gnssLoc.alt, 12.5);
  assert.equal(gnssLoc.acc, 3.2);
  assert.equal(gnssLoc.deviceId, "WLTK01");
  assert.equal(gnssLoc.source, "gnss");
});

test("LocationService handles unified lte, gnss, and hybrid MQTT payloads per resolution rule", async () => {
  const directory = await mkdtemp(join(tmpdir(), "gos-monitoring-unified-"));
  const localStore = new SqliteLocationStore(join(directory, "locations.sqlite"));
  localStore.open();
  let geoCalls = 0;
  const mockFetch = async () => {
    geoCalls += 1;
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ status: "ok", lat: 13.846494, lon: 100.568683, accuracy: 480 }),
    };
  };
  const cellGeoConfig = { provider: "unwiredlabs", apiKey: "tok-123", endpoint: "", localDbPath: "" };
  const locator = new CellGeolocator(cellGeoConfig, { fetchImpl: mockFetch, cacheDir: directory });
  const noopDb = { status: () => ({ enabled: false, lastError: "" }), insert: async () => {} };
  const service = new LocationService(
    { protocol: "mqtt", host: "broker.hivemq.com", port: 1883, topic: "test/nrf91", clientId: "c", username: "", password: "", qos: 0, rejectUnauthorized: true },
    [],
    { host: "", port: 5432, database: "", username: "", password: "", ssl: true },
    noopDb,
    localStore,
    cellGeoConfig,
    locator,
  );

  try {
    // 1. Unified LTE-only (gnss: null, lte: {...}) -> resolves via CellGeolocator
    const ltePayload = JSON.stringify({
      device_id: "WLTK01",
      uptime_ms: 12637,
      source: "lte",
      gnss: null,
      lte: {
        mcc: 520, mnc: 3, tac: 950, cid: 159260272, earfcn: 3600, pci: 242, rsrp: -80, rsrq: 12,
        ncells: [{ earfcn: 3600, pci: 243, rsrp: -92, rsrq: 10 }],
      },
    });
    const lteSaved = await service.handleMqttMessage("test/nrf91", Buffer.from(ltePayload));
    assert.ok(lteSaved);
    assert.equal(geoCalls, 1);
    assert.equal(lteSaved.lat, 13.846494);
    assert.equal(lteSaved.lon, 100.568683);
    assert.equal(lteSaved.acc, 480);
    assert.equal(lteSaved.deviceId, "WLTK01");
    assert.equal(lteSaved.source, "lte");

    // 2. Unified GNSS-only (gnss: {...}, lte: null) -> uses GNSS directly, no cell geolocation call
    const gnssPayload = JSON.stringify({
      device_id: "WLTK02",
      uptime_ms: 48210,
      source: "gnss",
      gnss: { lat: 13.756331, lon: 100.501765, alt: 12.5, acc: 3.2, time: "2026-11-25T13:30:00Z" },
      lte: null,
    });
    const gnssSaved = await service.handleMqttMessage("test/nrf91", Buffer.from(gnssPayload));
    assert.ok(gnssSaved);
    assert.equal(geoCalls, 1);
    assert.equal(gnssSaved.lat, 13.756331);
    assert.equal(gnssSaved.lon, 100.501765);
    assert.equal(gnssSaved.alt, 12.5);
    assert.equal(gnssSaved.acc, 3.2);
    assert.equal(gnssSaved.deviceTime, "2026-11-25T13:30:00.000Z");
    assert.equal(gnssSaved.deviceId, "WLTK02");
    assert.equal(gnssSaved.source, "gnss");

    // 3. Unified hybrid (both gnss and lte populated) -> uses GNSS directly, no cell geolocation call
    const hybridPayload = JSON.stringify({
      device_id: "WLTK03",
      uptime_ms: 51400,
      source: "hybrid",
      gnss: { lat: 13.756331, lon: 100.501765, alt: 12.5, acc: 3.2, time: "2026-11-25T13:30:00Z" },
      lte: {
        mcc: 520, mnc: 3, tac: 950, cid: 159260272, earfcn: 3600, pci: 242, rsrp: -78, rsrq: 11,
        ncells: [],
      },
    });
    const hybridSaved = await service.handleMqttMessage("test/nrf91", Buffer.from(hybridPayload));
    assert.ok(hybridSaved);
    assert.equal(geoCalls, 1);
    assert.equal(hybridSaved.lat, 13.756331);
    assert.equal(hybridSaved.lon, 100.501765);
    assert.equal(hybridSaved.alt, 12.5);
    assert.equal(hybridSaved.acc, 3.2);
    assert.equal(hybridSaved.deviceId, "WLTK03");
    assert.equal(hybridSaved.source, "hybrid");

    // 4. Unified payload with both gnss: null and lte: null -> rejected cleanly
    const emptyUnified = JSON.stringify({
      device_id: "WLTK04",
      uptime_ms: 55000,
      source: "gnss",
      gnss: null,
      lte: null,
    });
    const emptySaved = await service.handleMqttMessage("test/nrf91", Buffer.from(emptyUnified));
    assert.equal(emptySaved, null);
    assert.equal(service.invalidMessages, 1);

    // 5. Invalid unified source -> rejected cleanly
    const badSource = JSON.stringify({
      device_id: "WLTK05",
      uptime_ms: 56000,
      source: "wifi",
      gnss: { lat: 13.756331, lon: 100.501765, alt: 12.5, acc: 3.2, time: "2026-11-25T13:30:00Z" },
      lte: null,
    });
    const badSourceSaved = await service.handleMqttMessage("test/nrf91", Buffer.from(badSource));
    assert.equal(badSourceSaved, null);
    assert.equal(service.invalidMessages, 2);

    // Verify SQLite persistence preserves deviceId and source
    const stored = localStore.loadRecent(10);
    assert.equal(stored.length, 3);
    assert.deepEqual(
      stored.map((item) => ({ deviceId: item.deviceId, source: item.source })),
      [
        { deviceId: "WLTK01", source: "lte" },
        { deviceId: "WLTK02", source: "gnss" },
        { deviceId: "WLTK03", source: "hybrid" },
      ],
    );
  } finally {
    locator.close();
    localStore.close();
  }
});
