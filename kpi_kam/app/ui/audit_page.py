from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget
from app.models.domain import CalculationResult, CATEGORIES, CLIENT_TYPES
from app.services.audit_service import AuditFilter, select_audit
from app.ui.common import fill_table, money, number, page_layout, table


class AuditPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.result: CalculationResult | None = None
        self.source_rows: frozenset[int] | None = None
        self.selection = None
        layout = page_layout(self, 'Аудит отгрузок', 'Цены показаны до и после округления. Каждая строка сохраняет номер из исходной книги.')
        filters = QGridLayout()
        self.quarter = QComboBox()
        self.quarter.addItem('Все кварталы', None)
        for q in range(1,5):
            self.quarter.addItem(f'Q{q}', q)
        self.quarter.addItem('Некорректный период', 0)
        self.client_type = QComboBox()
        self.client_type.addItem('Все типы клиента', None)
        for client in CLIENT_TYPES:
            self.client_type.addItem(client, client)
        self.client_type.addItem('Неизвестный тип', '')
        self.category = QComboBox()
        self.category.addItem('Все категории', None)
        for category in CATEGORIES:
            self.category.addItem(category, category)
        self.category.addItem('Несопоставленный товар', '')
        self.status = QComboBox()
        self.status.addItem('Все строки', None)
        self.status.addItem('Включенные', True)
        self.status.addItem('Исключенные', False)
        self.product = QLineEdit()
        self.product.setPlaceholderText('Поиск по товару')
        self.client = QLineEdit()
        self.client.setPlaceholderText('Поиск по клиенту')
        for index, widget in enumerate((self.quarter,self.client_type,self.category,self.status,self.product,self.client)):
            filters.addWidget(widget, index//3, index%3)
        layout.addLayout(filters)
        selection_bar = QHBoxLayout()
        self.scope = QLabel('Все строки расчета')
        self.clear_button = QPushButton('Сбросить фильтры')
        self.clear_button.clicked.connect(self.clear_filters)
        selection_bar.addWidget(self.scope, 1)
        selection_bar.addWidget(self.clear_button)
        layout.addLayout(selection_bar)
        self.grid = table(['Строка Excel','Квартал','Клиент','Тип клиента','Исходный товар','Канонический товар',
                           'Категория','Количество','Выручка','Цена исходная','Цена 1 знак','Порог исходный',
                           'Порог 1 знак','Дельта','Статус','Причина исключения','ЮЛ','Задача','Комментарий'])
        layout.addWidget(self.grid, 1)
        self.footer = QLabel('Нет расчета')
        layout.addWidget(self.footer)
        for combo in (self.quarter,self.client_type,self.category,self.status):
            combo.currentIndexChanged.connect(self.refresh)
        self.product.textChanged.connect(self.refresh)
        self.client.textChanged.connect(self.refresh)

    def set_result(self, result: CalculationResult) -> None:
        self.result = result
        self.clear_filters()

    def clear_filters(self) -> None:
        self.source_rows = None
        for combo in (self.quarter,self.client_type,self.category,self.status):
            combo.setCurrentIndex(0)
        self.product.clear()
        self.client.clear()
        self.scope.setText('Все строки расчета')
        self.refresh()

    def drill_down(self, rows: tuple[int, ...], title: str) -> None:
        self.clear_filters()
        self.source_rows = frozenset(rows)
        self.scope.setText(title)
        self.refresh()

    def refresh(self, *_args) -> None:
        if self.result is None:
            return
        filters = AuditFilter(self.source_rows,self.quarter.currentData(),self.client_type.currentData(),
                              self.category.currentData(),self.status.currentData(),self.product.text(),self.client.text())
        self.selection = select_audit(self.result, filters)
        fill_table(self.grid, [[a.shipment.source_row,f'Q{a.quarter}' if a.quarter else '—',a.shipment.client,
            a.client_type or a.shipment.client_type,a.shipment.product_raw,a.product.canonical_name if a.product else None,
            a.product.category if a.product else None,number(a.shipment.quantity),money(a.shipment.revenue),
            number(a.actual_price_raw),number(a.actual_price_rounded),number(a.threshold_raw),number(a.threshold_rounded),
            number(a.price_delta),'Включена' if a.eligible else 'Исключена',a.exclusion_reason,
            a.shipment.legal_entity,a.shipment.bitrix_task,a.shipment.comment] for a in self.selection.rows])
        self.footer.setText(f'Строк: {len(self.selection.rows)} · Включенная выручка: {money(self.selection.included_revenue)} · '
                            f'Исключенная выручка: {money(self.selection.excluded_revenue)}')
