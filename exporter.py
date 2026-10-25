"""Turn a Telegram custom emoji sticker set into AI-friendly files.

Outputs:
  * numbered preview sheets (PNG) so a vision-capable AI can see each emoji
  * a JSON file with every custom_emoji_id + ready-to-paste snippets
  * a Markdown file (the main "feed this to your AI" document)
"""
from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

PAGE_SIZE = 60  # emoji per preview sheet (keeps images readable after AI downscaling)
COLS = 10
CELL = 104  # square cell size in px
THUMB = 88  # max emoji size inside a cell
LABEL_H = 22
PAD = 12
HEADER_H = 48

BG = (23, 33, 43)  # Telegram dark theme background
CELL_BG = (43, 54, 68)
TEXT = (235, 240, 245)
MUTED = (140, 155, 170)
DEFAULT_FALLBACK = "⭐"


@dataclass
class PreviewPage:
    first: int  # first emoji number on this sheet (1-based)
    last: int
    filename: str
    png: bytes
    url: str | None = None
    expires: str | None = None


@dataclass
class EmojiPack:
    name: str
    title: str
    emoji: list[dict]
    previews: list[PreviewPage] = field(default_factory=list)

    @property
    def link(self) -> str:
        return f"https://t.me/addemoji/{self.name}"


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1 ships a scalable font
    except TypeError:
        return ImageFont.load_default()


def sticker_format(sticker: dict) -> str:
    if sticker.get("is_video"):
        return "video"
    if sticker.get("is_animated"):
        return "animated"
    return "static"


def build_pack(sticker_set: dict) -> EmojiPack:
    """Extract the useful per-emoji data from a getStickerSet result."""
    entries = []
    for sticker in sticker_set.get("stickers", []):
        cid = sticker.get("custom_emoji_id")
        if not cid:
            continue
        fallback = sticker.get("emoji") or DEFAULT_FALLBACK
        entries.append({
            "index": len(entries) + 1,
            "custom_emoji_id": cid,
            "fallback": fallback,
            "format": sticker_format(sticker),
            "needs_repainting": bool(sticker.get("needs_repainting")),
            "html": f'<tg-emoji emoji-id="{cid}">{fallback}</tg-emoji>',
            "markdown_v2": f"![{fallback}](tg://emoji?id={cid})",
        })
    return EmojiPack(sticker_set["name"], sticker_set.get("title") or sticker_set["name"], entries)


