import { analyzeReadings, detectThresholdEvents, readingsInRange, readingsToCsv } from "./analysis.js";

const state = {
  config: null,
  connectionState: "disconnected",
  sessionStartedAt: null,
  readings: [],
  latest: new Map(),
  seen: new Set(),
  eventSource: null,
  channelMode: "both",
  view: null,
  live: true,
  liveWindowMs: null,
  thresholds: loadThresholds(),
};

const elements = {
  connection: document.querySelector("#connection"),
  connectionText: document.querySelector("#connectionText"),
  toggleConnection: document.querySelector("#toggleConnection"),
  portLabel: document.querySelector("#portLabel"),
  recordingState: document.querySelector("#recordingState"),
  sessionDuration: document.querySelector("#sessionDuration"),
  sampleCount: document.querySelector("#sampleCount"),
  pauseLive: document.querySelector("#pauseLive"),
  exportCsv: document.querySelector("#exportCsv"),
  rangeLabel: document.querySelector("#rangeLabel"),
  navigatorLabel: document.querySelector("#navigatorLabel"),
  dialog: document.querySelector("#settingsDialog"),
  form: document.querySelector("#settingsForm"),
  portPath: document.querySelector("#portPath"),
  baudRate: document.querySelector("#baudRate"),
  updateSeconds: document.querySelector("#updateSeconds"),
  historyLimit: document.querySelector("#historyLimit"),
  autoConnect: document.querySelector("#autoConnect"),
  scanPorts: document.querySelector("#scanPorts"),
  scanHelp: document.querySelector("#scanHelp"),
  formError: document.querySelector("#formError"),
  console: document.querySelector("#console"),
  toast: document.querySelector("#toast"),
  resetGraphView: document.querySelector("#resetGraphView"),
  overviewCanvas: document.querySelector("#overviewCanvas"),
  powerThreshold: document.querySelector("#powerThreshold"),
  currentThreshold: document.querySelector("#currentThreshold"),
  summaryEnergy: document.querySelector("#summaryEnergy"),
  summaryAverage: document.querySelector("#summaryAverage"),
  summaryPeak: document.querySelector("#summaryPeak"),
  summaryCoverage: document.querySelector("#summaryCoverage"),
  summaryGaps: document.querySelector("#summaryGaps"),
  reportSampleCount: document.querySelector("#reportSampleCount"),
  channelReport: document.querySelector("#channelReport"),
  eventCount: document.querySelector("#eventCount"),
  eventList: document.querySelector("#eventList"),
  telemetryRows: document.querySelector("#telemetryRows"),
  logRange: document.querySelector("#logRange"),
};

const canvases = [...document.querySelectorAll("canvas[data-metric]")];
const chartInteractions = new WeakMap(canvases.map((canvas) => [canvas, { hoverX: null, dragging: false }]));
const overviewInteraction = { dragging: false, startX: null, currentX: null };
const chartPadding = Object.freeze({ top: 48, right: 18, bottom: 28, left: 56 });
const channelColors = new Map([[0, "#167d9c"], [1, "#e46635"]]);
let toastTimer;
let drawQueued = false;

function loadThresholds() {
  try {
    return { powerW: 1, currentMa: 100, ...JSON.parse(localStorage.getItem("benchline-thresholds")) };
  } catch {
    return { powerW: 1, currentMa: 100 };
  }
}

