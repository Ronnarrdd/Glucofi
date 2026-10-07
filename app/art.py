"""Dessins de Glucofi, repris de la tablette (Common.kt, TodayScreen.kt) : logo, soleil et lune, renard sur sa tache.

Tout est tracé avec Gsk.Path et Gtk.Snapshot aux couleurs de app/theme.py : aucun chargeur SVG en couleur n'est
installé, et le logo suit le passage clair/sombre comme le reste. Le renard et la tache de pinceau sont les deux
illustrations de la tablette converties en PNG (scripts/illustrations.py). Le renard ne vit que dans la bannière.
"""

from __future__ import annotations

from functools import cache

from gi.repository import Adw, GObject, Gdk, Graphene, Gsk, Gtk

from app import theme

ILLUSTRATIONS_DIR = theme.APP_DIR / "illustrations"
FOX_FILE = ILLUSTRATIONS_DIR / "fox-banner.png"
BRUSH_FILE = ILLUSTRATIONS_DIR / "brush.png"


class MarkPaths:
    """Tracés de l'icône de lancement de la tablette (Common.kt, repère 128) : test_theme les garde identiques."""

    TILE = "M30,12H98A22,22 0,0 1,120 34V94A22,22 0,0 1,98 116H30A22,22 0,0 1,8 94V34A22,22 0,0 1,30 12Z"
    BAND = "M24,52H104A4,4 0,0 1,108 56V70A4,4 0,0 1,104 74H24A4,4 0,0 1,20 70V56A4,4 0,0 1,24 52Z"
    CURVE = "M20,84L38,70L52,78L68,52L84,62L108,40"
    DROP = "M96,10C96,10 80,30 80,41a16,16 0,0 0,32 0C112,30 96,10 96,10Z"
    VIEWPORT = 128


class DoodlePaths:
    """Soleil et lune des tuiles Matin et Soir (ic_doodle_sun.xml, ic_doodle_moon.xml, repère 64)."""

    SUN_DISC = "M32,19a13,13 0,1 1,0 26a13,13 0,1 1,0 -26Z"
    SUN_RAYS = (
        "M32,6L32,13M50.38,13.62L45.43,18.57M58,32L51,32M50.38,50.38L45.43,45.43"
        "M32,58L32,51M13.62,50.38L18.57,45.43M6,32L13,32M13.62,13.62L18.57,18.57"
    )
    MOON = (
        "M25.36,16.2A18,18 0,1 0,44.79 40.49A16,16 0,0 1,25.36 16.2Z",
        "M52,6Q52,12 58,12Q52,12 52,18Q52,12 46,12Q52,12 52,6Z",
        "M57,25Q57,29 61,29Q57,29 57,33Q57,29 53,29Q57,29 57,25Z",
    )
    VIEWPORT = 64


# montée du renard : ressort Compose DampingRatioLowBouncy, StiffnessVeryLow, de 48 px plus bas et transparent
RISE_DAMPING = 0.75
RISE_STIFFNESS = 50.0
RISE_OFFSET = 48.0

# bannière (FoxBanner de Common.kt)
BANNER_COMPACT_BELOW = 560
BANNER_MIN_HEIGHT = 216
ART_SHARE, ART_MIN, ART_MAX = 0.46, 240.0, 440.0
FOX_SHARE = 0.72
FOX_SHIFT = 0.2
BLOB_EXTRA = 100.0
BLOB_HEIGHT = 0.92
COMPACT_ART = (128, 156)
COMPACT_FOX_HEIGHT = 152
COMPACT_BLOB_HEIGHT = 0.75


@cache
def _path(data: str) -> Gsk.Path:
    path = Gsk.Path.parse(data)
    if path is None:
        raise ValueError(f"tracé illisible : {data[:30]}…")
    return path


@cache
def texture(path: str) -> Gdk.Texture:
    return Gdk.Texture.new_from_filename(path)


def fox_ratio() -> float:
    fox = texture(str(FOX_FILE))
    return fox.get_width() / fox.get_height()


def rgba(color: str, alpha: float = 1.0) -> Gdk.RGBA:
    value = Gdk.RGBA()
    value.parse(color)
    value.alpha = alpha
    return value


def release_children_on_destroy(widget: Gtk.Widget) -> None:
    """Détache les enfants d'un widget à mise en page maison quand GTK le détruit. PyGObject n'appelle pas un
    do_dispose écrit en Python : sans ce signal, GTK finalise le widget avec ses enfants encore attachés."""

    def release(parent: Gtk.Widget) -> None:
        child = parent.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            child.unparent()
            child = following

    widget.connect("destroy", release)


