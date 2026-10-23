# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from core.decryptor import (
    InvalidKeyError,
    MissingEkeyError,
    UnsupportedFormatError,
    decrypt_file,
    detect_format,
)
from tests.support import (
    craft_ekey_v1,
    encrypt_payload,
    encrypt_v1_payload,
    musicex_footer,
    pcv1_footer,
    qtag_footer,
    sample_plaintext,
)

DATASET_SAMPLE = Path(r"C:\Users\KC\Desktop\musictest\dataset\陈奕迅 - 浮夸_L.mgg")


def test_plain_audio_is_skipped(tmp_path):
    source = tmp_path / "普通歌曲.wav"
    data = sample_plaintext("wav")
    source.write_bytes(data)

    info = detect_format(source)
    assert info.family == "plain"

    result = decrypt_file(source)
    assert result.family == "plain"
    assert result.output == source
    assert source.read_bytes() == data


def test_detect_pcv1_mflac(tmp_path):
    master = bytes(range(256))
    ekey = craft_ekey_v1(master)
    source = tmp_path / "song.mflac"
    source.write_bytes(encrypt_payload(master, sample_plaintext("flac")) + pcv1_footer(ekey))

    info = detect_format(source)
    assert info.family == "qmcv2"
    assert info.container == "PcV1Legacy"
    assert info.output_ext == "flac"
    assert not info.needs_ekey


def test_decrypt_pcv1_map_mflac(tmp_path):
    master = bytes(range(256))
    ekey = craft_ekey_v1(master)
    plain = sample_plaintext("flac", size=200_000)
    source = tmp_path / "测试歌曲.mflac"
    source.write_bytes(encrypt_payload(master, plain) + pcv1_footer(ekey))

    calls: list[tuple[int, int]] = []
    result = decrypt_file(source, workdir=tmp_path / "out", progress=lambda d, t: calls.append((d, t)))

    assert result.output.suffix == ".flac"
    assert result.output.read_bytes() == plain
    assert calls and calls[-1][0] == calls[-1][1] == len(plain)


def test_decrypt_pcv1_rc4_mgg(tmp_path):
    master = bytes((i * 3) & 0xFF for i in range(512))
    ekey = craft_ekey_v1(master)
    plain = sample_plaintext("ogg", size=300_000)
    source = tmp_path / "RC4歌曲.mgg"
    source.write_bytes(encrypt_payload(master, plain) + pcv1_footer(ekey))

    result = decrypt_file(source, workdir=tmp_path / "out")
    assert result.output.suffix == ".ogg"
    assert result.output.read_bytes() == plain


def test_decrypt_qtag_embedded_key(tmp_path):
    master = bytes(range(128, 256))
    ekey = craft_ekey_v1(master)
    plain = sample_plaintext("ogg")
    source = tmp_path / "qtag.mgg"
    source.write_bytes(encrypt_payload(master, plain) + qtag_footer(ekey))

    assert detect_format(source).container == "QTag"
    result = decrypt_file(source, workdir=tmp_path / "out")
    assert result.output.read_bytes() == plain


def test_decrypt_qmc1_static(tmp_path):
    plain = sample_plaintext("flac", size=150_000)
    source = tmp_path / "old.qmcflac"
    source.write_bytes(encrypt_v1_payload(plain))

    info = detect_format(source)
    assert info.family == "qmcv1"

    result = decrypt_file(source, workdir=tmp_path / "out")
    assert result.output.suffix == ".flac"
    assert result.output.read_bytes() == plain


def test_musicex_requires_ekey(tmp_path):
    master = bytes(range(256))
    plain = sample_plaintext("ogg")
    source = tmp_path / "浮夸_L.mgg"
    source.write_bytes(encrypt_payload(master, plain) + musicex_footer())

    info = detect_format(source)
    assert info.container == "MusicEx"
    assert info.needs_ekey

    with pytest.raises(MissingEkeyError) as excinfo:
        decrypt_file(source, workdir=tmp_path / "out")
    assert "002ejEdb4KTwBw" in str(excinfo.value)
    assert excinfo.value.footer is not None


def test_musicex_with_explicit_ekey(tmp_path):
    master = bytes(range(256))
    ekey = craft_ekey_v1(master)
    plain = sample_plaintext("ogg")
    source = tmp_path / "musicex.mgg"
    source.write_bytes(encrypt_payload(master, plain) + musicex_footer())

    result = decrypt_file(source, workdir=tmp_path / "out", ekey=ekey)
    assert result.output.read_bytes() == plain


def test_musicex_with_ekey_file(tmp_path):
    master = bytes(range(256))
    ekey = craft_ekey_v1(master)
    plain = sample_plaintext("ogg")
    source = tmp_path / "musicex.mgg"
    source.write_bytes(encrypt_payload(master, plain) + musicex_footer())

    ekey_file = tmp_path / "song.ekey"
    ekey_file.write_text(ekey, encoding="utf-8")

    result = decrypt_file(source, workdir=tmp_path / "out", ekey=ekey_file)
    assert result.output.read_bytes() == plain


def test_wrong_key_raises(tmp_path):
    master = bytes(range(256))
    wrong = craft_ekey_v1(bytes((i + 1) & 0xFF for i in range(256)))
    source = tmp_path / "bad.mgg"
    source.write_bytes(encrypt_payload(master, sample_plaintext("ogg")) + musicex_footer())

    with pytest.raises(InvalidKeyError):
        decrypt_file(source, workdir=tmp_path / "out", ekey=wrong)


def test_unknown_file_raises(tmp_path):
    source = tmp_path / "mystery.bin"
    source.write_bytes(b"\x01\x02\x03" * 4000)
    with pytest.raises(UnsupportedFormatError):
        detect_format(source)


@pytest.mark.skipif(not DATASET_SAMPLE.exists(), reason="数据集样本不存在")
def test_real_dataset_is_musicex_without_key():
    info = detect_format(DATASET_SAMPLE)
    assert info.family == "qmcv2"
    assert info.container == "MusicEx"
    assert info.needs_ekey
    assert info.footer is not None
    assert info.footer.media_mid == "002ejEdb4KTwBw"
    assert info.footer.media_filename == "O4M000015Tgb2RIq59.mgg"

    with pytest.raises(MissingEkeyError) as excinfo:
        decrypt_file(DATASET_SAMPLE)
    assert "密钥未内嵌" in str(excinfo.value)
