from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import os

from src.config import settings
from src.logger import logger


class MetricsRequestHandler(BaseHTTPRequestHandler):
    metrics_path: Path = settings.PROMETHEUS_METRICS_PATH

    def do_GET(self) -> None:
        if self.path not in ("/metrics", "/metrics/"):
            self.send_error(404)
            return

        try:
            payload = self.metrics_path.read_bytes()
        except FileNotFoundError:
            payload = b"# Metrics are not available yet.\n"

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        return


def start_metrics_server() -> ThreadingHTTPServer:
    # Normalize an existing metrics file created by an older Windows build.
    # Prometheus 3 rejects CRLF in TYPE declarations as `gauge\r`.
    metrics_path = settings.PROMETHEUS_METRICS_PATH
    if metrics_path.exists():
        payload = metrics_path.read_bytes()
        normalized = payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if normalized != payload:
            temp_path = metrics_path.with_suffix(".prom.normalize.tmp")
            temp_path.write_bytes(normalized)
            os.replace(temp_path, metrics_path)

    MetricsRequestHandler.metrics_path = settings.PROMETHEUS_METRICS_PATH
    server = ThreadingHTTPServer(
        (settings.METRICS_HTTP_HOST, settings.METRICS_HTTP_PORT),
        MetricsRequestHandler,
    )
    Thread(target=server.serve_forever, name="metrics-http", daemon=True).start()
    logger.info(
        f"Prometheus metrics endpoint: http://{settings.METRICS_HTTP_HOST}:"
        f"{settings.METRICS_HTTP_PORT}/metrics"
    )
    return server
