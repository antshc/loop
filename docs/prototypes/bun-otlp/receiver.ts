// Tiny OTLP/HTTP receiver: records each request to out/received.jsonl and answers 200 with an empty JSON body.
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";

const port = Number(process.env.PORT ?? 4318);
mkdirSync("out", { recursive: true });
writeFileSync("out/received.jsonl", "");

Bun.serve({
  port,
  hostname: "127.0.0.1",
  async fetch(req) {
    const url = new URL(req.url);
    if (process.env.HANG) await new Promise(() => {}); // accept, never answer (cf. opentelemetry-js#5260)
    const body = new Uint8Array(await req.arrayBuffer());
    const contentType = req.headers.get("content-type") ?? "";
    const entry: Record<string, unknown> = {
      path: url.pathname,
      contentType,
      contentEncoding: req.headers.get("content-encoding"),
      bytes: body.length,
      at: Date.now(),
    };
    if (contentType.includes("json")) entry.body = JSON.parse(new TextDecoder().decode(body));
    appendFileSync("out/received.jsonl", JSON.stringify(entry) + "\n");
    return new Response("{}", { headers: { "content-type": "application/json" } });
  },
});
console.log(`receiver listening on ${port}`);
