from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7878
READY_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class ServerTarget:
    host: str
    port: int

    @property
    def app_url(self) -> str:
        return f"http://{self.host}:{self.port}/agent-console/"


def is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def choose_port(host: str, preferred_port: int) -> int:
    if is_port_available(host, preferred_port):
        return preferred_port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def is_ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=1) as response:
            return 200 <= response.status < 500
    except (OSError, URLError):
        return False


def wait_until_ready(url: str, timeout_seconds: int = READY_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if is_ready(url):
            return True
        time.sleep(0.25)
    return False


def run_server(target: ServerTarget) -> tuple[uvicorn.Server | None, threading.Thread | None]:
    if is_ready(target.app_url):
        return None, None

    config = uvicorn.Config(
        "app:app",
        host=target.host,
        port=target.port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="clideck-uvicorn", daemon=True)
    thread.start()
    return server, thread


def open_window(url: str) -> None:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "pywebview is not installed. Run `python -m pip install -e .[desktop]` "
            "or start CliDeck with CliDeckDesktop.cmd."
        ) from exc

    webview.create_window(
        "CliDeck",
        url,
        width=1280,
        height=860,
        min_size=(960, 640),
    )
    webview.start()


def stop_server(server: uvicorn.Server | None, thread: threading.Thread | None) -> None:
    if server is None or thread is None:
        return
    server.should_exit = True
    thread.join(timeout=5)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start CliDeck in a desktop window.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    preferred_target = ServerTarget(host=args.host, port=args.port)
    target = preferred_target
    if not is_ready(preferred_target.app_url):
        target = ServerTarget(host=args.host, port=choose_port(args.host, args.port))

    server, thread = run_server(target)
    try:
        if not wait_until_ready(target.app_url):
            raise RuntimeError(f"CliDeck did not become ready at {target.app_url}")
        open_window(target.app_url)
    finally:
        stop_server(server, thread)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
