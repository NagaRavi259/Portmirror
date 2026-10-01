# Gateway E: nftables data plane + portmirror manager (API/UI) in one container.
# Build context: repo root   (docker-compose.yml sets it)

# ---- UI build ----
FROM node:22-alpine AS ui
WORKDIR /ui
COPY manager/ui/package.json manager/ui/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY manager/ui/ ./
RUN npm run build

# ---- runtime without UI (fast backend iteration: --target base) ----
FROM python:3.12-alpine AS base
RUN apk add --no-cache nftables iproute2 tcpdump conntrack-tools bash iputils-ping
WORKDIR /opt/pm
COPY manager/requirements.txt manager/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
COPY manager/app ./app
COPY manager/tests ./tests
COPY manager/seed.json /etc/portmirror/seed.json
COPY images/gateway/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
 && printf '#!/bin/sh\ncd /opt/pm && exec python3 -m app.cli "$@"\n' > /usr/local/bin/pmctl \
 && chmod +x /usr/local/bin/pmctl
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/entrypoint.sh"]

# ---- full image ----
FROM base AS full
COPY --from=ui /ui/dist /opt/pm/ui
