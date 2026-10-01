"""Container B - threaded HTTPS server.

Replaces `openssl s_server -www`, which is single-threaded: under load it
serialises TLS handshakes and times clients out with or without the gateway
in the path (measured: 49 handshake timeouts direct vs 44 via gateway).
Handshakes run in the per-connection thread, not in accept()."""
import http.server
import ssl


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"container B (HTTPS) - you are {self.client_address[0]}\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class Server(http.server.ThreadingHTTPServer):
    request_queue_size = 128


ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain("/certs/cert.pem", "/certs/key.pem")
srv = Server(("0.0.0.0", 443), Handler)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
print("HTTPS listening on 0.0.0.0:443", flush=True)
srv.serve_forever()
