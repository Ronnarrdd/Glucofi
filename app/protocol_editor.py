"""Éditeur GTK du protocole : seuils, paliers, dose du matin, plages horaires (formulaire de app.protocol).

Chaque groupe est une carte blanche (classe form-section) ; chaque champ un champ à contour avec son unité
(« g/L », « UI », « jours », « HH:MM ») en suffixe, comme sur la tablette.
"""

from __future__ import annotations

from gi.repository import Adw, Gtk

from app.protocol import FormError, protocol_from_form

LOW_COLUMNS = (("below_g_l", "Sous", "0,60", "g/L"), ("step_ui", "baisser de", "4", "UI"))
HIGH_COLUMNS = (("above_g_l", "Au-dessus de", "2,00", "g/L"), ("step_ui", "augmenter de", "4", "UI"), ("days", "après", "2", "jours"))


def form_group(title: str | None = None, description: str | None = None) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(title=title or "", description=description)
    group.add_css_class("form-section")
    return group


def entry_row(title: str, text: str = "", suffix: str | None = None) -> Adw.EntryRow:
    row = Adw.EntryRow(title=title)
    row.set_text(text)
    row.connect("changed", lambda r: r.remove_css_class("error"))
    if suffix:
        unit = Gtk.Label(label=suffix, valign=Gtk.Align.CENTER)
        unit.add_css_class("unit-suffix")
        row.add_suffix(unit)
    return row


