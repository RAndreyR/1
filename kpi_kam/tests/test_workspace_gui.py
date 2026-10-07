"""The actual desktop flow, including permissions and durable payments."""
import os
from decimal import Decimal
import pytest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QElapsedTimer,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QPushButton
from app.ui.workspace_window import WorkspaceWindow
from tests.event_helpers import sales_book, price_book


@pytest.fixture
def desktop(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=WorkspaceWindow(tmp_path/'desktop.db')
    window.show();app.processEvents()
    yield window
    window.close();app.processEvents()


def wait_import(window):
    timer=QElapsedTimer();timer.start()
    while window._worker is not None and timer.elapsed()<5000:
        QTest.qWait(10)
    assert window._worker is None
    assert not window.error_label.isVisible(),window.error_label.text()


def prepare(window,tmp_path,role='KAM',raw='Товар'):
    window.admin.password.setText('stopp');window.admin.login.click()
    assert window.workflow.is_admin
    employee=window.repository.resolve_employee('Трофимов')
    window.workflow.set_role(employee.id,2026,role)
    window.begin_price_import(str(price_book(tmp_path/'price.xlsx')));wait_import(window)
    window.employee.setCurrentIndex(window.employee.findData(employee.id))
    window.year.setValue(2026)
    window.refresh_workspace()
    for edit in window.plan_inputs: edit.setText('1000')
    window.begin_sales_import(str(sales_book(tmp_path/'sales.xlsx',raw=raw)));wait_import(window)
    return employee


def test_default_window_admin_import_calculation_payment_and_restart(desktop,tmp_path):
    assert desktop.navigation.item(6).isHidden()
    assert not desktop.admin.tabs.isEnabled()
    assert desktop.employee.findText('Филимонова Анна')>=0
    employee=prepare(desktop,tmp_path)
    desktop.calculate_button.click()
    assert desktop.workspace_saved is not None,desktop.error_label.text()
    first=desktop.workspace_saved
    assert first.calculation.quarters[0].payable==50
    assert desktop.shipments.grid.rowCount()==1
    assert desktop.premiums.grid.item(0,2).text()=='1 000,00 ₽'
    desktop.confirm_payment(1,Decimal('40'))
    assert desktop.repository.payments(employee.id,2026)[0].amount==40
    desktop.admin.logout.click()
    assert not desktop.workflow.is_admin
    desktop.close()
    reopened=WorkspaceWindow(tmp_path/'desktop.db')
    try:
        assert not reopened.workflow.is_admin
        assert reopened.employee.currentData()==employee.id
        assert reopened.plan_inputs[0].text()=='1000'
        assert reopened.workspace_saved.status=='paid/closed'
        assert reopened.workspace_saved.price.products[0].category=='ВМК'
    finally: reopened.close()


def test_product_mapping_requires_explicit_choice_and_uses_calls_engine(desktop,tmp_path):
    prepare(desktop,tmp_path,role='SUPPORT',raw='Неизвестное название')
    desktop.call_inputs[0].setText('700')
    desktop.calculate_button.click()
    assert desktop.workspace_saved is None
    assert 'сопоставления' in desktop.error_label.text()
    desktop.mappings.grid.selectRow(0)
    desktop.mappings.products.setCurrentIndex(1)
    desktop.mappings.apply.click()
    desktop.calculate_button.click()
    assert desktop.workspace_saved.calculation.quarters[0].calls_bonus==28000
    assert desktop.workspace_saved.calculation.quarters[0].payable==28050
    desktop.open_workspace_history(desktop.workspace_saved.id)
    assert desktop.mappings.grid.item(0,4).text()=='alias'


def test_support_calls_plan_is_editable_used_by_engine_and_persisted(desktop,tmp_path):
    employee=prepare(desktop,tmp_path,role='SUPPORT')
    desktop.admin.logout.click()
    assert not desktop.workflow.is_admin
    for edit,value in zip(desktop.calls_plan_inputs,('1000','800','600','900')):
        edit.setText(value)
    desktop.call_inputs[0].setText('900')
    desktop.save_inputs_button.click()
    assert not desktop.error_label.isVisible(),desktop.error_label.text()
    assert desktop.repository.year_profile(employee.id,2026).calls_plans==tuple(map(Decimal,('1000','800','600','900')))
    desktop.calculate_button.click()
    assert desktop.workspace_saved.calculation.quarters[0].calls_bonus==27000
    assert desktop.workspace_saved.calculation.quarters[0].payable==27050
    desktop.confirm_payment(1)
    desktop.refresh_workspace()
    assert desktop.calls_plan_inputs[0].isReadOnly()
    assert not desktop.calls_plan_inputs[1].isReadOnly()
    desktop.close()
    reopened=WorkspaceWindow(tmp_path/'desktop.db')
    try:
        assert [edit.text() for edit in reopened.calls_plan_inputs]==['1000','800','600','900']
        assert reopened.calls_plan_inputs[0].isReadOnly()
        assert reopened.workspace_saved.profile.calls_plans[0]==1000
    finally:reopened.close()


@pytest.mark.parametrize('value',['','0','-1','text'])
def test_support_invalid_calls_plan_does_not_save_or_calculate(desktop,tmp_path,value):
    employee=prepare(desktop,tmp_path,role='SUPPORT')
    desktop.calls_plan_inputs[0].setText(value)
    desktop.calculate_button.click()
    assert desktop.error_label.isVisible()
    assert desktop.workspace_saved is None
    assert desktop.repository.year_profile(employee.id,2026) is None


def test_manual_calls_plans_follow_selected_employee_and_year(desktop,tmp_path):
    from app.models.domain import Plans
    employee=prepare(desktop,tmp_path,role='SUPPORT')
    other=desktop.repository.resolve_employee('Гайдина')
    desktop.workflow.set_role(other.id,2026,'SUPPORT')
    desktop.workflow.set_role(employee.id,2027,'SUPPORT')
    sales_plans=Plans('1000','1000','1000','1000')
    desktop.workflow.save_inputs(employee.id,2026,sales_plans,calls_plans=('1000','800','600','900'))
    desktop.workflow.save_inputs(other.id,2026,sales_plans,calls_plans=('400','500','600','700'))
    desktop.workflow.save_inputs(employee.id,2027,sales_plans,calls_plans=('1100','1200','1300','1400'))
    desktop.refresh_workspace()
    assert desktop.calls_plan_inputs[0].text()=='1000'
    desktop.employee.setCurrentIndex(desktop.employee.findData(other.id))
    assert [e.text() for e in desktop.calls_plan_inputs]==['400','500','600','700']
    desktop.employee.setCurrentIndex(desktop.employee.findData(employee.id))
    desktop.year.setValue(2027)
    assert [e.text() for e in desktop.calls_plan_inputs]==['1100','1200','1300','1400']
    desktop.year.setValue(2026)
    assert [e.text() for e in desktop.calls_plan_inputs]==['1000','800','600','900']


def test_common_sales_mapping_shows_packaging_and_filters_other_sizes(desktop,tmp_path):
    from tests.test_packaging_mapping import packaged_sales,price_variants
    prepare(desktop,tmp_path,raw='Стандарт')
    desktop.begin_price_import(str(price_variants(tmp_path/'variants.xlsx')));wait_import(desktop)
    desktop.begin_sales_import(str(packaged_sales(tmp_path/'packaged.xlsx')));wait_import(desktop)
    assert desktop.mappings.grid.rowCount()==3
    assert desktop.mappings.grid.horizontalHeaderItem(1).text()=='Фасовка'
    for row in range(3):
        desktop.mappings.grid.selectRow(row)
        size=desktop.mappings.grid.item(row,1).text()
        assert desktop.mappings.products.count()==2
        assert size in desktop.mappings.products.itemText(1)
        desktop.mappings.products.setCurrentIndex(1)
        QTest.mouseClick(desktop.mappings.apply,Qt.MouseButton.LeftButton)
        assert not desktop.error_label.isVisible(),desktop.error_label.text()
    desktop.calculate_button.click()
    assert desktop.workspace_saved is not None,desktop.error_label.text()
    assert [a.audit.product.canonical_name for a in desktop.workspace_saved.calculation.event_audit]==[
        'Иннованта Стандарт 200 мл','Иннованта Стандарт 500 мл','Иннованта Стандарт 1000 мл']
    assert [desktop.shipments.grid.item(row,16).text() for row in range(3)]==['200 мл','500 мл','1000 мл']


def test_returns_screen_and_guard_against_ordinary_price_import(desktop,tmp_path):
    employee=prepare(desktop,tmp_path)
    desktop.calculate_button.click();desktop.confirm_payment(1)
    desktop.begin_sales_import(str(sales_book(tmp_path/'updated.xlsx',returns={7:('2','200')})));wait_import(desktop)
    desktop.calculate_button.click()
    assert desktop.returns.grid.rowCount()==1
    assert desktop.workspace_saved.calculation.quarters[2].actual==-200
    assert desktop.workspace_saved.calculation.quarters[2].clawback==10
    desktop.admin.logout.click()
    before=desktop.repository.price_version().id
    desktop.begin_price_import(str(tmp_path/'price.xlsx'))
    assert desktop._worker is None
    assert desktop.repository.price_version().id==before
    assert 'администратора' in desktop.error_label.text()


def test_admin_confirms_missing_headers_with_preview(desktop,tmp_path,monkeypatch):
    from app.ui.workspace_pages import ColumnMappingDialog
    from app.services.sales_importer import FIELDS
    from PySide6.QtWidgets import QDialog
    prepare(desktop,tmp_path)
    path=sales_book(tmp_path/'missing.xlsx',missing_headers=True)
    desktop.begin_sales_import(str(path));wait_import(desktop)
    assert desktop.pending_columns is not None
    assert 'missing.xlsx' in desktop.source_label.text()
    assert 'не завершен' in desktop.source_label.text()
    assert 'контракты ЭП' in desktop.source_label.text()
    assert not desktop.calculate_button.isEnabled()
    assert 'не импортирован' not in desktop.source_label.text()
    before=desktop.repository.latest_sales(2026)[0]
    def confirmed(dialog):
        assert dialog.columns['manager'].itemText(8).startswith('H:')
        dialog.header.setValue(6)
        for col,field in enumerate(reversed(list(FIELDS)),2):
            dialog.columns[field].setCurrentIndex(dialog.columns[field].findData(col))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ColumnMappingDialog,'exec',confirmed)
    desktop.year.setValue(2027)
    desktop.resolve_columns();wait_import(desktop)
    assert desktop.pending_columns is None
    assert desktop.repository.latest_sales(2026)[0]!=before
    assert desktop.repository.latest_sales(2027) is None
    assert desktop.year.value()==2026
    assert len(desktop.repository.latest_sales(2026)[1].shipments)==1
    assert 'Продажи сохранены для 2026' in desktop.readiness.text()
    assert desktop.calculate_button.isEnabled()


