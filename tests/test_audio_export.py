# -*- coding: utf-8 -*-
import numpy as np
import soundfile as sf

from utils.audio import write_audio


def _tone(seconds=1.0, rate=44100):
    t = np.arange(int(rate * seconds)) / rate
    wave = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    return np.stack([wave, wave], axis=0)


def test_write_wav_roundtrip(tmp_path):
    target = tmp_path / "out.wav"
    write_audio(target, _tone(), 44100, container="wav")

    data, rate = sf.read(str(target), always_2d=True)
    assert rate == 44100
    assert data.shape == (44100, 2)
    assert np.max(np.abs(data)) > 0.2


def test_write_mp3_roundtrip(tmp_path):
    target = tmp_path / "out.mp3"
    write_audio(target, _tone(), 44100, container="mp3", bitrate=320)

    blob = target.read_bytes()
    assert len(blob) > 1000
    assert blob[:3] == b"ID3" or blob[0] == 0xFF

    info = sf.info(str(target))
    assert info.samplerate == 44100
    assert info.channels == 2
    assert abs(info.duration - 1.0) < 0.1


def test_write_mp3_resamples_high_rate(tmp_path):
    target = tmp_path / "out_96k.mp3"
    write_audio(target, _tone(0.5, 96000), 96000, container="mp3", bitrate=192)
    info = sf.info(str(target))
    assert info.samplerate == 44100
    assert abs(info.duration - 0.5) < 0.12


def test_write_unsupported_container(tmp_path):
    try:
        write_audio(tmp_path / "x.ogg", _tone(), 44100, container="ogg")
    except ValueError as exc:
        assert "不支持" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当抛出 ValueError")
