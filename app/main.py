"""Point d'entrée GTK de Glucofi."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from app.state import AppState  # noqa: E402
from services.store import Store, default_data_dir  # noqa: E402

APP_ID = "fr.librenard.Glucofi"
VERSION = "1.0.0"

CSS = """
.dose-value { font-size: 44px; font-weight: 800; }
.dose-card { padding: 18px 12px; }
.chart-card { background-color: white; border-radius: 12px; padding: 6px; }
"""


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "glucofi"


def setup_logging() -> Path:
    log_dir = state_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "glucofi.log"
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("glucofi")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler(sys.stderr))

    def log_uncaught(exc_type, exc, tb):
        root.error("exception non rattrapée", exc_info=(exc_type, exc, tb))

    sys.excepthook = log_uncaught
    return path


class GlucofiApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.state: AppState | None = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        os.environ.setdefault("MPLCONFIGDIR", str(state_dir() / "matplotlib"))

    def do_activate(self):
        window = self.get_active_window()
        if window is None:
            from app.window import MainWindow

            data_dir = default_data_dir()
            self.state = AppState(Store(data_dir / "glucofi.db"), data_dir)
            window = MainWindow(application=self, state=self.state)
        window.present()


def main() -> int:
    log_path = setup_logging()
    logging.getLogger("glucofi").info("démarrage de Glucofi %s (journal : %s)", VERSION, log_path)
    return GlucofiApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
