# -*- coding: utf-8 -*-
"""主窗口：拖拽导入、队列、后台解密 + 分离。"""
from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from core.decryptor import MissingEkeyError
from core.pipeline import (
    STAGE_CONVERT,
    STAGE_DECRYPT,
    STAGE_EXPORT,
    STAGE_SEPARATE,
    PipelineError,
    PipelineOptions,
    process_file,
)
from core.separator import (
    QUALITY_HIGH,
    QUALITY_FAST,
    QUALITY_LABELS,
    SeparatorError,
    backend_for,
    default_model_dir,
    missing_model_files,
)
from core.settings import AppSettings, load_settings, save_settings
from ui.download_dialog import ModelDownloadDialog
from ui.key_harvest_dialog import KeyHarvestDialog
from ui.settings_dialog import SettingsDialog
from utils.theme import apply_theme

SUPPORTED_SUFFIXES = {
    ".mgg", ".mgg0", ".mgg1", ".mggl",
    ".mflac", ".mflac0", ".mmp4",
    ".qmcflac", ".qmcogg", ".qmc0", ".qmc2", ".qmc3", ".qmc4", ".qmc6", ".qmc8",
    ".bkcmp3", ".bkcm4a", ".bkcflac", ".bkcwav", ".bkcape", ".bkcogg", ".bkcwma",
    ".tkm",
    ".mp3", ".flac", ".wav", ".m4a", ".ogg", ".opus",
}

STATUS_WAITING = "等待"
STATUS_DECRYPT = "解密中"
STATUS_DOWNLOAD = "下载模型"
STATUS_CONVERT = "转换中"
STATUS_SEPARATE = "分离中"
STATUS_EXPORT = "导出中"
STATUS_DONE = "完成"
STATUS_FAILED = "失败"
STATUS_NEED_KEY = "需要密钥"
STATUS_STOPPED = "已停止"

RUNNING_STATUSES = (
    STATUS_DECRYPT,
    STATUS_DOWNLOAD,
    STATUS_CONVERT,
    STATUS_SEPARATE,
    STATUS_EXPORT,
)
RETRYABLE_STATUSES = (STATUS_FAILED, STATUS_NEED_KEY, STATUS_STOPPED)

STAGE_STATUS = {
    STAGE_DECRYPT: STATUS_DECRYPT,
    "download": STATUS_DOWNLOAD,
    STAGE_CONVERT: STATUS_CONVERT,
    STAGE_SEPARATE: STATUS_SEPARATE,
    STAGE_EXPORT: STATUS_EXPORT,
}

COL_NAME, COL_FORMAT, COL_STATUS, COL_PROGRESS, COL_ACTION = range(5)
_MB = 1024 * 1024


@dataclass
class RowState:
    job_id: int
    path: Path
    status: str = STATUS_WAITING
    ekey: str | None = None
    outputs: list[Path] = field(default_factory=list)
    error: str = ""
    row: int = -1
    started_at: float | None = None


