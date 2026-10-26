"""Chiffrement des identifiants stockés par l'intégration."""
import os

from cryptography.fernet import Fernet

KEY_FILE = ".storage/nissan_connect_ca.key"


def _load_key(path):
    if os.path.exists(path):
        with open(path, "rb") as handle:
            return handle.read()
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def _encrypt(path, text):
    return Fernet(_load_key(path)).encrypt(text.encode()).decode()


def _decrypt(path, token):
    return Fernet(_load_key(path)).decrypt(token.encode()).decode()


async def async_encrypt(hass, text):
    """Chiffre un texte avec la clé locale (créée au besoin)."""
    path = hass.config.path(KEY_FILE)
    return await hass.async_add_executor_job(_encrypt, path, text)


async def async_decrypt(hass, token):
    """Déchiffre un texte; lève une exception si la clé ou le jeton est invalide."""
    path = hass.config.path(KEY_FILE)
    return await hass.async_add_executor_job(_decrypt, path, token)
