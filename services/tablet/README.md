# services/tablet

Synchronisation avec Glucofi pour Android : par le Wi-Fi (`net.py`, `secure.py`) ou en USB par adb (`adb.py`, `sync.py`). Contrat partagé avec l'app : `contracts/tablet_sync.py`.

- `TabletLink.connect()` : trouve adb (`find_adb` : PATH, puis `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `~/Android/Sdk/platform-tools`), choisit l'appareil où Glucofi est installée (`pick_device` : jamais un émulateur, sauf `GLUCOFI_SYNC_EMULATOR=1`, sinon ses données de démonstration se mêleraient aux vraies ; plusieurs tablettes, aucune, non autorisée : message clair), puis vérifie que l'app sait synchroniser et parle la même `SYNC_VERSION` (`hello`).
- `fetch(workdir)` : efface d'abord le dossier d'échange (`reset_sync_dir`, `rm -r -f` de ce seul dossier : un dossier créé par adb appartient à l'uid shell et l'app ne pourrait plus y écrire, vérifié sur Android 16), puis la tablette écrit sa base (`export`), adb la récupère, la copie laissée sur la tablette est effacée (`cleanup`).
- `send(pc_db)` : adb pousse la base du PC, la tablette la fusionne puis l'efface (`merge`, `cleanup`).
- `sync(pc, link, workdir)` : l'aller-retour complet. Après une synchronisation réussie, PC et tablette contiennent la même chose ; une seconde synchronisation ne change rien et ne laisse aucune sauvegarde.
- Toute erreur est une `TabletError` dont le message dit quoi faire (installer android-tools, autoriser le PC sur la tablette, mettre l'app à jour...).

Sécurité : l'app n'accepte que les appels d'adb (uid shell) ou de root ; les bases ne restent sur la tablette, dans le dossier de l'app (`/sdcard/Android/data/fr.librenard.glucofi/files/sync`), que le temps de l'échange. Rien ne passe par le réseau.

Threads : adb bloque (jusqu'à 120 s par commande). La fenêtre fait `connect` et `fetch` sur un thread, la fusion et l'export du PC sur le thread GTK (qui possède la base SQLite), puis `send` sur un thread (`app/window.py`, `sync_tablet`).

Tests : `tests/test_sync.py` avec `tests/fake_tablet.py` (faux adb qui répond comme l'app, sur une vraie base) ; `app/tests/test_window_sync.py` pour la fenêtre. Sur un vrai émulateur : `evals/tablet_sync_emulator.py` du dépôt GlucofiAndroid.

## Wi-Fi (`net.py`, `secure.py`)

- `pair(dir, code, address)` : couple le PC à la tablette qui affiche le code (16 lettres et chiffres, 80 bits). L'adresse saisie, sinon les tablettes annoncées en mDNS (`discover`, `avahi-browse`, facultatif). Rien n'est gardé tant qu'un `hello` chiffré n'a pas réussi. Le couplage (secret, dernière adresse) est dans `tablet-wifi.json`, mode 0600, à côté de la base mais jamais dedans : la base s'exporte et se fusionne.
- `NetLink` a la même forme que `TabletLink` (`device`, `fetch`, `send`) : `sync()` et la fenêtre ne voient pas la différence. `NetLink.connect` essaie l'adresse gardée, puis les tablettes annoncées ; la clé dit laquelle est la bonne.
- `connect_link(dir, usb)` : Wi-Fi si un couplage existe, câble en secours ; si les deux échouent, le message dit pourquoi pour chacun. Sans couplage, une erreur USB rappelle le menu de couplage.
- `secure.py` : HKDF-SHA256 du secret, AES-256-GCM, défi à usage unique de 16 octets lié à la méthode et au sens (requête ou réponse) dans les données authentifiées : un message rejoué, détourné ou retourné est refusé. Mauvaise clé : 403 sans détail ; 5 refus d'affilée : la tablette se tait une minute (429).
- Dépendance : `python3-cryptography`, importée seulement par ce chemin (le câble n'en a pas besoin).

Limites : la tablette n'écoute que Glucofi ouverte (pas de service en arrière-plan) ; le réseau local doit laisser passer le port 47831 entre appareils (certains Wi-Fi invités isolent les appareils) ; les échanges sont chiffrés et authentifiés mais pas anonymes : qui est sur le réseau voit qu'un échange Glucofi a lieu, sans en lire le contenu.

Tests : `tests/test_net.py` avec `tests/fake_net_tablet.py` (vrai serveur HTTP en boucle locale sur une vraie base, même protocole que l'app) ; `contracts/tablet_net_vector.json` est relu par le test Kotlin `NetCryptoTest`. Sur un vrai émulateur : `evals/tablet_net_emulator.py` du dépôt GlucofiAndroid.
