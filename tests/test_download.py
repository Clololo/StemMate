# -*- coding: utf-8 -*-
"""模型下载进度与对话框逻辑测试（不联网）。"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

import ui.download_dialog as download_dialog  # noqa: E402
from ui.download_dialog import ModelDownloadWorker, overall_percent  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_overall_percent():
    assert overall_percent(0, 1, 0, 0) == 0
    assert overall_percent(0, 1, 512, 1024) == 50
    assert overall_percent(1, 2, 0, 0) == 50
    assert overall_percent(1, 2, 1, 2) == 75
    assert overall_percent(3, 4, 0, 0) == 75
    assert overall_percent(9, 4, 0, 0) == 100


def test_worker_emits_progress(app, monkeypatch, tmp_path):
    def fake_download(quality, model_dir, keep_vocals=False, progress=None, on_bytes=None):
        progress(0, 2, "正在下载 a.onnx…")
        on_bytes(50, 100)
        progress(1, 2, "正在下载 b.onnx…")
        on_bytes(100, 100)
        progress(2, 2, "模型下载完成")
        return [tmp_path / "a.onnx", tmp_path / "b.onnx"]

    monkeypatch.setattr(download_dialog, "download_models", fake_download)

    worker = ModelDownloadWorker("fast", tmp_path, False)
    files: list[tuple[int, int, str]] = []
    bytes_seen: list[tuple[int, int]] = []
    done: list[bool] = []
    errors: list[str] = []
    worker.file_changed.connect(lambda i, t, m: files.append((i, t, m)))
    worker.byte_progress.connect(lambda d, t: bytes_seen.append((d, t)))
    worker.succeeded.connect(lambda: done.append(True))
    worker.failed.connect(errors.append)

    worker.run()  # 直接执行，避免测试里起线程

    assert files[0][:2] == (0, 2)
    assert (50, 100) in bytes_seen
    assert done == [True]
    assert errors == []


def test_worker_reports_failure(app, monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("离线")

    monkeypatch.setattr(download_dialog, "download_models", boom)
    worker = ModelDownloadWorker("fast", tmp_path, False)
    errors: list[str] = []
    worker.failed.connect(errors.append)
    worker.run()
    assert errors and "离线" in errors[0]


def test_dialog_completes_when_models_ready(app, monkeypatch, tmp_path):
    monkeypatch.setattr(download_dialog, "download_models", lambda *a, **k: [])
    monkeypatch.setattr(download_dialog, "missing_model_files", lambda *a, **k: [])

    dialog = download_dialog.ModelDownloadDialog("high", tmp_path, True)
    assert "下载分离模型" in dialog.windowTitle()

    deadline = time.time() + 5
    while time.time() < deadline:
        app.processEvents()
        if dialog.result() == QDialog.Accepted:
            break
        time.sleep(0.02)

    assert dialog.result() == QDialog.Accepted
    assert dialog._bar.value() == 100
    dialog.close()