function saveThresholds() {
  try {
    localStorage.setItem("benchline-thresholds", JSON.stringify(state.thresholds));
  } catch {
    // The controls still work when storage is unavailable.
  }
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: options.body ? { "Content-Type": "application/json", ...options.headers } : options.headers,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

function showToast(message) {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  toastTimer = setTimeout(() => elements.toast.classList.remove("visible"), 3200);
}

function applyStatus(status) {
  state.connectionState = status.state;
  state.config = status.config;
  state.sessionStartedAt = status.sessionStartedAt || state.sessionStartedAt;
  elements.connection.dataset.state = status.state;
  elements.connectionText.textContent = ({ connected: "Reading serial", connecting: "Opening port", disconnected: "Disconnected", error: "Serial error" })[status.state] || status.state;
  elements.toggleConnection.textContent = status.state === "connected" ? "Disconnect" : "Connect";
  elements.portLabel.textContent = status.config.portPath || "No port selected";
  elements.recordingState.textContent = status.telemetryPersistence ? "daily log active" : "memory only";
  if (status.lastError && status.state === "error") showToast(status.lastError);
}

function fillSettings() {
  if (!state.config) return;
  ensurePortOption(state.config.portPath);
  elements.portPath.value = state.config.portPath;
  elements.baudRate.value = String(state.config.baudRate);
  elements.updateSeconds.value = String(state.config.updateIntervalMs / 1000);
  elements.historyLimit.value = String(state.config.historyLimit);
  elements.autoConnect.checked = state.config.autoConnect;
  elements.formError.textContent = "";
}

function ensurePortOption(path) {
  if (!path || [...elements.portPath.options].some((option) => option.value === path)) return;
  elements.portPath.add(new Option(`${path} — saved port`, path));
}

async function scanPorts() {
  elements.scanPorts.disabled = true;
  elements.scanPorts.textContent = "Scanning…";
  elements.scanHelp.textContent = "Asking the operating system for available serial devices.";
  try {
    const selected = elements.portPath.value || state.config?.portPath || "";
    const { ports } = await request("/api/ports");
    elements.portPath.replaceChildren(new Option(ports.length ? "Choose a serial port" : "No serial ports found", ""));
    for (const port of ports) elements.portPath.add(new Option(port.label, port.path));
    ensurePortOption(selected);
    elements.portPath.value = selected;
    elements.scanHelp.textContent = ports.length === 1 ? "Found 1 serial port." : `Found ${ports.length} serial ports.`;
  } catch (error) {
    elements.scanHelp.textContent = error.message;
  } finally {
    elements.scanPorts.disabled = false;
    elements.scanPorts.textContent = "Scan ports";
  }
}

function readingKey(sample) {
  return `${sample.timestamp}:${sample.channel}`;
}

function addReadings(samples) {
  for (const sample of samples) {
    const key = readingKey(sample);
    if (state.seen.has(key)) continue;
    state.seen.add(key);
    state.readings.push(sample);
    state.latest.set(sample.channel, sample);
    renderMeter(sample);
  }
  state.readings.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  const limit = state.config?.historyLimit || 600;
  if (state.readings.length > limit) state.readings.splice(0, state.readings.length - limit);
  state.seen = new Set(state.readings.map(readingKey));
  queueDraw();
}

function renderMeter(sample) {
  const prefix = `c${sample.channel}`;
  for (const metric of ["battery", "voltage", "current", "power"]) {
    const output = document.querySelector(`#${prefix}${metric}`);
    if (output) output.textContent = sample[metric].toFixed(3);
  }
  const time = document.querySelector(`#channel${sample.channel}Time`);
  if (time) time.textContent = new Date(sample.timestamp).toLocaleTimeString([], { hour12: false });
}

function appendLog(entry) {
  elements.console.querySelector(".console-empty")?.remove();
  const row = document.createElement("p");
  row.className = `log-${entry.kind}`;
  const time = document.createElement("time");
  time.textContent = new Date(entry.timestamp).toLocaleTimeString([], { hour12: false });
  const kind = document.createElement("b");
  kind.textContent = entry.kind;
  const message = document.createElement("span");
  message.textContent = entry.message;
  row.append(time, kind, message);
  elements.console.append(row);
  while (elements.console.children.length > 300) elements.console.firstElementChild.remove();
  elements.console.scrollTop = elements.console.scrollHeight;
}

function selectedChannels() {
  return state.channelMode === "both" ? [0, 1] : [Number(state.channelMode)];
}

function timeExtent() {
  const channels = new Set(selectedChannels());
  const times = state.readings.filter((sample) => channels.has(sample.channel)).map((sample) => new Date(sample.timestamp).getTime()).filter(Number.isFinite);
  if (!times.length) return null;
  const minimum = Math.min(...times);
  const maximum = Math.max(...times);
  return minimum === maximum ? [minimum - 500, maximum + 500] : [minimum, maximum];
}

function visibleTimeRange() {
  const extent = timeExtent();
  if (!extent) return null;
  if (state.live && state.liveWindowMs) {
    return [Math.max(extent[0], extent[1] - state.liveWindowMs), extent[1]];
  }
  if (!state.view) return extent;
  const span = Math.min(Math.max(state.view.end - state.view.start, 1), extent[1] - extent[0]);
  const start = Math.min(Math.max(state.view.start, extent[0]), extent[1] - span);
  return [start, start + span];
}

function setLive(isLive) {
  state.live = isLive;
  elements.pauseLive.setAttribute("aria-pressed", String(!isLive));
  elements.pauseLive.textContent = isLive ? "Pause live" : "Resume live";
}

function setGraphView(start, end, { manual = true } = {}) {
  const extent = timeExtent();
  if (!extent) return;
  const extentSpan = extent[1] - extent[0];
  const minimumSpan = Math.min(1_000, extentSpan);
  const span = Math.min(Math.max(end - start, minimumSpan), extentSpan);
  const clampedStart = Math.min(Math.max(start, extent[0]), extent[1] - span);
  const coversAll = span >= extentSpan * .999;
  state.view = coversAll && !manual ? null : { start: clampedStart, end: clampedStart + span };
  if (manual) {
    state.liveWindowMs = null;
    setLive(false);
  }
  elements.resetGraphView.disabled = state.view === null && state.live;
  updateRangeButtons();
  queueDraw();
}

function resetGraphView() {
  state.view = null;
  state.liveWindowMs = null;
  setLive(true);
  elements.resetGraphView.disabled = true;
  updateRangeButtons("all");
  queueDraw();
}

function zoomGraph(factor, anchorRatio = .5) {
  const range = visibleTimeRange();
  if (!range) return;
  const span = range[1] - range[0];
  const anchor = range[0] + span * anchorRatio;
  setGraphView(anchor - span * factor * anchorRatio, anchor + span * factor * (1 - anchorRatio));
}

function panGraph(fraction) {
  const range = visibleTimeRange();
  if (!range) return;
  const shift = (range[1] - range[0]) * fraction;
  setGraphView(range[0] + shift, range[1] + shift);
}

function queueDraw() {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => {
    drawQueued = false;
    canvases.forEach(drawChart);
    drawOverview();
    renderAnalysis();
  });
}

