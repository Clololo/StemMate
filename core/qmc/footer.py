# -*- coding: utf-8 -*-
"""解析 QMC 文件尾部，识别封装类型并取出密钥/元数据。

目前遇到四种尾部：
- ``QTag``：安卓端封装，密钥内嵌；
- ``STag``：安卓端封装，只有元数据，密钥不内嵌；
- ``musicex``：PC 新版封装，只有元数据，密钥需另外提供；
- 无标记的「长度 + base64」尾部：PC 经典 PcV1Legacy，密钥内嵌。
"""
from __future__ import annotations

from dataclasses import dataclass, field

BASE64_CHARS = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
)
MUSICEX_MAGIC = b"musicex\x00"
MUSICEX_FOOTER_SIZE = 0xC0
MAX_EKEY_FIELD = 0x500


class FooterParseError(ValueError):
    """尾部结构损坏或不符合已知格式。"""


@dataclass(frozen=True)
class Footer:
    kind: str
    """``QTag`` / ``STag`` / ``MusicEx`` / ``PcV1Legacy``。"""
    total_size: int
    """应从文件末尾裁掉的字节数（尾部元数据本身）。"""
    ekey: str | None = None
    resource_id: int | None = None
    media_mid: str | None = None
    media_filename: str | None = None
    song_id: int | None = None
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def has_embedded_key(self) -> bool:
        return self.ekey is not None


def _decode_utf16le(raw: bytes) -> str:
    """读取截断到 NUL 的 UTF-16LE ASCII 字符串。"""
    chars = []
    for i in range(0, len(raw) - 1, 2):
        low, high = raw[i], raw[i + 1]
        if low == 0 and high == 0:
            break
        if high == 0 and 0 < low < 128:
            chars.append(chr(low))
        else:
            break
    return "".join(chars)


def _parse_csv_footer(payload: bytes, kind: str) -> list[str]:
    text = payload.decode("utf-8", "replace")
    parts = text.split(",")
    if len(parts) != 3:
        raise FooterParseError(f"{kind} 字段数量异常：{len(parts)}")
    return parts


def parse_footer(tail: bytes) -> Footer | None:
    """解析文件尾部。``tail`` 至少包含最后 1024 字节。

    返回 ``None`` 表示没有任何已知 QMC 尾部；结构匹配但损坏时抛
    :class:`FooterParseError`。
    """
    if len(tail) < 8:
        return None

    if tail.endswith(b"STag"):
        body = tail[:-4]
        if len(body) < 4:
            raise FooterParseError("STag 过短")
        payload_len = int.from_bytes(body[-4:], "big")
        if payload_len > len(body) - 4:
            raise FooterParseError("STag 长度越界")
        payload = body[len(body) - 4 - payload_len:len(body) - 4]
        song_id, version, media_mid = _parse_csv_footer(payload, "STag")
        if version != "2" or not song_id.isdigit():
            raise FooterParseError("STag 版本或 ID 非法")
        return Footer(
            kind="STag",
            total_size=payload_len + 8,
            resource_id=int(song_id),
            media_mid=media_mid,
        )

    if tail.endswith(b"QTag"):
        body = tail[:-4]
        if len(body) < 4:
            raise FooterParseError("QTag 过短")
        payload_len = int.from_bytes(body[-4:], "big")
        if payload_len > len(body) - 4:
            raise FooterParseError("QTag 长度越界")
        payload = body[len(body) - 4 - payload_len:len(body) - 4]
        ekey, song_id, version = _parse_csv_footer(payload, "QTag")
        if version != "2" or not song_id.isdigit():
            raise FooterParseError("QTag 版本或 ID 非法")
        if not ekey or any(ch not in BASE64_CHARS for ch in ekey.encode("latin-1")):
            raise FooterParseError("QTag EKey 非法")
        return Footer(
            kind="QTag",
            total_size=payload_len + 8,
            ekey=ekey,
            resource_id=int(song_id),
        )

    if tail.endswith(MUSICEX_MAGIC):
        body = tail[:-8]
        if len(body) < 8:
            raise FooterParseError("MusicEx 过短")
        version = int.from_bytes(body[-4:], "little")
        if version != 1:
            raise FooterParseError(f"MusicEx 版本不支持：{version}")
        body = body[:-4]
        footer_size = int.from_bytes(body[-4:], "little")
        if footer_size != MUSICEX_FOOTER_SIZE:
            raise FooterParseError(f"MusicEx 长度异常：{footer_size:#x}")
        body = body[:-4]
        if len(body) < footer_size - 16:
            raise FooterParseError("MusicEx 数据不完整")
        payload = body[len(body) - (footer_size - 16):]

        song_id = int.from_bytes(payload[0:4], "little")
        quality_a = int.from_bytes(payload[4:8], "little")
        quality_b = int.from_bytes(payload[8:12], "little")
        media_mid = _decode_utf16le(payload[12:72])
        media_filename = _decode_utf16le(payload[72:172])
        return Footer(
            kind="MusicEx",
            total_size=footer_size,
            media_mid=media_mid,
            media_filename=media_filename,
            song_id=song_id,
            extra={"quality_a": quality_a, "quality_b": quality_b},
        )

    # PC 经典尾部：base64 密钥 + 4 字节小端长度。
    if len(tail) < 8:
        return None
    payload_len = int.from_bytes(tail[-4:], "little")
    if not 0 < payload_len <= MAX_EKEY_FIELD:
        return None
    if payload_len > len(tail) - 4:
        raise FooterParseError("PcV1Legacy 长度越界")
    raw = tail[len(tail) - 4 - payload_len:len(tail) - 4]
    nul = raw.find(b"\x00")
    if nul != -1:
        raw = raw[:nul]
    if not raw or any(ch not in BASE64_CHARS for ch in raw):
        raise FooterParseError("PcV1Legacy EKey 非法")
    return Footer(
        kind="PcV1Legacy",
        total_size=payload_len + 4,
        ekey=raw.decode("latin-1"),
    )
