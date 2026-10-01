"""Hold N TCP connections open and answer commands on stdin (driven by manager_tests.py).

Usage: python3 conn_probe.py <host> <port> <n>
  prints "OPEN <k>" once connected; then per stdin line:
    ping        -> "ALIVE <a>"   (connections that echoed a reply within 2s)
    close <i>   -> "CLOSED <i>"
    ports       -> "PORTS <local ports, comma separated>"
    quit        -> "BYE"
"""
import socket
import sys

host, port, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
conns = []
for _ in range(n):
    try:
        conns.append(socket.create_connection((host, port), timeout=2))
    except OSError:
        pass
print(f"OPEN {len(conns)}", flush=True)

for line in sys.stdin:
    cmd = line.split()
    if not cmd:
        continue
    if cmd[0] == "ping":
        alive = 0
        for c in conns:
            if c is None:
                continue
            try:
                c.settimeout(2)
                c.sendall(b"hi\n")
                if c.recv(4096):
                    alive += 1
            except OSError:
                pass
        print(f"ALIVE {alive}", flush=True)
    elif cmd[0] == "close":
        i = int(cmd[1])
        if conns[i]:
            conns[i].close()
            conns[i] = None
        print(f"CLOSED {i}", flush=True)
    elif cmd[0] == "ports":
        print("PORTS " + ",".join(str(c.getsockname()[1]) for c in conns if c), flush=True)
    elif cmd[0] == "quit":
        break
for c in conns:
    if c:
        c.close()
print("BYE", flush=True)
