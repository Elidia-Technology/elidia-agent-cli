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


class TestFileReadPathClassification:
    """Regression coverage for the bug found 2026-07-26 live-testing tool
    calls: file_list (and file_grep/file_glob) default their path argument
    to "." when the model omits it. classify_action("file_read", path=None)
    used to fall through to the bare, unmapped "file_read" action, which
    defaults to EVERY_TIME — so a tool call like file_list({}) got an
    unexpected permission prompt instead of the AUTO tier every other
    project-local read gets."""

    def test_missing_path_classifies_as_project_local(self, manager):
        pm, _ = manager
        assert pm.classify_action("file_read", path=None) == "file_read_project"

    def test_explicit_dot_classifies_as_project_local(self, manager):
        pm, _ = manager
        assert pm.classify_action("file_read", path=".") == "file_read_project"

    def test_missing_path_ends_up_at_auto_tier(self, manager):
        pm, approvals = manager
        allowed = pm.check("file_read", session_id="s1", path=None)
        assert allowed
        assert approvals["count"] == 0, "AUTO tier should never invoke the prompt function"

    def test_external_path_still_requires_confirmation(self, manager, tmp_dir: Path):
        pm, approvals = manager
        pm.set_project_root(tmp_dir)
        allowed = pm.check("file_read", session_id="s1", path="/etc/passwd")
        assert allowed  # fixture prompt_fn always approves
        assert approvals["count"] == 1, "external path must still prompt, not silently AUTO-approve"
