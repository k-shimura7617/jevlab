# jevlab ドキュメント

jevlab は、TypeSafe の System One モデル **Jev**（と互換のローカルサーバ **Kev**）を実際の業務の形で試すための検証アプリです。
「評価ダッシュボード」（Jev / Kev / モックの判定を同じデータで比べる）と、「運用ダッシュボード」（問い合わせの受付箱を、個人情報のガード → 仕分け → 人の確認まで通す商用デモ）、「ツール」（言い方チェック）の 3 つで構成しています。

このディレクトリは Markdown を正本とし、HTML は `scripts/build_docs.py` で生成します（[HTML の作り方](#html-の作り方)）。

## 読む順番

### 設計の考え方を知りたい人（開発者本人・レビュアー）

1. [architecture.md](architecture.md) — 全体の構成、1 件の流れ、データ、API、安全の境界
2. [jev.md](jev.md) — Jev / TypeSafe の考え方と、このアプリで使った型・パターン
3. [adr/](adr/README.md) — 設計判断の記録（なぜそうしたか、何を捨てたか）
4. [research/](research/) — Jev の紹介動画などから得た知見と、改善・新機能の提案

### 操作を覚えたい人

- [walkthrough.md](walkthrough.md) — 画面の機能を項目ごとに、手順・確かめること・分かることの順で案内する手順書
- [slack-setup.md](slack-setup.md) — 実際の Slack とつなぐ手順（アプリの作成・トークン・チャンネル・画面の設定・動作の確認）

### 新しいセッション（別の Claude Code）で作業を引き継ぐ場合

1. [handoff.md](handoff.md) — 引き継ぎ資料。現在の状態、守るべき約束（秘密情報の扱い・課金・動作確認中のサーバに触れないこと）、進行中の作業と次の一手
2. [architecture.md](architecture.md) と [adr/](adr/README.md) — コードを読む前の地図
3. [jev.md](jev.md) — 質問（criteria）を書くときの約束ごと

## 一覧

| 文書 | 内容 | 主な読者 |
| --- | --- | --- |
| [architecture.md](architecture.md) | 構成要素、処理の流れ（図）、データモデル、API 一覧、設定、安全の境界、テスト方針 | 開発者・引き継ぎ |
| [jev.md](jev.md) | System One の考え方、Choice / Score / Noul、state と criteria の書き方、料金と上限、Kev 互換、使ったパターン | 開発者・引き継ぎ |
| [adr/](adr/README.md) | 設計判断の記録（MADR 形式） | 開発者・レビュアー |
| [walkthrough.md](walkthrough.md) | 画面操作の手順書 | 利用者 |
| [slack-setup.md](slack-setup.md) | 実際の Slack とつなぐ手順 | 利用者 |
| [handoff.md](handoff.md) | 別セッションへの引き継ぎ | 別セッションの Claude Code |
| [research/](research/) | 調査メモと提案 | 開発者 |

## ドキュメントの運用

- **正本は Markdown**。HTML は生成物なので直接編集しない（`docs/html/` は作り直すと上書きされる）。
- **アプリを修正したら、区切りのタイミングで該当する文書も更新する**。設計を変えた場合は、既存の ADR を書き換えずに新しい ADR を追加し、古い方の状態を「置き換え済み」にする。
- 図は Mermaid（テキスト）で書く。差分が読め、HTML では図として表示される。
- 秘密情報（`.env` の中身、API キー）や個人の情報は書かない。

## HTML の作り方

```bash
uv run --with markdown python scripts/build_docs.py
```

`docs/**/*.md` を `docs/html/` に変換します（ライト／ダーク対応の共通 CSS、`.md` へのリンクは `.html` に置き換え、Mermaid の図は表示時に描画）。
プロジェクトの依存関係は増やしません（`--with` で一時的に使うだけ）。生成後は `docs/html/index.html` をブラウザで開きます。
