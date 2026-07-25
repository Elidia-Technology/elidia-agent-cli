"""Research export — renders research reports to Markdown, HTML, or plain text.

Produces self-contained output files from research pipeline results.
"""
from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExportResult:
    format: str
    content: str
    file_path: str | None = None
    size_bytes: int = 0


def export_markdown(
    report: str,
    sources: list[dict[str, str]] | None = None,
    metadata: dict[str, Any] | None = None,
    output_path: Path | None = None,
) -> ExportResult:
    logger.debug(f"Entered into export_markdown: report_len={len(report)}")
    lines: list[str] = []

    if metadata:
        lines.append("---")
        lines.append(f"title: {metadata.get('title', 'Research Report')}")
        lines.append(f"date: {metadata.get('date', time.strftime('%Y-%m-%d'))}")
        if metadata.get("question"):
            lines.append(f"question: {metadata['question']}")
        lines.append("---")
        lines.append("")

    lines.append(report)

    if sources:
        lines.append("\n\n---\n")
        lines.append("## Sources\n")
        for i, src in enumerate(sources, 1):
            title = src.get("title", "Untitled")
            url = src.get("url", "")
            source = src.get("source", "")
            if url:
                lines.append(f"{i}. [{title}]({url})")
            else:
                lines.append(f"{i}. {title}")
            if source:
                lines[-1] += f" — {source}"

    content = "\n".join(lines)

    file_path = None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        file_path = str(output_path)
        logger.info(f"Exported markdown to {file_path}")

    return ExportResult(
        format="markdown",
        content=content,
        file_path=file_path,
        size_bytes=len(content.encode("utf-8")),
    )


