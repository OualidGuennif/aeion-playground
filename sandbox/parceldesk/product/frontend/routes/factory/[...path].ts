import type { Handlers } from "$fresh/server.ts";
import { cockpitUrl } from "../../lib/api.ts";

const ALLOWED = new Set(["parceldesk", "missions", "health"]);
const LIMIT = 100_000;

async function forward(request: Request, path: string): Promise<Response> {
  const first = path.split("/")[0];
  if (!ALLOWED.has(first)) {
    return new Response(JSON.stringify({ ok: false, error: "Unknown Factory path" }), {
      status: 404,
      headers: { "content-type": "application/json" },
    });
  }
  const url = new URL(request.url);
  const target = `${cockpitUrl}/api/${path}${url.search}`;
  const headers: Record<string, string> = { accept: "application/json" };
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
    return new Response(response.body, { status: response.status, headers: { "content-type": contentType } });
  } catch (error) {
    return new Response(JSON.stringify({ ok: false, error: `Factory unavailable: ${error}` }), {
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
