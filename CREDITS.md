# Third-party notices

The repository is a local playground. Third-party components are listed here with the
license that applies to them; the repository itself does not relicense them.

| Component | Version | License | Local use |
|---|---|---|---|
| [Three.js](https://github.com/mrdoob/three.js) | 0.180.0 | MIT | Vendored under `app/static/vendor/three/` for the ParcelDesk warehouse; no CDN at runtime. |
| [Archify](https://github.com/tt-a1i/archify) | 3.2.0 (`25cf370`) | MIT | Used to render `knowledge/aeion-playground-architecture.html` from the typed source; the generator is not vendored. |
| [Paperclip](https://github.com/paperclipai/paperclip) | pinned image in `compose.yaml` | See upstream | Optional workboard service. |
| [OpenTelemetry Collector](https://github.com/open-telemetry/opentelemetry-collector-contrib) | pinned image in `compose.yaml` | Apache-2.0 | Metadata-only telemetry collector. |

Provider models, CLIs and credentials are not distributed here. The MIT text for Three.js is
in `app/static/vendor/three/LICENSE`; upstream licenses govern the pinned container images.
