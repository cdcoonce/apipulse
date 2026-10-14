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


def declared_auth_label(raw: str | None) -> str:
    """Map a source list's declared auth to a closed vocabulary; anything else is unclear."""
    if raw is None:
        return _AUTH_LABELS[""]
    return _AUTH_LABELS.get(raw.replace("`", "").strip().lower(), _AUTH_UNCLEAR)


def category_label(tag: str) -> str:
    """Lowercase a tag, treat underscores as spaces, collapse whitespace, apply aliases."""
    label = _WHITESPACE.sub(" ", tag.lower().replace("_", " ")).strip()
    return _CATEGORY_ALIASES.get(label, label)
