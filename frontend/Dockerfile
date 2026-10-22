# syntax=docker/dockerfile:1
# FrogRadar web app: production Angular build served by nginx, which proxies /api, /health and
# /mcp to the backend (API_UPSTREAM, e.g. backend:8000). Build context: frontend/.

# --- Build stage ---
FROM node:22.20.0-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
RUN npx ng build --configuration production

# --- Runtime stage: unprivileged nginx (listens on 8080, runs as uid 101) ---
FROM nginxinc/nginx-unprivileged:1.29.3-alpine AS runtime
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /src/dist/frontend/browser /usr/share/nginx/html
ENV API_UPSTREAM=backend:8000
EXPOSE 8080
