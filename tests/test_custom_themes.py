"""Guardar, editar y cancelar paletas personales sin alterar los seis temas base."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QDialog

from qt_helpers import drain_qt_events

from desktop import MainWindow
from library import Library
from test_arandu import PDF
from theme_editor import ThemeEditorDialog
from themes import (
    CUSTOM_THEME_FILE, EDITABLE_COLORS, THEMES, contrast_issues, current_theme,
    load_custom_themes, make_custom_theme, normalize_color, save_custom_themes,
)


def custom_theme(theme_id="custom-reader", name="Mi lectura"):
    base = THEMES["grafito"]
    colors = {key: getattr(base, key) for key, _ in EDITABLE_COLORS}
    colors["accent"] = "#d4b890"
    return make_custom_theme(base, theme_id, name, colors)


class CustomStorageTests(unittest.TestCase):
    def test_round_trip_preserves_every_color_and_leaves_presets_intact(self):
        original = {key: asdict(value) for key, value in THEMES.items()}
        theme = custom_theme()
        with tempfile.TemporaryDirectory() as folder:
            save_custom_themes(folder, {theme.id: theme})
            self.assertEqual(load_custom_themes(folder), {theme.id: theme})
        self.assertEqual(original, {key: asdict(value) for key, value in THEMES.items()})
        self.assertEqual(contrast_issues(theme), [])

    def test_duplicate_name_does_not_overwrite_the_saved_file(self):
        first = custom_theme()
        second = custom_theme("custom-other")
        with tempfile.TemporaryDirectory() as folder:
            save_custom_themes(folder, {first.id: first})
            path = Path(folder) / CUSTOM_THEME_FILE
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                save_custom_themes(folder, {first.id: first, second.id: second})
            self.assertEqual(path.read_bytes(), before)

    def test_hex_validation_blocks_stylesheet_injection_and_malformed_colors(self):
        self.assertEqual(normalize_color(" AABBCC "), "#aabbcc")
        for value in ("#abc", "red", "#000000; color:red", None, "#ffffffff"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_color(value)

    def test_corrupted_theme_file_is_reported_and_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / CUSTOM_THEME_FILE
            path.write_text("not json")
            with self.assertRaises(ValueError):
                load_custom_themes(folder)
            self.assertEqual(path.read_text(), "not json")


class CustomThemeWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data")
        book = self.root / "Una lectura.pdf"
        book.write_bytes(PDF)
        self.library.add(book)
        self.window = MainWindow(self.library)
        self.window.set_theme("grafito")
        self.window.show()
        self.app.processEvents()
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if not dialog.closed:
                dialog.reject()
            dialog.deleteLater()
        self.window.close()
        self.window.deleteLater()
        drain_qt_events(self.app)
        self.temp.cleanup()

    def open_editor(self):
        dialog = ThemeEditorDialog(self.window.theme, self.window.available_themes, self.window.save_custom_theme, self.window)
        dialog.previewRequested.connect(lambda theme: self.window.set_theme(theme, persist=False))
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def screenshot(self, name, widget):
        target = os.environ.get("ARANDU_TEST_ARTIFACTS")
        if target:
            folder = Path(target)
            folder.mkdir(parents=True, exist_ok=True)
            self.app.processEvents()
            self.assertTrue(widget.grab().save(str(folder / name)))

    def test_live_preview_and_cancel_restore_the_original_without_saving(self):
        before = (self.library.data_dir / "window.json").read_bytes()
        dialog = self.open_editor()
        dialog.color_fields["accent"].set_color("#d4b890")
        dialog.refresh_preview()
        self.assertEqual(self.window.theme.accent, "#d4b890")
        self.assertEqual(current_theme(), self.window.theme)
        self.assertEqual(self.window.brand.theme, self.window.theme)
        dialog.reject()
        self.app.processEvents()
        self.assertEqual(self.window.theme, THEMES["grafito"])
        self.assertEqual((self.library.data_dir / "window.json").read_bytes(), before)
        self.assertFalse((self.library.data_dir / CUSTOM_THEME_FILE).exists())

    def test_save_adds_theme_to_selector_and_restores_it_after_restart(self):
        dialog = self.open_editor()
        dialog.name_edit.setText("Mi lectura")
        dialog.color_fields["accent"].set_color("#d4b890")
        dialog.refresh_preview()
        dialog.resize(610, 740)
        self.screenshot("theme-editor.png", dialog)
        dialog.save()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        saved = dialog.saved_theme
        self.assertIsNotNone(saved)
        self.assertEqual(self.window.theme, saved)
        self.assertGreaterEqual(self.window.theme_combo.findData(saved.id), 6)
        self.assertTrue(self.window.theme_actions[saved.id].isChecked())
        self.assertEqual(THEMES["grafito"].accent, "#b7bec8")
        self.screenshot("custom-theme-library.png", self.window)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.window = MainWindow(self.library)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.window.theme, saved)
        self.assertEqual(current_theme(), saved)
        self.assertEqual(self.window.brand.theme, saved)
        self.assertEqual(self.window.theme_combo.currentText(), "Mi lectura")

    def test_editing_a_saved_theme_updates_its_colors_without_a_duplicate(self):
        first = custom_theme()
        self.window.save_custom_theme(first)
        dialog = self.open_editor()
        dialog.name_edit.setText("Lectura nocturna")
        dialog.color_fields["accent"].set_color("#d8cbab")
        dialog.save()
        self.assertEqual(dialog.saved_theme.id, first.id)
        self.assertEqual(len(self.window.custom_themes), 1)
        self.assertEqual(self.window.theme.accent, "#d8cbab")
        self.assertEqual(self.window.brand.theme.accent, "#d8cbab")
        self.assertEqual(self.window.theme_actions[first.id].text(), "Lectura nocturna")
        self.assertEqual(load_custom_themes(self.library.data_dir)[first.id], dialog.saved_theme)

    def test_invalid_color_and_low_contrast_do_not_apply_or_save(self):
        dialog = self.open_editor()
        last_valid = self.window.theme
        dialog.color_fields["text"].set_color(last_valid.background)
        dialog.refresh_preview()
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertIn("4,5:1", dialog.validation.text())
        self.assertEqual(self.window.theme, last_valid)
        dialog.preview_check.setChecked(False)
        self.assertEqual(self.window.theme, THEMES["grafito"])
        dialog.color_fields["text"].hex_edit.setText("#xyz")
        dialog.save()
        self.assertIsNone(dialog.saved_theme)
        self.assertFalse((self.library.data_dir / CUSTOM_THEME_FILE).exists())
        dialog.reset_colors()
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.resize(480, 480)
        self.screenshot("theme-editor-compact.png", dialog)
        self.assertLessEqual(dialog.save_button.mapTo(dialog, dialog.save_button.rect().bottomRight()).y(), dialog.height())

    def test_write_failure_keeps_the_editor_open_and_cancel_reverts_preview(self):
        dialog = self.open_editor()
        with patch.object(dialog, "save_callback", side_effect=PermissionError("Carpeta de solo lectura")):
            dialog.save()
        self.assertTrue(dialog.isVisible())
        self.assertIn("No se pudo guardar", dialog.validation.text())
        self.assertIsNone(dialog.saved_theme)
        dialog.reject()
        self.assertEqual(self.window.theme, THEMES["grafito"])

    def test_appearance_action_opens_the_native_editor(self):
        captured = []

        def close_editor():
            dialog = self.app.activeModalWidget()
            captured.append(isinstance(dialog, ThemeEditorDialog))
            if dialog:
                dialog.reject()

        QTimer.singleShot(0, close_editor)
        self.window.customize_theme_action.trigger()
        self.assertEqual(captured, [True])
        self.assertEqual(self.window.theme, THEMES["grafito"])

    def test_combo_customization_entry_opens_editor_and_keeps_selection(self):
        with patch.object(self.window, "customize_theme") as editor:
            self.window.theme_combo.setCurrentIndex(self.window.theme_combo.findData("__customize__"))
            self.app.processEvents()
            editor.assert_called_once()
        self.assertEqual(self.window.theme_combo.currentData(), "grafito")

    def test_closing_the_app_cancels_an_unsaved_preview_before_writing_preferences(self):
        def close_app():
            dialog = self.app.activeModalWidget()
            dialog.color_fields["accent"].set_color("#d4b890")
            dialog.refresh_preview()
            self.window.close()

        QTimer.singleShot(0, close_app)
        self.window.customize_theme_action.trigger()
        settings = json.loads((self.library.data_dir / "window.json").read_text())
        self.assertEqual(settings["theme"], "grafito")
        self.assertEqual(self.window.theme, THEMES["grafito"])
        self.assertFalse((self.library.data_dir / CUSTOM_THEME_FILE).exists())


if __name__ == "__main__":
    unittest.main()
