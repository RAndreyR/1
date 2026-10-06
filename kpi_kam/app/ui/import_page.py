from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget
from app.services.mapping_service import ImportSession
from app.ui.common import fill_table, page_layout, table


class ImportPage(QWidget):
    mapping_requested = Signal()
    calculate_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = page_layout(self, 'Проверка импорта', 'Ошибки структуры блокируют расчет. Предупреждения сохраняются в аудите.')
        self.summary = QLabel('Excel-файл еще не проверен.')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.issues = table(['Уровень','Проблема','Лист','Строка','Поле'])
        layout.addWidget(self.issues)
        actions = QHBoxLayout()
        self.mapping_button = QPushButton('Перейти к сопоставлению')
        self.mapping_button.clicked.connect(self.mapping_requested)
        self.calculate_button = QPushButton('Рассчитать')
        self.calculate_button.setObjectName('primary')
        self.calculate_button.clicked.connect(self.calculate_requested)
        self.calculate_button.setEnabled(False)
        actions.addWidget(self.mapping_button)
        actions.addWidget(self.calculate_button)
        actions.addStretch()
        layout.addLayout(actions)

    def set_session(self, session: ImportSession | None, can_calculate: bool = False) -> None:
        self.calculate_button.setEnabled(can_calculate)
        self.mapping_button.setEnabled(session is not None and not session.imported.errors)
        if session is None:
            self.summary.setText('Excel-файл еще не проверен.')
            fill_table(self.issues, [])
            return
        r, imported = session.validation, session.imported
        self.summary.setText(f'Листы: {", ".join(imported.detected_sheets)}\n'
            f'Строк: {imported.shipment_row_count}; прочитано: {r.shipment_count}; товаров прайса: {r.product_count}\n'
            f'Сопоставлено: {r.matched_row_count}; неизвестных товаров: {len(r.unmatched_products)}; '
            f'ожидают решения: {len(r.pending_mapping_rows)}\n'
            f'Некорректных периодов: {len(r.invalid_quarters)}; неизвестных типов клиента: {len(r.unknown_client_types)}; '
            f'без цены: {len(r.missing_price_rows)}; без порога: {len(r.missing_threshold_rows)}')
        fill_table(self.issues, [[('Ошибка' if i.severity == 'error' else 'Предупреждение'),i.message,i.sheet,i.source_row,i.field] for i in r.issues])
