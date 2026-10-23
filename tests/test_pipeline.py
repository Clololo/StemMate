# -*- coding: utf-8 -*-
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from core import pipeline
from core.decryptor import MissingEkeyError
from core.pipeline import PipelineError, PipelineOptions, process_file
from core.separator import SeparationResult
from tests.support import craft_ekey_v1, encrypt_payload, pcv1_footer, wav_bytes

SR = 8000
LENGTH = 800


def _fake_separation(keep_vocals=True):
    def fake(path, **kwargs):
        inst = np.full((2, LENGTH), 0.25, dtype=np.float32)
        voc = np.full((2, LENGTH), 0.1, dtype=np.float32) if keep_vocals else None
        return SeparationResult(inst, voc, SR, "demucs-onnx", [])

    return fake


def test_pipeline_plain_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "separate_audio", _fake_separation())
    source = tmp_path / "普通歌曲.wav"
    source.write_bytes(wav_bytes())

    result = process_file(source, PipelineOptions(quality="fast"))
    assert result.instrumental.name == "普通歌曲_instrumental.wav"
    assert result.instrumental.parent == tmp_path
    assert result.vocals is None

    data, sr = sf.read(str(result.instrumental), always_2d=True)
    assert sr == SR
    assert data.shape == (LENGTH, 2)
    assert np.allclose(data[:, 0], 0.25, atol=1e-3)


def test_pipeline_encrypted_and_vocals(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "separate_audio", _fake_separation())
    master = bytes(range(256))
    ekey = craft_ekey_v1(master)
    source = tmp_path / "加密歌曲.mgg"
    source.write_bytes(encrypt_payload(master, wav_bytes()) + pcv1_footer(ekey))

    result = process_file(source, PipelineOptions(quality="high", keep_vocals=True))
    assert result.instrumental.name == "加密歌曲_instrumental.wav"
    assert result.vocals is not None and result.vocals.name == "加密歌曲_vocals.wav"
    assert result.decrypted_from == "PcV1Legacy"
    assert source.stat().st_size > 0  # 源文件未被修改


def test_pipeline_output_dir_and_unique_names(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "separate_audio", _fake_separation())
    source = tmp_path / "song.wav"
    source.write_bytes(wav_bytes())
    out_dir = tmp_path / "out"

    first = process_file(source, PipelineOptions(output_dir=out_dir))
    second = process_file(source, PipelineOptions(output_dir=out_dir))
    assert first.instrumental.parent == out_dir
    assert first.instrumental.name == "song_instrumental.wav"
    assert second.instrumental.name == "song_instrumental-1.wav"


def test_pipeline_reports_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "separate_audio", _fake_separation())
    source = tmp_path / "song.wav"
    source.write_bytes(wav_bytes())

    events: list[tuple[str, float | None]] = []
    process_file(
        source,
        PipelineOptions(),
        progress=lambda stage, fraction, message: events.append((stage, fraction)),
    )

    stages = [stage for stage, _fraction in events]
    assert pipeline.STAGE_DECRYPT in stages
    assert pipeline.STAGE_SEPARATE in stages
    assert pipeline.STAGE_EXPORT in stages
    assert events[-1][0] == pipeline.STAGE_EXPORT


def test_pipeline_preserves_needs_key_flag(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise MissingEkeyError("缺少密钥", footer=None)

    monkeypatch.setattr(pipeline, "decrypt_file", missing)
    source = tmp_path / "x.mgg"
    source.write_bytes(b"x" * 100)

    with pytest.raises(PipelineError) as excinfo:
        process_file(source)
    assert excinfo.value.needs_key is True


def test_pipeline_wraps_separator_error(tmp_path, monkeypatch):
    from core.separator import SeparatorError

    def boom(*args, **kwargs):
        raise SeparatorError("模型炸了")

    monkeypatch.setattr(pipeline, "separate_audio", boom)
    source = tmp_path / "song.wav"
    source.write_bytes(wav_bytes())

    with pytest.raises(PipelineError) as excinfo:
        process_file(source)
    assert "模型炸了" in str(excinfo.value)
    assert excinfo.value.needs_key is False


def test_pipeline_mp3_output(tmp_path, monkeypatch):
    def fake_44k(path, **kwargs):
        return SeparationResult(
            np.full((2, 44100), 0.2, dtype=np.float32), None, 44100, "demucs-onnx", []
        )

    monkeypatch.setattr(pipeline, "separate_audio", fake_44k)
    source = tmp_path / "song.wav"
    source.write_bytes(wav_bytes())

    result = process_file(source, PipelineOptions(output_format="mp3", bitrate=192))
    assert result.instrumental.suffix == ".mp3"
    assert result.instrumental.stat().st_size > 1000

    info = sf.info(str(result.instrumental))
    assert info.samplerate == 44100
    assert abs(info.duration - 1.0) < 0.12


def test_pipeline_rejects_unknown_format(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "separate_audio", _fake_separation(False))
    source = tmp_path / "song.wav"
    source.write_bytes(wav_bytes())

    with pytest.raises(PipelineError):
        process_file(source, PipelineOptions(output_format="ogg"))


def test_pipeline_transcodes_m4a(tmp_path, monkeypatch):
    from tests.test_transcode import make_m4a

    source = make_m4a(tmp_path / "song.m4a", 1.0)
    seen = {}

    def fake_sep(path, **kwargs):
        info = sf.info(str(path))
        seen["suffix"] = Path(path).suffix
        seen["rate"] = info.samplerate
        return SeparationResult(
            np.full((2, 800), 0.2, dtype=np.float32), None, 44100, "demucs-onnx", []
        )

    monkeypatch.setattr(pipeline, "separate_audio", fake_sep)
    result = process_file(source, PipelineOptions(output_format="wav"))

    assert seen == {"suffix": ".wav", "rate": 44100}
    assert result.instrumental.exists()
    assert any("转换为 WAV" in warning for warning in result.warnings)
