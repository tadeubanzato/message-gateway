"""
Turn a `context` value into HTML that is safe to drop into an HTML email template.

The sending system should not need to know about HTML. Whatever it sends, we make it safe:

- Plain text ("a < b & c\nnext line"): escaped, and line breaks become <br>.
- Text an upstream tool already escaped ("a &lt; b &amp; c<br>next"): kept as is, never double-escaped.
- Harmless formatting tags pass through (<br>, <hr>, <b>/<strong>, <i>/<em>, <u>, <s>, <code>, <pre>, <p>, <blockquote>,
  <h1>-<h4>, <sub>, <sup>, <small>, <ul>/<ol>/<li>, and <a href="http(s)/mailto">). Common non-standard names such as
  <bold>, <italic>, <underline> and <bullets> are mapped to the real tag. Every other tag, and every attribute but a safe href, is shown as text.

A context key that ends in `_html` is trusted and inserted as it is, for callers that build their own markup.
"""

from __future__ import annotations

import re

_ENTITY_RE = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,31}|#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6});")
_TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)((?:\s+[^<>]*?)?)\s*(/?)>")
_SIMPLE_TAGS = {"b", "strong", "i", "em", "u", "s", "strike", "code", "pre", "p", "ul", "ol", "li", "blockquote",
                "h1", "h2", "h3", "h4", "sub", "sup", "small"}
# names people (and LLMs) write that aren't real HTML: map them to the real tag
_ALIASES = {"bold": "strong", "italic": "em", "italics": "em", "underline": "u", "strikethrough": "s", "del": "s",
            "bullets": "ul", "bullet": "li", "list": "ul", "item": "li", "numbered": "ol", "paragraph": "p"}
_HREF_RE = re.compile(r"""\shref\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_SAFE_URL_RE = re.compile(r"^(?:https?://|mailto:)[^\s\"'<>]*$", re.I)


def is_trusted_key(key: str) -> bool:
    return key.endswith("_html")


def _escape_text(text: str) -> str:
    """Escape & < > and quotes, but leave entities that are already there alone."""
    out, last = [], 0
    for m in _ENTITY_RE.finditer(text):
        out.append(_esc(text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(_esc(text[last:]))
    return "".join(out)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _tag(m: "re.Match[str]") -> str:
    closing, name, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
    name = _ALIASES.get(name, name)
    if name in ("br", "hr"):
        return "" if closing else f"<{name}>"
    if name in _SIMPLE_TAGS:
        return f"</{name}>" if closing else f"<{name}>"
    if name == "a":
        if closing:
            return "</a>"
        h = _HREF_RE.search(" " + attrs)
        url = ((h.group(1) if h and h.group(1) is not None else h.group(2)) if h else "").strip()
        if _SAFE_URL_RE.match(url):
            return f'<a href="{_esc(url)}">'
        return "<a>"
    return _esc(m.group(0))   # not allowed: show it as text


def to_html(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out, last = [], 0
    for m in _TAG_RE.finditer(text):
        out.append(_escape_text(text[last:m.start()]))
        out.append(_tag(m))
        last = m.end()
    out.append(_escape_text(text[last:]))
    # lone line breaks become <br>; a break right after an upstream <br> is not doubled
    return re.sub(r"(<br>)?\n", "<br>", "".join(out))
