"""Display labels for stored fields; pure, and safe for hostile input."""

import re

_AUTH_LABELS = {
    "": "none listed",
    "no": "none listed",
    "apikey": "API key",
    "oauth": "OAuth",
    "x-mashape-key": "API key",
    "user-agent": "User-Agent header",
}
_AUTH_UNCLEAR = "unclear"
_CATEGORY_ALIASES = {"financial": "finance", "developer tools": "development"}
_WHITESPACE = re.compile(r"\s+")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_BREAK = re.compile(r"</?(?:br|p|div|li|ul|ol|h[1-6]|tr|td)\b[^>]*>", re.IGNORECASE)
_HTML_TAG = re.compile(r"</?[A-Za-z!][^>]*>")
_MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)")
_ELLIPSIS = "…"


def declared_auth_label(raw: str | None) -> str:
    """Map a source list's declared auth to a closed vocabulary; anything else is unclear."""
    if raw is None:
        return _AUTH_LABELS[""]
    return _AUTH_LABELS.get(raw.replace("`", "").strip().lower(), _AUTH_UNCLEAR)


def category_label(tag: str) -> str:
    """Lowercase a tag, treat underscores as spaces, collapse whitespace, apply aliases."""
    label = _WHITESPACE.sub(" ", tag.lower().replace("_", " ")).strip()
    return _CATEGORY_ALIASES.get(label, label)


def plain_text(value: str, limit: int) -> str:
    """Reduce markup in a stored description to plain text of at most `limit` characters.

    Removes HTML tags and comments, replaces markdown links with their text and collapses
    whitespace. A longer result is cut at a word boundary and ends in an ellipsis, which counts
    toward the limit. The output is still untrusted text: templates must keep autoescaping it.
    """
    text = _HTML_COMMENT.sub("", value)
    text = _HTML_BREAK.sub(" ", text)
    text = _HTML_TAG.sub("", text)
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    if limit < 1:
        return ""
    room = limit - len(_ELLIPSIS)
    head = text[:room]
    if text[room] != " " and " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip() + _ELLIPSIS
