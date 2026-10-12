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


def test_search_endpoint_returns_structured_answer_with_timing(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod.dbmod, "DB_PATH", tmp_path / "p.sqlite")
    monkeypatch.setattr("mlbasker.client.CACHE_DIR", tmp_path / "cache")
    with TestClient(appmod.create_app(feed=False)) as c:
        r = c.get("/api/search", params={"q": "what is OPS"}).json()
        assert r["kind"] == "define" and r["headline"] == "OPS" and r["query"] == "what is OPS"
        assert isinstance(r["took_ms"], int) and r["suggestions"]
        # every field the front end reads is present on every answer
        for key in ("text", "title", "headline", "subhead", "tiles", "columns", "rows", "results", "notes",
                    "footnote", "suggestions", "highlight_rows", "best_cells", "highlights"):
            assert key in r
        assert c.get("/api/search", params={"q": ""}).json()["kind"] == "help"
        # a question that needs the (empty) index says so, with a way forward
        r = c.get("/api/search", params={"q": "last time a game ended on a pickoff at first base"}).json()
        assert r["kind"] == "needs_index" and r["notes"]


def test_front_end_assets_are_served(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod.dbmod, "DB_PATH", tmp_path / "p.sqlite")
    monkeypatch.setattr("mlbasker.client.CACHE_DIR", tmp_path / "cache")
    with TestClient(appmod.create_app(feed=False)) as c:
        html = c.get("/").text
        assert "/static/app.js" in html and "/static/app.css" in html and 'role="search"' in html
        for asset in ("app.js", "app.css", "favicon.svg"):
            assert c.get(f"/static/{asset}").status_code == 200
