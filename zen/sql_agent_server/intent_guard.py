"""Pre-generation guardrails on raw user text.

Regex-based intent classifier. This is *defense in depth* — the post-generation
SQL validator is the authoritative gate. We reject the request here too so
the system never even spawns Claude Code on an obviously hostile prompt.

Rejection categories:
- execute-the-SQL intent
- mutation/DDL intent
- privilege escalation
- exfiltration of secrets / credentials
- prompt-injection / safety override attempts
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from zen.models.safety import SafetyViolation

_EXECUTE_INTENT = [
    re.compile(r"\b(run|execute|apply)\s+(this|the\s+sql|migration)\b", re.I),
]

_WRITE_INTENT = [
    re.compile(r"\b(delete\s+from|drop\s+(table|database|index|schema))\b", re.I),
    re.compile(r"\b(truncate|insert\s+into|update\s+\w+\s+set)\b", re.I),
    re.compile(r"\balter\s+(table|database|user)\b", re.I),
]

_PRIV_ESCALATION = [
    re.compile(r"\b(as|sudo)\s+(admin|root|superuser)\b", re.I),
    re.compile(
        r"\b(grant|revoke)\s+(all|privileges|select|insert|update)\b",
        re.I,
    ),
]

# Bounded `.{0,40}` filler accommodates phrases like "show me the .env"
_EXFILTRATION = [
    re.compile(
        r"\b(show|dump|read|cat|leak)\b.{0,40}"
        r"(\.env\b|\bcredentials?\b|\bsecrets?\b|\bapi[\s_-]?keys?\b)",
        re.I,
    ),
    re.compile(
        r"\b(password|token|secret)\s*(=|:)\s*[\"']?\w+",
        re.I,
    ),
]

_PROMPT_INJECTION = [
    re.compile(r"\bignore\b.{0,40}\binstructions?\b", re.I),
    re.compile(
        r"\b(disable|bypass|disregard)\s+(safety|guard|policy|rules?)\b",
        re.I,
    ),
    re.compile(r"\bjailbreak\b", re.I),
    re.compile(
        r"act\s+as\s+(a\s+)?(developer|admin|hacker|nothing\s+is\s+forbidden)",
        re.I,
    ),
]

_PATTERNS: list[tuple[str, re.Pattern[str]]] = (
    [("EXECUTE_INTENT", p) for p in _EXECUTE_INTENT]
    + [("WRITE_INTENT", p) for p in _WRITE_INTENT]
    + [("PRIV_ESCALATION", p) for p in _PRIV_ESCALATION]
    + [("EXFILTRATION", p) for p in _EXFILTRATION]
    + [("PROMPT_INJECTION", p) for p in _PROMPT_INJECTION]
)


def reject_if_unsafe_intent(
    text: str,
    *,
    patterns: Iterable[tuple[str, re.Pattern[str]]] | None = None,
) -> SafetyViolation | None:
    """Return a SafetyViolation if `text` matches any unsafe pattern, else None."""
    for rule, pattern in patterns or _PATTERNS:
        m = pattern.search(text)
        if m:
            return SafetyViolation(
                rule=rule,
                detail=f"matched pattern for {rule}",
                evidence={"match": m.group(0)[:120]},
            )
    return None