function prepareCanvas(canvas) {
  const bounds = canvas.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return null;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.round(bounds.width * ratio);
  const height = Math.round(bounds.height * ratio);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, bounds.width, bounds.height);
  return { bounds, context };
}

function drawChart(canvas) {
  const prepared = prepareCanvas(canvas);
  if (!prepared) return;
  const { bounds, context } = prepared;
  const metric = canvas.dataset.metric;
  const channels = new Set(selectedChannels());
  const range = visibleTimeRange();
  const allSamples = state.readings.filter((sample) => channels.has(sample.channel) && Number.isFinite(sample[metric]));
  const samples = range ? allSamples.filter((sample) => {
    const time = new Date(sample.timestamp).getTime();
    return time >= range[0] && time <= range[1];
  }) : [];
  const card = canvas.closest(".plot-card");
  card.classList.toggle("has-data", samples.length > 0);
  card.querySelector(".plot-empty").textContent = allSamples.length ? "No readings in this view" : `Waiting for channel ${state.channelMode === "both" ? "0 or 1" : state.channelMode}`;
  if (!samples.length || !range) return;

  const plotWidth = bounds.width - chartPadding.left - chartPadding.right;
  const plotHeight = bounds.height - chartPadding.top - chartPadding.bottom;
  const values = samples.map((sample) => sample[metric]);
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  const threshold = metric === "power" ? Number(state.thresholds.powerW) : metric === "current" ? Number(state.thresholds.currentMa) : null;
  const spread = maximum - minimum || Math.max(Math.abs(maximum) * .1, metric === "current" ? 1 : .05);
  minimum -= spread * .12;
  maximum += spread * .12;
  if (metric === "current" && minimum < 0 && maximum > 0) {
    const absolute = Math.max(Math.abs(minimum), Math.abs(maximum));
    minimum = -absolute;
    maximum = absolute;
  }
  const timeSpan = Math.max(range[1] - range[0], 1_000);
  const x = (sample) => chartPadding.left + ((new Date(sample.timestamp).getTime() - range[0]) / timeSpan) * plotWidth;
  const y = (value) => chartPadding.top + (1 - (value - minimum) / (maximum - minimum)) * plotHeight;

  context.lineWidth = 1;
  context.strokeStyle = "#d2dddc";
  context.fillStyle = "#6d8184";
  context.font = "8px 'SFMono-Regular', Consolas, monospace";
  context.textAlign = "right";
  context.textBaseline = "middle";
  const gridLines = canvas.closest(".plot-card-primary") ? 5 : 3;
  for (let index = 0; index <= gridLines; index += 1) {
    const position = chartPadding.top + (plotHeight * index) / gridLines;
    const value = maximum - ((maximum - minimum) * index) / gridLines;
    context.beginPath();
    context.moveTo(chartPadding.left, position);
    context.lineTo(bounds.width - chartPadding.right, position);
    context.stroke();
    context.fillText(formatAxis(value), chartPadding.left - 8, position);
  }

  if (minimum < 0 && maximum > 0) {
    context.strokeStyle = "#93a5a7";
    context.beginPath();
    context.moveTo(chartPadding.left, y(0));
    context.lineTo(bounds.width - chartPadding.right, y(0));
    context.stroke();
  }
  if (Number.isFinite(threshold)) {
    const thresholdLines = metric === "current" ? [threshold, -threshold] : [threshold];
    context.save();
    context.setLineDash([5, 4]);
    context.strokeStyle = "#bd443a";
    for (const thresholdLine of thresholdLines) {
      if (thresholdLine <= minimum || thresholdLine >= maximum) continue;
      context.beginPath();
      context.moveTo(chartPadding.left, y(thresholdLine));
      context.lineTo(bounds.width - chartPadding.right, y(thresholdLine));
      context.stroke();
    }
    context.restore();
  }

  for (const channel of [...new Set(samples.map((sample) => sample.channel))]) {
    const channelSamples = samples.filter((sample) => sample.channel === channel);
    const intervals = channelSamples.slice(1).map((sample, index) => new Date(sample.timestamp) - new Date(channelSamples[index].timestamp)).filter((value) => value > 0).sort((a, b) => a - b);
    const typicalInterval = intervals.length ? intervals[Math.floor(intervals.length / 2)] : 0;
    const gapLimit = Math.max(typicalInterval * 3, 5_000);
    context.beginPath();
    context.strokeStyle = channelColors.get(channel) || "#62777b";
    context.lineWidth = canvas.closest(".plot-card-primary") ? 2.2 : 1.8;
    context.lineJoin = "round";
    channelSamples.forEach((sample, index) => {
      const isGap = index && new Date(sample.timestamp) - new Date(channelSamples[index - 1].timestamp) > gapLimit;
      context[index === 0 || isGap ? "moveTo" : "lineTo"](x(sample), y(sample[metric]));
    });
    context.stroke();
  }

  context.fillStyle = "#6d8184";
  context.textBaseline = "bottom";
  context.textAlign = "left";
  context.fillText(formatChartTime(range[0]), chartPadding.left, bounds.height - 7);
  context.textAlign = "right";
  context.fillText(formatChartTime(range[1]), bounds.width - chartPadding.right, bounds.height - 7);
  drawInspection(canvas, context, samples, metric, x, y, bounds, range[0], timeSpan);
}