def _stroke(width: float) -> Gsk.Stroke:
    stroke = Gsk.Stroke.new(width)
    stroke.set_line_cap(Gsk.LineCap.ROUND)
    stroke.set_line_join(Gsk.LineJoin.ROUND)
    return stroke


class _Themed(Gtk.Widget):
    """Dessin aux couleurs de la palette en cours, redessiné au passage clair/sombre."""

    def __init__(self, size: int = 0, **props):
        super().__init__(accessible_role=Gtk.AccessibleRole.PRESENTATION, **props)
        self._size = size
        self._dark_handler = 0

    def colors(self) -> dict[str, str]:
        return theme.palette(Adw.StyleManager.get_default().get_dark())

    def do_realize(self):
        Gtk.Widget.do_realize(self)
        self._dark_handler = Adw.StyleManager.get_default().connect("notify::dark", lambda *_a: self.queue_draw())

    def do_unrealize(self):
        if self._dark_handler:
            Adw.StyleManager.get_default().disconnect(self._dark_handler)
            self._dark_handler = 0
        Gtk.Widget.do_unrealize(self)

    def do_measure(self, orientation, for_size):
        return self._size, self._size, -1, -1

    def _square(self) -> tuple[float, float, float]:
        """Côté et origine du plus grand carré centré dans l'allocation."""
        w, h = self.get_width(), self.get_height()
        side = min(w, h)
        return side, (w - side) / 2, (h - side) / 2


class GlucofiMark(_Themed):
    """Logo : bande de l'objectif, courbe et goutte sur une tuile, aux couleurs pastel du thème (GlucofiMark)."""

    __gtype_name__ = "GlucofiMark"

    def __init__(self, size: int = 40, **props):
        super().__init__(size=size, **props)

    def do_snapshot(self, snapshot):
        side, x, y = self._square()
        if side <= 0:
            return
        c = self.colors()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(x, y))
        snapshot.scale(side / MarkPaths.VIEWPORT, side / MarkPaths.VIEWPORT)
        snapshot.append_fill(_path(MarkPaths.TILE), Gsk.FillRule.WINDING, rgba(c["primary-container"]))
        snapshot.append_fill(_path(MarkPaths.BAND), Gsk.FillRule.WINDING, rgba(c["card"], 0.6))
        snapshot.append_stroke(_path(MarkPaths.CURVE), _stroke(7), rgba(c["primary"]))
        snapshot.append_fill(_path(MarkPaths.DROP), Gsk.FillRule.WINDING, rgba(c["tertiary"]))
        snapshot.append_stroke(_path(MarkPaths.DROP), _stroke(4), rgba(c["card"]))
        snapshot.restore()


class Doodle(_Themed):
    """Soleil (Matin) ou lune (Soir) des tuiles de dose, en aplats fixes lisibles en clair comme en sombre."""

    __gtype_name__ = "GlucofiDoodle"

    def __init__(self, kind: str, size: int = 80, **props):
        if kind not in ("sun", "moon"):
            raise ValueError(f"dessin inconnu : {kind}")
        super().__init__(size=size, **props)
        self.kind = kind

    def do_measure(self, orientation, for_size):
        # la dose passe avant : le dessin rapetisse quand la place manque, jusqu'à disparaître
        return 0, self._size, -1, -1

    def do_snapshot(self, snapshot):
        side, x, y = self._square()
        if side <= 0:
            return
        snapshot.save()
        snapshot.translate(Graphene.Point().init(x, y))
        snapshot.scale(side / DoodlePaths.VIEWPORT, side / DoodlePaths.VIEWPORT)
        if self.kind == "sun":
            snapshot.append_fill(_path(DoodlePaths.SUN_DISC), Gsk.FillRule.WINDING, rgba(theme.DOODLE["sun"]))
            snapshot.append_stroke(_path(DoodlePaths.SUN_RAYS), _stroke(4), rgba(theme.DOODLE["sun-ray"]))
        else:
            for data in DoodlePaths.MOON:
                snapshot.append_fill(_path(data), Gsk.FillRule.WINDING, rgba(theme.DOODLE["moon"]))
        snapshot.restore()


class BrushBlob(_Themed):
    """Tache de pinceau derrière le renard : le PNG sert de masque, teint de banner-blob, étiré à l'allocation."""

    __gtype_name__ = "GlucofiBrushBlob"

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return
        bounds = Graphene.Rect().init(0, 0, w, h)
        snapshot.push_mask(Gsk.MaskMode.ALPHA)
        snapshot.append_scaled_texture(texture(str(BRUSH_FILE)), Gsk.ScalingFilter.TRILINEAR, bounds)
        snapshot.pop()
        snapshot.append_color(rgba(self.colors()["banner-blob"]), bounds)
        snapshot.pop()


