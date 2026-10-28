"""
A redirect must not carry a picture download onto this server's own network.

Both downloaders (imagefetch.download_bytes for the search grid and the
admin's own picture, worker._download_to for paste-a-link saves) used to
let `requests` follow redirects unchecked, so only the first address was
held to the internal-address rule. A local web server stands in for the
internet here: "localhost" is treated as a public site and "127.0.0.1" as
the server's own network, so one process can play both.

    python tools/test_download_redirects.py
"""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATA_DIR", str(ROOT / "_test_data_redirects"))

from fastapi import HTTPException  # noqa: E402

import app.routes.worker as W  # noqa: E402
from app import imagefetch as IF  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 2000
SECRET_HITS = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        port = self.server.server_address[1]
        if self.path == "/secret.jpg":
            SECRET_HITS.append(self.headers.get("Host"))
            self._send(200, JPEG)
        elif self.path == "/to-internal.jpg":
            self._redirect(f"http://127.0.0.1:{port}/secret.jpg")
        elif self.path == "/to-public.jpg":
            self._redirect(f"http://localhost:{port}/secret.jpg")
        elif self.path == "/to-file.jpg":
            self._redirect("file:///etc/passwd")
        elif self.path == "/loop.jpg":
            self._redirect("/loop.jpg")
        else:
            self._send(404, b"")

    def _redirect(self, where):
        self.send_response(302)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    real = W._host_check
    W._host_check = lambda host, resolver=None: (
        ("public", "") if host == "localhost" else real(host))
    base = f"http://localhost:{port}"
    bad = 0

    def expect(label, ok):
        nonlocal bad
        print(("PASS " if ok else "FAIL ") + label)
        bad += not ok

    try:
        # download_bytes
        SECRET_HITS.clear()
        try:
            IF.download_bytes(f"{base}/to-internal.jpg")
            expect("download_bytes refuses a redirect to 127.0.0.1", False)
        except IF.FetchError as e:
            expect("download_bytes refuses a redirect to 127.0.0.1",
                   "own network" in str(e) and not SECRET_HITS)
        expect("download_bytes follows a redirect to a public site",
               IF.download_bytes(f"{base}/to-public.jpg") == JPEG)
        for path, label in (("/to-file.jpg", "a file: address"),
                            ("/loop.jpg", "an endless redirect loop")):
            try:
                IF.download_bytes(base + path)
                expect(f"download_bytes refuses {label}", False)
            except IF.FetchError:
                expect(f"download_bytes refuses {label}", True)

        # worker._download_to
        tmp = ROOT / "_test_redirect_target.jpg"
        SECRET_HITS.clear()
        try:
            W._download_to(f"{base}/to-internal.jpg", tmp)
            expect("_download_to refuses a redirect to 127.0.0.1", False)
        except HTTPException as e:
            expect("_download_to refuses a redirect to 127.0.0.1",
                   e.status_code == 502 and not SECRET_HITS and not tmp.exists())
        n = W._download_to(f"{base}/to-public.jpg", tmp)
        expect("_download_to follows a redirect to a public site",
               n == len(JPEG) and tmp.read_bytes() == JPEG)
        tmp.unlink(missing_ok=True)
    finally:
        W._host_check = real
        srv.shutdown()

    print("ALL REDIRECT CHECKS PASSED" if not bad else f"{bad} FAILED")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
