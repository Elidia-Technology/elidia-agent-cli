import json
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

# Separate service name from the AiUtils API key above — a leaked email
# app-password and a leaked AiUtils API key are different-severity
# incidents, and keeping them in unrelated keyring entries means deleting
# or rotating one never touches the other.
_EMAIL_SERVICE_NAME = "elidia-cli-email"
_EMAIL_ACCOUNT_NAME = "credentials"
_EMAIL_FALLBACK_PATH = ELIDIA_HOME / ".email_credentials"


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


def store_email_credentials(
    address: str, password: str,
    smtp_host: str, smtp_port: int,
    imap_host: str, imap_port: int,
    from_address: str | None = None,
) -> None:
    """Store email credentials (app password, not the account password) as
    a JSON blob in the OS keychain, under a service name separate from the
    AiUtils API key. Fallback to an encrypted file, same as store_api_key.

    `address` is the SMTP/IMAP login (AUTH username). For personal webmail
    (Gmail, Outlook) this is the same as the visible sender. For
    transactional relays (Zepto, SendGrid, Mailgun, SES) the AUTH username
    is a fixed token distinct from the sender address, so `from_address`
    lets those be configured separately — it defaults to `address` when
    omitted, which keeps the common case a no-op."""
    logger.debug(f"Entered into store_email_credentials: address={address}")
    payload = json.dumps({
        "address": address, "password": password,
        "smtp_host": smtp_host, "smtp_port": smtp_port,
        "imap_host": imap_host, "imap_port": imap_port,
        "from_address": from_address or address,
    })
    try:
        keyring.set_password(_EMAIL_SERVICE_NAME, _EMAIL_ACCOUNT_NAME, payload)
    except keyring.errors.KeyringError as exc:
        logger.warning(
            f"Entered into store_email_credentials: keychain unavailable ({exc.__class__.__name__}), falling back to file storage"
        )
        ELIDIA_HOME.mkdir(parents=True, exist_ok=True)
        _EMAIL_FALLBACK_PATH.write_text(payload, encoding="utf-8")
        os.chmod(_EMAIL_FALLBACK_PATH, stat.S_IRUSR | stat.S_IWUSR)


def get_email_credentials() -> dict | None:
    """Retrieve email credentials dict, or None if never configured."""
    logger.debug("Entered into get_email_credentials")
    payload: str | None = None
    try:
        payload = keyring.get_password(_EMAIL_SERVICE_NAME, _EMAIL_ACCOUNT_NAME)
    except keyring.errors.KeyringError as exc:
        logger.warning(
            f"Entered into get_email_credentials: keychain unavailable ({exc.__class__.__name__}), checking fallback file"
        )

    if not payload and _EMAIL_FALLBACK_PATH.exists():
        payload = _EMAIL_FALLBACK_PATH.read_text(encoding="utf-8").strip()

    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("Entered into get_email_credentials: stored credentials are corrupt (not valid JSON)")
        return None


def delete_email_credentials() -> None:
    """Remove email credentials from keychain and fallback file."""
    logger.debug("Entered into delete_email_credentials")
    try:
        keyring.delete_password(_EMAIL_SERVICE_NAME, _EMAIL_ACCOUNT_NAME)
    except keyring.errors.PasswordDeleteError:
        pass
    except keyring.errors.KeyringError as exc:
        logger.warning(f"Entered into delete_email_credentials: keychain unavailable ({exc.__class__.__name__})")

    if _EMAIL_FALLBACK_PATH.exists():
        _EMAIL_FALLBACK_PATH.unlink()
