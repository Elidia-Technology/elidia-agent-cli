"""Tests for elidia.cli.progress — spinners, progress bars, stage tracker."""
import pytest
from rich.console import Console

from elidia.cli.progress import (
    DownloadTracker,
    StageTracker,
    create_progress_bar,
    spinner,
)


class TestStageTracker:
    def test_init(self, console):
        st = StageTracker(console, ["A", "B", "C"])
        assert st.current_stage is None
        assert not st.is_complete

    def test_advance(self, console):
        st = StageTracker(console, ["A", "B"])
        st.start()
        st.advance()
        assert st.current_stage == "A"
        st.advance()
        assert st.current_stage == "B"

    def test_complete(self, console):
        st = StageTracker(console, ["A", "B"])
        st.start()
        st.advance()
        st.advance()
        st.complete()
        assert st.is_complete

    def test_advance_by_index(self, console):
        st = StageTracker(console, ["A", "B", "C"])
        st.start()
        st.advance(2)
        assert st.current_stage == "C"

    def test_render_summary_no_crash(self, console):
        st = StageTracker(console, ["A", "B"])
        st.start()
        st.advance()
        st.complete()
        st.render_summary()  # should not raise


class TestDownloadTracker:
    def test_start_and_update(self, console):
        dt = DownloadTracker(console)
        progress = dt.start("test.bin", 1000)
        assert progress is not None
        dt.update(500)
        dt.finish()


class TestProgressBar:
    def test_create_progress_bar(self, console):
        pb = create_progress_bar(console)
        assert pb is not None


class TestSpinner:
    def test_spinner_context(self, console):
        with spinner(console, "test") as live:
            assert live is not None
