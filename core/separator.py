# -*- coding: utf-8 -*-
"""人声分离后端封装。

- 高质量：demucs-onnx 的 ``htdemucs_ft`` 专家模型组；
- 快速：优先 Spleeter 2-stem，未安装时自动回退到 demucs-onnx 的 ``htdemucs``
  单文件模型（同样 CPU 可用）。

急口令（karaoke）模式：伴奏 = drums + bass + other，人声单独取 vocals。
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Windows 默认不支持符号链接，HF 缓存会打警告；打包后控制台不可见，直接静默。
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

QUALITY_FAST = "fast"
QUALITY_HIGH = "high"
QUALITY_LABELS = {
    QUALITY_FAST: "快速（Spleeter / htdemucs）",
    QUALITY_HIGH: "高质量（HT-Demucs FT）",
}

BACKEND_SPLEETER = "spleeter"
BACKEND_DEMUCS = "demucs-onnx"

_ACCOMPANIMENT_STEMS = ("drums", "bass", "other")
_MB = 1024 * 1024


class SeparatorError(Exception):
    """分离失败。"""


class ModelDownloadError(SeparatorError):
    """模型下载失败（通常是没有网络或磁盘空间不足）。"""


@dataclass(frozen=True)
class QualitySpec:
    key: str
    model: str
    files: tuple[str, ...]
    approx_bytes: int
    description: str


QUALITY_SPECS = {
    QUALITY_FAST: QualitySpec(
        key=QUALITY_FAST,
        model="htdemucs",
        files=("htdemucs.onnx",),
        approx_bytes=330 * _MB,
        description="HT-Demucs 单文件 4-stem 模型，速度约为 FT 的 1.4 倍",
    ),
    QUALITY_HIGH: QualitySpec(
        key=QUALITY_HIGH,
        model="htdemucs_ft",
        files=(
            "htdemucs_ft_drums.onnx",
            "htdemucs_ft_bass.onnx",
            "htdemucs_ft_other.onnx",
            "htdemucs_ft_vocals.onnx",
        ),
        approx_bytes=4 * 320 * _MB,
        description="HT-Demucs FT 专家模型组，质量最佳，速度约为快速档的 3 倍耗时",
    ),
}


@dataclass
class SeparationResult:
    instrumental: np.ndarray  # (channels, samples) float32
    vocals: np.ndarray | None
    sample_rate: int
    backend: str
    warnings: list[str]


def normalize_quality(value: str | None) -> str:
    key = (value or QUALITY_FAST).lower()
    if key in (QUALITY_FAST, QUALITY_HIGH):
        return key
    if key in ("fast", "spleeter", "quick"):
        return QUALITY_FAST
    if key in ("high", "best", "ft", "demucs"):
        return QUALITY_HIGH
    raise SeparatorError(f"未知的质量档：{value}")


def default_model_dir() -> Path:
    """默认模型缓存目录（与系统盘用户目录绑定，可被设置覆盖）。"""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "StemMate" / "models"
    return Path.home() / ".cache" / "stemmate" / "models"


def _required_files(quality: str, keep_vocals: bool) -> tuple[str, ...]:
    spec = QUALITY_SPECS[normalize_quality(quality)]
    if spec.key == QUALITY_FAST:
        return spec.files
    stems = list(_ACCOMPANIMENT_STEMS) + (["vocals"] if keep_vocals else [])
    return tuple(f"htdemucs_ft_{stem}.onnx" for stem in stems)


def missing_model_files(
    quality: str,
    model_dir: str | Path | None = None,
    *,
    keep_vocals: bool = False,
) -> list[str]:
    """返回尚未下载的模型文件名。"""
    directory = Path(model_dir) if model_dir else default_model_dir()
    if not directory.exists():
        return list(_required_files(quality, keep_vocals))
    cached = {path.name for path in directory.rglob("*.onnx")}
    return [name for name in _required_files(quality, keep_vocals) if name not in cached]


def is_model_ready(
    quality: str,
    model_dir: str | Path | None = None,
    *,
    keep_vocals: bool = False,
) -> bool:
    return not missing_model_files(quality, model_dir, keep_vocals=keep_vocals)


def _model_targets(quality: str, keep_vocals: bool) -> list[tuple[str, str, str]]:
    """返回 [(显示名, HF repo, 文件名)]。"""
    from demucs_onnx import _hub

    if normalize_quality(quality) == QUALITY_FAST:
        info = _hub.MODEL_REGISTRY["htdemucs"]
        return [("htdemucs", info.repo, info.filename_fp32)]

    stems = list(_ACCOMPANIMENT_STEMS) + (["vocals"] if keep_vocals else [])
    return [
        (stem, _hub.SPECIALIST_REPOS[stem], f"htdemucs_ft_{stem}.onnx")
        for stem in stems
    ]


def _incomplete_bytes(directory: Path) -> int:
    """统计 Hugging Face 缓存目录里正在写入的 ``.incomplete`` 文件大小。"""
    total = 0
    for path in directory.rglob("*.incomplete"):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _remote_size(repo: str, filename: str) -> int:
    """通过 HEAD 请求拿到模型文件大小；失败时返回 0（调用方使用估算值）。"""
    try:
        import requests

        response = requests.head(
            f"https://huggingface.co/{repo}/resolve/main/{filename}",
            allow_redirects=True,
            timeout=10,
        )
        if response.ok:
            return int(response.headers.get("Content-Length") or 0)
    except Exception:
        pass
    return 0


def download_models(
    quality: str,
    model_dir: str | Path | None = None,
    *,
    keep_vocals: bool = False,
    progress=None,
    on_bytes=None,
) -> list[Path]:
    """下载当前质量档所需模型，返回本地路径列表。

    ``progress(done_files, total_files, message)`` 用于文件级进度；
    ``on_bytes(downloaded, total)`` 用于当前文件的字节级进度。
    """
    try:
        from demucs_onnx import _hub  # 包内私有模块，但这是唯一公开的下载入口
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise ModelDownloadError("未安装 demucs-onnx / huggingface_hub，无法下载模型。") from exc

    directory = Path(model_dir) if model_dir else default_model_dir()
    directory.mkdir(parents=True, exist_ok=True)

    quality_key = normalize_quality(quality)
    targets = _model_targets(quality_key, keep_vocals)

    # 粗略的磁盘空间检查：按每个模型 330 MB 估算。
    required = 350 * _MB * len(targets)
    try:
        free = shutil.disk_usage(directory).free
    except OSError:
        free = required
    if free < required:
        raise ModelDownloadError(
            f"磁盘空间不足：需要约 {required // _MB} MB，当前仅剩 {free // _MB} MB。"
        )

    paths: list[Path] = []
    total = len(targets)
    approx_per_file = QUALITY_SPECS[quality_key].approx_bytes // max(1, total)

    for index, (name, repo, filename) in enumerate(targets, start=1):
        if progress:
            progress(index - 1, total, f"正在下载 {name}…")

        expected = _remote_size(repo, filename) if on_bytes else 0
        expected = expected or approx_per_file
        baseline = _incomplete_bytes(directory)

        stop = threading.Event()
        monitor: threading.Thread | None = None
        if on_bytes:
            def poll(expected=expected, baseline=baseline) -> None:
                while not stop.wait(0.4):
                    done = max(0, _incomplete_bytes(directory) - baseline)
                    on_bytes(min(done, expected), expected)

            monitor = threading.Thread(target=poll, name="model-download-progress", daemon=True)
            monitor.start()

        try:
            path = hf_hub_download(repo, filename, cache_dir=str(directory))
        except Exception:
            # 兜底：走 demucs-onnx 自己的下载入口（可能是旧版 HF）。
            try:
                if quality_key == QUALITY_FAST:
                    path = _hub.download_single_model("htdemucs", cache_dir=directory)
                else:
                    path = _hub.download_stem_model(name, cache_dir=directory)
            except Exception as exc:
                raise ModelDownloadError(
                    f"模型下载失败（{name}）：{exc}。首次运行需要联网下载模型，"
                    "之后即可完全离线使用。"
                ) from exc
        finally:
            stop.set()
            if monitor is not None:
                monitor.join(timeout=2)

        paths.append(Path(path))
        if on_bytes:
            on_bytes(expected, expected)

    if progress:
        progress(total, total, "模型下载完成")
    return paths


def _has_spleeter() -> bool:
    return importlib.util.find_spec("spleeter") is not None


def backend_for(quality: str) -> str:
    """当前环境实际会使用的后端。"""
    if normalize_quality(quality) == QUALITY_FAST and _has_spleeter():
        return BACKEND_SPLEETER
    return BACKEND_DEMUCS


def _separate_spleeter(path: Path, keep_vocals: bool) -> SeparationResult:
    """Spleeter 2-stem 推理（需要 TensorFlow 环境，未安装时不会走到这里）。"""
    import soundfile as sf
    import soxr
    from spleeter.separator import Separator

    data, native_sr = sf.read(str(path), always_2d=True, dtype="float32")
    work = data
    if native_sr != 44100:
        work = soxr.resample(work, native_sr, 44100)

    separator = Separator("spleeter:2stems")
    prediction = separator.separate(work)
    accompaniment = np.asarray(prediction["accompaniment"], dtype=np.float32).T
    vocals = np.asarray(prediction["vocals"], dtype=np.float32).T

    def back(array: np.ndarray) -> np.ndarray:
        return soxr.resample(array, 44100, native_sr) if native_sr != 44100 else array

    return SeparationResult(
        instrumental=back(accompaniment),
        vocals=back(vocals) if keep_vocals else None,
        sample_rate=native_sr,
        backend=BACKEND_SPLEETER,
        warnings=[],
    )


def _available_commit_gb() -> float:
    """返回 Windows 当前可提交内存（GB）；其他平台返回一个较大值。"""
    if os.name != "nt":
        return 999.0
    try:
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemStatus()
        status.dwLength = ctypes.sizeof(_MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.ullAvailPageFile / (1024 ** 3)
    except Exception:
        return 999.0


def _ort_levels():
    """决定 ONNX Runtime 图优化级别。

    低内存机器上一次失败的 ENABLE_ALL/BASIC 初始化会留下碎片，导致后续
    重试也失败，因此内存不足时直接使用 DISABLE_ALL。
    """
    import onnxruntime as ort

    override = os.environ.get("STEMMATE_ORT_LEVEL", "auto").lower()
    if override == "basic":
        return [ort.GraphOptimizationLevel.ORT_ENABLE_BASIC]
    if override == "disable":
        return [ort.GraphOptimizationLevel.ORT_DISABLE_ALL]
    if _available_commit_gb() < 8.0:
        return [ort.GraphOptimizationLevel.ORT_DISABLE_ALL]
    return [
        ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,
        ort.GraphOptimizationLevel.ORT_DISABLE_ALL,
    ]


def _prepare_demucs_sessions(
    model_dir: Path,
    quality: str,
    keep_vocals: bool,
    threads: int = 0,
) -> None:
    """用更保守的 ORT 选项预先创建会话并注入 demucs-onnx 的会话池。

    demucs-onnx 默认使用 ``ORT_ENABLE_ALL`` + 内存 arena，在 8~16 GB 的
    办公本上偶发 ``bad allocation``。这里改用 ``ORT_ENABLE_BASIC`` 并关闭
    CPU arena、限制线程数，可显著降低初始化峰值内存。
    """
    try:
        import onnxruntime as ort
        from demucs_onnx import _hub, inference
    except ImportError:
        return

    model_paths: list[Path] = []
    if normalize_quality(quality) == QUALITY_FAST:
        model_paths.append(Path(_hub.download_single_model("htdemucs", cache_dir=model_dir)))
    else:
        stems = list(_ACCOMPANIMENT_STEMS) + (["vocals"] if keep_vocals else [])
        for stem in stems:
            model_paths.append(Path(_hub.download_stem_model(stem, cache_dir=model_dir)))

    providers = ("CPUExecutionProvider",)
    pool = inference.session_pool()
    for path in model_paths:
        key = (str(path), "", providers)
        if key in pool._sessions:
            continue

        session = None
        last_error: Exception | None = None
        # 低内存时直接关闭全部图优化，避免「先失败、再重试」造成的内存碎片。
        for level in _ort_levels():
            options = ort.SessionOptions()
            options.graph_optimization_level = level
            options.enable_cpu_mem_arena = False
            options.enable_mem_pattern = False
            options.intra_op_num_threads = threads or min(4, os.cpu_count() or 1)
            options.inter_op_num_threads = 1
            try:
                session = ort.InferenceSession(
                    str(path), sess_options=options, providers=list(providers)
                )
                break
            except Exception as exc:
                message = str(exc).lower()
                last_error = exc
                if "bad allocation" not in message and "out of memory" not in message:
                    raise
        if session is None:
            raise RuntimeError(f"无法创建 ONNX 会话：{last_error}")
        pool._sessions[key] = session


def _separate_demucs(
    path: Path,
    quality: str,
    model_dir: Path,
    keep_vocals: bool,
    threads: int = 0,
) -> SeparationResult:
    try:
        import soundfile as sf
        import demucs_onnx
    except ImportError as exc:
        raise SeparatorError(
            "未安装 demucs-onnx。请执行：pip install 'demucs-onnx[mp3]'"
        ) from exc

    spec = QUALITY_SPECS[normalize_quality(quality)]
    stems = list(_ACCOMPANIMENT_STEMS) + (["vocals"] if keep_vocals else [])

    kwargs = dict(
        model=spec.model,
        stems=stems,
        cache_dir=str(model_dir),
        providers="cpu",
        progress=False,
        verbose=False,
    )

    # 模型齐全时强制 Hugging Face 走本地缓存：既避免每次联网校验，也让程序离线可用。
    models_ready = not missing_model_files(
        quality, model_dir, keep_vocals=keep_vocals
    )
    previous_offline = os.environ.get("HF_HUB_OFFLINE")
    if models_ready:
        os.environ["HF_HUB_OFFLINE"] = "1"

    try:
        if models_ready:
            try:
                _prepare_demucs_sessions(model_dir, quality, keep_vocals, threads)
            except Exception:
                pass  # 注入失败就退回 demucs-onnx 默认会话

        try:
            result = demucs_onnx.separate(str(path), **kwargs)
        except Exception as exc:
            # 内存紧张时 ORT 可能瞬时分配失败，清理缓存后重试一次。
            message = str(exc)
            if "bad allocation" not in message.lower() and "out of memory" not in message.lower():
                raise SeparatorError(f"分离失败：{exc}") from exc
            try:
                demucs_onnx.session_pool().clear()
            except Exception:
                pass
            try:
                _prepare_demucs_sessions(model_dir, quality, keep_vocals, threads)
            except Exception:
                pass
            try:
                result = demucs_onnx.separate(str(path), **kwargs)
            except Exception as retry_exc:
                raise SeparatorError(
                    "内存不足，分离失败。请关闭其他占用内存的程序，或改用“快速”质量档。"
                ) from retry_exc
    finally:
        if models_ready:
            if previous_offline is None:
                os.environ.pop("HF_HUB_OFFLINE", None)
            else:
                os.environ["HF_HUB_OFFLINE"] = previous_offline

    try:
        instrumental = sum(np.asarray(result[name], dtype=np.float32) for name in _ACCOMPANIMENT_STEMS)
    except KeyError as exc:
        raise SeparatorError(f"模型输出缺少声部：{exc}") from exc

    vocals = None
    if keep_vocals and "vocals" in result:
        vocals = np.asarray(result["vocals"], dtype=np.float32)

    native_sr = int(sf.info(str(path)).samplerate)
    return SeparationResult(
        instrumental=instrumental,
        vocals=vocals,
        sample_rate=native_sr,
        backend=BACKEND_DEMUCS,
        warnings=[],
    )


def separate_audio(
    path: str | Path,
    *,
    quality: str = QUALITY_FAST,
    model_dir: str | Path | None = None,
    keep_vocals: bool = False,
    threads: int = 0,
    progress=None,
) -> SeparationResult:
    """对音频文件做人声分离。

    ``progress(stage, message)`` 只用于粗粒度提示（下载 / 分离），
    细粒度进度由调用方用「忙碌」状态展示。
    """
    source = Path(path)
    if not source.is_file():
        raise SeparatorError(f"音频文件不存在：{source}")

    quality_key = normalize_quality(quality)
    directory = Path(model_dir) if model_dir else default_model_dir()

    if progress:
        progress("prepare", "检查模型…")

    if quality_key == QUALITY_FAST and _has_spleeter():
        if progress:
            progress("separate", "Spleeter 正在分离人声…")
        try:
            return _separate_spleeter(source, keep_vocals)
        except Exception as exc:  # Spleeter 环境异常时退回 demucs
            fallback_note = f"Spleeter 不可用，已改用 htdemucs：{exc}"
    else:
        fallback_note = None

    missing = missing_model_files(quality_key, directory, keep_vocals=keep_vocals)
    if missing:
        if progress:
            progress("download", "首次运行，正在下载模型…")
        download_models(
            quality_key,
            directory,
            keep_vocals=keep_vocals,
            progress=lambda done, total, message: progress("download", message) if progress else None,
        )

    if progress:
        progress("separate", "正在分离人声（CPU，可能需要数分钟）…")
    result = _separate_demucs(source, quality_key, directory, keep_vocals, threads)
    if quality_key == QUALITY_FAST and not _has_spleeter():
        result.warnings.append("当前环境未安装 Spleeter，快速档已使用 htdemucs。")
    if fallback_note:
        result.warnings.append(fallback_note)
    return result


def model_cache_size(model_dir: str | Path | None = None) -> int:
    """返回模型缓存占用的字节数。"""
    directory = Path(model_dir) if model_dir else default_model_dir()
    if not directory.exists():
        return 0
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def cleanup_model_cache(model_dir: str | Path | None = None) -> None:
    """删除整个模型缓存目录（设置页用）。"""
    directory = Path(model_dir) if model_dir else default_model_dir()
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
