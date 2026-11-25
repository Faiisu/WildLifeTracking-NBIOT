import { createReadStream, mkdirSync, statSync } from "node:fs";
import { dirname, extname, join } from "node:path";
import { createInterface } from "node:readline";
import { DatabaseSync } from "node:sqlite";

export const CELL_GEO_PROVIDERS = Object.freeze([
  "none",
  "unwiredlabs",
  "google",
  "nrfcloud",
  "opencellid_local",
]);

export const DEFAULT_CELL_GEO_ENDPOINTS = Object.freeze({
  unwiredlabs: "https://us1.unwiredlabs.com/v2/process.php",
  google: "https://www.googleapis.com/geolocation/v1/geolocate",
  nrfcloud: "https://api.nrfcloud.com/v1/location/ground-fix",
});

export const DEFAULT_CELL_GEO_CONFIG = Object.freeze({
  provider: "none",
  apiKey: "",
  endpoint: "",
  localDbPath: "",
});

const DEFAULT_CACHE_TTL_MS = 5 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 10_000;

export function envCellGeoConfig() {
  const provider = String(process.env.CELL_GEO_PROVIDER || "").trim().toLowerCase();
  if (!provider) return { ...DEFAULT_CELL_GEO_CONFIG };
  return {
    provider,
    apiKey: String(process.env.CELL_GEO_API_KEY || ""),
    endpoint: String(process.env.CELL_GEO_ENDPOINT || "").trim(),
    localDbPath: String(process.env.CELL_GEO_DB_PATH || "").trim(),
  };
}

export function validateCellGeoConfig(input, previous = DEFAULT_CELL_GEO_CONFIG) {
  if (!input || Array.isArray(input) || typeof input !== "object") {
    return { ...DEFAULT_CELL_GEO_CONFIG };
  }

  const rawProvider = input.cellGeoProvider ?? input.provider ?? "none";
  const provider = String(rawProvider || "none").trim().toLowerCase();
  if (!CELL_GEO_PROVIDERS.includes(provider)) {
    throw new Error(`Cell geolocation provider must be one of: ${CELL_GEO_PROVIDERS.join(", ")}`);
  }

  const endpoint = String(input.cellGeoEndpoint ?? input.endpoint ?? "").trim();
  if (endpoint) {
    let parsed;
    try {
      parsed = new URL(endpoint);
    } catch {
      throw new Error("Cell geolocation endpoint must be a valid http or https URL");
    }
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      throw new Error("Cell geolocation endpoint must use http or https");
    }
  }

  const localDbPath = String(input.cellGeoLocalDbPath ?? input.localDbPath ?? "").trim();
  if (localDbPath.length > 1024 || localDbPath.includes("\0")) {
    throw new Error("Local OpenCellID path is invalid");
  }

  let apiKey = previous?.apiKey || "";
  if (input.clearCellGeoApiKey === true) apiKey = "";
  const incomingKey = input.cellGeoApiKey ?? input.apiKey;
  if (typeof incomingKey === "string" && incomingKey.length > 0) {
    apiKey = incomingKey.trim();
  }
  if (apiKey.length > 2048) {
    throw new Error("Cell geolocation API key is too long");
  }

  if (["unwiredlabs", "google", "nrfcloud"].includes(provider) && !apiKey) {
    throw new Error(`An API key / token is required for ${provider}`);
  }
  if (provider === "opencellid_local" && !localDbPath) {
    throw new Error("A local CSV or SQLite file path is required for opencellid_local");
  }

  return { provider, apiKey, endpoint, localDbPath };
}

export function publicCellGeoConfig(config) {
  const { apiKey: _apiKey, ...safe } = config;
  return {
    ...safe,
    enabled: config.provider !== "none",
    apiKeySet: Boolean(config.apiKey),
  };
}

function hasServingCellFields(value) {
  return Boolean(
    value
    && !Array.isArray(value)
    && typeof value === "object"
    && value.lat === undefined
    && value.mcc !== undefined
    && value.mnc !== undefined
    && value.cid !== undefined,
  );
}

export function isCellReportPayload(value) {
  if (!value || Array.isArray(value) || typeof value !== "object") return false;
  if (Object.hasOwn(value, "gnss") || Object.hasOwn(value, "lte")) {
    return value.gnss == null && hasServingCellFields(value.lte);
  }
  return hasServingCellFields(value);
}

