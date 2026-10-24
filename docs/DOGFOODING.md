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

**Build scan (run #5, 22 Oct 2026):** OIDC login → both images pushed to `docker-trial`
(`frogradar-backend`, `frogradar-web`, tag `<commit>-<run>.<attempt>`, never overwritten) → Build Info `frogradar/5`
with both images, Git and environment → Xray build scan. **0 policy violations** (security, license, operational
risk) – no Xray watch or policy is defined yet, so that is not a gate. The vulnerability list (`--vuln`) pointed at
the **base images**, not FrogRadar's code: web 218 unique CVEs (15 Critical), backend 140 (3 Critical).

## Base images: scanned → fixed (24 Oct 2026, D85)

**What changed**

| Stage | Before | After (tag @ digest, image build date) |
|---|---|---|
| Web runtime | `nginxinc/nginx-unprivileged:1.29.3-alpine` (Alpine 3.22) | `nginxinc/nginx-unprivileged:1.31.5-alpine3.24@sha256:19c132c9…` (14 Sep), then `apk del` curl and the dynamic modules geoip, image-filter, njs, xslt |
| Web build (not shipped) | `node:22.20.0-alpine` | `node:22.23.3-alpine@sha256:0a7108bf…` (23 Sep) |
| Backend runtime | `python:3.12-slim-bookworm` (floating tag, Debian 12) | `python:3.12.14-slim-trixie@sha256:f77ac9e4…` (19 Sep, Debian 13), then `pip uninstall pip` and the bundled ensurepip wheel removed |
| Backend build (not shipped) | `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` (floating, Python 3.12.12) | the same `python:3.12.14-slim-trixie` digest + the `uv` binary from `ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e4…` (3 Oct) |

Every image is pinned by tag and digest and was built at least 14 days before the change (cooldown cutoff
10 Oct; dates from each image's own `created` field, not the registry's "last updated"). Builder and runtime use the
same Python image, so the venv copied between them matches exactly. Nothing in the images runs `apk upgrade` or
`apt-get upgrade`, which would pull same-day packages and make builds non-reproducible.

**Why removing packages is safe:** no healthcheck, Makefile target, CI step, entrypoint script or app code calls
`curl` or `pip` (the API healthcheck uses `python`; the web service has none; busybox `wget` stays in the web image).
Our nginx config loads no dynamic module. The app's venv (built by uv) has no pip.

**Before → after** – the same `jf docker scan --format=simple-json` (JFrog CLI 2.127.0, local jf config), on
linux/amd64 images built from the old and new Dockerfiles on the same day. Unique CVE IDs, counted at each CVE's
highest severity:

| Image | Before: unique CVEs | Critical | High | After: unique CVEs | Critical | High |
|---|---|---|---|---|---|---|
| `frogradar-web` | 229 | 17 | 93 | **6** | **0** | 3 |
| `frogradar-backend` | 140 | 3 | 25 | **95** | **0** | 21 |

The web "before" is 229 / 17 rather than run #5's 218 / 15 because new CVEs have been published against the same old
packages since then; the backend reproduces 140 / 3 exactly.

Where the drop comes from:
- **Web:** the new base (OpenSSL 3.5.4 → 3.5.8, nginx 1.29.3 → 1.31.5, newer expat, libxml2, pcre2, musl from Alpine
  3.24) plus removing curl / libcurl (29 CVEs in the old image): 229 → 34. Removing the four modules, which pulled in
  libgd, tiff, libxml2 / libxslt, freetype / brotli and X11: 34 → 6, including both Criticals left after the base
  change (tiff and brotli).
- **Backend:** Debian 13 replaces Debian 12's glibc, util-linux, SQLite, zlib and gcc runtime packages (120 OS CVEs →
  82); pip 25.0.1 removed (7 CVEs → 0).

**Remaining risk (accepted, tracked)**

| Image | Left | Detail |
|---|---|---|
| Web | 6: 3 High, 1 Medium, 1 Low, 1 unrated | OpenSSL 3.5.8 (CVE-2026-54873 High, no fixed version yet), zlib 1.3.2 (High, fixed in 1.3.2-r1), pcre2 10.48 (High, fixed in 10.49), musl (unrated, fixed in 1.2.6-r3). The fixed packages come with `nginx-unprivileged` 1.31.6 (built 12 Oct, clears the cooldown on 26 Oct). |
| Backend | 95: 21 High, 36 Medium, 38 Low, 0 Critical | 82 in Debian 13 packages (20 High; util-linux, OpenSSL, glibc, perl-base, gcc runtime, ncurses, PAM); 13 have a Debian fix, which arrives with `python:3.12.15-slim-trixie` (built 21 Oct, eligible 4 Nov). 13 in `filelock` 3.32.7 (1 High), see the audit above: fix 4.2.1 eligible 31 Oct. |

Next: refresh the bases as those images clear the cooldown, upgrade filelock and Angular, then add an Xray watch +
policy so the build scan fails on new Critical issues.

Setup notes for this platform: the build permission needs the pattern `frogradar/**` (build info is stored as
`frogradar/<number>-<timestamp>.json`), and the build must be indexed by name with Xray's v2 API
(`PUT /xray/api/v2/binMgr/builds` with `{build_name, build_repo}`); a v1 name list or an include pattern was
accepted but did not index it on Xray 3.157.

Decision records: D81 (CI) and D85 (base images) in [DESIGN.md](DESIGN.md).