class TierList:
    """Paliers d'une sorte (baisse ou hausse) pour une dose : une ligne par palier, ajout et suppression."""

    def __init__(self, group: Adw.PreferencesGroup, key: str, columns, label: str, add_label: str, rows: list[dict]):
        self.group, self.key, self.columns, self.label = group, key, columns, label
        self.rows: list[tuple[Adw.PreferencesRow, dict[str, Gtk.Entry]]] = []
        self.add_button = Adw.ButtonRow(title=add_label, start_icon_name="glucofi-add-symbolic")
        self.add_button.connect("activated", lambda _b: self.add({}))
        group.add(self.add_button)
        for values in rows:
            self.add(values)

    def add(self, values: dict) -> None:
        row = Adw.PreferencesRow(title=self.label)
        row.add_css_class("tier-row")
        # tient dans 360 px (dialogue étroit) : titre au-dessus des champs de 4 caractères (« 0,60 », « 2,20 »)
        line = Gtk.Box(spacing=8, margin_top=10, margin_bottom=10, margin_start=16, margin_end=6)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True, valign=Gtk.Align.CENTER)
        title = Gtk.Label(xalign=0, valign=Gtk.Align.CENTER)
        title.add_css_class("title-small")
        column.append(title)
        fields = Adw.WrapBox(child_spacing=12, line_spacing=6, halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
        entries = {}
        for name, before, placeholder, unit in self.columns:
            unit_box = Gtk.Box(spacing=6)
            unit_box.append(Gtk.Label(label=before))
            entry = Gtk.Entry(width_chars=4, max_width_chars=4, placeholder_text=placeholder, text=values.get(name, ""))
            entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
            entry.update_property([Gtk.AccessibleProperty.LABEL], [f"{before} ({unit})"])
            entry.connect("changed", lambda e: e.remove_css_class("error"))
            unit_box.append(entry)
            unit_box.append(Gtk.Label(label=unit))
            fields.append(unit_box)
            entries[name] = entry
        column.append(fields)
        line.append(column)
        remove = Gtk.Button(icon_name="glucofi-remove-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Supprimer ce palier")
        remove.add_css_class("flat")
        remove.add_css_class("circular")
        remove.update_property([Gtk.AccessibleProperty.LABEL], ["Supprimer ce palier"])
        remove.connect("clicked", lambda _b: self.remove(row))
        line.append(remove)
        row.set_child(line)
        row.title_label = title
        self.group.remove(self.add_button)
        self.group.add(row)
        self.group.add(self.add_button)
        self.rows.append((row, entries))
        self._renumber()

    def remove(self, row: Adw.PreferencesRow) -> None:
        self.group.remove(row)
        self.rows = [(r, e) for r, e in self.rows if r is not row]
        self._renumber()

    def _renumber(self) -> None:
        for i, (row, _entries) in enumerate(self.rows):
            row.set_title(f"{self.label} {i + 1}")
            row.title_label.set_label(f"{self.label} {i + 1}")

    def values(self) -> list[dict]:
        return [{name: entry.get_text() for name, entry in entries.items()} for _row, entries in self.rows]

    def widget(self, field: str) -> Gtk.Widget | None:
        """`field` = "<key>.<n>.<colonne>"."""
        _key, index, name = field.split(".")
        return self.rows[int(index)][1].get(name) if int(index) < len(self.rows) else None


class ProtocolEditor:
    """Groupes à empiler dans un formulaire ; `read()` rend (protocole, None) ou (None, message)."""

    def __init__(self, form: dict):
        self.rows: dict[str, Adw.EntryRow] = {}
        self.tiers: dict[str, TierList] = {}
        self.groups: list[Adw.PreferencesGroup] = []

        evening = self._group(
            "Dose du soir",
            "Recopiez les valeurs de l'ordonnance. Glycémie du matin sous le seuil bas : la dose du soir baisse "
            "du pas. Au-dessus du seuil haut le nombre de jours indiqué : elle augmente du pas. "
            "Glucofi ne fait que proposer ; rien n'est appliqué sans votre validation.",
        )
        self._rows(evening, form, (
            ("insulin", "Insuline (nom sur l'ordonnance)", None),
            ("low_g_l", "Seuil bas de la glycémie du matin", "g/L"),
            ("high_g_l", "Seuil haut de la glycémie du matin", "g/L"),
            ("step_ui", "Pas d'ajustement", "UI"),
            ("high_streak_days", "Jours consécutifs au-dessus pour une hausse", "jours"),
        ))
        window = self._group(
            "Glycémie du matin",
            "Plage horaire (heure du lecteur) : première mesure « à jeun », sinon « avant repas » ou sans marqueur.",
        )
        self._rows(window, form, (("morning_start", "Début", "HH:MM"), ("morning_end", "Fin", "HH:MM")))
        self._tier_group("", "Paliers de la dose du soir", form)

        self.morning = self._group(
            "Dose du matin",
            "Si le médecin ajuste aussi la dose du matin, selon la glycémie du soir (avant le dîner).",
        )
        self.morning_switch = Adw.SwitchRow(
            title="Ajuster la dose du matin",
            subtitle="Sinon, elle ne change que si vous la modifiez à la main",
            active=form.get("morning_enabled") == "1",
        )
        self.morning.add(self.morning_switch)
        self.morning_rows = self._rows(self.morning, form, (
            ("m_low_g_l", "Seuil bas de la glycémie du soir", "g/L"),
            ("m_high_g_l", "Seuil haut de la glycémie du soir", "g/L"),
            ("m_step_ui", "Pas d'ajustement", "UI"),
            ("m_high_streak_days", "Jours consécutifs au-dessus pour une hausse", "jours"),
            ("evening_start", "Glycémie du soir : début de la plage", "HH:MM"),
            ("evening_end", "Glycémie du soir : fin de la plage", "HH:MM"),
        ))
        self.morning_tiers = self._tier_group("m_", "Paliers de la dose du matin", form)
        self.morning_switch.connect("notify::active", lambda *_a: self._sync_morning())
        self._sync_morning()

    def _group(self, title: str, description: str) -> Adw.PreferencesGroup:
        group = form_group(title, description)
        self.groups.append(group)
        return group

    def _rows(self, group, form, fields) -> list[Adw.EntryRow]:
        out = []
        for key, title, unit in fields:
            row = entry_row(title, str(form.get(key, "")), suffix=unit)
            self.rows[key] = row
            group.add(row)
            out.append(row)
        return out

    def _tier_group(self, prefix: str, title: str, form: dict) -> Adw.PreferencesGroup:
        group = self._group(
            title,
            "Facultatif : glycémie encore plus basse, baisser davantage ; plus haute, augmenter davantage, "
            "éventuellement après moins de jours.",
        )
        self.tiers[f"{prefix}low_tiers"] = TierList(group, f"{prefix}low_tiers", LOW_COLUMNS, "Baisse", "Ajouter un palier de baisse", form.get(f"{prefix}low_tiers", []))
        self.tiers[f"{prefix}high_tiers"] = TierList(group, f"{prefix}high_tiers", HIGH_COLUMNS, "Hausse", "Ajouter un palier de hausse", form.get(f"{prefix}high_tiers", []))
        return group

    def _sync_morning(self) -> None:
        active = self.morning_switch.get_active()
        for row in self.morning_rows:
            row.set_visible(active)
        self.morning_tiers.set_visible(active)

    def form(self) -> dict:
        values = {key: row.get_text() for key, row in self.rows.items()}
        values |= {key: tiers.values() for key, tiers in self.tiers.items()}
        values["morning_enabled"] = "1" if self.morning_switch.get_active() else ""
        return values

    def read(self, base: dict):
        try:
            return protocol_from_form(self.form(), base), None
        except FormError as exc:
            self.mark(exc.field)
            return None, str(exc)

    def mark(self, field: str) -> None:
        if field in self.rows:
            widget = self.rows[field]
        elif "." in field and field.split(".")[0] in self.tiers:
            widget = self.tiers[field.split(".")[0]].widget(field)
        else:
            return
        if widget is not None:
            widget.add_css_class("error")
            widget.grab_focus()
