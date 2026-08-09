const state = {
  config: null,
  connectionState: "disconnected",
  readings: [],
  latest: new Map(),
  eventSource: null,
  channelMode: "both",
  view: null,
};

const elements = {
  connection: document.querySelector("#connection"),
  connectionText: document.querySelector("#connectionText"),
  toggleConnection: document.querySelector("#toggleConnection"),
  portLabel: document.querySelector("#portLabel"),
  updateRate: document.querySelector("#updateRate"),
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
};

const canvases = [...document.querySelectorAll("canvas[data-metric]")];
const chartInteractions = new WeakMap(canvases.map((canvas) => [canvas, { hoverX: null, dragging: false }]));
const chartPadding = Object.freeze({ top: 54, right: 20, bottom: 28, left: 55 });
let toastTimer;
let drawQueued = false;

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
  elements.connection.dataset.state = status.state;
  const labels = {
    connected: "Reading serial",
    connecting: "Opening port",
    disconnected: "Disconnected",
    error: "Serial error",
  };
  elements.connectionText.textContent = labels[status.state] || status.state;
  elements.toggleConnection.textContent = status.state === "connected" ? "Disconnect" : "Connect";
  elements.portLabel.textContent = status.config.portPath || "No port selected";
  elements.updateRate.textContent = `${(status.config.updateIntervalMs / 1000).toFixed(1)} s`;
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

function addReadings(samples) {
  for (const sample of samples) {
    state.readings.push(sample);
    state.latest.set(sample.channel, sample);
    renderMeter(sample);
  }
  const limit = state.config?.historyLimit || 600;
  if (state.readings.length > limit) state.readings.splice(0, state.readings.length - limit);
  queueDraw();
}

function renderMeter(sample) {
  const prefix = `c${sample.channel}`;
  const digits = { battery: 3, voltage: 3, current: 3, power: 3 };
  for (const metric of Object.keys(digits)) {
    const output = document.querySelector(`#${prefix}${metric}`);
    if (output) output.textContent = sample[metric].toFixed(digits[metric]);
  }
  const time = document.querySelector(`#channel${sample.channel}Time`);
  if (time) time.textContent = new Date(sample.timestamp).toLocaleTimeString();
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

function queueDraw() {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => {
    drawQueued = false;
    canvases.forEach(drawChart);
  });
}

function selectedChannels() {
  return state.channelMode === "both" ? [0, 1] : [Number(state.channelMode)];
}

function timeExtent() {
  const channels = new Set(selectedChannels());
  const times = state.readings
    .filter((sample) => channels.has(sample.channel))
    .map((sample) => new Date(sample.timestamp).getTime())
    .filter(Number.isFinite);
  if (!times.length) return null;
  const minimum = Math.min(...times);
  const maximum = Math.max(...times);
  return minimum === maximum ? [minimum - 500, maximum + 500] : [minimum, maximum];
}

function visibleTimeRange() {
  const extent = timeExtent();
  if (!extent) return null;
  if (!state.view) return extent;
  const extentSpan = extent[1] - extent[0];
  const viewSpan = Math.min(state.view.end - state.view.start, extentSpan);
  const start = Math.min(Math.max(state.view.start, extent[0]), extent[1] - viewSpan);
  return [start, start + viewSpan];
}

function setGraphView(start, end) {
  const extent = timeExtent();
  if (!extent) return;
  const extentSpan = extent[1] - extent[0];
  const span = Math.min(Math.max(end - start, Math.min(1000, extentSpan)), extentSpan);
  const clampedStart = Math.min(Math.max(start, extent[0]), extent[1] - span);
  state.view = span >= extentSpan * 0.999 ? null : { start: clampedStart, end: clampedStart + span };
  elements.resetGraphView.disabled = state.view === null;
  queueDraw();
}

function resetGraphView() {
  state.view = null;
  elements.resetGraphView.disabled = true;
  queueDraw();
}

function zoomGraph(factor, anchorRatio = 0.5) {
  const range = visibleTimeRange();
  if (!range) return;
  const span = range[1] - range[0];
  const anchor = range[0] + span * anchorRatio;
  const nextSpan = span * factor;
  setGraphView(anchor - nextSpan * anchorRatio, anchor + nextSpan * (1 - anchorRatio));
}

