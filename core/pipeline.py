# -*- coding: utf-8 -*-
"""完整流水线：解密 → 人声分离 → 导出伴奏（可选人声轨）。"""
from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.decryptor import DecryptError, MissingEkeyError, decrypt_file
from core.separator import (
    QUALITY_FAST,
    SeparatorError,
    default_model_dir,
    normalize_quality,
    separate_audio,
)
from utils.audio import is_soundfile_readable, safe_filename, transcode_to_wav, write_audio

STAGE_DECRYPT = "decrypt"
STAGE_CONVERT = "convert"
STAGE_SEPARATE = "separate"
STAGE_EXPORT = "export"


class PipelineError(Exception):
    """流水线失败。"""

    def __init__(self, message: str, *, needs_key: bool = False):
        super().__init__(message)
        self.needs_key = needs_key


@dataclass
class PipelineOptions:
    quality: str = QUALITY_FAST
    keep_vocals: bool = False
    output_format: str = "wav"          # "wav" / "mp3"
    bitrate: int = 320
    output_dir: Path | None = None      # None 表示与源文件同目录
    model_dir: Path | None = None
    threads: int = 0                    # 0 = 自动
    ekey: str | None = None
    ekey_db: Path | None = None
    overwrite: bool = False

    def model_path(self) -> Path:
        return Path(self.model_dir) if self.model_dir else default_model_dir()


@dataclass
class PipelineResult:
    source: Path
    instrumental: Path
    vocals: Path | None
    backend: str
    decrypted_from: str
    elapsed: float
    warnings: list[str] = field(default_factory=list)


def _notify(progress, stage: str, fraction: float | None, message: str) -> None:
    if progress is not None:
        progress(stage, fraction, message)


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    index = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{index}{suffix}"
        index += 1
    return candidate


def process_file(
    source: str | Path,
    options: PipelineOptions | None = None,
    progress=None,
) -> PipelineResult:
    """处理单个文件：解密（如需要）→ 分离 → 导出。"""
    opts = options or PipelineOptions()
    quality = normalize_quality(opts.quality)
    src = Path(source)
    started = time.time()
    warnings: list[str] = []

    workdir = Path(tempfile.mkdtemp(prefix="stemmate-"))
    try:
        _notify(progress, STAGE_DECRYPT, 0.0, "解密中…")

        def on_decrypt(done: int, total: int) -> None:
            fraction = (done / total) if total else None
            _notify(progress, STAGE_DECRYPT, fraction, "解密中…")

        try:
            decrypted = decrypt_file(
                src,
                workdir=workdir,
                ekey=opts.ekey,
                ekey_db=opts.ekey_db,
                progress=on_decrypt,
            )
        except DecryptError as exc:
            raise PipelineError(str(exc), needs_key=isinstance(exc, MissingEkeyError)) from exc

        if decrypted.output != src:
            _notify(progress, STAGE_DECRYPT, 1.0, "解密完成")
        else:
            _notify(progress, STAGE_DECRYPT, 1.0, "未加密文件，跳过解密")

        separation_input = decrypted.output
        if not is_soundfile_readable(separation_input):
            _notify(progress, STAGE_CONVERT, None, "转换音频格式…")
            try:
                separation_input = transcode_to_wav(
                    separation_input, workdir / "converted.wav"
                )
            except Exception as exc:
                raise PipelineError(
                    f"无法解码该音频格式（{decrypted.container}）：{exc}"
                ) from exc
            warnings.append(f"已将 {decrypted.container} 转换为 WAV 再分离")

        _notify(progress, STAGE_SEPARATE, None, "分离中…")

        def on_separator(stage: str, message: str) -> None:
            target = "download" if stage == "download" else STAGE_SEPARATE
            _notify(progress, target, None, message)

        try:
            separated = separate_audio(
                separation_input,
                quality=quality,
                model_dir=opts.model_path(),
                keep_vocals=opts.keep_vocals,
                threads=opts.threads,
                progress=on_separator,
            )
        except SeparatorError as exc:
            raise PipelineError(str(exc)) from exc
        warnings.extend(separated.warnings)

        _notify(progress, STAGE_EXPORT, None, "导出中…")
        out_dir = Path(opts.output_dir) if opts.output_dir else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = safe_filename(src.stem, "audio")

        output_format = (opts.output_format or "wav").lower()
        if output_format not in ("wav", "mp3"):
            raise PipelineError(f"暂不支持的输出格式：{opts.output_format}")
        suffix = f".{output_format}"

        instrumental_path = _unique_path(out_dir, f"{stem}_instrumental", suffix)
        write_audio(
            instrumental_path,
            separated.instrumental,
            separated.sample_rate,
            container=output_format,
            bitrate=opts.bitrate,
        )

        vocals_path: Path | None = None
        if opts.keep_vocals and separated.vocals is not None:
            vocals_path = _unique_path(out_dir, f"{stem}_vocals", suffix)
            write_audio(
                vocals_path,
                separated.vocals,
                separated.sample_rate,
                container=output_format,
                bitrate=opts.bitrate,
            )

        _notify(progress, STAGE_EXPORT, 1.0, "导出完成")
        return PipelineResult(
            source=src,
            instrumental=instrumental_path,
            vocals=vocals_path,
            backend=separated.backend,
            decrypted_from=decrypted.container,
            elapsed=time.time() - started,
            warnings=warnings,
        )
    finally:
        # 只清理本函数创建的临时目录，源文件永远不动。
        shutil.rmtree(workdir, ignore_errors=True)
