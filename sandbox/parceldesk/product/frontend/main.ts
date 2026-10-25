import { start } from "$fresh/server.ts";
import manifest from "./fresh.gen.ts";
import config from "./fresh.config.ts";

await start(manifest, {
  ...config,
  port: Number(Deno.env.get("PORT") ?? 8000),
});
