import type { Manifest } from "$fresh/server.ts";
import * as Index from "./routes/index.tsx";
import * as Tickets from "./routes/tickets.tsx";
import * as Pulls from "./routes/pulls.tsx";
import * as ApiProxy from "./routes/api/[...path].ts";
import * as FactoryProxy from "./routes/factory/[...path].ts";
import * as Sync from "./routes/sync.ts";

const manifest = {
  routes: {
    "./routes/index.tsx": Index,
    "./routes/tickets.tsx": Tickets,
    "./routes/pulls.tsx": Pulls,
    "./routes/api/[...path].ts": ApiProxy,
    "./routes/factory/[...path].ts": FactoryProxy,
    "./routes/sync.ts": Sync,
  },
  islands: {},
  baseUrl: import.meta.url,
} satisfies Manifest;

export default manifest;
