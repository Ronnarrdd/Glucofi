# services/store

SQLite (`~/.local/share/glucofi/glucofi.db`) : tables `readings` (unicité `device_time, mg_dl`, import idempotent, marqueur repas `meal`, `meter_serial`), `reading_notes` (note saisie dans Glucofi, clé `device_time, mg_dl` : étiquettes JSON, texte, `exclude_from_dosing`), `injections` (journal des injections, clé `day, target` : `taken`, `missed` ou NULL pour « remise à non renseignée », `updated_at`), `meters` (identité de chaque lecteur importé, première et dernière lecture), `dose_changes` (avec `excluded` : glycémies écartées lors de la validation), `protocol_changes` (historique du protocole : date d'effet, protocole complet en JSON canonique, note), `settings`, `imports` (journal de chaque import : reçues, ajoutées, rejetées, marqueurs ajoutés, lecteur, écart et action d'horloge, mesures annoncées).

Notes : `Store.readings()` attache la note à chaque mesure (`Reading.note`, jointure gauche) ; `Store.set_note(reading, note)` crée, remplace ou efface (note vide ou `None`) et refuse une mesure inconnue. Effacer garde une ligne vide datée : l'effacement se propage ainsi à la fusion au lieu de revenir de l'autre appareil (`count_notes` et `readings` l'ignorent). Journal des injections (schéma v6) : `Store.set_injection(jour, dose, état)` et `Store.injections(since, until)` ; revenir à « non renseignée » garde une ligne NULL datée, comme une note effacée. Les notes sont dans leur propre table : un import du lecteur ne peut ni les effacer ni les dupliquer. Une étiquette inconnue (base écrite par une version plus récente) est ignorée ; si l'exclusion perd ainsi son motif, la mesure n'est plus écartée.

Un import met à jour le marqueur des mesures déjà en base quand le lecteur en donne un (`ON CONFLICT ... DO UPDATE`) ; un import sans marqueur (fichier ancien format) n'efface jamais un marqueur connu.

## Protocole et historique

`save_dosing_settings(settings, note="", effective=None)` écrit le protocole en cours (réglage `dosing`) et, s'il diffère de la dernière version, une ligne de `protocol_changes` (rendue ; `None` si rien n'a changé). Les protocoles sont comparés une fois complétés par les valeurs par défaut : un protocole écrit par une ancienne version (sans paliers ni dose du matin) vaut le même protocole réenregistré. `protocol_changes()` rend l'historique du plus ancien au plus récent (une version illisible est sautée, avec un avertissement dans le journal).

## Export et fusion (PC et tablette)

- `export_to(path)` : copie cohérente de la base (API de sauvegarde SQLite), à transporter sur l'autre appareil (adb, Téléchargements). Rien ne passe par le réseau.
- `merge_from(path) -> MergeSummary` : ajoute ce que l'autre base a en plus. Le fichier source n'est jamais modifié (il est copié et migré dans un dossier temporaire). Refus (`MergeRefused`, message lisible) : base de Glucofi elle-même, fichier qui n'est pas une base Glucofi, base abîmée, schéma plus récent (`check_glucofi_db`). Une copie locale est faite avant : `glucofi.db.avant-fusion-AAAAMMJJ-HHMMSS.bak`.
- Règles : mesures réunies par `(device_time, mg_dl)` (un marqueur connu n'est jamais effacé) ; lecteurs réunis ; notes et injections : la modification la plus récente l'emporte, effacement compris (même seconde : départage fixe sur le contenu, pour que les deux bases convergent) ; doses validées réunies par `(effective, matin, soir, règle)` ; versions du protocole réunies par `(effective, protocole)`, la plus récente devient le protocole en cours (même date : départage sur le contenu) ; journal des lectures réuni ; nom du patient repris seulement s'il manque.
- Alertes (`MergeSummary.warnings`) : doses validées des deux côtés depuis la dernière fusion, ou protocole modifié des deux côtés. Les deux historiques sont gardés ; c'est à l'utilisateur de vérifier.
- Limites connues : deux doses validées à la même seconde sur les deux appareils sont ordonnées par leur id local, qui peut différer d'une base à l'autre ; ne se produit pas en pratique (validation manuelle, à la seconde).

Eval : `python3 -m evals.store_merge` (60 scénarios PC/tablette tirés au hasard, oracle tenu à côté : convergence, rien de perdu ni en double, notes, protocole en cours, idempotence, source intacte ; seuil 100 %).

## Migrations

`settings.schema_version` donne la version du schéma ; `Store(...)` migre automatiquement à l'ouverture.

- **v1 -> v2** : l'ancien accuchek calculait `epoch` en heure d'hiver toute l'année. La migration recalcule `epoch` depuis `device_time` (`contracts.local_epoch`, heure d'été comprise) et passe l'unicité de `(epoch, mg_dl)` à `(device_time, mg_dl)`, pour que les mesures d'été relues avec accuchek corrigé ne soient pas importées en double. Une copie de la base est faite avant : `glucofi.db.v1-AAAAMMJJ-HHMMSS.bak`. Le résultat (`Store.last_migration`) est écrit dans le journal.
- **v2 -> v3** : colonnes `readings.meal`, `readings.meter_serial`, détail des imports, table `meters`. Aucune ligne modifiée : les marqueurs des mesures déjà en base arrivent au prochain « Récupérer ». Une base v1 passe par v2 puis v3, avec une seule copie (`glucofi.db.v<version>-AAAAMMJJ-HHMMSS.bak`) ; `Store.migrations` liste les étapes.
- **v3 -> v4** : table `reading_notes` et colonne `dose_changes.excluded` (`'[]'` pour les doses déjà validées). Aucune ligne modifiée. Vérification sur une copie de la vraie base : `python3 -m evals.store_migration` (doses et mesures inchangées).
- **v4 -> v5** : table `protocol_changes`. Le protocole en cours, s'il est valide, devient la première version (date d'effet : la première dose de départ, sinon la date de migration ; note « Protocole saisi avant l'historique »). Vérification : `python3 -m evals.store_migration` (protocole inchangé et présent dans l'historique).
