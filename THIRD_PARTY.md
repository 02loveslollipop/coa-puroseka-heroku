# Upstream attribution

AWS Context Ontology Accelerator, release v0.3.7, https://github.com/aws/context-ontology-accelerator/tree/v0.3.7, Apache-2.0.

`web/src/App.tsx` adapts the React/Cloudscape theme and navigation shell from `packages/web-app/src/App.tsx`. `web/src/pages.tsx` adapts the Playground execution-mode/header/new-chat layout pattern and Cloudscape source/ontology page organization. These adaptations replace the upstream authentication, namespaces, generated API clients and runtime with a fixed-domain PoC. Modified files include copyright, license and modification notices. The original upstream LICENSE and NOTICE are retained.

Cloudscape Design System, React, React Flow, React Markdown, FastAPI, Psycopg, SQLGlot, RDFLib, and HTTPX retain their respective licenses, distributed with installed packages. Package versions are recorded in the npm lockfile and Python requirements.

The source data used by this application is obtained from [sekai.best datasets (Sekai Viewer)](https://sekai.best/). Dataset ownership and source provenance remain with sekai.best and the respective data owners; this project does not assert ownership or official game affiliation. Generated translations are approximate convenience labels, not official localization.

Token reservations use `tiktoken` (MIT) with the public Ministral 3 8B Tekken vocabulary (Apache-2.0). The vocabulary is downloaded from the official Mistral repository at a pinned revision and verified by SHA-256; it is not committed here. Text-only encoding follows the format documented by [mistral-common](https://github.com/mistralai/mistral-common/blob/main/src/mistral_common/tokens/tokenizers/tekken.py).

The UI now consumes Cloudscape components directly, following its official repository README and patterns. Source inspection checkout: `cloudscape-design/components` at `43480f95a039d3f48a35d13f6ec44df4e4830e2f`. The application uses the official npm packages for components, global styles, design tokens, collection hooks, code view and chat components. Exact versions are in `web/package-lock.json`. Cloudscape packages are Apache-2.0.
