# services/store

SQLite (`~/.local/share/glucofi/glucofi.db`) : tables `readings` (unicité `device_time, mg_dl`, import idempotent, marqueur repas `meal`, `meter_serial`), `reading_notes` (note saisie dans Glucofi, clé `device_time, mg_dl` : étiquettes JSON, texte, `exclude_from_dosing`), `meters` (identité de chaque lecteur importé, première et dernière lecture), `dose_changes` (avec `excluded` : glycémies du matin écartées lors de la validation), `settings`, `imports` (journal de chaque import : reçues, ajoutées, rejetées, marqueurs ajoutés, lecteur, écart et action d'horloge, mesures annoncées).

Notes : `Store.readings()` attache la note à chaque mesure (`Reading.note`, jointure gauche) ; `Store.set_note(reading, note)` crée, remplace ou efface (note vide ou `None`) et refuse une mesure inconnue. Les notes sont dans leur propre table : un import du lecteur ne peut ni les effacer ni les dupliquer. Une étiquette inconnue (base écrite par une version plus récente) est ignorée ; si l'exclusion perd ainsi son motif, la mesure n'est plus écartée.

Un import met à jour le marqueur des mesures déjà en base quand le lecteur en donne un (`ON CONFLICT ... DO UPDATE`) ; un import sans marqueur (fichier ancien format) n'efface jamais un marqueur connu.

## Migrations

`settings.schema_version` donne la version du schéma ; `Store(...)` migre automatiquement à l'ouverture.

- **v1 -> v2** : l'ancien accuchek calculait `epoch` en heure d'hiver toute l'année. La migration recalcule `epoch` depuis `device_time` (`contracts.local_epoch`, heure d'été comprise) et passe l'unicité de `(epoch, mg_dl)` à `(device_time, mg_dl)`, pour que les mesures d'été relues avec accuchek corrigé ne soient pas importées en double. Une copie de la base est faite avant : `glucofi.db.v1-AAAAMMJJ-HHMMSS.bak`. Le résultat (`Store.last_migration`) est écrit dans le journal.
- **v2 -> v3** : colonnes `readings.meal`, `readings.meter_serial`, détail des imports, table `meters`. Aucune ligne modifiée : les marqueurs des mesures déjà en base arrivent au prochain « Récupérer ». Une base v1 passe par v2 puis v3, avec une seule copie (`glucofi.db.v<version>-AAAAMMJJ-HHMMSS.bak`) ; `Store.migrations` liste les étapes.
- **v3 -> v4** : table `reading_notes` et colonne `dose_changes.excluded` (`'[]'` pour les doses déjà validées). Aucune ligne modifiée. Vérification sur une copie de la vraie base : `python3 -m evals.store_migration` (doses et mesures inchangées).
