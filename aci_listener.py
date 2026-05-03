"""
Ace Combat Infinity local mock server.

Listens on:
  - 0.0.0.0:80   (HTTP)
  - 0.0.0.0:443  (HTTPS, self-signed cert auto-generated on first run)

Logs every incoming request to console + requests.log.
Responds to known /Wind/* endpoints with stub bodies and 200 OK to everything
else, so we can see what the game asks for and iterate.

Setup:
  1) Add to C:\\Windows\\System32\\drivers\\etc\\hosts (as Administrator):
       127.0.0.1   dev-wind.siliconstudio.co.jp
  2) Run this script as Administrator (binding ports 80 / 443 needs admin):
       python aci_listener.py
  3) Boot ACI in RPCS3. Watch the console / requests.log.

Self-signed cert:
  On first run we auto-generate cert.pem / key.pem in this folder, signed for
  CN=dev-wind.siliconstudio.co.jp. RPCS3's libssl.sprx implementation does NOT
  verify the cert chain by default for most code paths, but if the game rejects
  the handshake we can later add the cert to RPCS3's cert store at
  dev_flash/data/cert/CA_LIST.cer or use the RPCS3 setting that disables TLS
  verification.

Stopping:
  Ctrl+C in the console.
"""

import datetime, http.server, json, os, socket, ssl, sys, threading, time
from socketserver import ThreadingMixIn

HERE = os.path.dirname(os.path.abspath(__file__))
CERT_PATH = os.path.join(HERE, "cert.pem")
KEY_PATH  = os.path.join(HERE, "key.pem")
LOG_PATH  = os.path.join(HERE, "requests.log")

# ---------------------------------------------------------------------------
# Self-signed cert generation (one-time)
# ---------------------------------------------------------------------------

