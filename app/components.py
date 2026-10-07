"""Composants de Glucofi PC, un par composant de la tablette (Common.kt, TodayScreen.kt, MeasuresScreen.kt).

Les tailles, couleurs et polices sont dans app/style.css (classes du même nom), d'après DESIGN.md de la tablette.
La couleur ne porte jamais seule une information : un niveau de glycémie a toujours sa flèche ou son libellé.
"""

from __future__ import annotations

from typing import Callable, Sequence

from gi.repository import Adw, GObject, Gtk

from app.art import Doodle

CONTENT_MAX = 1040
MARGIN_WIDE, MARGIN_NARROW = 32, 16
ARROWS = {"low": "glucofi-arrow-down-symbolic", "high": "glucofi-arrow-up-symbolic"}
MOMENT_ICONS = {"morning": "glucofi-morning-symbolic", "evening": "glucofi-evening-symbolic"}
NOTICE_ICONS = {
    "info": "glucofi-info-symbolic",
    "success": "glucofi-check-symbolic",
    "warning": "glucofi-warning-symbolic",
    "danger": "glucofi-error-symbolic",
}
BUTTON_KINDS = ("primary", "tonal", "outlined", "text")


def label(text: str, *classes: str, **props) -> Gtk.Label:
    widget = Gtk.Label(label=text, **props)
    for css in classes:
        widget.add_css_class(css)
    return widget


def heading(text: str, css: str, **props) -> Gtk.Label:
    """Titre lu comme tel par les lecteurs d'écran."""
    props.setdefault("xalign", 0)
    props.setdefault("wrap", True)
    return label(text, css, accessible_role=Gtk.AccessibleRole.HEADING, **props)


def clear(box: Gtk.Box) -> None:
    while (child := box.get_first_child()) is not None:
        box.remove(child)


def describe(widget: Gtk.Widget, text: str) -> None:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


def screen_title(text: str) -> Gtk.Label:
    """Titre d'écran (Mesures, Graphiques, Doses) : Fredoka 600, 36 px."""
    return heading(text, "display-small")


def button(
    text: str,
    icon: str | None = None,
    kind: str = "primary",
    tall: bool = False,
    on_click: Callable[[], None] | None = None,
    action_name: str | None = None,
    **props,
) -> Gtk.Button:
    """Bouton en pilule : plein bleu canard (action principale, 56 px avec `tall`), tonal sauge, contour ou texte."""
    if kind not in BUTTON_KINDS:
        raise ValueError(f"bouton inconnu : {kind}")
    # label=None passé avec child remplacerait le contenu par un libellé vide
    content = {"child": Adw.ButtonContent(icon_name=icon, label=text, can_shrink=True)} if icon else {"label": text}
    widget = Gtk.Button(**content, **props)
    widget.add_css_class("pill-button")
    widget.add_css_class(kind)
    if kind == "primary":
        widget.add_css_class("suggested-action")
    if tall:
        widget.add_css_class("tall")
    if action_name:
        widget.set_action_name(action_name)
    if on_click is not None:
        widget.connect("clicked", lambda _b: on_click())
    return widget


def icon_button(icon: str, description: str, on_click: Callable[[], None] | None = None, css: str = "flat") -> Gtk.Button:
    widget = Gtk.Button(icon_name=icon, tooltip_text=description, valign=Gtk.Align.CENTER)
    widget.add_css_class("icon-button")
    widget.add_css_class(css)
    describe(widget, description)
    if on_click is not None:
        widget.connect("clicked", lambda _b: on_click())
    return widget


class Page(Gtk.ScrolledWindow):
    """Colonne centrée de 1040 px au plus sur le fond sauge, marges de 32 px (16 en étroit), blocs espacés de 16."""

    __gtype_name__ = "GlucofiPage"

    def __init__(self, spacing: int = 16, top: int = 28):
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        self.box.set_margin_top(top)
        self.box.set_margin_bottom(32)
        for side in ("start", "end"):
            getattr(self.box, f"set_margin_{side}")(MARGIN_WIDE)
        self.set_child(Adw.Clamp(maximum_size=CONTENT_MAX, tightening_threshold=CONTENT_MAX, child=self.box))

    def append(self, widget: Gtk.Widget) -> None:
        self.box.append(widget)

    def narrow_setters(self, breakpoint: Adw.Breakpoint) -> None:
        for prop in ("margin-start", "margin-end"):
            breakpoint.add_setter(self.box, prop, MARGIN_NARROW)


