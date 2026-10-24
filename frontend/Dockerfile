# syntax=docker/dockerfile:1
# FrogRadar web app: production Angular build served by nginx, which proxies /api, /health and
# /mcp to the backend (API_UPSTREAM, e.g. backend:8000). Build context: frontend/.

# --- Build stage ---
# Base images are pinned by tag and digest, each at least 14 days old (supply-chain cooldown, D85).
FROM node:22.23.3-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
RUN npx ng build --configuration production

# --- Runtime stage: unprivileged nginx (listens on 8080, runs as uid 101) ---
FROM nginxinc/nginx-unprivileged:1.31.5-alpine3.24@sha256:19c132c9ab02d3b783f478743dafc7a7f42e27aa7d2bdcbec1bb1128ca8f2a07 AS runtime
# The base image ships curl and four dynamic nginx modules (geoip, image-filter, njs, xslt) that
# FrogRadar never uses: no healthcheck or entrypoint script calls curl, and our config loads no
# module. Removing them removes their libraries' CVEs (libcurl, libgd/tiff, libxml2/libxslt, X11).
# wget (busybox) stays available for debugging.
USER root
RUN apk del --no-cache curl \
      nginx-module-geoip nginx-module-image-filter nginx-module-njs nginx-module-xslt
USER 101
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /src/dist/frontend/browser /usr/share/nginx/html
ENV API_UPSTREAM=backend:8000
EXPOSE 8080
