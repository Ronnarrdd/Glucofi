"""Captures d'écran de chaque onglet, pour vérifier l'interface sans clic manuel.

Usage : python3 -m scripts.screenshots EXPORT.json SORTIE_DIR [default|proposal|onboarding]
Crée une base de démonstration temporaire (XDG_DATA_HOME), importe l'export,
démarre le protocole au 1er du mois précédent la dernière mesure.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from app.main import CSS, APP_ID  # noqa: E402
from app.state import AppState  # noqa: E402
from contracts import DosingSettings, Reading  # noqa: E402
from services.store import Store  # noqa: E402

# protocole fictif pour les captures, pas une recommandation
DEMO_PROTOCOL = DosingSettings(insulin="Insuline de démonstration", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)

PAGES = ("today", "charts", "measures", "doses")


def snapshot(window: Gtk.Window, path: Path) -> None:
    width, height = window.get_width(), window.get_height()
    paintable = Gtk.WidgetPaintable.new(window)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, width, height)
    node = snap.to_node()
    texture = window.get_renderer().render_texture(node, None)
    texture.save_to_png(str(path))
    print(f"capture : {path}")


def _high_mornings_until_today() -> list[Reading]:
    today = datetime.combine(date.today(), time(7, 30))
    out = []
    for days_ago, mg in ((2, 182), (1, 205), (0, 176)):
        t = today - timedelta(days=days_ago)
        out.append(Reading(t, mg, int(t.timestamp())))
    return out


def main() -> int:
    export, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    scenario = sys.argv[3] if len(sys.argv) > 3 else "default"
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="glucofi-demo-"))
    state = AppState(Store(data_dir / "glucofi.db"), data_dir)
    state.import_file(export)
    last = state.readings()[-1].device_time
    start = (last.replace(day=1) - timedelta(days=1)).replace(day=1, hour=0, minute=0)
    if scenario != "onboarding":
        state.start_protocol("Démo", start, 10, 6, DEMO_PROTOCOL)
    if scenario == "proposal":
        state.store.import_readings(_high_mornings_until_today(), "démo")
    pages = list(PAGES) if scenario == "default" else ["today"]

    app = Adw.Application(application_id=APP_ID + ".Screenshots")

    def on_activate(application):
        from gi.repository import Gdk

        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        from app.window import MainWindow

        window = MainWindow(application=application, state=state)
        window.set_default_size(1000, 1100)
        window.present()

        def capture(name):
            snapshot(window, out_dir / f"{name}-{scenario}.png")
            if pages:
                step()
            else:
                application.quit()
            return False

        def step():
            name = pages.pop(0)
            window.stack.set_visible_child_name(name)
            GLib.timeout_add(900, capture, name)
            return False

        GLib.timeout_add(1200, step)

    app.connect("activate", on_activate)
    return app.run([])


if __name__ == "__main__":
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplcfg")
    sys.exit(main())
