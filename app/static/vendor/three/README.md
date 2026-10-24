# Three.js

Vendored so the ParcelDesk warehouse works offline and the cockpit makes no CDN
request at runtime.

- Version: `0.180.0`
- Source: `https://cdn.jsdelivr.net/npm/three@0.180.0/build/`
- Files: `three.module.min.js`, `three.core.min.js` (the module imports the core file
  relatively), `LICENSE`
- SHA-256:
  - `three.module.min.js`: `e2b5ee6bccd38fd6d8a2428546b83c5f2426d84b152ef82be8055556e3b40eb6`
  - `three.core.min.js`: `61ba0df005b05991361d040d8ff670e1aadfd0ce7aeebd1fdb0725957a8957de`
- License: MIT, reproduced in `LICENSE`.

Do not edit the minified files. To upgrade, replace both files from the same version,
update the hashes above, and run the cockpit page in a browser.
