import { assertEquals } from "https://deno.land/std@0.216.0/assert/mod.ts";

const manifest = await Deno.readTextFile(new URL("../fresh.gen.ts", import.meta.url));
const scene = await Deno.readTextFile(new URL("../static/warehouse.js", import.meta.url));

Deno.test("fresh.gen.ts registers the shared app layout", () => {
  assertEquals(manifest.includes('import * as App from "./routes/_app.tsx";'), true);
  assertEquals(manifest.includes('"./routes/_app.tsx": App,'), true);
  assertEquals(manifest.includes('"./routes/tickets.tsx": Tickets,'), true);
});

Deno.test("the warehouse scene removes the loading overlay after the first frame", () => {
  const animate = scene.indexOf("animate();");
  const remove = scene.indexOf("if (loading) loading.remove();");
  assertEquals(animate > -1, true);
  assertEquals(remove > animate, true);
});
