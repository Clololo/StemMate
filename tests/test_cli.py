# -*- coding: utf-8 -*-
"""命令行处理入口测试（不真正分离）。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import core.pipeline as pipeline_module
import main as app_main
from core.pipeline import PipelineResult


def _namespace(tmp_path: Path, report: Path) -> argparse.Namespace:
    return argparse.Namespace(
        process=str(tmp_path / "song.mgg"),
        output_dir=str(tmp_path / "out"),
        format="mp3",
        bitrate=320,
        quality="fast",
        keep_vocals=False,
        model_dir=str(tmp_path / "models"),
        report=str(report),
    )


def test_run_process_writes_report(tmp_path, monkeypatch):
    def fake_process(source, options):
        assert options.output_format == "mp3"
        assert options.quality == "fast"
        output = Path(options.output_dir) / "song_instrumental.mp3"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"ID3")
        return PipelineResult(
            source=Path(source),
            instrumental=output,
            vocals=None,
            backend="mock",
            decrypted_from="PcV1Legacy",
            elapsed=0.5,
        )

    monkeypatch.setattr(pipeline_module, "process_file", fake_process)
    report = tmp_path / "result.json"
    code = app_main._run_process(_namespace(tmp_path, report))

    assert code == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["backend"] == "mock"
    assert data["instrumental"].endswith("song_instrumental.mp3")


def test_run_process_reports_failure(tmp_path, monkeypatch):
    def boom(source, options):
        raise RuntimeError("模型缺失")

    monkeypatch.setattr(pipeline_module, "process_file", boom)
    report = tmp_path / "result.json"
    code = app_main._run_process(_namespace(tmp_path, report))

    assert code == 2
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert "模型缺失" in data["error"]
