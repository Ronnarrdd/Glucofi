# services/store

SQLite (`~/.local/share/glucofi/glucofi.db`) : tables `readings` (unicité `device_time, mg_dl`, import idempotent, marqueur repas `meal`, `meter_serial`), `meters` (identité de chaque lecteur importé, première et dernière lecture), `dose_changes`, `settings`, `imports` (journal de chaque import : reçues, ajoutées, rejetées, marqueurs ajoutés, lecteur, écart et action d'horloge, mesures annoncées).

Un import met à jour le marqueur des mesures déjà en base quand le lecteur en donne un (`ON CONFLICT ... DO UPDATE`) ; un import sans marqueur (fichier ancien format) n'efface jamais un marqueur connu.

## Migrations

`settings.schema_version` donne la version du schéma ; `Store(...)` migre automatiquement à l'ouverture.

- **v1 -> v2** : l'ancien accuchek calculait `epoch` en heure d'hiver toute l'année. La migration recalcule `epoch` depuis `device_time` (`contracts.local_epoch`, heure d'été comprise) et passe l'unicité de `(epoch, mg_dl)` à `(device_time, mg_dl)`, pour que les mesures d'été relues avec accuchek corrigé ne soient pas importées en double. Une copie de la base est faite avant : `glucofi.db.v1-AAAAMMJJ-HHMMSS.bak`. Le résultat (`Store.last_migration`) est écrit dans le journal.
- **v2 -> v3** : colonnes `readings.meal`, `readings.meter_serial`, détail des imports, table `meters`. Aucune ligne modifiée : les marqueurs des mesures déjà en base arrivent au prochain « Récupérer ». Une base v1 passe par v2 puis v3, avec une seule copie (`glucofi.db.v<version>-AAAAMMJJ-HHMMSS.bak`) ; `Store.migrations` liste les étapes.
