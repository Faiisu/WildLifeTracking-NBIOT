import { createServer } from "node:http";
import { createReadStream } from "node:fs";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { SerialPort } from "serialport";

const APP_DIR = dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = join(APP_DIR, "public");
const DATA_DIR = process.env.DATA_DIR || join(APP_DIR, "data");
const CONFIG_FILE = join(DATA_DIR, "config.json");
const HOST = process.env.HOST || "127.0.0.1";
const PORT = Number.parseInt(process.env.PORT || "3100", 10);
const MAX_BODY_BYTES = 16 * 1024;
const MAX_LINE_BUFFER = 64 * 1024;
const MAX_LOG_LINES = 300;

export const DEFAULT_CONFIG = Object.freeze({
  portPath: "",
  baudRate: 115200,
  updateIntervalMs: 1000,
  historyLimit: 600,
  autoConnect: true,
});

const POWER_LINE = /^B(?<channel>\d+)\s*=\s*(?<battery>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*V\s*,\s*V\k<channel>\s*=\s*(?<voltage>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*V\s*,\s*I\k<channel>\s*=\s*(?<current>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*mA\s*,\s*P\k<channel>\s*=\s*(?<power>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*W\s*$/i;

const MIME_TYPES = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
};

export function parsePowerLine(line, timestamp = new Date()) {
  const match = String(line).trim().match(POWER_LINE);
  if (!match) return null;

  const values = {
    battery: Number(match.groups.battery),
    voltage: Number(match.groups.voltage),
    current: Number(match.groups.current),
    power: Number(match.groups.power),
  };
  if (!Object.values(values).every(Number.isFinite)) return null;

  return {
    timestamp: timestamp.toISOString(),
    channel: Number(match.groups.channel),
    ...values,
  };
}

export function validateConfig(input, previous = DEFAULT_CONFIG) {
  if (!input || Array.isArray(input) || typeof input !== "object") {
    throw new Error("Configuration must be a JSON object");
  }

  const portPath = String(input.portPath ?? previous.portPath ?? "").trim();
  if (portPath.length > 1024 || portPath.includes("\0")) {
    throw new Error("Serial port path is invalid");
  }

  const baudRate = Number(input.baudRate ?? previous.baudRate);
  if (!Number.isInteger(baudRate) || baudRate < 300 || baudRate > 4_000_000) {
    throw new Error("Baud rate must be an integer from 300 to 4000000");
  }

  const updateIntervalMs = Number(input.updateIntervalMs ?? previous.updateIntervalMs);
  if (!Number.isInteger(updateIntervalMs) || updateIntervalMs < 100 || updateIntervalMs > 60_000) {
    throw new Error("Update interval must be an integer from 100 to 60000 ms");
  }

  const historyLimit = Number(input.historyLimit ?? previous.historyLimit);
  if (!Number.isInteger(historyLimit) || historyLimit < 50 || historyLimit > 5_000) {
    throw new Error("History limit must be an integer from 50 to 5000");
  }

  return {
    portPath,
    baudRate,
    updateIntervalMs,
    historyLimit,
    autoConnect: input.autoConnect !== false,
  };
}

async function readConfig() {
  try {
    const saved = JSON.parse(await readFile(CONFIG_FILE, "utf8"));
    return validateConfig(saved);
  } catch (error) {
    if (error.code !== "ENOENT") console.warn(`Could not load saved configuration: ${error.message}`);
    return { ...DEFAULT_CONFIG };
  }
}

async function writeConfig(config) {
  await mkdir(DATA_DIR, { recursive: true, mode: 0o700 });
  const temporary = `${CONFIG_FILE}.tmp`;
  await writeFile(temporary, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
  await rename(temporary, CONFIG_FILE);
}

function publicPort(port) {
  return {
    path: port.path,
    manufacturer: port.manufacturer || "",
    serialNumber: port.serialNumber || "",
    vendorId: port.vendorId || "",
    productId: port.productId || "",
    label: [port.path, port.manufacturer].filter(Boolean).join(" — "),
  };
}

export class PowerMonitorService {
  constructor(config, serialApi = SerialPort) {
    this.config = config;
    this.serialApi = serialApi;
    this.serial = null;
    this.state = "disconnected";
    this.lastError = "";
    this.lineBuffer = "";
    this.readings = [];
    this.logs = [];
    this.pending = new Map();
    this.latest = new Map();
    this.listeners = new Set();
    this.publishTimer = null;
    this.reconnectTimer = null;
    this.startPublisher();
  }

  status() {
    return {
      state: this.state,
      lastError: this.lastError,
      config: this.config,
      readingCount: this.readings.length,
      latest: Object.fromEntries(this.latest),
    };
  }

  async listPorts() {
    return (await this.serialApi.list()).map(publicPort);
  }

  startPublisher() {
    clearInterval(this.publishTimer);
    this.publishTimer = setInterval(() => this.publish(), this.config.updateIntervalMs);
    this.publishTimer.unref?.();
  }

  publish() {
    if (this.pending.size === 0) return;
    const samples = [...this.pending.values()].sort((a, b) => a.channel - b.channel);
    this.pending.clear();
    this.broadcast("reading", { samples });
  }

  ingest(chunk) {
    this.lineBuffer += chunk.toString("utf8");
    if (this.lineBuffer.length > MAX_LINE_BUFFER) {
      this.lineBuffer = this.lineBuffer.slice(-MAX_LINE_BUFFER);
      this.recordLog("Line buffer exceeded 64 KiB; discarded older serial data", "warning");
    }

    const lines = this.lineBuffer.split(/\r?\n/);
    this.lineBuffer = lines.pop() || "";
    for (const line of lines) this.ingestLine(line);
  }

  ingestLine(line) {
    const clean = String(line).trim();
    if (!clean) return;
    this.recordLog(clean, "serial");
    const reading = parsePowerLine(clean);
    if (!reading) return;
    this.readings.push(reading);
    if (this.readings.length > this.config.historyLimit) {
      this.readings.splice(0, this.readings.length - this.config.historyLimit);
    }
    this.pending.set(reading.channel, reading);
    this.latest.set(reading.channel, reading);
  }

  recordLog(message, kind = "system") {
    const entry = { timestamp: new Date().toISOString(), message, kind };
    this.logs.push(entry);
    if (this.logs.length > MAX_LOG_LINES) this.logs.shift();
    this.broadcast("log", entry);
  }

  setState(state, error = "") {
    this.state = state;
    this.lastError = error;
    this.broadcast("status", this.status());
  }

  broadcast(event, value) {
    const frame = `event: ${event}\ndata: ${JSON.stringify(value)}\n\n`;
    for (const response of this.listeners) response.write(frame);
  }

  addListener(response) {
    this.listeners.add(response);
    response.write(`event: status\ndata: ${JSON.stringify(this.status())}\n\n`);
    const heartbeat = setInterval(() => response.write(": keepalive\n\n"), 20_000);
    heartbeat.unref?.();
    return () => {
      clearInterval(heartbeat);
      this.listeners.delete(response);
    };
  }

  async connect() {
    clearTimeout(this.reconnectTimer);
    if (!this.config.portPath) {
      this.setState("disconnected", "Select a serial port to begin");
      return;
    }
    await this.disconnect(false);
    this.setState("connecting");

    const serial = new this.serialApi({
      path: this.config.portPath,
      baudRate: this.config.baudRate,
      autoOpen: false,
    });
    this.serial = serial;
    serial.on("data", (chunk) => this.ingest(chunk));
    serial.on("error", (error) => this.handleSerialFailure(error));
    serial.on("close", () => {
      if (this.serial !== serial) return;
      this.serial = null;
      this.setState("disconnected", this.lastError);
      this.scheduleReconnect();
    });

    try {
      await new Promise((resolve, reject) => serial.open((error) => error ? reject(error) : resolve()));
      this.lineBuffer = "";
      this.setState("connected");
      this.recordLog(`Opened ${this.config.portPath} at ${this.config.baudRate} baud`);
    } catch (error) {
      if (this.serial === serial) this.serial = null;
      this.setState("error", error.message);
      this.recordLog(`Could not open ${this.config.portPath}: ${error.message}`, "error");
      this.scheduleReconnect();
    }
  }

  handleSerialFailure(error) {
    this.lastError = error.message;
    this.recordLog(`Serial error: ${error.message}`, "error");
    this.setState("error", error.message);
  }

  scheduleReconnect() {
    clearTimeout(this.reconnectTimer);
    if (!this.config.autoConnect || !this.config.portPath) return;
    this.reconnectTimer = setTimeout(() => this.connect(), 5_000);
    this.reconnectTimer.unref?.();
  }

  async disconnect(updateState = true) {
    clearTimeout(this.reconnectTimer);
    const serial = this.serial;
    this.serial = null;
    if (serial?.isOpen) {
      await new Promise((resolve) => serial.close(() => resolve()));
    }
    if (updateState) {
      this.setState("disconnected");
      this.recordLog("Serial connection stopped");
    }
  }

  async updateConfig(input) {
    const next = validateConfig(input, this.config);
    const reconnect = next.portPath !== this.config.portPath || next.baudRate !== this.config.baudRate;
    const intervalChanged = next.updateIntervalMs !== this.config.updateIntervalMs;
    this.config = next;
    if (this.readings.length > next.historyLimit) {
      this.readings.splice(0, this.readings.length - next.historyLimit);
    }
    if (intervalChanged) this.startPublisher();
    await writeConfig(next);
    this.recordLog(`Settings saved; dashboard updates every ${(next.updateIntervalMs / 1000).toFixed(1)} s`);
    if (reconnect || this.state !== "connected") await this.connect();
    return this.status();
  }
}

function json(response, status, value) {
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
      if (total > MAX_BODY_BYTES) {
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

function serveStatic(pathname, response) {
  const requested = pathname === "/" ? "index.html" : pathname.slice(1);
  const normalized = normalize(requested).replace(/^(\.\.(\/|\\|$))+/, "");
  const file = join(PUBLIC_DIR, normalized);
  if (!file.startsWith(PUBLIC_DIR)) {
    json(response, 403, { error: "Forbidden" });
    return;
  }
  const stream = createReadStream(file);
  stream.on("open", () => {
    response.writeHead(200, {
      "Content-Type": MIME_TYPES[extname(file)] || "application/octet-stream",
      "X-Content-Type-Options": "nosniff",
    });
    stream.pipe(response);
  });
  stream.on("error", (error) => json(response, error.code === "ENOENT" ? 404 : 500, { error: "File not found" }));
}

export function createApp(service) {
  return createServer(async (request, response) => {
    const url = new URL(request.url, `http://${request.headers.host || "localhost"}`);
    try {
      if (request.method === "GET" && url.pathname === "/api/status") return json(response, 200, service.status());
      if (request.method === "GET" && url.pathname === "/api/ports") return json(response, 200, { ports: await service.listPorts() });
      if (request.method === "GET" && url.pathname === "/api/readings") {
        const limit = Math.min(Math.max(Number(url.searchParams.get("limit")) || 600, 1), 5_000);
        return json(response, 200, { readings: service.readings.slice(-limit), logs: service.logs });
      }
      if (request.method === "GET" && url.pathname === "/api/events") {
        response.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
          "X-Accel-Buffering": "no",
        });
        const remove = service.addListener(response);
        request.on("close", remove);
        return;
      }
      if (request.method === "PUT" && url.pathname === "/api/config") return json(response, 200, await service.updateConfig(await readBody(request)));
      if (request.method === "POST" && url.pathname === "/api/connect") {
        await service.connect();
        return json(response, 200, service.status());
      }
      if (request.method === "POST" && url.pathname === "/api/disconnect") {
        await service.disconnect();
        return json(response, 200, service.status());
      }
      if (request.method === "GET") return serveStatic(url.pathname, response);
      return json(response, 404, { error: "Not found" });
    } catch (error) {
      console.error(error);
      return json(response, error.status || 400, { error: error.message || "Request failed" });
    }
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  const config = await readConfig();
  const service = new PowerMonitorService(config);
  const server = createApp(service);
  server.listen(PORT, HOST, () => {
    console.log(`Power Monitor is running at http://${HOST}:${PORT}`);
    if (config.autoConnect && config.portPath) service.connect();
  });
}
