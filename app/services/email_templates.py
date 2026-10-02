"""
Email templates: the ones saved from the web app's template builder, plus the
built-in files shipped in app/templates/email/.

Saved templates live in the gateway's database (SQLite or MongoDB, whichever is
set up), so they move with the rest of the data when the database is switched,
exported or backed up. Each gets a stable ID (`tpl_...`). The sending system passes
the ID (or the name) as `template`, plus `context` with the values for the
`{{ context.key }}` placeholders. A saved name wins over a built-in file of the same name.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from typing import Any, Optional

from app import bootstrap
from app.db import get_repository
from app.db.base import DuplicateTemplateNameError

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# `&nbsp;` too: typing spaces in the browser editor saves them as entities.
CONTEXT_TOKEN_RE = re.compile(r"\{\{(?:\s|&nbsp;)*context\.([A-Za-z0-9_]+)(?:\s|&nbsp;)*\}\}")
ID_RE = re.compile(r"^tpl_[0-9a-f]{12}$")

# ---- guardrail: editor-only styling must never reach a sent email ----
# The builder paints {{ context.x }} placeholders yellow while you edit. Chrome's editor can copy that
# look onto ordinary text as inline styles, so we strip it on save and again when a template is read.
_EDITOR_STYLE_TAG_RE = re.compile(r"<style\b[^>]*\bdata-mg\b[^>]*>.*?</style>", re.I | re.S)
_EDITOR_SPAN_RE = re.compile(r"<span\b[^>]*\bdata-mg-ph\b[^>]*>(.*?)</span>", re.I | re.S)
_EDITOR_ATTR_RE = re.compile(r"\s+(?:data-mg-ph|data-mg|contenteditable|spellcheck)(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?", re.I)
_STYLE_ATTR_RE = re.compile(r"""(\s)style\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_HIGHLIGHT_RE = re.compile(r"fff3bf|rgba?\(\s*255\s*,\s*243\s*,\s*191|c99a00|rgba?\(\s*201\s*,\s*154\s*,\s*0\b", re.I)
_BLACK_RE = re.compile(r"^(?:#000(?:000)?|black|rgb\(\s*0\s*,\s*0\s*,\s*0\s*\))$", re.I)


def _clean_style(m: "re.Match[str]") -> str:
    style = m.group(2) if m.group(2) is not None else m.group(3)
    decls = [d.strip() for d in style.split(";") if d.strip()]
    props = [(d.split(":", 1)[0].strip().lower(), d.split(":", 1)[1].strip() if ":" in d else "") for d in decls]
    if not any(p in ("background", "background-color", "outline", "outline-color") and _HIGHLIGHT_RE.search(v) for p, v in props):
        return m.group(0)   # not the editor's highlight: leave the author's styling alone
    kept = []
    for d, (p, v) in zip(decls, props):
        if p in ("background", "background-color", "outline", "outline-color") and _HIGHLIGHT_RE.search(v):
            continue
        if p.startswith("outline") or (p == "color" and _BLACK_RE.match(v)) or (p == "border-radius" and v == "3px"):
            continue   # the other bits of the same editor highlight
        kept.append(d)
    return f'{m.group(1)}style="{"; ".join(kept)}"' if kept else ""


def clean_editor_artifacts(html: str) -> str:
    """Remove the web editor's placeholder highlight (marker spans, <style data-mg>, and any inline
    yellow background/outline Chrome copied onto text) from template HTML."""
    if not html:
        return html
    html = _EDITOR_STYLE_TAG_RE.sub("", html)
    html = _EDITOR_SPAN_RE.sub(r"\1", html)
    html = _EDITOR_ATTR_RE.sub("", html)
    html = _STYLE_ATTR_RE.sub(_clean_style, html)
    return re.sub(r"<span\s*>(.*?)</span>", r"\1", html, flags=re.S)
EXTS = ("html", "txt")
MAX_BYTES = 512 * 1024


def _builtin_dir() -> str:
    return os.environ.get("EMAIL_TEMPLATE_DIR", "/app/templates/email").strip() or "/app/templates/email"


def valid_name(name: str) -> bool:
    return bool(name) and len(name) <= 64 and bool(NAME_RE.match(name)) and not name.startswith(".")


def keys_in(text: str) -> list[str]:
    return sorted(set(CONTEXT_TOKEN_RE.findall(text or "")))


