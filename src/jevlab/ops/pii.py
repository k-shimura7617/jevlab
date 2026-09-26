"""個人情報の候補を規則で拾い、方針に従ってマスクする。

候補を拾うのはコード、個人情報かどうかを決めるのはモデル（Kev）という分担にする。
規則は取りこぼしより拾いすぎを優先する（誤検出はモデルと人の確認で外せるが、拾わなかったものは誰も見ない）。
規則で拾えない氏名などは、全文に対する「候補以外に個人情報が残っているか」の判定と人の確認で補う。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Final, Literal, get_args

from pydantic import BaseModel

PiiType = Literal[
    "person_name", "phone", "email", "postal_code", "address", "card", "bank_account", "birthday", "sns_account"
]
PII_TYPES: Final[tuple[PiiType, ...]] = get_args(PiiType)
PII_LABELS: Final[dict[PiiType, str]] = {
    "person_name": "氏名",
    "phone": "電話番号",
    "email": "メールアドレス",
    "postal_code": "郵便番号",
    "address": "住所",
    "card": "カード番号",
    "bank_account": "口座番号",
    "birthday": "生年月日",
    "sns_account": "SNS アカウント",
}

Action = Literal["allow", "mask", "block"]
ACTIONS: Final[tuple[Action, ...]] = get_args(Action)
# 既定の方針。決済情報は外部に出さない（マスクしても用途がないため送らない）
DEFAULT_POLICY: Final[dict[PiiType, Action]] = {
    "person_name": "mask",
    "phone": "mask",
    "email": "mask",
    "postal_code": "mask",
    "address": "mask",
    "card": "block",
    "bank_account": "block",
    "birthday": "mask",
    "sns_account": "mask",
}

SpanSource = Literal["rule", "human"]
# 形で決まる種類は規則だけで確定する（モデルに聞くのは氏名など、規則では決めきれない候補だけ）
STRUCTURED: Final[frozenset[PiiType]] = frozenset(
    {"phone", "email", "postal_code", "address", "card", "bank_account", "birthday", "sns_account"}
)


class Span(BaseModel):
    start: int
    end: int
    type: PiiType
    text: str
    source: SpanSource = "rule"
    # モデルが「個人情報である」と判定した確率（規則で確定したもの・人が追加したものは None）
    score: float | None = None
    # モデル・人の判定で個人情報と確定したか
    confirmed: bool = True


_D = "[0-9０-９]"
# 数字の区切り（半角・全角のハイフン、長音、空白）
_SEP = "[-ー－‐ 　]"
_PREFECTURES = (
    "北海道|青森県|岩手県|宮城県|秋田県|山形県|福島県|茨城県|栃木県|群馬県|埼玉県|千葉県|東京都|神奈川県|新潟県|富山県|"
    "石川県|福井県|山梨県|長野県|岐阜県|静岡県|愛知県|三重県|滋賀県|京都府|大阪府|兵庫県|奈良県|和歌山県|鳥取県|島根県|"
    "岡山県|広島県|山口県|徳島県|香川県|愛媛県|高知県|福岡県|佐賀県|長崎県|熊本県|大分県|宮崎県|鹿児島県|沖縄県"
)
_KANJI = r"[一-龥々]"
_NAME = rf"{_KANJI}{{1,4}}(?:[ 　]?{_KANJI}{{1,3}}|[ 　]?[ぁ-んァ-ヶー]{{2,4}})?"
# 「様」「さん」の前に来ても人名ではない語
_NOT_NAMES: Final = frozenset(
    {
        "お客",
        "客",
        "皆",
        "皆々",
        "各位",
        "店長",
        "担当",
        "担当者",
        "店員",
        "配達員",
        "運送会社",
        "業者",
        "社長",
        "部長",
        "課長",
        "御社",
        "貴社",
        "神",
        "奥",
    }
)

# (種類, 正規表現, 取り出すグループ番号)
_RULES: Final[tuple[tuple[PiiType, re.Pattern[str], int], ...]] = (
    # 日本語の文字に続けて書かれても拾いすぎないよう、アドレスに使える文字に限定する
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"), 0),
    # 16 桁（4-4-4-4）と 15 桁（4-6-5、Amex）。全角数字・全角の区切りも拾う
    (
        "card",
        re.compile(
            rf"(?<!{_D})(?:(?:{_D}{{4}}{_SEP}?){{3}}{_D}{{4}}|{_D}{{4}}{_SEP}?{_D}{{6}}{_SEP}?{_D}{{5}})(?!{_D})"
        ),
        0,
    ),
    ("postal_code", re.compile(rf"〒?[ ]?(?<![{_D[1:-1]}-]){_D}{{3}}[-ー－‐]{_D}{{4}}(?![-ー－‐]?{_D})"), 0),
    # 090-1234-5678 / ０９０－… / 090 1234 5678 / 03(1234)5678
    (
        "phone",
        re.compile(
            rf"(?<![{_D[1:-1]}-])[0０]{_D}{{1,4}}(?:{_SEP}|[(（]){_D}{{1,4}}(?:{_SEP}|[)）]){_D}{{3,4}}(?!{_D})"
        ),
        0,
    ),
    ("phone", re.compile(rf"(?<!{_D})[0０][789７８９][0０]{_D}{{8}}(?!{_D})"), 0),
    (
        "bank_account",
        re.compile(
            rf"[^\s、。「」]{{1,12}}(?:銀行|信用金庫|信金|ゆうちょ)[^\n。]{{0,16}}?(?:普通|当座|貯蓄)[ 　]?{_D}{{6,8}}"
        ),
        0,
    ),
    (
        "birthday",
        re.compile(
            rf"(?:生年月日|誕生日)[はがを:：\s　「『]{{0,3}}((?:19|20){_D}{{2}}[年/.-]{_D}{{1,2}}[月/.-]{_D}{{1,2}}日?)"
        ),
        1,
    ),
    ("birthday", re.compile(rf"((?:19|20){_D}{{2}}年{_D}{{1,2}}月{_D}{{1,2}}日)生まれ"), 1),
    (
        "address",
        re.compile(
            rf"(?:{_PREFECTURES})[^\s、。,，\n（(]{{1,24}}?{_D}+(?:[-ー－丁目番地号]{{1,3}}{_D}+){{0,3}}(?:号)?"
        ),
        0,
    ),
    # 郵便番号の直後に続く住所（都道府県を省いた書き方）
    (
        "address",
        re.compile(
            rf"(?:〒|(?<![\w-])){_D}{{3}}[-ー－]{_D}{{4}}[ 　]*([^\s、。,，\n]{{2,24}}?{_D}+(?:[-ー－]{_D}+){{1,3}})"
        ),
        1,
    ),
    ("person_name", re.compile(rf"(?:氏名|お名前|名前|受取人|宛名)[は:：\s　「『]{{0,3}}({_NAME})"), 1),
    (
        "person_name",
        re.compile(rf"(?:^|[、。\s　の])({_KANJI}{{1,4}}(?:[ 　]?{_KANJI}{{1,3}})?)と申します", re.MULTILINE),
        1,
    ),
    ("person_name", re.compile(rf"(?:店|社|部|課|係|経理|総務|担当|事務局)の({_KANJI}{{2,4}})です"), 1),
    ("person_name", re.compile(rf"^[ 　]*({_KANJI}{{2,4}})です、", re.MULTILINE), 1),
    ("person_name", re.compile(rf"({_NAME})(?:様|さま|さん|くん|ちゃん|宛)"), 1),
    (
        "person_name",
        re.compile(
            r"(?:娘|息子|母|父|妻|夫|祖母|祖父|友人|同僚|姉|妹|兄|弟)の([一-龥々]{1,3}|[ぁ-ん]{2,4}|[ァ-ヶー]{2,5})(?:へ|に|が|の|と|は|から|ちゃん|くん)"
        ),
        1,
    ),
    # 名入れの文字（贈る相手の名前であることが多い）
    (
        "person_name",
        re.compile(rf"「({_KANJI}{{1,4}}[ 　]{_KANJI}{{1,4}}|[A-Za-z]{{2,16}}|[ぁ-んァ-ヶー]{{2,8}})」"),
        1,
    ),
    # 署名行（行末が「姓 名」の形。前に会社名・部署名があってもよい）
    ("person_name", re.compile(rf"(?:^|[ 　])({_KANJI}{{1,3}}[ 　]{_KANJI}{{1,3}})[ 　]*$", re.MULTILINE), 1),
)

# 人名の後ろに付かない語尾（会社・店・部署など）
_ORG_SUFFIX: Final = re.compile(r"(?:店|社|屋|部|課|局|所|院|会|室|係|協会|商店|商事|工房)$")

# 重なったときに残す優先度（小さいほど優先）
_PRIORITY: Final[dict[PiiType, int]] = {
    t: i
    for i, t in enumerate(
        ("email", "sns_account", "card", "bank_account", "address", "phone", "postal_code", "birthday", "person_name")
    )
}


# SNS のプロフィールの URL（会社のサイトや商品のページなど、ほかの URL は個人情報として扱わない）
_SNS_URL: Final = re.compile(
    r"(?<![A-Za-z0-9./\-])(?:https?://)?(?:www\.|m\.)?"
    r"(?:(?:x|twitter|instagram|facebook|fb)\.com/@?|(?:tiktok\.com|threads\.net)/@)"
    r"(?P<name>[A-Za-z0-9_.]{2,40})(?<!\.)"
)
# SNS の URL のうち、アカウントではない決まったページ（ホーム・検索・投稿の共有など）
_SNS_PAGES: Final = frozenset(
    {
        "home",
        "explore",
        "search",
        "i",
        "intent",
        "share",
        "sharer",
        "hashtag",
        "login",
        "signup",
        "settings",
        "messages",
        "notifications",
        "compose",
        "tos",
        "privacy",
        "about",
        "help",
        "legal",
        "watch",
        "reel",
        "reels",
        "p",
        "stories",
        "groups",
        "pages",
        "events",
        "marketplace",
        "gaming",
        "direct",
        "accounts",
        "discover",
    }
)
# @から始まる SNS のアカウント名。メールアドレスの @ は、前に英数字が続くので拾わない
_HANDLE: Final = re.compile(
    r"(?<![A-Za-z0-9._%+\-/@])@([A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?)(?![A-Za-z0-9_@])"
)
# @アカウント名を SNS のものとみなす手がかり（前後の近くに SNS の話があるとき）
_SNS_CONTEXT: Final = re.compile(
    r"(?i)instagram|インスタ|twitter|ツイッター|threads|スレッズ|tiktok|ティックトック|facebook|フェイスブック"
    r"|\bX\b|エックス|SNS|アカウント|DM|ダイレクトメッセージ|フォロー"
)
_SNS_WINDOW: Final = 25


def _sns_spans(text: str) -> Iterable[Span]:
    for m in _SNS_URL.finditer(text):
        if m.group("name").lower() in _SNS_PAGES:
            continue
        yield Span(start=m.start(), end=m.end(), type="sns_account", text=m.group(0))
    for m in _HANDLE.finditer(text):
        # 「@500円」のような数字だけのものは、単価などの書き方とみなす
        if m.group(1).isdigit():
            continue
        around = text[max(0, m.start() - _SNS_WINDOW) : m.end() + _SNS_WINDOW]
        if _SNS_CONTEXT.search(around):
            yield Span(start=m.start(), end=m.end(), type="sns_account", text=m.group(0))


def _matches(text: str) -> Iterable[Span]:
    yield from _sns_spans(text)
    for kind, pattern, group in _RULES:
        for m in pattern.finditer(text):
            value = m.group(group)
            if not value or not value.strip():
                continue
            start, end = m.span(group)
            if kind == "person_name":
                bare = value.replace(" ", "").replace("　", "")
                if len(bare) < 2 or bare in _NOT_NAMES or _ORG_SUFFIX.search(bare):
                    continue
            yield Span(start=start, end=end, type=kind, text=value)


def _overlaps(a: Span, b: Span) -> bool:
    return a.start < b.end and b.start < a.end


def detect(text: str) -> list[Span]:
    """規則で個人情報の候補を拾う。重なる候補は優先度の高い種類・長いものを残す。"""
    ranked = sorted(_matches(text), key=lambda s: (_PRIORITY[s.type], -(s.end - s.start), s.start))
    kept: list[Span] = []
    for span in ranked:
        if not any(_overlaps(span, k) for k in kept):
            kept.append(span)
    return sorted(kept, key=lambda s: s.start)


def mask_token(kind: PiiType) -> str:
    return f"【{PII_LABELS[kind]}】"


def _merged(spans: Iterable[Span]) -> list[tuple[int, int, PiiType]]:
    """重なる・接する範囲を 1 つにまとめる（種類は先に始まる方）。重なった部分が平文で残らないようにする。"""
    out: list[tuple[int, int, PiiType]] = []
    for s in sorted(spans, key=lambda x: (x.start, -x.end)):
        if out and s.start <= out[-1][1]:
            start, end, kind = out[-1]
            out[-1] = (start, max(end, s.end), kind)
        else:
            out.append((s.start, s.end, s.type))
    return out


def apply_mask(text: str, spans: Iterable[Span], policy: Mapping[PiiType, Action]) -> str:
    """方針が mask の確定済み候補を【種類】に置き換える。block・allow の候補はそのまま残す。

    方針に載っていない種類は安全側に倒してマスクする。
    """
    targets = [s for s in spans if s.confirmed and policy.get(s.type, "mask") == "mask"]
    out: list[str] = []
    pos = 0
    for start, end, kind in _merged(targets):
        out.append(text[pos:start])
        out.append(mask_token(kind))
        pos = end
    out.append(text[pos:])
    return "".join(out)


def mask_all(text: str, spans: Iterable[Span]) -> str:
    """方針に関係なく、個人情報の候補をすべて伏せる（チャンネルへの投稿や対応例など、外に出る表示用）。

    人が「氏名ではない」と外した候補や未確定の候補も伏せる。伏せすぎは表示が少し読みにくくなるだけだが、
    漏れは取り返せないため安全側に倒す。
    """
    every = [s.model_copy(update={"confirmed": True}) for s in spans]
    return apply_mask(text, every, {t: "mask" for t in PII_TYPES})


def blocked_types(spans: Iterable[Span], policy: Mapping[PiiType, Action]) -> list[PiiType]:
    return sorted({s.type for s in spans if s.confirmed and policy.get(s.type, "mask") == "block"}, key=PII_TYPES.index)


def validate_span(text: str, span: Span) -> Span:
    """人が追加した範囲を検証し、text を本文の該当部分に揃える。"""
    if not 0 <= span.start < span.end <= len(text):
        raise ValueError(f"範囲 {span.start}〜{span.end} が本文（{len(text)}文字）の外です")
    return span.model_copy(update={"text": text[span.start : span.end]})
