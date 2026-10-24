# Dogfooding: FrogRadar is built and scanned with JFrog

<!-- Detail moved out of the README; the README keeps a summary. Keep both in sync. -->

FrogRadar analyzes the software supply-chain market, so it is built and scanned with the product it analyzes.

**CI ([`.github/workflows/jfrog.yml`](../.github/workflows/jfrog.yml)):** on every push to `main`, GitHub Actions
authenticates to the JFrog Platform with **OIDC** (no stored credentials), builds the backend and web images, pushes
them to Artifactory, records both in **Build Info** `frogradar` with the Git and environment details, publishes it
and runs an **Xray build scan**. Actions are pinned to commit SHAs of releases at least 14 days old.

**`jf audit` with contextual analysis on FrogRadar itself (22 Oct 2026):**

| Scope | Result |
|---|---|
| Backend (Python, 119 runtime packages exported from `uv.lock`) | **13 CVEs, all in `filelock` 3.32.7** (transitive: litellm → huggingface-hub), 1 High, 9 Medium, 3 Low. Every one needs a *second local user* on the same host (AV:L); the container runs one non-root user, and nothing we install uses the vulnerable `ReadWriteLock`. The fix (4.2.1) is 5 days old, inside our 14-day supply-chain cooldown: upgrade on 31 Oct. Secrets: 0. IaC: 0. SAST: 12 Low (CLI error messages, one regex). |
| Frontend (npm) | 84 findings, most in **build tooling** that never ships (Angular CLI, vite, webpack-dev-server, tar…). The runtime Angular 18.2.14 packages carry 4 Critical/High XSS CVEs in the **i18n** and SVG `<script>` paths: contextual analysis marks the rest Not Applicable, and we checked that FrogRadar uses no i18n, no SVG scripts, no SSR / hydration and no XSRF config. The fix is an upgrade to Angular 20.3+ (Angular 18 is out of support): on the Next list. Secrets: 0. SAST: 0. |

**Latest green run (#5, 22 Oct 2026):** OIDC login → both images pushed to `docker-trial`
(`frogradar-backend`, `frogradar-web`, tag `<commit>-<run>.<attempt>`, never overwritten) → Build Info `frogradar/5`
with both images, Git and environment → Xray build scan. **0 policy violations** (security, license, operational
risk) – no Xray watch or policy is defined yet, so that is not a gate. The vulnerability list (`--vuln`) is the
useful part, and it points at the **base images**, not FrogRadar's code:

| Image (base) | Unique CVEs | Where |
|---|---|---|
| `frogradar-web` (nginx-unprivileged 1.29.3, Alpine 3.22) | 218: 15 Critical, 89 High | OpenSSL 3.5.4, curl / libcurl 8.14.1, nginx 1.29.3, expat, libxml2. The image only serves static files and proxies `/api`; curl isn't used at runtime. |
| `frogradar-backend` (python:3.12-slim, Debian 12) | 140: 3 Critical, 25 High | glibc, SQLite, zlib, util-linux from Debian; at the Python level only `filelock` (13, see above) and the base image's own `pip` 25.0.1 (7), which the app never runs. |

Next: move to newer base images (a current nginx-unprivileged Alpine and a Debian 13 slim Python), drop `pip` and
`curl` from the runtime images, then add an Xray watch + policy so the build scan fails on new Critical issues.

Setup notes for this platform: the build permission needs the pattern `frogradar/**` (build info is stored as
`frogradar/<number>-<timestamp>.json`), and the build must be indexed by name with Xray's v2 API
(`PUT /xray/api/v2/binMgr/builds` with `{build_name, build_repo}`); a v1 name list or an include pattern was
accepted but did not index it on Xray 3.157.

Decision record: D81 in [DESIGN.md](DESIGN.md).