function panGraph(fraction) {
  const range = visibleTimeRange();
  if (!range) return;
  const shift = (range[1] - range[0]) * fraction;
  setGraphView(range[0] + shift, range[1] + shift);
}

function drawChart(canvas) {
  const bounds = canvas.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return;
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

  const padding = chartPadding;
  const plotWidth = bounds.width - padding.left - padding.right;
  const plotHeight = bounds.height - padding.top - padding.bottom;
  const values = samples.map((sample) => sample[metric]);
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  const spread = maximum - minimum || Math.max(Math.abs(maximum) * 0.1, metric === "current" ? 1 : 0.05);
  minimum -= spread * 0.12;
  maximum += spread * 0.12;
  if (metric === "current" && minimum < 0 && maximum > 0) {
    const absolute = Math.max(Math.abs(minimum), Math.abs(maximum));
    minimum = -absolute;
    maximum = absolute;
  }
  const firstTime = range[0];
  const lastTime = range[1];
  const timeSpan = Math.max(lastTime - firstTime, 1000);
  const x = (sample) => padding.left + ((new Date(sample.timestamp).getTime() - firstTime) / timeSpan) * plotWidth;
  const y = (value) => padding.top + (1 - (value - minimum) / (maximum - minimum)) * plotHeight;

  context.lineWidth = 1;
  context.strokeStyle = "#d7e0e0";
  context.fillStyle = "#728589";
  context.font = "9px 'DM Mono', monospace";
  context.textAlign = "right";
  context.textBaseline = "middle";
  for (let index = 0; index <= 3; index += 1) {
    const position = padding.top + (plotHeight * index) / 3;
    const value = maximum - ((maximum - minimum) * index) / 3;
    context.beginPath();
    context.moveTo(padding.left, position);
    context.lineTo(bounds.width - padding.right, position);
    context.stroke();
    context.fillText(formatAxis(value), padding.left - 9, position);
  }

  if (minimum < 0 && maximum > 0) {
    context.strokeStyle = "#9eadaf";
    context.beginPath();
    context.moveTo(padding.left, y(0));
    context.lineTo(bounds.width - padding.right, y(0));
    context.stroke();
  }

  const colors = new Map([[0, "#247b97"], [1, "#dd6b3d"]]);
  for (const channel of [...new Set(samples.map((sample) => sample.channel))]) {
    const channelSamples = samples.filter((sample) => sample.channel === channel);
    context.beginPath();
    context.strokeStyle = colors.get(channel) || "#65797d";
    context.lineWidth = 2;
    context.lineJoin = "round";
    channelSamples.forEach((sample, index) => {
      const operation = index === 0 ? "moveTo" : "lineTo";
      context[operation](x(sample), y(sample[metric]));
    });
    context.stroke();
    const last = channelSamples.at(-1);
    context.fillStyle = context.strokeStyle;
    context.beginPath();
    context.arc(x(last), y(last[metric]), 3, 0, Math.PI * 2);
    context.fill();
  }

  context.fillStyle = "#728589";
  context.textAlign = "left";
  context.textBaseline = "bottom";
  context.fillText(new Date(firstTime).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }), padding.left, bounds.height - 7);
  context.textAlign = "right";
  context.fillText(new Date(lastTime).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }), bounds.width - padding.right, bounds.height - 7);

  drawInspection(canvas, context, samples, metric, x, y, bounds, firstTime, timeSpan);
}

