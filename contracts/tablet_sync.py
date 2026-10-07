"""Synchronisation USB avec l'app Android (adb) : ce que le PC et la tablette doivent dire pareil.

Le PC appelle le fournisseur de contenu de l'app par `adb shell content call --uri content://AUTHORITY
--method <méthode>` ; l'app refuse tout appelant autre qu'adb (uid shell) ou root. La réponse est un
Bundle à une seule clé, REPLY_KEY : le JSON de la réponse, encodé en base64 (aucun souci de
guillemets, d'accents ou de retours à la ligne dans la sortie d'adb).

Réponse (toutes les méthodes) : {"sync": SYNC_VERSION, "ok": bool, "message": str, "warnings": [str],
"changed": bool, "app": "<version de l'app>"}.

Méthodes :
- hello   : rien ne change ; sert à vérifier que l'app sait synchroniser et parle la même version.
- export  : la tablette écrit sa base dans REMOTE_DIR/TABLET_FILE.
- merge   : la tablette fusionne REMOTE_DIR/PC_FILE (posé par adb push) puis l'efface, réussite ou non.
- cleanup : la tablette efface REMOTE_DIR/TABLET_FILE et REMOTE_DIR/PC_FILE.

Le dossier est celui des fichiers externes de l'app (Context.getExternalFilesDir(null)/SYNC_DIR) :
adb y lit et y écrit, les autres apps non (Android 11 et plus). Les fichiers n'y restent que le temps
de la synchronisation.

Changement incompatible (méthode, fichier, champ) : SYNC_VERSION + 1, des deux côtés.
"""

from __future__ import annotations

SYNC_VERSION = 1
PACKAGE = "fr.librenard.glucofi"
AUTHORITY = f"{PACKAGE}.sync"
REPLY_KEY = "reply"
SYNC_DIR = "sync"
REMOTE_DIR = f"/sdcard/Android/data/{PACKAGE}/files/{SYNC_DIR}"  # effacé par le PC avant chaque échange (rm -r -f)
TABLET_FILE = "glucofi-tablette.db"
PC_FILE = "glucofi-pc.db"
METHODS = ("hello", "export", "merge", "cleanup")
