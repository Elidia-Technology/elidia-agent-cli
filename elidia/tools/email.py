"""Email Skills v1 — SMTP send + IMAP search/read via app-password auth.

Deliberately app-password based, not OAuth2, for this pass — see the
scope note in AIUT-2137 (elidia-agent-cli repo) for why: OAuth2 needs an
app registered in Google/Microsoft's developer console (redirect URIs,
consent screen) which is infra/account setup outside what a code change
alone delivers, and can't be honestly live-verified without it. App
passwords are supported by every major provider for exactly this use
case and are real, immediately usable, and testable today.

Credentials come from auth/keychain.py (store_email_credentials /
get_email_credentials) — never passed as tool arguments, so they can
never end up in a message history or a log line.
"""
from __future__ import annotations

import email.utils
import imaplib
import logging
import smtplib
from email.header import decode_header
from email.mime.text import MIMEText

from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

MAX_SEARCH_RESULTS = 20


def _require_credentials() -> dict:
    from elidia.auth.keychain import get_email_credentials
    creds = get_email_credentials()
    if not creds:
        raise RuntimeError("No email account configured — run 'elidia auth email-login' first")
    return creds


async def _email_send(to: str, subject: str, body: str) -> ToolResult:
    logger.debug(f"Entered into _email_send: to={to}, subject_len={len(subject)}")
    try:
        creds = _require_credentials()
    except RuntimeError as e:
        return ToolResult(content=str(e), is_error=True)

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = creds.get("from_address") or creds["address"]
        msg["To"] = to
        msg["Date"] = email.utils.formatdate(localtime=True)

        with smtplib.SMTP(creds["smtp_host"], creds["smtp_port"], timeout=30) as smtp:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls()
                smtp.ehlo()
            smtp.login(creds["address"], creds["password"])
            smtp.send_message(msg)

        return ToolResult(content=f"Sent to {to}: {subject}")
    except Exception as e:
        return ToolResult(content=f"Send failed: {e}", is_error=True)


async def _email_search(query: str, folder: str = "INBOX", limit: int = MAX_SEARCH_RESULTS) -> ToolResult:
    logger.debug(f"Entered into _email_search: query={query}, folder={folder}, limit={limit}")
    try:
        creds = _require_credentials()
    except RuntimeError as e:
        return ToolResult(content=str(e), is_error=True)

    try:
        with imaplib.IMAP4_SSL(creds["imap_host"], creds["imap_port"]) as imap:
            imap.login(creds["address"], creds["password"])
            imap.select(folder, readonly=True)

            status, data = imap.search(None, "TEXT", f'"{query}"')
            if status != "OK":
                return ToolResult(content=f"IMAP search failed: {status}", is_error=True)

            message_ids = data[0].split()
            if not message_ids:
                return ToolResult(content=f"No messages matching '{query}' in {folder}")

            message_ids = message_ids[-limit:]
            lines = []
            for msg_id in reversed(message_ids):
                status, hdr_data = imap.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                if status != "OK" or not hdr_data or not hdr_data[0]:
                    continue
                raw_headers = hdr_data[0][1].decode("utf-8", errors="replace")
                lines.append(f"[id={msg_id.decode()}]\n{raw_headers.strip()}")

            return ToolResult(
                content="\n\n".join(lines),
                metadata={"folder": folder, "count": len(message_ids)},
            )
    except Exception as e:
        return ToolResult(content=f"Search failed: {e}", is_error=True)


async def _email_read(message_id: str, folder: str = "INBOX") -> ToolResult:
    logger.debug(f"Entered into _email_read: message_id={message_id}, folder={folder}")
    try:
        creds = _require_credentials()
    except RuntimeError as e:
        return ToolResult(content=str(e), is_error=True)

    try:
        with imaplib.IMAP4_SSL(creds["imap_host"], creds["imap_port"]) as imap:
            imap.login(creds["address"], creds["password"])
            imap.select(folder, readonly=True)

            status, msg_data = imap.fetch(message_id.encode(), "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                return ToolResult(content=f"Message not found: {message_id}", is_error=True)

            import email as email_pkg
            raw_email = msg_data[0][1]
            parsed = email_pkg.message_from_bytes(raw_email)

            subject = _decode_header_value(parsed.get("Subject", ""))
            sender = _decode_header_value(parsed.get("From", ""))
            date = parsed.get("Date", "")
            body = _extract_body(parsed)

            content = f"From: {sender}\nSubject: {subject}\nDate: {date}\n\n{body}"
            return ToolResult(content=content, metadata={"message_id": message_id})
    except Exception as e:
        return ToolResult(content=f"Read failed: {e}", is_error=True)


def _decode_header_value(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for text, charset in parts:
        if isinstance(text, bytes):
            decoded.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(text)
    return "".join(decoded)


def _extract_body(parsed) -> str:
    if parsed.is_multipart():
        for part in parsed.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return "[No plain-text body found]"
    payload = parsed.get_payload(decode=True)
    if payload:
        return payload.decode(parsed.get_content_charset() or "utf-8", errors="replace")
    return str(parsed.get_payload())


def register_email_tools(registry: ToolRegistry) -> None:
    logger.debug("Entered into register_email_tools")

    registry.register(ToolDefinition(
        name="email_send", description="Send an email from the configured account",
        parameters={"type": "object", "properties": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject"},
            "body": {"type": "string", "description": "Email body (plain text)"},
        }, "required": ["to", "subject", "body"]},
        handler=_email_send, category="email",
    ))
    registry.register(ToolDefinition(
        name="email_search", description="Search emails in a folder (default INBOX)",
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "Text to search for"},
            "folder": {"type": "string", "description": "IMAP folder name", "default": "INBOX"},
            "limit": {"type": "integer", "description": "Max results", "default": MAX_SEARCH_RESULTS},
        }, "required": ["query"]},
        handler=_email_search, category="email",
    ))
    registry.register(ToolDefinition(
        name="email_read", description="Read the full content of a specific email by message id (from email_search results)",
        parameters={"type": "object", "properties": {
            "message_id": {"type": "string", "description": "Message id from email_search"},
            "folder": {"type": "string", "description": "IMAP folder name", "default": "INBOX"},
        }, "required": ["message_id"]},
        handler=_email_read, category="email",
    ))