function drawInspection(canvas, context, samples, metric, x, y, bounds, firstTime, timeSpan) {
  const interaction = chartInteractions.get(canvas);
  if (interaction.hoverX === null || interaction.dragging) return;
  const hoverX = Math.min(Math.max(interaction.hoverX, chartPadding.left), bounds.width - chartPadding.right);
  const targetTime = firstTime + ((hoverX - chartPadding.left) / (bounds.width - chartPadding.left - chartPadding.right)) * timeSpan;
  const nearest = selectedChannels().map((channel) => samples.filter((sample) => sample.channel === channel).reduce((best, sample) => {
    if (!best) return sample;
    return Math.abs(new Date(sample.timestamp) - targetTime) < Math.abs(new Date(best.timestamp) - targetTime) ? sample : best;
  }, null)).filter(Boolean);
  if (!nearest.length) return;
  const inspectionTime = new Date(nearest[0].timestamp).getTime();
  const crosshairX = chartPadding.left + ((inspectionTime - firstTime) / timeSpan) * (bounds.width - chartPadding.left - chartPadding.right);
  context.save();
  context.setLineDash([3, 3]);
  context.strokeStyle = "rgba(16,45,53,.5)";
  context.beginPath();
  context.moveTo(crosshairX, chartPadding.top);
  context.lineTo(crosshairX, bounds.height - chartPadding.bottom);
  context.stroke();
  context.setLineDash([]);
  for (const sample of nearest) {
    context.fillStyle = "#f8faf8";
    context.strokeStyle = channelColors.get(sample.channel) || "#62777b";
    context.lineWidth = 2;
    context.beginPath();
    context.arc(x(sample), y(sample[metric]), 4, 0, Math.PI * 2);
    context.fill();
    context.stroke();
  }
  const unit = { battery: "V", voltage: "V", current: "mA", power: "W" }[metric];
  const lines = nearest.map((sample) => `CH${sample.channel}  ${sample[metric].toFixed(3)} ${unit}`);
  const timeLabel = new Date(inspectionTime).toLocaleTimeString([], { hour12: false });
  context.font = "8px 'SFMono-Regular', Consolas, monospace";
  const textWidth = Math.max(context.measureText(timeLabel).width, ...lines.map((line) => context.measureText(line).width));
  const boxWidth = textWidth + 22;
  const boxHeight = 23 + lines.length * 15;
  const boxX = Math.min(Math.max(crosshairX + 9, chartPadding.left), bounds.width - chartPadding.right - boxWidth);
  const boxY = chartPadding.top + 6;
  context.fillStyle = "rgba(16,45,53,.95)";
  context.fillRect(boxX, boxY, boxWidth, boxHeight);
  context.textAlign = "left";
  context.textBaseline = "top";
  context.fillStyle = "#a3b5b6";
  context.fillText(timeLabel, boxX + 10, boxY + 7);
  lines.forEach((line, index) => {
    context.fillStyle = channelColors.get(nearest[index].channel) || "#edf2f1";
    context.fillText(line, boxX + 10, boxY + 22 + index * 15);
  });
  context.restore();
}

