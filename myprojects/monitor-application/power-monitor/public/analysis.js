function timestampOf(sample) {
  return new Date(sample.timestamp).getTime();
}

function median(values) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

export function readingsInRange(readings, channels, range = null) {
  const selected = new Set(channels);
  return readings.filter((sample) => {
    const time = timestampOf(sample);
    return selected.has(sample.channel)
      && Number.isFinite(time)
      && (!range || (time >= range[0] && time <= range[1]));
  });
}

export function analyzeChannel(samples) {
  const ordered = [...samples].sort((a, b) => timestampOf(a) - timestampOf(b));
  if (!ordered.length) return null;
  const intervals = ordered.slice(1).map((sample, index) => timestampOf(sample) - timestampOf(ordered[index])).filter((value) => value > 0);
  const medianIntervalMs = median(intervals);
  const gapThresholdMs = Math.max(medianIntervalMs * 3, 5_000);
  let energyWh = 0;
  let measuredDurationMs = 0;
  let gapCount = 0;
  for (let index = 1; index < ordered.length; index += 1) {
    const durationMs = timestampOf(ordered[index]) - timestampOf(ordered[index - 1]);
    if (durationMs <= 0) continue;
    if (durationMs > gapThresholdMs) {
      gapCount += 1;
      continue;
    }
    energyWh += ((ordered[index - 1].power + ordered[index].power) / 2) * (durationMs / 3_600_000);
    measuredDurationMs += durationMs;
  }
  const elapsedMs = Math.max(timestampOf(ordered.at(-1)) - timestampOf(ordered[0]), 0);
  const values = (metric) => ordered.map((sample) => sample[metric]).filter(Number.isFinite);
  const mean = (numbers) => numbers.reduce((sum, value) => sum + value, 0) / numbers.length;
  const power = values("power");
  const current = values("current");
  const voltage = values("voltage");
  const battery = values("battery");
  return {
    channel: ordered[0].channel,
    samples: ordered.length,
    start: ordered[0].timestamp,
    end: ordered.at(-1).timestamp,
    elapsedMs,
    measuredDurationMs,
    medianIntervalMs,
    gapCount,
    coverage: elapsedMs ? measuredDurationMs / elapsedMs : 1,
    energyMWh: energyWh * 1_000,
    averagePower: mean(power),
    peakPower: Math.max(...power),
    minimumPower: Math.min(...power),
    averageCurrent: mean(current),
    peakAbsoluteCurrent: Math.max(...current.map(Math.abs)),
    averageVoltage: mean(voltage),
    minimumVoltage: Math.min(...voltage),
    batteryDrop: battery[0] - battery.at(-1),
  };
}

export function analyzeReadings(readings, channels, range = null) {
  const selected = readingsInRange(readings, channels, range);
  const byChannel = new Map(channels.map((channel) => [channel, []]));
  for (const sample of selected) byChannel.get(sample.channel)?.push(sample);
  const channelsSummary = channels.map((channel) => analyzeChannel(byChannel.get(channel))).filter(Boolean);
  return {
    readings: selected,
    channels: channelsSummary,
    samples: channelsSummary.reduce((sum, summary) => sum + summary.samples, 0),
    energyMWh: channelsSummary.reduce((sum, summary) => sum + summary.energyMWh, 0),
    peakPower: channelsSummary.length ? Math.max(...channelsSummary.map((summary) => summary.peakPower)) : null,
    gapCount: channelsSummary.reduce((sum, summary) => sum + summary.gapCount, 0),
    coverage: channelsSummary.length ? channelsSummary.reduce((sum, summary) => sum + summary.coverage, 0) / channelsSummary.length : null,
  };
}

export function detectThresholdEvents(readings, channels, range, thresholds) {
  const samples = readingsInRange(readings, channels, range).sort((a, b) => timestampOf(a) - timestampOf(b));
  const definitions = [
    { type: "power", threshold: Number(thresholds.powerW), value: (sample) => sample.power, label: "Power high", unit: "W" },
    { type: "current", threshold: Number(thresholds.currentMa), value: (sample) => Math.abs(sample.current), label: "Current high", unit: "mA" },
  ].filter((definition) => Number.isFinite(definition.threshold) && definition.threshold > 0);
  const events = [];
  for (const definition of definitions) {
    const active = new Map();
    for (const sample of samples) {
      const key = sample.channel;
      const value = definition.value(sample);
      const existing = active.get(key);
      if (value >= definition.threshold) {
        if (existing) {
          existing.end = sample.timestamp;
          existing.peak = Math.max(existing.peak, value);
          existing.samples += 1;
        } else {
          active.set(key, {
            channel: key,
            type: definition.type,
            label: definition.label,
            unit: definition.unit,
            threshold: definition.threshold,
            peak: value,
            start: sample.timestamp,
            end: sample.timestamp,
            samples: 1,
          });
        }
      } else if (existing) {
        events.push(existing);
        active.delete(key);
      }
    }
    events.push(...active.values());
  }
  return events.sort((a, b) => timestampOf({ timestamp: b.start }) - timestampOf({ timestamp: a.start }));
}

export function readingsToCsv(readings) {
  const header = "timestamp,channel,battery_V,voltage_V,current_mA,power_W";
  const rows = readings.map((sample) => [sample.timestamp, sample.channel, sample.battery, sample.voltage, sample.current, sample.power].join(","));
  return [header, ...rows].join("\n");
}
