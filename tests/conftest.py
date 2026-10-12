import json
from pathlib import Path

import pytest

from mlbasker.index.db import connect
from mlbasker.index.extract import extract_game
from mlbasker.index.sync import _write

FIX = Path(__file__).parent / "fixtures"
META = json.loads((FIX / "meta.json").read_text())


def load_feed(name: str) -> dict:
    return json.loads((FIX / f"game_{name}.json").read_text())


@pytest.fixture(scope="session")
def extracted():
    return {k: extract_game(load_feed(k), META[k]) for k in META}


@pytest.fixture()
def index(extracted):
    con = connect(":memory:")
    for ex in extracted.values():
        _write(con, ex)
    con.commit()
    return con


class FakeClient:
    """Canned API responses; records every request so tests can inspect date params."""

    def __init__(self, routes: dict[str, dict] | None = None):
        self.routes = routes or {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, params=None, **kw):
        self.calls.append((path, dict(params or {})))
        for prefix, data in self.routes.items():
            if path.startswith(prefix):
                return data
        raise AssertionError(f"unexpected API call: {path} {params}")
