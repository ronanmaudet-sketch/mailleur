"""Tests du prototype reconstruit."""

def desktop_test_application():
    import sys
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    if sys.platform == "darwin" and app.platformName() == "offscreen":
        # Qt's headless macOS plugin requests a literal "Sans Serif" family.
        font = app.font()
        if font.family() == "Sans Serif" and "Helvetica Neue" in QFontDatabase.families():
            font.setFamily("Helvetica Neue")
            app.setFont(font)
    return app