def test_admin_password_change_and_unknown_manager_mapping(desktop,tmp_path):
    prepare(desktop,tmp_path)
    desktop.begin_sales_import(str(sales_book(tmp_path/'unknown.xlsx',manager='Бабенкова')));wait_import(desktop)
    assert 'Бабенкова' in desktop.admin.unknown.text()
    desktop.admin.alias.setText('Бабенкова')
    desktop.admin.employees.setCurrentIndex(desktop.admin.employees.findText('Трофимов Дмитрий'))
    desktop.map_employee()
    assert desktop.workflow.unknown_managers(desktop.repository.latest_sales(2026)[1])==()
    desktop.admin.old_password.setText('stopp')
    desktop.admin.new_password.setText('Changed_password_2026')
    desktop.admin.repeat_password.setText('Changed_password_2026')
    desktop.change_admin_password();desktop.admin.logout.click()
    desktop.admin.password.setText('Changed_password_2026');desktop.admin.login.click()
    assert desktop.workflow.is_admin
    assert not desktop.admin.new_password.text()


def role_editor(window):
    window.navigate(13)
    window.admin.password.setText('stopp')
    window.admin.login.click()
    window.admin.tabs.setCurrentIndex(1)
    return next(button for button in window.admin.findChildren(QPushButton)
                if button.text()=='Задать должность на год')


