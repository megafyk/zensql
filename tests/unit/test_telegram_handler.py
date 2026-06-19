from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from pydantic import SecretStr

import zen.telegram_bot.bot as bot_mod
from zen.config.settings import Settings
from zen.models.requests import UserSqlRequest
from zen.models.responses import GeneratedSqlResponse
from zen.telegram_bot.handlers import (
    detect_lang,
    format_ack,
    format_reply,
    process_message,
)


class FakeAgentClient:
    def __init__(
        self,
        response: GeneratedSqlResponse | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._response = response
        self._raises = raises
        self.calls: list[UserSqlRequest] = []

    async def generate(self, request: UserSqlRequest) -> GeneratedSqlResponse:
        self.calls.append(request)
        if self._raises:
            raise self._raises
        assert self._response is not None
        return self._response


def _settings(max_chars: int = 50) -> Settings:
    return Settings(
        _env_file=None,
        agent_api_token=SecretStr("test-token"),
        telegram_max_input_chars=max_chars,
    )


def _stub_response(req_id: str | None = None, sql: str = "SELECT 1;") -> GeneratedSqlResponse:
    return GeneratedSqlResponse(
        request_id=req_id or str(uuid4()),  # type: ignore[arg-type]
        job_id=uuid4(),
        sql="-- AI-GENERATED SQL — REVIEW BEFORE EXECUTING\n" + sql,
        explanation="stub",
        warnings=["AI-generated SQL. Review before executing."],
    )


def test_format_reply_contains_code_block_and_warning() -> None:
    resp = _stub_response()
    out = format_reply(resp)
    assert '<pre><code class="language-sql">' in out
    assert "AI-GENERATED SQL" in out
    assert "review before running" in out


def test_format_reply_adds_metabase_guide() -> None:
    resp = GeneratedSqlResponse(
        request_id=uuid4(),
        job_id=uuid4(),
        sql="SELECT 1;",
        metabase_databases=["prod_vm_link_recharge_view"],
    )
    out = format_reply(resp)
    assert "open Metabase" in out
    assert "<b>prod_vm_link_recharge_view</b>" in out
    assert "select database" in out  # singular


def test_format_reply_metabase_guide_plural_databases() -> None:
    resp = GeneratedSqlResponse(
        request_id=uuid4(),
        job_id=uuid4(),
        sql="SELECT 1;",
        metabase_databases=["db_a", "db_b"],
    )
    out = format_reply(resp)
    assert "select databases" in out  # plural


def test_format_reply_no_metabase_guide_when_empty() -> None:
    resp = _stub_response()  # no metabase_databases
    out = format_reply(resp)
    assert "open Metabase" not in out


def test_format_reply_chat_reply_is_plain_and_escaped() -> None:
    resp = GeneratedSqlResponse(
        request_id=uuid4(),
        job_id=uuid4(),
        chat_reply="Beep boop 🤖 I only speak <SELECT>!",
    )
    out = format_reply(resp)
    assert "Beep boop" in out
    assert "&lt;SELECT&gt;" in out  # HTML-escaped
    assert "<pre>" not in out  # no SQL block
    assert "review before running" not in out  # no warning
    assert "open Metabase" not in out  # no guide


def test_detect_lang() -> None:
    assert detect_lang("how many bank connections are there?") == "en"
    assert detect_lang("đếm số kết nối ngân hàng") == "vi"
    assert detect_lang("danh sách tài khoản mới nhất") == "vi"
    # ASCII-only Vietnamese (no diacritics) falls back to English.
    assert detect_lang("dem so ket noi") == "en"


def test_format_ack_localized() -> None:
    assert "Request received" in format_ack("en")
    assert "Đã nhận yêu cầu" in format_ack("vi")
    assert format_ack("xx") == format_ack("en")  # unknown -> English fallback


def test_format_reply_localizes_guide_and_warning_vietnamese() -> None:
    resp = GeneratedSqlResponse(
        request_id=uuid4(),
        job_id=uuid4(),
        sql="SELECT 1;",
        metabase_databases=["prod_vm_link_recharge_view"],
    )
    out = format_reply(resp, "vi")
    assert "Để chạy" in out
    assert "chọn cơ sở dữ liệu" in out
    assert "kiểm tra trước khi chạy" in out
    assert "open Metabase" not in out
    assert "review before running" not in out


async def test_process_message_matches_request_language() -> None:
    fake = FakeAgentClient(response=_stub_response())
    out = await process_message("đếm số kết nối ngân hàng", "tg:1", _settings(), fake)
    # Vietnamese request -> Vietnamese review warning.
    assert "kiểm tra trước khi chạy" in out


class _DummyTyping:
    """Stand-in for ChatActionSender.typing context manager."""

    async def __aenter__(self) -> _DummyTyping:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


async def test_on_text_acks_in_language_before_result() -> None:
    answers: list[str] = []

    async def _answer(text: str, **_: object) -> None:
        answers.append(text)

    message = SimpleNamespace(
        text="đếm số kết nối ngân hàng",  # Vietnamese
        from_user=SimpleNamespace(id=42),
        chat=SimpleNamespace(id=99),
        bot=object(),
        answer=_answer,
    )
    with (
        patch.object(bot_mod, "process_message", AsyncMock(return_value="RESULT")),
        patch.object(
            bot_mod, "ChatActionSender", SimpleNamespace(typing=lambda **_: _DummyTyping())
        ),
    ):
        await bot_mod.on_text(message)  # type: ignore[arg-type]

    assert len(answers) == 2
    # 1) Immediate localized acknowledgement, before the (mocked) work.
    assert "Đã nhận yêu cầu" in answers[0]
    # 2) Then the actual result.
    assert answers[1] == "RESULT"


def test_format_reply_escapes_html() -> None:
    resp = GeneratedSqlResponse(
        request_id=uuid4(),
        job_id=uuid4(),
        sql="SELECT '<script>' FROM t WHERE a < b & c > d;",
    )
    out = format_reply(resp)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&amp;" in out


def test_format_reply_surfaces_error_code() -> None:
    resp = GeneratedSqlResponse(
        request_id=uuid4(),
        job_id=uuid4(),
        sql=None,
        error_code="UNSAFE_INTENT",
        error_message="matched pattern for WRITE_INTENT",
    )
    out = format_reply(resp)
    assert "UNSAFE_INTENT" in out
    assert "matched pattern" in out
    # Don't pretend SQL was generated when it wasn't.
    assert "<pre>" not in out
    assert "AI-generated — review" not in out


async def test_process_message_happy_path() -> None:
    fake = FakeAgentClient(response=_stub_response())
    out = await process_message("orders this week", "tg:1", _settings(), fake)
    assert "AI-GENERATED SQL" in out
    assert len(fake.calls) == 1
    sent = fake.calls[0]
    assert sent.source == "telegram"
    assert sent.user_id == "tg:1"
    assert sent.text == "orders this week"


async def test_process_message_truncates_to_max_chars() -> None:
    fake = FakeAgentClient(response=_stub_response())
    await process_message("x" * 200, "tg:1", _settings(max_chars=50), fake)
    assert len(fake.calls[0].text) == 50


async def test_process_message_strips_whitespace() -> None:
    fake = FakeAgentClient(response=_stub_response())
    await process_message("  hello  ", "tg:1", _settings(), fake)
    assert fake.calls[0].text == "hello"


async def test_process_message_rejects_empty() -> None:
    fake = FakeAgentClient(response=_stub_response())
    out = await process_message("", "tg:1", _settings(), fake)
    assert "non-empty" in out
    assert fake.calls == []


async def test_process_message_rejects_whitespace_only() -> None:
    fake = FakeAgentClient(response=_stub_response())
    out = await process_message("   \n  ", "tg:1", _settings(), fake)
    assert "non-empty" in out
    assert fake.calls == []


async def test_process_message_surfaces_status_error() -> None:
    err = httpx.HTTPStatusError(
        "boom",
        request=httpx.Request("POST", "http://x"),
        response=httpx.Response(status_code=401),
    )
    fake = FakeAgentClient(raises=err)
    out = await process_message("anything", "tg:1", _settings(), fake)
    assert "401" in out


async def test_process_message_surfaces_transport_error() -> None:
    fake = FakeAgentClient(raises=httpx.ConnectError("conn refused"))
    out = await process_message("anything", "tg:1", _settings(), fake)
    assert "Upstream error" in out
    assert "ConnectError" in out


# ---------------------------------------------------------------------------
# split_reply
# ---------------------------------------------------------------------------


def test_split_reply_short_message_unchanged() -> None:
    from zen.telegram_bot.handlers import split_reply

    assert split_reply("hello") == ["hello"]


def test_split_reply_long_sql_chunks_stay_valid_html() -> None:
    from zen.telegram_bot.handlers import (
        TELEGRAM_MAX_MESSAGE_CHARS,
        format_reply,
        split_reply,
    )

    sql = "\n".join(f"SELECT col_{i} FROM big_table WHERE x = {i}" for i in range(300))
    resp = _stub_response(sql=sql)
    reply = format_reply(resp)
    chunks = split_reply(reply)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= TELEGRAM_MAX_MESSAGE_CHARS
        # every chunk balances its <pre><code> tags
        assert chunk.count('<pre><code class="language-sql">') == chunk.count(
            "</code></pre>"
        )
    # no SQL line is lost across the split
    joined = "".join(chunks)
    assert "col_0 " in joined and "col_299 " in joined


def test_split_reply_handles_monster_single_line() -> None:
    from zen.telegram_bot.handlers import TELEGRAM_MAX_MESSAGE_CHARS, split_reply

    chunks = split_reply("x" * 10000)
    assert all(len(c) <= TELEGRAM_MAX_MESSAGE_CHARS for c in chunks)
    assert sum(len(c) for c in chunks) == 10000


def test_split_reply_long_single_line_sql_stays_valid_html() -> None:
    """A single over-width SQL line (e.g. a giant IN-list) — escaped quotes
    become &#x27; entities and the line ends in </code></pre>. No chunk may
    bisect a tag or an entity, and tags must stay balanced."""
    from zen.telegram_bot.handlers import (
        TELEGRAM_MAX_MESSAGE_CHARS,
        format_reply,
        split_reply,
    )

    ids = ", ".join(f"'ORD-{i:05d}'" for i in range(1200))
    sql = f"SELECT * FROM orders WHERE code IN ({ids})"
    chunks = split_reply(format_reply(_stub_response(sql=sql)))

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= TELEGRAM_MAX_MESSAGE_CHARS
        assert chunk.count('<pre><code class="language-sql">') == chunk.count(
            "</code></pre>"
        )
        tail = chunk.rsplit("&", 1)
        if len(tail) == 2 and "<" not in tail[1]:
            assert ";" in tail[1], f"entity bisected at chunk tail: ...{tail[1]!r}"


def test_split_reply_line_ending_in_close_tag_inside_pre() -> None:
    from zen.telegram_bot.handlers import _PRE_CLOSE, _PRE_OPEN, split_reply

    width = 3900 - len(_PRE_OPEN) - len(_PRE_CLOSE) - 2
    reply = _PRE_OPEN + "SELECT 1\n" + "y" * (width - 4) + _PRE_CLOSE + "\nexplanation"
    chunks = split_reply(reply)
    for chunk in chunks:
        assert chunk.count(_PRE_OPEN) == chunk.count(_PRE_CLOSE)
        assert "</code<" not in chunk