class Fox(Gtk.Widget):
    """Renard au lecteur, dessiné à l'allocation. Il monte et apparaît une fois, en ressort, sauf animations coupées
    (Adw.Animation saute alors à la fin)."""

    __gtype_name__ = "GlucofiFox"

    def __init__(self, **props):
        super().__init__(accessible_role=Gtk.AccessibleRole.PRESENTATION, **props)
        self.rise = 0.0
        target = Adw.CallbackAnimationTarget.new(self._set_rise)
        params = Adw.SpringParams.new(RISE_DAMPING, 1.0, RISE_STIFFNESS)
        self.animation = Adw.SpringAnimation.new(self, 0.0, 1.0, params, target)
        self._played = False

    def _set_rise(self, value: float) -> None:
        self.rise = value
        self.queue_draw()

    def do_map(self):
        Gtk.Widget.do_map(self)
        if not self._played:
            self._played = True
            self.animation.play()

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return
        opacity = min(max(self.rise, 0.0), 1.0)
        if opacity <= 0:
            return
        snapshot.save()
        snapshot.translate(Graphene.Point().init(0, (1.0 - self.rise) * RISE_OFFSET))
        snapshot.push_opacity(opacity)
        snapshot.append_scaled_texture(
            texture(str(FOX_FILE)), Gsk.ScalingFilter.TRILINEAR, Graphene.Rect().init(0, 0, w, h),
        )
        snapshot.pop()
        snapshot.restore()


def banner_geometry(width: float, height: float, compact: bool) -> dict[str, tuple[float, float, float, float]]:
    """Rectangles (x, y, largeur, hauteur) de la tache et du renard dans une bannière `width` x `height`.

    Large : renard à 46 % de la largeur (240 à 440 px), tête près du haut, coupé par le bas de la bannière et décalé
    à gauche pour laisser la pastille du coin toucher sa queue. Étroit : renard de 152 px dans une case de 128 x 156
    (placée par l'appelant, origine de la case)."""
    ratio = fox_ratio()
    if compact:
        box_w, box_h = COMPACT_ART
        blob_h = box_h * COMPACT_BLOB_HEIGHT
        fox_h = COMPACT_FOX_HEIGHT
        fox_w = fox_h * ratio
        return {
            "blob": (0.0, (box_h - blob_h) / 2, float(box_w), blob_h),
            "fox": ((box_w - fox_w) / 2, (box_h - fox_h) / 2, fox_w, float(fox_h)),
        }
    art = min(max(width * ART_SHARE, ART_MIN), ART_MAX)
    blob_w, blob_h = art + BLOB_EXTRA, height * BLOB_HEIGHT
    fox_h = art * FOX_SHARE
    fox_w = fox_h * ratio
    return {
        "blob": (width - blob_w, (height - blob_h) / 2, blob_w, blob_h),
        "fox": (width - art + (art - fox_w) / 2 - art * FOX_SHIFT, 12.0, fox_w, fox_h),
        "art": (width - art, 0.0, art, height),
    }


