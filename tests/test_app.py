from fastapi.testclient import TestClient

from mlbasker import app as appmod


def test_status_and_help_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("MLBASKER_DB", str(tmp_path / "p.sqlite"))
    monkeypatch.setenv("MLBASKER_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(appmod.dbmod, "DB_PATH", tmp_path / "p.sqlite")
    monkeypatch.setattr("mlbasker.client.CACHE_DIR", tmp_path / "cache")
    with TestClient(appmod.create_app(feed=False)) as c:
        st = c.get("/api/status").json()
        assert st["index"]["games"] == 0 and "cutoff" in st
        # an empty question returns the help text without touching the network
        r = c.post("/api/ask", json={"question": "  "}).json()
        assert r["kind"] == "help"
        # history questions with an empty index explain how to build it
        r = c.post("/api/ask", json={"question": "last time a game ended on a pickoff at first base"}).json()
        assert r["kind"] == "needs_index"
        assert c.get("/").status_code == 200
