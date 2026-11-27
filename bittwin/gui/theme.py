"""Light and dark themes for BitTwin (Fusion style + stylesheet)."""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPalette
from PyQt6.QtWidgets import QApplication

DARK = {
    "bg": "#14161b", "surface": "#1c1f26", "surface2": "#242833", "hover": "#2c3140",
    "border": "#2f3442", "text": "#e7e9ef", "muted": "#99a0b1", "faint": "#6b7284",
    "accent": "#4f8cff", "accent_hover": "#6b9fff", "accent_text": "#ffffff",
    "accent_soft": "#1f2d4a", "success": "#3ecf8e", "warn": "#f5a524", "danger": "#f0556a",
    "row_alt": "#191c22", "select": "#2a3b5e",
}
LIGHT = {
    "bg": "#f3f5f9", "surface": "#ffffff", "surface2": "#edf0f5", "hover": "#e4e8f0",
    "border": "#d9dee7", "text": "#1b1f2c", "muted": "#5c6579", "faint": "#8a92a5",
    "accent": "#2f6fec", "accent_hover": "#245fd6", "accent_text": "#ffffff",
    "accent_soft": "#e3ecfd", "success": "#0f9f63", "warn": "#b87100", "danger": "#d33550",
    "row_alt": "#f8f9fc", "select": "#d5e3fc",
}

_current = DARK


def colors() -> dict:
    return _current


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        pal = QGuiApplication.palette()
        return pal.color(QPalette.ColorRole.Window).lightness() < 128


def apply(app: QApplication, choice: str = "system") -> None:
    global _current
    dark = system_is_dark() if choice == "system" else choice == "dark"
    c = _current = DARK if dark else LIGHT
    app.setStyle("Fusion")

    pal = QPalette()
    role = QPalette.ColorRole
    pal.setColor(role.Window, QColor(c["bg"]))
    pal.setColor(role.WindowText, QColor(c["text"]))
    pal.setColor(role.Base, QColor(c["surface"]))
    pal.setColor(role.AlternateBase, QColor(c["row_alt"]))
    pal.setColor(role.Text, QColor(c["text"]))
    pal.setColor(role.Button, QColor(c["surface2"]))
    pal.setColor(role.ButtonText, QColor(c["text"]))
    pal.setColor(role.Highlight, QColor(c["select"]))
    pal.setColor(role.HighlightedText, QColor(c["text"]))
    pal.setColor(role.ToolTipBase, QColor(c["surface2"]))
    pal.setColor(role.ToolTipText, QColor(c["text"]))
    pal.setColor(role.PlaceholderText, QColor(c["faint"]))
    pal.setColor(role.Link, QColor(c["accent"]))
    for grp in (QPalette.ColorGroup.Disabled,):
        pal.setColor(grp, role.Text, QColor(c["faint"]))
        pal.setColor(grp, role.ButtonText, QColor(c["faint"]))
        pal.setColor(grp, role.WindowText, QColor(c["faint"]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(c))


ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "assets")


