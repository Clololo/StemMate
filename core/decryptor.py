# -*- coding: utf-8 -*-
"""格式检测与 QMC 解密入口。

对外只暴露两件事：``detect_format()`` 判断文件属于哪种封装，
``decrypt_file()`` 把加密音频写进临时目录并返回可播放文件的路径。
源文件始终只读，不会被修改。
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.qmc import keystore
from core.qmc.ciphers import Qmc1Cipher, container_from_magic, make_cipher
from core.qmc.constants import OUTPUT_EXTENSION, V1_EXTENSIONS, V2_EXTENSIONS
from core.qmc.ekey import EKeyError, decrypt_ekey
from core.qmc.footer import Footer, FooterParseError, parse_footer
from utils.audio import safe_filename

TAIL_PROBE = 4096
CHUNK_SIZE = 1 << 20
ENV_EKEY = "STEMMATE_EKEY"

ProgressCallback = Callable[[int, int], None]


class DecryptError(Exception):
    """解密失败。"""


class UnsupportedFormatError(DecryptError):
    """文件既不是 QMC 加密音频，也不是可识别的普通音频。"""


class MissingEkeyError(DecryptError):
    """文件不内嵌密钥，需要用户提供 ekey 或密钥库。"""

    def __init__(self, message: str, footer: Footer | None = None):
        super().__init__(message)
        self.footer = footer


class InvalidKeyError(DecryptError):
    """密钥错误，解密结果不是可识别音频。"""


@dataclass(frozen=True)
class FormatInfo:
    family: str
    """``qmcv1`` / ``qmcv2`` / ``plain``。"""
    container: str
    """封装类型：``QTag`` / ``STag`` / ``MusicEx`` / ``PcV1Legacy`` / ``QMCv1`` / ``plain``。"""
    output_ext: str
    footer: Footer | None = None

    @property
    def needs_ekey(self) -> bool:
        return self.family == "qmcv2" and (self.footer is None or not self.footer.has_embedded_key)


@dataclass(frozen=True)
class DecryptResult:
    source: Path
    output: Path
    family: str
    container: str
    output_format: str
    duration: float | None = None
    note: str = ""


def _read_tail(path: Path, size: int) -> bytes:
    length = min(TAIL_PROBE, size)
    with path.open("rb") as fh:
        fh.seek(size - length)
        return fh.read(length)


def _read_head(path: Path, length: int = 16) -> bytes:
    with path.open("rb") as fh:
        return fh.read(length)


def detect_format(path: str | Path) -> FormatInfo:
    """检测文件格式。"""
    source = Path(path)
    if not source.is_file():
        raise DecryptError(f"文件不存在：{source}")

    size = source.stat().st_size
    if size < 16:
        raise UnsupportedFormatError("文件太小，无法识别格式")

    suffix = source.suffix.lower().lstrip(".")
    head = _read_head(source)
    tail = _read_tail(source, size)

    footer: Footer | None = None
    try:
        footer = parse_footer(tail)
    except FooterParseError as exc:
        # 尾部明确带有已知标记却解析失败，说明文件损坏，不要误判为普通音频。
        if tail.endswith((b"QTag", b"STag", b"musicex\x00")):
            raise UnsupportedFormatError(f"文件尾部损坏：{exc}") from exc

    if footer is not None:
        if footer.total_size >= size:
            raise UnsupportedFormatError("尾部长度异常，文件可能不完整")
        out_ext = OUTPUT_EXTENSION.get(suffix, "bin")
        return FormatInfo("qmcv2", footer.kind, out_ext, footer)

    plain = container_from_magic(head)
    if plain is not None:
        return FormatInfo("plain", "plain", suffix or plain)

    if suffix in V1_EXTENSIONS or suffix in V2_EXTENSIONS:
        # 没有现代尾部时，老格式一律按 QMC v1 静态密钥尝试。
        return FormatInfo("qmcv1", "QMCv1", OUTPUT_EXTENSION.get(suffix, "bin"))

    raise UnsupportedFormatError(
        f"无法识别的文件格式（扩展名 .{suffix or '无'}），既没有 QMC 尾部也没有已知音频头"
    )


def _resolve_ekey(
    source: Path,
    info: FormatInfo,
    ekey: str | Path | bytes | None,
    ekey_db: str | Path | None,
) -> str:
    """按优先级取出可用的 EKey 字符串。"""
    if info.footer and info.footer.ekey:
        return info.footer.ekey

    if ekey is not None:
        text = _coerce_ekey(ekey)
        if text:
            return text

    env_value = os.environ.get(ENV_EKEY, "").strip()
    if env_value:
        return env_value

    footer = info.footer
    names = [
        source.name,
        footer.media_filename if footer else None,
        footer.media_mid if footer else None,
    ]
    names = [name for name in names if name]

    if ekey_db:
        found = keystore.lookup_ekey(ekey_db, *names)
        if found:
            return found

    found = keystore.search_default_databases(*names)
    if found:
        return found[0]

    detail = []
    if footer and footer.media_mid:
        detail.append(f"歌曲 mid：{footer.media_mid}")
    if footer and footer.media_filename:
        detail.append(f"文件名：{footer.media_filename}")
    suffix = ("（" + "，".join(detail) + "）") if detail else ""
    raise MissingEkeyError(
        f"该文件为 {info.container} 封装，密钥未内嵌{suffix}。"
        "请在“设置 → 密钥库”中导入 QQ 音乐密钥库，或为该文件提供 ekey 后重试。",
        footer=footer,
    )


def _coerce_ekey(value: str | Path | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("latin-1").strip()
    text = str(value).strip()
    # 允许直接传入一个保存 ekey 的文本文件。
    if text and len(text) < 4096 and Path(text).is_file():
        raw = Path(text).read_bytes()
        return raw.decode("utf-8", "ignore").strip()
    return text


def _unique_path(directory: Path, stem: str, ext: str) -> Path:
    ext = ext.lstrip(".")
    candidate = directory / f"{stem}.{ext}"
    index = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{index}.{ext}"
        index += 1
    return candidate


def decrypt_file(
    source: str | Path,
    *,
    workdir: str | Path | None = None,
    ekey: str | Path | bytes | None = None,
    ekey_db: str | Path | None = None,
    progress: ProgressCallback | None = None,
) -> DecryptResult:
    """解密一个文件；普通音频直接返回原路径。"""
    src = Path(source)
    info = detect_format(src)

    if info.family == "plain":
        return DecryptResult(
            source=src,
            output=src,
            family="plain",
            container="plain",
            output_format=src.suffix.lstrip(".").lower(),
            note="未加密文件，已跳过解密",
        )

    if info.family == "qmcv1":
        cipher = Qmc1Cipher()
    else:
        key_text = _resolve_ekey(src, info, ekey, ekey_db)
        try:
            master_key = decrypt_ekey(key_text)
        except EKeyError as exc:
            raise InvalidKeyError(f"EKey 无效：{exc}") from exc
        cipher = make_cipher(master_key)

    size = src.stat().st_size
    audio_size = size - (info.footer.total_size if info.footer else 0)
    if audio_size <= 0:
        raise UnsupportedFormatError("解密数据长度为 0，文件可能不完整")

    out_dir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="stemmate-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(src.stem, "audio")
    out_path = _unique_path(out_dir, stem, info.output_ext)

    done = 0
    try:
        with src.open("rb") as fin, out_path.open("wb") as fout:
            fin.seek(0)
            while done < audio_size:
                chunk = fin.read(min(CHUNK_SIZE, audio_size - done))
                if not chunk:
                    break
                fout.write(cipher.decrypt(chunk, done))
                done += len(chunk)
                if progress:
                    progress(done, audio_size)
    except OSError as exc:
        out_path.unlink(missing_ok=True)
        raise DecryptError(f"写入临时文件失败：{exc}") from exc
    except Exception:
        out_path.unlink(missing_ok=True)
        raise

    magic = container_from_magic(_read_head(out_path))
    if magic is None:
        out_path.unlink(missing_ok=True)
        raise InvalidKeyError(
            "解密结果不是可识别的音频，密钥可能不正确，或该文件不是完整的 QMC 文件。"
        )

    if magic != info.output_ext:
        corrected = _unique_path(out_dir, stem, magic)
        out_path.replace(corrected)
        out_path = corrected

    return DecryptResult(
        source=src,
        output=out_path,
        family=info.family,
        container=info.container,
        output_format=magic,
    )
