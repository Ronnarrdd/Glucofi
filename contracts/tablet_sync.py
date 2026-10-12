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

Wi-Fi (même fusion, même SyncReply, sans adb) : la tablette écoute sur NET_PORT tant que Glucofi est
ouverte et qu'un PC est couplé. Le couplage est un secret de NET_SECRET_BYTES octets tiré par la tablette
et affiché une fois en base 32 (« ABCD-EFGH-IJKL-MNOP ») ; le PC le saisit et le garde dans un fichier
privé. Rien n'est en clair sur le réseau : tout est chiffré en AES-256-GCM avec
HKDF-SHA256(secret, NET_SALT, NET_INFO).

- GET NET_CHALLENGE_PATH : 16 octets aléatoires en hexadécimal, valables NET_CHALLENGE_TTL_S, une seule fois.
- POST NET_CALL_PATH + méthode (NET_METHODS), en-tête NET_CHALLENGE_HEADER = le défi. Corps = nonce (12
  octets) puis AES-GCM(trame), données authentifiées = défi + "|" + méthode + "|req". La réponse a la même
  forme, données authentifiées "...|rep". Pas de clé valide : 403 sans corps ; trop d'essais : 429.
- Trame = longueur du JSON sur 4 octets (gros-boutiste), le JSON, puis un fichier éventuel (la base).
  export : réponse = SyncReply + base de la tablette ; merge : requête = {} + base du PC, réponse = SyncReply.
- La tablette annonce `_glucofi._tcp` (mDNS) ; le PC essaie d'abord l'adresse gardée au couplage.

contracts/tablet_net_vector.json : un échange complet chiffré, relu par les deux côtés (tests).
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

NET_PORT = 47831
NET_SERVICE = "_glucofi._tcp"
NET_METHODS = ("hello", "export", "merge")
NET_CHALLENGE_PATH = "/v1/challenge"
NET_CALL_PATH = "/v1/call/"
NET_CHALLENGE_HEADER = "X-Glucofi-Challenge"
NET_CHALLENGE_BYTES = 16
NET_CHALLENGE_TTL_S = 60
NET_SECRET_BYTES = 10
NET_NONCE_BYTES = 12
NET_SALT = b"glucofi-sync"
NET_INFO = b"net-v1"
NET_MAX_BODY = 64 * 1024 * 1024
NET_MAX_FAILURES = 5  # essais refusés d'affilée avant NET_LOCK_S de silence
NET_LOCK_S = 60
