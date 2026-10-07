"""Common-sales desktop extension of the existing application shell."""
from decimal import Decimal
from datetime import datetime
from pathlib import Path
import sqlite3
from PySide6.QtCore import QThread,Signal
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QLineEdit,QComboBox,QSpinBox,QPushButton,QFileDialog,QMessageBox,QInputDialog,
    QGroupBox,QScrollArea,QDialog)
from app.models.domain import Plans,Rules
from app.models.events import RolePolicy
from app.repositories.workspace_repository import WorkspaceRepository
from app.services.workspace_service import WorkspaceService
from app.services.price_importer import import_price
from app.services.sales_importer import import_sales,client_identity
from app.services.workbook_reader import read_workbook
from app.utils.input_values import nonnegative_number,percentage,percent_text,percent_input
from app.ui.main_window import MainWindow,ERRORS
from app.ui.common import page_layout,fill_table,money,number
from app.ui.workspace_pages import DataPage,MappingPage,AdminPanel,ColumnMappingDialog,button

WORKSPACE_ERRORS=(*ERRORS,PermissionError,sqlite3.Error,OSError)


class WorkbookWorker(QThread):
    ready=Signal(object)
    failed=Signal(str)

    def __init__(self,path,year,kind,mappings=None,parent=None):
        super().__init__(parent);self.path=path;self.year=year;self.kind=kind;self.mappings=mappings

    def run(self):
        try:
            if self.kind=='price':
                self.ready.emit(('price',import_price(self.path),None))
            else:
                imported=import_sales(self.path,self.year,column_mappings=self.mappings)
                preview=read_workbook(self.path) if imported.column_requests else None
                self.ready.emit(('sales',imported,preview))
        except Exception as exc:
            self.failed.emit(str(exc))