def test_employee_switch_loads_own_saved_role_without_changing_other_employee(desktop):
    save_button=role_editor(desktop)
    gaidina=desktop.repository.resolve_employee('Гайдина')
    trofimov=desktop.repository.resolve_employee('Трофимов')
    desktop.admin.year.setValue(2026)
    desktop.admin.employees.setCurrentIndex(desktop.admin.employees.findData(gaidina.id))
    desktop.admin.role.setCurrentIndex(desktop.admin.role.findData('SUPPORT'))
    QTest.mouseClick(save_button,Qt.MouseButton.LeftButton)
    desktop.admin.employees.setCurrentIndex(desktop.admin.employees.findData(trofimov.id))
    desktop.admin.role.setCurrentIndex(desktop.admin.role.findData('KAM'))
    QTest.mouseClick(save_button,Qt.MouseButton.LeftButton)
    assert desktop.repository.year_role(gaidina.id,2026)=='SUPPORT'
    assert desktop.repository.year_role(trofimov.id,2026)=='KAM'
    desktop.admin.employees.setCurrentIndex(desktop.admin.employees.findData(gaidina.id))
    assert desktop.admin.role.currentData()=='SUPPORT'
    desktop.admin.employees.setCurrentIndex(desktop.admin.employees.findData(trofimov.id))
    assert desktop.admin.role.currentData()=='KAM'
    # Selecting a value without pressing Save must not update either employee.
    desktop.admin.role.setCurrentIndex(desktop.admin.role.findData('SUPPORT'))
    desktop.admin.employees.setCurrentIndex(desktop.admin.employees.findData(gaidina.id))
    desktop.admin.employees.setCurrentIndex(desktop.admin.employees.findData(trofimov.id))
    assert desktop.admin.role.currentData()=='KAM'
    assert desktop.repository.year_role(gaidina.id,2026)=='SUPPORT'
    assert desktop.repository.year_role(trofimov.id,2026)=='KAM'


