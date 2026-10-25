"""Argon2id password storage with verification-only legacy scrypt support."""

import hashlib
import hmac
import re
import secrets
from threading import BoundedSemaphore

from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError

# Explicit parameters avoid silent policy changes between library releases.
# Two simultaneous operations use at most 128 MiB for password hashing.
_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
_SLOTS = BoundedSemaphore(2)
_LEGACY = re.compile(r"([0-9a-f]{32}):([0-9a-f]{128})\Z")
_DUMMY = _HASHER.hash(secrets.token_urlsafe(32))


def password_hash(password):
    with _SLOTS:
        return _HASHER.hash(password)


def _safe_parameters(encoded):
    try:
        params = extract_parameters(encoded)
        return (
            params.type == Type.ID
            and 8 <= params.memory_cost <= 65536
            and 1 <= params.time_cost <= 10
            and 1 <= params.parallelism <= 4
            and 8 <= params.salt_len <= 64
            and 16 <= params.hash_len <= 64
        )
    except (InvalidHashError, ValueError, TypeError):
        return False


def password_matches(password, encoded):
    # Stored parameters are also bounded so a malformed hash cannot request
    # unbounded memory or threads. Unknown accounts still pay one Argon2 check.
    if not isinstance(password, str):
        return False
    legacy = (
        _LEGACY.fullmatch(encoded)
        if isinstance(encoded, str) and len(encoded) <= 512
        else None
    )
    with _SLOTS:
        if legacy:
            digest = hashlib.scrypt(
                password.encode(), salt=legacy[1].encode(), n=16384, r=8, p=1
            )
            return hmac.compare_digest(digest, bytes.fromhex(legacy[2]))
        valid_encoding = (
            isinstance(encoded, str)
            and len(encoded) <= 512
            and encoded.startswith("$argon2id$")
            and _safe_parameters(encoded)
        )
        try:
            valid = _HASHER.verify(encoded if valid_encoding else _DUMMY, password)
            return valid and valid_encoding
        except (VerificationError, InvalidHashError, ValueError, TypeError):
            return False


def password_needs_rehash(encoded):
    if not isinstance(encoded, str):
        return False
    if _LEGACY.fullmatch(encoded):
        return True
    return _safe_parameters(encoded) and _HASHER.check_needs_rehash(encoded)
