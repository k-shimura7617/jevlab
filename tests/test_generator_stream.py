from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jevlab.core.generator import ClaudeCliGenerator, GeneratedText, GenerateRequest, GenerationError, parse_stream_line

RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "こんにちは。",
    "total_cost_usd": 0.001,
    "usage": {"input_tokens": 10, "output_tokens": 5},
    "modelUsage": {"claude-sonnet-test": {}},
}


def _delta(text: str) -> str:
    return json.dumps(
        {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
        }
    )


def test_parse_stream_line() -> None:
    assert parse_stream_line(_delta("こん")) == ("text", "こん")
    kind, raw = parse_stream_line(json.dumps(RESULT))
    assert kind == "result" and raw is not None and json.loads(raw)["result"] == "こんにちは。"
    assert parse_stream_line('{"type":"system","subtype":"init"}') == ("other", None)
    assert parse_stream_line("") == ("other", None)
    with pytest.raises(GenerationError, match="JSON"):
        parse_stream_line("oops")


def test_stream_argv_uses_stream_json_without_schema() -> None:
    argv = ClaudeCliGenerator().stream_argv(GenerateRequest(system="s", prompt="p"))
    i = argv.index("--output-format")
    assert argv[i + 1] == "stream-json" and "--verbose" in argv and "--include-partial-messages" in argv
    with pytest.raises(GenerationError, match="構造化出力"):
        ClaudeCliGenerator().stream_argv(GenerateRequest(system="s", prompt="p", schema={"type": "object"}))


def _fake_claude(tmp_path: Path, lines: list[str], code: int = 0) -> str:
    """stream-json を出す偽の claude（標準入力のプロンプトは読み捨てる）。"""
    script = tmp_path / "claude"
    body = "\n".join(lines)
    script.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdin.read()\nprint({body!r})\n"
        + (f"sys.stderr.write('boom')\nsys.exit({code})\n" if code else "")
    )
    script.chmod(0o755)
    return str(script)


async def _collect(stream: AsyncIterator[str | GeneratedText]) -> list[str | GeneratedText]:
    return [part async for part in stream]


@pytest.mark.anyio
async def test_stream_yields_text_then_result(tmp_path: Path) -> None:
    exe = _fake_claude(tmp_path, ['{"type":"system"}', _delta("こん"), _delta("にちは。"), json.dumps(RESULT)])
    parts = await _collect(ClaudeCliGenerator(executable=exe).stream(GenerateRequest(system="s", prompt="p")))
    assert parts[:2] == ["こん", "にちは。"]
    final = parts[2]
    assert isinstance(final, GeneratedText) and final.text == "こんにちは。" and final.model == "claude-sonnet-test"


@pytest.mark.anyio
async def test_stream_reports_failures(tmp_path: Path) -> None:
    exe = _fake_claude(tmp_path, [_delta("途中")], code=1)
    with pytest.raises(GenerationError, match="boom"):
        await _collect(ClaudeCliGenerator(executable=exe).stream(GenerateRequest(system="s", prompt="p")))
    exe = _fake_claude(tmp_path, [_delta("結果なし")])
    with pytest.raises(GenerationError, match="結果を返しませんでした"):
        await _collect(ClaudeCliGenerator(executable=exe).stream(GenerateRequest(system="s", prompt="p")))
