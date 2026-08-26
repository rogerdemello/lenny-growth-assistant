"""Artifact sanitization — layer one of two.

Generated HTML is untrusted input. It is written by a language model that may
have been steered by transcript text, a user instruction, or a prompt injection
riding along in either. Treating it as trusted because "we generated it" is the
mistake worth avoiding.

Two independent layers protect the viewer, and neither is relied on alone:

  1. **This module strips.** An allowlist over tags and attributes, applied
     server-side, before anything is stored or served. What it removes is
     recorded and shown in the UI, so the user knows the document they see is
     not byte-identical to what the model wrote.

  2. **The client isolates.** The viewer renders into an iframe with a `sandbox`
     attribute that grants nothing — no scripts, no same-origin, no forms — plus
     a `default-src 'none'` CSP. Even a sanitizer bypass lands in a context with
     no script execution, no network access, and no reach into the parent page.

Because scripts are stripped in layer one, layer two never needs to grant
`allow-scripts`. That is what makes the policy simple enough to explain in a
sentence: **artifacts are documents, not programs.**

The deliberate trade-off: interactive HTML artifacts (charts that respond to
clicks, forms) do not work here. For an internal assistant that produces
documents and one-pagers, refusing to run untrusted JavaScript is the right
call. docs/design.md carries the full allow/block table.
"""

from __future__ import annotations

import re
from typing import Any

import nh3

from app.core.logging import get_logger

log = get_logger(__name__)

# Structural, textual, and tabular markup — everything a document needs.
ALLOWED_TAGS: set[str] = {
    "html", "head", "body", "meta", "title", "style",
    "div", "span", "section", "article", "header", "footer", "main", "aside", "nav",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "blockquote", "pre", "code", "em", "strong", "b", "i", "u", "s",
    "small", "sub", "sup", "mark", "abbr", "cite", "q", "time", "figure", "figcaption",
    "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
    "a", "img",
    # Inline SVG is allowed for diagrams and icons. It carries no script
    # capability once event handlers and <script> are gone, and the sandbox
    # would neutralise anything that slipped through.
    "svg", "path", "circle", "rect", "line", "polyline", "polygon", "g", "text", "ellipse", "defs",
}

ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "*": {"class", "id", "style", "title", "lang", "dir", "role", "aria-label", "aria-hidden"},
    # `rel` is deliberately absent: nh3 sets it itself via `link_rel`, and
    # declaring both is an error. We want nh3 to own it so a model cannot
    # write `rel=""` and strip the noopener protection.
    "a": {"href", "target"},
    "img": {"src", "alt", "width", "height", "loading"},
    "th": {"colspan", "rowspan", "scope"},
    "td": {"colspan", "rowspan"},
    "col": {"span"},
    "colgroup": {"span"},
    "time": {"datetime"},
    "meta": {"charset", "name", "content"},
    "svg": {"viewBox", "width", "height", "fill", "xmlns", "stroke", "stroke-width", "class"},
    "path": {"d", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"},
    "circle": {"cx", "cy", "r", "fill", "stroke", "stroke-width"},
    "ellipse": {"cx", "cy", "rx", "ry", "fill", "stroke"},
    "rect": {"x", "y", "width", "height", "fill", "stroke", "rx", "ry"},
    "line": {"x1", "y1", "x2", "y2", "stroke", "stroke-width"},
    "polyline": {"points", "fill", "stroke", "stroke-width"},
    "polygon": {"points", "fill", "stroke", "stroke-width"},
    "g": {"fill", "stroke", "transform"},
    "text": {"x", "y", "fill", "font-size", "text-anchor"},
}

# `data:` is permitted here so inline images work, and then narrowed per
# attribute by `_attribute_filter` below — nh3's scheme list is global, but the
# safe answer differs by attribute: `data:image/png` in an `<img src>` is fine,
# `data:text/html` in an `<a href>` is a navigation XSS vector.
ALLOWED_URL_SCHEMES: set[str] = {"http", "https", "mailto", "data"}

_SAFE_LINK_SCHEME_RE = re.compile(r"\A\s*(https?:|mailto:|#|/|\./|\.\./)", re.IGNORECASE)
_SAFE_IMAGE_SRC_RE = re.compile(r"\A\s*(data:image/(png|jpeg|jpg|gif|webp|svg\+xml);|https:)", re.IGNORECASE)


def _attribute_filter(tag: str, attribute: str, value: str) -> str | None:
    """Narrow URL policy per attribute. Returning None drops the attribute."""
    if attribute == "href":
        return value if _SAFE_LINK_SCHEME_RE.match(value) else None
    if attribute == "src":
        return value if _SAFE_IMAGE_SRC_RE.match(value) else None
    return value

# Patterns we report on. nh3 removes these regardless — the regexes exist so the
# UI can say *what* was removed, not to do the removing.
_REPORT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("script tag", re.compile(r"<\s*script\b", re.IGNORECASE)),
    ("inline event handler", re.compile(r"\bon[a-z]+\s*=", re.IGNORECASE)),
    ("javascript: URL", re.compile(r"javascript\s*:", re.IGNORECASE)),
    ("data:text/html URL", re.compile(r"data\s*:\s*text/html", re.IGNORECASE)),
    ("iframe", re.compile(r"<\s*iframe\b", re.IGNORECASE)),
    ("form", re.compile(r"<\s*form\b", re.IGNORECASE)),
    ("object/embed/applet", re.compile(r"<\s*(object|embed|applet)\b", re.IGNORECASE)),
    ("meta refresh", re.compile(r"<\s*meta[^>]+http-equiv\s*=\s*[\"']?refresh", re.IGNORECASE)),
    ("base tag", re.compile(r"<\s*base\b", re.IGNORECASE)),
    ("link tag", re.compile(r"<\s*link\b", re.IGNORECASE)),
    ("CSS expression()", re.compile(r"expression\s*\(", re.IGNORECASE)),
    ("CSS @import", re.compile(r"@import\b", re.IGNORECASE)),
]

