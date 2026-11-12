#!/usr/bin/env python3
"""
Юзербот: автоматическая авторизация в MRKT через @mrkt.

Токен кидаешь в «Избранное» (Saved Messages) в виде:
    /login ВАШ_ТОКЕН
или просто токеном одной строкой.
Ссылку из кнопки подтверждения бот пришлёт тебе же в «Избранное»
(или в REPORT_TO), а служебные сообщения удалит.
"""

import asyncio
import os
import re
import time
from urllib.parse import urlparse, parse_qs

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_NAME = os.getenv("TG_SESSION", "mrkt_userbot")
SESSION_STRING = os.getenv("TG_SESSION_STRING")          # опционально, для headless
MRKT = os.getenv("MRKT_BOT", "mrkt")
REPORT_TO = os.getenv("REPORT_TO", "me")                 # 'me' = Избранное
TIMEOUT = int(os.getenv("MRKT_TIMEOUT", "60"))           # сколько ждём кнопку, сек

CONFIRM_WORDS = (
    "подтверд", "принять", "прийняти", "войти", "вход",
    "продолжить", "confirm", "accept", "login", "sign in", "continue",
)

_lock = asyncio.Lock()
_sent_links: set[str] = set()


def normalize_token(raw: str) -> str:
    """'/login x', 'https://...?token=x', 'https://.../x' -> чистый токен."""
    raw = raw.strip()
    m = re.match(r"^/login\s+(\S+)$", raw, re.I)
    if m:
        raw = m.group(1)
    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        query = parse_qs(parsed.query)
        for key in ("token", "code", "t", "auth"):
            if key in query:
                return query[key][0]
        segments = [s for s in parsed.path.split("/") if s]
        if segments:
            return segments[-1]
    return raw


def extract_confirm_url(message) -> str | None:
    """Достаёт URL из кнопки подтверждения, иначе — первую URL-кнопку, иначе — ссылку из текста."""
    candidates: list[tuple[str, str]] = []
    for row in (message.buttons or []):
        for button in row:
            url = getattr(button, "url", None)
            if url:
                candidates.append(((button.text or "").lower(), url))

    for text, url in candidates:
        if any(word in text for word in CONFIRM_WORDS):
            return url
    if candidates:
        return candidates[0][1]

    m = re.search(r"https?://\S+", message.raw_text or "")
    return m.group(0) if m else None


async def request_confirm_link(client: TelegramClient, mrkt_entity, token: str) -> str | None:
    """Шлёт /start <token> и ждёт ответ с кнопкой. Возвращает (ссылку, сообщение)."""
    command = await client.send_message(mrkt_entity, f"/start {token}")
    deadline = time.monotonic() + TIMEOUT
    try:
        while time.monotonic() < deadline:
            try:
                incoming = await client.wait_for(
                    events.NewMessage(chats=mrkt_entity, incoming=True),
                    timeout=max(1.0, deadline - time.monotonic()),
                )
            except asyncio.TimeoutError:
                break

            url = extract_confirm_url(incoming)
            if url:
                return url, incoming, command
    except FloodWaitError as e:
        raise RuntimeError(f"FloodWait: подожди {e.seconds} сек.") from e

    return None, None, command


async def main() -> None:
    if SESSION_STRING:
        client = TelegramClient(SESSION_STRING, API_ID, API_HASH)
    else:
        client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

    await client.start()
    me = await client.get_me()
    mrkt_entity = await client.get_entity(MRKT)
    print(f"Залогинен как {me.first_name} (@{me.username}), цель: @{MRKT}")

    @client.on(events.NewMessage(chats="me", outgoing=True))
    async def on_token(event: events.NewMessage.Event) -> None:
        raw = (event.raw_text or "").strip()
        if not raw or "\n" in raw:
            return

        is_command = raw.lower().startswith("/login")
        if not is_command and (raw.startswith("/") or raw.startswith("http")):
            return
        if not is_command and " " in raw:
            return
        if raw in _sent_links:
            return

        token = normalize_token(raw)
        if not token:
            return

        async with _lock:
            status = await event.reply("Отправляю токен в @mrkt…")
            try:
                link, confirmation, command = await request_confirm_link(client, mrkt_entity, token)
            except Exception as exc:
                await status.edit(f"Ошибка: {exc}")
                return

            if not link:
                await status.edit("MRKT не прислал кнопку подтверждения (таймаут).")
                return

            _sent_links.add(raw)
            _sent_links.add(link)

            await client.send_message(REPORT_TO, link)

            # чистим: сообщение с командой и сообщение с подтверждением
            for msg in (command, confirmation):
                try:
                    await msg.delete()
                except Exception:
                    pass

            try:
                await status.delete()
            except Exception:
                pass

    print("Жду токен в «Избранном»…")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())