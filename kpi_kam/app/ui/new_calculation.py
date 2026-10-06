from datetime import date
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QWidget)
from app.models.domain import Plans, Rules
from app.utils.input_values import nonnegative_number, percentage, percent_input
from app.utils.normalization import CalculationInputError, format_money
from app.ui.common import page_layout


class NewCalculationPage(QWidget):
    import_requested = Signal(str)
    calculate_requested = Signal()
    mapping_requested = Signal()
    source_changed = Signal()

    def __init__(self, rules: Rules) -> None:
        super().__init__()
        self._ready, self._busy = False, False
        layout = page_layout(self, 'Новый расчет', 'Выберите файл отгрузок и введите планы на все четыре квартала.')
        data = QGroupBox('Сотрудник и источник')
        form = QFormLayout(data)
        self.employee = QLineEdit()
        self.employee.setPlaceholderText('Фамилия и имя сотрудника')
        self.year = QSpinBox()
        self.year.setRange(1900, 2100)
        self.year.setValue(date.today().year)
        self.source = QLineEdit()
        self.source.setReadOnly(True)
        self.source.setPlaceholderText('Файл .xlsx или .xlsm')
        file_row = QHBoxLayout()
        file_row.addWidget(self.source)
        self.browse = QPushButton('Выбрать файл')
        self.browse.clicked.connect(self._browse)
        file_row.addWidget(self.browse)
        form.addRow('Сотрудник', self.employee)
        form.addRow('Год', self.year)
        form.addRow('Excel-файл', file_row)
        layout.addWidget(data)
        plan_group = QGroupBox('Обязательные планы отгрузки')
        grid = QGridLayout(plan_group)
        self.plan_inputs = []
        for index in range(4):
            field = QLineEdit()
            field.setPlaceholderText('Например, 1 000 000,00')
            field.textChanged.connect(self.refresh)
            self.plan_inputs.append(field)
            grid.addWidget(QLabel(f'Q{index+1}'), 0, index)
            grid.addWidget(field, 1, index)
        self.annual_plan = QLabel('Годовой план: —')
        grid.addWidget(self.annual_plan, 2, 0, 1, 4)
        self.gate = QLineEdit(percent_input(rules.plan_gate))
        form_gate = QHBoxLayout()
        form_gate.addWidget(QLabel('Минимальное выполнение плана, %'))
        form_gate.addWidget(self.gate)
        grid.addLayout(form_gate, 3, 0, 1, 4)
        layout.addWidget(plan_group)
        paid_group = QGroupBox('Выплаты прошлых кварталов')
        paid_layout = QGridLayout(paid_group)
        self.override_paid = QCheckBox('Задать фактические выплаты Q1–Q3')
        self.override_paid.setToolTip('Без отметки используются выплаты Q1–Q3, рассчитанные по квартальным порогам.')
        paid_layout.addWidget(self.override_paid, 0, 0, 1, 3)
        self.paid_inputs = []
        for index in range(3):
            field = QLineEdit()
            field.setPlaceholderText(f'Выплачено Q{index+1}, ₽')
            field.setEnabled(False)
            field.textChanged.connect(self.refresh)
            self.paid_inputs.append(field)
            paid_layout.addWidget(field, 1, index)
        self.override_paid.toggled.connect(self._toggle_paid)
        layout.addWidget(paid_group)
        self.feedback = QLabel('Проверьте файл, затем разрешите сопоставления товаров.')
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        actions = QHBoxLayout()
        self.check_button = QPushButton('Проверить файл')
        self.check_button.clicked.connect(lambda: self.import_requested.emit(self.source.text()))
        self.mapping_button = QPushButton('Сопоставить товары')
        self.mapping_button.clicked.connect(self.mapping_requested)
        self.calculate_button = QPushButton('Рассчитать KPI')
        self.calculate_button.setObjectName('primary')
        self.calculate_button.clicked.connect(self.calculate_requested)
        for button in (self.check_button, self.mapping_button, self.calculate_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self.employee.textChanged.connect(self.refresh)
        self.gate.textChanged.connect(self.refresh)
        self.source.textChanged.connect(self._source_changed)
        self.refresh()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, 'Выберите файл отгрузок', '', 'Excel (*.xlsx *.xlsm)')
        if path:
            self.source.setText(path)

    def _source_changed(self) -> None:
        self._ready = False
        self.source_changed.emit()
        self.refresh()

    def _toggle_paid(self, checked: bool) -> None:
        for field in self.paid_inputs:
            field.setEnabled(checked)
        self.refresh()

    def plans(self) -> Plans:
        return Plans(*(nonnegative_number(field.text()) for field in self.plan_inputs))

    def paid_history(self):
        if not self.override_paid.isChecked():
            return None
        return {index: nonnegative_number(field.text()) for index, field in enumerate(self.paid_inputs, 1)}

    def inputs_valid(self) -> bool:
        try:
            self.plans()
            percentage(self.gate.text())
            self.paid_history()
            return bool(self.employee.text().strip())
        except CalculationInputError:
            return False

    def refresh(self, *_args) -> None:
        try:
            self.annual_plan.setText('Годовой план: ' + format_money(self.plans().annual))
        except CalculationInputError:
            self.annual_plan.setText('Годовой план: заполните все четыре плана')
        self.check_button.setEnabled(bool(self.source.text()) and not self._busy)
        self.calculate_button.setEnabled(self._ready and self.inputs_valid() and not self._busy)
        self.mapping_button.setEnabled(bool(self.source.text()) and not self._busy)

    def set_ready(self, ready: bool, message: str) -> None:
        self._ready = ready
        self.feedback.setText(message)
        self.refresh()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.browse.setEnabled(not busy)
        self.refresh()
