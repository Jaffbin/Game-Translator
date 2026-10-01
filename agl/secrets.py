from __future__ import annotations

import os
from typing import Optional

SERVICE_NAME = "AutoGameLocalizer"

try:
    import keyring
except ImportError:  # Source checkouts can still use the legacy .env fallback.
    keyring = None


def get_secret(name: str) -> Optional[str]:
    if not name:
        return None
    current = os.getenv(name)
    if current:
        return current
    if keyring is None:
        return None
    try:
        value = keyring.get_password(SERVICE_NAME, name)
    except Exception:
        return None
    if value:
        os.environ[name] = value
    return value


def set_secret(name: str, value: str) -> bool:
    """Store a secret in the OS credential vault when available.

    Returns ``True`` when persisted in the credential vault.  The process
    environment is always updated so the new value is immediately usable.
    """
    os.environ[name] = value
    if keyring is None:
        return False
    try:
        keyring.set_password(SERVICE_NAME, name, value)
    except Exception:
        return False
    return True