class WorkspaceWindow(MainWindow):
    repository_class=WorkspaceRepository

    def __init__(self,database_path=None):
        super().__init__(database_path)
        self.workflow=WorkspaceService(self.repository)
        self.workspace_saved=None;self.pending_columns=None;self._column_mappings={}
        self._pending_parse=None;self._refreshing=False
        for i in range(7):self.navigation.item(i).setHidden(True)
        self.navigation.item(7).setText('Старая история')
        self.home=QWidget();body=page_layout(self.home,'Новый расчет',
            'Общий файл продаж и отдельный внешний прайс. После импорта выберите сотрудника и год.')
        top=QHBoxLayout();body.addLayout(top)
        self.sales_button=button('Импорт общего файла продаж',self.choose_sales,top)
        button('Сопоставление товаров',lambda:self.navigate(9),top)
        self.price_label=QLabel();self.price_label.setWordWrap(True);body.addWidget(self.price_label)
        self.source_label=QLabel();self.source_label.setWordWrap(True);body.addWidget(self.source_label)
        self.columns_button=button('Подтвердить структуру листа (администратор)',self.resolve_columns,body)
        form=QFormLayout();body.addLayout(form)
        self.employee=QComboBox();form.addRow('Сотрудник',self.employee)
        self.year=QSpinBox();self.year.setRange(1900,2100);self.year.setValue(datetime.now().year);form.addRow('Год',self.year)
        self.role_label=QLabel();form.addRow('Должность на год',self.role_label)
        self.plan_inputs=[];self.call_inputs=[]
        self.calls_box=QGroupBox('Звонки · менеджер сопровождения');calls=QFormLayout(self.calls_box)
        self.calls_plan_labels=[]
        for q in range(1,5):
            edit=QLineEdit();edit.setPlaceholderText('Обязательный план');self.plan_inputs.append(edit);form.addRow(f'План Q{q}, ₽',edit)
            fact=QLineEdit('0');self.call_inputs.append(fact);calls.addRow(f'Факт звонков Q{q}',fact)
            label=QLabel();self.calls_plan_labels.append(label);calls.addRow(f'План звонков Q{q}',label)
        body.addWidget(self.calls_box)
        actions=QHBoxLayout();body.addLayout(actions)
        button('Сохранить планы и звонки',self.save_workspace_inputs,actions)
        self.calculate_button=button('Рассчитать премию',self.calculate_workspace,actions);self.calculate_button.setObjectName('primary')
        self.readiness=QLabel();self.readiness.setWordWrap(True);body.addWidget(self.readiness);body.addStretch()
        self.mappings=MappingPage(self)
        self.shipments=DataPage('Сформированные отгрузки',[
            'Месяц','Квартал','Клиент','Тип','Продукт из файла','Канонический продукт','Группа',
            'Количество','Выручка','Цена','Порог','Eligible','Лист','Строка','Контракт','Причина исключения'])
        self.returns=DataPage('Возвраты',['Месяц','Сотрудник','Клиент','Продукт','Сумма','Количество',
            'Исходная отгрузка','Исходный квартал','Eligible','Оплачен?','Clawback','Остаток суммы','Остаток количества','Статус'])
        self.reconcile_button=button('Выбрать исходную отгрузку (администратор)',self.reconcile_return,self.returns.body)
        self.premiums=DataPage('Расчет премии',['Квартал','План','Продажи до возвратов','Возвраты','Факт после возвратов',
            'Выполнение','Блок 1','Блок 2','Блок 3','Блок 4','Звонки план','Звонки факт','Звонки премия',
            'Расчетная премия','Корректировка премии за возвраты прошлых периодов','Перенос входящий','Перенос остаток','К выплате','Выплачено','Статус'])
        payment=QHBoxLayout();self.premiums.body.addLayout(payment)
        self.payment_quarter=QComboBox()
        for q in range(1,5):self.payment_quarter.addItem(f'Q{q}',q)
        payment.addWidget(self.payment_quarter);self.payment_amount=QLineEdit();payment.addWidget(self.payment_amount)
        self.payment_button=button('Подтвердить выплату',self.ask_confirm_payment,payment)
        self.payment_quarter.currentIndexChanged.connect(self.refresh_payment_amount)
        self.admin=AdminPanel(self)
        self.workspace_history=DataPage('История расчетов',['ID','Сотрудник','Год','Должность','Статус','Дата'])
        self.workspace_history.grid.cellDoubleClicked.connect(lambda *_:self.open_workspace_history(self.workspace_history.selected_key()))
        for title,page in zip(('Новый расчет','Сопоставление','Отгрузки','Возвраты','Премия','Администратор','История'),
                              (self.home,self.mappings,self.shipments,self.returns,self.premiums,self.admin,self.workspace_history)):
            if page in (self.home,self.admin):
                scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(page);self.stack.addWidget(scroll)
            else:self.stack.addWidget(page)
            self.navigation.addItem(title)
        self.employee.currentIndexChanged.connect(self.refresh_workspace)
        self.year.valueChanged.connect(self.refresh_workspace)
        self.refresh_employees()
        prefs=dict(self.repository.connection.execute("SELECT key,value FROM admin_settings WHERE key IN ('last_employee','last_year')"))
        self.year.setValue(int(prefs.get('last_year',self.year.value())))
        if 'last_employee' in prefs:self.employee.setCurrentIndex(max(0,self.employee.findData(int(prefs['last_employee']))))
        self.refresh_workspace();self.refresh_admin();self.load_policy_fields()
        self.navigate(8)

    def safe(self,action):
        try:
            self._clear_error();return action()
        except WORKSPACE_ERRORS as exc:
            self.report_error(str(exc));return None

    def refresh_employees(self):
        selected=self.employee.currentData();admin_selected=self.admin.employees.currentData()
        for combo in (self.employee,self.admin.employees,self.admin.export_employee):combo.blockSignals(True);combo.clear()
        self.admin.export_employee.addItem('Все сотрудники',None)
        for employee in self.repository.employees(include_inactive=True):
            if employee.active:self.employee.addItem(employee.name,employee.id)
            self.admin.employees.addItem(employee.name+(' (неактивен)' if not employee.active else ''),employee.id)
            self.admin.export_employee.addItem(employee.name,employee.id)
        self.employee.setCurrentIndex(max(0,self.employee.findData(selected)))
        self.admin.employees.setCurrentIndex(max(0,self.admin.employees.findData(admin_selected)))
        for combo in (self.employee,self.admin.employees,self.admin.export_employee):combo.blockSignals(False)
        self.admin_employee_selected()

    def refresh_workspace(self,*_):
        if self._refreshing:return
        self._refreshing=True
        try:
            eid=self.employee.currentData();year=self.year.value()
            profile=self.repository.year_profile(eid,year);role=self.repository.year_role(eid,year)
            self.role_label.setText({'KAM':'КАМ','SUPPORT':'Менеджер сопровождения'}.get(role,'Не задана — обратитесь к администратору'))
            self.calls_box.setVisible(role=='SUPPORT')
            payments={p.quarter:p for p in self.repository.payments(eid,year)} if eid else {}
            for i in range(4):
                self.plan_inputs[i].setText(number(profile.plans.values[i]) if profile else '')
                self.plan_inputs[i].setReadOnly(i+1 in payments)
                self.call_inputs[i].setText(number(profile.calls_facts[i]) if profile else '0')
                self.call_inputs[i].setReadOnly(i+1 in payments)
                self.calls_plan_labels[i].setText(number(profile.calls_plans[i]) if profile else '750')
            if eid:
                with self.repository.connection:
                    self.repository.connection.executemany('INSERT OR REPLACE INTO admin_settings VALUES (?,?)',
                        [('last_employee',str(eid)),('last_year',str(year))])
            self.refresh_sources()
            history=self.repository.workspace_history(eid,year)
            self.workspace_history.set_rows([list(r) for r in history],[r[0] for r in history])
            if history:self.display_workspace(self.repository.workspace_snapshot(history[0][0]))
            else:
                self.workspace_saved=None
                for page in (self.shipments,self.returns,self.premiums):page.set_rows([])
                self.payment_button.setEnabled(False)
            self.refresh_history()
        finally:self._refreshing=False

    def refresh_sources(self):
        price=self.repository.price_version();active=self.repository.latest_sales(self.year.value())
        self.price_label.setText(f'Активный прайс: v{price.id} · {Path(price.source_file).name} · {price.imported_at}' if price else 'Внешний прайс еще не импортирован администратором')
        imported=active[1] if active else None
        self.source_label.setText(f'Продажи: {Path(imported.source_file).name} · отгрузок {len(imported.shipments)} · возвратов {len(imported.returns)} · исключено «тендер»: {imported.excluded_tender_rows}' if imported else 'Общий файл продаж еще не импортирован для этого года')
        unknown=self.workflow.unknown_managers(imported) if imported else ()
        self.admin.unknown.setText('Неизвестные менеджеры: '+', '.join(unknown) if unknown else 'Все менеджеры сопоставлены.')
        self.readiness.setText('Для расчета нужны внешний прайс, продажи, должность и четыре плана. Неизвестные менеджеры и товары требуют явного решения.'+ (' Требуют сопоставления: '+', '.join(unknown) if unknown else ''))
        if self.pending_columns:
            self.readiness.setText('Новый файл требует подтверждения структуры: '+', '.join(self.pending_columns[0].column_requests)+'. До подтверждения он не сохранен.')
        self.refresh_mappings()
        self.columns_button.setVisible(self.pending_columns is not None)

    def refresh_mappings(self):
        entries=self.workflow.product_mappings(self.employee.currentData(),self.year.value())
        self.mappings.set_rows([[raw,p.canonical_name if p else '',p.category if p else '',kind or 'Требует подтверждения'] for raw,p,kind in entries],[raw for raw,_,_ in entries])
        self.mappings.products.clear();self.mappings.products.addItem('Выберите продукт прайса',None)
        price=self.repository.price_version()
        if price:
            for p in price.products:self.mappings.products.addItem(f'{p.canonical_name} · {p.category}',p.id)

    def choose_sales(self):
        path,_=QFileDialog.getOpenFileName(self,'Общий файл продаж','','Excel (*.xlsx *.xlsm)')
        if path:self.begin_sales_import(path)

    def choose_price(self):
        path,_=QFileDialog.getOpenFileName(self,'Отдельный прайс-лист','','Excel (*.xlsx *.xlsm)')
        if path:self.begin_price_import(path)

    def begin_sales_import(self,path,mappings=None):
        def start():
            if mappings:self.workflow.auth.require()
            self._start_parse(path,'sales',mappings)
        self.safe(start)

    def begin_price_import(self,path):
        def start():
            self.workflow.auth.require();self._start_parse(path,'price')
        self.safe(start)

    def _start_parse(self,path,kind,mappings=None):
        if self._worker is not None:return
        self.sales_button.setEnabled(False);self.calculate_button.setEnabled(False)
        self.statusBar().showMessage('Чтение Excel…')
        worker=WorkbookWorker(path,self.year.value(),kind,mappings,self);self._worker=worker
        worker.ready.connect(self.accept_parsed);worker.failed.connect(self.report_error)
        worker.finished.connect(self.parse_finished);worker.finished.connect(worker.deleteLater);worker.start()

    def accept_parsed(self,payload):
        if self._closing:return
        def accept():
            kind,data,preview=payload
            if kind=='price':
                self.workflow.auth.require();self.repository.save_price(*data)
            else:
                if self._worker.mappings:self.workflow.auth.require()
                if data.column_requests:
                    self.pending_columns=(data,preview);self._column_mappings={layout.name:layout for layout in data.layouts}
                elif data.errors:
                    raise ValueError('\n'.join(f'{i.sheet or ""} {i.source_row or ""}: {i.message}' for i in data.errors[:20]))
                else:
                    self.repository.save_sales(data);self.pending_columns=None
                    self.year.setValue(data.year)
            # Keep unsaved plans while importing; selection changes reload persisted inputs.
            self.refresh_sources();self.refresh_admin()
        self.safe(accept)

    def parse_finished(self):
        self._worker=None
        if not self._closing:
            self.sales_button.setEnabled(True);self.calculate_button.setEnabled(True)
            if not self.error_label.isVisible():self.statusBar().showMessage('Файл проверен',5000)

    def resolve_columns(self):
        def resolve():
            self.workflow.auth.require()
            if not self.pending_columns:return
            imported,preview=self.pending_columns
            mappings=dict(self._column_mappings)
            for name in imported.column_requests:
                dialog=ColumnMappingDialog(name,preview.sheets[name],self)
                if dialog.exec()!=QDialog.DialogCode.Accepted:return
                mappings[name]=dialog.layout_value()
            self.begin_sales_import(imported.source_file,mappings)
        self.safe(resolve)

    def apply_product_mapping(self):
        def apply():
            raw=self.mappings.selected_key();pid=self.mappings.products.currentData()
            if raw is None or pid is None:raise ValueError('Выберите строку и продукт прайса')
            self.workflow.map_product(raw,pid);self.refresh_mappings()
        self.safe(apply)

    def leave_product_mapping(self):
        def leave():
            raw=self.mappings.selected_key()
            if raw is None:raise ValueError('Выберите строку товара')
            self.workflow.leave_product_unmatched(raw);self.refresh_mappings()
        self.safe(leave)

    def _save_inputs(self):
        eid=self.employee.currentData();year=self.year.value()
        return self.workflow.save_inputs(eid,year,Plans(*(nonnegative_number(edit.text()) for edit in self.plan_inputs)),
            calls_facts=tuple(nonnegative_number(edit.text()) for edit in self.call_inputs))

    def save_workspace_inputs(self):
        self.safe(self._save_inputs)

    def calculate_workspace(self):
        def calculate():
            self._save_inputs()
            saved=self.workflow.calculate(self.employee.currentData(),self.year.value())
            self.display_workspace(saved);self.refresh_history()
            history=self.repository.workspace_history(self.employee.currentData(),self.year.value())
            self.workspace_history.set_rows([list(r) for r in history],[r[0] for r in history]);self.navigate(12)
        self.safe(calculate)

    def display_workspace(self,snapshot):
        self.workspace_saved=snapshot;calc=snapshot.calculation
        self.context.setText(f'{snapshot.employee.name} · {snapshot.profile.year} · {snapshot.profile.role} · расчет №{snapshot.id} · прайс v{snapshot.price.id} · {snapshot.status}')
        self.shipments.set_rows([[f'{e.event.year}-{e.event.month:02}',f'Q{e.event.quarter}',e.audit.shipment.client,e.audit.client_type,
            e.event.product_raw,e.audit.product.canonical_name if e.audit.product else '',e.audit.product.category if e.audit.product else '',
            number(e.event.quantity),money(e.event.revenue),number(e.audit.actual_price_rounded),number(e.audit.threshold_rounded),
            'Да' if e.audit.eligible else 'Нет',e.event.source_sheet,e.event.source_row,e.event.contract,e.audit.exclusion_reason or '']
            for e in calc.event_audit],[e.event.event_id for e in calc.event_audit])
        rows=[];keys=[]
        for review in calc.return_reviews:
            event=review.event;allocations=[a for a in calc.allocations if a.return_id==event.event_id]
            for a in allocations or [None]:
                rows.append([f'{event.year}-{event.month:02}',snapshot.employee.name,client_identity(event.db,event.lpu)[1],
                    a.product.canonical_name if a and a.product else event.product_raw,money(event.revenue),number(event.quantity),
                    a.shipment_id if a else '',f'{a.original_year} Q{a.original_quarter}' if a else '',
                    'Да' if a and a.eligible else 'Нет','Да' if a and a.paid else 'Нет',money(a.clawback) if a else '0',
                    money(review.remaining_revenue),number(review.remaining_quantity),review.status]);keys.append(event.event_id)
        self.returns.set_rows(rows,keys)
        payments={p.quarter:p for p in self.repository.payments(snapshot.employee.id,snapshot.profile.year)}
        self.premiums.set_rows([[f'Q{p.quarter}',money(q.plan),money(p.positive_sales),money(p.returns),money(p.actual),percent_text(q.achievement),
            *(f'База {money(b.base)}; ставка {percent_text(b.rate)}; премия {money(b.premium)}' for b in q.blocks),number(p.calls_plan),number(p.calls_fact),money(p.calls_bonus),money(p.calculated_premium),
            money(p.clawback),money(p.carried_in),money(p.carried_out),money(p.payable),money(payments[p.quarter].amount if p.quarter in payments else p.paid),
            'paid/closed' if p.quarter in payments else p.status] for p,q in zip(calc.quarters,calc.result.quarters)])
        self.payment_button.setEnabled(snapshot.status!='paid/closed');self.refresh_payment_amount()

    def refresh_payment_amount(self,*_):
        if self.workspace_saved:
            self.payment_amount.setText(number(self.workspace_saved.calculation.quarters[self.payment_quarter.currentIndex()].payable))

    def ask_confirm_payment(self):
        if not self.workspace_saved:return
        quarter=self.payment_quarter.currentData()
        def confirm():
            amount=nonnegative_number(self.payment_amount.text())
            if QMessageBox.question(self,'Подтверждение выплаты',f'Зафиксировать Q{quarter}: {money(amount)} ₽? После подтверждения расчет закрывается.')==QMessageBox.StandardButton.Yes:
                self.confirm_payment(quarter,amount)
        self.safe(confirm)

    def confirm_payment(self,quarter,amount=None):
        def confirm():
            if not self.workspace_saved:raise ValueError('Сначала выполните расчет')
            self.workflow.confirm_payment(self.workspace_saved.id,quarter,amount);self.refresh_workspace()
        return self.safe(confirm)

    def open_workspace_history(self,cid):
        if cid is not None:self.safe(lambda:(self.display_workspace(self.repository.workspace_snapshot(cid)),self.navigate(12)))

    def open_history(self,cid):
        row=self.repository.connection.execute('SELECT workspace_snapshot_json FROM calculations WHERE id=?',(cid,)).fetchone()
        if row and row[0]:self.open_workspace_history(cid)
        else:super().open_history(cid)

    def reconcile_return(self):
        def reconcile():
            self.workflow.auth.require();rid=self.returns.selected_key()
            if rid is None or self.workspace_saved is None:raise ValueError('Выберите возврат')
            from app.services.snapshot_service import load_object
            records=self.repository.connection.execute('SELECT event_id,payload_json FROM shipment_events').fetchall()
            events=[(sid,load_object(payload)) for sid,payload in records]
            events=[(sid,e) for sid,e in events if self.repository.resolve_employee(e.manager) and self.repository.resolve_employee(e.manager).id==self.workspace_saved.employee.id]
            choices=[f'{e.year}-{e.month:02} · {e.contract} · {e.product_raw} · {money(e.revenue)} · {sid}' for sid,e in events]
            if not choices:raise ValueError('Нет исходных отгрузок')
            selected,ok=QInputDialog.getItem(self,'Сверка возврата','Исходная отгрузка',choices,0,False)
            if ok:
                self.workflow.reconcile_manually(rid,events[choices.index(selected)][0],self.workspace_saved.employee.id)
                self.calculate_workspace()
        self.safe(reconcile)

    def refresh_admin(self):
        active=self.workflow.is_admin;self.admin.tabs.setEnabled(active)
        self.admin.logout.setEnabled(active);self.admin.login.setEnabled(not active)
        self.admin.state.setText('Администратор' if active else 'Обычный пользователь')
        price=self.repository.price_version()
        self.admin.price_info.setText(self.price_label.text())
        fill_table(self.admin.price_grid,[[p.canonical_name,p.category,number(p.price_lpu),number(p.price_distributor)] for p in price.products] if price else [])
        self.reconcile_button.setEnabled(active)

    def admin_login(self):
        password=self.admin.password.text();self.admin.password.clear()
        if not self.workflow.login_admin(password):self.report_error('Неверный пароль администратора')
        else:self._clear_error()
        self.refresh_admin()

    def admin_logout(self):
        self.workflow.logout_admin();self.refresh_admin()

    def admin_employee_selected(self,*_):
        eid=self.admin.employees.currentData()
        employee=next((e for e in self.repository.employees(True) if e.id==eid),None)
        if employee:self.admin.full_name.setText(employee.name);self.admin.active.setChecked(employee.active)

    def add_employee(self):
        def add():
            employee=self.workflow.add_employee(self.admin.full_name.text())
            if self.admin.alias.text().strip():self.workflow.map_employee(self.admin.alias.text(),employee.id)
            self.refresh_employees();self.refresh_sources()
        self.safe(add)

    def edit_employee(self):
        self.safe(lambda:(self.workflow.update_employee(self.admin.employees.currentData(),self.admin.full_name.text(),self.admin.active.isChecked()),self.refresh_employees(),self.refresh_workspace()))

    def map_employee(self):
        self.safe(lambda:(self.workflow.map_employee(self.admin.alias.text(),self.admin.employees.currentData()),self.refresh_sources()))

    def save_employee_role(self):
        self.safe(lambda:(self.workflow.set_role(self.admin.employees.currentData(),self.admin.year.value(),self.admin.role.currentData()),self.refresh_workspace()))

    def load_policy_fields(self,*_):
        policy=self.repository.role_policy(self.admin.policy_role.currentText())
        values=[percent_input(policy.rules.plan_gate),*(percent_input(rate) for rate in policy.rules.rates),
                percent_input(policy.calls_gate),number(policy.calls_max),number(policy.default_calls_plan)]
        for edit,value in zip(self.admin.policy_fields,values):edit.setText(value)

    def save_role_policy(self):
        def save():
            text=[e.text() for e in self.admin.policy_fields];role=self.admin.policy_role.currentText()
            self.workflow.save_policy(RolePolicy(role,Rules(*(percentage(t) for t in text[:5])),role=='SUPPORT',percentage(text[5]),nonnegative_number(text[6]),nonnegative_number(text[7])))
        self.safe(save)

    def change_admin_password(self):
        def change():
            if self.admin.new_password.text()!=self.admin.repeat_password.text():raise ValueError('Новые пароли не совпадают')
            self.workflow.change_password(self.admin.old_password.text(),self.admin.new_password.text())
            for edit in (self.admin.old_password,self.admin.new_password,self.admin.repeat_password):edit.clear()
            self.statusBar().showMessage('Пароль изменен',5000)
        self.safe(change)

    def choose_export(self):
        def export():
            self.workflow.auth.require()
            path,_=QFileDialog.getSaveFileName(self,'Экспорт KPI','KPI_KAM_export.xlsx','Excel (*.xlsx)')
            if path:self.workflow.export_data(path,self.admin.export_year.value(),employee_id=self.admin.export_employee.currentData(),quarter=self.admin.export_quarter.currentData())
        self.safe(export)

    def reset_history(self):
        def reset():
            self.workflow.auth.require()
            if QMessageBox.question(self,'Сброс истории','Удалить расчеты, выплаты, импорты продаж и учет возвратов?')!=QMessageBox.StandardButton.Yes:return
            if QMessageBox.warning(self,'Повторное подтверждение','Подтвердите удаление истории. Перед удалением будет создан backup БД.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
            backup=self.workflow.reset_history(confirmed=True,confirmed_again=True)
            self.pending_columns=None;self.refresh_workspace();self.refresh_admin()
            self.statusBar().showMessage(f'История обнулена. Backup: {backup}')
        self.safe(reset)

    def closeEvent(self,event):
        if hasattr(self,'workflow'):self.workflow.logout_admin()
        super().closeEvent(event)
