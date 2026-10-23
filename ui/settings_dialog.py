# -*- coding: utf-8 -*-
"""设置对话框：输出格式、人声轨、目录、线程数与密钥库。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.separator import cleanup_model_cache, default_model_dir, model_cache_size
from core.settings import VALID_BITRATES, AppSettings


def _format_size(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / 1024 ** 3:.2f} GB"
    if num_bytes >= 1024 ** 2:
        return f"{num_bytes / 1024 ** 2:.0f} MB"
    return f"{num_bytes / 1024:.0f} KB"


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(560)
        self._settings = settings.normalized()

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self._format_box = QComboBox(self)
        self._format_box.addItem("MP3（有损压缩）", "mp3")
        self._format_box.addItem("WAV（无损 PCM 16-bit）", "wav")
        index = self._format_box.findData(self._settings.output_format)
        self._format_box.setCurrentIndex(max(index, 0))
        self._format_box.currentIndexChanged.connect(self._sync_bitrate_state)
        form.addRow("输出格式：", self._format_box)

        self._bitrate_box = QComboBox(self)
        for value in VALID_BITRATES:
            self._bitrate_box.addItem(f"{value} kbps", value)
        bitrate_index = self._bitrate_box.findData(self._settings.bitrate)
        self._bitrate_box.setCurrentIndex(bitrate_index if bitrate_index >= 0 else len(VALID_BITRATES) - 1)
        form.addRow("MP3 码率：", self._bitrate_box)

        self._vocals_box = QCheckBox("同时输出人声轨（_vocals）", self)
        self._vocals_box.setChecked(self._settings.keep_vocals)
        form.addRow("", self._vocals_box)

        self._output_dir_edit = QLineEdit(self)
        self._output_dir_edit.setPlaceholderText("留空 = 与源文件同目录")
        self._output_dir_edit.setText(self._settings.output_dir or "")
        form.addRow("输出目录：", self._path_row(self._output_dir_edit, self._pick_output_dir))

        self._model_dir_edit = QLineEdit(self)
        default_dir = default_model_dir()
        self._model_dir_edit.setText(self._settings.model_dir or str(default_dir))
        self._model_dir_edit.textChanged.connect(lambda _text: self._refresh_cache_size())
        model_row = self._path_row(self._model_dir_edit, self._pick_model_dir)

        self._size_label = QLabel(self)
        clear_btn = QPushButton("清理缓存", self)
        clear_btn.clicked.connect(self._clear_models)
        model_row.addWidget(clear_btn)
        form.addRow("模型目录：", model_row)
        form.addRow("", self._size_label)

        self._threads_box = QSpinBox(self)
        self._threads_box.setRange(0, 16)
        self._threads_box.setSpecialValueText("自动")
        self._threads_box.setValue(self._settings.threads)
        self._threads_box.setToolTip("ONNX Runtime 推理线程数；自动 = min(4, CPU 核心数)")
        form.addRow("推理线程数：", self._threads_box)

        self._ekey_db_edit = QLineEdit(self)
        self._ekey_db_edit.setPlaceholderText("可选：QQ 音乐 player_process_db")
        self._ekey_db_edit.setText(self._settings.ekey_db or "")
        form.addRow("密钥库：", self._path_row(self._ekey_db_edit, self._pick_ekey_db))

        self._dark_box = QCheckBox("深色模式", self)
        self._dark_box.setChecked(self._settings.dark_mode)
        form.addRow("", self._dark_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self._sync_bitrate_state()
        self._refresh_cache_size()

    # ------------------------------------------------------------- UI 辅助

    def _path_row(self, edit: QLineEdit, picker) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        button = QPushButton("浏览…", self)
        button.clicked.connect(picker)
        row.addWidget(button)
        return row

    def _pick_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择输出目录", self._output_dir_edit.text() or str(Path.home())
        )
        if path:
            self._output_dir_edit.setText(path)

    def _pick_model_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择模型缓存目录", self._model_dir_edit.text() or str(Path.home())
        )
        if path:
            self._model_dir_edit.setText(path)
            self._refresh_cache_size()

    def _pick_ekey_db(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 QQ 音乐密钥库",
            self._ekey_db_edit.text() or str(Path.home()),
            "SQLite 数据库 (*.db *.sqlite *.sqlite3);;所有文件 (*)",
        )
        if path:
            self._ekey_db_edit.setText(path)

    def _sync_bitrate_state(self) -> None:
        self._bitrate_box.setEnabled(self._format_box.currentData() == "mp3")

    def _current_model_dir(self) -> Path:
        text = self._model_dir_edit.text().strip()
        return Path(text) if text else default_model_dir()

    def _refresh_cache_size(self) -> None:
        size = model_cache_size(self._current_model_dir())
        self._size_label.setText(f"当前缓存占用：{_format_size(size)}")

    def _clear_models(self) -> None:
        answer = QMessageBox.question(
            self,
            "清理模型缓存",
            "删除后下次处理需要重新下载模型，确定继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        cleanup_model_cache(self._current_model_dir())
        self._refresh_cache_size()
        QMessageBox.information(self, "已清理", "模型缓存已删除。")

    # ------------------------------------------------------------- 结果

    def result_settings(self) -> AppSettings:
        return AppSettings(
            quality=self._settings.quality,
            keep_vocals=self._vocals_box.isChecked(),
            output_format=self._format_box.currentData(),
            bitrate=int(self._bitrate_box.currentData()),
            output_dir=self._output_dir_edit.text().strip() or None,
            model_dir=self._model_dir_edit.text().strip() or None,
            threads=int(self._threads_box.value()),
            ekey_db=self._ekey_db_edit.text().strip() or None,
            dark_mode=self._dark_box.isChecked(),
        ).normalized()
