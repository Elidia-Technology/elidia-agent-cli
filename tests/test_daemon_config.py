"""Tests for elidia.daemon.config — TOML task declarations for the background daemon."""
from pathlib import Path

from elidia.daemon.config import (
    DaemonConfig,
    WatcherConfig,
    WebhookConfig,
    load_daemon_config,
    write_example_daemon_config,
)


class TestLoadDaemonConfig:
    def test_missing_file_returns_empty_config(self, tmp_dir: Path):
        config = load_daemon_config(tmp_dir / "nonexistent.toml")
        assert config == DaemonConfig()
        assert config.watchers == []
        assert config.schedules == []
        assert config.webhooks == []

    def test_loads_real_watcher(self, tmp_dir: Path):
        path = tmp_dir / "daemon.toml"
        path.write_text(
            '[[watcher]]\nname = "src-watch"\npath = "./src"\n'
            'patterns = ["*.py"]\ninterval = 5.0\n',
            encoding="utf-8",
        )
        config = load_daemon_config(path)
        assert len(config.watchers) == 1
        assert config.watchers[0] == WatcherConfig(name="src-watch", path="./src", patterns=["*.py"], interval=5.0)

    def test_loads_real_cron_schedule(self, tmp_dir: Path):
        path = tmp_dir / "daemon.toml"
        path.write_text(
            '[[schedule]]\nname = "nightly"\ncron = "0 9 * * *"\ncommand = "echo hi"\n',
            encoding="utf-8",
        )
        config = load_daemon_config(path)
        assert len(config.schedules) == 1
        assert config.schedules[0].name == "nightly"
        assert config.schedules[0].cron == "0 9 * * *"
        assert config.schedules[0].command == "echo hi"

    def test_loads_real_webhook(self, tmp_dir: Path):
        path = tmp_dir / "daemon.toml"
        path.write_text(
            '[[webhook]]\nname = "deploy"\npath = "/hook"\nport = 9999\n',
            encoding="utf-8",
        )
        config = load_daemon_config(path)
        assert config.webhooks == [WebhookConfig(name="deploy", path="/hook", port=9999)]

    def test_loads_multiple_of_each_type(self, tmp_dir: Path):
        path = tmp_dir / "daemon.toml"
        path.write_text(
            '[[watcher]]\nname = "a"\npath = "."\n'
            '[[watcher]]\nname = "b"\npath = "./x"\n'
            '[[schedule]]\nname = "s1"\ninterval_seconds = 60\n',
            encoding="utf-8",
        )
        config = load_daemon_config(path)
        assert len(config.watchers) == 2
        assert len(config.schedules) == 1

    def test_defaults_apply_when_optional_fields_omitted(self, tmp_dir: Path):
        path = tmp_dir / "daemon.toml"
        path.write_text('[[watcher]]\nname = "minimal"\npath = "."\n', encoding="utf-8")
        config = load_daemon_config(path)
        assert config.watchers[0].patterns == ["*"]
        assert config.watchers[0].interval == 2.0


class TestWriteExampleConfig:
    def test_writes_a_real_file(self, tmp_dir: Path):
        path = tmp_dir / "sub" / "daemon.toml"
        write_example_daemon_config(path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "watcher" in content
        assert "schedule" in content
        assert "webhook" in content

    def test_written_example_is_all_commented_out(self, tmp_dir: Path):
        """The example must not silently activate any real watcher/schedule/webhook."""
        path = tmp_dir / "daemon.toml"
        write_example_daemon_config(path)
        config = load_daemon_config(path)
        assert config == DaemonConfig()
