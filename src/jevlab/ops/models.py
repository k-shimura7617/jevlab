"""運用（受付箱）の型と設定。"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from jevlab.core.engine import AnswerView
from jevlab.core.target import Target
from jevlab.ops.pii import DEFAULT_POLICY, Action, PiiType, Span

# slack は実際の Slack ワークスペース（Socket Mode）からの受信。chat は画面上の疑似チャット
Channel = Literal["mail", "chat", "csv", "api", "slack"]
CHANNELS: Final[tuple[Channel, ...]] = get_args(Channel)
CHANNEL_LABELS: Final[dict[Channel, str]] = {
    "mail": "メール（support@komorebi.example）",
    "chat": "チャット（#お問い合わせ窓口）",
    "csv": "CSV 取り込み",
    "api": "API",
    "slack": "Slack（実際のワークスペース）",
}

# 処理の段階。queued → processing →（pii_review）→ review / escalated / routed → closed
Status = Literal["queued", "processing", "pii_review", "review", "escalated", "routed", "closed", "error"]
STATUSES: Final[tuple[Status, ...]] = get_args(Status)

# 分類の出どころ（Kev だけで確定したか、Jev か、人が決めたか）
Decider = Literal["jev", "kev", "mock", "human"]
PiiDecision = Literal["none", "masked", "blocked", "allowed", "skipped"]
EventKind = Literal[
    "received",
    "guard",
    "pii_review",
    "classify",
    "route",
    "review",
    "escalate",
    "assign",
    "note",
    "close",
    "audit",
    "error",
    "retry",
    "miss",
]
Actor = Literal["system", "kev", "jev", "mock", "human", "connector"]


class KevReference(BaseModel):
    """ブロックした件の参考の判定（Kev）。人が仕分けるときの手がかりとして出すだけ。"""

    category: str | None = None
    confidence: float | None = None
    assign_suggestion: str | None = None
    assign_probability: float | None = None
    model: str = ""


class Item(BaseModel):
    """受付箱の 1 件（チケット）。"""

    id: str
    seq: int
    channel: Channel
    from_name: str
    from_address: str
    subject: str
    body: str
    received_at: str
    status: Status = "queued"
    # 自動振り分け・確認待ち・エスカレーションになった理由（画面に出す）
    reason: str | None = None
    # 最初の振り分け結果（人が確定した後も、自動処理率の集計に使う）
    first_route: Literal["routed", "review", "escalated"] | None = None
    # 判定に使った文（件名＋本文）。個人情報の位置はこの文字列の中の位置
    text: str
    pii: list[Span] = []
    # 候補以外に個人情報が残っている確率（全文に対する判定）
    pii_leftover: float | None = None
    pii_decision: PiiDecision | None = None
    # 個人情報の確認で人が編集中の内容（未確定の下書き）。画面を離れても残し、まとめて処理するときもこれを使う
    pii_draft: list[Span] | None = None
    # Jev に送った文（マスク後）。送っていなければ None
    sent_text: str | None = None
    answers: dict[str, AnswerView] = {}
    category: str | None = None
    # 仕分けたときの分類の定義の版（Settings.categories_version）。無いのは分類を編集できるようになる前の件
    category_version: str | None = None
    decided_by: Decider | None = None
    confidence: float | None = None
    # 抽出したチケット項目（注文番号など）。キーは項目 ID
    fields: dict[str, str | None] = {}
    # 担当者の ID（StaffMember.id）
    assignee: str | None = None
    assigned_by: Literal["auto", "human"] | None = None
    # 一度でも自動で割り当てたか（人が変えた・外した後も残し、自動割り当ての実績を数える）
    auto_assigned: bool = False
    # 確率が閾値に届かず、仮で割り当てたか（人が割り当て直すと外れる）
    assign_provisional: bool = False
    # Jev が推定した担当者（確信度が低くて割り当てなかった場合も残し、推定の当たり具合を測る）
    assign_suggestion: str | None = None
    # 推定した担当の確率（名前は以前のまま。確信度は担当者の人数で意味が変わるため、確率を入れる）
    assign_confidence: float | None = None
    # 実際の Slack に投稿した件の親メッセージ（返信をこのスレッドに付ける）
    slack_channel: str | None = None
    slack_ts: str | None = None
    # 親のほかに Slack に出した投稿（分類の修正・完了の投稿など）。完了したら親と一緒に ✅ を付ける
    slack_more: list[tuple[str, str]] = []
    # 親の投稿を送りかけた（応答が返らず、投稿できたか分からない）。やり直す前に Slack 側を確かめる
    slack_parent_pending: bool = False
    # 担当についての最初の返信を送ったか（親と返信を別々に記録し、やり直しで二重に書かない）
    slack_notified: bool = False
    # 対応目安の前の知らせを送ったか（1 件につき 1 回だけ）
    slack_reminded: bool = False
    # 個人情報の方針で Jev に送らなかった件を、Kev（ローカル）で参考に判定した結果。自動の振り分け・割り当てには使わない
    kev_reference: KevReference | None = None
    notes: list[str] = []
    # 自動で振り分けた分から抜き取って人が確認する対象か
    audit: bool = False
    audit_result: Literal["ok", "fixed"] | None = None
    cost_usd: float = 0.0
    error: str | None = None
    # デモ用の想定ラベル、または取り込んだ過去の分類（評価・試算との照合用）
    expected: dict[str, Any] | None = None
    # 過去の問い合わせとして取り込んだ件（導入前の試算用）。投稿・人の対応には回さず、運用の集計にも入れない
    backfill: bool = False
    updated_at: str
    closed_at: str | None = None
    # 返信のいらない分類として自動で完了にした（人は見ていない）
    auto_closed: bool = False


class Event(BaseModel):
    id: int
    item_id: str
    at: str
    kind: EventKind
    actor: Actor
    message: str
    data: dict[str, Any] = {}


class Post(BaseModel):
    """疑似 Slack のチャンネルへの投稿。"""

    id: int
    channel: str
    at: str
    author: str
    text: str
    item_id: str | None = None
    fields: dict[str, str | None] = {}


# 検知漏れを報告できる状態（ガードレールを通って Jev に送った後）
MISS_STATUSES: Final[tuple[Status, ...]] = ("review", "escalated", "routed", "closed")


class MissReport(BaseModel):
    """ガードレールが見逃した個人情報の報告。

    本文はすでに Jev に送っているので取り消せない。改善（閾値の調整）に使うため、
    個人情報そのものは保存せず、種類・位置（受信した本文 Item.text の中の文字位置）・長さと、
    報告した時点の「候補以外に残っている確率」を残す。
    """

    id: int
    item_id: str
    type: PiiType
    start: int
    end: int
    length: int
    leftover: float | None
    at: str


# 個人情報の判定は、マスク前の本文を読むため外部（Jev）には送らない
GuardTarget = Literal["custom", "mock"]


class GuardSettings(BaseModel):
    # off にすると個人情報のチェック自体を行わず、元の本文のまま Jev に送る（デモ用。通常は on）
    enabled: bool = True
    # Kev（モデル）で判定するか。off なら規則だけで判定する（速いが、氏名の候補はすべて個人情報として扱う）
    use_model: bool = True
    # 個人情報の判定に使う接続先。閉域を想定して既定は Kev
    target: GuardTarget = "custom"
    # 検出したら人が確認するか（off なら方針に従って自動でマスク・ブロックする）
    human_check: bool = True
    # 見逃しの方が重いため低め（Kev の確率は本物の氏名でも 0.4〜0.7 程度に留まることがある）
    candidate_threshold: float = Field(0.3, ge=0, le=1)
    leftover_threshold: float = Field(0.5, ge=0, le=1)
    policy: dict[PiiType, Action] = dict(DEFAULT_POLICY)
    # ブロックした件の扱い: Kev だけで仕分ける / 人に回す
    blocked_route: Literal["kev", "human"] = "human"

    @field_validator("policy")
    @classmethod
    def _complete_policy(cls, v: dict[PiiType, Action]) -> dict[PiiType, Action]:
        # 一部の種類だけ送られても、抜けた種類が「許可」扱いにならないよう既定の方針で補う
        return {**DEFAULT_POLICY, **v}


class ClassifySettings(BaseModel):
    target: Target = "jev"
    auto_threshold: float = Field(0.9, ge=0, le=1)
    review_threshold: float = Field(0.5, ge=0, le=1)
    # 分類ごとに自動振り分けの閾値を上書きする（閾値の調整画面で反映する）
    label_thresholds: dict[str, float] = {}
    escalate_strong_frustration: bool = True
    # 「強い不満」の確率 P(不満度 2) がこれ以上ならエスカレーション（四捨五入した段階では、割れた件を見逃すため）
    strong_frustration_at: float = Field(0.35, ge=0, le=1)
    escalate_urgent: bool = True
    # 分類の上位 2 つの確率の差がこれ未満なら、確信度にかかわらず人が確認する（判断が割れている）
    split_margin: float = Field(0.1, ge=0, le=1)
    # 「本文だけでは種類を決める情報が足りない」の確率がこれ以上なら、自動にせず人が確認する。
    # Jev は「当てはまる」に寄りやすいので、問題の側を yes にした問いにして安全側に倒す
    insufficient_gate: bool = True
    insufficient_at: float = Field(0.6, ge=0, le=1)

    @field_validator("label_thresholds")
    @classmethod
    def _check_label_thresholds(cls, v: dict[str, float]) -> dict[str, float]:
        bad = {k: t for k, t in v.items() if not 0 <= t <= 1}
        if bad:
            raise ValueError(f"分類ごとの閾値は 0〜1 で指定してください: {bad}")
        return v


class StaffMember(BaseModel):
    # 担当者の推定では ID を選択肢のキーに使う。"none" は「該当なし」の選択肢と衝突するため使えない
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=60)
    role: str = Field("", max_length=60)
    # 担当範囲の説明。Jev はこれを読んで担当者を選ぶため、担当者どうしで重ならないように書く
    scope: str = Field("", max_length=300)
    # Slack のユーザー ID（U…）。あればエスカレーションのスレッドでメンションする
    slack_user_id: str = Field("", pattern=r"^$|^[UW][A-Z0-9]{6,20}$")
    # 担当するか。オフの人は一覧に残したまま、推定の選択肢・割り当て・振り分け担当（当番）から外す
    active: bool = True


MAX_STAFF: Final = 100

DEFAULT_STAFF: Final[list[StaffMember]] = [
    StaffMember(
        id="sato",
        name="佐藤",
        role="CS リーダー",
        scope="強い不満・返金や補償の強い要求・SNS や消費者センターへの言及など、会社として判断が要るクレーム",
    ),
    StaffMember(
        id="suzuki",
        name="鈴木",
        role="配送・在庫",
        scope="配送の遅れ・誤配送・届かない、在庫や納期の確認、発送前の注文内容の変更やキャンセル",
    ),
    StaffMember(id="takahashi", name="高橋", role="経理", scope="請求・支払い・二重決済・返金の手続き・領収書"),
    StaffMember(
        id="tamura",
        name="田村",
        role="品質管理",
        scope="商品の破損・欠け・不良、名入れの誤りや仕上がり、品質についての問い合わせ",
    ),
]


class AssignSettings(BaseModel):
    """エスカレーションの担当者の自動割り当て。"""

    # 確信度が閾値以上のときだけ自動で割り当てる（それ以外は推定を表示して人が決める）
    auto: bool = True
    # 推定した担当の確率がこれ以上なら自動で割り当てる
    threshold: float = Field(0.7, ge=0, le=1)
    # 担当者ごとに、最近対応を完了した件を判定の例として添える（人の割り当てから精度を上げる）
    use_examples: bool = True
    max_examples: int = Field(3, ge=0, le=10)
    # 人が割り当てて完了した件がこの数に達した担当者は、担当範囲の案を Claude に作らせられる
    scope_draft_min: int = Field(10, ge=1, le=200)


class KevFirstSettings(BaseModel):
    """B: Kev の確信度が十分なら Jev を呼ばずに確定する。"""

    enabled: bool = False
    threshold: float = Field(0.95, ge=0, le=1)


class SlaSettings(BaseModel):
    """エスカレーションの対応目安。営業時間だけを数える（昼休み・夜間・休日は進まない）。"""

    # 対応目安（営業時間）。既定の 9 時間は、平日 9:00〜18:00 の 1 営業日
    hours: float = Field(9.0, gt=0, le=100)
    # 営業日（0=月 … 6=日）
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], max_length=7)
    start: str = Field("09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field("18:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "Asia/Tokyo"
    # 休業日（YYYY-MM-DD）。祝日などを必要な分だけ入れる
    holidays: list[Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]] = Field(
        default_factory=list, max_length=100
    )

    @field_validator("days")
    @classmethod
    def _check_days(cls, v: list[int]) -> list[int]:
        if not v or any(not 0 <= d <= 6 for d in v):
            raise ValueError("営業日は 0（月）〜 6（日）で 1 日以上選んでください")
        return sorted(set(v))

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"タイムゾーン {v!r} が見つかりません") from e
        return v

    @model_validator(mode="after")
    def _check_hours(self) -> SlaSettings:
        if self.start >= self.end:
            raise ValueError("営業時間の開始は終了より前にしてください")
        return self


# Slack のチャンネル ID（公開 C…／非公開 G…）
_SLACK_CHANNEL_ID = r"^[CG][A-Z0-9]{6,20}$"


class SlackSettings(BaseModel):
    """実際の Slack とのつなぎ込み。トークンは環境変数（SLACK_BOT_TOKEN / SLACK_APP_TOKEN）から読む。"""

    # 振り分け・エスカレーションの投稿を、実際の Slack にも流す
    outbound: bool = False
    # 疑似チャンネル名（#cs-… など）→ 投稿先の Slack チャンネル ID。載っていないチャンネルは流さない
    channel_map: dict[str, Annotated[str, StringConstraints(pattern=_SLACK_CHANNEL_ID)]] = {}
    # 受信する Slack のチャンネル ID（ここに届いたメッセージを受付箱に取り込む）
    inbound_channels: list[Annotated[str, StringConstraints(pattern=_SLACK_CHANNEL_ID)]] = Field(
        default_factory=list, max_length=20
    )
    # 振り分け担当（当番）の担当者 ID。担当が決まっていない件はこの人をメンションする
    dispatcher: str | None = None
    # 投稿に付ける画面へのリンクの起点（開発中は Vite の 5173、ビルド版なら 8000）
    app_url: str = Field("http://127.0.0.1:5173", pattern=r"^https?://\S+$", max_length=200)
    # 担当が決まらないまま対応目安が近づいたら、振り分け担当に 1 回だけ知らせる（営業時間の分で数える）
    reminder: bool = True
    reminder_before_min: int = Field(60, ge=1, le=6000)


class SimulatorSettings(BaseModel):
    playing: bool = False
    interval_s: float = Field(4.0, ge=0.5, le=60)
    cursor: int = 0
    # 高速: 間隔なしで残りを一度に受信し、処理の並列数を上げる（Kev を使わない構成向け）
    fast: bool = False


# ---- 分類とチャンネル ----

_DEFAULT_CHANNELS: Final[dict[str, str]] = {
    "inquiry": "#cs-問い合わせ",
    "complaint": "#cs-クレーム",
    "thanks": "#cs-お礼",
    "other": "#cs-その他",
}
MAX_ACTIVE_CATEGORIES: Final = 9


class CategoryDef(BaseModel):
    """運用の分類。キーは作った後に変えない（過去の件はキーでつながる。名前は変えてよい）。"""

    key: str = Field(min_length=1, max_length=30, pattern=r"^[a-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=30)
    # Jev が読む説明（どういう問い合わせをこの分類にするか）
    criteria: str = Field(min_length=1, max_length=300)
    # 振り分け先の疑似チャンネル（Slack の割り当てはこの名前で決める）
    channel: str = Field(min_length=2, max_length=40, pattern=r"^#\S+$")
    # 廃止した分類は、仕分けの選択肢・確認のボタン・完了の選択から外す（過去の件の表示には残す）
    active: bool = True
    # 返信のいらない分類（お礼など）。自動で振り分けた件は、投稿したうえで自動で完了にする
    auto_close: bool = False

    # 返信が要るかを件ごとに Jev に判定させる分類（その他など、返信の要るものと要らないものが混ざる）。
    # 要らないと判定した件は、返信不要の分類と同じく自動で完了にする
    judge_reply: bool = False

    @model_validator(mode="before")
    @classmethod
    def _defaults_for_saved_settings(cls, data: object) -> object:
        # 項目を足す前に保存した設定では項目がない。お礼は返信不要、その他は返信の要否を判定として読む
        if not isinstance(data, dict):
            return data
        if "auto_close" not in data and data.get("key") == "thanks":
            data = {**data, "auto_close": True}
        if "judge_reply" not in data and data.get("key") == "other":
            data = {**data, "judge_reply": True}
        return data


def _default_categories() -> list[CategoryDef]:
    from jevlab.apps.mail.questions import CATEGORY_LABELS, QUESTIONS

    criteria = QUESTIONS["category"].criteria
    if not isinstance(criteria, dict):
        raise TypeError("メール仕分けの分類の説明が、キーごとの形ではありません")
    return [
        CategoryDef(
            key=k,
            label=label,
            criteria=str(criteria[k]),
            channel=_DEFAULT_CHANNELS[k],
            auto_close=k == "thanks",
            judge_reply=k == "other",
        )
        for k, label in CATEGORY_LABELS.items()
    ]


class Settings(BaseModel):
    # 分類の一覧と、どれにも当てはまらないときの受け皿（Jev が想定外の答えを返したときの振り分け先）
    categories: list[CategoryDef] = Field(default_factory=_default_categories)
    fallback_category: str = "other"

    guard: GuardSettings = GuardSettings()
    classify: ClassifySettings = ClassifySettings()
    kev_first: KevFirstSettings = KevFirstSettings()
    audit_rate: float = Field(0.05, ge=0, le=1)
    # slack は既定で切断（トークンを設定し、コネクタ画面で接続してから使う）
    connectors: dict[Channel, bool] = {"mail": True, "chat": True, "csv": True, "api": True, "slack": False}
    slack: SlackSettings = SlackSettings()
    sla: SlaSettings = SlaSettings()
    simulator: SimulatorSettings = SimulatorSettings()
    # 優先度の重み（画面のスライダー。再判定なしで並び順だけ変わる）
    priority_weights: dict[str, float] = {"frustration": 1.0, "urgent": 1.5, "refund": 0.8, "publicity": 1.2}
    staff: list[StaffMember] = list(DEFAULT_STAFF)
    assign: AssignSettings = AssignSettings()

    def on_duty(self) -> list[StaffMember]:
        """担当がオンの担当者。"""
        return [s for s in self.staff if s.active]

    def dispatcher_member(self) -> StaffMember | None:
        """振り分け担当（当番）。担当がオフなら None（未設定と同じ扱い）。"""
        return next((s for s in self.on_duty() if s.id == self.slack.dispatcher), None)

    @field_validator("staff", mode="before")
    @classmethod
    def _upgrade_staff(cls, v: object) -> object:
        # 以前の形式（名前だけの一覧）で保存された設定を読み込めるようにする
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return [{"id": f"staff{i + 1}", "name": x} for i, x in enumerate(v)]
        return v

    @model_validator(mode="after")
    def _check_categories(self) -> Settings:
        keys = [c.key for c in self.categories]
        if len(keys) != len(set(keys)):
            raise ValueError("分類のキーが重複しています")
        if "none" in keys:
            raise ValueError("分類のキーに none は使えません")
        active = [c for c in self.categories if c.active]
        if not active:
            raise ValueError("使う分類を 1 つ以上にしてください")
        if len(active) > MAX_ACTIVE_CATEGORIES:
            raise ValueError(f"使う分類は {MAX_ACTIVE_CATEGORIES} 個までです（確認の画面の 1〜9 キーに合わせる）")
        if self.fallback_category not in {c.key for c in active}:
            raise ValueError("受け皿の分類は、使っている分類から選んでください")
        return self

    def active_categories(self) -> list[CategoryDef]:
        return [c for c in self.categories if c.active]

    def category_label(self, key: str | None) -> str:
        """分類の表示名（廃止した分類は「（廃止）」を付ける。知らないキーはそのまま）。"""
        c = next((c for c in self.categories if c.key == key), None)
        if c is None:
            return key or "-"
        return c.label if c.active else f"{c.label}（廃止）"

    def auto_closes(self, key: str | None) -> bool:
        """返信のいらない分類として、自動で振り分けた件を自動で完了にするか。"""
        return any(c.key == key and c.active and c.auto_close for c in self.categories)

    def judges_reply(self, key: str | None) -> bool:
        """返信が要るかを件ごとに判定する分類か。"""
        return any(c.key == key and c.active and c.judge_reply and not c.auto_close for c in self.categories)

    def route_channel(self, key: str | None) -> str:
        """分類の振り分け先。知らない分類は受け皿の分類のチャンネル。"""
        by_key = {c.key: c.channel for c in self.categories}
        return by_key.get(key or "", by_key[self.fallback_category])

    def categories_version(self) -> str:
        """仕分けに使う分類の定義の版（使う分類のキーと説明から作る）。説明を変えると別の版になる。"""
        import hashlib

        raw = "\n".join(f"{c.key}\t{c.criteria}" for c in self.active_categories())
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    @field_validator("staff")
    @classmethod
    def _unique_staff(cls, v: list[StaffMember]) -> list[StaffMember]:
        ids = [s.id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("担当者の ID が重複しています")
        if "none" in ids:
            raise ValueError("担当者の ID に none は使えません（「該当なし」と区別できなくなるため）")
        # 推定の選択肢（Choice は最大 255、うち 1 つは「該当なし」）に収まる人数に抑える
        if len(ids) > MAX_STAFF:
            raise ValueError(f"担当者は {MAX_STAFF} 人までです")
        return v


class IngestRequest(BaseModel):
    channel: Channel
    from_name: str = Field("", max_length=100)
    from_address: str = Field("", max_length=200)
    subject: str = Field("", max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    expected: dict[str, Any] | None = None
    # 過去の問い合わせとして取り込む（導入前の試算用）。仕分けまで行い、投稿・人の対応には回さずに完了にする
    backfill: bool = False
    # 過去の問い合わせの元の受信日時（ISO 8601）。backfill のときだけ使う
    received_at: str | None = Field(None, max_length=40)
