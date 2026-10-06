from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget
from app.services.mapping_service import ImportSession
from app.ui.common import fill_table, page_layout, table

STATUS = {'exact':'Точное совпадение','saved_alias':'Сохраненный алиас','manual':'Выбран вручную',
          'unresolved':'Требуется решение','left_unmatched':'Оставлен несопоставленным'}


class MappingPage(QWidget):
    select_requested = Signal(str, str, bool)
    leave_requested = Signal(str)
    calculate_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.session: ImportSession | None = None
        layout = page_layout(self, 'Сопоставление товаров', 'Выберите товар из текущего прайса или явно оставьте его несопоставленным.')
        self.grid = table(['Исходное название','Строки Excel','Канонический товар','Категория','Статус'])
        self.grid.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.grid)
        self.description = QLabel('Выберите строку в таблице.')
        layout.addWidget(self.description)
        actions = QHBoxLayout()
        self.products = QComboBox()
        self.products.setMinimumWidth(250)
        actions.addWidget(self.products, 1)
        self.remember = QCheckBox('Запомнить сопоставление')
        actions.addWidget(self.remember)
        self.apply_button = QPushButton('Применить')
        self.apply_button.clicked.connect(self._apply)
        self.leave_button = QPushButton('Оставить несопоставленным')
        self.leave_button.clicked.connect(self._leave)
        actions.addWidget(self.apply_button)
        actions.addWidget(self.leave_button)
        layout.addLayout(actions)
        self.calculate_button = QPushButton('Рассчитать KPI')
        self.calculate_button.setObjectName('primary')
        self.calculate_button.clicked.connect(self.calculate_requested)
        self.calculate_button.setEnabled(False)
        layout.addWidget(self.calculate_button)
        self.products.currentIndexChanged.connect(self._update_apply)
        self._selection_changed()

    def set_session(self, session: ImportSession | None, can_calculate: bool = False) -> None:
        previous = self.current_key()
        self.session = session
        self.products.clear()
        self.products.addItem('Выберите товар из прайса', None)
        if session:
            for product in sorted(session.imported.products, key=lambda p:p.canonical_name.casefold()):
                self.products.addItem(f'{product.canonical_name.strip()} · {product.category}', product.id)
        entries = session.mappings if session else ()
        fill_table(self.grid, [[next((name for name in e.raw_names if name and name.strip()), '(пустое название)'),
            ', '.join(map(str,e.source_rows)),e.product.canonical_name if e.product else '—',
            e.product.category if e.product else '—',STATUS[e.status]] for e in entries])
        self.calculate_button.setEnabled(can_calculate)
        row = next((i for i,e in enumerate(entries) if e.key == previous),
                   next((i for i,e in enumerate(entries) if e.status == 'unresolved'), 0))
        if entries:
            self.grid.selectRow(row)
        self._selection_changed()

    def current_key(self) -> str | None:
        row = self.grid.currentRow()
        return self.session.mappings[row].key if self.session and 0 <= row < len(self.session.mappings) else None

    def _selection_changed(self) -> None:
        key = self.current_key()
        entry = next((e for e in self.session.mappings if e.key == key), None) if self.session else None
        editable = entry is not None and entry.status != 'exact' and not self.session.imported.errors
        self.apply_button.setEnabled(editable)
        self.leave_button.setEnabled(editable)
        self.products.setEnabled(editable)
        self.remember.setEnabled(editable and bool(entry.alias_normalized) and self.session.repository is not None if entry else False)
        self.remember.setChecked(False)
        self.products.setCurrentIndex(self.products.findData(entry.product.id) if entry and entry.product else 0)
        self._update_apply()
        self.description.setText('Категория и пороги берутся из выбранного товара текущего прайса.' if entry else 'Выберите строку в таблице.')

    def _update_apply(self, *_args) -> None:
        key = self.current_key()
        entry = next((e for e in self.session.mappings if e.key == key), None) if self.session else None
        self.apply_button.setEnabled(entry is not None and entry.status != 'exact'
            and not self.session.imported.errors and self.products.currentData() is not None)

    def _apply(self) -> None:
        key, product_id = self.current_key(), self.products.currentData()
        if key and product_id:
            self.select_requested.emit(key, product_id, self.remember.isChecked())

    def _leave(self) -> None:
        key = self.current_key()
        if key:
            self.leave_requested.emit(key)
