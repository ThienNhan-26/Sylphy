"""Convert display-formatted assistant text into natural TTS input.

The UI keeps the original response. This module prepares a separate speech
copy by removing Markdown decoration, emoji, pictographs, and decorative
Unicode symbols while preserving ordinary words and sentence punctuation.
"""
from __future__ import annotations

import re
import unicodedata


_MARKDOWN_LINK = re.compile(r"!\[([^\]]*)\]\([^)]*\)|\[([^\]]+)\]\([^)]*\)")
_FENCED_CODE = re.compile(r"\s*```[\s\S]*?```\s*")
_INLINE_CODE = re.compile(r"`{1,3}([^`]+)`{1,3}")
_HTML_TAG = re.compile(r"<[^>]*>")
_SHORTCODE = re.compile(r"(?<!\w):[a-zA-Z0-9_+-]{1,40}:(?!\w)")
_MARKDOWN_MARKS = re.compile(r"(?<!\\)(?:\*{1,3}|_{1,3}|~~|#{1,6}\s*)")
_BULLET_PREFIX = re.compile(r"(?m)^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_KEYCAP = re.compile(r"(?i)(?:[#*0-9])\ufe0f?\u20e3")
_SPACES = re.compile(r"[ \t]{2,}")
_BLANK_LINES = re.compile(r"\n{3,}")

# Symbol blocks commonly used for emoji, UI icons, pictographs, and decorative
# glyphs. Unicode-category checks below cover symbols outside these ranges.
_ICON_RANGES = (
    (0x2190, 0x21FF),    # arrows
    (0x2300, 0x23FF),    # technical / miscellaneous symbols
    (0x25A0, 0x25FF),    # geometric shapes
    (0x2600, 0x27BF),    # miscellaneous symbols and dingbats
    (0x2900, 0x297F),    # supplemental arrows
    (0x2B00, 0x2BFF),    # miscellaneous symbols and arrows
    (0x1F000, 0x1FAFF),  # emoji and pictographs
    (0x1F1E6, 0x1F1FF),  # regional indicator flags
)


def _is_icon_or_emoji(char: str) -> bool:
    """Return whether a character is a pictograph/decorative symbol for TTS."""
    code = ord(char)
    if any(start <= code <= end for start, end in _ICON_RANGES):
        return True

    category = unicodedata.category(char)
    if category in {"So", "Cf"}:
        return True

    return (
        0xFE00 <= code <= 0xFE0F          # variation selectors
        or 0xE0100 <= code <= 0xE01EF     # supplementary variation selectors
        or 0x1F3FB <= code <= 0x1F3FF     # skin-tone modifiers
        or 0xE0020 <= code <= 0xE007F     # emoji tag characters
        or code == 0x20E3                # keycap combining mark
    )


def prepare_speech_text(text: str) -> str:
    """Return readable plain text for TTS without mutating the displayed reply."""
    if not isinstance(text, str):
        raise TypeError("speech text must be a string")

    value = unicodedata.normalize("NFKC", text)

    # Remove complete code blocks before handling inline code.
    value = _FENCED_CODE.sub(" ", value)
    value = _MARKDOWN_LINK.sub(lambda match: match.group(1) or match.group(2) or "", value)
    value = _INLINE_CODE.sub(r"\1", value)
    value = _HTML_TAG.sub(" ", value)
    value = _SHORTCODE.sub(" ", value)
    value = _KEYCAP.sub(" ", value)
    value = _MARKDOWN_MARKS.sub("", value)
    value = _BULLET_PREFIX.sub("", value)
    value = "".join(" " if _is_icon_or_emoji(char) else char for char in value)
    value = value.replace(r"\*", "*").replace(r"\_", "_")

    value = _SPACES.sub(" ", value)
    value = _BLANK_LINES.sub("\n\n", value)
    # Keep sentence boundaries and punctuation while removing gaps left by icons.
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"([([{])\s+", r"\1", value)
    value = re.sub(r"\s+([)\]}])", r"\1", value)
    return value.strip()
