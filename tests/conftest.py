"""Shared fixtures for Elidia CLI tests."""
import asyncio
import tempfile
from pathlib import Path

import pytest
from rich.console import Console


@pytest.fixture
def console():
    return Console(quiet=True)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
