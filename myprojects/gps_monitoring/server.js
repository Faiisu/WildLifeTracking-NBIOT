import { createServer } from "node:http";
import { appendFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { createReadStream } from "node:fs";
import { dirname, extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";
import mqtt from "mqtt";

const APP_DIR = dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = join(APP_DIR, "public");
const DATA_DIR = process.env.DATA_DIR || join(APP_DIR, "data");
const CONFIG_FILE = join(DATA_DIR, "mqtt-config.json");
const HISTORY_FILE = join(DATA_DIR, "locations.jsonl");
const PORT = Number.parseInt(process.env.PORT || "3000", 10);
const HOST = process.env.HOST || "127.0.0.1";
const MAX_MEMORY_HISTORY = 5_000;
const MAX_REQUEST_BYTES = 64 * 1024;

const DEFAULT_CONFIG = Object.freeze({
  protocol: "mqtt",
  host: "localhost",
  port: 1883,
  topic: "assets/+/location",
  clientId: "gos-monitoring-web",
  username: "",
  password: "",
  qos: 0,
  rejectUnauthorized: true,
});

const MIME_TYPES = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
};

export function validateLocation(payload, topic = "") {
  let value;
  try {
    value = typeof payload === "string" ? JSON.parse(payload) : payload;
  } catch {
    throw new Error("Payload is not valid JSON");
  }

  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw new Error("Payload must be a JSON object");
  }

  const lat = Number(value.lat);
  const lon = Number(value.lon);
  const alt = Number(value.alt);
  const acc = Number(value.acc);
  const deviceTime = new Date(value.time);

  if (!Number.isFinite(lat) || lat < -90 || lat > 90) {
    throw new Error("lat must be a number from -90 to 90");
  }
  if (!Number.isFinite(lon) || lon < -180 || lon > 180) {
    throw new Error("lon must be a number from -180 to 180");
  }
  if (!Number.isFinite(alt)) {
    throw new Error("alt must be a number");
  }
  if (!Number.isFinite(acc) || acc < 0) {
    throw new Error("acc must be a non-negative number");
  }
  if (Number.isNaN(deviceTime.getTime())) {
    throw new Error("time must be an ISO-8601 date/time");
  }

  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
    lat,
    lon,
    alt,
    acc,
    deviceTime: deviceTime.toISOString(),
    receivedAt: new Date().toISOString(),
    topic,
  };
}

export function validateConfig(input, previous = DEFAULT_CONFIG) {
  if (!input || Array.isArray(input) || typeof input !== "object") {
    throw new Error("Configuration must be a JSON object");
  }

  const protocol = String(input.protocol || "").toLowerCase();
  if (!["mqtt", "mqtts", "ws", "wss"].includes(protocol)) {
    throw new Error("Protocol must be mqtt, mqtts, ws, or wss");
  }

  const host = String(input.host || "").trim();
  if (!host || host.length > 253 || /[\s/:]/.test(host)) {
    throw new Error("Host must be a hostname or IP address without a scheme or port");
  }

  const port = Number(input.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("Port must be an integer from 1 to 65535");
  }

  const topic = String(input.topic || "").trim();
  if (!topic || topic.length > 512 || topic.includes("\0")) {
    throw new Error("Topic is required and must be 512 characters or fewer");
  }

  const clientId = String(input.clientId || "").trim();
  if (!clientId || clientId.length > 128) {
    throw new Error("Client ID is required and must be 128 characters or fewer");
  }

  const qos = Number(input.qos);
  if (![0, 1, 2].includes(qos)) {
    throw new Error("QoS must be 0, 1, or 2");
  }

  let password = previous.password || "";
  if (input.clearPassword === true) password = "";
  if (typeof input.password === "string" && input.password.length > 0) password = input.password;
  if (password.length > 1024) throw new Error("Password is too long");

  return {
    protocol,
    host,
    port,
    topic,
    clientId,
    username: String(input.username || "").trim().slice(0, 512),
    password,
    qos,
    rejectUnauthorized: input.rejectUnauthorized !== false,
  };
}

