"""Tests du chiffrement des identifiants."""
import pytest
from cryptography.fernet import InvalidToken

from custom_components.nissan_connect_ca.crypto import _decrypt, _encrypt


def test_roundtrip(tmp_path):
    key = str(tmp_path / "test.key")
    token = _encrypt(key, "s3cret")
    assert token != "s3cret"
    assert _decrypt(key, token) == "s3cret"


def test_key_file_permissions(tmp_path):
    key = tmp_path / "test.key"
    _encrypt(str(key), "x")
    assert key.stat().st_mode & 0o777 == 0o600


def test_wrong_key_fails(tmp_path):
    token = _encrypt(str(tmp_path / "a.key"), "s3cret")
    with pytest.raises(InvalidToken):
        _decrypt(str(tmp_path / "b.key"), token)
