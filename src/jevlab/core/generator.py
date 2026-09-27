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
from collections.abc import AsyncIterator, Mapping
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


class StreamGenerator(Protocol):
    def stream(self, req: GenerateRequest) -> AsyncIterator[str | GeneratedText]:
        """書いた分から順に文字列を返し、最後に全体（GeneratedText）を 1 つ返す。構造化出力（schema）は使えない。"""
        ...


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

    def stream_argv(self, req: GenerateRequest) -> list[str]:
        """ストリーミング用。書いた分を順に受け取るため stream-json と部分メッセージを使う（構造化出力は使えない）。"""
        if req.schema is not None:
            raise GenerationError("ストリーミングでは構造化出力（schema）を使えません")
        argv = self.argv(req)
        i = argv.index("--output-format")
        return [*argv[: i + 1], "stream-json", "--verbose", "--include-partial-messages", *argv[i + 2 :]]

    async def stream(self, req: GenerateRequest) -> AsyncIterator[str | GeneratedText]:
        if shutil.which(self.executable) is None:
            raise GenerationError(
                f"{self.executable} コマンドが見つかりません（Claude Code を入れてログインしてください）"
            )
        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *self.stream_argv(req),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_workdir(),
            env=child_env(os.environ),
            limit=4 * 1024 * 1024,  # 1 行が長い（最後の結果に全文が入る）ため、読み取りの上限を広げる
        )
        if proc.stdin is None or proc.stdout is None or proc.stderr is None:
            raise GenerationError("claude -p の入出力を開けませんでした")
        final: GeneratedText | None = None
        # stderr は並行して読み続ける（読まずにいると、出力が多いときにパイプが詰まって止まる）
        err_task = asyncio.create_task(proc.stderr.read())
        err = b""
        try:
            try:
                proc.stdin.write(req.prompt.encode())
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                # すぐに終了した（ログインしていない など）。理由は終了コードと stderr で知らせる
                pass
            # 待ち時間は全体で数える。asyncio.timeout を yield をまたいで使うと読み手側の処理まで打ち切るため、1 行ずつ残り時間で待つ
            deadline = started + self.timeout_s
            while line := await asyncio.wait_for(proc.stdout.readline(), max(0.0, deadline - time.perf_counter())):
                kind, value = parse_stream_line(line.decode(errors="replace"))
                if kind == "text" and isinstance(value, str):
                    yield value
                elif kind == "result" and isinstance(value, str):
                    final = parse_envelope(value, (time.perf_counter() - started) * 1000, self.model)
            await asyncio.wait_for(proc.wait(), max(0.0, deadline - time.perf_counter()))
            err = await asyncio.wait_for(err_task, max(0.0, deadline - time.perf_counter()))
        except TimeoutError as e:
            raise GenerationError(f"claude -p が {self.timeout_s:.0f} 秒で応答しませんでした") from e
        except (ValueError, asyncio.LimitOverrunError) as e:
            raise GenerationError(f"claude -p の応答の 1 行が長すぎて読めません: {e}") from e
        except OSError as e:
            raise GenerationError(f"claude -p とのやり取りに失敗しました: {type(e).__name__}: {e}") from e
        finally:
            # 途中で止めた（画面を閉じた・時間切れ・中止）ときは、子プロセスを残さない
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            if not err_task.done():
                err_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, OSError):
                    await err_task
        if proc.returncode != 0:
            raise GenerationError(
                f"claude -p が失敗しました（終了コード {proc.returncode}）: {err.decode(errors='replace').strip()[:500]}"
            )
        if final is None:
            raise GenerationError("claude -p が結果を返しませんでした")
        yield final

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


def parse_stream_line(line: str) -> tuple[Literal["text", "result", "other"], str | None]:
    """stream-json の 1 行を読む。書いた分の文字（text）か、最後の結果の行そのもの（result）か、それ以外か。"""
    line = line.strip()
    if not line:
        return "other", None
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as e:
        raise GenerationError(f"claude -p のストリームの行が JSON ではありません: {line[:200]!r}") from e
    if not isinstance(msg, dict):
        return "other", None
    if msg.get("type") == "result":
        return "result", line
    event = msg.get("event")
    if msg.get("type") == "stream_event" and isinstance(event, dict) and event.get("type") == "content_block_delta":
        delta = event.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
            return "text", str(delta["text"])
    return "other", None


def _count(usage: Mapping[str, object], key: str) -> int:
    # 使用量は目安の表示にだけ使うので、欠けていたり null だったりしても 0 とみなす
    v = usage.get(key)
    return int(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0


def make_stream_generator(model: ClaudeModel) -> StreamGenerator:
    """ストリーミングの生成器を選ぶ（いまは claude-cli のみ）。"""
    kind = os.environ.get("JEVLAB_GENERATOR", "claude-cli")
    if kind == "claude-cli":
        return ClaudeCliGenerator(model=model)
    raise GenerationError(f"生成器 {kind!r} は未対応です（claude-cli のみ）")


def make_generator(model: ClaudeModel) -> Generator:
    """生成器を選ぶ。JEVLAB_GENERATOR で切り替える（いまは claude-cli のみ。API 版はここに足す）。"""
    kind = os.environ.get("JEVLAB_GENERATOR", "claude-cli")
    if kind == "claude-cli":
        return ClaudeCliGenerator(model=model)
    raise GenerationError(f"生成器 {kind!r} は未対応です（claude-cli のみ）")
