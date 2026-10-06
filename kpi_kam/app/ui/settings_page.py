from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget
from app.models.application import AppSettings
from app.models.domain import Rules
from app.utils.input_values import percentage, percent_input
from app.utils.normalization import CalculationInputError
from app.ui.common import page_layout


class SettingsPage(QWidget):
    save_requested = Signal(object)

    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        layout = page_layout(self,'Настройки','Изменения применяются к новым расчетам. Сохраненная история сохраняет исходные планы и ставки.')
        form = QFormLayout()
        self.fields = []
        for title,value in zip(('Минимальное выполнение плана, %','Блок 1, %','Блок 2, %','Блок 3, %','Блок 4, %'),
                               (settings.rules.plan_gate,*settings.rules.rates)):
            field = QLineEdit(percent_input(value))
            self.fields.append(field)
            form.addRow(title, field)
        self.precision = QLineEdit('1 знак после запятой')
        self.precision.setReadOnly(True)
        form.addRow('Точность сравнения цены',self.precision)
        self.export_folder = QLineEdit(settings.export_folder)
        folder = QHBoxLayout()
        folder.addWidget(self.export_folder)
        browse = QPushButton('Выбрать папку')
        browse.clicked.connect(self._browse)
        folder.addWidget(browse)
        form.addRow('Папка экспорта по умолчанию',folder)
        layout.addLayout(form)
        self.feedback = QLabel()
        layout.addWidget(self.feedback)
        self.save_button = QPushButton('Сохранить настройки')
        self.save_button.setObjectName('primary')
        self.save_button.clicked.connect(self._save)
        layout.addWidget(self.save_button)
        layout.addStretch()
        for field in self.fields:
            field.textChanged.connect(self._changed)
        self._changed()

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self,'Папка экспорта',self.export_folder.text())
        if path:
            self.export_folder.setText(path)

    def settings(self) -> AppSettings:
        return AppSettings(Rules(*(percentage(field.text()) for field in self.fields)),self.export_folder.text().strip())

    def _changed(self, *_args) -> None:
        try:
            self.settings()
            self.save_button.setEnabled(True)
            self.feedback.clear()
        except CalculationInputError:
            self.save_button.setEnabled(False)
            self.feedback.setText('Введите проценты от 0 до 100 во всех полях.')

    def _save(self) -> None:
        self.save_requested.emit(self.settings())