def render_previews(pack: EmojiPack, images: dict[str, bytes | None]) -> list[PreviewPage]:
    """Draw numbered grid sheets. `images` maps custom_emoji_id -> thumbnail bytes (WEBP/JPG/PNG)."""
    label_font, header_font = _font(15), _font(20)
    total = len(pack.emoji)
    pages = []

    for page_no, start in enumerate(range(0, total, PAGE_SIZE), 1):
        chunk = pack.emoji[start:start + PAGE_SIZE]
        rows = math.ceil(len(chunk) / COLS)
        width = PAD * 2 + COLS * CELL
        height = HEADER_H + rows * (CELL + LABEL_H) + PAD
        sheet = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(sheet)

        first, last = chunk[0]["index"], chunk[-1]["index"]
        draw.text((PAD, 14), f"{pack.name}  |  #{first}-#{last} of {total}", font=header_font, fill=TEXT)

        for i, item in enumerate(chunk):
            x = PAD + (i % COLS) * CELL
            y = HEADER_H + (i // COLS) * (CELL + LABEL_H)
            draw.rounded_rectangle((x + 3, y + 3, x + CELL - 3, y + CELL - 3), radius=12, fill=CELL_BG)

            raw = images.get(item["custom_emoji_id"])
            pasted = False
            if raw:
                try:
                    emoji_img = Image.open(io.BytesIO(raw)).convert("RGBA")
                    emoji_img.thumbnail((THUMB, THUMB), Image.LANCZOS)
                    ox = x + (CELL - emoji_img.width) // 2
                    oy = y + (CELL - emoji_img.height) // 2
                    sheet.paste(emoji_img, (ox, oy), emoji_img)
                    pasted = True
                except Exception:  # corrupt / unsupported thumbnail
                    pasted = False
            if not pasted:
                draw.text((x + CELL / 2, y + CELL / 2), "no preview", font=label_font, fill=MUTED, anchor="mm")

            label = f"#{item['index']}" + (" R" if item["needs_repainting"] else "")
            draw.text((x + CELL / 2, y + CELL + LABEL_H / 2 - 2), label, font=label_font, fill=TEXT, anchor="mm")

        buf = io.BytesIO()
        sheet.save(buf, "PNG", optimize=True)
        pages.append(PreviewPage(first, last, f"{pack.name}_preview_{page_no}.png", buf.getvalue()))

    pack.previews = pages
    return pages


def _preview_ref(page: PreviewPage) -> str:
    return page.url or f"{page.filename} (attached file)"


def build_json(pack: EmojiPack, generated_at: str) -> str:
    data = {
        "pack": {
            "name": pack.name,
            "title": pack.title,
            "link": pack.link,
            "emoji_count": len(pack.emoji),
            "generated_at": generated_at,
            "preview_images": [
                {"emoji_numbers": f"{p.first}-{p.last}", "url": _preview_ref(p)} for p in pack.previews
            ],
        },
        "usage": {
            "requirement": "Bot API 9.4+: the bot owner must have Telegram Premium "
                           "(or the group uses this pack as its group emoji pack).",
            "html": '<tg-emoji emoji-id="CUSTOM_EMOJI_ID">FALLBACK</tg-emoji>',
            "markdown_v2": "![FALLBACK](tg://emoji?id=CUSTOM_EMOJI_ID)",
            "entity": {"type": "custom_emoji", "offset": "UTF-16 offset", "length": "UTF-16 length of FALLBACK",
                       "custom_emoji_id": "CUSTOM_EMOJI_ID"},
            "button": {"text": "Label", "callback_data": "...", "icon_custom_emoji_id": "CUSTOM_EMOJI_ID"},
            "needs_repainting": "true = monochrome emoji that takes the text/accent colour (great for UI icons)",
        },
        "emoji": pack.emoji,
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_markdown(pack: EmojiPack, generated_at: str, json_url: str | None) -> str:
    title = pack.title.replace("|", "\\|")
    lines = [
        f"# Telegram custom emoji pack: {title}",
        "",
        f"- Pack name: `{pack.name}` ({pack.link})",
        f"- Emoji count: {len(pack.emoji)}",
        f"- Generated: {generated_at}",
        f"- JSON version: {json_url or pack.name + '.json (attached file)'}",
        "- Preview images (the `#` number under each emoji matches the `#` column below; `R` = repaintable):",
    ]
    lines += [f"  - #{p.first}-#{p.last}: {_preview_ref(p)}" for p in pack.previews]
    lines += [
        "",
        "## Instructions for the AI",
        "",
        "1. Open the preview images to see what each emoji looks like, and pick them by `#` number.",
        "2. Copy the exact `custom_emoji_id` from the table. Never invent IDs.",
        "3. Always keep the listed fallback emoji: Telegram shows it where custom emoji can't render.",
        "",
        "## How to use in a Telegram bot (Bot API 9.4+)",
        "",
        "Requirement: the bot owner has Telegram Premium (or the chat uses this pack as its group emoji pack).",
        "",
        "- HTML (`parse_mode=HTML`): `<tg-emoji emoji-id=\"ID\">FALLBACK</tg-emoji>`",
        "- MarkdownV2 (`parse_mode=MarkdownV2`): `![FALLBACK](tg://emoji?id=ID)`",
        "- Entities: `{\"type\": \"custom_emoji\", \"offset\": <utf16>, \"length\": <utf16 len of FALLBACK>, "
        "\"custom_emoji_id\": \"ID\"}`",
        "- Buttons (`InlineKeyboardButton` / `KeyboardButton`): `\"icon_custom_emoji_id\": \"ID\"`; "
        "optional `\"style\"` for button colour",
        "- `Repaint = yes`: monochrome emoji tinted to the text colour, ideal for icons on buttons and menus.",
        "",
        "## Emoji",
        "",
        "| # | Fallback | custom_emoji_id | Format | Repaint | HTML |",
        "|---|---|---|---|---|---|",
    ]
    for e in pack.emoji:
        lines.append(
            f"| {e['index']} | {e['fallback']} | `{e['custom_emoji_id']}` | {e['format']} | "
            f"{'yes' if e['needs_repainting'] else 'no'} | `{e['html']}` |"
        )
    return "\n".join(lines) + "\n"
