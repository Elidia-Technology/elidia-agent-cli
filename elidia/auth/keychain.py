import logging
import os
import stat

import keyring
import keyring.errors

from elidia.config.settings import ELIDIA_HOME

logger = logging.getLogger(__name__)

SERVICE_NAME = "elidia-cli"
ACCOUNT_NAME = "api_key"

_FALLBACK_KEY_PATH = ELIDIA_HOME / ".api_key"


def store_api_key(key: str) -> None:
    """Store API key in OS keychain. Fallback to encrypted file."""
    logger.debug("Entered into store_api_key: storing API key")
    try:
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, key)
    except keyring.errors.KeyringError as exc:
        logger.warning(
            f"Entered into store_api_key: keychain unavailable ({exc.__class__.__name__}), falling back to file storage"
        )
        _store_api_key_fallback(key)


def _store_api_key_fallback(key: str) -> None:
    logger.debug("Entered into _store_api_key_fallback: writing API key to fallback file")
    ELIDIA_HOME.mkdir(parents=True, exist_ok=True)
    _FALLBACK_KEY_PATH.write_text(key, encoding="utf-8")
    os.chmod(_FALLBACK_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)


def get_api_key() -> str | None:
    """Retrieve API key from OS keychain, then fallback file, then env, else None."""
    logger.debug("Entered into get_api_key: retrieving API key")
    try:
        value = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
        if value:
            return value
    except keyring.errors.KeyringError as exc:
        logger.warning(
            f"Entered into get_api_key: keychain unavailable ({exc.__class__.__name__}), checking fallback file"
        )

    if _FALLBACK_KEY_PATH.exists():
        value = _FALLBACK_KEY_PATH.read_text(encoding="utf-8").strip()
        if value:
            return value

    env_value = os.environ.get("AIUTILS_API_KEY")
    if env_value:
        return env_value

    return None


def delete_api_key() -> None:
    """Remove API key from keychain and fallback file."""
    logger.debug("Entered into delete_api_key: removing API key")
    try:
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except keyring.errors.PasswordDeleteError:
        pass
    except keyring.errors.KeyringError as exc:
        logger.warning(f"Entered into delete_api_key: keychain unavailable ({exc.__class__.__name__})")

    if _FALLBACK_KEY_PATH.exists():
        _FALLBACK_KEY_PATH.unlink()


def validate_api_key(key: str) -> bool:
    """Check key format: must start with 'ak-dev-'."""
    logger.debug("Entered into validate_api_key: checking key format")
    return key.startswith("ak-dev-") and len(key) > 10


def mask_api_key(key: str) -> str:
    """Return masked version: ak-dev-****7f2a"""
    logger.debug("Entered into mask_api_key: masking key for display")
    if len(key) <= 12:
        return "ak-dev-****"
    return f"{key[:7]}****{key[-4:]}"
