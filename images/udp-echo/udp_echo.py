"""UDP echo sidecar for container D (shares D's netns -> 10.0.0.15:9000).
Replies b"echo:" + payload. Logs the peer address so masquerade can be
verified from the server's own point of view."""
import os
import socket

PORT = int(os.environ.get("UDP_PORT", "9000"))

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("0.0.0.0", PORT))
print(f"UDP echo listening on 0.0.0.0:{PORT}", flush=True)
while True:
    data, peer = s.recvfrom(65535)
    print(f"from {peer[0]}:{peer[1]} len={len(data)}", flush=True)
    s.sendto(b"echo:" + data, peer)
