# services/tablet

Synchronisation USB avec Glucofi pour Android, par adb. Contrat partagé avec l'app : `contracts/tablet_sync.py`.

- `TabletLink.connect()` : trouve adb (`find_adb` : PATH, puis `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `~/Android/Sdk/platform-tools`), choisit l'appareil où Glucofi est installée (`pick_device` : jamais un émulateur, sauf `GLUCOFI_SYNC_EMULATOR=1`, sinon ses données de démonstration se mêleraient aux vraies ; plusieurs tablettes, aucune, non autorisée : message clair), puis vérifie que l'app sait synchroniser et parle la même `SYNC_VERSION` (`hello`).
- `fetch(workdir)` : efface d'abord le dossier d'échange (`reset_sync_dir`, `rm -r -f` de ce seul dossier : un dossier créé par adb appartient à l'uid shell et l'app ne pourrait plus y écrire, vérifié sur Android 16), puis la tablette écrit sa base (`export`), adb la récupère, la copie laissée sur la tablette est effacée (`cleanup`).
- `send(pc_db)` : adb pousse la base du PC, la tablette la fusionne puis l'efface (`merge`, `cleanup`).
- `sync(pc, link, workdir)` : l'aller-retour complet. Après une synchronisation réussie, PC et tablette contiennent la même chose ; une seconde synchronisation ne change rien et ne laisse aucune sauvegarde.
- Toute erreur est une `TabletError` dont le message dit quoi faire (installer android-tools, autoriser le PC sur la tablette, mettre l'app à jour...).

Sécurité : l'app n'accepte que les appels d'adb (uid shell) ou de root ; les bases ne restent sur la tablette, dans le dossier de l'app (`/sdcard/Android/data/fr.librenard.glucofi/files/sync`), que le temps de l'échange. Rien ne passe par le réseau.

Threads : adb bloque (jusqu'à 120 s par commande). La fenêtre fait `connect` et `fetch` sur un thread, la fusion et l'export du PC sur le thread GTK (qui possède la base SQLite), puis `send` sur un thread (`app/window.py`, `sync_tablet`).

Tests : `tests/test_sync.py` avec `tests/fake_tablet.py` (faux adb qui répond comme l'app, sur une vraie base) ; `app/tests/test_window_sync.py` pour la fenêtre. Sur un vrai émulateur : `evals/tablet_sync_emulator.py` du dépôt GlucofiAndroid.
