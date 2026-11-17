// Serves the exported browser build with a fake hub on the same origin, as the real hub serves
// /app. For looking at the browser build by hand: bun test/serve-web.ts [port]
import { join } from "node:path";

import { FakeHub, question } from "./fakehub";

const hub = new FakeHub();
hub.say("Morning. The desk lamp is on.");
hub.questions = [question("q00000001")];
const dist = join(import.meta.dir, "..", "dist");

const server = Bun.serve({
  port: Number(process.argv[2] ?? 0),
  async fetch(req) {
    const url = new URL(req.url);
    if (url.pathname.startsWith("/v1/")) return fetch(hub.base + url.pathname + url.search, { method: req.method, headers: req.headers, body: req.body });
    const file = Bun.file(join(dist, url.pathname));
    return (await file.exists()) && url.pathname !== "/" ? new Response(file) : new Response(Bun.file(join(dist, "index.html")));
  },
});
console.log(`http://127.0.0.1:${server.port}/`);
