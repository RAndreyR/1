from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QLabel, QPushButton, QWidget
from app.models.application import HistoryEntry
from app.ui.common import fill_table, money, page_layout, table


class HistoryPage(QWidget):
    open_requested = Signal(int)
    refresh_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.entries: tuple[HistoryEntry, ...] = ()
        layout = page_layout(self,'История расчетов','Двойной щелчок открывает сохраненный снимок с исходными планами, ставками и аудитом.')
        self.summary = QLabel('Нет сохраненных расчетов')
        layout.addWidget(self.summary)
        self.grid = table(['Дата','Сотрудник','Год','Источник','Q1 к выплате','Q2 к выплате','Q3 к выплате','Q4 к выплате','За год'])
        self.grid.cellDoubleClicked.connect(self._open)
        layout.addWidget(self.grid,1)
        refresh = QPushButton('Обновить историю')
        refresh.clicked.connect(self.refresh_requested)
        layout.addWidget(refresh)

    def set_entries(self, entries: tuple[HistoryEntry, ...]) -> None:
        self.entries = entries
        fill_table(self.grid, [[datetime.fromisoformat(e.calculated_at).astimezone().strftime('%d.%m.%Y %H:%M'),
            e.employee,e.year,Path(e.source_file).name,*(money(value) for value in e.quarter_payables),
            money(e.annual_payable)] for e in entries])
        self.summary.setText(f'Сохраненных расчетов: {len(entries)}')
        for row,entry in enumerate(entries):
            self.grid.item(row,0).setData(Qt.ItemDataRole.UserRole,entry.id)
            self.grid.item(row,3).setToolTip(entry.source_file)

    def _open(self, row: int, _column: int) -> None:
        self.open_requested.emit(self.entries[row].id)
