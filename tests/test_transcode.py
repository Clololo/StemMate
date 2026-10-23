# -*- coding: utf-8 -*-
"""m4a 转码与支持列表测试。"""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from utils.audio import is_soundfile_readable, transcode_to_wav

pytest.importorskip("av")


def make_m4a(path, seconds: float = 2.0, sample_rate: int = 44100):
    """用 PyAV 现场编码一个 AAC-in-MP4 样本。"""
    import av

    t = np.arange(int(sample_rate * seconds)) / sample_rate
    wave = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo = np.stack([wave, wave])

    container = av.open(str(path), "w")
    try:
        stream = container.add_stream("aac", rate=sample_rate)
        stream.layout = "stereo"
        stream.bit_rate = 192_000
        frame = av.AudioFrame.from_ndarray(stereo, format="fltp", layout="stereo")
        frame.sample_rate = sample_rate
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    finally:
        container.close()
    return path


def _tone_wav(path, seconds=1.0, sample_rate=44100):
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    wave = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    sf.write(str(path), np.stack([wave, wave], axis=1), sample_rate, subtype="PCM_16")
    return path


def test_soundfile_readability(tmp_path):
    wav = _tone_wav(tmp_path / "a.wav")
    m4a = make_m4a(tmp_path / "a.m4a", 0.5)

    assert is_soundfile_readable(wav) is True
    assert is_soundfile_readable(m4a) is False


def test_transcode_m4a_to_wav(tmp_path):
    m4a = make_m4a(tmp_path / "song.m4a", 2.0)
    target = transcode_to_wav(m4a, tmp_path / "converted.wav")

    info = sf.info(str(target))
    assert info.samplerate == 44100
    assert info.channels == 2
    assert abs(info.duration - 2.0) < 0.2

    data, _rate = sf.read(str(target), always_2d=True)
    assert np.max(np.abs(data)) > 0.1


def test_transcode_missing_file(tmp_path):
    with pytest.raises(Exception):
        transcode_to_wav(tmp_path / "missing.m4a", tmp_path / "out.wav")


def test_supported_suffix_list():
    from ui.main_window import SUPPORTED_SUFFIXES

    assert ".m4a" in SUPPORTED_SUFFIXES
    assert ".opus" in SUPPORTED_SUFFIXES
    for removed in (".aac", ".wma", ".ape"):
        assert removed not in SUPPORTED_SUFFIXES
