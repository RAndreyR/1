"""Real Qt widgets in offscreen mode; no skips or mocked GUI toolkit."""
import os
from decimal import Decimal
from pathlib import Path
import pytest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('XDG_CACHE_HOME','/tmp/kpi-kam-test-cache')

from PySide6.QtCore import Qt, QElapsedTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.models.application import AppSettings
from app.models.domain import Rules
from app.services.excel_importer import import_excel
from app.ui.main_window import MainWindow
from app.utils.input_values import percentage, percent_input, nonnegative_number
from tests.excel_helpers import make_workbook
from tests.workbook_fixture import FIXTURE


@pytest.fixture(scope='session')
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qt_app,tmp_path):
    widget = MainWindow(tmp_path/'gui.db')
    widget.show()
    qt_app.processEvents()
    yield widget
    widget.close()
    qt_app.processEvents()


def set_fields(window):
    window.new_page.employee.setText('Трофимов Дмитрий')
    window.new_page.year.setValue(2026)
    for field,value in zip(window.new_page.plan_inputs,('20000000','10000000','0','0')):
        field.setText(value)


def allow_unmatched(window):
    while True:
        entry = next((e for e in window.service.session.mappings if e.status == 'unresolved'),None)
        if entry is None:
            break
        row = next(i for i,e in enumerate(window.service.session.mappings) if e.key == entry.key)
        window.mapping_page.grid.selectRow(row)
        QTest.mouseClick(window.mapping_page.leave_button,Qt.MouseButton.LeftButton)


def ready_result(window):
    set_fields(window)
    window.load_import(import_excel(FIXTURE))
    window.navigate(2)
    allow_unmatched(window)
    QTest.mouseClick(window.mapping_page.calculate_button,Qt.MouseButton.LeftButton)
    assert window.saved is not None, window.error_label.text()
    return window.saved.result


def test_new_calculation_mandatory_inputs_and_decimal_validation(window):
    assert window.stack.count() == 8
    assert not window.new_page.calculate_button.isEnabled()
    window.load_import(import_excel(FIXTURE))
    window.navigate(2)
    allow_unmatched(window)
    assert not window.mapping_page.calculate_button.isEnabled()
    set_fields(window)
    assert window.new_page.calculate_button.isEnabled()
    window.new_page.plan_inputs[3].setText('-1')
    assert not window.new_page.calculate_button.isEnabled()
    assert not window.import_page.calculate_button.isEnabled()
    window.new_page.plan_inputs[3].setText('1 234,56')
    assert window.new_page.plans().q4 == Decimal('1234.56')
    window.new_page.gate.setText('101')
    assert not window.new_page.calculate_button.isEnabled()
    assert window.saved is None


def test_async_excel_import_maps_and_uses_existing_engine(window,monkeypatch):
    import app.services.mapping_service as mapping
    real = mapping.calculate_kpi
    calls = []
    def recorded(*args,**kwargs):
        calls.append((args,kwargs))
        return real(*args,**kwargs)
    monkeypatch.setattr(mapping,'calculate_kpi',recorded)
    set_fields(window)
    window.new_page.source.setText(str(FIXTURE))
    QTest.mouseClick(window.new_page.check_button,Qt.MouseButton.LeftButton)
    assert not window.new_page.check_button.isEnabled()
    timer = QElapsedTimer()
    timer.start()
    while window._worker is not None and timer.elapsed() < 5000:
        QTest.qWait(10)
    assert window._worker is None
    assert window.service.session is not None
    assert window.stack.currentIndex() == 1
    assert window.service.session.validation.pending_mapping_rows == (68,103,156,158)
    QTest.mouseClick(window.import_page.mapping_button,Qt.MouseButton.LeftButton)
    assert window.stack.currentIndex() == 2
    allow_unmatched(window)
    assert window.mapping_page.calculate_button.isEnabled()
    QTest.mouseClick(window.mapping_page.calculate_button,Qt.MouseButton.LeftButton)
    assert len(calls) == 1
    assert window.saved.result.annual_calculated_premium == Decimal('251283.3171100000090')
    assert window.saved.result.quarters[3].payable == Decimal('112087.8452000000000')
    assert window.stack.currentIndex() == 3
    assert window.history_page.grid.rowCount() == 1


