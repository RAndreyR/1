from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QWidget
from app.models.domain import CalculationResult, CATEGORIES, CLIENT_TYPES
from app.ui.common import BLOCK_NAMES, ValueCard, fill_table, money, page_layout, table
from app.utils.input_values import percent_text


class DashboardPage(QWidget):
    audit_requested = Signal(object, str)
    premium_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.result: CalculationResult | None = None
        layout = page_layout(self, 'Итоги KPI', 'Нажмите на факт или базу блока, чтобы увидеть исходные строки; на выплату — для деталей премии.')
        annual = QHBoxLayout()
        self.actual_card = ValueCard('Факт за год')
        self.premium_card = ValueCard('Расчетная премия за год')
        self.payable_card = ValueCard('К выплате за год')
        self.actual_card.clicked.connect(self._annual_actual)
        self.premium_card.clicked.connect(self._annual_premium)
        self.payable_card.clicked.connect(lambda:self.premium_requested.emit(4))
        for card in (self.actual_card,self.premium_card,self.payable_card):
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            annual.addWidget(card)
        layout.addLayout(annual)
        self.quarters = table(['Квартал','Факт','План','Порог','Выполнение','Порог пройден','Расчетная премия','К выплате'])
        self.quarters.setMinimumHeight(175)
        self.quarters.cellClicked.connect(self._quarter_click)
        layout.addWidget(self.quarters)
        layout.addWidget(QLabel('Премиальные блоки · нажмите строку для аудита базы'))
        self.blocks = table(['Квартал','Блок','База','Ставка','Премия'])
        self.blocks.setMinimumHeight(280)
        self.blocks.cellClicked.connect(self._block_click)
        layout.addWidget(self.blocks)
        layout.addWidget(QLabel('Зеленый коридор · выручка по категории и типу клиента'))
        self.green = table(['Тип клиента','Категория','Q1','Q2','Q3','Q4'])
        self.green.setMinimumHeight(250)
        self.green.cellClicked.connect(self._green_click)
        layout.addWidget(self.green)

    def set_result(self, result: CalculationResult) -> None:
        self.result = result
        self.actual_card.set_value(money(result.annual_actual))
        self.premium_card.set_value(money(result.annual_calculated_premium))
        self.payable_card.set_value(money(result.annual_payable))
        fill_table(self.quarters, [[f'Q{q.quarter}',money(q.actual),money(q.plan),money(q.gate_threshold),
            percent_text(q.achievement),'Да' if q.quarter_pass else 'Нет',money(q.calculated_premium),money(q.payable)] for q in result.quarters])
        fill_table(self.blocks, [[f'Q{q.quarter}',f'{b.number}. {BLOCK_NAMES[b.number-1]}',money(b.base),percent_text(b.rate),money(b.premium)] for q in result.quarters for b in q.blocks])
        fill_table(self.green, [[client,category,*(money(result.green_totals[q,client,category]) for q in range(1,5))] for client in CLIENT_TYPES for category in CATEGORIES])

    def _annual_actual(self) -> None:
        if self.result:
            self.audit_requested.emit(tuple(a.shipment.source_row for a in self.result.audit if a.quarter), 'Факт за год')

    def _annual_premium(self) -> None:
        if self.result:
            self.audit_requested.emit(tuple(a.shipment.source_row for a in self.result.included_rows), 'Расчетная премия за год')

    def _quarter_click(self, row: int, column: int) -> None:
        if not self.result:
            return
        q = self.result.quarters[row]
        if column == 1:
            self.audit_requested.emit(q.actual_source_rows, f'Факт Q{q.quarter}')
        elif column == 6:
            self.audit_requested.emit(tuple(a.shipment.source_row for a in self.result.included_rows if a.quarter == q.quarter), f'Расчетная премия Q{q.quarter}')
        else:
            self.premium_requested.emit(q.quarter)

    def _block_click(self, row: int, _column: int) -> None:
        if self.result:
            quarter, block = divmod(row, 4)
            self.audit_requested.emit(self.result.quarters[quarter].blocks[block].source_rows,
                                      f'Q{quarter+1} · блок {block+1}')

    def _green_click(self, row: int, column: int) -> None:
        if self.result and column >= 2:
            client, category = CLIENT_TYPES[row // len(CATEGORIES)], CATEGORIES[row % len(CATEGORIES)]
            quarter = column-1
            self.audit_requested.emit(tuple(a.shipment.source_row for a in self.result.included_rows if
                a.quarter == quarter and a.client_type == client and a.product.category == category),
                f'Q{quarter} · {client} · {category}')
