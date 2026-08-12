import test from "node:test";
import assert from "node:assert/strict";
import { analyzeChannel, analyzeReadings, detectThresholdEvents, readingsToCsv } from "../public/analysis.js";

function sample(seconds, channel, power, current = power * 100) {
  return {
    timestamp: new Date(Date.UTC(2026, 7, 12, 10, 0, seconds)).toISOString(),
    channel,
    battery: 4.2 - seconds / 10_000,
    voltage: 4.1,
    current,
    power,
  };
}

test("integrates energy and excludes missing-data gaps", () => {
  const summary = analyzeChannel([
    sample(0, 0, 1),
    sample(1, 0, 1),
    sample(2, 0, 1),
    sample(12, 0, 1),
  ]);
  assert.equal(summary.gapCount, 1);
  assert.equal(summary.measuredDurationMs, 2_000);
  assert.ok(Math.abs(summary.energyMWh - (2 / 3.6)) < 1e-9);
  assert.equal(summary.averagePower, 1);
  assert.equal(summary.peakPower, 1);
});

test("summarizes selected channels and a selected time range", () => {
  const readings = [sample(0, 0, 1), sample(1, 0, 2), sample(0, 1, 3), sample(1, 1, 4)];
  const summary = analyzeReadings(readings, [0], [Date.UTC(2026, 7, 12, 10, 0, 0), Date.UTC(2026, 7, 12, 10, 0, 1)]);
  assert.equal(summary.samples, 2);
  assert.equal(summary.channels.length, 1);
  assert.equal(summary.channels[0].channel, 0);
  assert.equal(summary.peakPower, 2);
});

test("groups consecutive threshold crossings into useful events", () => {
  const readings = [sample(0, 0, .5, 50), sample(1, 0, 1.2, 120), sample(2, 0, 1.4, 140), sample(3, 0, .4, 40)];
  const events = detectThresholdEvents(readings, [0], null, { powerW: 1, currentMa: 100 });
  assert.equal(events.length, 2);
  assert.deepEqual(events.map((event) => [event.type, event.samples, event.peak]).sort(), [["current", 2, 140], ["power", 2, 1.4]]);
});

test("exports a stable, unit-labelled CSV", () => {
  const csv = readingsToCsv([sample(0, 0, 1)]);
  assert.match(csv, /^timestamp,channel,battery_V,voltage_V,current_mA,power_W\n/);
  assert.match(csv, /,0,4\.2,4\.1,100,1$/);
});
