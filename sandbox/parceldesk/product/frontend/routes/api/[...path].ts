import type { Handlers } from "$fresh/server.ts";
import { backendUrl } from "../../lib/api.ts";

const ALLOWED = new Set(["orders", "summary", "pulls", "health", "refunds.csv"]);
const LIMIT = 250_000;

async function forward(request: Request, path: string): Promise<Response> {
  const first = path.split("/")[0];
  if (!ALLOWED.has(first)) {
    return new Response(JSON.stringify({ ok: false, error: "Unknown API path" }), {
      status: 404,
      headers: { "content-type": "application/json" },
    });
  }
  const url = new URL(request.url);
  const target = `${backendUrl}/api/${path}${url.search}`;
  const headers: Record<string, string> = { accept: path.endsWith(".csv") ? "text/csv" : "application/json" };
  let body: ArrayBuffer | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    body = await request.arrayBuffer();
    if (body.byteLength > LIMIT) {
      return new Response(JSON.stringify({ ok: false, error: "Request body too large" }), {
        status: 413,
        headers: { "content-type": "application/json" },
      });
    }
    const type = request.headers.get("content-type");
    if (type) headers["content-type"] = type;
  }
  try {
    const response = await fetch(target, { method: request.method, headers, body });
    const contentType = response.headers.get("content-type") ?? "application/json";
    const outgoing: Record<string, string> = { "content-type": contentType };
    const disposition = response.headers.get("content-disposition");
    if (disposition) outgoing["content-disposition"] = disposition;
    return new Response(response.body, { status: response.status, headers: outgoing });
  } catch (error) {
    return new Response(JSON.stringify({ ok: false, error: `API unavailable: ${error}` }), {
      status: 502,
      headers: { "content-type": "application/json" },
    });
  }
}

export const handler: Handlers = {
  GET(request, ctx) {
    return forward(request, ctx.params.path ?? "");
  },
  POST(request, ctx) {
    return forward(request, ctx.params.path ?? "");
  },
};