function drawOverview() {
  const prepared = prepareCanvas(elements.overviewCanvas);
  const extent = timeExtent();
  if (!prepared || !extent) return;
  const { bounds, context } = prepared;
  const padding = 6;
  const width = bounds.width - padding * 2;
  const height = bounds.height - 14;
  const samples = readingsInRange(state.readings, selectedChannels(), extent);
  if (!samples.length) return;
  const values = samples.map((sample) => sample.power);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const spread = maximum - minimum || 1;
  const xTime = (time) => padding + ((time - extent[0]) / (extent[1] - extent[0])) * width;
  const yValue = (value) => 5 + (1 - (value - minimum) / spread) * (height - 10);
  context.strokeStyle = "rgba(16,45,53,.13)";
  context.beginPath();
  context.moveTo(padding, height / 2);
  context.lineTo(bounds.width - padding, height / 2);
  context.stroke();
  for (const channel of selectedChannels()) {
    const channelSamples = samples.filter((sample) => sample.channel === channel);
    context.beginPath();
    context.strokeStyle = channelColors.get(channel);
    context.globalAlpha = .8;
    context.lineWidth = 1.2;
    channelSamples.forEach((sample, index) => context[index ? "lineTo" : "moveTo"](xTime(new Date(sample.timestamp).getTime()), yValue(sample.power)));
    context.stroke();
  }
  context.globalAlpha = 1;
  let range = visibleTimeRange();
  if (overviewInteraction.dragging) {
    const left = Math.min(overviewInteraction.startX, overviewInteraction.currentX);
    const right = Math.max(overviewInteraction.startX, overviewInteraction.currentX);
    range = [extent[0] + (left / bounds.width) * (extent[1] - extent[0]), extent[0] + (right / bounds.width) * (extent[1] - extent[0])];
  }
  const left = xTime(range[0]);
  const right = xTime(range[1]);
  context.fillStyle = "rgba(16,45,53,.11)";
  context.fillRect(padding, 0, Math.max(left - padding, 0), height);
  context.fillRect(right, 0, Math.max(bounds.width - padding - right, 0), height);
  context.strokeStyle = "#102d35";
  context.lineWidth = 1;
  context.strokeRect(left, 1, Math.max(right - left, 1), height - 2);
  context.fillStyle = "#102d35";
  context.fillRect(left - 2, height / 2 - 7, 4, 14);
  context.fillRect(right - 2, height / 2 - 7, 4, 14);
}