class Section(Gtk.Box):
    """Carte blanche à grands arrondis (24 px, marge 24) : titre Fredoka 22 px, sous-titre, action à droite."""

    __gtype_name__ = "GlucofiSection"

    def __init__(self, title: str | None = None, subtitle: str | None = None, action: Gtk.Widget | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("section-card")
        self.title_label = None
        if title is not None or action is not None:
            row = Gtk.Box(spacing=12)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
            if title is not None:
                self.title_label = heading(title, "title-large")
                texts.append(self.title_label)
            if subtitle:
                texts.append(label(subtitle, "body-medium", "muted", xalign=0, wrap=True))
            row.append(texts)
            if action is not None:
                action.set_valign(Gtk.Align.CENTER)
                row.append(action)
            self.append(row)


def divider() -> Gtk.Separator:
    sep = Gtk.Separator()
    sep.add_css_class("divider")
    return sep


class Notice(Gtk.Box):
    """Bandeau : icône dans une pastille ronde, texte, action facultative. Info, réussite, avertissement ou danger."""

    __gtype_name__ = "GlucofiNotice"

    def __init__(self, text: str, kind: str = "info", trailing: Gtk.Widget | None = None):
        if kind not in NOTICE_ICONS:
            raise ValueError(f"bandeau inconnu : {kind}")
        super().__init__(spacing=14)
        self.kind = kind
        self.add_css_class("notice")
        self.add_css_class(f"notice-{kind}")
        circle = Gtk.Box(valign=Gtk.Align.CENTER)
        circle.add_css_class("notice-icon")
        circle.append(Gtk.Image(icon_name=NOTICE_ICONS[kind], pixel_size=24, accessible_role=Gtk.AccessibleRole.PRESENTATION))
        self.append(circle)
        self.text = label(text, "body-large", xalign=0, wrap=True, hexpand=True, valign=Gtk.Align.CENTER)
        self.text.set_selectable(False)
        self.append(self.text)
        if trailing is not None:
            trailing.set_valign(Gtk.Align.CENTER)
            self.append(trailing)


class LevelChip(Gtk.Box):
    """Glycémie en pilule sur le conteneur de son niveau, flèche hors objectif ; grisée si la mesure est écartée."""

    __gtype_name__ = "GlucofiLevelChip"

    def __init__(self, text: str, level: str | None, level_label: str | None = None, dimmed: bool = False):
        super().__init__(spacing=4, valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
        self.add_css_class("level-chip")
        self.add_css_class("dimmed" if dimmed or level is None else f"level-{level}")
        if level in ARROWS:
            self.append(Gtk.Image(icon_name=ARROWS[level], pixel_size=16, accessible_role=Gtk.AccessibleRole.PRESENTATION))
        self.append(label(text, "label-large"))
        description = ", ".join(part for part in (text, level_label) if part)
        describe(self, description)
        if level_label:
            self.set_tooltip_text(level_label)


class LevelValue(Gtk.Box):
    """Valeur colorée selon le niveau, avec sa flèche (listes de mesures, dernière mesure)."""

    __gtype_name__ = "GlucofiLevelValue"

    def __init__(self, value: str, level: str | None, level_label: str | None = None, dimmed: bool = False):
        super().__init__(spacing=2, valign=Gtk.Align.CENTER)
        self.add_css_class("level-value")
        self.add_css_class("dimmed" if dimmed or level is None else f"level-{level}")
        if level in ARROWS:
            self.append(Gtk.Image(icon_name=ARROWS[level], pixel_size=20, accessible_role=Gtk.AccessibleRole.PRESENTATION))
        self.append(label(value, "title-medium", "value"))
        describe(self, ", ".join(part for part in (value, level_label) if part))
        if level_label:
            self.set_tooltip_text(level_label)


def tag(text: str, current: bool = False, tooltip: str | None = None) -> Gtk.Label:
    """Étiquette en pilule (« Retenue », « Écartée », « En cours ») : information, pas action."""
    widget = label(text, "tag", "label-medium", valign=Gtk.Align.CENTER)
    if current:
        widget.add_css_class("tag-current")
    if tooltip:
        widget.set_tooltip_text(tooltip)
    return widget


class FilterChips(Gtk.ScrolledWindow):
    """Rangée défilante de pilules blanches exclusives ; la choisie passe en bleu canard avec sa coche. Le bord droit
    s'estompe tant qu'il reste des filtres cachés (undershoot de la fenêtre défilante)."""

    __gtype_name__ = "GlucofiFilterChips"

    def __init__(self, options: Sequence[tuple[str, str]], active: str, on_change: Callable[[str], None], name: str = ""):
        super().__init__(
            hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
        )
        self.add_css_class("filter-chips")
        self._on_change = on_change
        self._active = active
        row = Gtk.Box(spacing=8)
        if name:
            describe(row, name)
        self.chips: dict[str, Gtk.ToggleButton] = {}
        first = None
        for key, text in options:
            content = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
            check = Gtk.Image(icon_name="glucofi-check-symbolic", pixel_size=18, visible=key == active)
            content.append(check)
            content.append(label(text, "label-large"))
            chip = Gtk.ToggleButton(child=content, active=key == active, group=first)
            chip.add_css_class("filter-chip")
            chip.check = check
            chip.connect("toggled", self._toggled, key)
            first = first or chip
            self.chips[key] = chip
            row.append(chip)
        self.set_child(row)

    @property
    def active(self) -> str:
        return self._active

    def set_active(self, key: str) -> None:
        self.chips[key].set_active(True)

    def _toggled(self, chip: Gtk.ToggleButton, key: str) -> None:
        chip.check.set_visible(chip.get_active())
        if chip.get_active() and key != self._active:
            self._active = key
            self._on_change(key)


class MeterChip(Gtk.Box):
    """Pastille menthe « Lecteur branché » ou « Lecteur non branché », icône USB."""

    __gtype_name__ = "GlucofiMeterChip"

    def __init__(self):
        super().__init__(spacing=6, valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
        self.add_css_class("meter-chip")
        self.append(Gtk.Image(icon_name="glucofi-usb-symbolic", pixel_size=18, accessible_role=Gtk.AccessibleRole.PRESENTATION))
        self.text = label("", "label-large")
        self.append(self.text)
        self.plugged: bool | None = None
        self.set_plugged(False)

    def set_plugged(self, plugged: bool) -> None:
        if plugged == self.plugged:
            return
        self.plugged = plugged
        self.text.set_label("Lecteur branché" if plugged else "Lecteur non branché")


class DoseStack(Gtk.Stack):
    """Dose en très gros (« 9 UI ») qui défile vers le haut quand elle monte, vers le bas quand elle baisse."""

    __gtype_name__ = "GlucofiDoseStack"

    def __init__(self):
        super().__init__(interpolate_size=True, vhomogeneous=False, hhomogeneous=False, transition_duration=350)
        self.value: int | None = None
        self._labels = [self._dose_label(), self._dose_label()]
        for i, widget in enumerate(self._labels):
            self.add_named(widget, str(i))
        self._shown = 0

    @staticmethod
    def _dose_label() -> Gtk.Label:
        widget = Gtk.Label(use_markup=True)
        widget.add_css_class("dose-number")
        return widget

    @staticmethod
    def markup(ui: int) -> str:
        return f'{ui}<span size="45.8%"> UI</span>'

    def set_value(self, ui: int) -> None:
        if ui == self.value:
            return
        if self.value is None:
            self._labels[self._shown].set_markup(self.markup(ui))
            self.set_visible_child_name(str(self._shown))
        else:
            up = ui > self.value
            self.set_transition_type(Gtk.StackTransitionType.SLIDE_UP if up else Gtk.StackTransitionType.SLIDE_DOWN)
            self._shown = 1 - self._shown
            self._labels[self._shown].set_markup(self.markup(ui))
            self.set_visible_child_name(str(self._shown))
        self.value = ui

    @property
    def text(self) -> str:
        return self._labels[self._shown].get_text()


class MomentTile(Gtk.Box):
    """Tuile pastel d'un moment (pêche le matin, lavande le soir) : icône et libellé, dose en 96 px à côté du soleil ou
    de la lune, pastille d'état, puis « Valider N UI » pleine largeur qui se replie une fois la dose validée."""

    __gtype_name__ = "GlucofiMomentTile"

    def __init__(self, target: str, title: str, on_validate: Callable[[], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.target = target
        self.add_css_class("moment-tile")
        self.add_css_class(f"moment-{target}")
        head = Gtk.Box(spacing=10)
        head.append(Gtk.Image(icon_name=MOMENT_ICONS[target], pixel_size=28, accessible_role=Gtk.AccessibleRole.PRESENTATION))
        head.append(heading(title, "headline-small", wrap=False))
        self.append(head)
        self.append(Gtk.Box(vexpand=True))
        dose_row = Gtk.Box(spacing=16, halign=Gtk.Align.CENTER, margin_top=8)
        self.dose = DoseStack()
        dose_row.append(self.dose)
        self.doodle = Doodle("sun" if target == "morning" else "moon", size=80, valign=Gtk.Align.CENTER)
        dose_row.append(self.doodle)
        self.append(dose_row)
        self.status = label("", "status-pill", "title-medium", halign=Gtk.Align.CENTER, wrap=True, justify=Gtk.Justification.CENTER)
        self.append(self.status)
        self.append(Gtk.Box(vexpand=True, height_request=16))
        self.validate = button("", kind="primary", tall=True, on_click=on_validate, margin_top=4)
        self.revealer = Gtk.Revealer(
            child=self.validate, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=300,
        )
        self.append(self.revealer)

    def update(self, current_ui: int, proposed_ui: int | None, status: str, description: str) -> None:
        shown = proposed_ui if proposed_ui is not None else current_ui
        self.dose.set_value(shown)
        self.status.set_label(status)
        if proposed_ui is not None:
            self.status.add_css_class("proposed")
            self.validate.set_label(f"Valider {proposed_ui} UI")
        else:
            self.status.remove_css_class("proposed")
        self.revealer.set_reveal_child(proposed_ui is not None)
        describe(self.dose, description)


class NavBar(Gtk.Box):
    """Destinations en pilules : la destination active passe en bleu canard, icône pleine. En haut, icône et libellé
    côte à côte ; en bas (fenêtre étroite, `compact`), l'icône au-dessus du libellé."""

    __gtype_name__ = "GlucofiNavBar"

    def __init__(self, stack: Adw.ViewStack | Gtk.Stack, items: Sequence[tuple[str, str, str, str]], compact: bool = False):
        super().__init__(spacing=4 if not compact else 0, halign=Gtk.Align.CENTER, homogeneous=compact)
        self.add_css_class("navbar")
        self.stack = stack
        self._compact = compact
        self.buttons: dict[str, Gtk.ToggleButton] = {}
        self._icons: dict[str, tuple[Gtk.Image, str, str]] = {}
        first = None
        for name, text, icon, icon_active in items:
            content = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
            image = Gtk.Image(icon_name=icon, pixel_size=22, accessible_role=Gtk.AccessibleRole.PRESENTATION)
            content.append(image)
            content.append(label(text, "nav-label"))
            item = Gtk.ToggleButton(child=content, group=first, tooltip_text=text)
            item.add_css_class("nav-item")
            item.connect("toggled", self._toggled, name)
            first = first or item
            self.buttons[name] = item
            self._icons[name] = (image, icon, icon_active)
            self.append(item)
        self._set_compact(compact)
        self._handler = stack.connect("notify::visible-child-name", lambda *_a: self._follow())
        self.connect("destroy", lambda *_a: stack.disconnect(self._handler))
        self._follow()

    def _set_compact(self, compact: bool) -> None:
        for item in self.buttons.values():
            content = item.get_child()
            content.set_orientation(Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)
            content.set_spacing(4 if compact else 8)
        if compact:
            self.add_css_class("compact")
            self.set_halign(Gtk.Align.FILL)
        else:
            self.remove_css_class("compact")

    @property
    def active(self) -> str | None:
        return self.stack.get_visible_child_name()

    def _follow(self) -> None:
        current = self.stack.get_visible_child_name()
        for name, item in self.buttons.items():
            if (name == current) != item.get_active() and name == current:
                item.set_active(True)
            image, icon, icon_active = self._icons[name]
            image.set_from_icon_name(icon_active if name == current else icon)

    def _toggled(self, item: Gtk.ToggleButton, name: str) -> None:
        if item.get_active() and self.stack.get_visible_child_name() != name:
            self.stack.set_visible_child_name(name)
        self._follow()
