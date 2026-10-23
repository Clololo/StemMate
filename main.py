# -*- coding: utf-8 -*-
"""伴生 StemMate 应用入口。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

APP_NAME = "伴生 StemMate"
APP_VERSION = "1.1.0"


def _crash_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "StemMate" / "logs" / "crash.log"


def _install_excepthook() -> None:
    """未捕获异常写入日志并弹窗，避免打包后静默退出。"""

    def hook(exc_type, exc_value, exc_tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        path = _crash_log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n{text}\n")
        except OSError:
            pass
        try:
            QMessageBox.critical(
                None,
                "程序出错",
                f"{exc_type.__name__}: {exc_value}\n\n详细信息已写入：\n{path}",
            )
        except Exception:
            pass
        sys.stderr.write(text)

    sys.excepthook = hook


def _run_selftest(app: QApplication) -> int:
    """打包后自检：不依赖命令行参数，验证关键依赖与主窗口可创建。"""
    from ui.main_window import MainWindow

    checks: dict[str, object] = {}
    for module in (
        "soundfile",
        "onnxruntime",
        "lameenc",
        "soxr",
        "demucs_onnx",
        "huggingface_hub",
        "PySide6",
        "av",
    ):
        try:
            __import__(module)
            checks[module] = True
        except Exception as exc:  # pragma: no cover - 打包环境诊断
            checks[module] = f"ERROR: {exc}"

    window_ok = False
    try:
        window = MainWindow()
        window.show()
        app.processEvents()
        window_ok = True
        window.close()
    except Exception as exc:  # pragma: no cover
        checks["window"] = f"ERROR: {exc}"

    report = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "imports": checks,
        "window": window_ok,
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    try:
        report_path = _crash_log_path().with_name("selftest.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(payload, encoding="utf-8")
    except OSError:
        pass
    return 0 if window_ok and all(value is True for value in checks.values()) else 1


def _run_process(args) -> int:
    """无界面处理单个文件（自动化验收 / 脚本调用）。"""
    from core.pipeline import PipelineOptions, process_file

    options = PipelineOptions(
        quality=args.quality,
        keep_vocals=args.keep_vocals,
        output_format=args.format,
        bitrate=args.bitrate,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        model_dir=Path(args.model_dir) if args.model_dir else None,
    )
    report: dict[str, object] = {"source": str(args.process)}
    try:
        result = process_file(args.process, options)
    except Exception as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        code = 2
    else:
        report.update(
            ok=True,
            instrumental=str(result.instrumental),
            vocals=str(result.vocals) if result.vocals else None,
            backend=result.backend,
            elapsed=round(result.elapsed, 2),
        )
        code = 0

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    return code


def _run_harvest(args) -> int:
    """无界面扫描本地 QQ 音乐数据，找出目标文件的 ekey。"""
    from core.qmc.harvest import harvest, store_matches

    targets = list(args.target or [])
    report: dict[str, object] = {"targets": targets, "paths": list(args.harvest)}
    if not targets:
        report.update(ok=False, error="至少需要一个 --target 文件")
        code = 2
    else:
        result = harvest(args.harvest, targets)
        saved = store_matches(result) if args.store_keys and result.matched_count else 0
        report.update(
            ok=result.matched_count > 0,
            matched=result.matched_count,
            scanned_files=result.scanned_files,
            candidates=result.candidate_count,
            skipped=result.skipped_targets,
            errors=result.errors,
            stored=saved,
            items=[
                {
                    "target": key,
                    "media_mid": match.media_mid,
                    "source": str(match.source),
                    "ekey": match.ekey,
                }
                for key, match in result.matches.items()
            ],
        )
        code = 0 if result.matched_count > 0 else 1

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.harvest_report:
        target = Path(args.harvest_report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--selftest", action="store_true", help="检查运行环境后退出")
    parser.add_argument("--version", action="store_true", help="显示版本号")
    parser.add_argument("--process", metavar="FILE", help="无界面处理单个音频文件")
    parser.add_argument("--output-dir", help="输出目录（默认与源文件同目录）")
    parser.add_argument("--format", choices=["mp3", "wav"], default="mp3")
    parser.add_argument("--bitrate", type=int, default=320)
    parser.add_argument("--quality", choices=["fast", "high"], default="fast")
    parser.add_argument("--keep-vocals", action="store_true")
    parser.add_argument("--model-dir", help="模型缓存目录")
    parser.add_argument("--report", help="把处理结果写入 JSON 文件")
    parser.add_argument(
        "--harvest", action="append", metavar="PATH",
        help="扫描 QQ 音乐本地数据（文件或目录），可重复",
    )
    parser.add_argument(
        "--target", action="append", metavar="FILE",
        help="需要提取密钥的目标文件，可重复",
    )
    parser.add_argument("--store-keys", action="store_true", help="把命中的 ekey 写入内置密钥库")
    parser.add_argument("--harvest-report", help="把提取结果写入 JSON 文件")
    args, qt_args = parser.parse_known_args(argv)

    if args.version:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0

    if args.harvest:
        return _run_harvest(args)

    if args.process:
        return _run_process(args)

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("StemMate")
    app.setApplicationVersion(APP_VERSION)

    _install_excepthook()

    if args.selftest:
        return _run_selftest(app)

    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
