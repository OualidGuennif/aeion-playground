import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";
import { handler } from "../routes/api/[...path].ts";

Deno.test("the api proxy serves the refunds csv", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    assertEquals(String(input).endsWith("/api/refunds.csv"), true);
    assertEquals((init?.headers as Record<string, string>).accept, "text/csv");
    return Promise.resolve(new Response("id,customer\nPD-1050,Alice\n", {
      status: 200,
      headers: {
        "content-type": "text/csv;charset=UTF-8",
        "content-disposition": 'attachment; filename="refunds.csv"',
      },
    }));
  }) as typeof fetch;
  try {
    const response = await handler.GET!(
      new Request("http://localhost/api/refunds.csv"),
      { params: { path: "refunds.csv" } } as never,
    );
    assertEquals(response.status, 200);
    assertEquals(response.headers.get("content-type"), "text/csv;charset=UTF-8");
    assertEquals(response.headers.get("content-disposition"), 'attachment; filename="refunds.csv"');
    assertEquals(await response.text(), "id,customer\nPD-1050,Alice\n");
  } finally {
    globalThis.fetch = original;
  }
});