# `img-src data:` and nothing else network-facing: an artifact cannot make a
# single outbound request. That closes the exfiltration channel where generated
# content encodes data into a remote image URL — worth more than the ability to
# hotlink an image, given the content is model-generated and untrusted.
CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"

# The sandbox grants nothing at all. Scripts are already stripped, so there is
# no reason to allow them, and no reason to allow same-origin either.
SANDBOX = ""


def _detect(content: str) -> list[str]:
    return [label for label, pattern in _REPORT_PATTERNS if pattern.search(content)]


# nh3 preserves the *contents* of an allowed <style> tag verbatim — it sanitizes
# markup, not CSS. So CSS needs its own pass. The CSP would already block these
# (`default-src 'none'` stops @import and any non-data/https url()), but relying
# on a single control for a whole class of attack is how bypasses happen.
_STYLE_BLOCK_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style\s*>)", re.IGNORECASE | re.DOTALL)
_CSS_DANGEROUS = [
    # Pulls in a remote stylesheet, which can exfiltrate via selectors.
    (re.compile(r"@import[^;]*;?", re.IGNORECASE), ""),
    # Legacy IE script execution vector.
    (re.compile(r"expression\s*\([^)]*\)", re.IGNORECASE), ""),
    (re.compile(r"behavior\s*:[^;]*;?", re.IGNORECASE), ""),
    (re.compile(r"-moz-binding\s*:[^;]*;?", re.IGNORECASE), ""),
    # Any url() that is not a data: or https: reference — blocks
    # javascript:, http: mixed content, and remote beacons alike.
    (re.compile(r"url\s*\(\s*['\"]?\s*(?!data:|https:)[^)]*\)", re.IGNORECASE), "none"),
]


def _sanitize_css(css: str) -> tuple[str, bool]:
    cleaned = css
    for pattern, replacement in _CSS_DANGEROUS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned, cleaned != css


def _scrub_style_blocks(html: str) -> tuple[str, bool]:
    changed = False

    def _replace(match: re.Match[str]) -> str:
        nonlocal changed
        css, did = _sanitize_css(match.group(2))
        changed = changed or did
        return f"{match.group(1)}{css}{match.group(3)}"

    return _STYLE_BLOCK_RE.sub(_replace, html), changed


def sanitize_html(content: str) -> tuple[str, dict[str, Any]]:
    removed = _detect(content)

    content, css_changed = _scrub_style_blocks(content)
    if css_changed and "unsafe CSS" not in removed:
        removed.append("unsafe CSS directive")

    cleaned = nh3.clean(
        content,
        tags=ALLOWED_TAGS,
        attributes={k: set(v) for k, v in ALLOWED_ATTRIBUTES.items()},
        url_schemes=ALLOWED_URL_SCHEMES,
        attribute_filter=_attribute_filter,
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
        # Without this, stripping `<script>` leaves its *body* behind as visible
        # text — so `<script>alert(1)</script>` would render as "alert(1)".
        # Harmless, but it looks like a bypass and reads as one in review.
        clean_content_tags={"script", "iframe", "object", "embed", "applet", "form", "noscript"},
    )

    report: dict[str, Any] = {
        "kind": "html",
        "removed": removed,
        "modified": cleaned != content,
        "original_bytes": len(content),
        "sanitized_bytes": len(cleaned),
        "policy": "allowlist + sandboxed iframe (no scripts, no same-origin)",
    }
    if removed:
        log.warning("sanitize.removed", removed=removed, bytes=len(content))
    return cleaned, report


# Markdown is rendered client-side with raw HTML disabled, so the parser itself
# is the boundary. These two checks catch the cases that survive that: a raw
# HTML block the renderer might be configured to pass through, and a link whose
# scheme executes.
# The link target may itself contain parentheses — `javascript:alert(1)` is the
# common case — so the pattern allows one level of nesting rather than stopping
# at the first `)`, which would leave a stray bracket in the output.
_MD_LINK_RE = re.compile(
    r"\[([^\]]*)\]\(\s*(?:javascript|data|vbscript|file)\s*:(?:[^()\n]|\([^()\n]*\))*\)",
    re.IGNORECASE,
)
# Matches both opening and closing tags, so escaping leaves balanced visible text.
_MD_HTML_RE = re.compile(
    r"<\s*/?\s*(script|iframe|object|embed|form|style|link|base|applet)\b", re.IGNORECASE
)


def sanitize_markdown(content: str) -> tuple[str, dict[str, Any]]:
    removed: list[str] = []

    cleaned, n = _MD_LINK_RE.subn(r"[\1](#blocked-unsafe-link)", content)
    if n:
        removed.append(f"{n} unsafe link scheme(s)")

    if _MD_HTML_RE.search(cleaned):
        removed.append("raw HTML block(s)")
        # Neutralise by escaping the opening bracket: the tag becomes visible
        # text rather than markup, which is honest about what the model wrote.
        cleaned = _MD_HTML_RE.sub(lambda m: "&lt;" + m.group(0)[1:], cleaned)

    return cleaned, {
        "kind": "markdown",
        "removed": removed,
        "modified": cleaned != content,
        "original_bytes": len(content),
        "sanitized_bytes": len(cleaned),
        "policy": "raw HTML disabled in the renderer; unsafe URL schemes rewritten",
    }


def sanitize_artifact(kind: str, content: str) -> tuple[str, dict[str, Any]]:
    if kind == "html":
        return sanitize_html(content)
    return sanitize_markdown(content)
