# -*- coding: utf-8 -*-
"""QMC 解密所需的固定参数。

本文件只放「算法常量」与「扩展名映射」，不含任何文件读写逻辑。
常量来源于公开的 QMC 格式逆向资料（unlock-music / qmc-decoder 系列项目），
本项目按算法语义重新实现，未复制其源码。
"""
from __future__ import annotations

import math
import struct

# ---------------------------------------------------------------------------
# QMC v1（旧式 .qmc0 / .qmc3 / .qmcflac / .qmcogg）
# ---------------------------------------------------------------------------

QMC1_KEY_SIZE = 128
# 偏移量超过 0x7FFF 后按 0x7FFF 取模，而不是按 0x8000，这是 QMC v1 的历史遗留行为。
QMC1_OFFSET_WRAP = 0x7FFF
QMC1_STATIC_KEY = bytes([
    0xC3, 0x4A, 0xD6, 0xCA, 0x90, 0x67, 0xF7, 0x52,
    0xD8, 0xA1, 0x66, 0x62, 0x9F, 0x5B, 0x09, 0x00,
    0xC3, 0x5E, 0x95, 0x23, 0x9F, 0x13, 0x11, 0x7E,
    0xD8, 0x92, 0x3F, 0xBC, 0x90, 0xBB, 0x74, 0x0E,
    0xC3, 0x47, 0x74, 0x3D, 0x90, 0xAA, 0x3F, 0x51,
    0xD8, 0xF4, 0x11, 0x84, 0x9F, 0xDE, 0x95, 0x1D,
    0xC3, 0xC6, 0x09, 0xD5, 0x9F, 0xFA, 0x66, 0xF9,
    0xD8, 0xF0, 0xF7, 0xA0, 0x90, 0xA1, 0xD6, 0xF3,
    0xC3, 0xF3, 0xD6, 0xA1, 0x90, 0xA0, 0xF7, 0xF0,
    0xD8, 0xF9, 0x66, 0xFA, 0x9F, 0xD5, 0x09, 0xC6,
    0xC3, 0x1D, 0x95, 0xDE, 0x9F, 0x84, 0x11, 0xF4,
    0xD8, 0x51, 0x3F, 0xAA, 0x90, 0x3D, 0x74, 0x47,
    0xC3, 0x0E, 0x74, 0xBB, 0x90, 0xBC, 0x3F, 0x92,
    0xD8, 0x7E, 0x11, 0x13, 0x9F, 0x23, 0x95, 0x5E,
    0xC3, 0x00, 0x09, 0x5B, 0x9F, 0x62, 0x66, 0xA1,
    0xD8, 0x52, 0xF7, 0x67, 0x90, 0xCA, 0xD6, 0x4A,
])

# ---------------------------------------------------------------------------
# QMC v2 EKey（内嵌密钥）解密
# ---------------------------------------------------------------------------

V2_EKEY_PREFIX = b"QQMusic EncV2,Key:"
# 整个 EKey 字符串以「前缀的 base64」开头时，说明密钥经过了双层封装。
V2_EKEY_PREFIX_B64 = b"UVFNdXNpYyBFbmNWMixLZXk6"
# 双层封装使用的两个 TEA 密钥（16 字节，两轮）。
V2_LAYER_KEY_1 = bytes([
    0x33, 0x38, 0x36, 0x5A, 0x4A, 0x59, 0x21, 0x40,
    0x23, 0x2A, 0x24, 0x25, 0x5E, 0x26, 0x29, 0x28,
])
V2_LAYER_KEY_2 = bytes([
    0x2A, 0x2A, 0x23, 0x21, 0x28, 0x23, 0x24, 0x25,
    0x26, 0x5E, 0x61, 0x31, 0x63, 0x5A, 0x2C, 0x54,
])

# 短密钥（Map 模式）与长密钥（RC4 模式）的分界：主密钥长度 > 300 走 RC4。
V2_MAP_MAX_KEY_LEN = 300

# RC4 模式的分段参数。
RC4_FIRST_SEGMENT = 0x0080
RC4_SEGMENT = 0x1400
RC4_KEYSTREAM_CACHE = RC4_SEGMENT + 512


def _f32(value: float) -> float:
    """把 Python float 截断成 IEEE-754 单精度，复刻客户端的 f32 运算。"""
    return struct.unpack("f", struct.pack("f", value))[0]


def make_ekey_seed() -> bytes:
    """生成 V1 EKey 的 8 字节固定种子。

    客户端用 ``tan(106 + i * 0.1) * 100`` 的单精度结果取整得到，
    这里严格按 f32 逐步舍入，保证与客户端逐字节一致。
    """
    step = _f32(0.1)
    out = bytearray()
    for i in range(8):
        angle = _f32(106.0 + _f32(i * step))
        scaled = _f32(abs(math.tan(angle)))
        value = _f32(scaled * 100.0)
        out.append(max(0, min(int(value), 255)))
    return bytes(out)


EKEY_SEED_KEY = make_ekey_seed()

# ---------------------------------------------------------------------------
# 扩展名映射
# ---------------------------------------------------------------------------

# QMC v2 常见扩展名。
V2_EXTENSIONS = {
    "mgg", "mgg0", "mgg1", "mggl", "mflac", "mflac0", "mmp4",
    "qmcflac", "qmcogg", "qmc0", "qmc2", "qmc3", "qmc4", "qmc6", "qmc8",
}
# QMC v1 静态密钥格式。
V1_EXTENSIONS = {
    "bkcmp3", "bkcm4a", "bkcflac", "bkcwav", "bkcape", "bkcogg", "bkcwma",
    "tkm", "666c6163", "6d7033", "6f6767", "6d3461", "776176",
}
# 扩展名 -> 解密后的容器格式。
OUTPUT_EXTENSION = {
    "mgg": "ogg", "mgg0": "ogg", "mgg1": "ogg", "mggl": "ogg",
    "mflac": "flac", "mflac0": "flac", "mmp4": "mp4",
    "qmcflac": "flac", "qmcogg": "ogg", "qmc0": "mp3",
    "qmc2": "ogg", "qmc3": "mp3", "qmc4": "ogg", "qmc6": "ogg", "qmc8": "ogg",
    "bkcmp3": "mp3", "bkcm4a": "m4a", "bkcflac": "flac", "bkcwav": "wav",
    "bkcape": "ape", "bkcogg": "ogg", "bkcwma": "wma", "tkm": "m4a",
    "666c6163": "flac", "6d7033": "mp3", "6f6767": "ogg",
    "6d3461": "m4a", "776176": "wav",
}
