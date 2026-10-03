"""Captures d'écran de chaque onglet, pour vérifier l'interface sans clic manuel.

Usage : python3 -m scripts.screenshots EXPORT.json SORTIE_DIR [default|proposal|onboarding|measures]
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

from app.main import APP_ID, install_style  # noqa: E402
from app.state import AppState  # noqa: E402
from contracts import DosingSettings, Meal, NoteTag, Reading, ReadingNote  # noqa: E402
from services.store import Store  # noqa: E402

# protocole fictif pour les captures, pas une recommandation
DEMO_PROTOCOL = DosingSettings(insulin="Insuline de démonstration", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)

PAGES = ("today", "charts", "measures", "doses")
# scénario « measures » : onglet Mesures en largeur (px) et thème donnés
MEASURES_SHOTS = {"mesures-clair": (1000, False), "mesures-sombre": (1000, True), "mesures-etroit": (360, False)}


def snapshot(window: Gtk.Window, path: Path) -> bool:
    """False si la fenêtre n'a encore rien dessiné (changement de thème en cours) : réessayer plus tard."""
    width, height = window.get_width(), window.get_height()
    paintable = Gtk.WidgetPaintable.new(window)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, width, height)
    node = snap.to_node()
    if node is None:
        return False
    texture = window.get_renderer().render_texture(node, None)
    texture.save_to_png(str(path))
    print(f"capture : {path}")
    return True


def _high_mornings_until_today() -> list[Reading]:
    today = datetime.combine(date.today(), time(7, 30))
    out = []
    for days_ago, mg in ((2, 182), (1, 205), (0, 176)):
        t = today - timedelta(days=days_ago)
        out.append(Reading(t, mg, int(t.timestamp())))
    return out


def _add_demo_notes(state: AppState) -> None:
    """Notes fictives sur les mesures les plus récentes : une glycémie du matin écartée, deux notes simples."""
    readings = list(reversed(state.readings()))
    mornings = [r for r in readings if r.meal is Meal.FASTING and r.mg_dl >= 80]
    others = [r for r in readings if r.meal is Meal.AFTER_MEAL]
    if mornings:
        state.set_note(mornings[0], ReadingNote((NoteTag.LARGE_MEAL,), "repas de famille la veille", exclude_from_dosing=True))
    if others:
        state.set_note(others[0], ReadingNote((NoteTag.EXERCISE,), "marche de 40 min"))
    if len(mornings) > 2:
        state.set_note(mornings[2], ReadingNote((NoteTag.SIDE_EFFECT,), "nausées après l'injection"))


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
        _add_demo_notes(state)
    if scenario == "proposal":
        state.store.import_readings(_high_mornings_until_today(), "démo")
    if scenario == "default":
        pages = list(PAGES)
    elif scenario == "measures":
        pages = list(MEASURES_SHOTS)
    else:
        pages = ["today"]

    app = Adw.Application(application_id=APP_ID + ".Screenshots")

    def on_activate(application):
        from gi.repository import Gdk

        install_style(Gdk.Display.get_default())
        from app.window import MainWindow

        window = MainWindow(application=application, state=state)
        window.set_default_size(1000, 1100)
        window.present()

        def capture(name, attempt=0):
            if not snapshot(window, out_dir / f"{name}-{scenario}.png"):
                if attempt >= 20:
                    print(f"capture vide après 6 s : {name}", file=sys.stderr)
                    application.quit()
                    return False
                GLib.timeout_add(300, capture, name, attempt + 1)
                return False
            if pages:
                step()
            else:
                application.quit()
            return False

        def step():
            name = pages.pop(0)
            if name in MEASURES_SHOTS:
                width, dark = MEASURES_SHOTS[name]
                scheme = Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT
                Adw.StyleManager.get_default().set_color_scheme(scheme)
                window.set_default_size(width, 1100)
                window.stack.set_visible_child_name("measures")
            else:
                window.stack.set_visible_child_name(name)
            GLib.timeout_add(900, capture, name)
            return False

        GLib.timeout_add(1200, step)

    app.connect("activate", on_activate)
    return app.run([])


if __name__ == "__main__":
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplcfg")
    sys.exit(main())
