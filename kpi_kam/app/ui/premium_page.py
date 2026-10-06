from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QWidget
from app.models.domain import CalculationResult
from app.ui.common import BLOCK_NAMES, ValueCard, fill_table, money, page_layout, table
from app.utils.input_values import percent_text


class PremiumPage(QWidget):
    audit_requested = Signal(object, str)

    def __init__(self) -> None:
        super().__init__()
        self.result: CalculationResult | None = None
        layout = page_layout(self, 'Детали премии', 'Базы, ставки, квартальный порог и годовой перерасчет взяты из снимка расчета.')
        self.quarter = QComboBox()
        for q in range(1,5):
            self.quarter.addItem(f'Q{q}', q)
        self.quarter.currentIndexChanged.connect(self.refresh)
        layout.addWidget(self.quarter)
        grid = QGridLayout()
        self.cards = {}
        for index, (key, title) in enumerate((('actual','Факт'),('plan','План'),('gate','Порог плана'),
                      ('achievement','Выполнение'),('calculated','Расчетная премия'),('payable','К выплате'))):
            card = ValueCard(title)
            self.cards[key] = card
            grid.addWidget(card, index//3, index%3)
        for key in ('actual','calculated','payable'):
            self.cards[key].setCursor(Qt.CursorShape.PointingHandCursor)
            self.cards[key].clicked.connect(lambda key=key:self._card_click(key))
        layout.addLayout(grid)
        self.gate_status = QLabel('Нет расчета')
        layout.addWidget(self.gate_status)
        self.blocks = table(['Блок','База','Ставка','Премия'])
        self.blocks.setMinimumHeight(190)
        self.blocks.cellClicked.connect(self._block_click)
        layout.addWidget(self.blocks)
        self.annual = QLabel()
        self.annual.setWordWrap(True)
        layout.addWidget(self.annual)
        layout.addStretch()

    def set_result(self, result: CalculationResult) -> None:
        self.result = result
        self.refresh()

    def select_quarter(self, quarter: int) -> None:
        self.quarter.setCurrentIndex(quarter-1)
        self.refresh()

    def refresh(self, *_args) -> None:
        if not self.result:
            return
        r = self.result
        q = r.quarters[self.quarter.currentIndex()]
        for key,value in (('actual',money(q.actual)),('plan',money(q.plan)),('gate',money(q.gate_threshold)),
                          ('achievement',percent_text(q.achievement)),('calculated',money(q.calculated_premium)),('payable',money(q.payable))):
            self.cards[key].set_value(value)
        self.cards['gate'].caption.setText(f'Порог плана · {percent_text(r.rules.plan_gate)}')
        self.gate_status.setText('Квартальный порог пройден' if q.quarter_pass else 'Квартальный порог не пройден')
        fill_table(self.blocks, [[f'{b.number}. {BLOCK_NAMES[b.number-1]}',money(b.base),percent_text(b.rate),money(b.premium)] for b in q.blocks])
        self.annual.setVisible(q.quarter == 4)
        self.annual.setText(f'Годовой план: {money(r.plans.annual)}\nГодовой факт: {money(r.annual_actual)} · '
            f'Выполнение: {percent_text(r.annual_achievement)}\n'
            f'Годовой порог: {"пройден — выполняется годовой перерасчет" if r.annual_pass else "не пройден — применяется квартальный порог Q4"}\n'
            f'Расчетная премия за год: {money(r.annual_calculated_premium)}\n'
            f'Уже выплачено Q1–Q3: {money(r.paid_q1_q3)}\n'
            f'Годовой перерасчет к выплате в Q4: {money(r.annual_catch_up) if r.annual_catch_up is not None else "не применяется"}')

    def _card_click(self, key: str) -> None:
        if self.result is None:
            return
        q = self.result.quarters[self.quarter.currentIndex()]
        if key == 'actual':
            rows = q.actual_source_rows
            title = f'Факт Q{q.quarter}'
        else:
            annual = key == 'payable' and q.quarter == 4 and self.result.annual_pass
            rows = tuple(a.shipment.source_row for a in self.result.included_rows if annual or a.quarter == q.quarter)
            title = 'Годовая база премии для перерасчета Q4' if annual else f'База премии Q{q.quarter}'
        self.audit_requested.emit(rows,title)

    def _block_click(self, row: int, _column: int) -> None:
        if self.result:
            q = self.result.quarters[self.quarter.currentIndex()]
            self.audit_requested.emit(q.blocks[row].source_rows,f'Q{q.quarter} · блок {row+1}')
