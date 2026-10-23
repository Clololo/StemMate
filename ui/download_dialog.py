# -*- coding: utf-8 -*-
"""首次运行模型下载对话框（带字节级进度）。"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.separator import (
    QUALITY_SPECS,
    download_models,
    missing_model_files,
    normalize_quality,
)

_MB = 1024 * 1024


def overall_percent(file_index: int, total_files: int, done: int, total: int) -> int:
    """把「第几个文件 + 当前文件进度」折算成 0-100 的总进度。"""
    if total_files <= 0:
        return 0
    fraction = (done / total) if total else 0.0
    percent = ((file_index + fraction) / total_files) * 100.0
    return max(0, min(100, int(percent)))


class ModelDownloadWorker(QThread):
    file_changed = Signal(int, int, str)
    byte_progress = Signal(int, int)
    failed = Signal(str)
    succeeded = Signal()

    def __init__(
        self,
        quality: str,
        model_dir: Path,
        keep_vocals: bool,
        parent=None,
    ):
        super().__init__(parent)
        self._quality = quality
        self._model_dir = model_dir
        self._keep_vocals = keep_vocals
        self._index = 0
        self._total = 1
        self._name = ""

    def run(self) -> None:  # noqa: D401
        try:
            download_models(
                self._quality,
                self._model_dir,
                keep_vocals=self._keep_vocals,
                progress=self._on_file,
                on_bytes=self.byte_progress.emit,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit()

    def _on_file(self, index: int, total: int, message: str) -> None:
        self._index = index
        self._total = max(1, total)
        self._name = message
        self.file_changed.emit(index, total, message)


class ModelDownloadDialog(QDialog):
    """下载完成后返回 Accepted，模型即可离线使用。"""

    def __init__(
        self,
        quality: str,
        model_dir: str | Path,
        keep_vocals: bool,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._quality = normalize_quality(quality)
        self._model_dir = Path(model_dir)
        self._keep_vocals = keep_vocals
        self._worker: ModelDownloadWorker | None = None
        self._started_at = time.time()
        self._file_index = 0
        self._file_total = 1

        spec = QUALITY_SPECS[self._quality]
        self.setWindowTitle("首次运行：下载分离模型")
        self.setMinimumWidth(520)
        self.setModal(True)

        title = QLabel("首次使用需要下载人声分离模型", self)
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        info = QLabel(
            f"质量档：{'高质量（htdemucs_ft）' if self._quality == 'high' else '快速（htdemucs）'}\n"
            f"预计下载约 {spec.approx_bytes // _MB} MB，保存到：\n{self._model_dir}\n\n"
            "只需下载一次，之后可完全离线使用。",
            self,
        )
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._file_label = QLabel("准备下载…", self)
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setAlignment(Qt.AlignCenter)

        self._detail_label = QLabel("", self)
        self._detail_label.setStyleSheet("color: #666;")

        self._retry_btn = QPushButton("重试", self)
        self._retry_btn.setVisible(False)
        self._retry_btn.clicked.connect(self._start)

        self._cancel_btn = QPushButton("取消", self)
        self._cancel_btn.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self._retry_btn)
        buttons.addStretch(1)
        buttons.addWidget(self._cancel_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(info)
        layout.addSpacing(8)
        layout.addWidget(self._file_label)
        layout.addWidget(self._bar)
        layout.addWidget(self._detail_label)
        layout.addSpacing(8)
        layout.addLayout(buttons)

        QTimer.singleShot(0, self._start)

    # ------------------------------------------------------------------ 下载

    def _start(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._retry_btn.setVisible(False)
        self._cancel_btn.setEnabled(False)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._file_label.setText("正在准备下载…")
        self._started_at = time.time()

        self._worker = ModelDownloadWorker(
            self._quality, self._model_dir, self._keep_vocals, self
        )
        self._worker.file_changed.connect(self._on_file_changed)
        self._worker.byte_progress.connect(self._on_bytes)
        self._worker.failed.connect(self._on_failed)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.start()

    def _on_file_changed(self, index: int, total: int, message: str) -> None:
        self._file_index = index
        self._file_total = max(1, total)
        name = message.replace("正在下载 ", "").rstrip("…")
        self._file_label.setText(f"正在下载 {name}（{min(index + 1, total)}/{total}）")
        self._bar.setValue(overall_percent(index, total, 0, 0))
        self._detail_label.setText("")

    def _on_bytes(self, done: int, total: int) -> None:
        percent = overall_percent(self._file_index, self._file_total, done, total)
        self._bar.setValue(percent)
        elapsed = max(0.1, time.time() - self._started_at)
        if total:
            self._detail_label.setText(
                f"{done / _MB:.1f} / {total / _MB:.1f} MB · 已用 {elapsed:.0f}s"
            )
        else:
            self._detail_label.setText(f"{done / _MB:.1f} MB · 已用 {elapsed:.0f}s")

    def _on_failed(self, message: str) -> None:
        self._cancel_btn.setEnabled(True)
        self._retry_btn.setVisible(True)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._file_label.setText("下载失败")
        self._detail_label.setText(message)
        QMessageBox.warning(self, "模型下载失败", message)

    def _on_succeeded(self) -> None:
        missing = missing_model_files(
            self._quality, self._model_dir, keep_vocals=self._keep_vocals
        )
        if missing:
            self._on_failed("下载完成但模型文件校验失败：" + "、".join(missing))
            return
        self._bar.setValue(100)
        self._file_label.setText("模型下载完成，即将开始处理…")
        self._cancel_btn.setEnabled(True)
        QTimer.singleShot(400, self.accept)

    # ------------------------------------------------------------------ 关闭

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker and self._worker.isRunning():
            event.ignore()
            self._detail_label.setText("下载进行中，完成后会自动继续…")
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self._worker and self._worker.isRunning():
            self._detail_label.setText("下载进行中，完成后会自动继续…")
            return
        super().reject()
