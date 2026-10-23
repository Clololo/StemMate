# -*- coding: utf-8 -*-
import struct

import pytest

from core.qmc.footer import FooterParseError, parse_footer
from tests.support import (
    musicex_footer,
    pcv1_footer,
    qtag_footer,
    sample_plaintext,
    stag_footer,
)


def test_pcv1_footer_roundtrip():
    footer = parse_footer(sample_plaintext() + pcv1_footer("QUJDREVGRw=="))
    assert footer is not None
    assert footer.kind == "PcV1Legacy"
    assert footer.ekey == "QUJDREVGRw=="
    assert footer.total_size == len("QUJDREVGRw==") + 4


def test_qtag_footer_roundtrip():
    blob = b"\x00" * 64 + qtag_footer("QUJDREVGRw==", song_id=123)
    footer = parse_footer(blob)
    assert footer is not None
    assert footer.kind == "QTag"
    assert footer.ekey == "QUJDREVGRw=="
    assert footer.resource_id == 123
    assert footer.has_embedded_key


def test_stag_footer_has_no_key():
    blob = b"\x00" * 64 + stag_footer(song_id=456, media_mid="001abcDEFghiJK")
    footer = parse_footer(blob)
    assert footer is not None
    assert footer.kind == "STag"
    assert footer.ekey is None
    assert footer.resource_id == 456
    assert footer.media_mid == "001abcDEFghiJK"
    assert not footer.has_embedded_key


def test_musicex_footer_metadata():
    blob = b"\x00" * 64 + musicex_footer()
    footer = parse_footer(blob)
    assert footer is not None
    assert footer.kind == "MusicEx"
    assert footer.song_id == 1251166
    assert footer.media_mid == "002ejEdb4KTwBw"
    assert footer.media_filename == "O4M000015Tgb2RIq59.mgg"
    assert footer.ekey is None
    assert footer.total_size == 0xC0


def test_no_footer_returns_none():
    assert parse_footer(b"\xff" * 4096) is None
    assert parse_footer(b"OggS" + b"\x00" * 1020) is None


def test_broken_qtag_raises():
    payload = b"not-base64!,1,2"
    blob = payload + struct.pack(">I", len(payload)) + b"QTag"
    with pytest.raises(FooterParseError):
        parse_footer(blob)


def test_musicex_wrong_version_raises():
    bad = bytearray(musicex_footer(version=2))
    with pytest.raises(FooterParseError):
        parse_footer(bytes(bad))
