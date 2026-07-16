# Builds the frontend production bundle and serves it from nginx, which also
# reverse-proxies /api to the backend and terminates TLS. No frontend dev
# server (vite --host) runs in this image — see docs/deployment.md.

FROM node:24-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json ./
RUN npm install
COPY frontend/ .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=frontend-build /app/dist /usr/share/nginx/html
COPY deploy/nginx/nginx.conf /etc/nginx/conf.d/default.conf
