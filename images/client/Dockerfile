# Clients F and G - everything client_test_suite.sh needs.
FROM debian:bookworm-slim
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      bash curl ca-certificates postgresql-client python3 python3-websocket xxd \
      iproute2 iputils-ping netcat-openbsd nmap tcpdump procps \
 && rm -rf /var/lib/apt/lists/*
# tests/ is bind-mounted at /opt/tests by docker-compose.yml
WORKDIR /tmp
CMD ["sleep", "infinity"]
