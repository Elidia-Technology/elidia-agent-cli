"""Tests for elidia.permissions.manager — 4-tier permission system, focused on
the NEVER_PROMOTE exemption added for Database/Email skills."""
from pathlib import Path

import pytest

from elidia.config.settings import PermissionConfig
from elidia.permissions.audit import AuditLogger
from elidia.permissions.manager import ACTION_TIERS, NEVER_PROMOTE, PermissionManager, PermissionTier
from elidia.permissions.trust import TrustEngine


@pytest.fixture
def manager(tmp_dir: Path):
    audit = AuditLogger(path=tmp_dir / "audit.jsonl")
    audit.open()
    config = PermissionConfig(progressive_trust=True, trust_threshold=3)
    trust = TrustEngine(config)
    approvals = {"count": 0}

    def always_approve(_description: str) -> bool:
        approvals["count"] += 1
        return True

    pm = PermissionManager(config=config, audit=audit, prompt_fn=always_approve, trust_engine=trust)
    yield pm, approvals
    audit.close()


class TestNeverPromoteExemption:
    def test_db_query_is_every_time_tier(self):
        assert ACTION_TIERS["db_query"] == PermissionTier.EVERY_TIME

    def test_ordinary_every_time_action_does_auto_promote(self, manager):
        """Baseline: confirm the promotion mechanism itself works for an
        action NOT in NEVER_PROMOTE, so the exemption test below is
        actually proving something (not just "nothing ever promotes")."""
        pm, approvals = manager
        assert "code_execute" not in NEVER_PROMOTE
        for _ in range(5):
            pm.check("code_execute", session_id="s1")
        # After trust_threshold (3) clean approvals, further calls should
        # be auto-approved without re-invoking the prompt function.
        approvals["count"] = 0
        pm.check("code_execute", session_id="s1")
        assert approvals["count"] == 0, "expected trust-promoted auto-approval, prompt was still called"

    def test_db_query_never_auto_promotes_even_after_many_approvals(self, manager):
        pm, approvals = manager
        assert "db_query" in NEVER_PROMOTE

        for _ in range(25):
            allowed = pm.check("db_query", session_id="s1")
            assert allowed

        # 25 approvals is well past trust_threshold=3 — an ordinary
        # EVERY_TIME action would have auto-promoted by now. db_query must
        # still be hitting the real prompt function on every single call.
        assert approvals["count"] == 25

    def test_email_send_also_exempted(self):
        assert "email_send" in NEVER_PROMOTE
        assert ACTION_TIERS["email_send"] == PermissionTier.EVERY_TIME
