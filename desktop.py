#!/usr/bin/env python3
"""Native macOS shell for Outcome Metrics."""
from __future__ import annotations

import threading

import server as outcome_server
import webview
from webview.menu import Menu, MenuAction

from desktop_policy import install_policy


def run() -> None:
    server = outcome_server.make_server(port=0)
    worker = threading.Thread(target=server.serve_forever, name="outcome-metrics-http", daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}/?desktop=1"
    window = None

    def quit_app(_window=None):
        if window is not None:
            window.destroy()

    try:
        install_policy(f"http://127.0.0.1:{server.server_port}/")
        window = webview.create_window(
            "Outcome Metrics",
            url,
            width=1080,
            height=760,
            min_size=(800, 600),
            menu=[Menu("File", [MenuAction("Quit", quit_app)])],
        )
        webview.start()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


if __name__ == "__main__":
    try:
        run()
    except Exception:
        import AppKit

        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Outcome Metrics could not start")
        alert.setInformativeText_("The local app failed to open. Restart Outcome Metrics and try again.")
        alert.addButtonWithTitle_("OK")
        alert.runModal()
        raise SystemExit(1)
