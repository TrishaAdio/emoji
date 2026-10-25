"""Telegram bot: send it a custom emoji -> get the whole pack as AI-ready files, uploaded to catbox.moe.

Run:  BOT_TOKEN=123:abc python bot.py
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

import exporter
import icons
import uploader
from icons import e

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("emoji-pack-bot")


ENV_PATH = Path(__file__).with_name(".env")


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Values in .env override existing env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'").strip()
            if value:
                os.environ[key.strip()] = value


load_dotenv(ENV_PATH)

TOKEN = re.sub(r"\s+", "", os.getenv("BOT_TOKEN", ""))
if TOKEN.lower().startswith("bot") and ":" in TOKEN:  # people often paste "bot123:abc"
    TOKEN = TOKEN[3:]
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
ALLOWED = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x}
MAX_PACKS_PER_MESSAGE = 5
DOWNLOAD_WORKERS = 4  # parallel thumbnail downloads (more just triggers Telegram flood control)
DOWNLOAD_BUDGET = float(os.getenv("DOWNLOAD_BUDGET", "150"))  # max seconds spent on previews per pack
API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"
PACK_LINK_RE = re.compile(r"(?:t\.me|telegram\.me)/(?:addemoji|addstickers)/([A-Za-z0-9_]+)")

EXAMPLE_PACK = "https://t.me/addemoji/tgmacicons"


def help_text() -> str:
    return (
        f"{e('gem')} <b>Emoji Pack Exporter</b>\n\n"
        "Send me any <b>custom emoji</b> (or several), a custom emoji sticker, or a pack link like "
        "<code>t.me/addemoji/PackName</code>.\n\n"
        "I'll fetch the <b>whole pack</b> and build:\n"
        f"{e('image')} numbered preview sheets, so an AI can <i>see</i> every emoji\n"
        f"{e('doc')} a Markdown file with every <code>custom_emoji_id</code> and ready-to-paste snippets\n"
        f"{e('json')} the same data as JSON\n\n"
        f"{e('upload')} Everything is uploaded to catbox.moe and sent here as files.\n\n"
        f"{e('bulb')} Give the Markdown link to your AI and ask it to pick the best-looking emoji "
        "for your bots."
    )


def help_markup() -> dict:
    return {"inline_keyboard": [[icons.button("Example pack", "link", "primary", url=EXAMPLE_PACK)]]}


class TelegramError(Exception):
    pass


# ---------------------------------------------------------------- Telegram API helpers

def api(method: str, files: dict | None = None, http_timeout: float = 60, max_wait: float = 60, **params):
    """Call a Bot API method. Retries on flood control (429) if the wait is at most `max_wait` seconds."""
    for _ in range(5):
        if files:
            resp = requests.post(f"{API}/{method}", data=params, files=files, timeout=http_timeout)
        else:
            resp = requests.post(f"{API}/{method}", json=params, timeout=http_timeout)
        try:
            data = resp.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {resp.status_code}, non-JSON response") from None
        if data.get("ok"):
            return data["result"]
        retry = data.get("parameters", {}).get("retry_after")
        if data.get("error_code") == 429 and retry:
            if retry > max_wait:
                raise TelegramError(f"{method}: flood control, retry after {retry}s (limit {max_wait}s)")
            log.warning("%s: flood control, waiting %ss", method, retry)
            time.sleep(retry + 1)
            continue
        raise TelegramError(f"{method}: {data.get('description')}")
    raise TelegramError(f"{method}: too many retries")


def api_msg(method: str, files: dict | None = None, http_timeout: float = 60, max_wait: float = 60,
            **params):
    """api() for messages that may contain custom emoji.

    If Telegram refuses the custom emoji (e.g. the bot owner has no Telegram Premium), the message
    is resent with plain fallback emoji and custom emoji are switched off for the rest of the run.
    """
    def call(p: dict):
        if files:  # multipart: nested objects must be JSON strings
            p = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in p.items()}
        return api(method, files=files, http_timeout=http_timeout, max_wait=max_wait, **p)

    if not icons.ENABLED:
        return call(icons.strip(params))
    try:
        return call(params)
    except TelegramError as exc:
        reason = str(exc)
        if not icons.has_custom(params) or "not modified" in reason or "flood control" in reason:
            raise
        result = call(icons.strip(params))
        icons.ENABLED = False
        log.warning("Telegram refused custom emoji (%s); using plain emoji from now on. "
                    "Custom emoji need a bot owner with Telegram Premium.", reason)
        return result


def send(chat_id: int, text: str, reply_to: int | None = None, **extra) -> dict:
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "link_preview_options": {"is_disabled": True}, **extra}
    if reply_to:
        params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    return api_msg("sendMessage", **params)


def edit(chat_id: int, message_id: int, text: str, reply_markup: dict | None = None,
         max_wait: float = 5) -> bool:
    """Best-effort status update: never blocks long and never raises. Returns True on success."""
    params = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML",
              "link_preview_options": {"is_disabled": True}}
    if reply_markup:
        params["reply_markup"] = reply_markup
    try:
        api_msg("editMessageText", http_timeout=15, max_wait=max_wait, **params)
        return True
    except (TelegramError, requests.RequestException) as exc:
        if "not modified" in str(exc):
            return True
        log.warning("edit failed: %s", exc)
        return False


def send_document(chat_id: int, filename: str, content: bytes, caption: str = "") -> None:
    api_msg("sendDocument", files={"document": (filename, content)}, http_timeout=120,
            chat_id=chat_id, caption=caption, parse_mode="HTML")


def download_file(file_id: str, deadline: float | None = None) -> bytes | None:
    """Download a small file (thumbnail). Never raises; gives up once `deadline` (monotonic) passes."""
    try:
        if deadline and time.monotonic() > deadline:
            return None
        wait_budget = max(0.0, (deadline - time.monotonic()) if deadline else 30.0)
        info = api("getFile", file_id=file_id, http_timeout=20, max_wait=min(15.0, wait_budget))
        if deadline and time.monotonic() > deadline:
            return None
        resp = requests.get(f"{FILE_API}/{info['file_path']}", timeout=(10, 20))
        resp.raise_for_status()
        return resp.content
    except Exception as exc:  # thumbnails are optional: never let one break the whole pack
        log.warning("download of %s failed: %s", file_id, exc)
        return None


# ---------------------------------------------------------------- pack processing

def find_pack_names(msg: dict) -> tuple[list[str], int]:
    """Return (unique sticker set names, number of custom emoji that had no pack)."""
    names: list[str] = []
    ids: list[str] = []

    for key in ("entities", "caption_entities"):
        for ent in msg.get(key, []):
            if ent.get("type") == "custom_emoji" and ent.get("custom_emoji_id"):
                ids.append(ent["custom_emoji_id"])

    sticker = msg.get("sticker")
    if sticker and sticker.get("set_name"):
        names.append(sticker["set_name"])

    text = msg.get("text") or msg.get("caption") or ""
    names += PACK_LINK_RE.findall(text)

    orphans = 0
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 200):  # getCustomEmojiStickers accepts up to 200 IDs
        for st in api("getCustomEmojiStickers", custom_emoji_ids=ids[i:i + 200]):
            if st.get("set_name"):
                names.append(st["set_name"])
            else:
                orphans += 1

    return list(dict.fromkeys(names)), orphans


def fetch_thumbnails(sticker_set: dict, progress=None) -> dict[str, bytes | None]:
    """Download a still preview for every emoji (thumbnail, or the file itself for static WEBP).

    Runs at most DOWNLOAD_BUDGET seconds in total; anything not done by then gets "no preview".
    `progress(done, total)` is called every few seconds so the user can see it's alive.
    """
    jobs: dict[str, str | None] = {}
    for st in sticker_set.get("stickers", []):
        cid = st.get("custom_emoji_id")
        if not cid:
            continue
        if st.get("thumbnail"):
            jobs[cid] = st["thumbnail"]["file_id"]
        elif not st.get("is_animated") and not st.get("is_video"):
            jobs[cid] = st["file_id"]
        else:
            jobs[cid] = None

    results: dict[str, bytes | None] = {cid: None for cid in jobs}
    todo = {cid: fid for cid, fid in jobs.items() if fid}
    total, done = len(todo), 0
    deadline = time.monotonic() + DOWNLOAD_BUDGET
    last_report = time.monotonic()

    pool = ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS)
    futures = {pool.submit(download_file, fid, deadline): cid for cid, fid in todo.items()}
    try:
        for fut in as_completed(futures, timeout=DOWNLOAD_BUDGET + 10):
            results[futures[fut]] = fut.result()
            done += 1
            if progress and time.monotonic() - last_report >= 3:
                last_report = time.monotonic()
                progress(done, total)
    except FuturesTimeout:
        log.warning("thumbnail download hit the time budget: %d/%d done", done, total)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def upload_safe(filename: str, content: bytes, mime: str) -> uploader.UploadResult | None:
    try:
        return uploader.upload(filename, content, mime)
    except uploader.UploadError as exc:
        log.error("upload of %s failed: %s", filename, exc)
        return None


def link_line(icon: str, label: str, res: uploader.UploadResult | None) -> str:
    if not res:
        return f"{e('warning')} {label}: <i>upload failed, sent as a file below</i>"
    return f'{e(icon)} {label}: <a href="{res.url}">{html.escape(res.url)}</a>'


def progress_bar(done: int, total: int, width: int = 10) -> str:
    filled = round(width * done / total) if total else width
    return "▰" * filled + "▱" * (width - filled)


def result_markup(pack: exporter.EmojiPack, md_res, json_res, pages, preview_results) -> dict:
    """Inline buttons: open the Markdown (main), JSON, each preview sheet and the pack itself."""
    rows = []
    if md_res:
        rows.append([icons.button("Markdown for your AI", "doc", "primary", url=md_res.url)])
    if json_res:
        rows.append([icons.button("JSON", "json", url=json_res.url)])
    previews = [icons.button(f"#{p.first}–{p.last}", "image", url=r.url)
                for p, r in zip(pages, preview_results) if r]
    rows += [previews[i:i + 3] for i in range(0, len(previews), 3)]
    rows.append([icons.button("Add pack to Telegram", "gem", "success", url=pack.link)])
    return {"inline_keyboard": rows}


def process_pack(chat_id: int, reply_to: int, set_name: str) -> None:
    name = html.escape(set_name)
    status = send(chat_id, f"{e('search')} Looking up pack <code>{name}</code>…", reply_to)["message_id"]

    try:
        sticker_set = api("getStickerSet", name=set_name)
    except TelegramError as exc:
        edit(chat_id, status, f"{e('error')} Couldn't load <code>{name}</code>\n"
                              f"<blockquote>{html.escape(str(exc))}</blockquote>")
        return
    if sticker_set.get("sticker_type") != "custom_emoji":
        edit(chat_id, status, f"{e('warning')} <code>{name}</code> is a regular sticker pack, "
                              "not a custom emoji pack.")
        return

    pack = exporter.build_pack(sticker_set)
    title = html.escape(pack.title)
    header = f"<b>{title}</b> · {len(pack.emoji)} emoji"
    edit(chat_id, status, f"{e('download')} {header}\nDownloading previews…")
    started = time.monotonic()
    thumbs = fetch_thumbnails(
        sticker_set,
        progress=lambda done, total: edit(
            chat_id, status, f"{e('download')} {header}\nDownloading previews…\n"
                             f"<code>{progress_bar(done, total)}</code> {done}/{total}"),
    )
    missing = sum(1 for e in pack.emoji if not thumbs.get(e["custom_emoji_id"]))
    log.info("%s: %d previews in %.1fs, %d missing", set_name, len(pack.emoji) - missing,
             time.monotonic() - started, missing)

    edit(chat_id, status, f"{e('brush')} {header}\nRendering preview sheets…")
    pages = exporter.render_previews(pack, thumbs)

    edit(chat_id, status, f"{e('upload')} {header}\nUploading to catbox.moe…")
    preview_results = []
    for page in pages:
        res = upload_safe(page.filename, page.png, "image/png")
        if res:
            page.url, page.expires = res.url, res.expires
        preview_results.append(res)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    json_bytes = exporter.build_json(pack, now).encode()
    json_res = upload_safe(f"{pack.name}.json", json_bytes, "application/json")
    md_bytes = exporter.build_markdown(pack, now, json_res.url if json_res else None).encode()
    md_res = upload_safe(f"{pack.name}.md", md_bytes, "text/markdown")

    uploads = [r for r in (md_res, json_res, *preview_results) if r]
    expiry = next((r.expires for r in uploads if r.host == "litterbox"), None)

    lines = [
        f"{e('done')} <b>{title}</b> is ready",
        f"<a href=\"{pack.link}\">{html.escape(pack.name)}</a> · {len(pack.emoji)} emoji · "
        f"{len(pages)} preview sheet{'s' if len(pages) != 1 else ''}",
        "",
        f"{e('bulb')} <b>Give the Markdown link to your AI:</b>",
        link_line("doc", "Markdown", md_res),
        link_line("json", "JSON", json_res),
    ]
    lines += [link_line("image", f"#{p.first}–{p.last}", r) for p, r in zip(pages, preview_results)]
    if expiry:
        lines += ["", f"{e('clock')} <i>catbox refused the upload, so these are litterbox links "
                      f"(expire in {expiry}). The files are also attached below.</i>"]
    if missing:
        lines += ["", f"{e('warning')} {missing} emoji had no preview; their IDs are still in the files."]
    summary = "\n".join(lines)

    # A taste of the pack itself (renders when custom emoji are available).
    demo = "".join(item["html"] for item in pack.emoji[:12]) if icons.ENABLED else ""
    markup = result_markup(pack, md_res, json_res, pages, preview_results)
    if not (demo and edit(chat_id, status, f"{summary}\n\n{demo}", markup, max_wait=30)):
        if not edit(chat_id, status, summary, markup, max_wait=30):
            send(chat_id, summary, reply_to, reply_markup=markup)

    # Always attach the files too, so nothing is lost if an upload failed or litterbox expires.
    send_document(chat_id, f"{pack.name}.md", md_bytes, f"{e('doc')} <b>Feed this to your AI</b>")
    send_document(chat_id, f"{pack.name}.json", json_bytes, f"{e('json')} Same data as JSON")
    for page in pages:
        send_document(chat_id, page.filename, page.png, f"{e('image')} Preview #{page.first}–{page.last}")


def handle_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")
    if ALLOWED and user_id not in ALLOWED:
        return

    text = (msg.get("text") or "").strip()
    if text.split("@")[0].split(" ")[0] in ("/start", "/help"):
        send(chat_id, help_text(), reply_markup=help_markup())
        return

    names, orphans = find_pack_names(msg)
    if not names:
        hint = (f"{e('info')} Those emoji don't belong to a public pack." if orphans else
                f"{e('info')} Send me a <b>custom emoji</b> or a <code>t.me/addemoji/…</code> link.\n"
                "Need help? /help")
        send(chat_id, hint, msg["message_id"])
        return

    if len(names) > MAX_PACKS_PER_MESSAGE:
        send(chat_id, f"{e('warning')} Found {len(names)} packs; exporting the first {MAX_PACKS_PER_MESSAGE}.",
             msg["message_id"])
    for name in names[:MAX_PACKS_PER_MESSAGE]:
        try:
            process_pack(chat_id, msg["message_id"], name)
        except Exception as exc:  # keep the bot alive on unexpected errors
            log.exception("failed to process %s", name)
            send(chat_id, f"{e('error')} Something went wrong with <code>{html.escape(name)}</code>\n"
                          f"<blockquote>{html.escape(str(exc))}</blockquote>", msg["message_id"])


def main() -> None:
    source = f"{ENV_PATH}" if ENV_PATH.exists() else "BOT_TOKEN environment variable (no .env file found)"
    masked = f"{TOKEN[:6]}…{TOKEN[-4:]}" if len(TOKEN) > 12 else repr(TOKEN)
    if not TOKEN:
        raise SystemExit(f"No BOT_TOKEN found. Put BOT_TOKEN=<token from @BotFather> in {ENV_PATH}")
    if "replace-me" in TOKEN or not TOKEN_RE.match(TOKEN):
        raise SystemExit(f"BOT_TOKEN {masked} (from {source}) doesn't look like a real token.\n"
                         "It should look like 123456789:AAH... - copy it from @BotFather.")
    try:
        me = api("getMe")
    except TelegramError as exc:
        raise SystemExit(f"Telegram rejected BOT_TOKEN {masked} (from {source}): {exc}\n"
                         "The token is wrong or was revoked. In @BotFather: /mybots -> your bot -> "
                         "API Token, then paste the current token into .env.")
    log.info("running as @%s%s, custom emoji %s", me["username"],
             f" (allowed users: {sorted(ALLOWED)})" if ALLOWED else " (open to everyone)",
             "on" if icons.ENABLED else "off")
    api("setMyCommands", commands=[{"command": "help", "description": "How to use this bot"}])

    offset = 0
    while True:
        try:
            updates = api("getUpdates", http_timeout=70, offset=offset, timeout=50,
                          allowed_updates=["message"])
        except (TelegramError, requests.RequestException) as exc:
            log.warning("polling error: %s", exc)
            time.sleep(3)
            continue
        for upd in updates:
            offset = upd["update_id"] + 1
            if "message" in upd:
                try:
                    handle_message(upd["message"])
                except Exception:
                    log.exception("error handling update %s", upd["update_id"])


if __name__ == "__main__":
    main()
