from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jevlab.core.generator import GeneratedText, GenerateRequest
from jevlab.ops import api as ops_api
from jevlab.ops.pipeline import Pipeline
from jevlab.web import app

PENDING = {"queued", "processing"}


@pytest.fixture
def client(mock_env: Path) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def settle(client: TestClient, item_id: str, timeout: float = 5.0) -> dict[str, Any]:
    """処理ワーカーが件を処理し終えるまで待つ。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail: dict[str, Any] = client.get(f"/api/ops/items/{item_id}").json()
        if detail["item"]["status"] not in PENDING:
            return detail
        time.sleep(0.05)
    raise AssertionError(f"{item_id} の処理が {timeout} 秒で終わりませんでした")


def configure(client: TestClient, **changes: Any) -> dict[str, Any]:
    settings: dict[str, Any] = client.get("/api/ops/settings").json()
    for path, value in changes.items():
        section, _, key = path.partition("__")
        if key:
            settings[section][key] = value
        else:
            settings[section] = value
    res = client.put("/api/ops/settings", json=settings)
    assert res.status_code == 200, res.text
    return settings


def ingest(client: TestClient, body: str, **extra: str) -> str:
    res = client.post(
        "/api/ops/ingest", json={"channel": "mail", "from_name": "テスト", "subject": "", "body": body, **extra}
    )
    assert res.status_code == 200, res.text
    item_id: str = res.json()["id"]
    return item_id


def kinds(detail: dict[str, Any]) -> list[str]:
    return [e["kind"] for e in detail["events"]]


def test_initial_settings_use_mock_in_mock_mode(client: TestClient) -> None:
    s = client.get("/api/ops/settings").json()
    assert s["guard"]["target"] == "mock"
    assert s["classify"]["target"] == "mock"
    meta = client.get("/api/ops/meta").json()
    assert meta["demo_count"] == 64
    assert set(meta["categories"]) == {"inquiry", "complaint", "thanks", "other"}


def test_item_without_pii_is_classified_and_routed(client: TestClient) -> None:
    # モックの「取りこぼしの可能性」は乱数なので、人の確認を切って流れを決まったものにする
    configure(
        client, guard__human_check=False, classify__escalate_urgent=False, classify__escalate_strong_frustration=False
    )
    item_id = ingest(client, "木製のペン立てに名入れはできますか。KM-250914-0031 で注文予定です。")
    detail = settle(client, item_id)
    item = detail["item"]
    assert item["status"] in {"routed", "review", "escalated"}
    assert item["decided_by"] == "mock"
    assert item["category"] in {"inquiry", "complaint", "thanks", "other"}
    assert item["first_route"] == item["status"]
    assert set(item["priority"]) == {"frustration", "urgent", "refund", "publicity"}
    assert kinds(detail)[:3] == ["received", "guard", "guard"]
    assert "classify" in kinds(detail)


def test_structured_pii_goes_to_human_check_then_masked(client: TestClient) -> None:
    item_id = ingest(client, "配送先の電話番号を 090-0000-1234 に変更してください。")
    detail = settle(client, item_id)
    item = detail["item"]
    assert item["status"] == "pii_review"
    phone = next(s for s in item["pii"] if s["type"] == "phone")
    # 形で決まる種類はモデルに聞かず規則で確定する
    assert phone["confirmed"] is True and phone["score"] is None
    res = client.post(f"/api/ops/items/{item_id}/pii", json={"spans": item["pii"], "action": "continue"})
    assert res.status_code == 200, res.text
    done = settle(client, item_id)["item"]
    assert done["pii_decision"] == "masked"
    assert "090-0000-1234" not in done["sent_text"] and "【電話番号】" in done["sent_text"]
    assert done["category"] is not None


def test_pii_eval_counts_only_items_a_human_reviewed(client: TestClient) -> None:
    assert client.get("/api/ops/pii-eval").json()["items"] == 0
    item_id = ingest(client, "配送先の電話番号を 090-0000-1234 に変更してください。")
    item = settle(client, item_id)["item"]
    assert item["status"] == "pii_review"
    client.post(f"/api/ops/items/{item_id}/pii", json={"spans": item["pii"], "action": "continue"})
    settle(client, item_id)
    # 1 件ずつ確認した件は数える。一括で編集なしに流した件は数えない
    other = ingest(client, "電話は 090-1111-2222 です。")
    assert settle(client, other)["item"]["status"] == "pii_review"
    client.post("/api/ops/bulk/pii", json={"ids": [other]})
    settle(client, other)
    assert client.get("/api/ops/pii-eval").json()["items"] == 1


def test_human_can_add_missed_span(client: TestClient) -> None:
    body = "電話 090-0000-1234。受取人は木村です"
    item_id = ingest(client, body)
    item = settle(client, item_id)["item"]
    start = item["text"].index("木村")
    added = {"start": start, "end": start + 2, "type": "person_name", "text": "", "source": "human", "confirmed": True}
    res = client.post(f"/api/ops/items/{item_id}/pii", json={"spans": [*item["pii"], added], "action": "continue"})
    assert res.status_code == 200, res.text
    done = settle(client, item_id)
    assert "木村" not in done["item"]["sent_text"]
    assert any("追加 1 件" in e["message"] for e in done["events"])


def test_blocked_pii_is_never_sent_to_classifier(client: TestClient) -> None:
    configure(client, guard__human_check=False)
    item_id = ingest(client, "カード 4111-1111-1111-1111 で二重に引き落とされました。")
    detail = settle(client, item_id)
    item = detail["item"]
    assert item["pii_decision"] == "blocked"
    assert item["sent_text"] is None
    assert item["status"] == "escalated"
    # 仕分け（Jev）には回さない。記録されるのは、ローカル（Kev・MOCK）の参考の判定だけ
    classify = [e for e in detail["events"] if e["kind"] == "classify"]
    assert all(e["actor"] != "jev" and e["message"].startswith("参考") for e in classify)
    assert item["category"] is None


def test_blocked_item_gets_local_reference_without_auto_routing(client: TestClient) -> None:
    configure(client, guard__human_check=False, guard__target="mock")
    configure(client, assign={"auto": True, "threshold": 0.0, "use_examples": True, "max_examples": 3})
    item_id = ingest(client, "カード 4111-1111-1111-1111 で二重に引き落とされました。")
    detail = settle(client, item_id)
    item = detail["item"]
    ref = item["kev_reference"]
    # 参考の分類・担当の推定は残るが、自動の振り分け・割り当てはしない
    assert ref is not None and ref["category"] in {"inquiry", "complaint", "thanks", "other"}
    assert item["status"] == "escalated" and item["category"] is None and item["assignee"] is None
    assert item["sent_text"] is None
    assert any(e["message"].startswith("参考（MOCK）") for e in detail["events"])
    # チャンネル（Slack に流れうる投稿）には件名の伏せ字だけ。カード番号は出さない
    assert all("4111" not in p["text"] for p in client.get("/api/ops/posts").json())


def test_blocked_item_is_posted_before_kev_reference(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Kev が遅い・落ちていても、人への知らせ（Slack への転送元）は待たせない
    seen: list[bool] = []

    async def slow_reference(self: Pipeline, item: Any, settings: Any) -> None:
        seen.append(any(p.item_id == item.id for p in self.store.posts()))

    monkeypatch.setattr(Pipeline, "_kev_reference", slow_reference)
    configure(client, guard__human_check=False, guard__target="mock")
    settle(client, ingest(client, "カード 4111-1111-1111-1111 で二重に引き落とされました。"))
    assert seen == [True]


def test_no_reference_when_model_is_off(client: TestClient) -> None:
    configure(client, guard__human_check=False, guard__target="mock", guard__use_model=False)
    item_id = ingest(client, "カード 4111-1111-1111-1111 で二重に引き落とされました。")
    detail = settle(client, item_id)
    assert detail["item"]["kev_reference"] is None
    assert any("参考の判定はしません" in e["message"] for e in detail["events"])


def test_blocked_pii_can_be_classified_by_local_model(client: TestClient) -> None:
    configure(client, guard__human_check=False, guard__blocked_route="kev")
    item_id = ingest(client, "カード 4111-1111-1111-1111 で二重に引き落とされました。")
    detail = settle(client, item_id)
    # テストでは Kev に接続できないため、Kev で仕分けようとしてエラーになる（外部には送らない）
    assert detail["item"]["status"] == "error"
    # 画面には直し方の分かる文を出し、元のエラーは経過のデータに残す
    assert "に接続できません" in detail["item"]["error"] and "再実行" in detail["item"]["error"]
    errors = [e for e in detail["events"] if e["kind"] == "error"]
    assert errors and "Connect" in errors[-1]["data"]["raw"]
    assert detail["item"]["sent_text"] is None


def test_review_decision_routes_and_posts(client: TestClient) -> None:
    configure(
        client,
        guard__human_check=False,
        classify__auto_threshold=1.0,
        classify__review_threshold=0.0,
        classify__escalate_urgent=False,
        classify__escalate_strong_frustration=False,
    )
    item_id = ingest(client, "ラッピングはできますか？")
    assert settle(client, item_id)["item"]["status"] == "review"
    res = client.post(f"/api/ops/items/{item_id}/decide", json={"category": "inquiry"})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "routed"
    posts = client.get("/api/ops/posts").json()
    assert any(p["item_id"] == item_id and p["channel"] == "#cs-問い合わせ" for p in posts)
    assert client.post(f"/api/ops/items/{item_id}/decide", json={"category": "nope"}).status_code == 422


def test_escalation_assign_note_close(client: TestClient) -> None:
    configure(client, guard__human_check=False, classify__auto_threshold=1.0, classify__review_threshold=1.0)
    item_id = ingest(client, "至急連絡ください")
    assert settle(client, item_id)["item"]["status"] == "escalated"
    assert client.post(f"/api/ops/items/{item_id}/assign", json={"assignee": "suzuki"}).json()["assignee"] == "suzuki"
    assert client.post(f"/api/ops/items/{item_id}/note", json={"text": "電話済み"}).json()["notes"] == ["電話済み"]
    closed = client.post(f"/api/ops/items/{item_id}/close", json={"category": "complaint"}).json()
    assert closed["status"] == "closed" and closed["category"] == "complaint"
    # 完了した件はもう完了にできない
    assert client.post(f"/api/ops/items/{item_id}/close", json={"category": None}).status_code == 422


def test_off_duty_staff_are_not_suggested_or_assigned(client: TestClient) -> None:
    settings = client.get("/api/ops/settings").json()
    # 鈴木だけ担当オン。推定は鈴木か「該当なし」に限られ、ほかの人には割り当てられない
    for m in settings["staff"]:
        m["active"] = m["id"] == "suzuki"
    client.put("/api/ops/settings", json=settings)
    configure(client, guard__human_check=False, classify__auto_threshold=1.0, classify__review_threshold=1.0)
    item_id = ingest(client, "至急連絡ください")
    item = settle(client, item_id)["item"]
    assert item["status"] == "escalated"
    assert item["assign_suggestion"] in {None, "suzuki"}
    res = client.post(f"/api/ops/items/{item_id}/assign", json={"assignee": "sato"})
    assert res.status_code == 422 and "オフ" in res.text
    assert client.post(f"/api/ops/items/{item_id}/assign", json={"assignee": "suzuki"}).status_code == 200


def test_backfill_import_is_classified_but_not_posted(client: TestClient) -> None:
    rows = [
        {
            "subject": "在庫",
            "body": "木製のペン立ては再入荷しますか？",
            "category": "inquiry",
            "received_at": "2025-01-10T09:00:00+09:00",
        },
        {"subject": "返金", "body": "電話は 090-0000-1234 です。返金してください", "category": "complaint"},
    ]
    res = client.post(
        "/api/ops/import", json={"file_name": "過去分.csv", "channel": "mail", "backfill": True, "rows": rows}
    )
    assert res.status_code == 200, res.text
    ids = res.json()["ids"]
    items = [settle(client, i)["item"] for i in ids]
    # 人の確認にも投稿にも回さず、振り分けの結果だけ残して完了にする
    assert all(i["status"] == "closed" and i["backfill"] and i["first_route"] is not None for i in items)
    assert items[0]["received_at"].startswith("2025-01-10") and items[0]["channel"] == "mail"
    assert [i["expected"]["category"] for i in items] == ["inquiry", "complaint"]
    # 電話番号は伏せてから Jev に送る
    assert "090-0000-1234" not in (items[1]["sent_text"] or "")
    assert client.get("/api/ops/posts").json() == []
    # 運用の集計には入れず、試算（過去の分類を正解にした閾値の調整）には入れる
    assert client.get("/api/ops/overview").json()["flow"]["received"] == 0
    assert client.get("/api/ops/tuning", params={"source": "expected"}).json()["n"] == 2
    assert client.get("/api/ops/tuning", params={"source": "human"}).json()["n"] == 0


def test_import_rejects_unknown_category(client: TestClient) -> None:
    rows = [{"body": "本文", "category": "苦情"}]
    assert client.post("/api/ops/import", json={"rows": rows}).status_code == 422


def test_parse_import_file(client: TestClient) -> None:
    data = base64.b64encode("件名,本文\n在庫,再入荷しますか\n".encode("cp932")).decode()
    res = client.post("/api/ops/import/parse", json={"file_name": "a.csv", "data": data})
    assert res.status_code == 200 and res.json()["table"][1] == ["在庫", "再入荷しますか"]
    bad = client.post("/api/ops/import/parse", json={"file_name": "a.msg", "data": data})
    assert bad.status_code == 422 and ".eml" in bad.text


def test_fast_simulator_ingests_everything_at_once(client: TestClient) -> None:
    configure(client, guard__use_model=False, guard__human_check=False)
    state = client.post("/api/ops/simulator", json={"fast": True, "playing": True}).json()
    assert state["fast"] is True and state["fast_workers"] > 3
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        ov = client.get("/api/ops/overview").json()
        if ov["simulator"]["cursor"] == ov["simulator"]["total"] and ov["flow"]["waiting"] == 0:
            break
        time.sleep(0.1)
    ov = client.get("/api/ops/overview").json()
    # 間隔なしで全件を受信し、最後まで流すと止まる
    assert ov["simulator"]["cursor"] == ov["simulator"]["total"] and ov["simulator"]["playing"] is False
    assert ov["flow"]["received"] == ov["simulator"]["total"] and ov["flow"]["waiting"] == 0


def test_disconnected_connector_rejects_ingest(client: TestClient) -> None:
    settings = client.get("/api/ops/settings").json()
    settings["connectors"]["mail"] = False
    client.put("/api/ops/settings", json=settings)
    res = client.post("/api/ops/ingest", json={"channel": "mail", "body": "こんにちは"})
    assert res.status_code == 409
    assert "切断" in res.json()["detail"]


def test_csv_import_and_chat(client: TestClient) -> None:
    res = client.post(
        "/api/ops/import",
        json={"file_name": "a.csv", "rows": [{"subject": "件名", "body": "本文1"}, {"body": "本文2"}]},
    )
    assert res.status_code == 200
    assert res.json()["imported"] == 2
    chat = client.post("/api/ops/chat", json={"from_name": "森野商店", "body": "数量を変更できますか"}).json()
    assert chat["channel"] == "chat"
    assert any(p["channel"] == "#お問い合わせ窓口" for p in client.get("/api/ops/posts").json())
    assert client.post("/api/ops/import", json={"rows": []}).status_code == 422


def test_simulator_step_rewind_and_reset(client: TestClient) -> None:
    state = client.post("/api/ops/simulator", json={"step": True}).json()
    assert state["cursor"] == 1
    items = client.get("/api/ops/items").json()
    assert len(items) == 1 and items[0]["expected"] is not None
    assert client.post("/api/ops/simulator", json={"rewind": True}).json()["cursor"] == 0
    client.post("/api/ops/reset")
    assert client.get("/api/ops/items").json() == []


def test_settings_validation(client: TestClient) -> None:
    settings = client.get("/api/ops/settings").json()
    settings["classify"]["review_threshold"] = 0.95
    settings["classify"]["auto_threshold"] = 0.9
    assert client.put("/api/ops/settings", json=settings).status_code == 422


def test_unknown_item_is_404(client: TestClient) -> None:
    assert client.get("/api/ops/items/T-9999").status_code == 404
    assert client.post("/api/ops/items/T-9999/decide", json={"category": "inquiry"}).status_code == 404


def test_overview_and_tuning(client: TestClient) -> None:
    for _ in range(3):
        client.post("/api/ops/simulator", json={"step": True})
    overview = client.get("/api/ops/overview").json()
    assert overview["flow"]["received"] == 3
    report = client.get("/api/ops/tuning", params={"source": "expected", "target_error": 0.05}).json()
    assert report["overall"]["recommended"] is None  # 件数が少ないうちは提案しない
    assert len(report["by_label"]) == 4


def test_overview_reports_kev_only_when_used(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from jevlab.core import kev_health

    state: dict[str, str | None] = {"reason": "Kev（http://127.0.0.1:9）に接続できません: ConnectError"}

    async def fake() -> str | None:
        return state["reason"]

    monkeypatch.setattr(kev_health, "cached_down_reason", fake)
    assert client.get("/api/ops/overview").json()["kev"] is None  # モックの設定では Kev を使わない
    configure(client, guard__target="custom")
    kev = client.get("/api/ops/overview").json()["kev"]
    assert kev["available"] is False
    assert kev["endpoint"] == "http://127.0.0.1:9" and kev["uses"] == ["個人情報のチェック"]
    # 次の読み込みで戻ったことが分かる（画面は概要を数秒おきに読む）
    state["reason"] = None
    assert client.get("/api/ops/overview").json()["kev"]["available"] is True


def test_ops_pages_are_served(mock_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jevlab import web

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    monkeypatch.setattr(web, "STATIC_DIR", static)
    with TestClient(app) as c:
        for path in ("/ops", "/ops/inbox", "/ops/items/T-0001"):
            assert c.get(path).status_code == 200


def test_guard_cannot_send_raw_text_to_jev(client: TestClient) -> None:
    settings = client.get("/api/ops/settings").json()
    settings["guard"]["target"] = "jev"
    assert client.put("/api/ops/settings", json=settings).status_code == 422


def test_partial_policy_is_completed_with_defaults(client: TestClient) -> None:
    settings = client.get("/api/ops/settings").json()
    settings["guard"]["policy"] = {"phone": "allow"}
    saved = client.put("/api/ops/settings", json=settings).json()
    assert saved["guard"]["policy"]["card"] == "block"
    assert saved["guard"]["policy"]["phone"] == "allow"


def test_rule_confirmed_pii_cannot_be_removed_by_client(client: TestClient) -> None:
    item_id = ingest(client, "カード 4111-1111-1111-1111 で二重に引き落とされました。")
    item = settle(client, item_id)["item"]
    assert item["status"] == "pii_review"
    # 画面からカード番号を外して送っても、ブロックの方針は守られる
    unconfirmed = [{**s, "confirmed": False} for s in item["pii"]]
    client.post(f"/api/ops/items/{item_id}/pii", json={"spans": unconfirmed, "action": "continue"})
    detail = settle(client, item_id)
    assert detail["item"]["pii_decision"] == "blocked"
    assert detail["item"]["sent_text"] is None
    assert any("戻しました" in e["message"] for e in detail["events"])


def test_posts_do_not_contain_pii(client: TestClient) -> None:
    configure(client, guard__human_check=False, classify__auto_threshold=1.0, classify__review_threshold=1.0)
    item_id = ingest(client, "至急お電話ください", subject="090-0000-1234 まで連絡を", from_name="山田 花子")
    settle(client, item_id)
    posts = [p for p in client.get("/api/ops/posts").json() if p["item_id"] == item_id]
    assert posts
    assert all("090-0000-1234" not in p["text"] and "山田" not in p["text"] for p in posts)


def test_decide_rejects_routed_items_outside_audit(client: TestClient) -> None:
    configure(
        client,
        guard__human_check=False,
        classify__auto_threshold=0.0,
        classify__review_threshold=0.0,
        classify__escalate_urgent=False,
        classify__escalate_strong_frustration=False,
        classify__insufficient_gate=False,
        audit_rate=0.0,
    )
    item_id = ingest(client, "ラッピングはできますか？")
    assert settle(client, item_id)["item"]["status"] == "routed"
    assert client.post(f"/api/ops/items/{item_id}/decide", json={"category": "other"}).status_code == 422


def test_routed_items_can_be_closed_and_count_as_human_checked(client: TestClient) -> None:
    configure(
        client,
        guard__human_check=False,
        classify__auto_threshold=0.0,
        classify__review_threshold=0.0,
        classify__escalate_urgent=False,
        classify__escalate_strong_frustration=False,
        classify__insufficient_gate=False,
        audit_rate=1.0,
    )
    kept = ingest(client, "ラッピングはできますか？")
    fixed = ingest(client, "ラッピングはできますか？（2 件目）")
    first = settle(client, kept)["item"]
    second = settle(client, fixed)["item"]
    assert first["status"] == second["status"] == "routed"
    # 分類を触らずに完了 → 合っていた（抜き取りの結果も「問題なし」）
    closed = client.post(f"/api/ops/items/{kept}/close", json={"category": None}).json()
    assert closed["status"] == "closed" and closed["category"] == first["category"]
    assert closed["audit_result"] == "ok"
    # 分類を切り替えて完了 → 修正（人が決めた分類になる）
    other = next(c for c in ("inquiry", "other") if c != second["category"])
    changed = client.post(f"/api/ops/items/{fixed}/close", json={"category": other}).json()
    assert changed["category"] == other and changed["decided_by"] == "human" and changed["audit_result"] == "fixed"
    # 完了した件は、閾値の調整で人が確かめた正解として数える
    report = client.get("/api/ops/tuning", params={"source": "human"}).json()
    assert report["n"] == 2
    # 完了した件は、もう完了にできない
    assert client.post(f"/api/ops/items/{kept}/close", json={"category": None}).status_code == 422


def test_audit_decision_records_result_once(client: TestClient) -> None:
    configure(
        client,
        guard__human_check=False,
        classify__auto_threshold=0.0,
        classify__review_threshold=0.0,
        classify__escalate_urgent=False,
        classify__escalate_strong_frustration=False,
        classify__insufficient_gate=False,
        audit_rate=1.0,
    )
    item_id = ingest(client, "ラッピングはできますか？")
    item = settle(client, item_id)["item"]
    assert item["audit"] is True
    other = next(c for c in ("inquiry", "other") if c != item["category"])
    assert client.post(f"/api/ops/items/{item_id}/decide", json={"category": other}).json()["audit_result"] == "fixed"
    # 確認済みの件はもう一度確認できない（結果が上書きされない）
    assert client.post(f"/api/ops/items/{item_id}/decide", json={"category": item["category"]}).status_code == 422


def test_insufficient_gate_sends_confident_items_to_review(client: TestClient) -> None:
    # 基準 0 なら、判断材料が足りない確率が少しでもあれば人の確認に回す
    configure(
        client,
        guard__human_check=False,
        classify__auto_threshold=0.0,
        classify__review_threshold=0.0,
        classify__escalate_urgent=False,
        classify__escalate_strong_frustration=False,
        classify__split_margin=0.0,
        classify__insufficient_at=0.0,
        audit_rate=0.0,
    )
    item = settle(client, ingest(client, "ラッピングはできますか？"))["item"]
    assert item["status"] == "review"
    assert item["reason"].startswith("判断材料が足りない")
    assert "insufficient" in item["answers"] and "refund_mentioned" in item["answers"]


def test_retry_after_error(client: TestClient) -> None:
    configure(client, guard__human_check=False, guard__blocked_route="kev")
    item_id = ingest(client, "カード 4111-1111-1111-1111 の件です")
    assert settle(client, item_id)["item"]["status"] == "error"
    assert client.post(f"/api/ops/items/{item_id}/retry").json()["status"] == "queued"
    detail = settle(client, item_id)
    assert detail["item"]["status"] == "error"  # Kev がないので再び失敗するが、外部には送らない
    assert detail["item"]["sent_text"] is None
    assert [e["kind"] for e in detail["events"]].count("retry") == 1
    assert client.post(f"/api/ops/items/{item_id}/retry").status_code == 200


def test_ids_are_not_reused_after_reset(client: TestClient) -> None:
    first = ingest(client, "一件目")
    client.post("/api/ops/reset")
    second = ingest(client, "二件目")
    assert first != second


def test_legacy_staff_names_are_upgraded() -> None:
    from jevlab.ops.models import Settings

    s = Settings.model_validate({"staff": ["佐藤", "鈴木"]})
    assert [(m.id, m.name) for m in s.staff] == [("staff1", "佐藤"), ("staff2", "鈴木")]


def test_duplicate_staff_ids_are_rejected(client: TestClient) -> None:
    settings = client.get("/api/ops/settings").json()
    settings["staff"] = [{"id": "a", "name": "A"}, {"id": "a", "name": "B"}]
    assert client.put("/api/ops/settings", json=settings).status_code == 422


def escalate_one(client: TestClient, body: str = "至急連絡ください") -> dict[str, Any]:
    item_id = ingest(client, body)
    return settle(client, item_id)["item"]


def test_auto_assign_only_when_confident(client: TestClient) -> None:
    configure(client, guard__human_check=False, classify__auto_threshold=1.0, classify__review_threshold=1.0)
    configure(client, assign={"auto": True, "threshold": 0.0, "use_examples": True, "max_examples": 3})
    item = escalate_one(client)
    if item["assign_suggestion"] is not None:  # モックは「該当なし」を選ぶこともある
        assert item["assignee"] == item["assign_suggestion"] and item["assigned_by"] == "auto"
    configure(client, assign={"auto": True, "threshold": 1.0, "use_examples": True, "max_examples": 3})
    item = escalate_one(client, "返金してください、至急")
    assert item["assignee"] is None and item["assigned_by"] is None
    assert item["assign_confidence"] is not None


def test_bulk_assign_and_assignment_stats(client: TestClient) -> None:
    configure(client, guard__human_check=False, classify__auto_threshold=1.0, classify__review_threshold=1.0)
    configure(client, assign={"auto": False, "threshold": 0.7, "use_examples": True, "max_examples": 3})
    ids = [escalate_one(client, f"至急の件 {n}")["id"] for n in range(3)]
    res = client.post("/api/ops/bulk/assign", json={"ids": [*ids, "T-9999"], "assignee": "takahashi"}).json()
    assert res["done"] == ids and "T-9999" in res["errors"]
    assert client.post("/api/ops/bulk/assign", json={"ids": ids, "assignee": "nobody"}).json()["done"] == []
    for i in ids:
        client.post(f"/api/ops/items/{i}/close", json={"category": "complaint"})
    stats = client.get("/api/ops/assignment").json()
    assert stats["closed"] == 3
    # 人が割り当てた件のうち、推定があった件だけで当たり具合を測る
    suggestions = [client.get(f"/api/ops/items/{i}").json()["item"]["assign_suggestion"] for i in ids]
    assert stats["with_suggestion"] == sum(1 for s in suggestions if s is not None)
    assert stats["matched"] == sum(1 for s in suggestions if s == "takahashi")
    assert stats["auto_assigned"] == 0
    posts = [p for p in client.get("/api/ops/posts").json() if p["author"] == "担当者（対応完了）"]
    assert len(posts) == 3 and all("高橋" in p["text"] for p in posts)


def test_bulk_pii_passes_items_through(client: TestClient) -> None:
    ids = [ingest(client, f"電話は 090-0000-12{n}{n} です") for n in range(2)]
    for i in ids:
        assert settle(client, i)["item"]["status"] == "pii_review"
    listed = client.get("/api/ops/items", params={"status": "pii_review"}).json()
    assert all("detected" in i["pii_flags"] for i in listed)
    res = client.post("/api/ops/bulk/pii", json={"ids": ids}).json()
    assert res["done"] == ids
    for i in ids:
        detail = settle(client, i)
        assert "【電話番号】" in detail["item"]["sent_text"]
        assert any(e["message"].startswith("一括で確認") for e in detail["events"])


def test_bulk_pii_masks_possible_names_and_skips_missing_ids(client: TestClient) -> None:
    # 閾値を最大にして、氏名の候補を必ず「未確定」にする
    settings = client.get("/api/ops/settings").json()
    settings["guard"]["candidate_threshold"] = 1.0
    assert client.put("/api/ops/settings", json=settings).status_code == 200
    item_id = ingest(client, "山田花子と申します。注文の件です")
    item = settle(client, item_id)["item"]
    assert item["status"] == "pii_review"
    assert any(not s["confirmed"] for s in item["pii"])
    res = client.post("/api/ops/bulk/pii", json={"ids": [item_id, "T-9999"]}).json()
    assert res["done"] == [item_id] and "T-9999" in res["errors"]
    # 一括では本文を見ていないので、氏名かもしれない語もマスクして送る
    assert "山田花子" not in settle(client, item_id)["item"]["sent_text"]
    assert client.get("/api/ops/items/T-9999").status_code == 404


def test_staff_id_none_is_rejected(client: TestClient) -> None:
    settings = client.get("/api/ops/settings").json()
    for bad in ("none", "a b"):
        staff = [*settings["staff"], {"id": bad, "name": "x", "role": "", "scope": ""}]
        assert client.put("/api/ops/settings", json={**settings, "staff": staff}).status_code == 422


def test_pii_draft_is_kept_and_used_by_bulk(client: TestClient) -> None:
    item_id = ingest(client, "電話は 090-0000-1234 です。担当の件でご相談です")
    item = settle(client, item_id)["item"]
    assert item["status"] == "pii_review" and item["pii_draft"] is None
    # 人が「担当」を個人情報として追加した下書きを保存する（確定はしない）
    start = item["text"].index("担当")
    added = {
        "start": start,
        "end": start + 2,
        "type": "person_name",
        "text": "担当",
        "source": "human",
        "confirmed": True,
    }
    res = client.put(f"/api/ops/items/{item_id}/pii/draft", json={"spans": [*item["pii"], added]})
    assert res.status_code == 200, res.text
    # 画面を離れて読み直しても下書きが残っている
    kept = client.get(f"/api/ops/items/{item_id}").json()["item"]
    assert kept["status"] == "pii_review" and len(kept["pii_draft"]) == len(item["pii"]) + 1
    # まとめて処理すると下書きの判断が使われ、処理後は下書きが消える
    assert client.post("/api/ops/bulk/pii", json={"ids": [item_id]}).json()["done"] == [item_id]
    done = settle(client, item_id)["item"]
    assert "【氏名】" in done["sent_text"] and "【電話番号】" in done["sent_text"]
    assert done["pii_draft"] is None
    # 確認待ちでない件には下書きを保存できない
    assert client.put(f"/api/ops/items/{item_id}/pii/draft", json={"spans": None}).status_code == 422


def test_pii_draft_can_be_cleared(client: TestClient) -> None:
    item_id = ingest(client, "電話は 090-0000-5678 です")
    item = settle(client, item_id)["item"]
    unchecked = [{**s, "confirmed": False} for s in item["pii"]]
    client.put(f"/api/ops/items/{item_id}/pii/draft", json={"spans": unchecked})
    res = client.put(f"/api/ops/items/{item_id}/pii/draft", json={"spans": None})
    assert res.json()["pii_draft"] is None


def test_guard_rules_only_skips_model_and_masks_candidates(client: TestClient) -> None:
    settings = client.get("/api/ops/settings").json()
    settings["guard"]["use_model"] = False
    settings["guard"]["human_check"] = False
    assert client.put("/api/ops/settings", json=settings).status_code == 200
    item_id = ingest(client, "山田花子と申します。電話は 090-0000-4321 です")
    detail = settle(client, item_id)
    item = detail["item"]
    # 氏名の候補も確かめずに個人情報として扱い、マスクして送る
    assert item["pii_leftover"] is None
    assert all(s["confirmed"] and s["score"] is None for s in item["pii"])
    assert "山田花子" not in item["sent_text"] and "【電話番号】" in item["sent_text"]
    assert any("規則だけで判定" in e["message"] for e in detail["events"])


def test_bulk_assign_can_unassign(client: TestClient) -> None:
    configure(client, guard__human_check=False, classify__auto_threshold=1.0, classify__review_threshold=1.0)
    configure(client, assign={"auto": False, "threshold": 0.7, "use_examples": True, "max_examples": 3})
    ids = [escalate_one(client, f"至急の件 {n}")["id"] for n in range(2)]
    assert client.post("/api/ops/bulk/assign", json={"ids": ids, "assignee": "sato"}).json()["done"] == ids
    # 空文字で担当を外す
    assert client.post("/api/ops/bulk/assign", json={"ids": ids[:1], "assignee": ""}).json()["done"] == ids[:1]
    assert client.get(f"/api/ops/items/{ids[0]}").json()["item"]["assignee"] is None
    assert client.get(f"/api/ops/items/{ids[1]}").json()["item"]["assignee"] == "sato"


def test_slack_inbound_reaches_jev_only_masked(client: TestClient) -> None:
    """Slack から受けた書き込みが、個人情報を伏せた本文だけで仕分けに回ることを通しで確かめる。"""
    from jevlab.ops.slack import SlackConnector

    configure(client, guard__human_check=False)
    settings = client.get("/api/ops/settings").json()
    settings["connectors"]["slack"] = True
    settings["slack"]["inbound_channels"] = ["C0INBOUND01"]
    assert client.put("/api/ops/settings", json=settings).status_code == 200

    pipeline: Pipeline = app.state.ops
    seen: list[tuple[str, str]] = []
    original = pipeline._ask

    async def spy(t: Any, app_name: str, state: object, questions: Any) -> Any:
        seen.append((app_name, json.dumps(state, ensure_ascii=False, default=str)))
        return await original(t, app_name, state, questions)

    pipeline._ask = spy  # type: ignore[method-assign]
    handlers: list[Any] = []

    class Handle:
        def close(self) -> None: ...

        def is_connected(self) -> bool:
            return True

    def factory(on_message: Any) -> Handle:
        handlers.append(on_message)
        return Handle()

    conn = SlackConnector(pipeline, None, factory, bot_user="UBOT")
    assert client.portal is not None
    client.portal.call(conn.tick)
    handlers[0](
        {
            "type": "message",
            "channel": "C0INBOUND01",
            "user": "U1",
            "text": "箱が潰れていました。電話は 090-1234-5678 です",
            "ts": "9.0",
        }
    )
    deadline = time.monotonic() + 5
    items: list[dict[str, Any]] = []
    while time.monotonic() < deadline and not items:
        items = [i for i in client.get("/api/ops/items").json() if i["channel"] == "slack"]
        time.sleep(0.05)
    assert items, "Slack からの件が受付箱に届いていない"
    detail = settle(client, items[0]["id"])
    assert "【電話番号】" in (detail["item"]["sent_text"] or "")
    classify = [s for a, s in seen if a == "ops-classify"]
    assert classify and all("090-1234-5678" not in s for s in classify)


def test_miss_report_records_span_without_text(client: TestClient) -> None:
    configure(client, guard__human_check=False)
    body = "インスタは hana0503 です。注文の件で相談です。"
    item_id = ingest(client, body)
    item = settle(client, item_id)["item"]
    assert item["status"] in {"review", "escalated", "routed"}
    start = item["text"].index("hana0503")
    end = start + len("hana0503")
    res = client.post(f"/api/ops/items/{item_id}/miss", json={"type": "sns_account", "start": start, "end": end})
    assert res.status_code == 200, res.text
    miss = res.json()
    assert (miss["start"], miss["end"], miss["length"]) == (start, end, 8)
    assert miss["leftover"] == item["pii_leftover"]
    # 個人情報そのものは記録に残さない
    events = client.get(f"/api/ops/items/{item_id}").json()["events"]
    recorded = [e for e in events if e["kind"] == "miss"]
    assert len(recorded) == 1 and "hana0503" not in json.dumps(recorded, ensure_ascii=False)
    # 同じ範囲は二重に数えない・本文の外は弾く
    again = client.post(f"/api/ops/items/{item_id}/miss", json={"type": "sns_account", "start": start, "end": end})
    assert again.status_code == 409
    outside = client.post(f"/api/ops/items/{item_id}/miss", json={"type": "phone", "start": 0, "end": 9999})
    assert outside.status_code == 422
    summary = client.get("/api/ops/misses", params={"catch": 0.8}).json()
    assert summary["reports"] == 1 and summary["by_type"] == {"sns_account": 1}


def test_miss_report_needs_item_sent_to_jev(client: TestClient) -> None:
    configure(client, guard__human_check=False)
    item_id = ingest(client, "カード 4111-1111-1111-1111 で二重に引き落とされました。")
    settle(client, item_id)  # ブロックして Jev には送っていない
    res = client.post(f"/api/ops/items/{item_id}/miss", json={"type": "phone", "start": 0, "end": 3})
    assert res.status_code == 422


def test_reminder_must_be_shorter_than_sla(client: TestClient) -> None:
    settings: dict[str, Any] = client.get("/api/ops/settings").json()
    settings["sla"]["hours"] = 1
    settings["slack"]["reminder_before_min"] = 60
    res = client.put("/api/ops/settings", json=settings)
    assert res.status_code == 422 and "対応目安" in res.json()["detail"]
    settings["slack"]["reminder_before_min"] = 59
    assert client.put("/api/ops/settings", json=settings).status_code == 200


def test_reported_miss_is_masked_in_titles_and_assign_examples(client: TestClient) -> None:
    from jevlab.ops.pipeline import safe_title

    configure(
        client,
        guard__human_check=False,
        assign={"auto": False, "threshold": 0.9, "use_examples": True, "max_examples": 3},
    )
    item_id = ingest(client, "インスタは hana0503 です。注文の件で相談です。")
    item = settle(client, item_id)["item"]
    start = item["text"].index("hana0503")
    res = client.post(f"/api/ops/items/{item_id}/miss", json={"type": "sns_account", "start": start, "end": start + 8})
    assert res.status_code == 200, res.text
    pipeline: Pipeline = client.app.state.ops  # type: ignore[attr-defined]
    stored = pipeline.store.get(item_id)
    assert [(s.start, s.type, s.source, s.confirmed) for s in stored.pii] == [(start, "sns_account", "human", True)]
    assert "hana0503" not in safe_title(stored)
    # 担当者が完了した件は、担当の推定の例として Jev に送る。報告した箇所は伏せる
    staff = client.get("/api/ops/settings").json()["staff"][0]["id"]
    assert client.post(f"/api/ops/items/{item_id}/assign", json={"assignee": staff}).status_code == 200
    assert client.post(f"/api/ops/items/{item_id}/close", json={"category": "inquiry"}).status_code == 200
    examples = pipeline._assign_examples(pipeline.store.settings())
    assert examples[staff] and all("hana0503" not in t for t in examples[staff])


class FakeScopeGenerator:
    def __init__(self, structured: dict[str, object] | None) -> None:
        self.structured = structured
        self.requests: list[GenerateRequest] = []

    async def generate(self, req: GenerateRequest) -> GeneratedText:
        self.requests.append(req)
        return GeneratedText("", self.structured, "claude-test", 5.0, 0.001, 10, 5)


def test_scope_draft_uses_masked_titles_of_handled_items(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(client, guard__human_check=False, classify__auto_threshold=1.0, classify__review_threshold=1.0)
    configure(
        client, assign={"auto": False, "threshold": 0.7, "use_examples": True, "max_examples": 3, "scope_draft_min": 2}
    )
    fake = FakeScopeGenerator({"scope": "返金・二重請求の確認", "notes": ["請求の件が多い"]})
    monkeypatch.setattr(ops_api, "make_generator", lambda model: fake)
    first = escalate_one(client, "至急、二重に請求されています")["id"]
    # 1 件だけでは案を作れない
    client.post(f"/api/ops/items/{first}/assign", json={"assignee": "takahashi"})
    client.post(f"/api/ops/items/{first}/close", json={"category": "complaint"})
    assert client.post("/api/ops/staff/takahashi/scope-draft", json={}).status_code == 422
    second = escalate_one(client, "至急、090-1234-5678 まで返金の件で連絡ください")["id"]
    client.post(f"/api/ops/items/{second}/assign", json={"assignee": "takahashi"})
    client.post(f"/api/ops/items/{second}/close", json={"category": "complaint"})
    assert client.get("/api/ops/assignment").json()["handled_by_staff"]["takahashi"] == 2
    res = client.post("/api/ops/staff/takahashi/scope-draft", json={})
    assert res.status_code == 200, res.text
    assert res.json()["scope"] == "返金・二重請求の確認" and res.json()["based_on"] == 2
    prompt = fake.requests[0].prompt
    # 渡すのは伏せ字にした見出しと分類だけ（電話番号は伏せる）
    assert "高橋" in prompt and "[クレーム]" in prompt and "090-1234-5678" not in prompt
    assert client.post("/api/ops/staff/nobody/scope-draft", json={}).status_code == 404


def test_scope_draft_reports_bad_generator_output(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(client, guard__human_check=False, classify__auto_threshold=1.0, classify__review_threshold=1.0)
    configure(
        client, assign={"auto": False, "threshold": 0.7, "use_examples": True, "max_examples": 3, "scope_draft_min": 1}
    )
    monkeypatch.setattr(ops_api, "make_generator", lambda model: FakeScopeGenerator({"notes": []}))
    item = escalate_one(client, "至急の件")["id"]
    client.post(f"/api/ops/items/{item}/assign", json={"assignee": "sato"})
    client.post(f"/api/ops/items/{item}/close", json={"category": "other"})
    assert client.post("/api/ops/staff/sato/scope-draft", json={}).status_code == 502


def test_escalation_close_is_posted_to_the_escalation_channel_with_notes(client: TestClient) -> None:
    configure(client, guard__human_check=False, classify__auto_threshold=1.0, classify__review_threshold=1.0)
    item_id = ingest(client, "至急連絡ください")
    assert settle(client, item_id)["item"]["status"] == "escalated"
    client.post(f"/api/ops/items/{item_id}/note", json={"text": "電話済み"})
    client.post(f"/api/ops/items/{item_id}/close", json={"category": "complaint"})
    done = [p for p in client.get("/api/ops/posts").json() if p["author"] == "担当者（対応完了）"]
    assert [p["channel"] for p in done] == ["#cs-エスカレーション"]
    assert done[0]["text"].splitlines()[-2:] == ["メモ:", "・電話済み"]
