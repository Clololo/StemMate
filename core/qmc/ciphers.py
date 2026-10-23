# -*- coding: utf-8 -*-
"""QMC 数据流密码。

- QMC v1：整文件与 128 字节静态表异或，偏移超过 0x7FFF 后按 0x7FFF 回绕；
- QMC v2 Map：主密钥压缩成 128 字节表后，用同一套 v1 变换；
- QMC v2 RC4：主密钥直接作为魔改 RC4 的状态密钥，按 128 / 5120 字节分段。

三种密码都支持带偏移的分块解密，便于流式处理大文件。
"""
from __future__ import annotations

from .constants import (
    QMC1_KEY_SIZE,
    QMC1_OFFSET_WRAP,
    QMC1_STATIC_KEY,
    RC4_FIRST_SEGMENT,
    RC4_KEYSTREAM_CACHE,
    RC4_SEGMENT,
    V2_MAP_MAX_KEY_LEN,
)


def _xor_bytes(left: bytes, right: bytes) -> bytes:
    """等长字节串异或（用大整数实现，快于逐字节循环）。"""
    a = int.from_bytes(left, "little")
    b = int.from_bytes(right, "little")
    return (a ^ b).to_bytes(len(left), "little")


def _apply_key_table(data: bytes, key: bytes, offset: int) -> bytes:
    """按 QMC 的偏移语义把 ``data`` 与 ``key`` 循环异或。

    起点偏移为 0 至 0x7FFF 时，密钥索引直接取 ``offset % 128``；
    超过 0x7FFF 后，索引按 ``(offset % 0x7FFF) % 128`` 走，因此
    0x7FFF 与 0x8000 处都会出现一次「相位回绕」。
    """
    total = len(data)
    if total == 0:
        return b""
    if len(key) != QMC1_KEY_SIZE:
        raise ValueError(f"密钥表长度必须为 {QMC1_KEY_SIZE}，当前为 {len(key)}")

    out = bytearray(data)
    index = 0
    pos = offset
    while index < total:
        if pos <= QMC1_OFFSET_WRAP:
            take = min(QMC1_OFFSET_WRAP + 1 - pos, total - index)
            phase = pos % QMC1_KEY_SIZE
        else:
            remainder = pos % QMC1_OFFSET_WRAP
            take = min(QMC1_OFFSET_WRAP - remainder, total - index)
            phase = remainder % QMC1_KEY_SIZE
        rotated = key[phase:] + key[:phase]
        stream = (rotated * (take // QMC1_KEY_SIZE + 1))[:take]
        out[index:index + take] = _xor_bytes(out[index:index + take], stream)
        index += take
        pos += take
    return bytes(out)


class Qmc1Cipher:
    """QMC v1：静态表密码。"""

    def decrypt(self, data: bytes, offset: int = 0) -> bytes:
        return _apply_key_table(data, QMC1_STATIC_KEY, offset)


def compress_master_key(master_key: bytes) -> bytes:
    """把任意长度的主密钥压缩（拉伸）成 128 字节的 Map 密钥表。"""
    length = len(master_key)
    if length == 0:
        raise ValueError("主密钥为空")
    table = bytearray(QMC1_KEY_SIZE)
    for i in range(QMC1_KEY_SIZE):
        idx = (i * i + 71214) % length
        value = master_key[idx]
        shift = (idx + 4) % 8
        table[i] = ((value << shift) | (value >> shift)) & 0xFF
    return bytes(table)


class Qmc2MapCipher:
    """QMC v2 短密钥（Map）密码。"""

    def __init__(self, master_key: bytes):
        self.key = compress_master_key(master_key)

    def decrypt(self, data: bytes, offset: int = 0) -> bytes:
        return _apply_key_table(data, self.key, offset)


def _key_hash(key: bytes) -> float:
    """RC4 模式用于计算分段跳过的哈希值。"""
    value = 1
    for byte in key:
        if byte == 0:
            continue
        nxt = (value * byte) & 0xFFFFFFFF
        if nxt == 0 or nxt <= value:
            break
        value = nxt
    return float(value)


def _segment_skip(segment_id: int, seed: int, hash_value: float) -> int:
    if seed == 0:
        return 0
    divisor = float((segment_id + 1) * seed)
    return int(hash_value / divisor * 100.0)


class _StateRc4:
    """魔改 RC4：状态长度等于密钥长度（而非 256），字节值按 256 取模。"""

    def __init__(self, key: bytes):
        size = len(key)
        self.size = size
        state = [i & 0xFF for i in range(size)]
        j = 0
        for i in range(size):
            j = (j + state[i] + key[i % size]) % size
            state[i], state[j] = state[j], state[i]
        self._state = state
        self._i = 0
        self._j = 0

    def next_byte(self) -> int:
        size = self.size
        self._i = (self._i + 1) % size
        self._j = (self._j + self._state[self._i]) % size
        self._state[self._i], self._state[self._j] = (
            self._state[self._j],
            self._state[self._i],
        )
        idx = (self._state[self._i] + self._state[self._j]) % size
        return self._state[idx]


class Qmc2Rc4Cipher:
    """QMC v2 长密钥（RC4）密码。"""

    def __init__(self, master_key: bytes):
        key = bytes(master_key)
        self.key = key
        self.size = len(key)
        self.hash = _key_hash(key)
        rc4 = _StateRc4(key)
        self._keystream = bytes(rc4.next_byte() for _ in range(RC4_KEYSTREAM_CACHE))

    def _first_segment(self, buf: bytearray, start: int, length: int, offset: int) -> None:
        size = self.size
        for step in range(length):
            pos = offset + step
            seed = self.key[pos % size]
            skip = _segment_skip(pos, seed, self.hash) % size
            buf[start + step] ^= self.key[skip]

    def _other_segment(self, buf: bytearray, start: int, length: int, offset: int) -> None:
        segment_id = offset // RC4_SEGMENT
        within = offset % RC4_SEGMENT
        seed = self.key[segment_id % self.size]
        skip = _segment_skip(segment_id, seed, self.hash) & 0x1FF
        chunk = bytes(buf[start:start + length])
        stream = self._keystream[skip + within:skip + within + length]
        buf[start:start + length] = _xor_bytes(chunk, stream)

    def decrypt(self, data: bytes, offset: int = 0) -> bytes:
        out = bytearray(data)
        total = len(out)
        index = 0
        pos = offset

        if pos < RC4_FIRST_SEGMENT:
            take = min(RC4_FIRST_SEGMENT - pos, total)
            self._first_segment(out, 0, take, pos)
            index += take
            pos += take

        if index < total and pos % RC4_SEGMENT != 0:
            take = min(RC4_SEGMENT - pos % RC4_SEGMENT, total - index)
            self._other_segment(out, index, take, pos)
            index += take
            pos += take

        while index < total:
            take = min(RC4_SEGMENT, total - index)
            self._other_segment(out, index, take, pos)
            index += take
            pos += take

        return bytes(out)


def make_cipher(master_key: bytes):
    """按主密钥长度选择 QMC v2 密码。"""
    master_key = bytes(master_key)
    if not master_key:
        raise ValueError("主密钥为空")
    if len(master_key) <= V2_MAP_MAX_KEY_LEN:
        return Qmc2MapCipher(master_key)
    return Qmc2Rc4Cipher(master_key)


def container_from_magic(head: bytes) -> str | None:
    """根据文件头识别明文容器格式，用于校验解密结果。"""
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"ID3"):
        return "mp3"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "mp3"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "mka"
    if head.startswith(b"MAC "):
        return "ape"
    if head.startswith(b"FRM8"):
        return "dff"
    if head.startswith(b"\x30\x26\xb2\x75"):
        return "wma"
    return None


__all__ = [
    "Qmc1Cipher",
    "Qmc2MapCipher",
    "Qmc2Rc4Cipher",
    "compress_master_key",
    "container_from_magic",
    "make_cipher",
]
