"""Test target (10.0.0.20): TCP + UDP echo on a port range.

Every reply is prefixed with the port it arrived on - "port:7003:<payload>" -
so tests can prove which target port a forward really mapped to.
TCP connections are persistent: every chunk received is echoed back."""
import os
import socket
import threading

LO, HI = (int(x) for x in os.environ.get("ECHO_PORTS", "7000-7010").split("-"))


def tcp_conn(conn: socket.socket, port: int):
    with conn:
        while True:
            try:
                data = conn.recv(65536)
            except OSError:
                return
            if not data:
                return
            conn.sendall(f"port:{port}:".encode() + data)


def tcp_server(port: int):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(512)
    while True:
        c, _ = s.accept()
        threading.Thread(target=tcp_conn, args=(c, port), daemon=True).start()


def udp_server(port: int):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    while True:
        data, peer = s.recvfrom(65535)
        s.sendto(f"port:{port}:".encode() + data, peer)


for p in range(LO, HI + 1):
    threading.Thread(target=tcp_server, args=(p,), daemon=True).start()
    threading.Thread(target=udp_server, args=(p,), daemon=True).start()
print(f"multi-echo on tcp+udp {LO}-{HI}", flush=True)
threading.Event().wait()
