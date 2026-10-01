# VNC sidecar for container C. Runs in C's network namespace
# (network_mode: service:container_c) so it answers on 10.0.0.14:5900.
# danchitnis/xrdp only exposes RDP 3389; its Xvnc backend is session-local.
FROM alpine:3.20
RUN apk add --no-cache xvfb x11vnc fluxbox xterm bash
ENV DISPLAY=:1
CMD ["bash", "-c", "Xvfb :1 -screen 0 1024x768x16 & sleep 1; fluxbox & xterm & exec x11vnc -display :1 -forever -shared -nopw -rfbport 5900 -quiet"]
