# Builds the frontend production bundle and serves it from nginx, which also
# reverse-proxies /api to the backend and terminates TLS. No frontend dev
# server (vite --host) runs in this image — see docs/deployment.md.

FROM node:24-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json ./
RUN npm install
COPY frontend/ .
RUN npm run build

# 1.29 (mainline, as 1.27 was) on Alpine 3.23, OpenSSL 3.5.6 -- 2026-10-10.
# Trivy, HIGH+CRITICAL: OpenSSL HIGHs 9 -> 2 per library and CRITICAL 2 -> 0,
# which is the part reachable here, since this container terminates TLS.
# The newer base adds HIGHs in curl/libcurl, libuuid and pcre2. nginx never
# runs curl, and nginx.conf uses no regex locations, rewrites or maps, so
# PCRE2 is not exercised by request handling. 1.28-alpine (stable) scanned
# worse: 2 CRITICAL, OpenSSL 3.5.5. Re-check with deploy.sh's per-deploy scan.
# Note, 2026-10-29: these HIGH figures were read off Trivy's table, which
# prints a repeated severity once per package, so they likely undercount.
# The CRITICALs match Trivy's totals (docs/roadmap.md, "The deploy scans are
# read by nobody"); the per-library HIGHs cannot be re-derived.
FROM nginx:1.29-alpine
COPY --from=frontend-build /app/dist /usr/share/nginx/html
# A template, not a plain config: the image's entrypoint substitutes
# environment variables into it at start (only ones that are set, so
# nginx's own $host etc. are untouched). That is how this image's commit
# reaches the backend's /health on every /api/ request (backend/app/health.py).
COPY deploy/nginx/nginx.conf /etc/nginx/templates/default.conf.template
ARG WINGRC_BUILD_SHA=unknown
ENV WINGRC_BUILD_SHA=${WINGRC_BUILD_SHA}
LABEL org.opencontainers.image.revision=${WINGRC_BUILD_SHA}
