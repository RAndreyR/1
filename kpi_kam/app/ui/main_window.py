"""Application shell and signal routing. No KPI formulas in this module."""
import logging
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)
from app.models.application import AppSettings, SavedCalculation
from app.models.import_data import ExcelImport
from app.repositories.alias_repository import AliasStorageError
from app.repositories.application_repository import ApplicationRepository, ApplicationStorageError
from app.services.application_service import ApplicationService
from app.services.excel_importer import import_excel
from app.utils.input_values import percentage, percent_input
from app.utils.normalization import CalculationInputError
from app.ui.audit_page import AuditPage
from app.ui.common import STYLE
from app.ui.dashboard import DashboardPage
from app.ui.history_page import HistoryPage
from app.ui.import_page import ImportPage
from app.ui.mapping_page import MappingPage
from app.ui.new_calculation import NewCalculationPage
from app.ui.premium_page import PremiumPage
from app.ui.settings_page import SettingsPage

logger = logging.getLogger(__name__)
ERRORS = (CalculationInputError, AliasStorageError, ApplicationStorageError, ValueError)


class ImportWorker(QThread):
    imported = Signal(object)
    failed = Signal(str)

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self.path = path

    def run(self) -> None:
        try:
            self.imported.emit(import_excel(self.path))
        except Exception as exc:
            logger.exception('Excel import failed')
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    repository_class = ApplicationRepository

    def __init__(self, database_path: str | Path | None = None) -> None:
        super().__init__()
        self.repository = self.repository_class(database_path)
        self.service = ApplicationService(self.repository)
        self.saved: SavedCalculation | None = None
        self._worker: ImportWorker | None = None
        self._closing = False
        self.setWindowTitle('KPI KAM')
        self.resize(1380, 900)
        self.setMinimumSize(1000, 680)
        self.setStyleSheet(STYLE)
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0,0,0,0)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(218)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0,12,0,12)
        brand = QLabel('KPI KAM')
        brand.setObjectName('brand')
        side_layout.addWidget(brand)
        self.navigation = QListWidget()
        self.navigation.setObjectName('navigation')
        self.navigation.addItems(['Новый расчет','Проверка файла','Сопоставление товаров','Итоги KPI',
                                  'Аудит отгрузок','Детали премии','Настройки','История'])
        side_layout.addWidget(self.navigation)
        outer.addWidget(sidebar)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0,0,0,0)
        self.context = QLabel('Новый расчет')
        self.context.setContentsMargins(24,12,24,8)
        body.addWidget(self.context)
        self.error_label = QLabel()
        self.error_label.setObjectName('error')
        self.error_label.setContentsMargins(24,0,24,0)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        body.addWidget(self.error_label)
        self.stack = QStackedWidget()
        body.addWidget(self.stack,1)
        outer.addWidget(content,1)
        self.setCentralWidget(root)
        self.new_page = NewCalculationPage(self.service.settings.rules)
        self.import_page = ImportPage()
        self.mapping_page = MappingPage()
        self.dashboard_page = DashboardPage()
        self.audit_page = AuditPage()
        self.premium_page = PremiumPage()
        self.settings_page = SettingsPage(self.service.settings)
        self.history_page = HistoryPage()
        pages = (self.new_page,self.import_page,self.mapping_page,self.dashboard_page,
                 self.audit_page,self.premium_page,self.settings_page,self.history_page)
        for index,page in enumerate(pages):
            if index in (0,3,5,6):
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QFrame.Shape.NoFrame)
                scroll.setWidget(page)
                self.stack.addWidget(scroll)
            else:
                self.stack.addWidget(page)
        for index in (3,4,5):
            item = self.navigation.item(index)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self.navigation.currentRowChanged.connect(self._page_changed)
        self.navigation.setCurrentRow(0)
        self.new_page.import_requested.connect(self.begin_import)
        self.new_page.source_changed.connect(self._clear_import)
        self.new_page.mapping_requested.connect(lambda:self.navigate(2))
        self.import_page.mapping_requested.connect(lambda:self.navigate(2))
        for page in (self.new_page,self.import_page,self.mapping_page):
            page.calculate_requested.connect(self.calculate)
        self.mapping_page.select_requested.connect(self.select_product)
        self.mapping_page.leave_requested.connect(self.leave_unmatched)
        self.dashboard_page.audit_requested.connect(self.show_audit)
        self.dashboard_page.premium_requested.connect(self.show_premium)
        self.premium_page.audit_requested.connect(self.show_audit)
        self.settings_page.save_requested.connect(self.save_settings)
        self.history_page.refresh_requested.connect(self.refresh_history)
        self.history_page.open_requested.connect(self.open_history)
        for field in (self.new_page.employee,self.new_page.gate,*self.new_page.plan_inputs,*self.new_page.paid_inputs):
            field.textChanged.connect(self._sync_readiness)
        self.new_page.override_paid.toggled.connect(self._sync_readiness)
        self.refresh_history()
        self._refresh_session()

    def navigate(self, page: int) -> None:
        self.navigation.setCurrentRow(page)

    def _page_changed(self, page: int) -> None:
        self.stack.setCurrentIndex(page)
        if page in (3,4,5) and self.saved:
            saved = self.saved
            self.context.setText(f'{saved.employee} · {saved.year} · {Path(saved.source_file).name} · расчет №{saved.id}')
        else:
            self.context.setText('Настройки для новых расчетов' if page == 6 else
                                 'История расчетов' if page == 7 else 'Новый расчет')

    def report_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()
        self.statusBar().showMessage(message)

    def _clear_error(self) -> None:
        self.error_label.hide()
        self.statusBar().clearMessage()

    def _clear_import(self) -> None:
        self.service.session = None
        self._refresh_session()

    def _sync_readiness(self, *_args) -> None:
        session = self.service.session
        ready = session is not None and session.validation.can_calculate
        self.new_page._ready = ready
        self.new_page.refresh()
        self.new_page.mapping_button.setEnabled(session is not None and not session.imported.errors and self._worker is None)
        enabled = self.new_page.calculate_button.isEnabled()
        self.import_page.calculate_button.setEnabled(enabled)
        self.mapping_page.calculate_button.setEnabled(enabled)

    def _refresh_session(self) -> None:
        session = self.service.session
        if session is None:
            self.new_page.set_ready(False,'Проверьте файл, затем разрешите сопоставления товаров.')
        else:
            report = session.validation
            message = ('Файл готов. Заполните сотрудника и все четыре плана.' if report.can_calculate else
                       f'Ошибок структуры: {len(report.errors)}; строк, ожидающих сопоставления: {len(report.pending_mapping_rows)}.')
            self.new_page.set_ready(report.can_calculate,message)
        self._sync_readiness()
        enabled = self.new_page.calculate_button.isEnabled()
        self.import_page.set_session(session,enabled)
        self.mapping_page.set_session(session,enabled)

    def begin_import(self, path: str) -> None:
        if self._worker is not None or not path:
            return
        self._clear_error()
        self._clear_import()
        self.new_page.set_busy(True)
        self.new_page.feedback.setText('Проверяем Excel-файл…')
        self._worker = ImportWorker(path,self)
        self._worker.imported.connect(self._accept_import)
        self._worker.failed.connect(self.report_error)
        self._worker.finished.connect(self._import_finished)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _accept_import(self, imported: ExcelImport) -> None:
        if not self._closing and Path(self.new_page.source.text()).resolve() == Path(imported.source_file):
            self.load_import(imported)
            self.navigate(1)

    def _import_finished(self) -> None:
        self._worker = None
        if not self._closing:
            self.new_page.set_busy(False)
            self._refresh_session()

    def load_import(self, imported: ExcelImport) -> None:
        try:
            self.new_page.source.setText(imported.source_file)
            self.service.set_import(imported)
            self._refresh_session()
        except ERRORS as exc:
            self.report_error(str(exc))

    def select_product(self, key: str, product_id: str, remember: bool = False) -> None:
        try:
            if self.service.session:
                self.service.session.select_product(key,product_id,remember=remember)
                self._clear_error()
                self._refresh_session()
        except ERRORS as exc:
            self.report_error(str(exc))

    def leave_unmatched(self, key: str) -> None:
        try:
            if self.service.session:
                self.service.session.leave_unmatched(key)
                self._clear_error()
                self._refresh_session()
        except ERRORS as exc:
            self.report_error(str(exc))

    def calculate(self) -> None:
        try:
            saved = self.service.calculate(self.new_page.employee.text(),self.new_page.year.value(),
                self.new_page.plans(),percentage(self.new_page.gate.text()),self.new_page.paid_history())
            self._clear_error()
            self.display_calculation(saved)
            self.refresh_history()
            self.statusBar().showMessage('Расчет сохранен в историю',5000)
        except ERRORS as exc:
            logger.exception('Calculation could not be completed')
            self.report_error(str(exc))

    def display_calculation(self, saved: SavedCalculation) -> None:
        self.saved = saved
        for index in (3,4,5):
            item = self.navigation.item(index)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled)
        self.context.setText(f'{saved.employee} · {saved.year} · {Path(saved.source_file).name} · расчет №{saved.id}')
        self.dashboard_page.set_result(saved.result)
        self.audit_page.set_result(saved.result)
        self.premium_page.set_result(saved.result)
        self.navigate(3)

    def show_audit(self, rows: tuple[int, ...], title: str) -> None:
        if self.saved:
            self.audit_page.drill_down(rows,title)
            self.navigate(4)

    def show_premium(self, quarter: int) -> None:
        if self.saved:
            self.premium_page.select_quarter(quarter)
            self.navigate(5)

    def save_settings(self, settings: AppSettings) -> None:
        try:
            old_gate = percent_input(self.service.settings.rules.plan_gate)
            self.service.save_settings(settings)
            if self.new_page.gate.text() == old_gate:
                self.new_page.gate.setText(percent_input(settings.rules.plan_gate))
            self.settings_page.feedback.setText('Настройки сохранены. История не изменена.')
            self._clear_error()
            self._sync_readiness()
        except ERRORS as exc:
            self.report_error(str(exc))

    def refresh_history(self) -> None:
        try:
            self.history_page.set_entries(self.repository.history())
        except ERRORS as exc:
            self.report_error(str(exc))

    def open_history(self, calculation_id: int) -> None:
        try:
            self.display_calculation(self.repository.load_calculation(calculation_id))
            self._clear_error()
        except ERRORS as exc:
            self.report_error(str(exc))

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            if not self._worker.wait(5000):
                self.report_error('Дождитесь завершения проверки файла перед закрытием.')
                event.ignore()
                return
        self._closing = True
        self.repository.close()
        super().closeEvent(event)
