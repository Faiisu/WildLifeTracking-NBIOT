import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { EventEmitter } from "node:events";
import { loadHistory, PostgresLocationStore, testMqttConnection, testPostgresConnection, validateConfig, validateDatabaseConfig, validateLocation } from "../server.js";

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

test("loads valid JSONL history and skips malformed lines", async () => {
  const directory = await mkdtemp(join(tmpdir(), "gos-monitoring-"));
  const file = join(directory, "history.jsonl");
  await writeFile(file, '{"id":"one"}\nmalformed\n{"id":"two"}\n');
  const history = await loadHistory(file, 10);
  assert.deepEqual(history.map((entry) => entry.id), ["one", "two"]);
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