def _read_file(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _builtin_names() -> set[str]:
    try:
        files = os.listdir(_builtin_dir())
    except OSError:
        return set()
    out = set()
    for f in files:
        stem, _, ext = f.rpartition(".")
        if ext in EXTS and valid_name(stem):
            out.add(stem)
    return out


# ---- one-time import of templates saved as files by an earlier version ----
_migrated = False


def _migrate_legacy_files() -> None:
    """Earlier builds kept saved templates as DATA_DIR/templates/email/<id>.json|html|txt.
    Move them into the database (once), then delete the files."""
    global _migrated
    if _migrated:
        return
    _migrated = True
    legacy = os.path.join(bootstrap.DATA_DIR, "templates", "email")
    try:
        files = os.listdir(legacy)
    except OSError:
        return
    repo = get_repository()
    for f in sorted(files):
        stem, _, ext = f.rpartition(".")
        if ext != "json" or not ID_RE.match(stem):
            continue
        try:
            meta = json.loads(_read_file(os.path.join(legacy, f)) or "{}")
            if repo.get_email_template(stem) is None and meta.get("name") and not repo.find_email_template_by_name(meta["name"]):
                now = time.time()
                repo.save_email_template({
                    "_id": stem, "name": meta["name"], "created_at": now, "updated_at": now,
                    "html": _read_file(os.path.join(legacy, f"{stem}.html")) or "",
                    "txt": _read_file(os.path.join(legacy, f"{stem}.txt")) or "",
                })
            for e in ("json", "html", "txt"):
                p = os.path.join(legacy, f"{stem}.{e}")
                if os.path.exists(p):
                    os.remove(p)
        except Exception:
            continue   # leave the files alone; retried on the next restart


def _saved_by_ref(ref: str) -> Optional[dict[str, Any]]:
    repo = get_repository()
    if ID_RE.match(ref):
        t = repo.get_email_template(ref)
        if t:
            return t
    return repo.find_email_template_by_name(ref)


def read(ref: str, ext: str) -> Optional[str]:
    """The template's text for an ID or name, or None. Order: saved ID, saved name, built-in name."""
    ref = (ref or "").strip()
    if ext not in EXTS or not valid_name(ref):
        return None
    _migrate_legacy_files()
    t = _saved_by_ref(ref)
    if t:
        text = t.get(ext) or None
        return clean_editor_artifacts(text) if ext == "html" and text else text
    if ref in _builtin_names():
        return _read_file(os.path.join(_builtin_dir(), f"{ref}.{ext}"))
    return None


def _describe(tid: Optional[str], name: str, html: str, txt: str) -> dict[str, Any]:
    return {
        "id": tid, "name": name, "has_html": bool(html), "has_txt": bool(txt),
        "keys": keys_in(html + "\n" + txt), "source": "saved" if tid else "built-in",
        "html": html, "txt": txt,
    }


def list_templates() -> list[dict[str, Any]]:
    """Saved templates (with IDs) first, then built-ins not shadowed by a saved name."""
    _migrate_legacy_files()
    saved = get_repository().list_email_templates()
    out = [_describe(t["_id"], t["name"], t.get("html") or "", t.get("txt") or "") for t in saved]
    taken = {t["name"] for t in saved}
    for n in sorted(_builtin_names() - taken):
        out.append(_describe(None, n, _read_file(os.path.join(_builtin_dir(), f"{n}.html")) or "",
                             _read_file(os.path.join(_builtin_dir(), f"{n}.txt")) or ""))
    for t in out:
        t.pop("html"), t.pop("txt")
    return sorted(out, key=lambda t: (t["source"] != "saved", t["name"].lower()))


def get(ref: str) -> Optional[dict[str, Any]]:
    ref = (ref or "").strip()
    if not valid_name(ref):
        return None
    _migrate_legacy_files()
    t = _saved_by_ref(ref)
    if t:
        return _describe(t["_id"], t["name"], t.get("html") or "", t.get("txt") or "")
    if ref in _builtin_names():
        return _describe(None, ref, _read_file(os.path.join(_builtin_dir(), f"{ref}.html")) or "",
                         _read_file(os.path.join(_builtin_dir(), f"{ref}.txt")) or "")
    return None


def save(tid: Optional[str], name: str, html: str, txt: str) -> dict[str, Any]:
    """Create (tid None) or update a saved template. Names must be unique among
    saved templates. Raises ValueError."""
    name = (name or "").strip()
    if not valid_name(name):
        raise ValueError("Use letters, numbers, dot, dash or underscore (max 64) for the template name.")
    if ID_RE.match(name):
        raise ValueError("That name looks like a template ID; pick another.")
    html, txt = clean_editor_artifacts(html or ""), txt or ""
    if not html.strip() and not txt.strip():
        raise ValueError("Add an HTML or a text body before saving.")
    if len(html.encode("utf-8")) > MAX_BYTES or len(txt.encode("utf-8")) > MAX_BYTES:
        raise ValueError("A template can be at most 512 KB.")
    _migrate_legacy_files()
    repo = get_repository()
    now = time.time()
    if tid is None:
        if repo.find_email_template_by_name(name):
            raise ValueError(f'A template named "{name}" already exists.')
        doc = {"_id": "tpl_" + secrets.token_hex(6), "created_at": now}
    else:
        existing = repo.get_email_template(tid) if ID_RE.match(tid) else None
        if not existing:
            raise ValueError("That template no longer exists.")
        clash = repo.find_email_template_by_name(name)
        if clash and clash["_id"] != tid:
            raise ValueError(f'A template named "{name}" already exists.')
        doc = {"_id": tid, "created_at": existing.get("created_at", now)}
    doc.update({"name": name, "html": html if html.strip() else "", "txt": txt if txt.strip() else "", "updated_at": now})
    try:
        repo.save_email_template(doc)
    except DuplicateTemplateNameError:
        raise ValueError(f'A template named "{name}" already exists.')
    return get(doc["_id"]) or {}


def delete(tid: str) -> bool:
    """Remove a saved template by ID (a built-in with the same name shows again)."""
    if not ID_RE.match(tid or ""):
        return False
    return get_repository().delete_email_template(tid)
