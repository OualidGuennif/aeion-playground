# Architecture map

[Open the interactive diagram](aeion-playground-architecture.html) — a self-contained HTML
file, no network needed. The editable source is [`architecture.json`](architecture.json).

The map shows what runs where and which boundary decides:

- the containerised cockpit, ParcelDesk surface, ledger, workboard, PostgreSQL and OTel
  collector;
- the trusted host executor agent, which is the only component with Docker access;
- the disposable test worker and the two model roles, with their own boundaries;
- the inference gateway, where provider credentials stay;
- the paths the cockpit is allowed to ask for: `probe`, `run_tests`, `run_role`.

It is deliberately an architecture map, not a measured request trace. The "what is not
wired" card repeats the repository's honest state: GitHub delivery is pending, live
provider inference has not been exercised here, and the agent cannot sign evidence.

## Generation

Produced with [Archify](https://github.com/tt-a1i/archify) 3.2.0 at commit
`25cf37080fc8b1dbe758a4efc3935b55f3327083` from the typed JSON source. The `finalize`
command ran in a disposable `node:22-alpine` container with Chromium, at
`--quality showcase`:

```sh
node archify/bin/archify.mjs finalize architecture knowledge/architecture.json \
  knowledge/aeion-playground-architecture.html --quality showcase --json
```

Its receipt reported `validate`, `deliver`, `check` and `browser-check` all `pass`. The
receipts contain container-local paths and are not committed; the HTML and the JSON source
are. The diagram was not perceptually reviewed by a human.

To regenerate, install the skill (`npx skills add tt-a1i/archify -g`) or clone the pinned
commit, then run the command above. The renderer is zero-dependency but needs Node 18+;
the browser check needs Chrome/Chromium.

## Editing

Edit `architecture.json`, not the HTML. Keep component ids stable: connections and
boundaries refer to them. After an edit, rerun `finalize` and commit both files together.
