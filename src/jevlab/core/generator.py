"""文章の生成器（書き換え案など）。

いまは Claude Code の `claude -p`（個人のサブスク枠）で生成する。
サービスとして提供する段階では、同じ Generator の形で Anthropic API 版に差し替える
（API 版は実課金になるため、budget.py と同じ予算の上限と記録を付ける）。
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import shutil
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol, get_args

# haiku（Haiku 4.5）は claude -p 経由だと Sonnet 5 より遅く、出力も長いため選べないようにした（2026-09 実測）
ClaudeModel = Literal["sonnet", "opus"]
CLAUDE_MODELS: Final[tuple[ClaudeModel, ...]] = get_args(ClaudeModel)
DEFAULT_TIMEOUT_S: Final = 120.0


@dataclass(frozen=True)
class GenerateRequest:
    system: str
    prompt: str
    # 構造化出力が欲しいときの JSON Schema（None ならテキストだけ）
    schema: Mapping[str, object] | None = None


@dataclass(frozen=True)
class GeneratedText:
    text: str
    structured: Mapping[str, object] | None
    model: str
    latency_ms: float
    # CLI が報告する定価換算のコスト（サブスク経由では実際には請求されない）
    reported_cost_usd: float | None
    input_tokens: int
    output_tokens: int


class Generator(Protocol):
    async def generate(self, req: GenerateRequest) -> GeneratedText: ...


class GenerationError(RuntimeError):
    pass


@functools.cache
def _workdir() -> Path:
    # プロジェクトの CLAUDE.md などを読ませないよう、空のディレクトリで起動する。
    # 決まった名前だと他のユーザーが先に作れてしまうので、プロセスごとに推測できない名前で作る
    return Path(tempfile.mkdtemp(prefix="jevlab-claude-"))


# 子プロセスに渡さない環境変数。
# jevlab 自身の鍵は claude に不要で、ANTHROPIC_API_KEY があると claude がサブスクではなく API 課金で動くため外す。
# API 課金に切り替えたいときは JEVLAB_CLAUDE_USE_API_KEY=1 を設定する
_SECRET_PREFIXES: Final = ("TYPESAFE_", "KEV_")


def child_env(env: Mapping[str, str]) -> dict[str, str]:
    keep_api_key = env.get("JEVLAB_CLAUDE_USE_API_KEY") == "1"
    return {
        k: v
        for k, v in env.items()
        if not k.startswith(_SECRET_PREFIXES) and (keep_api_key or k != "ANTHROPIC_API_KEY")
    }


@dataclass(frozen=True)
class ClaudeCliGenerator:
    """`claude -p` で生成する。

    既定のシステムプロンプト・ツール・MCP・設定ファイルを読ませない指定を付ける。
    付けないと 1 回あたり約 5 万トークンの前提が載り、遅く重くなる（試作で確認済み）。
    """

    model: ClaudeModel = "sonnet"
    timeout_s: float = DEFAULT_TIMEOUT_S
    executable: str = "claude"

    def argv(self, req: GenerateRequest) -> list[str]:
        base = [
            self.executable,
            "-p",
            "--output-format", "json",
            "--tools", "",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--setting-sources", "",
            "--model", self.model,
            "--system-prompt", req.system,
        ]  # fmt: skip
        return base + (["--json-schema", json.dumps(req.schema, ensure_ascii=False)] if req.schema else [])

    async def generate(self, req: GenerateRequest) -> GeneratedText:
        if shutil.which(self.executable) is None:
            raise GenerationError(
                f"{self.executable} コマンドが見つかりません（Claude Code を入れてログインしてください）"
            )
        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *self.argv(req),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_workdir(),
            env=child_env(os.environ),
        )
        try:
            # プロンプトは引数でなく標準入力で渡す（長文・改行・先頭の "-" でも崩れない）
            out, err = await asyncio.wait_for(proc.communicate(req.prompt.encode()), self.timeout_s)
        except TimeoutError as e:
            with contextlib.suppress(ProcessLookupError):  # 終了と同時だった場合
                proc.kill()
            await proc.wait()
            raise GenerationError(f"claude -p が {self.timeout_s:.0f} 秒で応答しませんでした") from e
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):  # 終了と同時だった場合
                proc.kill()
            await proc.wait()
            raise
        latency_ms = (time.perf_counter() - started) * 1000
        if proc.returncode != 0:
            detail = err.decode(errors="replace").strip() or out.decode(errors="replace").strip()
            raise GenerationError(f"claude -p が失敗しました（終了コード {proc.returncode}）: {detail[:500]}")
        return parse_envelope(out.decode(errors="replace"), latency_ms, self.model)


def parse_envelope(raw: str, latency_ms: float, requested_model: str) -> GeneratedText:
    try:
        env = json.loads(raw)
    except json.JSONDecodeError as e:
        raise GenerationError(f"claude -p の出力が JSON ではありません: {raw[:300]!r}") from e
    if not isinstance(env, dict):
        raise GenerationError(f"claude -p の出力の形が想定と違います: {raw[:300]!r}")
    if env.get("is_error") or env.get("subtype") != "success":
        raise GenerationError(
            f"claude -p がエラーを返しました: subtype={env.get('subtype')} "
            f"api_error_status={env.get('api_error_status')} result={str(env.get('result'))[:300]}"
        )
    usage = env.get("usage") or {}
    models = list(env.get("modelUsage") or {})
    structured = env.get("structured_output")
    return GeneratedText(
        text=str(env.get("result", "")),
        structured=structured if isinstance(structured, dict) else None,
        model=models[0] if models else requested_model,
        latency_ms=round(latency_ms, 1),
        reported_cost_usd=cost if isinstance(cost := env.get("total_cost_usd"), int | float) else None,
        input_tokens=_count(usage, "input_tokens")
        + _count(usage, "cache_creation_input_tokens")
        + _count(usage, "cache_read_input_tokens"),
        output_tokens=_count(usage, "output_tokens"),
    )


def _count(usage: Mapping[str, object], key: str) -> int:
    # 使用量は目安の表示にだけ使うので、欠けていたり null だったりしても 0 とみなす
    v = usage.get(key)
    return int(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0


def make_generator(model: ClaudeModel) -> Generator:
    """生成器を選ぶ。JEVLAB_GENERATOR で切り替える（いまは claude-cli のみ。API 版はここに足す）。"""
    kind = os.environ.get("JEVLAB_GENERATOR", "claude-cli")
    if kind == "claude-cli":
        return ClaudeCliGenerator(model=model)
    raise GenerationError(f"生成器 {kind!r} は未対応です（claude-cli のみ）")