def stylesheet(c: dict) -> str:
    check = os.path.join(ASSETS, "check.svg")
    dot = os.path.join(ASSETS, "dot.svg")
    chevron = os.path.join(ASSETS, "chevron.svg")
    return f"""
* {{ outline: none; }}
QWidget {{ color: {c['text']}; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {c['bg']}; }}
QToolTip {{ background: {c['surface2']}; color: {c['text']}; border: 1px solid {c['border']};
           padding: 6px 8px; border-radius: 6px; }}

#Header {{ background: {c['surface']}; border-bottom: 1px solid {c['border']}; }}
#AppTitle {{ font-size: 15pt; font-weight: 700; letter-spacing: 0.3px; }}
#AppSubtitle {{ color: {c['muted']}; font-size: 9pt; }}

QFrame#Card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#Card[active="true"] {{ border: 1px solid {c['accent']}; }}
#CardSide {{ color: {c['accent']}; font-size: 8pt; font-weight: 700; letter-spacing: 1.5px; }}
#CardPath {{ font-size: 12.5pt; font-weight: 600; }}
#CardPathEmpty {{ font-size: 12.5pt; font-weight: 500; color: {c['faint']}; }}
#CardMeta {{ color: {c['muted']}; font-size: 9pt; }}
#Muted {{ color: {c['muted']}; }}
#ModeHint {{ color: {c['muted']}; font-size: 9.5pt; padding: 2px 4px; }}
#SectionTitle {{ font-size: 11pt; font-weight: 600; }}
#Badge {{ background: {c['accent_soft']}; color: {c['accent']}; border-radius: 9px;
          padding: 2px 9px; font-size: 8.5pt; font-weight: 600; }}

QPushButton {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 8px;
               padding: 7px 14px; font-weight: 500; }}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:disabled {{ color: {c['faint']}; background: {c['surface']}; }}
QPushButton#Primary {{ background: {c['accent']}; color: {c['accent_text']}; border: none;
                       font-weight: 600; padding: 9px 22px; }}
QPushButton#Primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#Primary:disabled {{ background: {c['surface2']}; color: {c['faint']}; }}
QPushButton#Danger {{ background: transparent; color: {c['danger']}; border: 1px solid {c['danger']}; }}
QPushButton#Danger:hover {{ background: {c['danger']}; color: white; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; }}
QPushButton#Ghost:hover {{ background: {c['hover']}; border: 1px solid {c['border']}; }}
QPushButton#Seg {{ border-radius: 0; padding: 7px 16px; border-right: none; }}
QPushButton#SegFirst {{ border-top-right-radius: 0; border-bottom-right-radius: 0; padding: 7px 16px;
                        border-right: none; }}
QPushButton#SegLast {{ border-top-left-radius: 0; border-bottom-left-radius: 0; padding: 7px 16px; }}
QPushButton#Seg:checked, QPushButton#SegFirst:checked, QPushButton#SegLast:checked {{
    background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; font-weight: 600; }}
QPushButton#Seg:checked:disabled, QPushButton#SegFirst:checked:disabled, QPushButton#SegLast:checked:disabled {{
    background: {c['surface2']}; color: {c['faint']}; border-color: {c['border']}; font-weight: 500; }}
QPushButton#Filter {{ background: transparent; border: 1px solid transparent; border-radius: 14px;
                      padding: 4px 12px; color: {c['muted']}; }}
QPushButton#Filter:hover {{ background: {c['hover']}; }}
QPushButton#Filter:checked {{ background: {c['accent_soft']}; color: {c['accent']}; font-weight: 600; }}
QPushButton::menu-indicator {{ subcontrol-position: right center; right: 8px; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px;
    padding: 6px 9px; selection-background-color: {c['select']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {c['accent']}; }}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 28px;
    border: none; background: transparent; }}
QComboBox::down-arrow {{ image: url({chevron}); width: 12px; height: 12px; }}
QComboBox::down-arrow:on {{ top: 1px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border']};
    selection-background-color: {c['select']}; padding: 4px; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px;
    border: 1.5px solid {c['faint']}; background: {c['surface']}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({check}); }}
QRadioButton::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({dot}); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {c['border']}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QAbstractSpinBox::up-arrow, QAbstractSpinBox::down-arrow {{ width: 0; height: 0; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 10px; margin-top: 14px; padding: 14px 12px 10px 12px;
             background: {c['surface']}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {c['muted']};
                    font-weight: 600; }}

QTableView {{ background: {c['surface']}; alternate-background-color: {c['row_alt']};
              border: 1px solid {c['border']}; border-radius: 12px; gridline-color: transparent;
              selection-background-color: {c['select']}; selection-color: {c['text']}; }}
QTableView::item {{ padding: 0 6px; border: none; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {c['surface']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 8px 6px; font-weight: 600; font-size: 9pt; }}
QTableCornerButton::section {{ background: {c['surface']}; border: none; }}

QProgressBar {{ background: {c['surface2']}; border: none; border-radius: 5px; height: 10px;
                text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}

QMenu {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 7px 26px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['hover']}; }}
QMenu::item:disabled {{ color: {c['faint']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 5px 6px; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

#Footer {{ background: {c['surface']}; border-top: 1px solid {c['border']}; }}
#StatValue {{ font-size: 11pt; font-weight: 700; }}
#StatLabel {{ color: {c['muted']}; font-size: 8.5pt; }}
#Empty {{ color: {c['muted']}; font-size: 11pt; }}
#EmptyTitle {{ font-size: 15pt; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 10px; background: {c['surface']}; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; color: {c['muted']}; border: none; }}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; font-weight: 600; }}
"""
