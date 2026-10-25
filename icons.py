"""Custom emoji used by the bot, from the "TG iOS & macOS Icons" pack (https://t.me/addemoji/tgmacicons).

Every icon in this pack is repaintable: Telegram tints it to the text/button colour, so it fits light
and dark themes. Custom emoji need Bot API 9.4+ and a bot owner with Telegram Premium; without that,
Telegram shows the fallback emoji. Set USE_CUSTOM_EMOJI=0 in .env to always send plain emoji.
"""
from __future__ import annotations

import os
import re

ENABLED = os.getenv("USE_CUSTOM_EMOJI", "1").strip().lower() not in ("0", "false", "no", "off")

# name: (custom_emoji_id, fallback emoji)        pack # - what it looks like
ICONS: dict[str, tuple[str, str]] = {
    "bot":      ("5258093637450866522", "🤖"),   # 19  robot
    "gem":      ("5280962371207077415", "💎"),   # 70  outlined gem
    "search":   ("5429571366384842791", "🔎"),   # 126 magnifier
    "download": ("5258336354642697821", "⬇️"),   # 74  arrow into tray
    "brush":    ("5258215635996908355", "🎨"),   # 25  paint brush
    "upload":   ("5260379144167890225", "⬆️"),   # 84  arrow out of tray
    "done":     ("5260416304224936047", "✅"),   # 78  check in circle
    "error":    ("5260342697075416641", "❌"),   # 28  cross in circle
    "warning":  ("5258474669769497337", "❗"),   # 69  exclamation in circle
    "info":     ("5258503720928288433", "ℹ️"),   # 86  "i" in circle
    "doc":      ("5257965810634202885", "📄"),   # 38  document
    "json":     ("5257965174979042426", "📝"),   # 15  text lines
    "image":    ("5258254475386167466", "🖼"),   # 83  picture
    "link":     ("5260730055880876557", "🔗"),   # 29  chain link
    "clock":    ("5258419835922030550", "🕔"),   # 75  clock with arrow
    "bulb":     ("5258216851472654189", "💡"),   # 46  light bulb
}


def e(name: str) -> str:
    """Icon as HTML (for parse_mode=HTML text and captions)."""
    cid, fallback = ICONS[name]
    if not ENABLED:
        return fallback
    return f'<tg-emoji emoji-id="{cid}">{fallback}</tg-emoji>'


def button(text: str, icon: str | None = None, style: str | None = None, **action) -> dict:
    """InlineKeyboardButton with an optional icon and style ("primary" blue, "success" green, "danger" red)."""
    btn = {"text": text, **action}
    if icon and ENABLED:
        btn["icon_custom_emoji_id"] = ICONS[icon][0]
    if style:
        btn["style"] = style
    return btn


_TG_EMOJI_RE = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', re.S)


def has_custom(params: dict) -> bool:
    text = f"{params.get('text', '')}{params.get('caption', '')}"
    return "<tg-emoji" in text or "icon_custom_emoji_id" in repr(params.get("reply_markup"))


def strip(params: dict) -> dict:
    """Copy of Bot API params with every custom emoji replaced by its fallback."""
    out = dict(params)
    for key in ("text", "caption"):
        if isinstance(out.get(key), str):
            out[key] = _TG_EMOJI_RE.sub(r"\1", out[key])
    markup = out.get("reply_markup")
    if isinstance(markup, dict) and "inline_keyboard" in markup:
        out["reply_markup"] = {**markup, "inline_keyboard": [
            [{k: v for k, v in btn.items() if k != "icon_custom_emoji_id"} for btn in row]
            for row in markup["inline_keyboard"]
        ]}
    return out
