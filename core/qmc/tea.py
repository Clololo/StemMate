# -*- coding: utf-8 -*-
"""腾讯 tc_tea 的 TEA 与「魔改 CBC」实现。

QMC v2 的 EKey 就是由这层算法封装的，密钥与填充方式都与标准 TEA 不同：
- 16 轮、大端序的 64 位分组；
- CBC 的链值同时依赖上一块密文与「上一块的解密中间值」，即 tc_tea 的写法；
- 明文头部包含 1 字节填充长度、2 字节 salt，尾部有 7 字节零校验。
"""
from __future__ import annotations

import struct

DELTA = 0x9E3779B9
ROUNDS = 16
SALT_LEN = 2
ZERO_LEN = 7
MASK32 = 0xFFFFFFFF
MASK64 = 0xFFFFFFFFFFFFFFFF


def _round_mix(value: int, sum_value: int, key_a: int, key_b: int) -> int:
    """TEA 的一轮混合函数。"""
    left = ((value << 4) & MASK32) + key_a
    right = (value >> 5) + key_b
    middle = (sum_value + value) & MASK32
    return (left ^ middle ^ right) & MASK32


def _ecb_encrypt_block(block: int, key: tuple[int, int, int, int]) -> int:
    y = (block >> 32) & MASK32
    z = block & MASK32
    total = 0
    for _ in range(ROUNDS):
        total = (total + DELTA) & MASK32
        y = (y + _round_mix(z, total, key[0], key[1])) & MASK32
        z = (z + _round_mix(y, total, key[2], key[3])) & MASK32
    return (y << 32) | z


def _ecb_decrypt_block(block: int, key: tuple[int, int, int, int]) -> int:
    y = (block >> 32) & MASK32
    z = block & MASK32
    total = (DELTA * ROUNDS) & MASK32
    for _ in range(ROUNDS):
        z = (z - _round_mix(y, total, key[2], key[3])) & MASK32
        y = (y - _round_mix(z, total, key[0], key[1])) & MASK32
        total = (total - DELTA) & MASK32
    return (y << 32) | z


def _unpack_key(key: bytes) -> tuple[int, int, int, int]:
    if len(key) != 16:
        raise ValueError(f"TEA 密钥长度必须为 16 字节，当前为 {len(key)}")
    return struct.unpack(">IIII", key)


def decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """tc_tea 解密，返回去掉填充后的明文，并校验尾部零填充。"""
    if len(ciphertext) < 16 or len(ciphertext) % 8 != 0:
        raise ValueError(f"TEA 密文长度非法：{len(ciphertext)}")

    keys = _unpack_key(key)
    iv_plain = 0
    iv_mid = 0
    plain = bytearray()

    for offset in range(0, len(ciphertext), 8):
        block = int.from_bytes(ciphertext[offset:offset + 8], "big")
        mid = _ecb_decrypt_block((block ^ iv_mid) & MASK64, keys)
        plain += ((mid ^ iv_plain) & MASK64).to_bytes(8, "big")
        iv_plain = block
        iv_mid = mid

    pad_size = plain[0] & 0b111
    start = 1 + pad_size + SALT_LEN
    end = len(ciphertext) - ZERO_LEN
    if start > end:
        raise ValueError("TEA 明文长度非法")
    if any(plain[end:]):
        raise ValueError("TEA 零填充校验失败（密钥可能不正确）")
    return bytes(plain[start:end])


def encrypt(plaintext: bytes, key: bytes, salt: bytes) -> bytes:
    """tc_tea 加密（用于构造测试样本与自检）。``salt`` 至少 10 字节。"""
    keys = _unpack_key(key)

    total_len = 10 + len(plaintext)
    pad_len = (8 - (total_len & 7)) & 7
    header_len = 1 + pad_len + SALT_LEN
    total_len += pad_len

    header = bytearray(max(16, header_len))
    header[:header_len] = salt[:header_len]
    header[0] = (header[0] & 0xF8) | pad_len
    take = min(16 - header_len, len(plaintext))
    header[header_len:header_len + take] = plaintext[:take]
    rest = plaintext[take:]

    iv_cipher = 0   # 上一块的密文
    iv_mid = 0      # 上一块的「明文异或上一块密文」中间值
    out = bytearray()

    def crypt(block: bytes) -> bytes:
        nonlocal iv_cipher, iv_mid
        value = int.from_bytes(block, "big")
        mid = (value ^ iv_cipher) & MASK64
        cipher = (_ecb_encrypt_block(mid, keys) ^ iv_mid) & MASK64
        iv_cipher, iv_mid = cipher, mid
        return cipher.to_bytes(8, "big")

    out += crypt(bytes(header[:8]))
    out += crypt(bytes(header[8:16]))

    pos = 0
    while pos + 8 <= len(rest):
        out += crypt(rest[pos:pos + 8])
        pos += 8
    if pos < len(rest):
        out += crypt(rest[pos:] + b"\x00" * (8 - len(rest[pos:])))

    return bytes(out[:total_len])
