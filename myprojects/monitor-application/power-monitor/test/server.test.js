import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  DEFAULT_CONFIG,
  PowerMonitorService,
  TelemetryStore,
  parsePowerJsonLine,
  parsePowerLine,
  validateConfig,
} from "../server.js";

test("parses a JSON serial frame into unit-correct channel readings", () => {
  const timestamp = new Date("2026-08-12T10:00:00.000Z");
  const result = parsePowerJsonLine(JSON.stringify({
    uptime_ms: 12_540,
    channel0: { battery_v: 4.102, load_v: 4.098, current_ma: 20.126, power_w: 0.082 },
    channel1: { battery_v: 3.701, load_v: 3.699, current_ma: 5.417, power_w: 0.02 },
  }), timestamp);

  assert.deepEqual(result, [
    {
      timestamp: "2026-08-12T10:00:00.000Z",
      deviceUptimeMs: 12_540,
      channel: 0,
      battery: 4.102,
      voltage: 4.098,
      current: 20.126,
      power: 0.082,
    },
    {
      timestamp: "2026-08-12T10:00:00.000Z",
      deviceUptimeMs: 12_540,
      channel: 1,
      battery: 3.701,
      voltage: 3.699,
      current: 5.417,
      power: 0.02,
    },
  ]);
});

test("allows a disabled JSON channel and rejects malformed measurements", () => {
  const valid = parsePowerJsonLine(JSON.stringify({
    uptime_ms: 1000,
    channel0: { battery_v: 4, load_v: 3.9, current_ma: 10, power_w: 0.039 },
    channel1: null,
  }));
  assert.deepEqual(valid.map((reading) => reading.channel), [0]);
  assert.equal(parsePowerJsonLine('{"uptime_ms":1000,"channel0":{"battery_v":4,"load_v":3.9,"current_ma":10,"power_mw":39}}')[0].power, 0.039);
  assert.equal(parsePowerJsonLine('{"uptime_ms":1000,"channel0":{"battery_v":4}}'), null);
  assert.equal(parsePowerJsonLine('{"uptime_ms":1000,"channel0":{"battery_v":4,"load_v":3.9,"current_ma":10,"power_w":null}}'), null);
  assert.equal(parsePowerJsonLine("not JSON"), null);
});

test("parses the legacy power monitor format", () => {
  const timestamp = new Date("2026-08-09T10:00:00.000Z");
  const result = parsePowerLine(
    "B0 = 4.122 V, V0 = 4.150 V, I0 = -138.275 mA, P0 = 0.002 W",
    timestamp,
  );
  assert.deepEqual(result, {
    timestamp: "2026-08-09T10:00:00.000Z",
    channel: 0,
    battery: 4.122,
    voltage: 4.15,
    current: -138.275,
    power: 0.002,
  });
});

test("parses additional channels and flexible whitespace", () => {
  const result = parsePowerLine("B12=0 V,V12=0.000 V,I12=0 mA,P12=-0.01 W");
  assert.equal(result.channel, 12);
  assert.equal(result.power, -0.01);
});

test("rejects separators, mismatched channels, and incomplete lines", () => {
  assert.equal(parsePowerLine("------------------------"), null);
  assert.equal(parsePowerLine("B0 = 4 V, V1 = 4 V, I0 = 1 mA, P0 = 1 W"), null);
  assert.equal(parsePowerLine("B0 = 4 V, V0 = 4 V"), null);
});

test("validates and normalizes dashboard configuration", () => {
  const result = validateConfig({
    portPath: "/dev/cu.usbmodem01",
    baudRate: 115200,
    updateIntervalMs: 250,
    historyLimit: 1000,
    autoConnect: false,
  });
  assert.deepEqual(result, {
    portPath: "/dev/cu.usbmodem01",
    baudRate: 115200,
    updateIntervalMs: 250,
    historyLimit: 1000,
    autoConnect: false,
  });
});

test("preserves omitted settings and rejects unsafe ranges", () => {
  assert.equal(validateConfig({ portPath: "/dev/test" }, DEFAULT_CONFIG).baudRate, 115200);
  assert.throws(() => validateConfig({ ...DEFAULT_CONFIG, updateIntervalMs: 99 }), /Update interval/);
  assert.throws(() => validateConfig({ ...DEFAULT_CONFIG, historyLimit: 5001 }), /History limit/);
  assert.throws(() => validateConfig({ ...DEFAULT_CONFIG, portPath: "bad\0path" }), /path/);
});

test("buffers partial serial chunks and publishes every parsed reading", () => {
  const service = new PowerMonitorService({ ...DEFAULT_CONFIG, updateIntervalMs: 60_000 });
  const frames = [];
  service.listeners.add({ write: (frame) => frames.push(frame) });

  const jsonLine = JSON.stringify({
    uptime_ms: 12_540,
    channel0: { battery_v: 4.122, load_v: 4.15, current_ma: -138.275, power_w: 0.002 },
    channel1: { battery_v: 0, load_v: 0, current_ma: 0, power_w: 0 },
  });
  service.ingest(Buffer.from(jsonLine.slice(0, 70)));
  assert.equal(service.readings.length, 0);
  service.ingest(Buffer.from(`${jsonLine.slice(70)}\r\n`));
  service.ingest(Buffer.from("B0 = 4.100 V, V0 = 4.120 V, I0 = -120 mA, P0 = 0.003 W\n"));

  assert.equal(service.readings.length, 3);
  assert.equal(service.pending.length, 3);
  service.publish();
  const readingFrame = frames.findLast((frame) => frame.startsWith("event: reading"));
  const payload = JSON.parse(readingFrame.match(/data: (.+)\n\n/)[1]);
  assert.deepEqual(payload.samples.map((sample) => [sample.channel, sample.battery]), [[0, 4.122], [1, 0], [0, 4.1]]);
  assert.equal(service.pending.length, 0);
  clearInterval(service.publishTimer);
});

test("persists telemetry by day and restores the most recent bounded history", async (context) => {
  const directory = await mkdtemp(join(tmpdir(), "benchline-test-"));
  context.after(() => rm(directory, { recursive: true, force: true }));
  const store = new TelemetryStore(directory);
  await store.initialize();
  await store.append(parsePowerLine("B0=4 V,V0=4 V,I0=100 mA,P0=0.4 W", new Date("2026-08-11T23:59:59Z")));
  await store.append(parsePowerLine("B0=4 V,V0=4 V,I0=200 mA,P0=0.8 W", new Date("2026-08-12T00:00:01Z")));
  await store.append(parsePowerLine("B1=4 V,V1=4 V,I1=300 mA,P1=1.2 W", new Date("2026-08-12T00:00:02Z")));

  const restored = await store.loadRecent(2);
  assert.deepEqual(restored.map((reading) => [reading.channel, reading.power]), [[0, 0.8], [1, 1.2]]);
});
