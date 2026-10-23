# -*- coding: utf-8 -*-
import os

from core.qmc.ciphers import (
    Qmc1Cipher,
    Qmc2MapCipher,
    Qmc2Rc4Cipher,
    compress_master_key,
    container_from_magic,
    make_cipher,
)


def _payload(size: int, seed: int = 7) -> bytes:
    out = bytearray()
    value = seed
    for _ in range(size):
        value = (value * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(value >> 16 & 0xFF)
    return bytes(out)


def _chunked(cipher, data: bytes, chunks: list[int]) -> bytes:
    out = bytearray()
    offset = 0
    index = 0
    while index < len(data):
        take = min(chunks[index % len(chunks)], len(data) - index)
        out += cipher.decrypt(data[index:index + take], offset)
        index += take
        offset += take
    return bytes(out)


def test_qmc1_chunking_matches_whole_file():
    data = _payload(120_000)
    cipher = Qmc1Cipher()
    whole = cipher.decrypt(data, 0)
    assert _chunked(cipher, data, [1, 7, 1000, 65536]) == whole
    assert cipher.decrypt(whole, 0) == data  # 对称


def test_qmc1_boundary_around_0x7fff():
    cipher = Qmc1Cipher()
    large = _payload(0x9000, seed=11)
    whole = cipher.decrypt(large, 0)
    piece = cipher.decrypt(large[0x7FF0:0x8030], 0x7FF0)
    assert piece == whole[0x7FF0:0x8030]


def test_map_key_is_128_bytes():
    assert len(compress_master_key(os.urandom(256))) == 128
    assert len(compress_master_key(os.urandom(128))) == 128


def test_map_chunking_matches_whole_file():
    master = os.urandom(256)
    cipher = Qmc2MapCipher(master)
    data = _payload(200_000)
    whole = cipher.decrypt(data, 0)
    assert _chunked(cipher, data, [1, 128, 4096, 70000]) == whole
    assert cipher.decrypt(whole, 0) == data


def test_rc4_chunking_matches_whole_file():
    master = os.urandom(512)
    cipher = Qmc2Rc4Cipher(master)
    data = _payload(200_000)
    whole = cipher.decrypt(data, 0)

    assert _chunked(cipher, data, [1, 127, 128, 5120, 33333]) == whole
    # 从任意偏移开始解密，结果与整体解密的对应片段一致
    for offset in (1, 127, 128, 129, 5120, 5121, 77_777):
        assert cipher.decrypt(data[offset:offset + 9000], offset) == whole[offset:offset + 9000]
    assert cipher.decrypt(whole, 0) == data


def test_make_cipher_by_key_length():
    assert isinstance(make_cipher(os.urandom(256)), Qmc2MapCipher)
    assert isinstance(make_cipher(os.urandom(300)), Qmc2MapCipher)
    assert isinstance(make_cipher(os.urandom(301)), Qmc2Rc4Cipher)
    assert isinstance(make_cipher(os.urandom(512)), Qmc2Rc4Cipher)


def test_container_sniffing():
    assert container_from_magic(b"fLaC\x00\x00") == "flac"
    assert container_from_magic(b"OggS\x00") == "ogg"
    assert container_from_magic(b"RIFF\x00\x00\x00\x00WAVE") == "wav"
    assert container_from_magic(b"ID3\x03") == "mp3"
    assert container_from_magic(b"\xff\xfb\x90\x00") == "mp3"
    assert container_from_magic(b"\x00\x00\x00\x20ftypM4A ") == "m4a"
    assert container_from_magic(b"garbage!") is None
