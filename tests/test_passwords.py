"""Argon2id policy and compatibility with the exact previous scrypt encoding."""

import hashlib
from types import SimpleNamespace
import pytest
from argon2 import PasswordHasher, Type, extract_parameters
from shared import passwords

PASSWORD = "Local-test-password-7"


def legacy_hash(password=PASSWORD):
    salt = "01" * 16
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1)
    return salt + ":" + digest.hex()


def test_new_passwords_use_salted_argon2id():
    one = passwords.password_hash(PASSWORD)
    two = passwords.password_hash(PASSWORD)
    assert one != two and one.startswith("$argon2id$v=19$")
    params = extract_parameters(one)
    assert params.type == Type.ID
    assert (params.memory_cost, params.time_cost, params.parallelism) == (65536, 3, 1)
    assert passwords.password_matches(PASSWORD, one)
    assert not passwords.password_matches("wrong", one)
    assert not passwords.password_needs_rehash(one)


def test_legacy_scrypt_verifies_without_generating_new_scrypt():
    encoded = legacy_hash()
    assert passwords.password_matches(PASSWORD, encoded)
    assert not passwords.password_matches("wrong", encoded)
    assert passwords.password_needs_rehash(encoded)


def test_outdated_argon2id_parameters_need_rehash():
    encoded = PasswordHasher(
        time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID
    ).hash(PASSWORD)
    assert passwords.password_matches(PASSWORD, encoded)
    assert passwords.password_needs_rehash(encoded)


@pytest.mark.parametrize(
    "encoded", [None, "", "malformed", "abc:def", "$argon2id$bad", "$argon2i$v=19$bad"]
)
def test_malformed_or_unknown_hashes_fail_closed(encoded):
    assert not passwords.password_matches(PASSWORD, encoded)
    assert not passwords.password_needs_rehash(encoded)


def test_untrusted_parameters_cannot_allocate_excessive_memory(monkeypatch):
    encoded = passwords.password_hash(PASSWORD).replace("m=65536", "m=1073741824")
    seen = []
    monkeypatch.setattr(
        passwords,
        "_HASHER",
        SimpleNamespace(verify=lambda value, password: seen.append(value) or True),
    )
    assert not passwords.password_matches(PASSWORD, encoded)
    assert seen == [passwords._DUMMY]
