import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../static/", import.meta.url));
const clients = new Set();
const replay = [];
let nextId = Date.now() * 1000;
let requestCount = 0;

const mime = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
};

function wire(event) {
  return `id: ${event.id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
}

function publish(input) {
  const event = {
    id: Number(input.id || ++nextId),
    type: String(input.type || "message"),
    resourceId: String(input.resourceId || ""),
    time: input.time || new Date().toISOString(),
    data: input.data || {},
  };
  nextId = Math.max(nextId, event.id);
  replay.push(event);
  if (replay.length > 256) replay.shift();
  for (const response of clients) response.write(wire(event));
  return event;
}

async function readJson(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url || "/", "http://127.0.0.1:43817");
  if (url.pathname === "/api/events") {
    requestCount += 1;
    response.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Access-Control-Allow-Origin": "*",
    });
    const lastId = Number(request.headers["last-event-id"] || url.searchParams.get("lastEventId") || 0);
    for (const event of replay) if (event.id > lastId) response.write(wire(event));
    const ready = { id: ++nextId, type: "stream.ready", resourceId: "", time: new Date().toISOString(), data: { lastEventId: nextId } };
    response.write(wire(ready));
    clients.add(response);
    const timer = setInterval(() => {
      if (!response.destroyed) {
        const heartbeat = { id: ++nextId, type: "stream.heartbeat", resourceId: "", time: new Date().toISOString(), data: { lastEventId: nextId } };
        response.write(wire(heartbeat));
      }
    }, 2000);
    request.on("close", () => { clearInterval(timer); clients.delete(response); });
    return;
  }
  if (url.pathname === "/__test/sse/emit" && request.method === "POST") {
    const event = publish(await readJson(request));
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify(event));
    return;
  }
  if (url.pathname === "/__test/sse/drop" && request.method === "POST") {
    for (const client of [...clients]) client.destroy();
    response.writeHead(204).end();
    return;
  }
  if (url.pathname === "/__test/sse/stats") {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ connections: clients.size, requests: requestCount, latestId: nextId }));
    return;
  }
  const relative = url.pathname === "/" ? "index.html" : decodeURIComponent(url.pathname.slice(1));
  const safe = normalize(relative).replace(/^(\.\.(\/|\\|$))+/, "");
  const file = join(root, safe);
  if (!file.startsWith(root) || !existsSync(file) || !statSync(file).isFile()) {
    response.writeHead(404).end("not found");
    return;
  }
  response.writeHead(200, { "Content-Type": mime[extname(file)] || "application/octet-stream" });
  createReadStream(file).pipe(response);
});

server.listen(43817, "127.0.0.1", () => console.log("e2e server on 43817"));
