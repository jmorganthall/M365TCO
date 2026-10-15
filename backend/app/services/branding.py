"""The practice's readout branding (TARGET_SCHEMA §3.5, D25).

One install-wide row, entered in the GUI. Every field is nullable: NULL means the
built-in default below, resolved here at render time and never written back. The
defaults are deliberately neutral — a palette and generic wording, no identity —
so the repository stays white-label; a practice's own logo and words exist only as
data in its deployment.

Rich-text blocks are stored as sanitized HTML fragments: bold, italic, links
(http, https, mailto) and a highlight span, nothing else. Sanitizing happens on
write and again on render, so a fragment can never carry markup into the readout.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from html.parser import HTMLParser

from sqlalchemy.orm import Session

from .. import models

# Built-in defaults: a deep navy for ink and bands, a bright blue accent and a
# warm highlight. Neutral — used until the practice sets its own.
DEFAULT_COLORS = {
    "primary_color": "#0E2841",
    "accent_color": "#1578F8",
    "highlight_color": "#F7A816",
}
DEFAULT_TEXT = {
    "tagline": "",
    "cover_subtitle": "Microsoft 365 total cost of ownership",
    "closing_block": "",
    "footer_disclaimer": (
        "Licensing-only view. Migration services, Microsoft funding, Azure consumption "
        "and operational savings are quantified in the follow-on."
    ),
}
RICH_FIELDS = tuple(DEFAULT_TEXT)
COLOR_FIELDS = tuple(DEFAULT_COLORS)
# A logo is a picture, not a document: keep the stored data URL modest.
MAX_LOGO_BYTES = 2 * 1024 * 1024

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOGO_RE = re.compile(r"^data:image/(png|jpeg|jpg|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=\s]+$")


def is_color(value: str | None) -> bool:
    return bool(_COLOR_RE.match((value or "").strip()))


def is_logo(value: str | None) -> bool:
    """True only for a base64 image data URL (PNG, JPEG, GIF, WebP or SVG), so a
    logo can never inject a script, an external URL or other markup."""
    return bool(_LOGO_RE.match(value or ""))


# ---- Rich text -----------------------------------------------------------------

_ALLOWED = {"b": "b", "strong": "b", "i": "i", "em": "i", "a": "a", "br": "br",
            "p": "p", "div": "p", "span": "span"}


class _Sanitizer(HTMLParser):
    """Keeps text, <b>, <i>, <br>, paragraphs, <a href> to http/https/mailto, and
    <span class="hl">; every other tag is dropped (its text kept), every other
    attribute removed."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str | None] = []
        self.skip = 0          # inside <script>/<style>: drop the text too

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        t = _ALLOWED.get(tag)
        a = dict(attrs)
        if t == "br":
            self.out.append("<br>")
            return
        if t == "a":
            href = (a.get("href") or "").strip()
            if re.match(r"^(https?://|mailto:)[^\s\"'<>]+$", href, re.I):
                self.out.append(f'<a href="{html.escape(href, quote=True)}">')
            else:
                t = None
        elif t == "span":
            # The editor marks the highlight with class="hl"; any other span
            # (pasted text) keeps only its text.
            t = "span" if "hl" in (a.get("class") or "").split() else None
            if t:
                self.out.append('<span class="hl">')
        elif t:
            self.out.append(f"<{t}>")
        self.stack.append(t)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        want = _ALLOWED.get(tag)
        if want == "br":
            return
        # Close the innermost open element with this tag; pasted HTML is not
        # always well nested. A dropped tag (None) closes a dropped entry.
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i] == want:
                t = self.stack.pop(i)
                if t:
                    self.out.append(f"</{t}>")
                return

    def handle_data(self, data):
        if self.skip:
            return
        self.out.append(html.escape(data, quote=False))

    def close_all(self) -> str:
        while self.stack:
            t = self.stack.pop()
            if t:
                self.out.append(f"</{t}>")
        return "".join(self.out)


def sanitize_rich(value: str | None) -> str:
    if not value:
        return ""
    p = _Sanitizer()
    p.feed(value)
    p.close()
    out = p.close_all().strip()
    # An editor that has been emptied leaves only breaks and empty paragraphs.
    if not re.sub(r"<[^>]+>|&nbsp;|\s", "", out):
        return ""
    return out


def plain_text(value: str | None) -> str:
    """The rich text without markup (for places that take a plain string)."""
    return html.unescape(re.sub(r"<[^>]+>", " ", value or "")).strip()


# ---- The record ----------------------------------------------------------------

def get(db: Session) -> models.PracticeBranding:
    row = db.get(models.PracticeBranding, "singleton")
    if row is None:
        row = models.PracticeBranding(id="singleton")
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update(db: Session, row: models.PracticeBranding, changes: dict) -> models.PracticeBranding:
    """Apply a partial update. An empty value clears the field back to NULL (the
    built-in default); a value is validated and stored exactly as entered (rich
    text after sanitizing). Raises ValueError naming the first bad field."""
    for key, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        if value in (None, ""):
            setattr(row, key, None)
        elif key in COLOR_FIELDS:
            if not is_color(value):
                raise ValueError(f"{key}: use a hex colour such as #0E2841")
            setattr(row, key, value.upper())
        elif key == "logo_data_url":
            if not is_logo(value):
                raise ValueError("logo_data_url: upload a PNG, JPEG, GIF, WebP or SVG image")
            if len(value) > MAX_LOGO_BYTES * 4 // 3 + 64:
                raise ValueError("logo_data_url: the image is larger than 2 MB")
            setattr(row, key, value)
        elif key in RICH_FIELDS:
            setattr(row, key, sanitize_rich(value) or None)
        else:
            raise ValueError(f"{key}: not a branding field")
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def resolved(row: models.PracticeBranding | None) -> dict:
    """What the readout uses: each stored value, or its built-in default. Colours
    and the logo are re-validated and rich text re-sanitized, so a value written
    around the API can't break the page."""
    out: dict = {}
    for k, default in DEFAULT_COLORS.items():
        v = getattr(row, k, None) if row is not None else None
        out[k] = v if is_color(v) else default
    logo = getattr(row, "logo_data_url", None) if row is not None else None
    out["logo_data_url"] = logo if is_logo(logo) else ""
    for k, default in DEFAULT_TEXT.items():
        v = getattr(row, k, None) if row is not None else None
        out[k] = sanitize_rich(v) if v else html.escape(default)
    return out