function optionalInt(value, min, max, label) {
  if (value === null || value === undefined) return null;
  const num = Number(value);
  if (!Number.isInteger(num) || num < min || num > max) {
    throw new Error(`${label} must be an integer from ${min} to ${max}`);
  }
  return num;
}

function optionalNumber(value, label) {
  if (value === null || value === undefined) return null;
  const num = Number(value);
  if (!Number.isFinite(num)) {
    throw new Error(`${label} must be a finite number`);
  }
  return num;
}

export function validateCellReport(value) {
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw new Error("Cell report must be a JSON object");
  }

  const target = (
    value.mcc === undefined
    && value.lte
    && !Array.isArray(value.lte)
    && typeof value.lte === "object"
  ) ? value.lte : value;

  const mcc = optionalInt(target.mcc, 100, 999, "mcc");
  const mnc = optionalInt(target.mnc, 0, 999, "mnc");
  const cid = optionalInt(target.cid, 0, 268_435_455, "cid");
  if (mcc === null) throw new Error("mcc is required");
  if (mnc === null) throw new Error("mnc is required");
  if (cid === null) throw new Error("cid is required");

  const tac = optionalInt(target.tac, 0, 65_535, "tac");
  const earfcn = optionalInt(target.earfcn, 0, 262_143, "earfcn");
  const pci = optionalInt(target.pci, 0, 503, "pci");
  const rsrp = optionalNumber(target.rsrp, "rsrp");
  const rsrq = optionalNumber(target.rsrq, "rsrq");

  const ncells = [];
  if (Array.isArray(target.ncells)) {
    for (const entry of target.ncells) {
      if (!entry || typeof entry !== "object") continue;
      const nc = {
        earfcn: optionalInt(entry.earfcn, 0, 262_143, "ncell.earfcn"),
        pci: optionalInt(entry.pci, 0, 503, "ncell.pci"),
        rsrp: optionalNumber(entry.rsrp, "ncell.rsrp"),
        rsrq: optionalNumber(entry.rsrq, "ncell.rsrq"),
        cid: optionalInt(entry.cid, 0, 268_435_455, "ncell.cid"),
        tac: optionalInt(entry.tac, 0, 65_535, "ncell.tac"),
      };
      ncells.push(nc);
    }
  }

  return { mcc, mnc, tac, cid, earfcn, pci, rsrp, rsrq, ncells };
}

function validateCoordinates(lat, lon, acc, providerLabel) {
  const numLat = Number(lat);
  const numLon = Number(lon);
  const numAcc = acc === undefined || acc === null ? 1000 : Number(acc);

  if (!Number.isFinite(numLat) || numLat < -90 || numLat > 90) {
    throw new Error(`${providerLabel} returned an invalid latitude`);
  }
  if (!Number.isFinite(numLon) || numLon < -180 || numLon > 180) {
    throw new Error(`${providerLabel} returned an invalid longitude`);
  }
  if (!Number.isFinite(numAcc) || numAcc < 0) {
    throw new Error(`${providerLabel} returned an invalid accuracy`);
  }

  return { lat: numLat, lon: numLon, acc: numAcc };
}

function buildLocation(coords, topic, metadata = {}) {
  const now = new Date().toISOString();
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
    lat: coords.lat,
    lon: coords.lon,
    alt: 0,
    acc: coords.acc,
    deviceTime: now,
    receivedAt: now,
    topic,
    deviceId: metadata.deviceId || "",
    source: metadata.source || "lte",
  };
}

function cacheKeyFor(config, report) {
  const ncellSig = report.ncells
    .slice(0, 3)
    .map((nc) => `${nc.earfcn ?? ""}:${nc.pci ?? ""}`)
    .join(",");
  return `${config.provider}|${config.endpoint}|${config.localDbPath}|${report.mcc}:${report.mnc}:${report.tac ?? ""}:${report.cid}|${ncellSig}`;
}

async function fetchJsonWithTimeout(fetchImpl, url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetchImpl(url, { ...options, signal: controller.signal });
    const text = await response.text();
    let body = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = null;
    }
    return { ok: response.ok, status: response.status, body, text };
  } finally {
    clearTimeout(timer);
  }
}

export class LocalOpenCellIdStore {
  constructor(sourcePath, cacheDir = null) {
    this.sourcePath = sourcePath;
    this.cacheDir = cacheDir;
    this.db = null;
    this.queryWithTac = null;
    this.queryWithoutTac = null;
  }