def ensure_cert():
    if os.path.exists(CERT_PATH) and os.path.exists(KEY_PATH):
        return
    print("[setup] generating self-signed cert for dev-wind.siliconstudio.co.jp ...")
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        sys.exit(
            "[setup] missing 'cryptography' package.\n"
            "        run:  pip install cryptography\n"
            "        then re-run this script."
        )

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "dev-wind.siliconstudio.co.jp"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ACI Mock"),
    ])
    san = x509.SubjectAlternativeName([
        x509.DNSName("dev-wind.siliconstudio.co.jp"),
        x509.DNSName("localhost"),
    ])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365 * 10))
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    with open(CERT_PATH, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(KEY_PATH, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    print(f"[setup] wrote {CERT_PATH} / {KEY_PATH}")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_log_lock = threading.Lock()

def log(msg):
    line = f"[{datetime.datetime.now().isoformat(timespec='milliseconds')}] {msg}"
    with _log_lock:
        print(line, flush=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")

def _looks_like_tls_client_hello(sock):
    try:
        first = sock.recv(1, socket.MSG_PEEK)
    except (BlockingIOError, InterruptedError):
        return True
    except OSError:
        return False
    if not first:
        return False
    return first[0] == 0x16


# ---------------------------------------------------------------------------
# Stub responses for known endpoints
# ---------------------------------------------------------------------------
#
# Longest-prefix match wins. Every endpoint seen in the wild gets its own
# entry so the catch-all "!!!" marker only fires on genuinely new ones.
#
# Format: list of (path_prefix, handler_callable)
# Handler signature: fn(handler: ACIHandler) -> (status, content_type, body)

def _stub_authorize(req):
    return 200, "application/json", json.dumps({
        "result": 0,
        "status": "ok",
        "session": "00000000000000000000000000000000",
        "playerId": 1,
        "serverTime": int(time.time()),
    }).encode()

def _stub_player(req):
    return 200, "application/json", json.dumps({
        "result": 0,
        "player": {"id": 1, "name": "Pilot", "rank": 1, "credits": 999999},
    }).encode()

def _stub_empty_ok(req):
    return 200, "application/json", b'{"result":0}'

def _stub_unrecognized(req):
    """
    Catch-all for any /Wind/* path we haven't explicitly handled yet.
    Logs a loud !!! line so it's easy to spot in the log, then returns
    the same empty-ok body the game already accepts — so it won't crash.
    Add a proper entry to STUB_RESPONSES once you know the expected shape.
    """
    log(f"!!! UNRECOGNIZED ENDPOINT: {req.path}  <-- add a stub for this")
    return 200, "application/json", b'{"result":0}'

STUB_RESPONSES = [
    # --- auth / session ---
    ("/Wind/authorize",                    _stub_authorize),

    # --- player profile ---
    ("/Wind/player",                       _stub_player),

    # --- analytics / telemetry (seen in requests.log) ---
    ("/Wind/save/ev_pinger",               _stub_empty_ok),
    ("/Wind/save/ev_load_save_success",    _stub_empty_ok),
    ("/Wind/save/accum_data",              _stub_empty_ok),
    ("/Wind/save/ev_sortie",               _stub_empty_ok),
    ("/Wind/save/ev_objective_end",        _stub_empty_ok),

    # --- save / load / misc ---
    ("/Wind/uploadSaveData",               _stub_empty_ok),
    ("/Wind/recovery",                     _stub_empty_ok),
    ("/Wind/save",                         _stub_empty_ok),   # any other /save/*
    ("/Wind/load",                         _stub_empty_ok),
    ("/Wind/test",                         _stub_empty_ok),

    # --- catch-all: flags anything we haven't seen before ---
    ("/Wind/",                             _stub_unrecognized),
]


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class ACIHandler(http.server.BaseHTTPRequestHandler):
    server_version = "ACIMock/0.1"

    def log_message(self, fmt, *args):
        # Suppress default stderr access log; we do our own.
        pass

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _serve(self, method):
        body = self._read_body()
        host = self.headers.get("Host", "?")
        scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
        log(f"--- {method} {scheme}://{host}{self.path}  client={self.client_address[0]}")
        for h, v in self.headers.items():
            log(f"    h: {h}: {v}")
        if body:
            try:
                log(f"    body[utf8]: {body.decode('utf-8')}")
            except UnicodeDecodeError:
                log(f"    body[hex]: {body.hex()}")

        # Pick a stub (longest-prefix match)
        status, ctype, payload = 200, "application/octet-stream", b""
        for prefix, fn in STUB_RESPONSES:
            if self.path.startswith(prefix):
                status, ctype, payload = fn(self)
                break

        if isinstance(payload, str):
            payload = payload.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

        # Log what we sent back (truncated to 200 chars so the log stays readable)
        preview = payload.decode("utf-8", errors="replace")[:200]
        if len(payload) > 200:
            preview += f"  ...[{len(payload)} bytes total]"
        log(f"    -> {status} {ctype} {len(payload)}B  resp: {preview}")

    def do_GET(self):    self._serve("GET")
    def do_POST(self):   self._serve("POST")
    def do_PUT(self):    self._serve("PUT")
    def do_DELETE(self): self._serve("DELETE")
    def do_HEAD(self):   self._serve("HEAD")


class ThreadingHTTPServer(ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

def serve_http(port):
    httpd = LoggingHTTPServer(("0.0.0.0", port), ACIHandler)
    log(f"[http]  listening on 0.0.0.0:{port}")
    httpd.serve_forever()

class LoggingPort443Server(ThreadingHTTPServer):
    """Port 443 listener that accepts either TLS or plaintext HTTP."""
    def __init__(self, addr, handler, ctx):
        super().__init__(addr, handler)
        self._ctx = ctx
    def get_request(self):
        sock, addr = self.socket.accept()
        log(f"[tcp:443] accept from {addr[0]}:{addr[1]}")
        if not _looks_like_tls_client_hello(sock):
            log(f"[http:443] plaintext HTTP detected from {addr[0]}:{addr[1]}; serving without TLS")
            return sock, addr
        try:
            tls = self._ctx.wrap_socket(sock, server_side=True)
        except Exception as e:
            log(f"[tls:443] handshake FAILED from {addr[0]}:{addr[1]}: {type(e).__name__}: {e}")
            try: sock.close()
            except Exception: pass
            raise
        log(f"[tls:443] handshake OK from {addr[0]}:{addr[1]} cipher={tls.cipher()} ver={tls.version()}")
        return tls, addr

def serve_https(port):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_PATH, KEY_PATH)
    # PS3 libssl is old; allow weak ciphers / TLS 1.0+
    ctx.minimum_version = ssl.TLSVersion.TLSv1
    try:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0:!aNULL:!eNULL")
    except ssl.SSLError:
        pass
    httpd = LoggingPort443Server(("0.0.0.0", port), ACIHandler, ctx)
    log(f"[https] listening on 0.0.0.0:{port}  (TLS + plaintext HTTP fallback, cert={CERT_PATH})")
    httpd.serve_forever()


class LoggingHTTPServer(ThreadingHTTPServer):
    def get_request(self):
        sock, addr = self.socket.accept()
        log(f"[tcp:80]  accept from {addr[0]}:{addr[1]}")
        return sock, addr


def main():
    ensure_cert()
    open(LOG_PATH, "a").close()  # touch
    log("=" * 60)
    log("ACI mock listener starting")
    log("=" * 60)

    threads = [
        threading.Thread(target=serve_http,  args=(80,),  daemon=True),
        threading.Thread(target=serve_https, args=(443,), daemon=True),
    ]
    for t in threads:
        t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("shutting down")


if __name__ == "__main__":
    main()