def test_dashboard_block_drilldown_and_audit_filters(window):
    result = ready_result(window)
    window.dashboard_page.blocks.cellClicked.emit(0,2)
    assert window.stack.currentIndex() == 4
    assert {r.shipment.source_row for r in window.audit_page.selection.rows} == set(result.quarters[0].blocks[0].source_rows)
    assert window.audit_page.selection.included_revenue == result.quarters[0].blocks[0].base
    QTest.mouseClick(window.audit_page.clear_button,Qt.MouseButton.LeftButton)
    assert window.audit_page.grid.rowCount() == 170
    window.audit_page.status.setCurrentIndex(2)
    assert window.audit_page.grid.rowCount() == 106
    window.audit_page.product.setText('Resource')
    assert [a.shipment.source_row for a in window.audit_page.selection.rows] == [68]
    window.audit_page.clear_filters()
    window.audit_page.category.setCurrentIndex(window.audit_page.category.count()-1)
    assert [a.shipment.source_row for a in window.audit_page.selection.rows] == [68,103,156,158]
    window.dashboard_page.green.cellClicked.emit(0,2)
    assert window.audit_page.selection.included_revenue == result.green_totals[1,'дистрибьютер','СБКС']


def test_settings_and_history_preserve_previous_calculation(window):
    original = ready_result(window)
    first_id = window.saved.id
    window.navigate(6)
    window.settings_page.fields[1].setText('10')
    QTest.mouseClick(window.settings_page.save_button,Qt.MouseButton.LeftButton)
    assert window.repository.settings().rules.rate_block_1 == Decimal('0.1')
    assert window.saved.result == original
    window.navigate(0)
    QTest.mouseClick(window.new_page.calculate_button,Qt.MouseButton.LeftButton)
    assert window.saved.id != first_id
    assert window.saved.result.rules.rate_block_1 == Decimal('0.1')
    assert window.history_page.grid.rowCount() == 2
    window.navigate(7)
    window.history_page.grid.cellDoubleClicked.emit(1,0)
    assert window.saved.id == first_id
    assert window.saved.result == original
    window.show_premium(4)
    assert '139 195,47' in window.premium_page.annual.text()
    assert '112 087,85' in window.premium_page.cards['payable'].value.text()
    assert window.premium_page.cards['gate'].caption.text().endswith('90,0%')


def test_gui_mapping_remember_and_reopen(window,tmp_path):
    rows = [['1 кв.','Клиент','ЛПУ','Старое имя',10,1000,100,None,None,None]]
    path = make_workbook(tmp_path/'input.xlsx',shipments=rows)
    imported = import_excel(path)
    window.load_import(imported)
    window.navigate(2)
    window.mapping_page.grid.selectRow(0)
    assert window.mapping_page.remember.isEnabled()
    window.mapping_page.products.setCurrentIndex(1)
    window.mapping_page.remember.setChecked(True)
    QTest.mouseClick(window.mapping_page.apply_button,Qt.MouseButton.LeftButton)
    assert window.service.session.mappings[0].status == 'manual'
    assert window.repository.aliases()
    window.load_import(imported)
    assert window.service.session.mappings[0].status == 'saved_alias'
    assert window.mapping_page.grid.item(0,3).text() == 'СБКС'


def test_gui_source_change_invalidates_ready_session(window):
    ready_result(window)
    window.new_page.source.setText('different.xlsx')
    assert window.service.session is None
    assert not window.new_page.calculate_button.isEnabled()
    assert window.mapping_page.grid.rowCount() == 0
    assert window.saved is not None  # The displayed historical snapshot stays intact.


def test_explicit_paid_history_and_q4_details(window):
    ready_result(window)
    window.navigate(0)
    window.new_page.override_paid.setChecked(True)
    assert not window.new_page.calculate_button.isEnabled()
    for field,value in zip(window.new_page.paid_inputs,('50000','100000','0')):
        field.setText(value)
    assert window.new_page.calculate_button.isEnabled()
    QTest.mouseClick(window.new_page.calculate_button,Qt.MouseButton.LeftButton)
    assert window.saved.result.paid_q1_q3 == Decimal('150000')
    assert window.saved.result.quarters[3].payable == Decimal('101283.3171100000090')
    window.show_premium(4)
    assert '150 000,00' in window.premium_page.annual.text()


@pytest.mark.parametrize('value,text', [('1','100'),('0.90','90'),('0.05','5'),('0','0')])
def test_percent_inputs_keep_integral_zeros(value,text):
    assert percent_input(Decimal(value)) == text
    assert percentage(text) == Decimal(value)


def test_desktop_entry_point_event_loop(tmp_path):
    import subprocess
    import sys
    project = Path(__file__).parents[1]
    script = '''
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from main import main
import sys
app=QApplication([])
QTimer.singleShot(200,app.quit)
raise SystemExit(main(['gui','--db',sys.argv[1]]))
'''
    result = subprocess.run([sys.executable,'-c',script,str(tmp_path/'desktop.db')],
                            cwd=project,capture_output=True,text=True,timeout=10)
    assert result.returncode == 0, result.stderr
    assert (tmp_path/'desktop.db').exists()
    assert (tmp_path/'logs/app.log').exists()
