# -*- coding: utf-8 -*-
"""队列与批量行为测试（离屏 Qt）。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import ui.main_window as mw  # noqa: E402
from core.pipeline import PipelineResult  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def _pump(app, predicate, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _fake_runner(order, delay=0.0):
    def fake_process(source, options, progress):
        order.append(Path(source).name)
        progress("decrypt", 1.0, "解密完成")
        time.sleep(delay)
        progress("separate", None, "分离中")
        progress("export", 1.0, "导出完成")
        output = Path(source).with_name(Path(source).stem + "_instrumental.wav")
        output.write_bytes(b"RIFF")
        return PipelineResult(
            source=Path(source),
            instrumental=output,
            vocals=None,
            backend="mock",
            decrypted_from="plain",
            elapsed=0.01,
        )

    return fake_process


def _make_window(app, monkeypatch, tmp_path, delay=0.0):
    order: list[str] = []
    monkeypatch.setattr(mw, "process_file", _fake_runner(order, delay))
    monkeypatch.setattr(mw, "missing_model_files", lambda *a, **k: [])
    monkeypatch.setattr(mw, "load_settings", lambda: mw.AppSettings())
    monkeypatch.setattr(mw, "save_settings", lambda *a, **k: None)

    window = mw.MainWindow()
    paths = []
    for index in range(3):
        path = tmp_path / f"曲目{index}.mp3"
        path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00")
        paths.append(path)
    window._add_paths(paths)
    return window, paths, order


def test_batch_three_files_all_done(app, monkeypatch, tmp_path):
    window, paths, order = _make_window(app, monkeypatch, tmp_path)
    assert window.table.rowCount() == 3

    window._start_queue()
    finished = _pump(
        app,
        lambda: window._worker is not None
        and window._worker.isFinished()
        and all(row.status == mw.STATUS_DONE for row in window._rows.values()),
    )
    assert finished
    assert order == [path.name for path in paths]  # 严格按队列顺序

    for row in window._rows.values():
        assert row.outputs and row.outputs[0].exists()
        assert row.started_at is None

    window.close()


def test_stop_marks_remaining_as_stopped(app, monkeypatch, tmp_path):
    window, _paths, order = _make_window(app, monkeypatch, tmp_path, delay=0.4)

    window._start_queue()
    assert _pump(app, lambda: window._batch_index >= 1, 5)
    window._stop_queue()
    assert _pump(app, lambda: window._worker is not None and window._worker.isFinished(), 15)

    statuses = [row.status for row in window._rows.values()]
    assert mw.STATUS_STOPPED in statuses
    assert len(order) == 1  # 只处理了第一个文件
    stopped = [row for row in window._rows.values() if row.status == mw.STATUS_STOPPED]
    assert len(stopped) == 2
    assert all(row.started_at is None for row in stopped)

    window.close()


def test_retry_failed_requeues_rows(app, monkeypatch, tmp_path):
    window, _paths, _order = _make_window(app, monkeypatch, tmp_path)
    rows = list(window._rows.values())
    window._set_status(rows[0], mw.STATUS_FAILED)
    window._set_status(rows[1], mw.STATUS_STOPPED)

    window._retry_failed()
    assert all(row.status == mw.STATUS_WAITING for row in rows)

    window.close()


def test_settings_are_passed_to_pipeline(app, monkeypatch, tmp_path):
    captured = {}

    def fake_process(source, options, progress):
        captured["options"] = options
        output = Path(source).with_name(Path(source).stem + "_instrumental.wav")
        output.write_bytes(b"RIFF")
        return PipelineResult(
            source=Path(source),
            instrumental=output,
            vocals=None,
            backend="mock",
            decrypted_from="plain",
            elapsed=0.01,
        )

    monkeypatch.setattr(mw, "process_file", fake_process)
    monkeypatch.setattr(mw, "missing_model_files", lambda *a, **k: [])
    monkeypatch.setattr(
        mw,
        "load_settings",
        lambda: mw.AppSettings(
            quality="high",
            keep_vocals=True,
            output_format="wav",
            threads=3,
            output_dir=str(tmp_path / "out"),
            model_dir=str(tmp_path / "models"),
        ),
    )
    monkeypatch.setattr(mw, "save_settings", lambda *a, **k: None)

    window = mw.MainWindow()
    source = tmp_path / "s.mp3"
    source.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00")
    window._add_paths([source])
    window._start_queue()
    assert _pump(app, lambda: captured, 5)

    options = captured["options"]
    assert options.quality == "high"
    assert options.keep_vocals is True
    assert options.output_format == "wav"
    assert options.threads == 3
    assert str(options.output_dir) == str(tmp_path / "out")
    assert str(options.model_dir) == str(tmp_path / "models")

    window.close()
