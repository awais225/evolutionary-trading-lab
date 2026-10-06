"""
Evolutionary Trading Research Lab - Desktop Application Shell (V2.4)

Provides a standalone desktop application experience.
Launches the local FastAPI / Uvicorn research backend in an embedded thread
and displays the React trading lab dashboard inside a native desktop window
(using PySide6 QWebEngineView or pywebview with native Edge WebView2).
Clean shutdown terminates the backend, closes SQLite databases, and releases MT5 handles.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

# Setup paths
DESKTOP_DIR = Path(__file__).resolve().parent
ROOT_DIR = DESKTOP_DIR.parent
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Ensure required runtime directories exist
(ROOT_DIR / "LOGS").mkdir(parents=True, exist_ok=True)
(ROOT_DIR / "DATABASE").mkdir(parents=True, exist_ok=True)
(ROOT_DIR / "DATA").mkdir(parents=True, exist_ok=True)
(ROOT_DIR / "RESEARCH").mkdir(parents=True, exist_ok=True)

import uvicorn
from app.main import app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("desktop_app")

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8787
SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"


class ServerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        config = uvicorn.Config(
            app=app,
            host=SERVER_HOST,
            port=SERVER_PORT,
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(config=config)

    def run(self):
        self.server.run()

    def stop(self):
        self.server.should_exit = True


def wait_for_server(timeout_sec: int = 30) -> bool:
    """Poll /api/health until server is active and verified."""
    start_t = time.time()
    while time.time() - start_t < timeout_sec:
        try:
            req = urllib.request.urlopen(f"{SERVER_URL}/api/health", timeout=1.5)
            if req.getcode() == 200:
                body = json.loads(req.read().decode("utf-8"))
                logger.info("Backend health confirmed: version=%s, db=%s, mt5=%s",
                            body.get("version"), body.get("database"), body.get("mt5_api"))
                return True
        except Exception:
            time.sleep(0.4)
    return False


def launch_native_window(url: str, title: str = "Evolutionary Trading Research Lab v2.4") -> bool:
    """
    Attempts to launch an embedded desktop window without an external browser.
    Tries:
      1. pywebview (native Windows WebView2)
      2. PySide6 / PyQt6 QWebEngineView
    Returns True if a desktop window ran, False if fallbacks are needed.
    """
    # 1. Try pywebview
    try:
        import webview
        logger.info("Starting pywebview native desktop shell...")
        window = webview.create_window(
            title=title,
            url=url,
            width=1480,
            height=920,
            min_size=(1024, 700),
            confirm_close=False,
        )
        webview.start()
        logger.info("Desktop window closed.")
        return True
    except ImportError:
        logger.info("pywebview not installed, trying QtWebEngine...")
    except Exception as e:
        logger.warning("pywebview failed to start: %s", e)

    # 2. Try PySide6 QWebEngineView
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView

        logger.info("Starting PySide6 QWebEngineView shell...")
        qt_app = QApplication(sys.argv)
        web_view = QWebEngineView()
        web_view.setWindowTitle(title)
        web_view.resize(1480, 920)
        web_view.load(QUrl(url))
        web_view.show()
        qt_app.exec()
        logger.info("Qt window closed.")
        return True
    except ImportError:
        logger.info("PySide6 not installed.")
    except Exception as e:
        logger.warning("PySide6 WebEngine failed: %s", e)

    return False


def run_desktop_app():
    logger.info("Starting local research backend server...")
    server_thread = ServerThread()
    server_thread.start()

    logger.info("Waiting for research backend health check...")
    if not wait_for_server(timeout_sec=30):
        logger.error("Backend server failed to respond within 30 seconds.")
        sys.exit(1)

    logger.info("Research backend is ready at %s", SERVER_URL)

    # Attempt native desktop window first
    launched = launch_native_window(SERVER_URL)
    if not launched:
        logger.info("Native desktop shell not available in current environment. Opening default browser...")
        import webbrowser
        webbrowser.open(SERVER_URL)
        logger.info("Evolutionary Trading Research Lab running. Press Ctrl+C to exit.")
        try:
            while server_thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received.")

    # Clean shutdown
    logger.info("Stopping backend server...")
    server_thread.stop()
    time.sleep(0.5)
    logger.info("Application exited cleanly.")


if __name__ == "__main__":
    run_desktop_app()
