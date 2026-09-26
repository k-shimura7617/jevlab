"""ファイル取り込みの大量の見本（100・500・1000 件）を作る。Jev の高速処理（並列）を見るためのもの。

すべて架空のデータ。個人情報は入れない（差出人は「お客様0001」、アドレスは example.com）。
ガードで止まらず Jev まで流れるよう、本文にも氏名・電話番号・住所は書かない。
同じ内容を作り直せるよう、乱数の種は固定する。

使い方: uv run python scripts/make_bulk_samples.py
"""

from __future__ import annotations

import csv
import io
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

OUT = Path(__file__).resolve().parent.parent / "docs" / "samples" / "import"
SIZES = (100, 500, 1000)
PRODUCTS = (
    "マグカップ",
    "豆皿",
    "ガラスのコップ",
    "木のトレー",
    "ブランケット",
    "ハンドタオル",
    "キャンドル",
    "花瓶",
    "箸置き",
    "ランチョンマット",
)

# 種別（取り込みの「過去の分類」に当たる）ごとの件名と本文のひな形。{p} は商品、{o} は注文番号
TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "問合せ": [
        ("{p}の在庫について", "{p}の在庫はいつ頃入りますか。入荷したら購入したいです。"),
        ("ラッピングについて", "{o} の注文ですが、プレゼント用のラッピングは追加できますか。"),
        ("配送日の指定", "{o} の配送日を来週の土曜日に指定できますか。"),
        ("支払い方法", "コンビニ払いは使えますか。{p}を購入したいです。"),
        ("{p}のサイズ", "{p}の大きさを教えてください。棚に入るか確かめたいです。"),
        ("領収書の発行", "{o} の領収書を発行してもらえますか。"),
        ("注文内容の変更", "{o} で注文した{p}の色を変更したいのですが、まだ間に合いますか。"),
        ("お手入れの方法", "{p}は食洗機で洗えますか。お手入れの方法を知りたいです。"),
    ],
    "苦情": [
        ("届いた{p}が割れていました", "{o} で届いた{p}が割れていました。交換をお願いします。写真も送れます。"),
        ("注文と違う商品", "{o} で{p}を注文したのに、別の商品が届きました。どうなっているのですか。"),
        ("まだ届きません", "{o} の{p}が、予定日を 3 日過ぎても届きません。いつ届くのか教えてください。"),
        ("二重に請求されています", "{o} の代金が二重に引き落とされています。すぐに確認してください。"),
        ("対応が遅い", "先週問い合わせたのに返事がありません。{o} の件、どうなっていますか。"),
        ("{p}に傷がありました", "届いた{p}に目立つ傷がありました。返品できますか。"),
        ("梱包がひどい", "{o} の箱が潰れていて、{p}の角が欠けていました。残念です。"),
    ],
    "お礼": [
        ("すてきな{p}でした", "届いた{p}がとてもすてきでした。家族も喜んでいます。ありがとうございました。"),
        ("丁寧な対応に感謝", "先日の交換の件、丁寧に対応していただきありがとうございました。"),
        ("プレゼントに喜ばれました", "{p}を贈り物にしたら、とても喜ばれました。また利用します。"),
        ("早い発送ありがとうございます", "{o} がすぐに届きました。梱包も丁寧でうれしかったです。"),
    ],
    "その他": [
        ("取材のお願い", "雑貨の特集で、お店を取材させていただけないでしょうか。"),
        ("仕入れのご提案", "当社の{p}をお取り扱いいただけないか、ご提案させてください。"),
        ("アンケートのお願い", "ネットショップの利用に関するアンケートにご協力いただけませんか。"),
        ("採用について", "店舗スタッフの募集はしていますか。"),
        ("システム保守のご案内", "ネットショップのシステム保守サービスのご案内です。"),
    ],
}
# 実際の問い合わせに近い割合（問合せが多く、その他は少なめ）
WEIGHTS = {"問合せ": 45, "苦情": 25, "お礼": 15, "その他": 15}
HEADER = ("受信日時", "差出人", "アドレス", "件名", "本文", "種別")


def rows(n: int, seed: int = 20260926) -> list[tuple[str, ...]]:
    rnd = random.Random(seed + n)
    start = datetime(2025, 7, 1, 9, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    kinds = list(WEIGHTS)
    out: list[tuple[str, ...]] = []
    for i in range(1, n + 1):
        kind = rnd.choices(kinds, weights=[WEIGHTS[k] for k in kinds])[0]
        subject, body = rnd.choice(TEMPLATES[kind])
        p = rnd.choice(PRODUCTS)
        o = f"KM-{2507 + i // 400:04d}{i:02d}-{rnd.randint(1, 9999):04d}"
        at = start + timedelta(minutes=37 * i + rnd.randint(0, 30))
        out.append(
            (
                at.strftime("%Y/%m/%d %H:%M"),
                f"お客様{i:04d}",
                f"customer{i:04d}@example.com",
                subject.format(p=p, o=o),
                body.format(p=p, o=o),
                kind,
            )
        )
    return out


def main() -> None:
    for n in SIZES:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(HEADER)
        w.writerows(rows(n))
        path = OUT / f"大量_{n}件.csv"
        # Excel で開けるよう BOM 付きの UTF-8 にする
        path.write_bytes(buf.getvalue().encode("utf-8-sig"))
        print(f"{path.relative_to(OUT.parent.parent.parent)}: {n} 件")


if __name__ == "__main__":
    main()