  async ensureOpen() {
    if (this.db) return;
    const stats = statSync(this.sourcePath);
    const ext = extname(this.sourcePath).toLowerCase();

    if (ext === ".sqlite" || ext === ".db" || ext === ".sqlite3") {
      this.db = new DatabaseSync(this.sourcePath, { readOnly: true });
      this.prepareStatements();
      return;
    }

    const indexPath = this.cacheDir
      ? join(this.cacheDir, "opencellid-index.sqlite")
      : `${this.sourcePath}.sqlite`;
    mkdirSync(dirname(indexPath), { recursive: true, mode: 0o700 });
    const db = new DatabaseSync(indexPath);
    db.exec(`
      PRAGMA journal_mode = WAL;
      CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS cells (
        mcc INTEGER NOT NULL,
        mnc INTEGER NOT NULL,
        tac INTEGER NOT NULL,
        cid INTEGER NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        range INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_cells_lookup ON cells (mcc, mnc, cid, tac);
    `);

    const signature = `${this.sourcePath}:${stats.size}:${Math.trunc(stats.mtimeMs)}`;
    const existing = db.prepare("SELECT value FROM meta WHERE key = 'source_signature'").get();
    if (!existing || existing.value !== signature) {
      await this.importCsv(db, signature);
    }

    this.db = db;
    this.prepareStatements();
  }

  async importCsv(db, signature) {
    db.exec("BEGIN");
    try {
      db.exec("DELETE FROM cells");
      const insert = db.prepare(
        "INSERT INTO cells (mcc, mnc, tac, cid, lat, lon, range) VALUES (?, ?, ?, ?, ?, ?, ?)",
      );
      const stream = createReadStream(this.sourcePath, { encoding: "utf8" });
      const rl = createInterface({ input: stream, crlfDelay: Infinity });
      for await (const rawLine of rl) {
        const line = rawLine.trim();
        if (!line || line.startsWith("radio,")) continue;
        const cols = line.split(",");
        if (cols.length < 9) continue;
        const mcc = Number.parseInt(cols[1], 10);
        const mnc = Number.parseInt(cols[2], 10);
        const tac = Number.parseInt(cols[3], 10);
        const cid = Number.parseInt(cols[4], 10);
        const lon = Number.parseFloat(cols[6]);
        const lat = Number.parseFloat(cols[7]);
        const range = Number.parseInt(cols[8], 10);
        if (
          !Number.isInteger(mcc)
          || !Number.isInteger(mnc)
          || !Number.isInteger(tac)
          || !Number.isInteger(cid)
          || !Number.isFinite(lat)
          || !Number.isFinite(lon)
        ) {
          continue;
        }
        insert.run(mcc, mnc, tac, cid, lat, lon, Number.isFinite(range) && range >= 0 ? range : 1000);
      }
      db.prepare("INSERT OR REPLACE INTO meta (key, value) VALUES ('source_signature', ?)").run(signature);
      db.exec("COMMIT");
    } catch (error) {
      db.exec("ROLLBACK");
      throw error;
    }
  }

  prepareStatements() {
    const columns = new Set(this.db.prepare("PRAGMA table_info(cells)").all().map((c) => c.name));
    const mncCol = columns.has("mnc") ? "mnc" : "net";
    const tacCol = columns.has("tac") ? "tac" : "area";
    const cidCol = columns.has("cid") ? "cid" : "cell";
    const rangeCol = columns.has("range") ? "range" : "1000";

    this.queryWithTac = this.db.prepare(
      `SELECT lat, lon, ${rangeCol} AS range FROM cells WHERE mcc = ? AND ${mncCol} = ? AND ${cidCol} = ? AND ${tacCol} = ? LIMIT 1`,
    );
    this.queryWithoutTac = this.db.prepare(
      `SELECT lat, lon, ${rangeCol} AS range FROM cells WHERE mcc = ? AND ${mncCol} = ? AND ${cidCol} = ? LIMIT 1`,
    );
  }

  async lookup(report) {
    await this.ensureOpen();
    let row = null;
    if (report.tac !== null) {
      row = this.queryWithTac.get(report.mcc, report.mnc, report.cid, report.tac);
    }
    if (!row) {
      row = this.queryWithoutTac.get(report.mcc, report.mnc, report.cid);
    }
    if (!row) {
      throw new Error(
        `Cell not found in local OpenCellID database (MCC ${report.mcc}, MNC ${report.mnc}, TAC ${report.tac ?? "n/a"}, CID ${report.cid})`,
      );
    }
    return validateCoordinates(row.lat, row.lon, row.range, "Local OpenCellID");
  }

