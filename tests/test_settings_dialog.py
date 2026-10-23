# -*- coding: utf-8 -*-
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.settings import AppSettings  # noqa: E402
from ui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def test_dialog_reflects_existing_settings(app):
    dialog = SettingsDialog(
        AppSettings(output_format="wav", bitrate=256, keep_vocals=True, threads=2)
    )
    assert dialog._format_box.currentData() == "wav"
    assert dialog._bitrate_box.currentData() == 256
    assert dialog._bitrate_box.isEnabled() is False
    assert dialog._vocals_box.isChecked() is True
    assert dialog._threads_box.value() == 2
    assert dialog._size_label.text()

    dialog.close()


def test_dialog_result_settings(app):
    dialog = SettingsDialog(AppSettings(output_format="wav"))
    dialog._format_box.setCurrentIndex(dialog._format_box.findData("mp3"))
    assert dialog._bitrate_box.isEnabled() is True
    dialog._bitrate_box.setCurrentIndex(dialog._bitrate_box.findData(192))
    dialog._vocals_box.setChecked(True)
    dialog._threads_box.setValue(5)
    dialog._output_dir_edit.setText("  ")
    dialog._model_dir_edit.setText("")

    result = dialog.result_settings()
    assert result.output_format == "mp3"
    assert result.bitrate == 192
    assert result.keep_vocals is True
    assert result.threads == 5
    assert result.output_dir is None
    assert result.model_dir is None

    dialog.close()


def test_dialog_preserves_quality(app):
    dialog = SettingsDialog(AppSettings(quality="high"))
    assert dialog.result_settings().quality == "high"
    dialog.close()


def test_dialog_dark_mode(app):
    dialog = SettingsDialog(AppSettings(dark_mode=True))
    assert dialog._dark_box.isChecked() is True
    dialog._dark_box.setChecked(False)
    assert dialog.result_settings().dark_mode is False
    dialog.close()
