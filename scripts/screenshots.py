"""Captures d'écran de chaque onglet, pour vérifier l'interface sans clic manuel.

Usage :
    python3 -m scripts.screenshots EXPORT.json SORTIE_DIR [default|proposal|onboarding|measures|titration]
    python3 -m scripts.screenshots --readme   # refait docs/screenshots (mesures fictives jusqu'à aujourd'hui)

Crée une base de démonstration temporaire, importe l'export, démarre le protocole au 1er du mois
précédent la dernière mesure. Tourne dans scripts/headless.sh (compositeur sans écran, rendu stable,
rien ne s'ouvre sur le bureau) ; --display pour capturer sur l'écran courant.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

from app.main import APP_ID, install_style  # noqa: E402
from app.state import AppState  # noqa: E402
from contracts import DosingSettings, HighTier, LowTier, Meal, NoteTag, Reading, ReadingNote, Titration  # noqa: E402
from scripts import demo_export, headless  # noqa: E402
from services.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
README_DIR = ROOT / "docs" / "screenshots"

# protocole fictif pour les captures, pas une recommandation
DEMO_PROTOCOL = DosingSettings(insulin="Insuline de démonstration", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)

# scénario « titration » : paliers et dose du matin ajustée sur la glycémie du soir (fictif lui aussi)
DEMO_FULL = replace(
    DEMO_PROTOCOL,
    low_tiers=(LowTier(0.60, 4),),
    high_tiers=(HighTier(2.20, 4, 2),),
    morning_titration=Titration(0.90, 1.60, 1, 2, (LowTier(0.70, 2),), ()),
)

SCENARIOS = ("default", "proposal", "onboarding", "measures", "titration")
PAGES = ("today", "charts", "measures", "doses")
WIDTH, HEIGHT = 1000, 1100
# scénario « measures » : onglet Mesures en largeur (px) et thème donnés
MEASURES_SHOTS = {"mesures-clair": (1000, False), "mesures-sombre": (1000, True), "mesures-etroit": (360, False)}
TITRATION_VIEWS = ("today", "doses", "preferences", "preferences-0.45", "preferences-1")
# captures du README, en clair à 1000 px : fichier -> (scénario, vues à enchaîner, la dernière est capturée)
README_SHOTS = {
    "aujourdhui.png": ("default", ("measures",)),
    "graphiques.png": ("default", ("charts",)),
    "premier-lancement.png": ("onboarding", ("today",)),
    "deux-doses.png": ("titration", ("today",)),
    "protocole.png": ("titration", ("doses", "preferences", "preferences-0.45")),
}


def render(window: Gtk.Window) -> Gdk.Texture | None:
    """Rendu de la fenêtre, pixel (0, 0) en haut à gauche ; None si elle n'a encore rien dessiné."""
    width, height = window.get_width(), window.get_height()
    paintable = Gtk.WidgetPaintable.new(window)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, width, height)
    node = snap.to_node()
    if node is None:
        return None
    area = Graphene.Rect()
    area.init(0, 0, width, height)
    return window.get_renderer().render_texture(node, area)


def snapshot(window: Gtk.Window, path: Path) -> bool:
    """False si la fenêtre n'a encore rien dessiné : réessayer plus tard."""
    texture = render(window)
    if texture is None:
        return False
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


def demo_export_file(days: int = 60, end: date | None = None) -> Path:
    """Export fictif de scripts.demo_export jusqu'à `end` (aujourd'hui par défaut), dans un fichier temporaire."""
    path = Path(tempfile.mkdtemp(prefix="glucofi-demo-export-")) / "demo.json"
    path.write_text(json.dumps(demo_export.build(days, end or date.today()), ensure_ascii=False), encoding="utf-8")
    return path


def demo_state(export: Path, scenario: str) -> AppState:
    """Base de démonstration temporaire pour `scenario` (voir SCENARIOS)."""
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
    return state