function renderAnalysis() {
  const range = visibleTimeRange();
  const analysis = analyzeReadings(state.readings, selectedChannels(), range);
  elements.sampleCount.textContent = `${state.readings.length.toLocaleString()} samples`;
  elements.exportCsv.disabled = analysis.samples === 0;
  elements.reportSampleCount.textContent = `${analysis.samples.toLocaleString()} samples`;
  elements.logRange.textContent = analysis.samples > 200 ? `Showing latest 200 of ${analysis.samples.toLocaleString()} samples` : `${analysis.samples.toLocaleString()} samples in range`;
  if (range && analysis.samples) {
    const duration = range[1] - range[0];
    elements.rangeLabel.textContent = `${formatDateTime(range[0])} → ${formatDateTime(range[1])} · ${formatDuration(duration)}`;
    const extent = timeExtent();
    const ratio = extent ? (duration / (extent[1] - extent[0])) * 100 : 100;
    elements.navigatorLabel.textContent = ratio > 99.5 ? "FULL HISTORY" : `${ratio.toFixed(1)}% SELECTED`;
  } else {
    elements.rangeLabel.textContent = "Waiting for serial readings";
    elements.navigatorLabel.textContent = "FULL HISTORY";
  }
  elements.summaryEnergy.textContent = analysis.samples ? formatNumber(analysis.energyMWh, 3) : "—";
  const average = analysis.samples ? analysis.readings.reduce((sum, sample) => sum + sample.power, 0) / analysis.samples : null;
  elements.summaryAverage.textContent = average === null ? "—" : formatNumber(average, 3);
  elements.summaryPeak.textContent = analysis.peakPower === null ? "—" : formatNumber(analysis.peakPower, 3);
  elements.summaryCoverage.textContent = analysis.coverage === null ? "—" : `${Math.min(analysis.coverage * 100, 100).toFixed(1)}%`;
  elements.summaryGaps.textContent = `${analysis.gapCount} gap${analysis.gapCount === 1 ? "" : "s"}`;
  renderChannelReport(analysis.channels);
  renderEvents(detectThresholdEvents(state.readings, selectedChannels(), range, state.thresholds));
  renderTelemetryRows(analysis.readings.slice(-200).reverse());
}

