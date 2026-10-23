# -*- coding: utf-8 -*-
import numpy as np
import pytest

from core import separator
from core.separator import SeparationResult


def _touch_model(directory, names):
    for name in names:
        target = directory / "models--StemSplitio--x" / "snapshots" / "rev" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"stub")


def test_normalize_quality():
    assert separator.normalize_quality(None) == separator.QUALITY_FAST
    assert separator.normalize_quality("fast") == separator.QUALITY_FAST
    assert separator.normalize_quality("high") == separator.QUALITY_HIGH
    assert separator.normalize_quality("ft") == separator.QUALITY_HIGH
    with pytest.raises(separator.SeparatorError):
        separator.normalize_quality("ultra")


def test_missing_models_initially_all(tmp_path):
    missing = separator.missing_model_files("fast", tmp_path)
    assert missing == ["htdemucs.onnx"]

    missing_high = separator.missing_model_files("high", tmp_path, keep_vocals=True)
    assert len(missing_high) == 4
    missing_high_no_vocals = separator.missing_model_files("high", tmp_path)
    assert len(missing_high_no_vocals) == 3


def test_models_ready_after_download(tmp_path):
    _touch_model(tmp_path, ["htdemucs.onnx"])
    assert separator.is_model_ready("fast", tmp_path)

    _touch_model(tmp_path, [
        "htdemucs_ft_drums.onnx",
        "htdemucs_ft_bass.onnx",
        "htdemucs_ft_other.onnx",
        "htdemucs_ft_vocals.onnx",
    ])
    assert separator.is_model_ready("high", tmp_path, keep_vocals=True)


def test_separate_uses_demucs_and_sums_stems(tmp_path, monkeypatch):
    audio = np.zeros((2, 4410), dtype=np.float32)
    audio[0] = 0.1

    called = {}

    def fake_demucs(path, quality, model_dir, keep_vocals, threads=0):
        called["quality"] = quality
        called["keep_vocals"] = keep_vocals
        inst = np.full((2, 4410), 0.2, dtype=np.float32)
        voc = np.full((2, 4410), 0.05, dtype=np.float32)
        return SeparationResult(inst, voc if keep_vocals else None, 44100, "demucs-onnx", [])

    monkeypatch.setattr(separator, "_has_spleeter", lambda: False)
    monkeypatch.setattr(separator, "missing_model_files", lambda *a, **k: [])
    monkeypatch.setattr(separator, "_separate_demucs", fake_demucs)

    (tmp_path / "x.wav").write_bytes(b"RIFF")
    result = separator.separate_audio(tmp_path / "x.wav", quality="fast", keep_vocals=True)
    assert called == {"quality": "fast", "keep_vocals": True}
    assert result.instrumental.shape == (2, 4410)
    assert result.vocals is not None
    assert any("Spleeter" in warning for warning in result.warnings)


def test_download_models_wraps_errors(tmp_path, monkeypatch):
    import demucs_onnx._hub as hub
    import huggingface_hub

    def boom(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(hub, "download_single_model", boom)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", boom)
    with pytest.raises(separator.ModelDownloadError):
        separator.download_models("fast", tmp_path)


def test_ort_level_override(monkeypatch):
    import onnxruntime as ort

    monkeypatch.setenv("STEMMATE_ORT_LEVEL", "disable")
    assert separator._ort_levels() == [ort.GraphOptimizationLevel.ORT_DISABLE_ALL]

    monkeypatch.setenv("STEMMATE_ORT_LEVEL", "basic")
    assert separator._ort_levels() == [ort.GraphOptimizationLevel.ORT_ENABLE_BASIC]

    monkeypatch.setenv("STEMMATE_ORT_LEVEL", "auto")
    assert separator._available_commit_gb() > 0
    assert separator._ort_levels()


def test_incomplete_bytes(tmp_path):
    (tmp_path / "snap").mkdir()
    (tmp_path / "snap" / "a.incomplete").write_bytes(b"x" * 100)
    (tmp_path / "snap" / "b.incomplete").write_bytes(b"y" * 40)
    (tmp_path / "snap" / "done.onnx").write_bytes(b"z" * 999)
    assert separator._incomplete_bytes(tmp_path) == 140


def test_remote_size_uses_content_length(monkeypatch):
    import requests

    class Response:
        ok = True
        headers = {"Content-Length": "12345"}

    monkeypatch.setattr(requests, "head", lambda *a, **k: Response())
    assert separator._remote_size("org/repo", "model.onnx") == 12345

    class Broken:
        ok = False
        headers = {}

    monkeypatch.setattr(requests, "head", lambda *a, **k: Broken())
    assert separator._remote_size("org/repo", "model.onnx") == 0
