# -*- coding: utf-8 -*-
"""用户设置的读写（JSON，不依赖 Qt，便于测试）。

设置文件位置：``%APPDATA%\\StemMate\\settings.json``。
读取时对每个字段做校验与兜底，损坏的文件不会影响启动。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from core.separator import QUALITY_FAST, QUALITY_HIGH

VALID_QUALITIES = (QUALITY_FAST, QUALITY_HIGH)
VALID_FORMATS = ("mp3", "wav")
VALID_BITRATES = (128, 192, 256, 320)


def settings_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "StemMate"
    return Path.home() / ".config" / "stemmate"


def settings_path() -> Path:
    return settings_dir() / "settings.json"


@dataclass
class AppSettings:
    quality: str = QUALITY_FAST
    keep_vocals: bool = False
    output_format: str = "mp3"
    bitrate: int = 320
    output_dir: str | None = None      # None / 空串 = 与源文件同目录
    model_dir: str | None = None       # None / 空串 = 默认缓存目录
    threads: int = 0                   # 0 = 自动
    ekey_db: str | None = None
    dark_mode: bool = False

    def normalized(self) -> "AppSettings":
        return AppSettings(
            quality=self.quality if self.quality in VALID_QUALITIES else QUALITY_FAST,
            keep_vocals=bool(self.keep_vocals),
            output_format=self.output_format if self.output_format in VALID_FORMATS else "mp3",
            bitrate=self.bitrate if self.bitrate in VALID_BITRATES else 320,
            output_dir=(self.output_dir or None),
            model_dir=(self.model_dir or None),
            threads=max(0, min(int(self.threads or 0), 16)),
            ekey_db=(self.ekey_db or None),
            dark_mode=bool(self.dark_mode),
        )


def load_settings(path: str | Path | None = None) -> AppSettings:
    target = Path(path) if path else settings_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppSettings()
    if not isinstance(raw, dict):
        return AppSettings()
    known = {f for f in AppSettings().__dataclass_fields__}
    data = {key: value for key, value in raw.items() if key in known}
    try:
        return AppSettings(**data).normalized()
    except (TypeError, ValueError):
        return AppSettings()


def save_settings(settings: AppSettings, path: str | Path | None = None) -> Path:
    target = Path(path) if path else settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(settings.normalized()), ensure_ascii=False, indent=2)
    target.write_text(payload, encoding="utf-8")
    return target
