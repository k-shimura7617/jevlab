from __future__ import annotations

import pytest

from jevlab.ops.pii import DEFAULT_POLICY, Span, apply_mask, blocked_types, detect, validate_span

TEXT = (
    "件名: 配送先の変更\n\n"
    "注文番号 KM-250914-0031 の配送先を変更したいです。\n"
    "〒100-0000 東京都架空区若葉町1-2-3、山田 花子宛てでお願いします。電話は090-0000-1234、"
    "メールは hanako@example.com です。\n"
    "娘の美咲へのプレゼントです。お客様センターの方にも伝えました。\n"
    "カード 4111-1111-1111-1111 で二重に3,300円引き落とされています。見本銀行 本店 普通 1234567 に返金してください。\n"
    "生年月日は1990年4月1日です。\n\n"
    "株式会社見本商事 総務部 伊藤 直樹"
)


def found(text: str) -> set[tuple[str, str]]:
    return {(s.type, s.text) for s in detect(text)}


def test_detect_finds_each_kind() -> None:
    assert found(TEXT) >= {
        ("postal_code", "〒100-0000"),
        ("address", "東京都架空区若葉町1-2-3"),
        ("person_name", "山田 花子"),
        ("phone", "090-0000-1234"),
        ("email", "hanako@example.com"),
        ("person_name", "美咲"),
        ("card", "4111-1111-1111-1111"),
        ("bank_account", "見本銀行 本店 普通 1234567"),
        ("birthday", "1990年4月1日"),
        ("person_name", "伊藤 直樹"),
    }


def test_detect_ignores_order_numbers_amounts_and_non_names() -> None:
    texts = {s.text for s in detect(TEXT)}
    assert not any("KM-250914-0031" in t for t in texts)
    assert "3,300円" not in texts
    # 「お客様」「こもれび雑貨店さん」は人名ではない
    assert found("お客様、こもれび雑貨店さんにご相談です。") == set()


def test_detect_does_not_split_phone_as_postal_code() -> None:
    assert found("電話は 090-0000-1234 です") == {("phone", "090-0000-1234")}


def test_detect_names_in_engraving_quotes_and_self_introduction() -> None:
    assert ("person_name", "佐々木 陽向") in found("名入れは「佐々木 陽向」でお願いします。")
    assert ("person_name", "鈴木一郎") in found("株式会社見本の鈴木一郎と申します。")
    assert ("person_name", "小林") in found("小林です、先日はありがとうございました。")


def test_overlapping_candidates_keep_the_more_specific_kind() -> None:
    spans = detect("連絡先 sato@example.com")
    assert [(s.type, s.text) for s in spans] == [("email", "sato@example.com")]


def test_apply_mask_follows_policy() -> None:
    spans = detect(TEXT)
    masked = apply_mask(TEXT, spans, DEFAULT_POLICY)
    assert "090-0000-1234" not in masked and "【電話番号】" in masked
    assert "hanako@example.com" not in masked
    assert "【氏名】宛て" in masked
    # カード・口座はブロックの方針なので、マスクではなく残したままにする（送らない判断は別で行う）
    assert "4111-1111-1111-1111" in masked
    assert blocked_types(spans, DEFAULT_POLICY) == ["card", "bank_account"]


def test_apply_mask_skips_unconfirmed_and_allowed() -> None:
    spans = [s.model_copy(update={"confirmed": s.type != "phone"}) for s in detect(TEXT)]
    policy = {**DEFAULT_POLICY, "email": "allow"}
    masked = apply_mask(TEXT, spans, policy)
    assert "090-0000-1234" in masked
    assert "hanako@example.com" in masked


def test_validate_span_rejects_out_of_range_and_normalizes_text() -> None:
    span = Span(start=0, end=3, type="person_name", text="ちがう", source="human")
    assert validate_span("山田太郎です", span).text == "山田太"
    with pytest.raises(ValueError, match="本文"):
        validate_span("短い", span.model_copy(update={"end": 10}))


def test_overlapping_spans_are_masked_as_one() -> None:
    spans = [
        Span(start=5, end=15, type="person_name", text="", source="human"),
        Span(start=10, end=20, type="phone", text=""),
    ]
    assert apply_mask("0123456789ABCDEFGHIJKL", spans, DEFAULT_POLICY) == "01234【氏名】KL"


def test_unknown_policy_type_is_masked() -> None:
    spans = detect("電話 090-0000-1234")
    assert "090-0000-1234" not in apply_mask("電話 090-0000-1234", spans, {})


def test_detect_full_width_and_other_number_styles() -> None:
    assert found("カード ４１１１－１１１１－１１１１－１１１１ です") == {
        ("card", "４１１１－１１１１－１１１１－１１１１")
    }
    assert found("Amex 3782-822463-10005") == {("card", "3782-822463-10005")}
    assert found("電話 ０９０－１２３４－５６７８") == {("phone", "０９０－１２３４－５６７８")}
    assert found("090 1234 5678 まで") == {("phone", "090 1234 5678")}
    assert found("03(1234)5678 へ") == {("phone", "03(1234)5678")}
    assert found("連絡先はtaro@example.comまで") == {("email", "taro@example.com")}


def _sns(text: str) -> list[str]:
    return [s.text for s in detect(text) if s.type == "sns_account"]


def test_sns_accounts_are_detected() -> None:
    assert _sns("Instagram の @kanako_0503 に DM をいただけますか。") == ["@kanako_0503"]
    assert _sns("インスタ（@hana.zakka）で見ました") == ["@hana.zakka"]
    assert _sns("プロフィールは https://x.com/taro_yamada です") == ["https://x.com/taro_yamada"]
    assert _sns("instagram.com/hanako.y と tiktok.com/@hanako_y") == ["instagram.com/hanako.y", "tiktok.com/@hanako_y"]
    assert _sns("https://www.threads.net/@komorebi_fan") == ["https://www.threads.net/@komorebi_fan"]


def test_sns_false_positives_are_avoided() -> None:
    # メールアドレス、注文番号、Slack のメンションの置き換え、SNS の話がない @、ふつうの URL
    assert _sns("連絡先は taro@example.com です（SNS はやっていません）") == []
    assert _sns("注文番号 KM-250926-0201 です。アカウントは作っていません") == []
    assert _sns("@ユーザー 箱が潰れていました") == []
    assert _sns("担当 @sato に回してください") == []
    assert _sns("会社のサイト https://komorebi.example/products/12 を見ました") == []
    assert _sns("https://www.example.com/x.com/abc の記事") == []
    # SNS の決まったページ（アカウントではない）と、「@500円」のような単価の書き方
    assert _sns("Instagram の https://www.instagram.com/explore とx.com/home を見ました") == []
    assert _sns("インスタで見た @500円 のセール") == []
