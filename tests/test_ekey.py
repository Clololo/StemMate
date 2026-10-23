# -*- coding: utf-8 -*-
import pytest

from core.qmc.ekey import EKeyError, decrypt_ekey
from tests.support import craft_ekey_v1, craft_ekey_v2


def test_ekey_v1_roundtrip():
    master = bytes(range(256))
    ekey = craft_ekey_v1(master)
    assert decrypt_ekey(ekey) == master


def test_ekey_v1_long_key_roundtrip():
    master = bytes((i * 7) & 0xFF for i in range(512))
    ekey = craft_ekey_v1(master)
    assert decrypt_ekey(ekey) == master


def test_ekey_v2_roundtrip():
    master = bytes((255 - i) & 0xFF for i in range(256))
    v1 = craft_ekey_v1(master)
    v2 = craft_ekey_v2(v1)
    assert v2.startswith("UVFNdXNpYyBFbmNWMixLZXk6")
    assert decrypt_ekey(v2) == master


def test_ekey_accepts_whitespace():
    master = bytes(range(128, 256))
    ekey = craft_ekey_v1(master)
    assert decrypt_ekey(f"  {ekey}  \n") == master


def test_ekey_garbage_raises():
    with pytest.raises(EKeyError):
        decrypt_ekey("")
    with pytest.raises(EKeyError):
        decrypt_ekey("not-a-real-ekey")
