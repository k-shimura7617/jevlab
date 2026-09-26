from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jevlab import web
from jevlab.apps import APPS
from jevlab.web import app

SPA_HTML = '<!doctype html><div id="root"></div>'


@pytest.fixture
def static_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """frontend のビルド有無に依存しないよう、ビルド出力の代わりを置く。"""
    d = tmp_path / "static"
    d.mkdir()
    (d / "index.html").write_text(SPA_HTML, encoding="utf-8")
    (d / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    monkeypatch.setattr(web, "STATIC_DIR", d)
    return d


def test_endpoints_in_mock_mode(mock_env: Path, static_dir: Path) -> None:
    with TestClient(app) as client:
        for path in ("/", "/eval", "/eval/apps/triage", "/eval/apps/triage/run", "/apps/triage", "/apps/triage/run"):
            res = client.get(path)
            assert res.status_code == 200
            assert res.text == SPA_HTML
        favicon = client.get("/favicon.ico")
        assert favicon.status_code == 200
        assert favicon.headers["content-type"].startswith("image/svg+xml")
        assert client.get("/api/status").json()["default"] == "mock"
        assert [a["name"] for a in client.get("/api/apps").json()] == list(APPS)
        assert client.get("/api/apps/triage").json()["title"] == "問い合わせ仕分け"
        assert len(client.get("/api/apps/triage/samples").json()) == 30
        res = client.post("/api/apps/triage/judge", json={"body": "ログインできません"})
        assert res.status_code == 200
        assert res.json()["model"] == "mock-jev"
        assert client.post("/api/apps/triage/judge", json={"body": ""}).status_code == 422
        assert client.post("/api/apps/triage/evaluate").json()["n"] == 30


def test_pages_without_frontend_build_are_503(mock_env: Path, static_dir: Path) -> None:
    (static_dir / "index.html").unlink()
    with TestClient(app) as client:
        res = client.get("/")
        assert res.status_code == 503
        assert "npm run build" in res.json()["detail"]
        assert client.get("/api/status").status_code == 200


def test_unknown_app_is_404(mock_env: Path) -> None:
    with TestClient(app) as client:
        assert client.get("/apps/nope").status_code == 404
        assert client.get("/eval/apps/nope/run").status_code == 404
        assert client.get("/api/apps/nope").status_code == 404
        assert client.post("/api/apps/nope/judge", json={"body": "x"}).status_code == 404


def test_budget_exceeded_maps_to_409(mock_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEVLAB_BUDGET_USD", "0")
    with TestClient(app) as client:
        res = client.post("/api/apps/triage/judge", json={"body": "ログインできません"})
    assert res.status_code == 409
    assert "予算上限" in res.json()["detail"]


def _targets(status: dict[str, object]) -> dict[str, dict[str, object]]:
    targets = status["targets"]
    assert isinstance(targets, list)
    return {t["name"]: t for t in targets}


def test_status_for_custom_endpoint(mock_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 従来の custom 起動（TYPESAFE_BASE_URL を Kev に向ける）
    monkeypatch.delenv("JEVLAB_MOCK")
    monkeypatch.delenv("KEV_URL")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("TYPESAFE_API_KEY", "local")
    with TestClient(app) as client:
        status = client.get("/api/status").json()
    assert status["default"] == "custom"
    custom = _targets(status)["custom"]
    assert custom["endpoint"] == "http://127.0.0.1:9"
    assert custom["billed"] is False
    # ダミーの鍵を Jev に使わない
    assert _targets(status)["jev"]["available"] is False


def test_status_lists_all_targets(mock_env: Path) -> None:
    with TestClient(app) as client:
        targets = _targets(client.get("/api/status").json())
    assert list(targets) == ["mock", "jev", "custom"]
    assert targets["mock"]["available"] is True
    assert targets["jev"]["available"] is False
    assert "TYPESAFE_API_KEY" in str(targets["jev"]["reason"])
    assert targets["custom"]["available"] is False
    assert "Kev" in str(targets["custom"]["reason"])


def test_jev_target_is_enabled_with_api_key(mock_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "dummy")
    with TestClient(app) as client:
        status = client.get("/api/status").json()
    assert status["default"] == "mock"
    jev = _targets(status)["jev"]
    assert jev["available"] is True
    assert jev["endpoint"] == "https://api.typesafe.ai"
    assert jev["billed"] is True


def test_default_target_env(mock_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEVLAB_MOCK")
    monkeypatch.setenv("JEVLAB_DEFAULT_TARGET", "custom")
    with TestClient(app) as client:
        assert client.get("/api/status").json()["default"] == "custom"


def test_target_query_selects_backend(mock_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEVLAB_DEFAULT_TARGET", "custom")
    with TestClient(app) as client:
        res = client.post("/api/apps/triage/judge?target=mock", json={"body": "ログインできません"})
        assert res.status_code == 200
        assert res.json()["model"] == "mock-jev"
        # 使用量は接続先ごとの記録に入る
        assert (mock_env / "usage.mock.jsonl").is_file()
        assert not (mock_env / "usage.custom.jsonl").exists()
        # Kev は止まっているので既定（custom）では API エラーになる
        assert client.post("/api/apps/triage/judge", json={"body": "x"}).status_code == 502


def test_invalid_or_unavailable_target(mock_env: Path) -> None:
    with TestClient(app) as client:
        assert client.post("/api/apps/triage/judge?target=nope", json={"body": "x"}).status_code == 422
        res = client.post("/api/apps/triage/judge?target=jev", json={"body": "x"})
        assert res.status_code == 400
        assert "TYPESAFE_API_KEY" in res.json()["detail"]


def test_runs_are_recorded_per_target(mock_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEVLAB_DEFAULT_TARGET", "custom")
    with TestClient(app) as client:
        sample_id = client.get("/api/apps/triage/samples").json()[0]["id"]
        case = client.post(f"/api/apps/triage/samples/{sample_id}/judge?target=mock").json()
        client.post("/api/apps/triage/summary?target=mock", json={"cases": [case]})
        client.post("/api/apps/triage/summary?target=custom", json={"cases": [case]})
        modes = sorted(r["mode"] for r in client.get("/api/apps/triage/runs").json())
    assert modes == ["custom", "mock"]


def test_live_evaluation_flow(mock_env: Path) -> None:
    with TestClient(app) as client:
        assert client.get("/apps/triage/run").status_code == 200
        assert client.get("/apps/nope/run").status_code == 404
        assert client.get("/api/runs/latest").json() == []

        samples = client.get("/api/apps/triage/samples").json()
        res = client.post(f"/api/apps/triage/samples/{samples[0]['id']}/judge")
        assert res.status_code == 200
        case = res.json()
        assert case["sample"]["id"] == samples[0]["id"]
        assert set(case["ok"]) == set(samples[0]["labels"])
        assert client.post("/api/apps/triage/samples/nope/judge").status_code == 404

        # 途中で中止した想定で 1 件だけ集計する
        report = client.post("/api/apps/triage/summary", json={"cases": [case]}).json()
        assert report["n"] == 1
        latest = client.get("/api/runs/latest").json()
        assert [(r["app"], r["mode"], r["n"], r["total"]) for r in latest] == [("triage", "mock", 1, 30)]


def test_summary_regrades_with_server_labels(mock_env: Path) -> None:
    with TestClient(app) as client:
        sample_id = client.get("/api/apps/triage/samples").json()[0]["id"]
        case = client.post(f"/api/apps/triage/samples/{sample_id}/judge").json()
        tampered = {**case, "ok": dict.fromkeys(case["ok"], True), "sample": {**case["sample"], "labels": {}}}
        report = client.post("/api/apps/triage/summary", json={"cases": [tampered]}).json()
        assert report["cases"][0]["ok"] == case["ok"]


def test_summary_rejects_invalid_cases(mock_env: Path) -> None:
    with TestClient(app) as client:
        sample_id = client.get("/api/apps/triage/samples").json()[0]["id"]
        case = client.post(f"/api/apps/triage/samples/{sample_id}/judge").json()
        assert client.post("/api/apps/triage/summary", json={"cases": []}).status_code == 422
        assert client.post("/api/apps/triage/summary", json={"cases": [case, case]}).status_code == 422
        unknown = {**case, "sample": {**case["sample"], "id": "nope"}}
        assert client.post("/api/apps/triage/summary", json={"cases": [unknown]}).status_code == 422


def test_evaluate_is_recorded_per_mode(mock_env: Path) -> None:
    with TestClient(app) as client:
        client.post("/api/apps/triage/evaluate")
        client.post("/api/apps/triage/evaluate")
        history = client.get("/api/apps/triage/runs").json()
        assert len(history) == 2
        assert all(r["mode"] == "mock" and r["n"] == r["total"] == 30 for r in history)
        assert len(client.get("/api/runs/latest").json()) == 1
        assert client.get("/api/apps/nope/runs").status_code == 404
