"""Captures d'écran de chaque onglet, pour vérifier l'interface sans clic manuel.

Usage : python3 -m scripts.screenshots EXPORT.json SORTIE_DIR [default|proposal|onboarding|measures|titration]
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
from dataclasses import replace  # noqa: E402

from contracts import DosingSettings, HighTier, LowTier, Meal, NoteTag, Reading, ReadingNote, Titration  # noqa: E402
from services.store import Store  # noqa: E402

# protocole fictif pour les captures, pas une recommandation
DEMO_PROTOCOL = DosingSettings(insulin="Insuline de démonstration", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)

# scénario « titration » : paliers et dose du matin ajustée sur la glycémie du soir (fictif lui aussi)
DEMO_FULL = replace(
    DEMO_PROTOCOL,
    low_tiers=(LowTier(0.60, 4),),
    high_tiers=(HighTier(2.20, 4, 2),),
    morning_titration=Titration(0.90, 1.60, 1, 2, (LowTier(0.70, 2),), ()),
)

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


def _high_mornings_until_today(meal: Meal | None = None, hour: time = time(7, 30)) -> list[Reading]:
    today = datetime.combine(date.today(), hour)
    out = []
    for days_ago, mg in ((2, 182), (1, 205), (0, 176)):
        t = today - timedelta(days=days_ago)
        out.append(Reading(t, mg, int(t.timestamp()), meal=meal))
    return out


def _scroll_dialog(window: Gtk.Window, fraction: float) -> None:
    """Fait défiler le dialogue ouvert (fraction de la hauteur défilable), pour capturer le bas du formulaire."""
    def scrolled(widget):
        if isinstance(widget, Gtk.ScrolledWindow):
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = scrolled(child)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None

    dialog = window.get_visible_dialog()
    area = scrolled(dialog) if dialog is not None else None
    if area is not None:
        adj = area.get_vadjustment()
        adj.set_value((adj.get_upper() - adj.get_page_size()) * fraction)


def _both_doses_until_today() -> list[Reading]:
    """Trois matins hauts (hausse du soir) et un soir bas avant le dîner (baisse du matin)."""
    out = _high_mornings_until_today(Meal.FASTING, time(6, 50))
    t = datetime.combine(date.today(), time(18, 20))
    out.append(Reading(t, 78, int(t.timestamp()) + 5, meal=Meal.BEFORE_MEAL))
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
    if scenario == "titration":
        state.configure_protocol(DEMO_FULL, "Consultation (démonstration)")
        state.store.import_readings(_both_doses_until_today(), "démo")
    if scenario == "default":
        pages = list(PAGES)
    elif scenario == "measures":
        pages = list(MEASURES_SHOTS)
    elif scenario == "titration":
        pages = ["today", "doses", "preferences", "preferences-0.45", "preferences-1"]
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
            elif name == "preferences":
                window.show_preferences()
            elif name.startswith("preferences-"):
                _scroll_dialog(window, float(name.split("-")[1]))
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
