# -*- coding: utf-8 -*-
"""音频辅助函数：容器嗅探、时长探测、文件名整理。

阶段 1 只用到「识别与探测」；阶段 2 的转码与导出也会放在这里。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.qmc.ciphers import container_from_magic

__all__ = [
    "AudioInfo",
    "format_duration",
    "guess_container",
    "is_soundfile_readable",
    "probe_audio",
    "safe_filename",
    "transcode_to_wav",
    "write_audio",
]


@dataclass(frozen=True)
class AudioInfo:
    container: str
    duration: float | None = None
    sample_rate: int | None = None
    channels: int | None = None


def guess_container(head: bytes) -> str | None:
    return container_from_magic(head)


def probe_audio(path: str | Path) -> AudioInfo | None:
    """用 soundfile 读取音频信息；失败时返回 ``None``（不影响主流程）。"""
    target = Path(path)
    head = b""
    try:
        with target.open("rb") as fh:
            head = fh.read(16)
    except OSError:
        return None

    container = guess_container(head) or target.suffix.lstrip(".").lower()
    try:
        import soundfile as sf

        info = sf.info(str(target))
        return AudioInfo(
            container=container,
            duration=float(info.duration),
            sample_rate=int(info.samplerate),
            channels=int(info.channels),
        )
    except Exception:
        # 解码器缺失（例如部分 m4a/ape）时仍然返回容器信息。
        return AudioInfo(container=container) if container else None


def format_duration(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "--:--"
    total = int(round(seconds))
    minutes, sec = divmod(total, 60)
    return f"{minutes}:{sec:02d}"


_ILLEGAL_NAME_CHARS = '<>:"/\\|?*'


def safe_filename(name: str, fallback: str = "audio") -> str:
    """把任意文本整理成可用的文件名。"""
    cleaned = "".join("_" if ch in _ILLEGAL_NAME_CHARS or ord(ch) < 32 else ch for ch in name)
    cleaned = cleaned.strip().strip(".")
    return cleaned or fallback


def _as_stereo(audio: np.ndarray) -> np.ndarray:
    data = np.asarray(audio, dtype=np.float32)
    if data.ndim == 1:
        data = np.stack([data, data], axis=0)
    if data.ndim != 2:
        raise ValueError(f"音频数组维度非法：{data.shape}")
    return data


def is_soundfile_readable(path: str | Path) -> bool:
    """判断 libsndfile 能否直接解码该文件（WAV/FLAC/OGG Vorbis/MP3）。"""
    try:
        import soundfile as sf

        sf.info(str(path))
        return True
    except Exception:
        return False


def transcode_to_wav(
    source: str | Path,
    destination: str | Path,
    target_rate: int = 44100,
) -> Path:
    """用 PyAV（FFmpeg）把 m4a/aac/wma/ape/opus 等转成临时 WAV。

    仅作为分离前的解码桥接，避免依赖外部 ffmpeg.exe。
    """
    try:
        import av
        from av.audio.resampler import AudioResampler
    except ImportError as exc:  # pragma: no cover - 打包环境已内置
        raise RuntimeError("未安装 PyAV，无法转换该音频格式") from exc

    source_path = Path(source)
    target_path = Path(destination)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    container = av.open(str(source_path))
    try:
        stream = next(iter(container.streams.audio), None)
        if stream is None:
            raise ValueError("文件里没有音频流")
        resampler = AudioResampler(format="fltp", layout="stereo", rate=int(target_rate))
        chunks: list[np.ndarray] = []
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray())
        for resampled in resampler.resample(None):
            chunks.append(resampled.to_ndarray())
    finally:
        container.close()

    if not chunks:
        raise ValueError("解码结果为空")

    audio = np.concatenate(chunks, axis=1)
    if audio.shape[0] == 1:
        audio = np.repeat(audio, 2, axis=0)
    audio = audio[:2]

    import soundfile as sf

    sf.write(str(target_path), np.clip(audio.T, -1.0, 1.0), int(target_rate), subtype="PCM_24")
    return target_path


def write_audio(
    path: str | Path,
    audio: np.ndarray,
    sample_rate: int,
    container: str = "wav",
    bitrate: int = 320,
) -> None:
    """把分离结果写成 WAV（PCM 16-bit）或 MP3。"""
    target = Path(path)
    data = _as_stereo(audio)
    container = container.lower()

    if container == "wav":
        import soundfile as sf

        sf.write(
            str(target),
            np.clip(data.T, -1.0, 1.0),
            int(sample_rate),
            subtype="PCM_16",
        )
        return

    if container == "mp3":
        import lameenc

        work, rate = data, int(sample_rate)
        if not 8000 <= rate <= 48000:
            import soxr

            work = soxr.resample(work.T, rate, 44100).T
            rate = 44100

        channels = work.shape[0]
        pcm = (np.clip(work.T, -1.0, 1.0) * 32767).astype("<i2").tobytes()

        encoder = lameenc.Encoder()
        encoder.set_bit_rate(max(32, min(int(bitrate), 320)))
        encoder.set_in_sample_rate(rate)
        encoder.set_channels(channels)
        encoder.set_quality(2)
        target.write_bytes(encoder.encode(pcm) + encoder.flush())
        return

    raise ValueError(f"不支持的输出格式：{container}")