def test_year_switch_loads_own_role_and_unassigned_year_stays_unassigned(desktop):
    save_button=role_editor(desktop)
    employee=desktop.repository.resolve_employee('Гайдина')
    desktop.admin.employees.setCurrentIndex(desktop.admin.employees.findData(employee.id))
    for year,role in ((2026,'SUPPORT'),(2027,'KAM')):
        desktop.admin.year.setValue(year)
        desktop.admin.role.setCurrentIndex(desktop.admin.role.findData(role))
        QTest.mouseClick(save_button,Qt.MouseButton.LeftButton)
    desktop.admin.year.setValue(2026)
    assert desktop.admin.role.currentData()=='SUPPORT'
    desktop.admin.year.setValue(2027)
    assert desktop.admin.role.currentData()=='KAM'
    desktop.admin.year.setValue(2028)
    assert desktop.admin.role.currentData() is None
    assert not save_button.isEnabled()
    assert desktop.repository.year_role(employee.id,2028) is None


def test_role_editor_restores_employee_year_roles_after_restart(desktop,tmp_path):
    save_button=role_editor(desktop)
    employee=desktop.repository.resolve_employee('Гайдина')
    other=desktop.repository.resolve_employee('Трофимов')
    desktop.admin.employees.setCurrentIndex(desktop.admin.employees.findData(employee.id))
    desktop.admin.role.setCurrentIndex(desktop.admin.role.findData('SUPPORT'))
    QTest.mouseClick(save_button,Qt.MouseButton.LeftButton)
    desktop.admin.employees.setCurrentIndex(desktop.admin.employees.findData(other.id))
    desktop.admin.role.setCurrentIndex(desktop.admin.role.findData('KAM'))
    QTest.mouseClick(save_button,Qt.MouseButton.LeftButton)
    desktop.close()
    reopened=WorkspaceWindow(tmp_path/'desktop.db')
    try:
        # Role assignment must be readable even before mandatory plans are entered.
        assert reopened.repository.year_profile(employee.id,2026) is None
        reopened.admin.employees.setCurrentIndex(reopened.admin.employees.findData(employee.id))
        assert reopened.admin.role.currentData()=='SUPPORT'
        reopened.admin.employees.setCurrentIndex(reopened.admin.employees.findData(other.id))
        assert reopened.admin.role.currentData()=='KAM'
        assert not reopened.workflow.is_admin
    finally:reopened.close()


def test_ordinary_import_saved_status_and_year_switch(desktop,tmp_path):
    desktop.year.setValue(2026)
    path=sales_book(tmp_path/'ordinary-sales.xlsx')
    desktop.begin_sales_import(str(path));wait_import(desktop)
    assert desktop.repository.latest_sales(2026) is not None
    assert 'ordinary-sales.xlsx' in desktop.source_label.text()
    assert 'Продажи сохранены для 2026' in desktop.readiness.text()
    assert 'продажи' not in desktop.readiness.text().split('Осталось:')[-1].casefold()
    desktop.year.setValue(2027)
    assert desktop.repository.latest_sales(2027) is None
    assert '2027' in desktop.source_label.text()
    desktop.year.setValue(2026)
    assert 'ordinary-sales.xlsx' in desktop.source_label.text()
    assert 'Продажи сохранены для 2026' in desktop.readiness.text()


def test_unconfirmed_headers_show_file_and_admin_action_without_saving(desktop,tmp_path):
    desktop.year.setValue(2026)
    path=sales_book(tmp_path/'needs-columns.xlsx',missing_headers=True)
    desktop.begin_sales_import(str(path));wait_import(desktop)
    assert desktop.repository.latest_sales(2026) is None
    assert 'needs-columns.xlsx' in desktop.source_label.text()
    assert 'контракты ЭП' in desktop.source_label.text()
    assert 'Войдите' in desktop.readiness.text()
    assert desktop.columns_button.isVisible()
    assert not desktop.calculate_button.isEnabled()
    # Confirmation belongs to the year captured when importing, not a later UI year.
    desktop.year.setValue(2027)
    assert not desktop.columns_button.isVisible()
    assert '2027' in desktop.source_label.text()
    desktop.year.setValue(2026)
    assert desktop.columns_button.isVisible()
    assert 'needs-columns.xlsx' in desktop.source_label.text()