function drawInspection(canvas, context, samples, metric, x, y, bounds, firstTime, timeSpan) {
  const interaction = chartInteractions.get(canvas);
  if (interaction.hoverX === null || interaction.dragging) return;
  const hoverX = Math.min(Math.max(interaction.hoverX, chartPadding.left), bounds.width - chartPadding.right);
  const ratio = (hoverX - chartPadding.left) / (bounds.width - chartPadding.left - chartPadding.right);
  const targetTime = firstTime + ratio * timeSpan;
  const nearest = selectedChannels().map((channel) => {
    const channelSamples = samples.filter((sample) => sample.channel === channel);
    return channelSamples.reduce((best, sample) => {
      if (!best) return sample;
      return Math.abs(new Date(sample.timestamp).getTime() - targetTime) < Math.abs(new Date(best.timestamp).getTime() - targetTime) ? sample : best;
    }, null);
  }).filter(Boolean);
  if (!nearest.length) return;

  const inspectionTime = new Date(nearest[0].timestamp).getTime();
  const crosshairX = chartPadding.left + ((inspectionTime - firstTime) / timeSpan) * (bounds.width - chartPadding.left - chartPadding.right);
  context.save();
  context.setLineDash([3, 3]);
  context.strokeStyle = "rgba(16,45,53,.48)";
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(crosshairX, chartPadding.top);
  context.lineTo(crosshairX, bounds.height - chartPadding.bottom);
  context.stroke();
  context.setLineDash([]);

  const colors = new Map([[0, "#247b97"], [1, "#dd6b3d"]]);
  for (const sample of nearest) {
    context.fillStyle = "#f8faf9";
    context.strokeStyle = colors.get(sample.channel) || "#65797d";
    context.lineWidth = 2;
    context.beginPath();
    context.arc(x(sample), y(sample[metric]), 4, 0, Math.PI * 2);
    context.fill();
    context.stroke();
  }

  const unit = { battery: "V", voltage: "V", current: "mA", power: "W" }[metric];
  const lines = nearest.map((sample) => `CH${sample.channel}  ${formatInspection(sample[metric])} ${unit}`);
  const timeLabel = new Date(inspectionTime).toLocaleTimeString([], { hour12: false });
  context.font = "9px 'DM Mono', monospace";
  const textWidth = Math.max(context.measureText(timeLabel).width, ...lines.map((line) => context.measureText(line).width));
  const boxWidth = textWidth + 22;
  const boxHeight = 24 + lines.length * 16;
  const boxX = Math.min(Math.max(crosshairX + 10, chartPadding.left), bounds.width - chartPadding.right - boxWidth);
  const boxY = chartPadding.top + 7;
  context.fillStyle = "rgba(16,45,53,.94)";
  context.fillRect(boxX, boxY, boxWidth, boxHeight);
  context.fillStyle = "#9bb0b3";
  context.textAlign = "left";
  context.textBaseline = "top";
  context.fillText(timeLabel, boxX + 11, boxY + 8);
  lines.forEach((line, index) => {
    context.fillStyle = colors.get(nearest[index].channel) || "#eef3f4";
    context.fillText(line, boxX + 11, boxY + 24 + index * 16);
  });
  context.restore();
}

function formatAxis(value) {
  const absolute = Math.abs(value);
  if (absolute >= 1000) return value.toFixed(0);
  if (absolute >= 10) return value.toFixed(1);
  return value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}

function formatInspection(value) {
  return Number(value).toFixed(3);
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
  try {
    const [status, history] = await Promise.all([request("/api/status"), request("/api/readings?limit=600")]);
    applyStatus(status);
    addReadings(history.readings);
    history.logs.forEach(appendLog);
    openEvents();
  } catch (error) {
    showToast(`Could not reach backend: ${error.message}`);
  }
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
        updateIntervalMs: Math.round(Number(elements.updateSeconds.value) * 1000),
        historyLimit: Number(elements.historyLimit.value),
        autoConnect: elements.autoConnect.checked,
      }),
    });
    applyStatus(status);
    elements.dialog.close();
    showToast("Settings saved");
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
    document.querySelectorAll("[data-channel-mode]").forEach((candidate) => {
      candidate.setAttribute("aria-pressed", String(candidate === button));
    });
    resetGraphView();
  });
});
elements.resetGraphView.addEventListener("click", resetGraphView);

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
      const span = interaction.rangeEnd - interaction.rangeStart;
      const shift = -((event.clientX - interaction.startX) / plotWidth) * span;
      setGraphView(interaction.rangeStart + shift, interaction.rangeEnd + shift);
    } else {
      queueDraw();
    }
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
    zoomGraph(event.deltaY < 0 ? 0.8 : 1.25, anchor);
  }, { passive: false });
  canvas.addEventListener("keydown", (event) => {
    if (["+", "="].includes(event.key)) zoomGraph(0.8);
    else if (["-", "_"].includes(event.key)) zoomGraph(1.25);
    else if (event.key === "ArrowLeft") panGraph(-0.1);
    else if (event.key === "ArrowRight") panGraph(0.1);
    else if (["0", "Escape", "Home"].includes(event.key)) resetGraphView();
    else return;
    event.preventDefault();
  });
}

new ResizeObserver(queueDraw).observe(document.querySelector(".plot-grid"));
initialize();