function publicConfig(config) {
  const { password: _password, ...safe } = config;
  return { ...safe, passwordSet: Boolean(config.password) };
}

async function readJson(file, fallback) {
  try {
    return JSON.parse(await readFile(file, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return fallback;
    console.warn(`Could not read ${file}: ${error.message}`);
    return fallback;
  }
}

async function writeJsonAtomic(file, value) {
  await mkdir(dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  await rename(temporary, file);
}

export async function loadHistory(file = HISTORY_FILE, limit = MAX_MEMORY_HISTORY) {
  try {
    const lines = (await readFile(file, "utf8")).split("\n").filter(Boolean);
    const result = [];
    for (const line of lines.slice(-limit)) {
      try {
        result.push(JSON.parse(line));
      } catch {
        console.warn("Skipped a malformed location history line");
      }
    }
    return result;
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}

function jsonResponse(response, status, value) {
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(JSON.stringify(value));
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    request.on("data", (chunk) => {
      total += chunk.length;
      if (total > MAX_REQUEST_BYTES) {
        reject(Object.assign(new Error("Request body is too large"), { status: 413 }));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch {
        reject(Object.assign(new Error("Request body must be valid JSON"), { status: 400 }));
      }
    });
    request.on("error", reject);
  });
}

class LocationService {
  constructor(config, history) {
    this.config = config;
    this.history = history;
    this.client = null;
    this.listeners = new Set();
    this.state = "disconnected";
    this.lastError = "";
    this.invalidMessages = 0;
  }

  status() {
    return {
      state: this.state,
      lastError: this.lastError,
      invalidMessages: this.invalidMessages,
      pointsStored: this.history.length,
      config: publicConfig(this.config),
    };
  }

  broadcast(type, data) {
    const event = `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
    for (const response of this.listeners) response.write(event);
  }

  setState(state, lastError = "") {
    this.state = state;
    this.lastError = lastError;
    this.broadcast("status", this.status());
  }

  async saveConfig(input) {
    this.config = validateConfig(input, this.config);
    await writeJsonAtomic(CONFIG_FILE, this.config);
    this.connect();
    return this.status();
  }

  connect() {
    if (this.client) {
      this.client.removeAllListeners();
      this.client.end(true);
      this.client = null;
    }

    const config = this.config;
    this.setState("connecting");
    const url = `${config.protocol}://${config.host}:${config.port}`;
    const options = {
      clientId: config.clientId,
      clean: true,
      reconnectPeriod: 5_000,
      connectTimeout: 15_000,
      username: config.username || undefined,
      password: config.password || undefined,
      rejectUnauthorized: config.rejectUnauthorized,
    };

    this.client = mqtt.connect(url, options);
    this.client.on("connect", () => {
      this.client.subscribe(config.topic, { qos: config.qos }, (error) => {
        if (error) {
          this.setState("error", `Subscribe failed: ${error.message}`);
          return;
        }
        this.setState("connected");
        console.log(`MQTT connected; subscribed to ${config.topic}`);
      });
    });
    this.client.on("reconnect", () => this.setState("connecting"));
    this.client.on("offline", () => this.setState("disconnected"));
    this.client.on("error", (error) => {
      this.setState("error", error.message);
      console.error(`MQTT error: ${error.message}`);
    });
    this.client.on("message", async (topic, payload) => {
      try {
        const location = validateLocation(payload.toString("utf8"), topic);
        await mkdir(DATA_DIR, { recursive: true, mode: 0o700 });
        await appendFile(HISTORY_FILE, `${JSON.stringify(location)}\n`, { mode: 0o600 });
        this.history.push(location);
        if (this.history.length > MAX_MEMORY_HISTORY) this.history.shift();
        this.broadcast("location", location);
      } catch (error) {
        this.invalidMessages += 1;
        this.lastError = `Ignored message: ${error.message}`;
        this.broadcast("status", this.status());
        console.warn(`Ignored MQTT message on ${topic}: ${error.message}`);
      }
    });
  }

  disconnect() {
    if (this.client) {
      this.client.removeAllListeners();
      this.client.end(true);
      this.client = null;
    }
    this.setState("disconnected");
  }
}

function resolveStaticFile(pathname) {
  if (pathname.startsWith("/vendor/leaflet/")) {
    const relative = pathname.slice("/vendor/leaflet/".length);
    const root = join(APP_DIR, "node_modules", "leaflet", "dist");
    const file = normalize(join(root, relative));
    return file.startsWith(root) ? file : null;
  }
  const relative = pathname === "/" ? "index.html" : pathname.slice(1);
  const file = normalize(join(PUBLIC_DIR, relative));
  return file.startsWith(PUBLIC_DIR) ? file : null;
}

async function serveStatic(pathname, response) {
  const file = resolveStaticFile(pathname);
  if (!file) return jsonResponse(response, 404, { error: "Not found" });
  try {
    const stream = createReadStream(file);
    stream.on("error", (error) => {
      if (!response.headersSent) jsonResponse(response, error.code === "ENOENT" ? 404 : 500, { error: "File unavailable" });
      else response.destroy(error);
    });
    response.writeHead(200, {
      "Content-Type": MIME_TYPES[extname(file)] || "application/octet-stream",
      "Cache-Control": pathname.startsWith("/vendor/") ? "public, max-age=86400" : "no-cache",
      "X-Content-Type-Options": "nosniff",
    });
    stream.pipe(response);
  } catch {
    jsonResponse(response, 404, { error: "Not found" });
  }
}

export async function createApplication() {
  await mkdir(DATA_DIR, { recursive: true, mode: 0o700 });
  const savedConfig = await readJson(CONFIG_FILE, DEFAULT_CONFIG);
  let config;
  try {
    config = validateConfig(savedConfig, DEFAULT_CONFIG);
  } catch (error) {
    console.warn(`Using default MQTT configuration: ${error.message}`);
    config = { ...DEFAULT_CONFIG };
  }
  const history = await loadHistory();
  const service = new LocationService(config, history);

  const server = createServer(async (request, response) => {
    const url = new URL(request.url, "http://localhost");
    try {
      if (request.method === "GET" && url.pathname === "/api/status") {
        return jsonResponse(response, 200, service.status());
      }
      if (request.method === "GET" && url.pathname === "/api/locations") {
        const requested = Number.parseInt(url.searchParams.get("limit") || "500", 10);
        const limit = Number.isFinite(requested) ? Math.min(Math.max(requested, 1), MAX_MEMORY_HISTORY) : 500;
        return jsonResponse(response, 200, service.history.slice(-limit));
      }
      if (request.method === "PUT" && url.pathname === "/api/config") {
        const status = await service.saveConfig(await readBody(request));
        return jsonResponse(response, 200, status);
      }
      if (request.method === "POST" && url.pathname === "/api/connect") {
        service.connect();
        return jsonResponse(response, 202, service.status());
      }
      if (request.method === "POST" && url.pathname === "/api/disconnect") {
        service.disconnect();
        return jsonResponse(response, 200, service.status());
      }
      if (request.method === "GET" && url.pathname === "/api/events") {
        response.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        });
        response.write(`event: status\ndata: ${JSON.stringify(service.status())}\n\n`);
        service.listeners.add(response);
        const heartbeat = setInterval(() => response.write(": heartbeat\n\n"), 25_000);
        request.on("close", () => {
          clearInterval(heartbeat);
          service.listeners.delete(response);
        });
        return;
      }
      if (request.method !== "GET") return jsonResponse(response, 404, { error: "Not found" });
      return serveStatic(url.pathname, response);
    } catch (error) {
      console.error(error);
      return jsonResponse(response, error.status || 400, { error: error.message || "Request failed" });
    }
  });

  return { server, service };
}

if (process.argv[1] && normalize(process.argv[1]) === normalize(fileURLToPath(import.meta.url))) {
  const { server, service } = await createApplication();
  server.listen(PORT, HOST, () => {
    console.log(`GOS Monitoring is running at http://${HOST}:${PORT}`);
    service.connect();
  });

  const shutdown = () => {
    service.disconnect();
    server.close(() => process.exit(0));
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}