  async count() {
    await this.ensureOpen();
    const row = this.db.prepare("SELECT COUNT(*) AS count FROM cells").get();
    return Number(row?.count || 0);
  }

  close() {
    if (this.db) {
      this.db.close();
      this.db = null;
      this.queryWithTac = null;
      this.queryWithoutTac = null;
    }
  }
}

export class CellGeolocator {
  constructor(config = DEFAULT_CELL_GEO_CONFIG, options = {}) {
    this.config = { ...DEFAULT_CELL_GEO_CONFIG, ...config };
    this.fetchImpl = options.fetchImpl || globalThis.fetch;
    this.cacheTtlMs = options.cacheTtlMs ?? DEFAULT_CACHE_TTL_MS;
    this.cacheDir = options.cacheDir || null;
    this.cache = new Map();
    this.localStore = null;
    this.lastError = "";
  }

  get enabled() {
    return this.config.provider !== "none";
  }

  status() {
    return {
      enabled: this.enabled,
      provider: this.config.provider,
      lastError: this.lastError,
    };
  }

  configure(config) {
    this.close();
    this.config = { ...DEFAULT_CELL_GEO_CONFIG, ...config };
    this.cache.clear();
    this.lastError = "";
  }

  close() {
    if (this.localStore) {
      this.localStore.close();
      this.localStore = null;
    }
  }

  async resolve(rawReport, topic = "", metadata = {}) {
    const report = validateCellReport(rawReport);
    const locationMeta = {
      deviceId: metadata.deviceId ?? (typeof rawReport?.device_id === "string" ? rawReport.device_id.trim() : ""),
      source: metadata.source ?? (typeof rawReport?.source === "string" ? rawReport.source.trim().toLowerCase() : "lte"),
    };

    if (!this.enabled) {
      const message = `LTE cell report received (MCC ${report.mcc}, MNC ${report.mnc}, CID ${report.cid}) — configure Cell Geolocation in Connections to resolve coordinates`;
      this.lastError = message;
      throw new Error(message);
    }

    const key = cacheKeyFor(this.config, report);
    const cached = this.cache.get(key);
    if (cached && cached.expiresAt > Date.now()) {
      this.lastError = "";
      return buildLocation(cached.coords, topic, locationMeta);
    }

    let coords;
    try {
      switch (this.config.provider) {
        case "unwiredlabs":
          coords = await this.resolveUnwiredLabs(report);
          break;
        case "google":
          coords = await this.resolveGoogle(report);
          break;
        case "nrfcloud":
          coords = await this.resolveNrfCloud(report);
          break;
        case "opencellid_local":
          coords = await this.resolveOpenCellIdLocal(report);
          break;
        default:
          throw new Error(`Unsupported cell geolocation provider: ${this.config.provider}`);
      }
      this.lastError = "";
    } catch (error) {
      this.lastError = error.message || String(error);
      throw error;
    }

    if (this.cacheTtlMs > 0) {
      this.cache.set(key, { coords, expiresAt: Date.now() + this.cacheTtlMs });
    }
    return buildLocation(coords, topic, locationMeta);
  }

  async resolveUnwiredLabs(report) {
    const url = this.config.endpoint || DEFAULT_CELL_GEO_ENDPOINTS.unwiredlabs;
    const serving = { cid: report.cid };
    if (report.tac !== null) serving.lac = report.tac;
    if (report.rsrp !== null) serving.signal = report.rsrp;
    if (report.pci !== null) serving.psc = report.pci;

    const cells = [serving];
    for (const nc of report.ncells) {
      if (nc.cid !== null) {
        const entry = { cid: nc.cid };
        if (nc.tac !== null) entry.lac = nc.tac;
        if (nc.rsrp !== null) entry.signal = nc.rsrp;
        if (nc.pci !== null) entry.psc = nc.pci;
        cells.push(entry);
      }
    }

    const payload = {
      token: this.config.apiKey,
      radio: "lte",
      mcc: report.mcc,
      mnc: report.mnc,
      cells,
      address: 0,
    };

    const { ok, status, body, text } = await fetchJsonWithTimeout(this.fetchImpl, url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!ok || !body || body.status !== "ok") {
      const detail = body?.message || text || `HTTP ${status}`;
      throw new Error(`Unwired Labs lookup failed: ${detail}`);
    }
    return validateCoordinates(body.lat, body.lon, body.accuracy, "Unwired Labs");
  }

