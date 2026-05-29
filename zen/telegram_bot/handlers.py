"""Message handler logic for the Telegram bot.

The pure helpers (`process_message`, `format_reply`) are decoupled from
aiogram primitives so they can be unit-tested without a live bot.
"""
from __future__ import annotations

from html import escape
from uuid import uuid4

import httpx

from zen.config.settings import Settings
from zen.models.requests import UserSqlRequest
from zen.models.responses import GeneratedSqlResponse
from zen.telegram_bot.client import AgentClientProtocol

# Distinctly-Vietnamese base letters; combined with the Latin Extended
# Additional block (U+1EA0–U+1EF9) this gives a dependency-free EN/VI detector.
_VI_LETTERS = "ăĂâÂđĐêÊôÔơƠưƯ"

# Fixed user-facing strings localized to the request language (EN fallback).
_GUIDE = {
    "en": (
        "📊 To run: open Metabase → select {label} {dbs} → "
        "New → SQL query, then paste the SQL above."
    ),
    "vi": (
        "📊 Để chạy: mở Metabase → chọn {label} {dbs} → "
        "New → SQL query, rồi dán đoạn SQL ở trên."
    ),
}
_DB_LABEL = {"en": ("database", "databases"), "vi": ("cơ sở dữ liệu", "cơ sở dữ liệu")}
_REVIEW_WARNING = {
    "en": "⚠️ AI-generated — review before running",
    "vi": "⚠️ Nội dung do AI tạo — vui lòng kiểm tra trước khi chạy",
}
_ACK = {
    "en": "✅ Request received — one moment…",
    "vi": "✅ Đã nhận yêu cầu — chờ một chút…",
}


def detect_lang(text: str) -> str:
    """Tiny EN/VI detector: Vietnamese diacritics/letters -> 'vi', else 'en'."""
    for ch in text:
        if 0x1EA0 <= ord(ch) <= 0x1EF9 or ch in _VI_LETTERS:
            return "vi"
    return "en"


def format_ack(lang: str) -> str:
    """Immediate acknowledgement sent the moment a request arrives."""
    return _ACK.get(lang, _ACK["en"])


def format_reply(resp: GeneratedSqlResponse, lang: str = "en") -> str:
    if resp.error_code:
        parts = [f"⚠️ Request could not be completed: {escape(resp.error_code)}"]
        if resp.error_message:
            parts.append(escape(resp.error_message))
        return "\n".join(parts)
    if resp.chat_reply:
        # Conversational (non-SQL) reply — just the message, no SQL chrome.
        return escape(resp.chat_reply)
    parts: list[str] = []
    if resp.sql:
        parts.append(f'<pre><code class="language-sql">{escape(resp.sql)}</code></pre>')
    if resp.explanation:
        parts.append("")
        parts.append(escape(resp.explanation))
    if resp.metabase_databases:
        dbs = ", ".join(f"<b>{escape(d)}</b>" for d in resp.metabase_databases)
        singular, plural = _DB_LABEL.get(lang, _DB_LABEL["en"])
        label = singular if len(resp.metabase_databases) == 1 else plural
        parts.append("")
        parts.append(_GUIDE.get(lang, _GUIDE["en"]).format(label=label, dbs=dbs))
    parts.append("")
    parts.append(_REVIEW_WARNING.get(lang, _REVIEW_WARNING["en"]))
    return "\n".join(parts)


async def process_message(
    text: str,
    user_id: str,
    settings: Settings,
    client: AgentClientProtocol,
) -> str:
    truncated = text[: settings.telegram_max_input_chars].strip()
    if not truncated:
        return "Please send a non-empty SQL question."
    lang = detect_lang(truncated)
    request = UserSqlRequest(
        request_id=uuid4(),
        source="telegram",
        user_id=user_id,
        text=truncated,
    )
    try:
        resp = await client.generate(request)
    except httpx.HTTPStatusError as e:
        return f"⚠️ Upstream rejected the request ({e.response.status_code})."
    except httpx.HTTPError as e:
        return f"⚠️ Upstream error: {e.__class__.__name__}"
    return format_reply(resp, lang)
