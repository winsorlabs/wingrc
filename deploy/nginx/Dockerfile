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
FROM nginx:1.29-alpine
COPY --from=frontend-build /app/dist /usr/share/nginx/html
COPY deploy/nginx/nginx.conf /etc/nginx/conf.d/default.conf
ARG WINGRC_BUILD_SHA=unknown
LABEL org.opencontainers.image.revision=${WINGRC_BUILD_SHA}
