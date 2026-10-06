"""Reusable presentation widgets and formatting."""
from decimal import Decimal
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHeaderView, QLabel,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from app.utils.normalization import format_money

BLOCK_NAMES = ('Дистрибьютеры · СБКС и ВМК', 'ЛПУ · СБКС и ВМК',
               'Дистрибьютеры · ЭП, Latema, Novionta', 'ЛПУ · ЭП, Latema, Novionta')

STYLE = '''
QWidget { color: #183047; font-family: "Segoe UI", "DejaVu Sans"; font-size: 13px; }
QMainWindow, QStackedWidget, QScrollArea { background: #f4f7fc; }
QFrame#sidebar { background: #102a43; }
QLabel#brand { color: white; font-size: 24px; font-weight: 700; padding: 14px; }
QListWidget#navigation { background: #102a43; color: #cbd9e8; border: none; }
QListWidget#navigation::item { padding: 14px; margin: 3px 8px; border-radius: 7px; }
QListWidget#navigation::item:selected { background: #24598c; color: white; }
QLabel#title { font-size: 25px; font-weight: 700; }
QLabel#subtitle { color: #61778e; }
QLabel#error { color: #b12c39; }
QLabel#success { color: #16714b; }
QFrame#card, QGroupBox { background: white; border: 1px solid #dce5ee; border-radius: 9px; }
QGroupBox { margin-top: 16px; padding: 18px 12px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
QLabel#cardValue { font-size: 21px; font-weight: 700; }
QPushButton { background: white; border: 1px solid #cbd7e5; border-radius: 6px; padding: 9px 15px; }
QPushButton:hover { background: #eaf2fd; }
QPushButton#primary { background: #165dce; color: white; border: none; }
QPushButton#primary:hover { background: #124fab; }
QPushButton:disabled, QPushButton#primary:disabled { background: #e5ebf3; color: #899aad; border: 1px solid #dce5ee; }
QLineEdit, QSpinBox, QComboBox { background: white; border: 1px solid #cbd7e5; border-radius: 5px; padding: 8px; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #165dce; }
QTableWidget { background: white; alternate-background-color: #f6f9fd; border: 1px solid #dce5ee; gridline-color: #e7edf4; }
QHeaderView::section { background: #edf3fa; color: #34536f; padding: 9px; border: none; font-weight: 600; }
QCheckBox { padding: 4px; }
'''


def page_layout(page: QWidget, title: str, subtitle: str = '') -> QVBoxLayout:
    layout = QVBoxLayout(page)
    layout.setContentsMargins(24, 22, 24, 22)
    layout.setSpacing(16)
    label = QLabel(title)
    label.setObjectName('title')
    layout.addWidget(label)
    if subtitle:
        text = QLabel(subtitle)
        text.setWordWrap(True)
        text.setObjectName('subtitle')
        layout.addWidget(text)
    return layout


class ValueCard(QFrame):
    clicked = Signal()

    def __init__(self, title: str, value: str = '—', parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('card')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        self.caption = QLabel(title)
        self.caption.setObjectName('subtitle')
        self.value = QLabel(value)
        self.value.setObjectName('cardValue')
        self.value.setWordWrap(True)
        layout.addWidget(self.caption)
        layout.addWidget(self.value)

    def set_value(self, value: str) -> None:
        self.value.setText(value)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def table(headers: tuple[str, ...] | list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.setAlternatingRowColors(True)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


def fill_table(widget: QTableWidget, rows: list[list[object]]) -> None:
    widget.setRowCount(len(rows))
    for number, values in enumerate(rows):
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value) if value is not None else '—')
            item.setToolTip(item.text())
            widget.setItem(number, column, item)


def money(value: Decimal | None) -> str:
    return format_money(value) if value is not None else '—'


def number(value: Decimal | None) -> str:
    return format(value, 'f').replace('.', ',') if value is not None else '—'
