from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSettings


def test_gui_settings_do_not_write_a_preexisting_user_namespace(tmp_path: Path) -> None:
    user_settings = QSettings(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope,
        "BulkSeq", "BulkSeq Studio",
    )
    assert Path(user_settings.fileName()).resolve().is_relative_to(
        (tmp_path / "qt-settings").resolve()
    )
    user_settings.setValue("theme_mode", "preserve")
    user_settings.sync()
    assert user_settings.status() == QSettings.Status.NoError

    test_settings = QSettings()
    assert QCoreApplication.organizationName() == "BulkSeqLocalTests"
    assert test_settings.format() == QSettings.Format.IniFormat
    assert Path(test_settings.fileName()).resolve().is_relative_to(
        (tmp_path / "qt-settings").resolve()
    )
    assert test_settings.fileName() != user_settings.fileName()
    test_settings.setValue("theme_mode", "dark")
    test_settings.sync()
    assert test_settings.status() == QSettings.Status.NoError
    user_settings.sync()
    assert user_settings.value("theme_mode") == "preserve"