def export_html(
    report: str,
    sources: list[dict[str, str]] | None = None,
    metadata: dict[str, Any] | None = None,
    output_path: Path | None = None,
) -> ExportResult:
    logger.debug(f"Entered into export_html: report_len={len(report)}")

    title = (metadata or {}).get("title", "Research Report")
    date = (metadata or {}).get("date", time.strftime("%Y-%m-%d"))

    body_html = _markdown_to_html(report)

    sources_html = ""
    if sources:
        source_items = []
        for src in sources:
            t = html.escape(src.get("title", "Untitled"))
            u = src.get("url", "")
            s = html.escape(src.get("source", ""))
            if u:
                source_items.append(f'<li><a href="{html.escape(u)}">{t}</a>{f" — {s}" if s else ""}</li>')
            else:
                source_items.append(f"<li>{t}{f' — {s}' if s else ''}</li>")
        sources_html = f'<h2>Sources</h2>\n<ol>\n{"chr(10)".join(source_items)}\n</ol>'

    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ --bg: #fff; --fg: #1a1a1a; --muted: #6b7280; --border: #e5e7eb; --link: #2563eb; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #111; --fg: #e5e5e5; --muted: #9ca3af; --border: #374151; --link: #60a5fa; }}
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: var(--bg); color: var(--fg); line-height: 1.7;
         max-width: 48rem; margin: 0 auto; padding: 2rem 1.5rem; }}
  h1 {{ font-size: 1.75rem; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1.3rem; margin-top: 2rem; margin-bottom: 0.5rem; border-bottom: 1px solid var(--border); padding-bottom: 0.25rem; }}
  h3 {{ font-size: 1.1rem; margin-top: 1.5rem; margin-bottom: 0.5rem; }}
  p {{ margin-bottom: 1rem; }}
  a {{ color: var(--link); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  ul, ol {{ margin-bottom: 1rem; padding-left: 1.5rem; }}
  li {{ margin-bottom: 0.25rem; }}
  blockquote {{ border-left: 3px solid var(--border); padding-left: 1rem; color: var(--muted); margin-bottom: 1rem; }}
  code {{ background: var(--border); padding: 0.15rem 0.35rem; border-radius: 3px; font-size: 0.9em; }}
  pre {{ background: var(--border); padding: 1rem; border-radius: 6px; overflow-x: auto; margin-bottom: 1rem; }}
  pre code {{ background: none; padding: 0; }}
  .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 2rem; }}
  hr {{ border: none; border-top: 1px solid var(--border); margin: 2rem 0; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
<p class="meta">Generated on {html.escape(date)}</p>
<hr>
{body_html}
{f"<hr>{sources_html}" if sources_html else ""}
</body>
</html>"""

    file_path = None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        file_path = str(output_path)
        logger.info(f"Exported HTML to {file_path}")

    return ExportResult(
        format="html",
        content=content,
        file_path=file_path,
        size_bytes=len(content.encode("utf-8")),
    )


def _markdown_to_html(md: str) -> str:
    logger.debug(f"Entered into _markdown_to_html: len={len(md)}")
    lines = md.split("\n")
    html_lines: list[str] = []
    in_list = False
    in_code = False
    code_lines: list[str] = []

    for line in lines:
        if line.startswith("```"):
            if in_code:
                html_lines.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        stripped = line.strip()

        if stripped.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{html.escape(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{html.escape(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{html.escape(stripped[2:])}</h2>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            item_text = _inline_markdown(stripped[2:])
            html_lines.append(f"<li>{item_text}</li>")
        elif stripped.startswith("> "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<blockquote><p>{_inline_markdown(stripped[2:])}</p></blockquote>")
        elif stripped == "---" or stripped == "***":
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<hr>")
        elif stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{_inline_markdown(stripped)}</p>")

    if in_list:
        html_lines.append("</ul>")
    if in_code:
        html_lines.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")

    return "\n".join(html_lines)


def _inline_markdown(text: str) -> str:
    import re
    result = html.escape(text)
    result = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', result)
    result = re.sub(r'\*(.+?)\*', r'<em>\1</em>', result)
    result = re.sub(r'`(.+?)`', r'<code>\1</code>', result)
    result = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        r'<a href="\2">\1</a>',
        result,
    )
    return result


async def export_pdf(
    report: str,
    title: str = "Research Report",
    sources: list[Any] | None = None,
    output_path: str | Path | None = None,
) -> ExportResult:
    """Export a research report as a self-contained PDF using Playwright.

    Requires: pip install playwright && playwright install chromium
    """
    logger.debug(f"Entered into export_pdf: title={title!r}, len={len(report)}")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright not installed. Run: pip install playwright && playwright install chromium"
        )

    html = _build_html_document(report, title, sources or [])

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html, wait_until="networkidle")
        pdf_bytes = await page.pdf(format="A4", print_background=True, margin={
            "top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm",
        })
        await browser.close()

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pdf_bytes)
        return ExportResult(content=f"PDF saved to {path}", file_path=str(path))

    return ExportResult(content="PDF generated", metadata={"bytes": len(pdf_bytes)})


def _build_html_document(report: str, title: str, sources: list[Any]) -> str:
    """Build a self-contained HTML document for PDF rendering."""
    import html as html_mod

    report_html = _md_to_html(report)

    sources_html = ""
    if sources:
        source_items = []
        for i, s in enumerate(sources, 1):
            title_s = getattr(s, "title", "") or getattr(s, "name", "") or str(s)
            url = getattr(s, "url", "") or getattr(s, "link", "") or ""
            source_items.append(
                f'<li><a href="{html_mod.escape(url)}">{html_mod.escape(title_s)}</a></li>'
                if url else f"<li>{html_mod.escape(title_s)}</li>"
            )
        sources_html = f"<h2>Sources</h2><ol>{''.join(source_items)}</ol>"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{html_mod.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
         max-width: 800px; margin: 0 auto; padding: 40px 20px;
         color: #1a1a1a; line-height: 1.6; font-size: 12pt; }}
  h1 {{ border-bottom: 2px solid #2563eb; padding-bottom: 8px; }}
  h2 {{ color: #2563eb; margin-top: 24px; }}
  code {{ background: #f1f5f9; padding: 2px 6px; border-radius: 3px; font-size: 10pt; }}
  pre {{ background: #1e293b; color: #e2e8f0; padding: 16px; border-radius: 6px;
         overflow-x: auto; font-size: 10pt; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left; }}
  th {{ background: #f1f5f9; }}
  a {{ color: #2563eb; }}
</style>
</head>
<body>
<h1>{html_mod.escape(title)}</h1>
{report_html}
{sources_html}
</body>
</html>"""