function renderChannelReport(summaries) {
  if (!summaries.length) {
    elements.channelReport.replaceChildren(tableMessage(7, "No data in this range"));
    return;
  }
  const rows = summaries.map((summary) => {
    const row = document.createElement("tr");
    const channel = document.createElement("td");
    const key = document.createElement("span");
    key.className = `channel-key channel-${summary.channel}`;
    key.textContent = `CH ${summary.channel}`;
    channel.append(key);
    row.append(channel);
    [
      `${formatNumber(summary.energyMWh, 3)} mWh`,
      `${formatNumber(summary.averagePower, 3)} W`,
      `${formatNumber(summary.peakPower, 3)} W`,
      `${formatNumber(summary.averageCurrent, 2)} mA`,
      `${formatNumber(summary.minimumVoltage, 3)} V`,
      `${summary.batteryDrop >= 0 ? "−" : "+"}${formatNumber(Math.abs(summary.batteryDrop), 3)} V`,
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    return row;
  });
  elements.channelReport.replaceChildren(...rows);
}

function renderEvents(events) {
  elements.eventCount.textContent = `${events.length} event${events.length === 1 ? "" : "s"}`;
  if (!events.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No threshold crossings in the selected range.";
    elements.eventList.replaceChildren(empty);
    return;
  }
  const rows = events.slice(0, 50).map((event) => {
    const row = document.createElement("div");
    row.className = "event-row";
    const marker = document.createElement("i");
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${event.label} · CH ${event.channel}`;
    const detail = document.createElement("span");
    detail.textContent = `${formatDateTime(new Date(event.start).getTime())} · peak ${formatNumber(event.peak, 3)} ${event.unit} · ${event.samples} sample${event.samples === 1 ? "" : "s"}`;
    body.append(title, detail);
    row.append(marker, body);
    return row;
  });
  elements.eventList.replaceChildren(...rows);
}

function renderTelemetryRows(readings) {
  if (!readings.length) {
    elements.telemetryRows.replaceChildren(tableMessage(6, "Serial readings will appear here."));
    return;
  }
  const rows = readings.map((sample) => {
    const row = document.createElement("tr");
    const values = [
      formatDateTime(new Date(sample.timestamp).getTime(), true),
      `CH ${sample.channel}`,
      sample.battery.toFixed(3),
      sample.voltage.toFixed(3),
      sample.current.toFixed(3),
      sample.power.toFixed(3),
    ];
    values.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    return row;
  });
  elements.telemetryRows.replaceChildren(...rows);
}

function tableMessage(columns, message) {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = columns;
  cell.className = "empty-cell";
  cell.textContent = message;
  row.append(cell);
  return row;
}

function formatAxis(value) {
  const absolute = Math.abs(value);
  if (absolute >= 1_000) return value.toFixed(0);
  if (absolute >= 10) return value.toFixed(1);
  return value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}

function formatNumber(value, digits) {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: Math.min(digits, 2) });
}

function formatChartTime(timestamp) {
  return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDateTime(timestamp, milliseconds = false) {
  return new Date(timestamp).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
    ...(milliseconds ? { fractionalSecondDigits: 3 } : {}),
  });
}

function formatDuration(milliseconds) {
  const seconds = Math.max(Math.round(milliseconds / 1_000), 0);
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const remainder = seconds % 60;
  return [hours, minutes, remainder].map((value) => String(value).padStart(2, "0")).join(":");
}

function updateSessionClock() {
  elements.sessionDuration.textContent = state.sessionStartedAt ? formatDuration(Date.now() - new Date(state.sessionStartedAt).getTime()) : "00:00:00";
}

function updateRangeButtons(active = null) {
  if (active === null && state.live && state.liveWindowMs) active = String(state.liveWindowMs);
  if (active === null && state.live) active = "all";
  document.querySelectorAll("[data-range]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.range === active)));
}

function openEvents() {
  state.eventSource?.close();
  const source = new EventSource("/api/events");
  state.eventSource = source;
  source.addEventListener("status", (event) => applyStatus(JSON.parse(event.data)));
  source.addEventListener("reading", (event) => addReadings(JSON.parse(event.data).samples));
  source.addEventListener("log", (event) => appendLog(JSON.parse(event.data)));
  source.onerror = () => {
    elements.connection.dataset.state = "error";
    elements.connectionText.textContent = "Backend offline";
  };
}

async function initialize() {
  elements.powerThreshold.value = state.thresholds.powerW;
  elements.currentThreshold.value = state.thresholds.currentMa;
  try {
    const status = await request("/api/status");
    applyStatus(status);
    const history = await request(`/api/readings?limit=${status.config.historyLimit}`);
    addReadings(history.readings);
    history.logs.forEach(appendLog);
    openEvents();
  } catch (error) {
    showToast(`Could not reach backend: ${error.message}`);
  }
  updateSessionClock();
}

document.querySelector("#openSettings").addEventListener("click", () => {
  fillSettings();
  elements.dialog.showModal();
  scanPorts();
});
document.querySelector("#closeSettings").addEventListener("click", () => elements.dialog.close());
document.querySelector("#cancelSettings").addEventListener("click", () => elements.dialog.close());
elements.scanPorts.addEventListener("click", scanPorts);
elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.formError.textContent = "";
  const submit = elements.form.querySelector("button[type='submit']");
  submit.disabled = true;
  submit.textContent = "Saving…";
  try {
    const status = await request("/api/config", {
      method: "PUT",
      body: JSON.stringify({
        portPath: elements.portPath.value,
        baudRate: Number(elements.baudRate.value),
        updateIntervalMs: Math.round(Number(elements.updateSeconds.value) * 1_000),
        historyLimit: Number(elements.historyLimit.value),
        autoConnect: elements.autoConnect.checked,
      }),
    });
    applyStatus(status);
    elements.dialog.close();
    showToast("Settings saved; serial input applied");
  } catch (error) {
    elements.formError.textContent = error.message;
  } finally {
    submit.disabled = false;
    submit.textContent = "Save & connect";
  }
});
elements.toggleConnection.addEventListener("click", async () => {
  try {
    const action = state.connectionState === "connected" ? "disconnect" : "connect";
    applyStatus(await request(`/api/${action}`, { method: "POST" }));
  } catch (error) {
    showToast(error.message);
  }
});
document.querySelector("#clearConsole").addEventListener("click", () => {
  elements.console.replaceChildren(Object.assign(document.createElement("p"), { className: "console-empty", textContent: "Console view cleared. New messages will appear here." }));
});

document.querySelectorAll("[data-channel-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    state.channelMode = button.dataset.channelMode;
    document.querySelectorAll("[data-channel-mode]").forEach((candidate) => candidate.setAttribute("aria-pressed", String(candidate === button)));
    resetGraphView();
  });
});
document.querySelectorAll("[data-range]").forEach((button) => {
  button.addEventListener("click", () => {
    const extent = timeExtent();
    if (!extent) return;
    if (button.dataset.range === "all") resetGraphView();
    else {
      const duration = Number(button.dataset.range);
      state.view = null;
      state.liveWindowMs = duration;
      setLive(true);
      elements.resetGraphView.disabled = false;
      updateRangeButtons();
      queueDraw();
    }
  });
});
elements.resetGraphView.addEventListener("click", resetGraphView);
elements.pauseLive.addEventListener("click", () => {
  if (state.live) {
    const range = visibleTimeRange();
    if (range) {
      state.liveWindowMs = null;
      setGraphView(range[0], range[1]);
    }
  } else resetGraphView();
});
elements.exportCsv.addEventListener("click", () => {
  const readings = readingsInRange(state.readings, selectedChannels(), visibleTimeRange());
  if (!readings.length) return;
  const blob = new Blob([readingsToCsv(readings)], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `benchline-${new Date().toISOString().replaceAll(":", "-")}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 0);
  showToast(`Exported ${readings.length.toLocaleString()} readings`);
});

for (const [input, key] of [[elements.powerThreshold, "powerW"], [elements.currentThreshold, "currentMa"]]) {
  input.addEventListener("input", () => {
    state.thresholds[key] = Number(input.value);
    saveThresholds();
    queueDraw();
  });
}

for (const canvas of canvases) {
  const interaction = chartInteractions.get(canvas);
  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !visibleTimeRange()) return;
    const range = visibleTimeRange();
    interaction.dragging = true;
    interaction.startX = event.clientX;
    interaction.rangeStart = range[0];
    interaction.rangeEnd = range[1];
    canvas.classList.add("is-dragging");
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    interaction.hoverX = event.clientX - canvas.getBoundingClientRect().left;
    if (interaction.dragging) {
      const plotWidth = canvas.getBoundingClientRect().width - chartPadding.left - chartPadding.right;
      const shift = -((event.clientX - interaction.startX) / plotWidth) * (interaction.rangeEnd - interaction.rangeStart);
      setGraphView(interaction.rangeStart + shift, interaction.rangeEnd + shift);
    } else queueDraw();
  });
  const endDrag = (event) => {
    if (!interaction.dragging) return;
    interaction.dragging = false;
    canvas.classList.remove("is-dragging");
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    queueDraw();
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("pointerleave", () => {
    if (!interaction.dragging) {
      interaction.hoverX = null;
      queueDraw();
    }
  });
  canvas.addEventListener("wheel", (event) => {
    if (!visibleTimeRange()) return;
    event.preventDefault();
    const bounds = canvas.getBoundingClientRect();
    const plotWidth = bounds.width - chartPadding.left - chartPadding.right;
    const anchor = Math.min(Math.max((event.clientX - bounds.left - chartPadding.left) / plotWidth, 0), 1);
    zoomGraph(event.deltaY < 0 ? .8 : 1.25, anchor);
  }, { passive: false });
  canvas.addEventListener("keydown", (event) => {
    if (["+", "="].includes(event.key)) zoomGraph(.8);
    else if (["-", "_"].includes(event.key)) zoomGraph(1.25);
    else if (event.key === "ArrowLeft") panGraph(-.1);
    else if (event.key === "ArrowRight") panGraph(.1);
    else if (["0", "Escape", "Home"].includes(event.key)) resetGraphView();
    else return;
    event.preventDefault();
  });
}

