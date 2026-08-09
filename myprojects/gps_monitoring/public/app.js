const map = L.map("map", { zoomControl: false, attributionControl: true }).setView([13.846494, 100.568683], 13);
L.control.zoom({ position: "bottomleft" }).addTo(map);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

const markerIcon = L.divIcon({ className: "", html: '<div class="asset-marker"><i></i></div>', iconSize: [22, 22], iconAnchor: [11, 11] });
const route = L.polyline([], { color: "#36d6d0", weight: 3, opacity: 0.82, lineJoin: "round" }).addTo(map);
let marker;
let accuracyCircle;
let locations = [];
let followAsset = true;
let status = null;
let toastTimer;
let displayedError = "";

const byId = (id) => document.getElementById(id);
const elements = {
  connectionPill: byId("connectionPill"), connectionLabel: byId("connectionLabel"),
  assetPosition: byId("assetPosition"), assetSubline: byId("assetSubline"), altitude: byId("altitudeValue"),
  accuracy: byId("accuracyValue"), deviceTime: byId("deviceTimeValue"), topic: byId("topicValue"),
  pointCount: byId("pointCount"), distance: byId("distanceValue"), invalid: byId("invalidValue"),
  history: byId("historyList"), empty: byId("emptyState"), dialog: byId("settingsDialog"), form: byId("mqttForm"),
  formError: byId("formError"), saveButton: byId("saveButton"), toast: byId("toast"),
};

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => elements.toast.classList.remove("visible"), 3500);
}

function formatTime(value) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}

function distanceBetween(a, b) {
  const radius = 6_371_000;
  const radians = (degree) => degree * Math.PI / 180;
  const deltaLat = radians(b.lat - a.lat);
  const deltaLon = radians(b.lon - a.lon);
  const value = Math.sin(deltaLat / 2) ** 2 + Math.cos(radians(a.lat)) * Math.cos(radians(b.lat)) * Math.sin(deltaLon / 2) ** 2;
  return 2 * radius * Math.atan2(Math.sqrt(value), Math.sqrt(1 - value));
}

function routeDistance() {
  let total = 0;
  for (let index = 1; index < locations.length; index += 1) total += distanceBetween(locations[index - 1], locations[index]);
  return total >= 1000 ? `${(total / 1000).toFixed(2)} km` : `${Math.round(total)} m`;
}

function renderHistory() {
  const recent = [...locations].reverse().slice(0, 250);
  if (!recent.length) {
    elements.history.innerHTML = '<li class="empty-state"><span class="empty-radar" aria-hidden="true"></span><strong>No position reports yet</strong><p>Valid MQTT messages will appear here and persist across restarts.</p></li>';
  } else {
    elements.history.innerHTML = recent.map((location) => `
      <li class="history-item" tabindex="0" data-id="${escapeHtml(location.id)}">
        <span class="history-node" aria-hidden="true"></span>
        <div>
          <div class="history-top"><span class="history-coords">${location.lat.toFixed(6)}, ${location.lon.toFixed(6)}</span><time class="history-time">${escapeHtml(formatTime(location.deviceTime))}</time></div>
          <div class="history-meta">ALT ${location.alt.toFixed(1)} m · ACC ±${location.acc.toFixed(1)} m</div>
        </div>
      </li>`).join("");
  }
  elements.pointCount.textContent = String(locations.length);
  elements.distance.textContent = routeDistance();
}

function focusLocation(location) {
  map.flyTo([location.lat, location.lon], Math.max(map.getZoom(), 16), { duration: 0.7 });
}

function plotLocation(location, animate = true) {
  route.addLatLng([location.lat, location.lon]);
  if (!accuracyCircle) {
    accuracyCircle = L.circle([location.lat, location.lon], {
      radius: location.acc,
      color: "#ffbd59",
      weight: 1.5,
      opacity: 0.9,
      fillColor: "#36d6d0",
      fillOpacity: 0.14,
      interactive: false,
    }).addTo(map);
  } else {
    accuracyCircle.setLatLng([location.lat, location.lon]);
    accuracyCircle.setRadius(location.acc);
  }
  if (!marker) marker = L.marker([location.lat, location.lon], { icon: markerIcon, zIndexOffset: 1000 }).addTo(map);
  else marker.setLatLng([location.lat, location.lon]);
  marker.bindTooltip(`${location.lat.toFixed(6)}, ${location.lon.toFixed(6)}`, { direction: "top", offset: [0, -14] });

  elements.assetPosition.textContent = `${Math.abs(location.lat).toFixed(5)}° ${location.lat >= 0 ? "N" : "S"} / ${Math.abs(location.lon).toFixed(5)}° ${location.lon >= 0 ? "E" : "W"}`;
  elements.assetSubline.textContent = `Last received ${formatTime(location.receivedAt)}`;
  elements.altitude.textContent = `${location.alt.toFixed(1)} m`;
  elements.accuracy.textContent = `±${location.acc.toFixed(1)} m`;
  elements.deviceTime.textContent = formatTime(location.deviceTime);
  elements.topic.textContent = location.topic || "—";
  if (followAsset && animate) map.flyTo([location.lat, location.lon], Math.max(map.getZoom(), 15), { duration: 0.8 });
}

