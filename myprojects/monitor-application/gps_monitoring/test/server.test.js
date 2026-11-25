import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { EventEmitter } from "node:events";
import { LocationService, PostgresLocationStore, SqliteLocationStore, testMqttConnection, testPostgresConnection, validateConfig, validateDatabaseConfig, validateLocation } from "../server.js";

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
