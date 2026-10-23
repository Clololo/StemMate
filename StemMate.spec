# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（目录模式）。

用法：
    python -m PyInstaller StemMate.spec --noconfirm --clean

产物：dist/StemMate/StemMate.exe
"""
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

hiddenimports = [
    "demucs_onnx",
    "demucs_onnx._hub",
    "demucs_onnx.inference",
    "demucs_onnx.providers",
    "onnxruntime",
    "onnxruntime.capi._pybind_state",
    "soundfile",
    "lameenc",
    "lameenc._lameenc",
    "soxr",
    "huggingface_hub",
    "huggingface_hub.errors",
    "huggingface_hub.utils",
    "av",
    "av.audio",
    "av.audio.resampler",
    "av.error",
]
hiddenimports += [
    module
    for module in collect_submodules("demucs_onnx")
    if not module.startswith(("demucs_onnx.export", "demucs_onnx.browser"))
]

# 这些包只是其它依赖的「可选能力」，运行时用不到；
# 如果不排除，PyInstaller 会把 torch / cv2 / scipy 等数百 MB 一起打进目录。
_HEAVY_EXCLUDES = [
    "torch",
    "torchaudio",
    "torchvision",
    "torchgen",
    "demucs",
    "onnx",
    "onnxscript",
    "tensorflow",
    "jax",
    "transformers",
    "tokenizers",
    "cv2",
    "polars",
    "pyarrow",
    "faiss",
    "sklearn",
    "scipy",
    "librosa",
    "PIL",
    "pandas",
    "matplotlib",
    "IPython",
    "notebook",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "tkinter",
    "pytest",
    "_pytest",
    "tests",
    "tools",
]

binaries = (
    collect_dynamic_libs("onnxruntime")
    + collect_dynamic_libs("soundfile")
    + collect_dynamic_libs("soxr")
    + collect_dynamic_libs("lameenc")
)
datas = collect_data_files("soundfile") + collect_data_files("certifi")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_HEAVY_EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StemMate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="StemMate",
)