class FoxBanner(Gtk.Widget):
    """Bannière rose du renard (seul endroit où il apparaît) : titre, sous-titre et actions à gauche, renard sur sa
    tache à droite, pastille facultative dans le coin bas droit.

    `compact` (posé par un point de rupture de la fenêtre ou du dialogue) passe en étroit : renard de 152 px à côté du
    titre, pastille puis actions en pleine largeur dessous. Le mode ne change jamais pendant l'allocation."""

    __gtype_name__ = "GlucofiFoxBanner"

    def __init__(self, title: str, subtitle: str | None = None, corner: Gtk.Widget | None = None):
        super().__init__(css_name="foxbanner")
        self.add_css_class("fox-banner")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._compact = False
        self.blob = BrushBlob()
        self.fox = Fox()
        self.heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(label=title, xalign=0, wrap=True, accessible_role=Gtk.AccessibleRole.HEADING)
        self.title.add_css_class("banner-title")
        self.heading.append(self.title)
        self.subtitle = Gtk.Label(label=subtitle or "", xalign=0, wrap=True, visible=bool(subtitle))
        self.subtitle.add_css_class("banner-subtitle")
        self.heading.append(self.subtitle)
        self.actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.corner = corner
        for child in (self.blob, self.fox, self.heading, self.actions, corner):
            if child is not None:
                child.set_parent(self)
        release_children_on_destroy(self)
        self._apply_mode()

    @GObject.Property(type=bool, default=False)
    def compact(self) -> bool:
        return self._compact

    @compact.setter
    def compact(self, value: bool) -> None:
        if value != self._compact:
            self._compact = value
            self._apply_mode()
            self.queue_resize()

    def set_subtitle(self, text: str | None) -> None:
        self.subtitle.set_label(text or "")
        self.subtitle.set_visible(bool(text))

    def add_action(self, widget: Gtk.Widget) -> None:
        self.actions.append(widget)
        self._apply_mode()

    def _apply_mode(self) -> None:
        compact = self._compact
        for widget, wide, narrow in (
            (self.title, "display-medium", "headline-large"),
            (self.subtitle, "title-large", "title-medium"),
        ):
            widget.remove_css_class(narrow if not compact else wide)
            widget.add_css_class(narrow if compact else wide)
        self.heading.set_spacing(4 if compact else 6)
        if compact:
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")
        child = self.actions.get_first_child()
        while child is not None:
            child.set_halign(Gtk.Align.FILL if compact else Gtk.Align.START)
            child = child.get_next_sibling()

    # mise en page

    @staticmethod
    def _height(widget: Gtk.Widget | None, width: int) -> int:
        if widget is None or not widget.get_visible():
            return 0
        return widget.measure(Gtk.Orientation.VERTICAL, max(width, 0))[1]

    def _text_width(self, width: int) -> int:
        if self._compact:
            return width - 40 - COMPACT_ART[0]
        art = min(max(width * ART_SHARE, ART_MIN), ART_MAX)
        return int(width - 32 - art)

    def _layout_height(self, width: int) -> int:
        corner_h = self._height(self.corner, width)
        if self._compact:
            row = max(self._height(self.heading, self._text_width(width)), COMPACT_ART[1])
            actions = self._height(self.actions, width - 40)
            return 20 + row + (16 + corner_h if corner_h else 0) + (16 + actions if actions else 0) + 20
        # en large, la pastille du coin est dans la part du renard : elle ne compte pas dans la colonne de texte
        text_w = self._text_width(width)
        actions = self._height(self.actions, text_w)
        column = self._height(self.heading, text_w) + (22 + actions if actions else 0)
        return max(BANNER_MIN_HEIGHT, 24 + column + 24, 16 + corner_h + 16)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            heading = self.heading.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
            actions = self.actions.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
            corner = self.corner.measure(Gtk.Orientation.HORIZONTAL, -1)[0] if self.corner else 0
            if self._compact:
                minimum = 40 + max(heading + COMPACT_ART[0], actions, corner)
            else:
                minimum = int(32 + max(heading, actions) + ART_MIN)
            return minimum, max(minimum, 960), -1, -1
        width = for_size if for_size >= 0 else 960
        height = self._layout_height(width)
        return height, height, -1, -1

    def _place(self, widget: Gtk.Widget, x: float, y: float, w: float, h: float) -> None:
        transform = Gsk.Transform().translate(Graphene.Point().init(x, y))
        widget.allocate(max(int(w), 0), max(int(h), 0), -1, transform)

    def do_size_allocate(self, width, height, baseline):
        art = banner_geometry(width, height, self._compact)
        corner_w = corner_h = 0
        if self.corner is not None:
            corner_w = self.corner.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            corner_h = self._height(self.corner, corner_w)
        if self._compact:
            text_w = self._text_width(width)
            heading_h = self._height(self.heading, text_w)
            row = max(heading_h, COMPACT_ART[1])
            box_x, box_y = width - 20 - COMPACT_ART[0], 20 + (row - COMPACT_ART[1]) / 2
            bx, by, bw, bh = art["blob"]
            self._place(self.blob, box_x + bx, box_y + by, bw, bh)
            fx, fy, fw, fh = art["fox"]
            self._place(self.fox, box_x + fx, box_y + fy, fw, fh)
            self._place(self.heading, 20, 20 + (row - heading_h) / 2, text_w, heading_h)
            y = 20 + row
            if self.corner is not None and corner_h:
                self._place(self.corner, 20, y + 16, min(corner_w, width - 40), corner_h)
                y += 16 + corner_h
            actions_h = self._height(self.actions, width - 40)
            self._place(self.actions, 20, y + 16, width - 40, actions_h)
            return
        self._place(self.blob, *art["blob"])
        self._place(self.fox, *art["fox"])
        text_w = self._text_width(width)
        heading_h = self._height(self.heading, text_w)
        actions_h = self._height(self.actions, text_w)
        self._place(self.heading, 32, 24, text_w, heading_h)
        self._place(self.actions, 32, 24 + heading_h + 22, text_w, actions_h)
        if self.corner is not None and corner_h:
            self._place(self.corner, width - 16 - corner_w, height - 16 - corner_h, corner_w, corner_h)
