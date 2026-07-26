"""Tests for elidia.tools.email — SMTP send + IMAP search/read.

Send is tested against a real local SMTP server (aiosmtpd) — proves a
sent message actually arrives with correct headers/body/auth, not just
that smtplib was called correctly. IMAP is mocked: a real local IMAP
test server is much heavier to stand up than SMTP for what it buys here,
per the ticket's own scoping (AIUT-2137).
"""
from unittest.mock import MagicMock, patch

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword

from elidia.permissions.manager import ACTION_TIERS, NEVER_PROMOTE, PermissionTier
from elidia.tools import ToolRegistry, create_default_registry
from elidia.tools.email import (
    _email_read,
    _email_search,
    _email_send,
    register_email_tools,
)

TEST_USER = "test@example.com"
TEST_PASSWORD = "app-password-123"


class _CapturingHandler:
    def __init__(self):
        self.envelopes = []

    async def handle_DATA(self, server, session, envelope):
        self.envelopes.append(envelope)
        return "250 Message accepted"


def _authenticator(server, session, envelope, mechanism, login_password: LoginPassword) -> AuthResult:
    ok = (
        login_password.login.decode() == TEST_USER
        and login_password.password.decode() == TEST_PASSWORD
    )
    return AuthResult(success=ok)


@pytest.fixture
def smtp_server():
    handler = _CapturingHandler()
    controller = Controller(
        handler,
        hostname="127.0.0.1",
        port=10250,
        authenticator=_authenticator,
        auth_require_tls=False,
        auth_required=True,
    )
    controller.start()
    yield controller, handler
    controller.stop()


def _mock_creds(smtp_host: str, smtp_port: int):
    return {
        "address": TEST_USER, "password": TEST_PASSWORD,
        "smtp_host": smtp_host, "smtp_port": smtp_port,
        "imap_host": "imap.example.com", "imap_port": 993,
    }


class TestRegistration:
    def test_registers_three_tools(self):
        registry = ToolRegistry()
        register_email_tools(registry)
        names = {t.name for t in registry.list_tools()}
        assert names == {"email_send", "email_search", "email_read"}

    def test_wired_into_default_registry(self):
        registry = create_default_registry()
        assert registry.get("email_send") is not None


class TestPermissionTiering:
    def test_email_send_is_every_time(self):
        assert ACTION_TIERS["email_send"] == PermissionTier.EVERY_TIME

    def test_email_send_never_promotes(self):
        assert "email_send" in NEVER_PROMOTE

    def test_email_read_is_session_not_every_time(self):
        assert ACTION_TIERS["email_read"] == PermissionTier.SESSION


class TestEmailSendNoCredentials:
    @pytest.mark.asyncio
    async def test_send_without_credentials_is_error(self):
        with patch("elidia.auth.keychain.get_email_credentials", return_value=None):
            result = await _email_send("someone@example.com", "Hi", "Body")
        assert result.is_error
        assert "email-login" in result.content


class TestEmailSendLive:
    @pytest.mark.asyncio
    async def test_send_reaches_real_local_server(self, smtp_server):
        controller, handler = smtp_server
        creds = _mock_creds(controller.hostname, controller.port)

        with patch("elidia.auth.keychain.get_email_credentials", return_value=creds):
            result = await _email_send("recipient@example.com", "Test Subject", "Test body content.")

        assert not result.is_error, result.content
        assert len(handler.envelopes) == 1
        envelope = handler.envelopes[0]
        assert envelope.mail_from == TEST_USER
        assert envelope.rcpt_tos == ["recipient@example.com"]
        raw = envelope.content.decode("utf-8", errors="replace")
        assert "Test Subject" in raw
        assert "Test body content." in raw

    @pytest.mark.asyncio
    async def test_send_uses_from_address_when_login_is_a_relay_token(self, smtp_server):
        """Transactional relays (Zepto, SendGrid, Mailgun) authenticate with a
        fixed API-key-style username distinct from the visible sender address.
        The SMTP AUTH login must stay the token, but the From header must use
        from_address, not the login token itself."""
        controller, handler = smtp_server
        # `address` stays the real login the fake server accepts (TEST_USER) —
        # only from_address diverges, mirroring a relay where the AUTH token
        # itself would never be a valid login on a real server either.
        creds = _mock_creds(controller.hostname, controller.port)
        creds["from_address"] = "no-reply@aiutils.io"

        with patch("elidia.auth.keychain.get_email_credentials", return_value=creds):
            result = await _email_send("recipient@example.com", "Relay Test", "Body")

        assert not result.is_error, result.content
        envelope = handler.envelopes[0]
        raw = envelope.content.decode("utf-8", errors="replace")
        assert "From: no-reply@aiutils.io" in raw
        assert f"From: {TEST_USER}" not in raw

    @pytest.mark.asyncio
    async def test_send_wrong_password_is_rejected(self, smtp_server):
        controller, handler = smtp_server
        creds = _mock_creds(controller.hostname, controller.port)
        creds["password"] = "wrong-password"

        with patch("elidia.auth.keychain.get_email_credentials", return_value=creds):
            result = await _email_send("recipient@example.com", "Test", "Body")

        assert result.is_error
        assert len(handler.envelopes) == 0


