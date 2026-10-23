# -*- coding: utf-8 -*-
"""EKey 解码：把尾部 / 密钥库 / API 得到的 EKey 字符串还原成主密钥。

EKey 有两种封装：
- V1：``base64(8 字节头部 + TEA 密文)``，TEA 密钥由 8 字节固定种子与头部
  交替拼接而成；
- V2：``base64("QQMusic EncV2,Key:" + 两层 TEA 密文)``，先依次用两个固定
  密钥解两层 TEA，得到 V1 EKey 的 base64 文本，再按 V1 解一次。

解出的主密钥长度决定流密码：≤300 字节走 Map，>300 字节走 RC4。
"""
from __future__ import annotations

import base64
import binascii
from typing import Final

from . import tea
from .constants import (
    EKEY_SEED_KEY,
    V2_EKEY_PREFIX,
    V2_EKEY_PREFIX_B64,
    V2_LAYER_KEY_1,
    V2_LAYER_KEY_2,
)


class EKeyError(ValueError):
    """EKey 解析或解密失败。"""


def _b64decode(text: bytes) -> bytes:
    """宽容的 base64 解码：兼容标准/URL-safe 与缺失填充。"""
    cleaned = bytes(ch for ch in text if not chr(ch).isspace())
    if not cleaned:
        raise EKeyError("EKey 为空")
    padded = cleaned + b"=" * (-len(cleaned) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return decoder(padded)
        except (binascii.Error, ValueError):
            continue
    raise EKeyError("EKey 不是合法的 base64 文本")


def _decrypt_v1(ekey_text: bytes) -> bytes:
    blob = _b64decode(ekey_text)
    if len(blob) < 16:
        raise EKeyError(f"EKey 解码后过短：{len(blob)} 字节")

    header, cipher = blob[:8], blob[8:]

    tea_key = bytearray()
    for seed_byte, header_byte in zip(EKEY_SEED_KEY, header):
        tea_key.append(seed_byte)
        tea_key.append(header_byte)

    try:
        plain = tea.decrypt(cipher, bytes(tea_key))
    except ValueError as exc:
        raise EKeyError(f"EKey 解密失败：{exc}") from exc
    return header + plain


def _decrypt_v2(ekey_text: bytes) -> bytes:
    payload = _b64decode(ekey_text)
    try:
        payload = tea.decrypt(payload, V2_LAYER_KEY_1)
        payload = tea.decrypt(payload, V2_LAYER_KEY_2)
    except ValueError as exc:
        raise EKeyError(f"EKey 外层解密失败：{exc}") from exc

    # 两层解开后是 V1 EKey 的文本，末尾以 0 截断。
    end = payload.find(b"\x00")
    if end != -1:
        payload = payload[:end]
    return _decrypt_v1(payload)


def decrypt_ekey(ekey: str | bytes) -> bytes:
    """把 EKey 字符串解码为主密钥。"""
    if isinstance(ekey, str):
        try:
            raw = ekey.strip().encode("latin-1")
        except UnicodeEncodeError as exc:
            raise EKeyError("EKey 含有非 ASCII 字符") from exc
    else:
        raw = bytes(ekey).strip()

    if not raw:
        raise EKeyError("EKey 为空")

    if raw.startswith(V2_EKEY_PREFIX_B64):
        return _decrypt_v2(raw[len(V2_EKEY_PREFIX_B64):])
    if raw.startswith(V2_EKEY_PREFIX):
        return _decrypt_v2(raw[len(V2_EKEY_PREFIX):])
    return _decrypt_v1(raw)
