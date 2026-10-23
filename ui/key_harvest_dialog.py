# -*- coding: utf-8 -*-
"""QQ 音乐密钥提取对话框：扫描本地数据，验证并导入 ekey。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.qmc.harvest import HarvestResult, export_matches, harvest, store_matches


class HarvestWorker(QThread):
    progress = Signal(str, int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, paths: list[str], targets: list[Path], parent=None):
        super().__init__(parent)
        self._paths = paths
        self._targets = targets

    def run(self) -> None:  # noqa: D401
        try:
            result = harvest(self._paths, self._targets, progress=self.progress.emit)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.succeeded.emit(result)


class KeyHarvestDialog(QDialog):
    keys_stored = Signal(int)

    def __init__(
        self,
        targets: list[Path],
        parent: QWidget | None = None,
        initial_paths: list[str] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("从 QQ 音乐本地数据提取密钥")
        self.resize(860, 620)
        self._targets = [Path(t) for t in targets if Path(t).is_file()]
        self._worker: HarvestWorker | None = None
        self._result: HarvestResult | None = None

        header = QLabel("提取的 EKey 会先用目标文件实际试解密验证，不会盲存。", self)
        header.setStyleSheet("font-size: 14px; font-weight: 600;")

        target_label = QLabel(f"目标文件（{len(self._targets)} 个，来自当前队列）", self)
        self._target_list = QListWidget(self)
        for path in self._targets:
            self._target_list.addItem(str(path))
        self._target_list.setMaximumHeight(90)

        path_label = QLabel("QQ 音乐本地数据（可添加数据库、INI、缓存文件或整个目录）", self)
        self._path_list = QListWidget(self)
        self._path_list.setMaximumHeight(110)
        for initial in initial_paths or []:
            if initial:
                self._append_path(initial)

        path_buttons = QHBoxLayout()
        add_files = QPushButton("添加文件…", self)
        add_files.clicked.connect(self._add_files)
        add_folder = QPushButton("添加文件夹…", self)
        add_folder.clicked.connect(self._add_folder)
        remove = QPushButton("移除选中", self)
        remove.clicked.connect(self._remove_selected)
        path_buttons.addWidget(add_files)
        path_buttons.addWidget(add_folder)
        path_buttons.addWidget(remove)
        path_buttons.addStretch(1)

        hint = QLabel(
            "常见位置：安卓 player_process_db；Windows 的 QQ 音乐安装/数据目录、"
            "qqmusic.db、*.ini、缓存目录；macOS 的 qqmusic.sqlite 所在文件夹。",
            self,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")

        self._scan_btn = QPushButton("开始扫描并验证", self)
        self._scan_btn.setEnabled(bool(self._targets))
        self._scan_btn.clicked.connect(self._start)

        self._status = QLabel("请选择路径后开始扫描。", self)
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setAlignment(Qt.AlignCenter)

        self._table = QTableWidget(0, 4, self)
        self._table.setHorizontalHeaderLabels(["目标文件", "结果", "来源", "EKey"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.setColumnWidth(3, 180)

        actions = QHBoxLayout()
        self._store_btn = QPushButton("写入内置密钥库", self)
        self._store_btn.setEnabled(False)
        self._store_btn.clicked.connect(self._store)
        self._export_btn = QPushButton("导出 ekey 文件…", self)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export)
        close_btn = QPushButton("关闭", self)
        close_btn.clicked.connect(self.reject)
        actions.addWidget(self._store_btn)
        actions.addWidget(self._export_btn)
        actions.addStretch(1)
        actions.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(target_label)
        layout.addWidget(self._target_list)
        layout.addWidget(path_label)
        layout.addWidget(self._path_list)
        layout.addLayout(path_buttons)
        layout.addWidget(hint)
        layout.addWidget(self._scan_btn)
        layout.addWidget(self._status)
        layout.addWidget(self._bar)
        layout.addWidget(self._table, 1)
        layout.addLayout(actions)

        if not self._targets:
            self._status.setText("当前队列里没有需要密钥的文件。请先在主窗口添加 musicex / STag 文件。")

    # ------------------------------------------------------------- 路径管理

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 QQ 音乐数据文件",
            str(Path.home()),
            "所有文件 (*)",
        )
        for file in files:
            self._append_path(file)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择 QQ 音乐数据目录", str(Path.home()))
        if folder:
            self._append_path(folder)

    def _append_path(self, value: str) -> None:
        value = value.strip()
        if not value:
            return
        existing = {self._path_list.item(i).text() for i in range(self._path_list.count())}
        if value not in existing:
            self._path_list.addItem(value)

    def _remove_selected(self) -> None:
        for item in self._path_list.selectedItems():
            self._path_list.takeItem(self._path_list.row(item))

    # ------------------------------------------------------------- 扫描

    def _start(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        paths = [self._path_list.item(i).text() for i in range(self._path_list.count())]
        if not paths:
            QMessageBox.information(self, "缺少路径", "请先添加 QQ 音乐的数据文件或目录。")
            return

        self._scan_btn.setEnabled(False)
        self._store_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._table.setRowCount(0)
        self._bar.setRange(0, 0)
        self._status.setText("正在扫描…")

        self._worker = HarvestWorker(paths, self._targets, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, stage: str, done: int, total: int, message: str) -> None:
        if stage == "scan" and total:
            self._bar.setRange(0, 100)
            self._bar.setValue(int(done * 100 / total))
        self._status.setText(message)

    def _on_failed(self, message: str) -> None:
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._scan_btn.setEnabled(True)
        self._status.setText("扫描失败")
        QMessageBox.warning(self, "扫描失败", message)

    def _on_succeeded(self, result: HarvestResult) -> None:
        self._result = result
        self._bar.setRange(0, 100)
        self._bar.setValue(100)
        self._scan_btn.setEnabled(True)

        self._table.setRowCount(0)
        for target in self._targets:
            match = result.matches.get(str(target))
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(str(target)))
            if match:
                self._table.setItem(row, 1, QTableWidgetItem("已找到"))
                self._table.setItem(row, 2, QTableWidgetItem(str(match.source)))
                preview = match.ekey[:28] + "…" if len(match.ekey) > 28 else match.ekey
                item = QTableWidgetItem(preview)
                item.setToolTip(match.ekey)
                self._table.setItem(row, 3, item)
            else:
                self._table.setItem(row, 1, QTableWidgetItem("未找到"))
                self._table.setItem(row, 2, QTableWidgetItem("—"))
                self._table.setItem(row, 3, QTableWidgetItem("—"))

        summary = (
            f"扫描 {result.scanned_files} 个文件，候选 {result.candidate_count} 个，"
            f"匹配 {result.matched_count}/{len(self._targets)} 个目标。"
        )
        if result.skipped_targets:
            summary += " 已跳过：" + "、".join(result.skipped_targets)
        if result.errors:
            summary += " 提示：" + "；".join(result.errors[:3])
        self._status.setText(summary)

        self._store_btn.setEnabled(result.matched_count > 0)
        self._export_btn.setEnabled(result.matched_count > 0)

    # ------------------------------------------------------------- 入库 / 导出

    def _store(self) -> None:
        if self._result is None:
            return
        saved = store_matches(self._result)
        if saved:
            self.keys_stored.emit(saved)
            QMessageBox.information(
                self, "已写入", f"已把 {saved} 个 EKey 写入内置密钥库，可以直接重新处理对应文件。"
            )
        else:
            QMessageBox.warning(self, "写入失败", "无法写入内置密钥库（检查磁盘权限）。")

    def _export(self) -> None:
        if self._result is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择导出目录", str(Path.home()))
        if not folder:
            return
        written = export_matches(self._result, folder)
        QMessageBox.information(
            self, "已导出", f"已导出 {len(written) - 1} 个 ekey 文件与清单 ekeys.json。"
        )

    # ------------------------------------------------------------- 生命周期

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker and self._worker.isRunning():
            event.ignore()
            self._status.setText("扫描进行中，请稍候…")
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self._worker and self._worker.isRunning():
            self._status.setText("扫描进行中，请稍候…")
            return
        super().reject()
