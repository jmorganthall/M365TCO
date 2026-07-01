"""Encrypted-at-rest local secret store (PRD 4.4 / 8.2).

No secrets in config files. For self-hosted Unraid, an encrypted-at-rest local
store keyed by an operator-supplied master secret is acceptable for v1. The
OpenRouter API key lives here. (Price-sheet sync uses interactive login and
stores no token — see app/pricesync/.)

Azure Key Vault is the documented alternative; this module exposes a small
get/set interface so a Key Vault backend can be dropped in without touching
callers (swap _LocalStore for an AzureKeyVaultStore).

Encryption: Fernet (AES-128-CBC + HMAC) with a key derived from the master
secret via PBKDF2-HMAC-SHA256. The store is unreadable without the master
secret. If no master secret is configured, the store degrades to empty/read-only
and dependent features (AI assist) report themselves disabled.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from ..config import settings

# Fixed application salt. The confidentiality comes from the operator master
# secret, not the salt; a fixed salt keeps the derived key stable across restarts.
_SALT = b"m365-tco-secret-store-v1"


class SecretStore:
    def __init__(self, data_dir: str, master_secret: Optional[str]):
        self._path = os.path.join(data_dir, "secrets.enc")
        self._master = master_secret
        self._fernet: Optional[Fernet] = None
        if master_secret:
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(), length=32, salt=_SALT, iterations=480_000
            )
            key = base64.urlsafe_b64encode(kdf.derive(master_secret.encode()))
            self._fernet = Fernet(key)

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def _read_all(self) -> dict:
        if not self.enabled or not os.path.exists(self._path):
            return {}
        with open(self._path, "rb") as fh:
            blob = fh.read()
        if not blob:
            return {}
        try:
            return json.loads(self._fernet.decrypt(blob).decode())
        except (InvalidToken, ValueError) as exc:
            raise RuntimeError(
                "Secret store could not be decrypted — wrong master secret?"
            ) from exc

    def _write_all(self, data: dict) -> None:
        if not self.enabled:
            raise RuntimeError("Secret store disabled: no master secret configured.")
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        token = self._fernet.encrypt(json.dumps(data).encode())
        # Write atomically.
        tmp = self._path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(token)
        os.replace(tmp, self._path)

    def get(self, key: str) -> Optional[str]:
        return self._read_all().get(key)

    def set(self, key: str, value: str) -> None:
        data = self._read_all()
        data[key] = value
        self._write_all(data)

    def delete(self, key: str) -> None:
        data = self._read_all()
        data.pop(key, None)
        self._write_all(data)

    def keys(self) -> list[str]:
        return sorted(self._read_all().keys())


# Well-known secret keys.
OPENROUTER_API_KEY = "openrouter_api_key"


_store: Optional[SecretStore] = None


def get_store() -> SecretStore:
    global _store
    if _store is None:
        _store = SecretStore(settings.data_dir, settings.master_secret)
    return _store
