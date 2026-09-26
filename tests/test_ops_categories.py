from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_ops_api import configure, ingest, settle

from jevlab.ops import questions as oq
from jevlab.ops.models import CategoryDef, Settings
from jevlab.web import app

SHIPPING = {"key": "shipping", "label": "配送", "criteria": "配送の遅れ・届かない・届け先の変更", "channel": "#cs-配送"}


@pytest.fixture
def client(mock_env: Path) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _put_categories(client: TestClient, categories: list[dict[str, Any]], **extra: Any) -> Any:
    s = client.get("/api/ops/settings").json()
    s["categories"] = categories
    s.update(extra)
    return client.put("/api/ops/settings", json=s)


def test_settings_validation() -> None:
    base = Settings()
    with pytest.raises(ValidationError, match="重複"):
        Settings(categories=[*base.categories, base.categories[0]])
    with pytest.raises(ValidationError, match="1 つ以上"):
        Settings(categories=[c.model_copy(update={"active": False}) for c in base.categories])
    with pytest.raises(ValidationError, match="受け皿"):
        Settings(
            fallback_category="thanks",
            categories=[c.model_copy(update={"active": c.key != "thanks"}) for c in base.categories],
        )
    many = [CategoryDef(key=f"k{i}", label=f"分類{i}", criteria="説明", channel=f"#c{i}") for i in range(10)]
    with pytest.raises(ValidationError, match="9 個まで"):
        Settings(categories=many, fallback_category="k0")


def test_labels_channels_and_version() -> None:
    s = Settings()
    retired = s.model_copy(
        update={"categories": [c.model_copy(update={"active": c.key != "thanks"}) for c in s.categories]}
    )
    assert retired.category_label("thanks") == "お礼（廃止）" and retired.category_label("unknown") == "unknown"
    assert s.route_channel("nope") == "#cs-その他"
    # 説明を変えると版が変わる
    edited = s.model_copy(
        update={
            "categories": [
                c.model_copy(update={"criteria": "別の説明"}) if c.key == "other" else c for c in s.categories
            ]
        }
    )
    assert edited.categories_version() != s.categories_version()


def test_classify_question_uses_the_edited_categories() -> None:
    cats = [*Settings().active_categories(), CategoryDef.model_validate(SHIPPING)]
    q = oq.classify_questions({}, cats)["category"]
    assert isinstance(q.criteria, dict) and q.criteria["shipping"] == SHIPPING["criteria"]


def test_custom_category_is_offered_and_routed(client: TestClient) -> None:
    s = client.get("/api/ops/settings").json()
    assert _put_categories(client, [*s["categories"], SHIPPING]).status_code == 200
    meta = client.get("/api/ops/meta").json()
    assert meta["categories"]["shipping"] == "配送" and meta["route_channels"]["shipping"] == "#cs-配送"
    assert "#cs-配送" in client.get("/api/ops/slack").json()["mirrorable"]
    configure(client, guard__human_check=False, classify__auto_threshold=1.0, classify__review_threshold=0.0)
    item_id = ingest(client, "荷物が届きません")
    item = settle(client, item_id)["item"]
    assert item["status"] == "review" and item["category_version"]
    client.post(f"/api/ops/items/{item_id}/decide", json={"category": "shipping"})
    posts = client.get("/api/ops/posts").json()
    assert any(p["channel"] == "#cs-配送" and item_id in p["text"] for p in posts)


def test_retired_category_is_not_selectable_and_used_one_cannot_be_deleted(client: TestClient) -> None:
    s = client.get("/api/ops/settings").json()
    configure(client, guard__human_check=False, classify__auto_threshold=1.0, classify__review_threshold=0.0)
    item_id = ingest(client, "お礼を言いたくて")
    settle(client, item_id)
    retired = [dict(c, active=c["key"] != "thanks") for c in s["categories"]]
    assert _put_categories(client, retired).status_code == 200
    assert "thanks" not in client.get("/api/ops/meta").json()["categories"]
    assert client.post(f"/api/ops/items/{item_id}/decide", json={"category": "thanks"}).status_code == 422
    used = client.get(f"/api/ops/items/{item_id}").json()["item"]["category"]
    without = [c for c in s["categories"] if c["key"] != used]
    res = _put_categories(client, without, fallback_category=next(c["key"] for c in without))
    assert res.status_code == 422 and "廃止" in res.text


def test_renaming_a_channel_moves_the_slack_mapping(client: TestClient) -> None:
    s = client.get("/api/ops/settings").json()
    s["slack"]["channel_map"] = {"#cs-クレーム": "C0COMPLAIN1"}
    client.put("/api/ops/settings", json=s)
    renamed = [dict(c, channel="#cs-苦情") if c["key"] == "complaint" else c for c in s["categories"]]
    saved = _put_categories(client, renamed).json()
    assert saved["slack"]["channel_map"] == {"#cs-苦情": "C0COMPLAIN1"}


def test_tuning_counts_only_the_current_definition_by_default(client: TestClient) -> None:
    configure(client, guard__human_check=False)
    rows = [{"body": "在庫はありますか", "category": "inquiry"}, {"body": "壊れていました", "category": "complaint"}]
    ids = client.post("/api/ops/import", json={"backfill": True, "rows": rows}).json()["ids"]
    for i in ids:
        settle(client, i)
    assert client.get("/api/ops/tuning", params={"source": "expected"}).json()["n"] == 2
    s = client.get("/api/ops/settings").json()
    edited = [dict(c, criteria=c["criteria"] + "（改訂）") if c["key"] == "other" else c for c in s["categories"]]
    _put_categories(client, edited)
    assert client.get("/api/ops/tuning", params={"source": "expected"}).json()["n"] == 0
    assert client.get("/api/ops/tuning", params={"source": "expected", "all_versions": True}).json()["n"] == 2