function renderStatus(nextStatus) {
  status = nextStatus;
  const labels = { connected: "Broker online", connecting: "Connecting", disconnected: "Disconnected", error: "Connection error" };
  elements.connectionPill.dataset.state = nextStatus.state;
  elements.connectionLabel.textContent = labels[nextStatus.state] || nextStatus.state;
  elements.invalid.textContent = String(nextStatus.invalidMessages || 0);
  if (nextStatus.lastError && nextStatus.lastError !== displayedError) {
    displayedError = nextStatus.lastError;
    showToast(nextStatus.lastError);
  }
  if (!nextStatus.lastError) displayedError = "";
}

function populateForm(config) {
  for (const key of ["protocol", "host", "port", "topic", "clientId", "username", "qos"]) {
    const field = byId(key);
    if (field) field.value = config[key] ?? "";
  }
  byId("rejectUnauthorized").checked = config.rejectUnauthorized !== false;
  byId("clearPassword").checked = false;
  byId("password").value = "";
  byId("passwordHint").textContent = config.passwordSet ? "saved — blank keeps it" : "optional";
}

async function request(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

async function initialize() {
  try {
    const [initialStatus, history] = await Promise.all([request("/api/status"), request("/api/locations?limit=5000")]);
    renderStatus(initialStatus);
    populateForm(initialStatus.config);
    locations = history;
    for (const location of locations) plotLocation(location, false);
    renderHistory();
    if (locations.length) {
      const bounds = route.getBounds();
      if (bounds.isValid()) map.fitBounds(bounds.pad(0.2), { maxZoom: 16 });
    }

    const events = new EventSource("/api/events");
    events.addEventListener("status", (event) => renderStatus(JSON.parse(event.data)));
    events.addEventListener("location", (event) => {
      const location = JSON.parse(event.data);
      locations.push(location);
      plotLocation(location);
      renderHistory();
    });
    events.onerror = () => {
      elements.connectionPill.dataset.state = "error";
      elements.connectionLabel.textContent = "Dashboard offline";
    };
  } catch (error) {
    showToast(error.message);
    elements.connectionPill.dataset.state = "error";
    elements.connectionLabel.textContent = "Dashboard offline";
  }
}

byId("settingsButton").addEventListener("click", () => {
  if (status) populateForm(status.config);
  elements.formError.textContent = "";
  elements.dialog.showModal();
});

elements.form.addEventListener("submit", async (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  elements.formError.textContent = "";
  elements.saveButton.disabled = true;
  elements.saveButton.textContent = "Connecting…";
  const data = Object.fromEntries(new FormData(elements.form));
  data.port = Number(data.port);
  data.qos = Number(data.qos);
  data.rejectUnauthorized = byId("rejectUnauthorized").checked;
  data.clearPassword = byId("clearPassword").checked;
  try {
    const nextStatus = await request("/api/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    renderStatus(nextStatus);
    populateForm(nextStatus.config);
    elements.dialog.close();
    showToast("Broker settings saved. Connecting now.");
  } catch (error) {
    elements.formError.textContent = error.message;
  } finally {
    elements.saveButton.disabled = false;
    elements.saveButton.textContent = "Save & connect";
  }
});

byId("disconnectButton").addEventListener("click", async () => {
  try {
    renderStatus(await request("/api/disconnect", { method: "POST" }));
    elements.dialog.close();
    showToast("Broker disconnected.");
  } catch (error) { elements.formError.textContent = error.message; }
});

byId("followButton").addEventListener("click", (event) => {
  followAsset = !followAsset;
  event.currentTarget.setAttribute("aria-pressed", String(followAsset));
  if (followAsset && locations.length) focusLocation(locations.at(-1));
});

byId("fitButton").addEventListener("click", () => {
  const bounds = route.getBounds();
  if (bounds.isValid()) map.fitBounds(bounds.pad(0.18), { maxZoom: 16 });
});

elements.history.addEventListener("click", (event) => {
  const item = event.target.closest(".history-item");
  if (item) focusLocation(locations.find((entry) => entry.id === item.dataset.id));
});
elements.history.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    const item = event.target.closest(".history-item");
    if (item) { event.preventDefault(); focusLocation(locations.find((entry) => entry.id === item.dataset.id)); }
  }
});

initialize();
