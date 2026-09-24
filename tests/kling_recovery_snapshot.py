"""Proof-only encrypted recovery evidence, never another operational authority.

The existing protected FAL_KEY derives an encryption key; credentials are never
serialized. Restoring requires that same secret and an empty disposable test DB.
"""
import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

MAGIC = b"BEN-KLING-RECOVERY-1\n"


def encryption_key(credential, salt):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=MAGIC).derive(credential.encode())


def seal(path, payload, credential):
    data = json.dumps(payload, default=str, allow_nan=False).encode()
    assert credential and credential.encode() not in data, "Credential must never enter snapshot"
    salt, nonce = os.urandom(16), os.urandom(12)
    encrypted = MAGIC + salt + nonce + AESGCM(encryption_key(credential, salt)).encrypt(nonce, data, MAGIC)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        handle.write(encrypted)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def unseal(path, credential):
    raw = path.read_bytes()
    assert raw.startswith(MAGIC)
    payload = raw[len(MAGIC):]
    salt, nonce, encrypted = payload[:16], payload[16:28], payload[28:]
    return json.loads(AESGCM(encryption_key(credential, salt)).decrypt(nonce, encrypted, MAGIC))