class TestEmailSearchAndRead:
    @pytest.mark.asyncio
    async def test_search_no_credentials_is_error(self):
        with patch("elidia.auth.keychain.get_email_credentials", return_value=None):
            result = await _email_search("invoice")
        assert result.is_error

    @pytest.mark.asyncio
    async def test_search_returns_matching_headers(self):
        creds = _mock_creds("imap.example.com", 993)
        mock_imap = MagicMock()
        mock_imap.search.return_value = ("OK", [b"1 2"])
        mock_imap.fetch.side_effect = [
            ("OK", [(b"1", b"From: a@example.com\r\nSubject: Invoice #1\r\n")]),
            ("OK", [(b"2", b"From: b@example.com\r\nSubject: Invoice #2\r\n")]),
        ]
        mock_imap.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap.__exit__ = MagicMock(return_value=False)

        with patch("elidia.auth.keychain.get_email_credentials", return_value=creds), \
             patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = await _email_search("Invoice")

        assert not result.is_error
        assert "Invoice #1" in result.content
        assert "Invoice #2" in result.content
        mock_imap.login.assert_called_once_with(TEST_USER, TEST_PASSWORD)

    @pytest.mark.asyncio
    async def test_search_no_matches(self):
        creds = _mock_creds("imap.example.com", 993)
        mock_imap = MagicMock()
        mock_imap.search.return_value = ("OK", [b""])
        mock_imap.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap.__exit__ = MagicMock(return_value=False)

        with patch("elidia.auth.keychain.get_email_credentials", return_value=creds), \
             patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = await _email_search("nonexistent")

        assert not result.is_error
        assert "No messages" in result.content

    @pytest.mark.asyncio
    async def test_read_returns_full_message(self):
        creds = _mock_creds("imap.example.com", 993)
        raw_message = (
            b"From: sender@example.com\r\n"
            b"Subject: Meeting notes\r\n"
            b"Date: Mon, 1 Jan 2026 00:00:00 +0000\r\n"
            b"Content-Type: text/plain\r\n\r\n"
            b"Let's meet at 3pm.\r\n"
        )
        mock_imap = MagicMock()
        mock_imap.fetch.return_value = ("OK", [(b"1", raw_message)])
        mock_imap.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap.__exit__ = MagicMock(return_value=False)

        with patch("elidia.auth.keychain.get_email_credentials", return_value=creds), \
             patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = await _email_read("1")

        assert not result.is_error
        assert "sender@example.com" in result.content
        assert "Meeting notes" in result.content
        assert "Let's meet at 3pm." in result.content

    @pytest.mark.asyncio
    async def test_read_missing_message(self):
        creds = _mock_creds("imap.example.com", 993)
        mock_imap = MagicMock()
        mock_imap.fetch.return_value = ("OK", [None])
        mock_imap.__enter__ = MagicMock(return_value=mock_imap)
        mock_imap.__exit__ = MagicMock(return_value=False)

        with patch("elidia.auth.keychain.get_email_credentials", return_value=creds), \
             patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = await _email_read("999")

        assert result.is_error
