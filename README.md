# Emoji Pack Exporter Bot

Send this Telegram bot a custom emoji. It fetches the **whole emoji pack** and turns it into files an AI can read, then uploads them to catbox.moe:

| File | Purpose |
|---|---|
| `<pack>.md` | **Give this one to your AI.** It has every emoji's `custom_emoji_id`, fallback emoji, format and a ready-to-paste HTML snippet, plus usage notes and links to the preview images. |
| `<pack>.json` | The same data in JSON, for scripts or for an AI to generate code from. |
| `<pack>_preview_N.png` | Numbered grids of the emoji (60 per image). The `#` under each emoji matches the `#` in the md/json, so an AI that can read images can pick emoji by how they look. `R` means repaintable (a one-colour emoji tinted to the text colour, good for button icons). |

If catbox.moe refuses the upload (it sometimes pauses uploads), the bot uses **litterbox** (temporary, 72h by default). The bot also sends every file straight to the chat, so you always have a copy.

## Setup

```bash
git clone https://github.com/TrishaAdio/emoji.git && cd emoji
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then put your BOT_TOKEN in .env
python bot.py
```

## Using it

You can send the bot any of these:
- one or more custom emoji (every pack they come from gets exported)
- a custom emoji sticker
- a pack link such as `https://t.me/addemoji/PackName`

Then give the `.md` link to your AI with a prompt like:

> Here is a Telegram custom emoji pack: <md link>. Look at the preview images and pick the best-looking emoji for a shop bot's main menu and buttons. Use only the exact `custom_emoji_id`s from the file.

## Requirements for *using* the emoji in your bots

Since Bot API 9.4, a bot can send custom emoji in private chats, groups and supergroups **if the bot's owner has Telegram Premium**. It can also put them on buttons with `icon_custom_emoji_id`. Exporting a pack with this bot doesn't need Premium.
