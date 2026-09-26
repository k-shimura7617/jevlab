"""docs/**/*.md を docs/html/ に HTML として書き出す（Markdown が正本、HTML は生成物）。

使い方（プロジェクトの依存関係は増やさない）:
    uv run --with markdown python scripts/build_docs.py

- 各ディレクトリの README.md は index.html にする。README の無いディレクトリには一覧ページを作る
- 相対リンクの .md は .html に置き換える（存在しないリンク先は警告する）
- ```mermaid のコードブロックは、表示時に Mermaid（cdn.jsdelivr.net）で図にする
- 見出しのアンカーは GitHub と同じ規則で作る（日本語の見出しへのリンクも GitHub 上と同じく動く）
"""

from __future__ import annotations

import html
import os
import re
import shutil
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = DOCS / "html"
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs"

CSS = """
:root {
  --bg: #ffffff; --panel: #f6f8fa; --text: #1f2328; --muted: #59636e; --line: #d1d9e0;
  --accent: #0969da; --code-bg: #eff1f3;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --panel: #161b22; --text: #e6edf3; --muted: #9198a1; --line: #30363d;
    --accent: #4493f8; --code-bg: #1f2428;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.75 system-ui, -apple-system, "Segoe UI", "Hiragino Sans", "Noto Sans JP", sans-serif;
}
header.site {
  position: sticky; top: 0; z-index: 1; display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
  padding: 10px 16px; background: var(--panel); border-bottom: 1px solid var(--line); font-size: 14px;
}
header.site a { color: var(--accent); text-decoration: none; }
header.site .crumbs { color: var(--muted); }
main { max-width: 920px; margin: 0 auto; padding: 24px 16px 64px; }
h1, h2, h3 { line-height: 1.35; }
h1 { font-size: 1.9em; border-bottom: 1px solid var(--line); padding-bottom: .3em; }
h2 { font-size: 1.45em; border-bottom: 1px solid var(--line); padding-bottom: .25em; margin-top: 2em; }
h3 { font-size: 1.15em; margin-top: 1.6em; }
a { color: var(--accent); }
code { background: var(--code-bg); padding: .15em .35em; border-radius: 4px; font-size: .9em;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
pre { background: var(--code-bg); padding: 12px 14px; border-radius: 6px; overflow-x: auto; line-height: 1.5; }
pre code { background: none; padding: 0; }
pre.mermaid { background: var(--panel); text-align: center; }
blockquote { margin: 1em 0; padding: .2em 1em; color: var(--muted); border-left: 4px solid var(--line); }
.table-wrap { overflow-x: auto; margin: 1em 0; }
table { border-collapse: collapse; min-width: 60%; font-size: .95em; }
th, td { border: 1px solid var(--line); padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: var(--panel); }
img { max-width: 100%; }
footer { margin-top: 48px; color: var(--muted); font-size: 13px; }
"""

PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — jevlab docs</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="{root}style.css">
</head>
<body>
<header class="site">
  <a href="{root}index.html">jevlab docs</a>
  <span class="crumbs">{crumbs}</span>