  async resolveGoogle(report) {
    const base = this.config.endpoint || DEFAULT_CELL_GEO_ENDPOINTS.google;
    const url = new URL(base);
    url.searchParams.set("key", this.config.apiKey);

    const serving = {
      cellId: report.cid,
      mobileCountryCode: report.mcc,
      mobileNetworkCode: report.mnc,
    };
    if (report.tac !== null) serving.locationAreaCode = report.tac;
    if (report.rsrp !== null) serving.signalStrength = report.rsrp;

    const cellTowers = [serving];
    for (const nc of report.ncells) {
      if (nc.cid !== null) {
        const entry = {
          cellId: nc.cid,
          mobileCountryCode: report.mcc,
          mobileNetworkCode: report.mnc,
        };
        if (nc.tac !== null) entry.locationAreaCode = nc.tac;
        if (nc.rsrp !== null) entry.signalStrength = nc.rsrp;
        cellTowers.push(entry);
      }
    }

    const payload = {
      considerIp: false,
      radioType: "lte",
      homeMobileCountryCode: report.mcc,
      homeMobileNetworkCode: report.mnc,
      cellTowers,
    };

    const { ok, status, body, text } = await fetchJsonWithTimeout(this.fetchImpl, url.toString(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!ok || !body?.location) {
      const detail = body?.error?.message || text || `HTTP ${status}`;
      throw new Error(`Google Geolocation lookup failed: ${detail}`);
    }
    return validateCoordinates(body.location.lat, body.location.lng, body.accuracy, "Google Geolocation");
  }

  async resolveNrfCloud(report) {
    const url = this.config.endpoint || DEFAULT_CELL_GEO_ENDPOINTS.nrfcloud;
    const cell = {
      mcc: report.mcc,
      mnc: report.mnc,
      eci: report.cid,
    };
    if (report.tac !== null) cell.tac = report.tac;
    if (report.earfcn !== null) cell.earfcn = report.earfcn;
    if (report.pci !== null) cell.pci = report.pci;
    if (report.rsrp !== null) cell.rsrp = report.rsrp;
    if (report.rsrq !== null) cell.rsrq = report.rsrq;

    const nmr = report.ncells
      .filter((nc) => nc.earfcn !== null && nc.pci !== null)
      .map((nc) => {
        const item = { earfcn: nc.earfcn, pci: nc.pci };
        if (nc.rsrp !== null) item.rsrp = nc.rsrp;
        if (nc.rsrq !== null) item.rsrq = nc.rsrq;
        return item;
      });
    if (nmr.length > 0) cell.nmr = nmr;

    const { ok, status, body, text } = await fetchJsonWithTimeout(this.fetchImpl, url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.config.apiKey}`,
      },
      body: JSON.stringify({ lte: [cell] }),
    });

    if (!ok || !body) {
      const detail = body?.message || text || `HTTP ${status}`;
      throw new Error(`nRF Cloud lookup failed: ${detail}`);
    }
    return validateCoordinates(body.lat, body.lon, body.uncertainty, "nRF Cloud");
  }

  async resolveOpenCellIdLocal(report) {
    if (!this.localStore) {
      this.localStore = new LocalOpenCellIdStore(this.config.localDbPath, this.cacheDir);
    }
    return this.localStore.lookup(report);
  }
}

export async function testCellGeolocation(config, options = {}) {
  if (!config || config.provider === "none") {
    throw new Error("Select a Cell Geolocation provider before testing");
  }

  if (config.provider === "opencellid_local") {
    const store = new LocalOpenCellIdStore(config.localDbPath, options.cacheDir || null);
    try {
      const count = await store.count();
      if (count === 0) {
        throw new Error("Local OpenCellID file opened, but contains 0 valid cell records");
      }
      return { ok: true, message: `Local OpenCellID database ready (${count.toLocaleString()} cells).` };
    } finally {
      store.close();
    }
  }

  const sampleCell = options.sampleCell || { mcc: 310, mnc: 410, tac: 7033, cid: 17811 };
  const locator = new CellGeolocator(config, { ...options, cacheTtlMs: 0 });
  try {
    const result = await locator.resolve(sampleCell, "test");
    return {
      ok: true,
      message: `${config.provider} lookup succeeded (${result.lat.toFixed(4)}, ${result.lon.toFixed(4)}, ±${Math.round(result.acc)} m).`,
    };
  } finally {
    locator.close();
  }
}
