# -*- coding: utf-8 -*-
import json

from core.settings import AppSettings, load_settings, save_settings


def test_defaults(tmp_path):
    settings = load_settings(tmp_path / "missing.json")
    assert settings.quality == "fast"
    assert settings.output_format == "mp3"
    assert settings.bitrate == 320
    assert settings.threads == 0
    assert settings.keep_vocals is False


def test_roundtrip(tmp_path):
    target = tmp_path / "settings.json"
    original = AppSettings(
        quality="high",
        keep_vocals=True,
        output_format="wav",
        bitrate=256,
        output_dir="D:/out",
        model_dir="D:/models",
        threads=4,
        ekey_db="C:/db.sqlite",
    )
    save_settings(original, target)
    loaded = load_settings(target)
    assert loaded == original.normalized()


def test_corrupt_file_falls_back(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text("{not json", encoding="utf-8")
    assert load_settings(target).output_format == "mp3"


def test_invalid_values_are_normalized(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps({
            "quality": "ultra",
            "output_format": "ogg",
            "bitrate": 111,
            "threads": 99,
            "keep_vocals": "yes",
            "unknown": 1,
        }),
        encoding="utf-8",
    )
    settings = load_settings(target)
    assert settings.quality == "fast"
    assert settings.output_format == "mp3"
    assert settings.bitrate == 320
    assert settings.threads == 16
    assert settings.keep_vocals is True


def test_save_creates_parent_directory(tmp_path):
    target = tmp_path / "nested" / "dir" / "settings.json"
    save_settings(AppSettings(), target)
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8"))["output_format"] == "mp3"
