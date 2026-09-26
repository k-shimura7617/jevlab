# 設計判断の記録（ADR）

このアプリの設計で「なぜそうしたか」「何を選ばなかったか」を残す。形式は [MADR](https://adr.github.io/madr/) にならう。

## 一覧

| 番号 | 判断 | 状態 |
| --- | --- | --- |
| [0001](0001-jev-for-judgment-claude-for-generation.md) | 判定は Jev、文章の生成は Claude に分ける | 採用 |
| [0002](0002-kev-pii-guardrail.md) | 個人情報のガードは Kev で行い、形で決まるものは規則で確定する | 採用 |
| [0003](0003-sqlite-store-async-worker.md) | 保存は SQLite、処理は同じプロセスの非同期ワーカーで行う | 採用 |
| [0004](0004-threshold-tuning-not-finetuning.md) | 精度は学習ではなく閾値の調整で上げる | 採用 |
| [0005](0005-candidate-selection-extraction.md) | 項目の抽出は「生成」ではなく「候補から選ばせる」 | 採用 |
| [0006](0006-priority-in-code.md) | 優先度は観点ごとに判定し、重み付けはコードと画面で行う | 採用 |
| [0007](0007-human-in-the-loop-queues.md) | 人の確認は目的別の 3 つの待ち行列と、抜き取り確認で行う | 採用 |
| [0008](0008-assignee-auto-assign-threshold.md) | 担当者は確信度が高いときだけ自動で割り当て、人の割り当てを例として学ばせる | 採用（閾値は確率で判定。→ 0017） |
| [0009](0009-claude-cli-generator.md) | 文章の生成は、生成器の型の裏で claude -p（サブスク枠）を使う | 採用 |
| [0010](0010-server-side-pii-draft.md) | 個人情報の確認の編集は、サーバ側に下書きとして保存する | 採用 |
| [0011](0011-single-repo-vite-build.md) | 画面は同じリポジトリの Vite ビルドを FastAPI から配信する | 採用 |
| [0012](0012-staging-copy-during-user-testing.md) | 利用者の動作確認中は、別のコピーで実装して区切りで反映する | 置き換え済み（→ 0023） |
| [0013](0013-slack-socket-mode.md) | 実際の Slack とのつなぎ込みは Socket Mode と同期 SDK で行う | 採用 |
| [0014](0014-close-routed-items-in-jevlab.md) | 振り分けた件も jevlab で完了にし、Slack にはスレッドの返信と ✅ で知らせる | 採用 |
| [0015](0015-import-existing-inquiries.md) | 既存の問い合わせのファイルを取り込み、過去分で導入前に試算する | 採用 |
| [0016](0016-guard-rules-only-mode.md) | 個人情報のガードに「規則だけ」のモードを用意する（Kev の ON/OFF） | 採用 |
| [0017](0017-confidence-semantics.md) | 確信度は Jev の公式の定義で扱い、判断が割れた件は人に回す | 採用 |
| [0018](0018-business-hours-sla-single-reminder.md) | 対応目安は営業時間で数え、担当未定の知らせは 1 件 1 回にする | 採用 |
| [0019](0019-kev-reference-for-blocked-items.md) | ブロックした件には、Kev の参考の判定を添える | 採用 |
| [0020](0020-measure-guard-with-reviews-and-miss-reports.md) | ガードの精度は人の確認の結果と検知漏れの報告で測る | 採用 |
| [0021](0021-sns-accounts-as-pii.md) | SNS のアカウント名とプロフィールの URL を個人情報として扱う | 採用 |
| [0022](0022-ops-dashboard-as-entry.md) | 運用ダッシュボードを入口にし、評価は /eval に移す | 採用 |
| [0023](0023-worktree-and-pr-workflow.md) | 並行の作業は worktree と PR で分ける | 採用 |
| [0024](0024-escalation-close-notice-in-thread-only.md) | エスカレーションの完了は、エスカレーションのスレッドだけで知らせる | 採用 |

## 運用のきまり

- 1 つの判断を 1 ファイルにし、番号は連番にする（`NNNN-短い名前.md`）。
- 採用した ADR は書き換えない。判断を変えるときは新しい ADR を追加し、古い方の状態を「置き換え済み（→ NNNN）」にする。
- 状態は次のいずれかにする: 提案 / 採用 / 却下 / 廃止 / 置き換え済み。
- コードの修正で判断が変わったときは、修正の区切りで ADR を追加・更新する。

## ひな形

```markdown
# NNNN. 判断の見出し（何をどうするか）

- 状態: 提案 | 採用 | 却下 | 廃止 | 置き換え済み（→ NNNN）
- 日付: YYYY-MM-DD
- 関連: 関連する ADR・文書へのリンク

## 背景と課題

何が問題で、なぜ判断が必要か。

## 判断の基準

何を重く見たか（安全・速さ・費用・運用の手間など）。

## 検討した選択肢

1. 選択肢 A
2. 選択肢 B

## 決定

選んだ選択肢と、その具体的な中身。

## 結果

- 良い点
- 悪い点（引き受けたこと・今後の見直し条件）
```
