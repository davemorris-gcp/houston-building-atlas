"""HTTP Range-capable static file server for The Houston Building Atlas v2 (supports PMTiles HTTP 206 requests)."""

from __future__ import annotations

import argparse
import os
import re
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BYTE_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


class RangeHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Static HTTP handler with RFC 7233 Byte-Range (HTTP 206) support required by PMTiles."""

    protocol_version = "HTTP/1.1"

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".pmtiles": "application/vnd.pmtiles",
        ".geojson": "application/geo+json",
        ".json": "application/json",
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".css": "text/css",
        ".svg": "image/svg+xml",
    }

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_head(self):  # type: ignore[override]
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()

        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        fs = os.fstat(f.fileno())
        file_len = fs.st_size
        ctype = self.guess_type(path)

        range_header = self.headers.get("Range")
        if range_header:
            m = BYTE_RANGE_RE.match(range_header.strip())
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else file_len - 1
                end = min(end, file_len - 1)
                if start >= file_len or start > end:
                    self.send_response(416, "Requested Range Not Satisfiable")
                    self.send_header("Content-Range", f"bytes */{file_len}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    f.close()
                    return None

                length = end - start + 1
                self.send_response(206, "Partial Content")
                self.send_header("Content-type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_len}")
                self.send_header("Content-Length", str(length))
                self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
                self.end_headers()
                f.seek(start)
                self._range_bytes_remaining = length
                return f

        self._range_bytes_remaining = None
        self.send_response(200)
        self.send_header("Content-type", ctype)
        self.send_header("Content-Length", str(file_len))
        self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
        self.end_headers()
        return f

    def copyfile(self, source, outputfile) -> None:  # type: ignore[override]
        remaining = getattr(self, "_range_bytes_remaining", None)
        try:
            if remaining is None:
                super().copyfile(source, outputfile)
                return

            bufsize = 256 * 1024
            while remaining > 0:
                chunk = source.read(min(bufsize, remaining))
                if not chunk:
                    break
                outputfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve The Houston Building Atlas v2 static web app.")
    parser.add_argument("--port", type=int, default=8085, help="Port to listen on (default: 8085)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Root directory to serve.",
    )
    args = parser.parse_args()

    os.chdir(args.dir)
    server = ThreadingHTTPServer((args.host, args.port), RangeHTTPRequestHandler)
    print(f"Serving Houston Building Atlas v2 (threaded HTTP/1.1) from {args.dir} at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