</header>
<main>
{body}
<footer>このページは docs/{source} から生成しました。編集は Markdown に対して行い、
<code>uv run --with markdown python scripts/build_docs.py</code> で作り直します。</footer>
</main>
{mermaid}
</body>
</html>
"""

MERMAID_SCRIPT = f"""<script type="module">
import mermaid from "{MERMAID}";
const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
mermaid.initialize({{ startOnLoad: true, theme: dark ? "dark" : "default", securityLevel: "strict" }});
</script>"""


def github_slug(value: str, separator: str = "-") -> str:
    """GitHub の見出しアンカーと同じ規則（小文字化・記号除去・空白をハイフンに。日本語は残す）。"""
    value = value.strip().lower()
    value = re.sub(r"[^\w\- ]", "", value)
    return value.replace(" ", separator)


def out_name(md: Path) -> Path:
    """docs からの相対パスで、出力先の相対パスを返す（README.md は index.html）。"""
    rel = md.relative_to(DOCS)
    return rel.with_name("index.html") if rel.name == "README.md" else rel.with_suffix(".html")


def rewrite_links(body: str, page: Path, warnings: list[str]) -> str:
    """相対リンクの .md を .html に、ディレクトリへのリンクを index.html に置き換える。"""

    def fix(m: re.Match[str]) -> str:
        href = html.unescape(m.group(1))
        if re.match(r"^[a-z]+:|^#|^/", href):
            return m.group(0)
        path, _, frag = href.partition("#")
        target = (page.parent / path).resolve()
        if DOCS.resolve() not in target.parents and target != DOCS.resolve():
            # docs の外（リポジトリ直下の SKILL.md やソースなど）は HTML にしないので、生成先から元のファイルを指す
            if not target.exists():
                warnings.append(f"{page.relative_to(DOCS)}: リンク先がありません: {href}")
            new = os.path.relpath(target, (OUT / out_name(page)).parent)
            return f'href="{html.escape(new + ("#" + frag if frag else ""))}"'
        if path.endswith("/") or target.is_dir():
            new = path.rstrip("/") + "/index.html"
            if not target.exists():
                warnings.append(f"{page.relative_to(DOCS)}: リンク先のディレクトリがありません: {href}")
        elif path.endswith(".md"):
            new = str(Path(path).with_name("index.html")) if Path(path).name == "README.md" else path[:-3] + ".html"
            if not target.exists():
                warnings.append(f"{page.relative_to(DOCS)}: リンク先がありません: {href}")
        else:
            return m.group(0)
        return f'href="{html.escape(new + ("#" + frag if frag else ""))}"'

    return re.sub(r'href="([^"]+)"', fix, body)


def convert(md: Path, warnings: list[str]) -> str:
    text = md.read_text(encoding="utf-8")
    converter = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        extension_configs={"toc": {"slugify": github_slug}},
    )
    body = converter.convert(text)
    # ```mermaid は Mermaid が読む形に変える（中身は HTML エスケープのままでよい。Mermaid は textContent を読む）
    body, n_mermaid = re.subn(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        r'<pre class="mermaid">\1</pre>',
        body,
        flags=re.DOTALL,
    )
    body = re.sub(r"<table>", '<div class="table-wrap"><table>', body)
    body = re.sub(r"</table>", "</table></div>", body)
    body = rewrite_links(body, md, warnings)
    m = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    title = m.group(1).strip() if m else md.stem
    rel = md.relative_to(DOCS)
    depth = len(out_name(md).parts) - 1
    root = "../" * depth
    crumbs = " / ".join(html.escape(p) for p in rel.parts)
    return PAGE.format(
        title=html.escape(title),
        root=root,
        crumbs=crumbs,
        body=body,
        source=html.escape(str(rel)),
        mermaid=MERMAID_SCRIPT if n_mermaid else "",
    )


def directory_index(directory: Path, pages: list[Path]) -> str:
    """README の無いディレクトリの一覧ページ。"""
    rel = directory.relative_to(DOCS)
    depth = len(rel.parts)
    items = "\n".join(
        f'<li><a href="{html.escape(out_name(p).name)}">{html.escape(first_heading(p))}</a> '
        f"<code>{html.escape(p.name)}</code></li>"
        for p in sorted(pages)
    )
    body = f"<h1>{html.escape(str(rel))}/</h1>\n<ul>\n{items or '<li>まだ文書はありません</li>'}\n</ul>"
    return PAGE.format(
        title=html.escape(f"{rel}/"),
        root="../" * depth,
        crumbs=html.escape(str(rel)),
        body=body,
        source=html.escape(f"{rel}/"),
        mermaid="",
    )


def first_heading(md: Path) -> str:
    m = re.search(r"^#\s+(.+)$", md.read_text(encoding="utf-8"), flags=re.MULTILINE)
    return m.group(1).strip() if m else md.stem


def main() -> int:
    if not DOCS.is_dir():
        print(f"docs ディレクトリがありません: {DOCS}", file=sys.stderr)
        return 1
    sources = sorted(p for p in DOCS.rglob("*.md") if OUT not in p.parents)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    (OUT / "style.css").write_text(CSS.lstrip(), encoding="utf-8")
    warnings: list[str] = []
    for md in sources:
        dest = OUT / out_name(md)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(convert(md, warnings), encoding="utf-8")
    # README の無いディレクトリ（docs 直下を除く）にも一覧ページを作る
    dirs = {p.parent for p in sources} | {d for d in DOCS.rglob("*") if d.is_dir() and OUT not in [d, *d.parents]}
    for d in sorted(dirs):
        if d == DOCS or (d / "README.md").exists():
            continue
        dest = OUT / d.relative_to(DOCS) / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(directory_index(d, [p for p in sources if p.parent == d]), encoding="utf-8")
    print(f"{len(sources)} 件の Markdown を {OUT.relative_to(ROOT)}/ に書き出しました")
    for w in warnings:
        print(f"警告: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
