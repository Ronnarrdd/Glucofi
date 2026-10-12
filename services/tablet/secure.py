"""Chiffrement des échanges Wi-Fi avec la tablette (contracts/tablet_sync.py) : code de couplage, clé, trames."""

from __future__ import annotations

import base64
import json
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from contracts.tablet_sync import NET_INFO, NET_NONCE_BYTES, NET_SALT, NET_SECRET_BYTES


class BadMessage(Exception):
    """Message illisible : mauvaise clé, défi différent, ou octets abîmés (on ne distingue pas, volontairement)."""


def new_secret() -> bytes:
    return os.urandom(NET_SECRET_BYTES)


def format_code(secret: bytes) -> str:
    """« ABCD-EFGH-IJKL-MNOP » : base 32 (sans 0, 1, 8, 9), par groupes de quatre pour la saisie."""
    text = base64.b32encode(secret).decode("ascii").rstrip("=")
    return "-".join(text[i : i + 4] for i in range(0, len(text), 4))


def parse_code(text: str) -> bytes:
    """Le secret d'un code saisi à la main (casse, tirets et espaces ignorés) ; ValueError s'il est faux."""
    clean = "".join(c for c in text.upper() if c.isalnum())
    try:
        secret = base64.b32decode(clean + "=" * (-len(clean) % 8))
    except ValueError as exc:
        raise ValueError("Code de couplage invalide : il contient 16 lettres et chiffres, sans 0, 1, 8 ni 9.") from exc
    if len(secret) != NET_SECRET_BYTES:
        raise ValueError("Code de couplage incomplet : il contient 16 lettres et chiffres.")
    return secret


def derive_key(secret: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=NET_SALT, info=NET_INFO).derive(secret)


def _aad(challenge: bytes, method: str, direction: str) -> bytes:
    return challenge + b"|" + method.encode("ascii") + b"|" + direction.encode("ascii")


def seal(key: bytes, challenge: bytes, method: str, direction: str, frame: bytes, nonce: bytes | None = None) -> bytes:
    nonce = nonce or os.urandom(NET_NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, frame, _aad(challenge, method, direction))


def unseal(key: bytes, challenge: bytes, method: str, direction: str, body: bytes) -> bytes:
    if len(body) <= NET_NONCE_BYTES:
        raise BadMessage("trop court")
    try:
        return AESGCM(key).decrypt(body[:NET_NONCE_BYTES], body[NET_NONCE_BYTES:], _aad(challenge, method, direction))
    except InvalidTag as exc:
        raise BadMessage("authentification refusée") from exc


def pack(header: dict, blob: bytes = b"") -> bytes:
    text = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(text)) + text + blob


def unpack(frame: bytes) -> tuple[dict, bytes]:
    if len(frame) < 4:
        raise BadMessage("trame trop courte")
    (size,) = struct.unpack(">I", frame[:4])
    if size > len(frame) - 4:
        raise BadMessage("trame tronquée")
    try:
        header = json.loads(frame[4 : 4 + size].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadMessage("en-tête illisible") from exc
    if not isinstance(header, dict):
        raise BadMessage("en-tête illisible")
    return header, frame[4 + size :]
