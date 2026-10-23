# -*- coding: utf-8 -*-
"""测试用样本构造函数（只在测试中使用，不参与打包）。"""
from __future__ import annotations

import base64
import struct

from core.qmc import tea
from core.qmc.ciphers import Qmc1Cipher, make_cipher
from core.qmc.constants import (
    EKEY_SEED_KEY,
    V2_EKEY_PREFIX_B64,
    V2_LAYER_KEY_1,
    V2_LAYER_KEY_2,
)

SALT = bytes([0xA5, 0x6E, 0x35, 0xBC, 0x7C, 0x31, 0x04, 0x55, 0xA0, 0xBF])


def craft_ekey_v1(master_key: bytes, salt: bytes = SALT) -> str:
    """把主密钥封装成单层 EKey。"""
    header, plain = master_key[:8], master_key[8:]
    tea_key = bytearray()
    for seed_byte, header_byte in zip(EKEY_SEED_KEY, header):
        tea_key.append(seed_byte)
        tea_key.append(header_byte)
    cipher = tea.encrypt(plain, bytes(tea_key), salt)
    return base64.b64encode(header + cipher).decode("ascii")


def craft_ekey_v2(v1_ekey: str, salt: bytes = SALT) -> str:
    """把 V1 EKey 再包两层，得到 EncV2 形式。"""
    inner = v1_ekey.encode("ascii") + b"\x00" * 8
    layer2 = tea.encrypt(inner, V2_LAYER_KEY_2, salt)
    layer1 = tea.encrypt(layer2, V2_LAYER_KEY_1, salt)
    return (V2_EKEY_PREFIX_B64 + base64.b64encode(layer1)).decode("ascii")


def encrypt_payload(master_key: bytes, plaintext: bytes) -> bytes:
    """Map / RC4 都是异或流密码，加密与解密同一操作。"""
    return make_cipher(master_key).decrypt(plaintext, 0)


def encrypt_v1_payload(plaintext: bytes) -> bytes:
    return Qmc1Cipher().decrypt(plaintext, 0)


def pcv1_footer(ekey: str) -> bytes:
    raw = ekey.encode("ascii")
    return raw + struct.pack("<I", len(raw))


def qtag_footer(ekey: str, song_id: int = 987654, version: str = "2") -> bytes:
    payload = f"{ekey},{song_id},{version}".encode("ascii")
    return payload + struct.pack(">I", len(payload)) + b"QTag"


def stag_footer(song_id: int = 987654, media_mid: str = "001abcDEFghiJK", version: str = "2") -> bytes:
    payload = f"{song_id},{version},{media_mid}".encode("ascii")
    return payload + struct.pack(">I", len(payload)) + b"STag"


def _utf16_fixed(text: str, size: int) -> bytes:
    raw = text.encode("utf-16le")[:size]
    return raw + b"\x00" * (size - len(raw))


def musicex_footer(
    song_id: int = 1251166,
    media_mid: str = "002ejEdb4KTwBw",
    media_filename: str = "O4M000015Tgb2RIq59.mgg",
    footer_size: int = 0xC0,
    version: int = 1,
) -> bytes:
    payload = bytearray(footer_size - 16)
    payload[0:4] = struct.pack("<I", song_id)
    payload[4:8] = struct.pack("<I", 2)
    payload[8:12] = struct.pack("<I", 2)
    payload[12:72] = _utf16_fixed(media_mid, 60)
    payload[72:172] = _utf16_fixed(media_filename, 100)
    return (
        bytes(payload)
        + struct.pack("<I", footer_size)
        + struct.pack("<I", version)
        + b"musicex\x00"
    )


def sample_plaintext(container: str = "flac", size: int = 4096, seed: int = 1) -> bytes:
    """构造一段带正确文件头、长度可观的伪音频数据。"""
    headers = {
        "flac": b"fLaC",
        "ogg": b"OggS",
        "mp3": b"ID3\x04\x00\x00\x00\x00\x00\x00",
        "wav": b"RIFF\x00\x00\x00\x00WAVE",
        "m4a": b"\x00\x00\x00\x20ftypM4A ",
    }
    head = headers[container]
    body = bytearray()
    value = seed
    for _ in range(max(0, size - len(head))):
        value = (value * 1103515245 + 12345) & 0x7FFFFFFF
        body.append(value >> 16 & 0xFF)
    return head + bytes(body)


def wav_bytes(seconds: float = 0.5, sample_rate: int = 8000) -> bytes:
    """生成一段真实可解码的 WAV 字节，供流水线测试使用。"""
    import io

    import numpy as np
    import soundfile as sf

    t = np.arange(int(seconds * sample_rate)) / sample_rate
    wave = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo = np.stack([wave, wave], axis=1)
    buffer = io.BytesIO()
    sf.write(buffer, stereo, sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()