def show(window, view: str) -> None:
    """Affiche une vue : un onglet (PAGES), « preferences », ou « preferences-F » (dialogue défilé à la fraction F)."""
    if view == "preferences":
        window.show_preferences()
    elif view.startswith("preferences-"):
        _scroll_dialog(window, float(view.split("-")[1]))
    else:
        window.stack.set_visible_child_name(view)


def set_dark(dark: bool) -> None:
    scheme = Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT
    Adw.StyleManager.get_default().set_color_scheme(scheme)


# (scénario, vue, fichier ou None pour une vue intermédiaire, largeur, sombre)
Shot = tuple[str, str, Path | None, int, bool]


def scenario_shots(scenario: str, out_dir: Path) -> list[Shot]:
    if scenario == "measures":
        return [("measures", "measures", out_dir / f"{name}-measures.png", width, dark)
                for name, (width, dark) in MEASURES_SHOTS.items()]
    views = {"default": PAGES, "titration": TITRATION_VIEWS}.get(scenario, ("today",))
    return [(scenario, view, out_dir / f"{view}-{scenario}.png", WIDTH, False) for view in views]


def readme_shots(out_dir: Path) -> list[Shot]:
    shots: list[Shot] = []
    for name, (scenario, views) in README_SHOTS.items():
        shots += [(scenario, view, None, WIDTH, False) for view in views[:-1]]
        shots.append((scenario, views[-1], out_dir / name, WIDTH, False))
    return shots


def run(export: Path, shots: list[Shot]) -> int:
    """Enchaîne les captures ; une fenêtre (et une base) par scénario. 1 si une capture reste vide."""
    app = Adw.Application(application_id=APP_ID + ".Screenshots")
    states: dict[str, AppState] = {}
    current: dict[str, object] = {"scenario": None, "window": None}
    failed: list[str] = []

    def window_for(application, scenario: str):
        if current["scenario"] == scenario:
            return current["window"], False
        from app.window import MainWindow

        if current["window"] is not None:
            current["window"].destroy()
        if scenario not in states:
            states[scenario] = demo_state(export, scenario)
        window = MainWindow(application=application, state=states[scenario])
        window.set_default_size(WIDTH, HEIGHT)
        window.present()
        current.update(scenario=scenario, window=window)
        return window, True

    def on_activate(application):
        install_style(Gdk.Display.get_default())
        application.hold()

        def capture(window, path, attempt=0):
            if path is not None and not snapshot(window, path):
                if attempt < 20:
                    GLib.timeout_add(300, capture, window, path, attempt + 1)
                    return False
                print(f"capture vide après 6 s : {path.name}", file=sys.stderr)
                failed.append(path.name)
            if shots:
                step()
            else:
                application.release()
                application.quit()
            return False

        def step():
            scenario, view, path, width, dark = shots.pop(0)
            window, new = window_for(application, scenario)
            set_dark(dark)
            window.set_default_size(width, HEIGHT)

            def go():
                show(window, view)
                GLib.timeout_add(900, capture, window, path)
                return False

            GLib.timeout_add(1200 if new else 0, go)

        step()

    app.connect("activate", on_activate)
    app.run([])
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    display = "--display" in argv
    args = [a for a in argv if a != "--display"]
    if not display and not headless.in_headless():
        headless.reexec("scripts.screenshots", argv)
    if args[:1] == ["--readme"]:
        README_DIR.mkdir(parents=True, exist_ok=True)
        return run(demo_export_file(), readme_shots(README_DIR))
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    export, out_dir = Path(args[0]), Path(args[1])
    scenario = args[2] if len(args) > 2 else "default"
    if scenario not in SCENARIOS:
        print(f"scénario inconnu : {scenario} ({', '.join(SCENARIOS)})", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    return run(export, scenario_shots(scenario, out_dir))


if __name__ == "__main__":
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplcfg")
    sys.exit(main(sys.argv[1:]))
