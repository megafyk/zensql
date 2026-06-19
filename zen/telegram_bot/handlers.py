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


# Telegram rejects messages longer than 4096 chars; keep headroom for the
# closing/reopening <pre> tags added at split boundaries.
TELEGRAM_MAX_MESSAGE_CHARS = 4096
_SPLIT_LIMIT = 3900
_PRE_OPEN = '<pre><code class="language-sql">'
_PRE_CLOSE = "</code></pre>"


def _pre_state(line: str, in_pre: bool) -> bool:
    if _PRE_OPEN in line and _PRE_CLOSE in line:
        return in_pre
    if _PRE_OPEN in line:
        return True
    if _PRE_CLOSE in line:
        return False
    return in_pre


def _safe_slices(text: str, width: int) -> list[str]:
    """Slice `text` into <=width pieces, backing each cut off so it never
    lands inside an HTML entity (`&...;` — html.escape emits up to 6 chars)."""
    out: list[str] = []
    i = 0
    while i < len(text):
        j = min(i + width, len(text))
        if j < len(text):
            amp = text.rfind("&", max(i, j - 8), j)
            if amp > i and ";" not in text[amp:j]:
                j = amp
        out.append(text[i:j])
        i = j
    return out


def split_reply(reply: str, limit: int = _SPLIT_LIMIT) -> list[str]:
    """Split an HTML reply into Telegram-sized chunks. A split inside the SQL
    <pre><code> block closes the tags on the outgoing chunk and reopens them
    on the next so every chunk renders as valid HTML."""
    if len(reply) <= limit:
        return [reply]

    width = limit - len(_PRE_OPEN) - len(_PRE_CLOSE) - 2
    lines: list[str] = []
    for raw in reply.split("\n"):
        if len(raw) <= width:
            lines.append(raw)
            continue
        # Peel the pre tags off an over-width line before slicing so a cut can
        # never land inside a tag and desync the in_pre tracking below.
        prefix = suffix = ""
        body = raw
        if body.startswith(_PRE_OPEN):
            prefix, body = _PRE_OPEN, body[len(_PRE_OPEN) :]
        if body.endswith(_PRE_CLOSE):
            body, suffix = body[: -len(_PRE_CLOSE)], _PRE_CLOSE
        pieces = _safe_slices(body, width) or [""]
        pieces[0] = prefix + pieces[0]
        pieces[-1] = pieces[-1] + suffix
        lines.extend(pieces)

    chunks: list[str] = []
    buf = ""
    in_pre = False
    for line in lines:
        sep = "" if not buf or buf == _PRE_OPEN else "\n"
        if buf and len(buf) + len(sep) + len(line) > limit - len(_PRE_CLOSE):
            chunks.append(buf + (_PRE_CLOSE if in_pre else ""))
            buf = _PRE_OPEN if in_pre else ""
            sep = "" if not buf or buf == _PRE_OPEN else "\n"
        buf = f"{buf}{sep}{line}"
        in_pre = _pre_state(line, in_pre)
    if buf and buf != _PRE_OPEN:
        chunks.append(buf)
    return chunks


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
    parts: list[str] = []
    if resp.error_code:
        parts = [f"⚠️ Request could not be completed: {escape(resp.error_code)}"]
        if resp.error_message:
            parts.append(escape(resp.error_message))
        return "\n".join(parts)
    if resp.chat_reply:
        # Conversational (non-SQL) reply — just the message, no SQL chrome.
        return escape(resp.chat_reply)
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
