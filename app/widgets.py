"""Petits widgets partagés par les onglets."""

from __future__ import annotations

from gi.repository import Adw, Gtk


def clear(box: Gtk.Box) -> None:
    while (child := box.get_first_child()) is not None:
        box.remove(child)


def page(child: Gtk.Widget, max_width: int = 820) -> Gtk.ScrolledWindow:
    clamp = Adw.Clamp(maximum_size=max_width, child=child)
    child.set_margin_top(18)
    child.set_margin_bottom(24)
    child.set_margin_start(12)
    child.set_margin_end(12)
    return Gtk.ScrolledWindow(child=clamp, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)


def toggle_group(options, active: str, on_change) -> Adw.ToggleGroup:
    group = Adw.ToggleGroup(halign=Gtk.Align.CENTER)
    group.add_css_class("round")
    for name, label in options:
        group.add(Adw.Toggle(name=name, label=label))
    group.set_active_name(active)
    group.connect("notify::active-name", lambda g, _p: on_change(g.get_active_name()))
    return group


def label(text: str, *classes: str, **props) -> Gtk.Label:
    widget = Gtk.Label(label=text, **props)
    for css in classes:
        widget.add_css_class(css)
    return widget