class PipelineWorker(QThread):
    """顺序处理队列，解密与分离都在这个线程里完成。"""

    job_started = Signal(int)
    job_stage = Signal(int, str, str)
    job_progress = Signal(int, int)      # 0-100；-1 表示忙碌（不确定进度）
    job_done = Signal(int, object, str, float, object)
    job_failed = Signal(int, str, bool)
    queue_finished = Signal()

    def __init__(
        self,
        jobs: list[tuple[int, Path, str | None]],
        base_options: PipelineOptions,
        parent=None,
    ):
        super().__init__(parent)
        self._jobs = jobs
        self._base = base_options
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:  # noqa: D401
        base = self._base
        for job_id, path, ekey in self._jobs:
            if self._stop_requested:
                break
            self.job_started.emit(job_id)

            def on_progress(stage: str, fraction, message: str, job_id: int = job_id) -> None:
                self.job_stage.emit(job_id, stage, message)
                if stage == STAGE_DECRYPT and fraction is not None:
                    self.job_progress.emit(job_id, int(fraction * 100))
                elif stage == STAGE_EXPORT and fraction is not None:
                    self.job_progress.emit(job_id, int(fraction * 100))
                else:
                    self.job_progress.emit(job_id, -1)

            options = PipelineOptions(
                quality=base.quality,
                keep_vocals=base.keep_vocals,
                output_format=base.output_format,
                bitrate=base.bitrate,
                output_dir=base.output_dir,
                model_dir=base.model_dir,
                threads=base.threads,
                ekey=ekey,
                ekey_db=base.ekey_db,
            )
            try:
                result = process_file(path, options, on_progress)
            except PipelineError as exc:
                self.job_failed.emit(job_id, str(exc), exc.needs_key)
            except MissingEkeyError as exc:
                self.job_failed.emit(job_id, str(exc), True)
            except SeparatorError as exc:
                self.job_failed.emit(job_id, str(exc), False)
            except Exception as exc:  # 兜底：任何异常都要让界面看到
                self.job_failed.emit(job_id, f"{type(exc).__name__}: {exc}", False)
            else:
                outputs = [result.instrumental]
                if result.vocals is not None:
                    outputs.append(result.vocals)
                self.job_done.emit(
                    job_id,
                    [str(p) for p in outputs],
                    result.backend,
                    result.elapsed,
                    result.warnings,
                )
        self.queue_finished.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("伴生 StemMate — 本地伴奏分离")
        self.resize(1000, 620)
        self.setAcceptDrops(True)

        self._rows: dict[int, RowState] = {}
        self._next_job_id = 1
        self._worker: PipelineWorker | None = None
        self._workdir: Path | None = None
        self._last_output_dir: Path | None = None
        self._stop_requested = False
        self._batch_total = 0
        self._batch_index = 0
        self._settings: AppSettings = load_settings()
        self._ekey_db: Path | None = (
            Path(self._settings.ekey_db) if self._settings.ekey_db else None
        )
        self._model_dir: Path = (
            Path(self._settings.model_dir) if self._settings.model_dir else default_model_dir()
        )

        self._build_ui()
        self._apply_settings(self._settings)

        # 每 500ms 刷新一次「分离中 12s」这类已用时间，不阻塞主线程。
        self._ticker = QTimer(self)
        self._ticker.setInterval(500)
        self._ticker.timeout.connect(self._tick_elapsed)
        self._ticker.start()
        self.statusBar().showMessage(
            "拖入 .mgg / .mflac / .mp3 等文件，点击“开始处理”即可生成伴奏。"
        )

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        toolbar = self.addToolBar("主工具栏")
        toolbar.setMovable(False)

        add_action = QAction("添加文件", self)
        add_action.setShortcut(QKeySequence("Ctrl+O"))
        add_action.triggered.connect(self._choose_files)
        toolbar.addAction(add_action)

        self._start_action = QAction("开始处理", self)
        self._start_action.setShortcut(QKeySequence("Ctrl+Return"))
        self._start_action.triggered.connect(self._start_queue)
        toolbar.addAction(self._start_action)

        self._stop_action = QAction("停止", self)
        self._stop_action.setEnabled(False)
        self._stop_action.setToolTip("当前文件处理结束后停止，剩余文件标记为已停止")
        self._stop_action.triggered.connect(self._stop_queue)
        toolbar.addAction(self._stop_action)

        self._retry_action = QAction("重试失败项", self)
        self._retry_action.setToolTip("把失败/缺密钥/已停止的文件重新放回队列")
        self._retry_action.triggered.connect(self._retry_failed)
        toolbar.addAction(self._retry_action)

        toolbar.addSeparator()

        toolbar.addWidget(QLabel(" 质量："))
        self._quality_box = QComboBox(self)
        self._quality_box.addItem(QUALITY_LABELS[QUALITY_FAST], QUALITY_FAST)
        self._quality_box.addItem(QUALITY_LABELS[QUALITY_HIGH], QUALITY_HIGH)
        self._quality_box.setToolTip("快速档优先 Spleeter；未安装时使用 htdemucs")
        self._quality_box.currentIndexChanged.connect(lambda _i: self._refresh_model_hint())
        toolbar.addWidget(self._quality_box)

        self._vocals_box = QCheckBox("保留人声轨")
        toolbar.addWidget(self._vocals_box)

        toolbar.addSeparator()

        open_output_action = QAction("打开输出目录", self)
        open_output_action.triggered.connect(self._open_output_dir)
        toolbar.addAction(open_output_action)

        ekey_action = QAction("导入 EKey…", self)
        ekey_action.setToolTip("为 musicex / STag 文件手动提供 ekey")
        ekey_action.triggered.connect(self._import_ekey_for_selection)
        toolbar.addAction(ekey_action)

        db_action = QAction("选择密钥库…", self)
        db_action.setToolTip("QQ 音乐 player_process_db 等 SQLite 密钥库")
        db_action.triggered.connect(self._choose_ekey_db)
        toolbar.addAction(db_action)

        harvest_action = QAction("提取密钥…", self)
        harvest_action.setToolTip("扫描本地 QQ 音乐数据，自动找出能解开队列文件的 ekey")
        harvest_action.triggered.connect(self._open_harvest)
        toolbar.addAction(harvest_action)

        toolbar.addSeparator()

        clear_action = QAction("清空队列", self)
        clear_action.triggered.connect(self._clear_rows)
        toolbar.addAction(clear_action)

        toolbar.addSeparator()
        settings_action = QAction("设置…", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._open_settings)
        toolbar.addAction(settings_action)

        central = QWidget(self)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)

        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(["文件名", "格式", "状态", "进度", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_PROGRESS, QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setColumnWidth(COL_FORMAT, 90)
        self.table.setColumnWidth(COL_STATUS, 100)
        self.table.setColumnWidth(COL_PROGRESS, 180)
        self.table.setColumnWidth(COL_ACTION, 80)
        layout.addWidget(self.table)
        self.setCentralWidget(central)

        self._hint = QLabel(self)
        self.statusBar().addPermanentWidget(self._hint)

    def _refresh_model_hint(self) -> None:
        quality = self._quality_box.currentData()
        backend = backend_for(quality)
        if quality == QUALITY_HIGH:
            text = "高质量：htdemucs_ft"
        elif backend == "spleeter":
            text = "快速：Spleeter 2-stem"
        else:
            text = "快速：htdemucs（未安装 Spleeter）"
        if self._settings.output_format == "mp3":
            text += f" · 输出 MP3 {self._settings.bitrate}k"
        else:
            text += " · 输出 WAV"
        self._hint.setText(text)

    def _apply_settings(self, settings: AppSettings) -> None:
        """把设置应用到工具栏控件与内部路径。"""
        self._settings = settings.normalized()
        index = self._quality_box.findData(self._settings.quality)
        if index >= 0:
            self._quality_box.setCurrentIndex(index)
        self._vocals_box.setChecked(self._settings.keep_vocals)
        self._ekey_db = Path(self._settings.ekey_db) if self._settings.ekey_db else None
        self._model_dir = (
            Path(self._settings.model_dir) if self._settings.model_dir else default_model_dir()
        )
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self._settings.dark_mode)
        self._refresh_model_hint()

    def _persist_toolbar_settings(self) -> None:
        """把工具栏上的快捷选择同步进设置并保存。"""
        self._settings = replace(
            self._settings,
            quality=self._quality_box.currentData(),
            keep_vocals=self._vocals_box.isChecked(),
        ).normalized()
        try:
            save_settings(self._settings)
        except OSError as exc:
            self.statusBar().showMessage(f"设置保存失败：{exc}", 6000)

    def _open_settings(self) -> None:
        self._persist_toolbar_settings()
        dialog = SettingsDialog(self._settings, self)
        if dialog.exec():
            self._apply_settings(dialog.result_settings())
            try:
                save_settings(self._settings)
            except OSError as exc:
                QMessageBox.warning(self, "保存失败", f"无法写入设置文件：{exc}")
            self.statusBar().showMessage("设置已保存。", 5000)

    # -------------------------------------------------------------- 导入文件

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        self._add_paths(paths)

    def _choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择音频文件",
            str(Path.home() / "Music"),
            "音频 (*.mgg *.mgg0 *.mgg1 *.mflac *.mflac0 *.qmcflac *.qmcogg *.qmc0 *.qmc3 "
            "*.tkm *.mp3 *.flac *.wav *.m4a *.ogg *.opus);;所有文件 (*)",
        )
        self._add_paths([Path(f) for f in files])

    def _add_paths(self, paths: list[Path]) -> None:
        existing = {row.path.resolve() for row in self._rows.values() if row.path.exists()}
        added = 0
        for path in paths:
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix and suffix not in SUPPORTED_SUFFIXES:
                self.statusBar().showMessage(f"已忽略不支持的文件：{path.name}", 5000)
                continue
            if path.resolve() in existing:
                continue
            existing.add(path.resolve())
            self._append_row(path)
            added += 1
        if added:
            self.statusBar().showMessage(f"已添加 {added} 个文件。", 4000)

    def _append_row(self, path: Path) -> None:
        job_id = self._next_job_id
        self._next_job_id += 1

        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(path.name)
        name_item.setToolTip(str(path))
        name_item.setData(Qt.UserRole, job_id)
        self.table.setItem(row, COL_NAME, name_item)

        fmt = path.suffix.lstrip(".").upper() or "未知"
        self.table.setItem(row, COL_FORMAT, QTableWidgetItem(fmt))
        self.table.setItem(row, COL_STATUS, QTableWidgetItem(STATUS_WAITING))

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(True)
        bar.setAlignment(Qt.AlignCenter)
        self.table.setCellWidget(row, COL_PROGRESS, bar)

        remove_btn = QPushButton("移除")
        remove_btn.clicked.connect(lambda _=False, jid=job_id: self._remove_job(jid))
        self.table.setCellWidget(row, COL_ACTION, remove_btn)

        self._rows[job_id] = RowState(job_id=job_id, path=path, row=row)

    # -------------------------------------------------------------- 队列操作

    def _remove_job(self, job_id: int) -> None:
        state = self._rows.get(job_id)
        if state is None:
            return
        if state.status in RUNNING_STATUSES:
            self.statusBar().showMessage("正在处理的文件不能移除。", 4000)
            return
        self.table.removeRow(state.row)
        del self._rows[job_id]
        self._refresh_row_indices()

    def _refresh_row_indices(self) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_NAME)
            if item is None:
                continue
            job_id = item.data(Qt.UserRole)
            if job_id in self._rows:
                self._rows[job_id].row = row

    def _clear_rows(self) -> None:
        if self._worker and self._worker.isRunning():
            self.statusBar().showMessage("请先停止当前任务。", 4000)
            return
        self.table.setRowCount(0)
        self._rows.clear()

    def _start_queue(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        pending = [
            (state.job_id, state.path, state.ekey)
            for state in self._rows.values()
            if state.status in (STATUS_WAITING, *RETRYABLE_STATUSES)
        ]
        if not pending:
            self.statusBar().showMessage("没有等待处理的文件。", 4000)
            return

        quality = self._quality_box.currentData()
        keep_vocals = self._vocals_box.isChecked()
        self._persist_toolbar_settings()
        missing = missing_model_files(quality, self._model_dir, keep_vocals=keep_vocals)
        if missing:
            dialog = ModelDownloadDialog(quality, self._model_dir, keep_vocals, self)
            if not dialog.exec():
                return
            if missing_model_files(quality, self._model_dir, keep_vocals=keep_vocals):
                self.statusBar().showMessage("模型未就绪，已取消本次处理。", 6000)
                return

        output_dir = Path(self._settings.output_dir) if self._settings.output_dir else None
        options = PipelineOptions(
            quality=self._settings.quality,
            keep_vocals=self._settings.keep_vocals,
            output_format=self._settings.output_format,
            bitrate=self._settings.bitrate,
            output_dir=output_dir,
            model_dir=self._model_dir,
            threads=self._settings.threads,
            ekey_db=self._ekey_db,
        )
        for job_id, _path, _ekey in pending:
            state = self._rows.get(job_id)
            if state is None:
                continue
            state.started_at = None
            self._set_status(state, STATUS_WAITING)
            self._set_progress(state, 0)

        self._worker = PipelineWorker(pending, options, self)
        self._worker.job_started.connect(self._on_job_started)
        self._worker.job_stage.connect(self._on_job_stage)
        self._worker.job_progress.connect(self._on_job_progress)
        self._worker.job_done.connect(self._on_job_done)
        self._worker.job_failed.connect(self._on_job_failed)
        self._worker.queue_finished.connect(self._on_queue_finished)
        self._worker.start()

        self._start_action.setEnabled(False)
        self._stop_action.setEnabled(True)
        self._ticker.start()
        self._stop_requested = False
        self._batch_total = len(pending)
        self._batch_index = 0
        self.statusBar().showMessage(f"开始处理 {len(pending)} 个文件…")

    def _stop_queue(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
            self._stop_requested = True
            self._stop_action.setEnabled(False)
            self.statusBar().showMessage(
                "正在停止：当前文件完成后暂停，剩余文件将标记为“已停止”。", 6000
            )

    def _retry_failed(self) -> None:
        if self._worker and self._worker.isRunning():
            self.statusBar().showMessage("请先停止当前任务。", 4000)
            return
        count = 0
        for state in self._rows.values():
            if state.status in RETRYABLE_STATUSES:
                state.started_at = None
                self._set_status(state, STATUS_WAITING)
                self._set_progress(state, 0)
                count += 1
        if count:
            self.statusBar().showMessage(f"已将 {count} 个文件重新加入队列。", 5000)
        else:
            self.statusBar().showMessage("没有可重试的文件。", 4000)

    # -------------------------------------------------------------- 线程回调

    def _on_job_started(self, job_id: int) -> None:
        state = self._rows.get(job_id)
        if state is None:
            return
        self._batch_index += 1
        state.started_at = time.time()
        self._set_status(state, STATUS_DECRYPT)
        self._set_progress(state, -1)
        prefix = f"[{self._batch_index}/{self._batch_total}] " if self._batch_total else ""
        self.statusBar().showMessage(f"{prefix}正在处理：{state.path.name}")

    def _on_job_stage(self, job_id: int, stage: str, message: str) -> None:
        state = self._rows.get(job_id)
        if state is None:
            return
        status = STAGE_STATUS.get(stage)
        if status:
            self._set_status(state, status)
        if message:
            self.statusBar().showMessage(f"{state.path.name}：{message}")

    def _on_job_progress(self, job_id: int, percent: int) -> None:
        state = self._rows.get(job_id)
        if state is not None:
            self._set_progress(state, percent)

    def _on_job_done(self, job_id: int, outputs: list[str], backend: str, elapsed: float, warnings: list[str]) -> None:
        state = self._rows.get(job_id)
        if state is None:
            return
        state.outputs = [Path(p) for p in outputs]
        state.started_at = None
        self._last_output_dir = state.outputs[0].parent if state.outputs else None
        self._set_status(state, STATUS_DONE)
        self._set_progress(state, 100)

        item = self.table.item(state.row, COL_NAME)
        if item is not None:
            lines = [f"源文件：{state.path}", f"后端：{backend}，耗时 {elapsed:.1f}s"]
            lines += [f"输出：{p}" for p in state.outputs]
            lines += [f"提示：{note}" for note in warnings]
            item.setToolTip("\n".join(lines))

        self.statusBar().showMessage(
            f"{state.path.name} 处理完成（{backend}，{elapsed:.1f}s）", 8000
        )

    def _on_job_failed(self, job_id: int, message: str, needs_key: bool) -> None:
        state = self._rows.get(job_id)
        if state is None:
            return
        state.error = message
        state.started_at = None
        self._set_status(state, STATUS_NEED_KEY if needs_key else STATUS_FAILED)
        self._set_progress(state, 0)
        item = self.table.item(state.row, COL_NAME)
        if item is not None:
            item.setToolTip(message)
        self.statusBar().showMessage(message, 8000)

        if needs_key:
            self._prompt_missing_key(state, message)

    def _on_queue_finished(self) -> None:
        self._start_action.setEnabled(True)
        self._stop_action.setEnabled(False)

        stopped = 0
        if self._stop_requested:
            for state in self._rows.values():
                if state.status == STATUS_WAITING:
                    self._set_status(state, STATUS_STOPPED)
                    self._set_progress(state, 0)
                    stopped += 1
        self._stop_requested = False

        done = sum(1 for row in self._rows.values() if row.status == STATUS_DONE)
        failed = sum(
            1 for row in self._rows.values()
            if row.status in (STATUS_FAILED, STATUS_NEED_KEY)
        )
        if stopped:
            self.statusBar().showMessage(
                f"已停止：完成 {done} 个，失败 {failed} 个，中断 {stopped} 个。"
            )
        else:
            self.statusBar().showMessage(f"队列结束：完成 {done} 个，失败 {failed} 个。")

    # -------------------------------------------------------------- 界面辅助

    def _set_status(self, state: RowState, text: str) -> None:
        state.status = text
        self._render_status(state)

    def _render_status(self, state: RowState) -> None:
        item = self.table.item(state.row, COL_STATUS)
        if item is None:
            return
        if state.status in RUNNING_STATUSES and state.started_at:
            elapsed = int(time.time() - state.started_at)
            item.setText(f"{state.status} {elapsed}s")
        else:
            item.setText(state.status)

    def _tick_elapsed(self) -> None:
        for state in self._rows.values():
            if state.status in RUNNING_STATUSES:
                self._render_status(state)

    def _set_progress(self, state: RowState, percent: int) -> None:
        widget = self.table.cellWidget(state.row, COL_PROGRESS)
        if not isinstance(widget, QProgressBar):
            return
        if percent < 0:
            widget.setRange(0, 0)  # 忙碌状态（分离耗时不确定）
        else:
            widget.setRange(0, 100)
            widget.setValue(max(0, min(percent, 100)))

    def _open_output_dir(self) -> None:
        if self._last_output_dir and self._last_output_dir.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_output_dir)))
            return
        for state in self._rows.values():
            if state.outputs:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(state.outputs[0].parent)))
                return
        self.statusBar().showMessage("还没有输出文件。", 4000)

    def _open_harvest(self) -> None:
        targets = [row.path for row in self._rows.values() if row.status == STATUS_NEED_KEY]
        if not targets:
            targets = [row.path for row in self._rows.values()]
        if not targets:
            QMessageBox.information(
                self, "没有目标文件", "请先把需要密钥的 musicex / STag 文件加入队列。"
            )
            return

        initial = [str(self._ekey_db)] if self._ekey_db else []
        dialog = KeyHarvestDialog(targets, self, initial_paths=initial)
        dialog.keys_stored.connect(self._on_keys_stored)
        dialog.exec()

    def _on_keys_stored(self, count: int) -> None:
        requeued = 0
        for state in self._rows.values():
            if state.status == STATUS_NEED_KEY:
                state.started_at = None
                self._set_status(state, STATUS_WAITING)
                self._set_progress(state, 0)
                requeued += 1
        self.statusBar().showMessage(
            f"已导入 {count} 个密钥；{requeued} 个文件已重新排队，可点击“开始处理”。", 8000
        )

    def _choose_ekey_db(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 QQ 音乐密钥库",
            str(Path.home()),
            "SQLite 数据库 (*.db *.sqlite *.sqlite3);;所有文件 (*)",
        )
        if not path:
            return
        self._ekey_db = Path(path)
        self._settings = replace(self._settings, ekey_db=str(self._ekey_db)).normalized()
        try:
            save_settings(self._settings)
        except OSError:
            pass
        self.statusBar().showMessage(f"已选择密钥库：{path}", 6000)

    def _import_ekey_for_selection(self) -> None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        target: RowState | None = None
        if rows:
            job_id = self.table.item(rows[0].row(), COL_NAME).data(Qt.UserRole)
            target = self._rows.get(job_id)
        if target is None:
            target = next(
                (r for r in self._rows.values() if r.status == STATUS_NEED_KEY), None
            )
        if target is None:
            self.statusBar().showMessage("请先选择一个需要 ekey 的文件。", 5000)
            return
        self._ask_ekey_file(target)

    def _prompt_missing_key(self, state: RowState, message: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("需要 EKey")
        box.setText("该文件不内嵌密钥，无法离线解密。")
        box.setInformativeText(message)
        import_btn = box.addButton("导入 EKey 文件…", QMessageBox.AcceptRole)
        db_btn = box.addButton("选择密钥库…", QMessageBox.ActionRole)
        box.addButton("跳过", QMessageBox.RejectRole)
        box.exec()

        if box.clickedButton() is import_btn:
            self._ask_ekey_file(state)
        elif box.clickedButton() is db_btn:
            self._choose_ekey_db()

    def _ask_ekey_file(self, state: RowState) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择包含 EKey 的文本文件",
            str(state.path.parent),
            "文本文件 (*.txt *.ekey);;所有文件 (*)",
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
        except OSError as exc:
            QMessageBox.critical(self, "读取失败", f"无法读取 EKey 文件：{exc}")
            return
        if not text:
            QMessageBox.warning(self, "内容为空", "该文件没有可用的 ekey 文本。")
            return
        state.ekey = text
        self._set_status(state, STATUS_WAITING)
        self.statusBar().showMessage(
            f"已为 {state.path.name} 载入 ekey，可点击“开始处理”重试。", 6000
        )
