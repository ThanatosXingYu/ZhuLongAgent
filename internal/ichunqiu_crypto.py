"""Pure helpers for the public iChunQiu match protocol."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import struct
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias
from urllib.parse import parse_qs, quote, unquote, urlsplit

MATCH_ORIGIN = "https://match.ichunqiu.com"
MATCH_HOST = "match.ichunqiu.com"
API_ORIGIN = "https://apiterminator.ichunqiu.com"

# These values are public constants embedded in the iChunQiu browser client.
# They identify the wire protocol; neither value grants access to an account.
PROTOCOL_SIGNING_SALT = "7637b08bdb0b29e08300a976b24ca672"
LOGIN_CIPHER_KEY = "9633369c7144a6b2290016fc494207ff"

_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
_ENTRY_KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,512}")
_UINT32_MASK = 0xFFFFFFFF
_XXTEA_DELTA = 0x9E3779B9

JsonPrimitive: TypeAlias = str | int | float | bool | None


class MatchURLValidationError(ValueError):
    """Raised when a user-supplied match URL is not an iChunQiu URL."""


@dataclass(frozen=True)
class MatchAddress:
    """Validated match address and the values used to bind it."""

    normalized_url: str
    event_slug: str = ""
    entry_key: str = ""

    @property
    def encrypted_binding(self) -> bool:
        return bool(self.entry_key)

    @property
    def binding_value(self) -> str:
        return self.entry_key or self.event_slug


def parse_match_url(value: str) -> MatchAddress:
    """Validate and normalize one of the two supported match URL forms."""

    raw = value.strip()
    if not raw:
        raise MatchURLValidationError("请输入比赛地址")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise MatchURLValidationError("比赛地址格式无效") from exc
    if parsed.scheme.lower() != "https" or parsed.hostname != MATCH_HOST:
        raise MatchURLValidationError("比赛地址必须使用 https://match.ichunqiu.com")
    if parsed.username is not None or parsed.password is not None or port not in (None, 443):
        raise MatchURLValidationError("比赛地址不能包含用户信息或非标准端口")

    query = parse_qs(parsed.query, keep_blank_values=True)
    access_values = query.get("k", [])
    if len(access_values) > 1:
        raise MatchURLValidationError("比赛地址只能包含一个 k 参数")
    entry_key = access_values[0].strip() if access_values else ""
    if access_values:
        if not _ENTRY_KEY_PATTERN.fullmatch(entry_key):
            raise MatchURLValidationError("比赛地址中的 k 参数无效")
        normalized = f"{MATCH_ORIGIN}/index?k={quote(entry_key, safe='-_')}"
        return MatchAddress(normalized, entry_key=entry_key)

    path = unquote(parsed.path).strip("/")
    if "/" in path or not _SLUG_PATTERN.fullmatch(path):
        raise MatchURLValidationError("比赛地址中缺少有效的比赛标识")
    return MatchAddress(f"{MATCH_ORIGIN}/{path}", event_slug=path)


def request_signature(payload: Mapping[str, JsonPrimitive]) -> str:
    """Return the SHA-1 request signature expected by the public frontend."""

    parts = sorted(f"{key}={_javascript_string(value)}" for key, value in payload.items())
    wire_value = f"{'&'.join(parts)}&{PROTOCOL_SIGNING_SALT}"
    return hashlib.sha1(wire_value.encode("utf-8"), usedforsecurity=False).hexdigest()


def create_request_id() -> str:
    """Create the 32-character ``rs`` value used for request correlation."""

    seed = f"{time.time_ns()}:{secrets.token_hex(16)}:icq"
    return hashlib.md5(seed.encode("ascii"), usedforsecurity=False).hexdigest()


def build_request_payload(
    values: Mapping[str, JsonPrimitive],
    match_key: str,
    token: str,
    *,
    timestamp_ms: int | None = None,
    request_id: str | None = None,
) -> dict[str, JsonPrimitive]:
    """Add the common iChunQiu request fields without mutating the input."""

    payload = dict(values)
    payload["k"] = match_key
    payload["stamp"] = timestamp_ms if timestamp_ms is not None else time.time_ns() // 1_000_000
    payload["token"] = token
    payload["rs"] = request_id or create_request_id()
    return payload


def encrypt_login_field(value: str) -> str:
    """XXTEA-encrypt a login field using the public frontend wire format."""

    if not value:
        return ""
    encrypted = _xxtea_encrypt(value.encode("utf-8"), LOGIN_CIPHER_KEY.encode("ascii"))
    return base64.urlsafe_b64encode(encrypted).decode("ascii").rstrip("=")


def _javascript_string(value: JsonPrimitive) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _xxtea_encrypt(value: bytes, key: bytes) -> bytes:
    words = _bytes_to_words(value, include_length=True)
    key_words = _bytes_to_words(key, include_length=False)
    key_words.extend([0] * (4 - len(key_words)))
    key_words = key_words[:4]

    word_count = len(words)
    rounds = 6 + 52 // word_count
    total = 0
    last = words[-1]
    for _ in range(rounds):
        total = (total + _XXTEA_DELTA) & _UINT32_MASK
        selector = (total >> 2) & 3
        for position in range(word_count - 1):
            following = words[position + 1]
            words[position] = (
                words[position]
                + _xxtea_mix(total, following, last, position, selector, key_words)
            ) & _UINT32_MASK
            last = words[position]
        following = words[0]
        words[-1] = (
            words[-1]
            + _xxtea_mix(total, following, last, word_count - 1, selector, key_words)
        ) & _UINT32_MASK
        last = words[-1]
    return b"".join(struct.pack("<I", word) for word in words)


def _bytes_to_words(value: bytes, *, include_length: bool) -> list[int]:
    word_count = (len(value) + 3) // 4
    words = [0] * word_count
    for index, byte in enumerate(value):
        words[index >> 2] |= byte << ((index & 3) << 3)
    if include_length:
        words.append(len(value))
    return words


def _xxtea_mix(
    total: int,
    following: int,
    previous: int,
    position: int,
    selector: int,
    key: list[int],
) -> int:
    return (
        ((previous >> 5 ^ following << 2) + (following >> 3 ^ previous << 4))
        ^ ((total ^ following) + (key[position & 3 ^ selector] ^ previous))
    ) & _UINT32_MASK