elements.overviewCanvas.addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || !timeExtent()) return;
  const bounds = elements.overviewCanvas.getBoundingClientRect();
  overviewInteraction.dragging = true;
  overviewInteraction.startX = Math.min(Math.max(event.clientX - bounds.left, 0), bounds.width);
  overviewInteraction.currentX = overviewInteraction.startX;
  elements.overviewCanvas.setPointerCapture(event.pointerId);
});
elements.overviewCanvas.addEventListener("pointermove", (event) => {
  if (!overviewInteraction.dragging) return;
  const bounds = elements.overviewCanvas.getBoundingClientRect();
  overviewInteraction.currentX = Math.min(Math.max(event.clientX - bounds.left, 0), bounds.width);
  queueDraw();
});
elements.overviewCanvas.addEventListener("pointerup", (event) => {
  if (!overviewInteraction.dragging) return;
  const bounds = elements.overviewCanvas.getBoundingClientRect();
  const extent = timeExtent();
  const left = Math.min(overviewInteraction.startX, overviewInteraction.currentX);
  const right = Math.max(overviewInteraction.startX, overviewInteraction.currentX);
  overviewInteraction.dragging = false;
  if (elements.overviewCanvas.hasPointerCapture(event.pointerId)) elements.overviewCanvas.releasePointerCapture(event.pointerId);
  if (right - left > 6) setGraphView(extent[0] + (left / bounds.width) * (extent[1] - extent[0]), extent[0] + (right / bounds.width) * (extent[1] - extent[0]));
  else queueDraw();
});
elements.overviewCanvas.addEventListener("pointercancel", () => {
  overviewInteraction.dragging = false;
  queueDraw();
});
elements.overviewCanvas.addEventListener("dblclick", resetGraphView);

new ResizeObserver(queueDraw).observe(document.querySelector(".analysis-panel"));
setInterval(updateSessionClock, 1_000).unref?.();
initialize();
