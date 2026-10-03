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
ICONS_DIR = Path(__file__).resolve().parent / "icons"

CSS = """
.dose-value { font-size: 44px; font-weight: 800; }
.dose-card { padding: 18px 12px; }
.chart-card { background-color: white; border-radius: 12px; padding: 6px; }

/* Mesures : la couleur ne sert qu'au niveau de la glycémie */
.summary-card { padding: 18px 20px; }
.summary-card flowboxchild { padding: 0; }
.stat-label { font-weight: 600; opacity: 0.75; }
.stat-value { font-size: 26px; font-weight: 800; }
.range-bar { border-radius: 999px; background-color: alpha(currentColor, 0.08); }
.range-segment { min-height: 10px; }
.range-segment.level-low, .legend-dot.level-low { background-color: var(--error-bg-color); }
.range-segment.level-in, .legend-dot.level-in { background-color: var(--success-bg-color); }
.range-segment.level-high, .legend-dot.level-high { background-color: var(--warning-bg-color); }
.legend-dot { min-width: 10px; min-height: 10px; border-radius: 999px; }
.measure-day-title { font-size: 17px; font-weight: 700; }
.measure-row .title { font-weight: 600; font-feature-settings: "tnum"; }
.marker-badge { min-width: 34px; min-height: 34px; border-radius: 999px; background-color: alpha(currentColor, 0.08); }
.marker-badge.marker-fasting { background-color: alpha(currentColor, 0.14); }
.marker-badge.marker-none { opacity: 0.55; }
.morning-chip, .retained-chip { padding: 3px 10px; border-radius: 999px; background-color: alpha(currentColor, 0.08); }
.value-pill { padding: 4px 12px; border-radius: 999px; font-weight: 700; font-size: 15px; }
.value-pill.level-low { color: var(--error-color); background-color: color-mix(in srgb, var(--error-bg-color) 16%, transparent); }
.value-pill.level-in { color: var(--success-color); background-color: color-mix(in srgb, var(--success-bg-color) 16%, transparent); }
.value-pill.level-high { color: var(--warning-color); background-color: color-mix(in srgb, var(--warning-bg-color) 20%, transparent); }
.level-text-low { color: var(--error-color); }
.level-text-in { color: var(--success-color); }
.level-text-high { color: var(--warning-color); }
"""


def install_style(display: Gdk.Display) -> None:
    """Feuille de style de l'appli et icônes de app/icons (marqueurs repas)."""
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Gtk.IconTheme.get_for_display(display).add_search_path(str(ICONS_DIR))


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
        install_style(Gdk.Display.get_default())
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
