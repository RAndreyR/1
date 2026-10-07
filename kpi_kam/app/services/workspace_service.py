"""Authorization and durable workflow; widgets never calculate financial values."""
from dataclasses import replace
from decimal import Decimal
from app.models.domain import ZERO
from app.services.admin_auth import AdminAuth
from app.services.price_importer import import_price
from app.services.sales_importer import import_sales
from app.services.event_calculation import calculate_events
from app.services.product_matcher import ProductMatcher
from app.utils.normalization import CalculationInputError, normalize_text
from app.utils.packaging import product_mapping_key,packaging_compatible


class WorkspaceService:
    def __init__(self,repository):
        self.repository=repository
        self.auth=AdminAuth(repository)

    @property
    def is_admin(self):
        return self.auth.authenticated

    def login_admin(self,password):
        return self.auth.login(password)

    def logout_admin(self):
        self.auth.logout()

    def change_password(self,current,new):
        self.auth.change_password(current,new)

    def add_employee(self,name):
        self.auth.require()
        return self.repository.add_employee(name)

    def update_employee(self,eid,name,active):
        self.auth.require();self.repository.update_employee(eid,name,active)

    def map_employee(self,raw,eid):
        self.auth.require();self.repository.employee_alias(raw,eid)

    def set_role(self,eid,year,role):
        self.auth.require();self.repository.set_year_role(eid,year,role)

    def save_policy(self,policy):
        self.auth.require();self.repository.save_role_policy(policy)

    def import_price(self,path):
        self.auth.require()
        data,products=import_price(path)
        return self.repository.save_price(data,products)

    def import_sales(self,path,year,*,column_mappings=None):
        if column_mappings:
            self.auth.require()
        imported=import_sales(path,year,column_mappings=column_mappings)
        if not imported.errors:
            self.repository.save_sales(imported)
            imported=self.repository.latest_sales(year)[1]
        return imported

    def unknown_managers(self,imported):
        return tuple(sorted({e.manager for e in (*imported.shipments,*imported.returns)
                             if self.repository.resolve_employee(e.manager) is None}))

    def product_mappings(self,employee_id,year):
        active=self.repository.latest_sales(year);price=self.repository.price_version()
        if not active or not price:
            return ()
        aliases={k:v for k,v in self.repository.aliases().items() if v in {p.id for p in price.products}}
        matcher=ProductMatcher(price.products,aliases)
        names=set()
        payments=self.repository.payments(employee_id)
        frozen={a.event.event_id:a for p in payments for a in p.snapshot.calculation.event_audit if a.event.quarter==p.quarter}
        frozen_lines={a.event.line_key:a for a in frozen.values()}
        require_current=set();historical={}
        for e in (*active[1].shipments,*active[1].returns):
            employee=self.repository.resolve_employee(e.manager)
            if employee and employee.id==employee_id:
                key=product_mapping_key(e.product_raw,e.packaging)
                names.add(key)
                source=frozen.get(e.event_id) or (frozen_lines.get(e.line_key) if e in active[1].returns else None)
                if source:
                    historical[key]=source.audit.product
                else:
                    require_current.add(key)
        left={r[0] for r in self.repository.connection.execute('SELECT alias_normalized FROM product_mapping_decisions WHERE price_version_id=?',(price.id,))}
        results=[]
        for raw in sorted(names):
            if raw not in require_current and raw in historical:
                results.append((raw,historical[raw],'historical'))
                continue
            product,kind=matcher.match(raw)
            results.append((raw,product,kind if product else 'left_unmatched' if normalize_text(raw) in left else None))
        return tuple(results)

    def map_product(self,raw,pid,*,packaging=''):
        raw=product_mapping_key(raw,packaging)
        price=self.repository.price_version()
        if price is None or pid not in {p.id for p in price.products}:
            raise CalculationInputError('Выберите товар из активного внешнего прайса')
        product=next(p for p in price.products if p.id==pid)
        if not packaging_compatible(raw,product.canonical_name):
            raise CalculationInputError('Фасовка из продаж не соответствует фасовке продукта прайса')
        matched,kind=ProductMatcher(price.products).match(raw)
        if kind=='exact' and matched.id!=pid:
            raise CalculationInputError('Точное совпадение имеет приоритет')
        self.repository.remember(raw,pid)

    def leave_product_unmatched(self,raw,*,packaging=''):
        raw=product_mapping_key(raw,packaging)
        price=self.repository.price_version()
        if price is None:
            raise CalculationInputError('Сначала импортируйте внешний прайс')
        product,kind=ProductMatcher(price.products).match(raw)
        if kind=='exact':
            raise CalculationInputError('Точное совпадение нельзя исключить')
        with self.repository.connection:
            self.repository.connection.execute("INSERT OR REPLACE INTO product_mapping_decisions VALUES (?,?,'unmatched')",(normalize_text(raw),price.id))
            self.repository.connection.execute('DELETE FROM product_aliases WHERE alias_normalized=?',(normalize_text(raw),))

    def save_inputs(self,eid,year,plans,*,calls_facts=None):
        role=self.repository.year_role(eid,year)
        if not role:
            raise CalculationInputError('Администратор должен задать должность для сотрудника и года')
        for payment in self.repository.payments(eid,year):
            q=payment.quarter-1
            if plans.values[q]!=payment.snapshot.profile.plans.values[q]:
                raise CalculationInputError('План оплаченного квартала зафиксирован')
            if calls_facts is not None and Decimal(calls_facts[q])!=payment.snapshot.profile.calls_facts[q]:
                raise CalculationInputError('Факт звонков оплаченного квартала зафиксирован')
        return self.repository.save_year_profile(eid,year,role,plans,calls_facts=calls_facts)

    def reconcile_manually(self,return_id,shipment_id,employee_id):
        self.auth.require()
        connection=self.repository.connection
        from app.services.snapshot_service import load_object
        ret=connection.execute('SELECT payload_json FROM return_events WHERE event_id=?',(return_id,)).fetchone()
        source=connection.execute('SELECT payload_json FROM shipment_events WHERE event_id=?',(shipment_id,)).fetchone()
        if not ret or not source:
            raise CalculationInputError('Не найдены возврат или исходная отгрузка')
        ret,source=load_object(ret[0]),load_object(source[0])
        for event in (ret,source):
            employee=self.repository.resolve_employee(event.manager)
            if not employee or employee.id!=employee_id:
                raise CalculationInputError('Нельзя распределить возврат на другого сотрудника')
        if (source.year,source.month)>(ret.year,ret.month):
            raise CalculationInputError('Исходная отгрузка должна предшествовать возврату')
        price=self.repository.price_version()
        aliases={k:v for k,v in self.repository.aliases().items() if v in {p.id for p in price.products}}
        matcher=ProductMatcher(price.products,aliases)
        rp,_=matcher.match(ret.product_raw,ret.packaging);sp,_=matcher.match(source.product_raw,source.packaging)
        if not rp or not sp or rp.id!=sp.id:
            raise CalculationInputError('Возврат и продажа должны относиться к одному каноническому продукту')
        self.repository.save_manual_link(return_id,shipment_id)

    def calculate(self,eid,year):
        repo=self.repository
        employee=next((e for e in repo.employees() if e.id==eid),None)
        profile=repo.year_profile(eid,year);price=repo.price_version();active=repo.latest_sales(year)
        if not employee or not profile or not price or not active:
            raise CalculationInputError('Нужны активный сотрудник, должность/планы, продажи и внешний прайс')
        unknown=self.unknown_managers(active[1])
        if unknown:
            raise CalculationInputError('Неизвестные менеджеры требуют решения администратора: '+', '.join(unknown))
        if any(product is None and kind not in ('left_unmatched','historical') for _,product,kind in self.product_mappings(eid,year)):
            raise CalculationInputError('Подтвердите все сопоставления товаров')
        selected=lambda e:repo.resolve_employee(e.manager).id==eid
        events=tuple(e for e in active[1].shipments if selected(e));returns=tuple(e for e in active[1].returns if selected(e))
        payments=repo.payments(eid)
        previous=repo.allocations()
        past={}
        for item in repo.workspace_history(eid):
            if item[2]<year and item[2] not in past:
                past[item[2]]=repo.workspace_snapshot(item[0])
        prior_audits=tuple(a for old in past.values() for a in old.calculation.event_audit)
        foreign_basis=tuple(a for a in previous if a.original_year==year and not a.paid
                            and repo.connection.execute('SELECT year FROM return_events WHERE event_id=?',(a.return_id,)).fetchone()[0]>year)
        aliases={k:v for k,v in repo.aliases().items() if v in {p.id for p in price.products}}
        earlier=[p for p in payments if p.year<year]
        opening=earlier[-1].snapshot.calculation.quarters[earlier[-1].quarter-1].carried_out if earlier else ZERO
        scheduled={q:ZERO for q in range(1,5)}
        current_return_ids={r.event_id for r in returns}
        for rid,q,amount in repo.connection.execute('SELECT return_id,target_quarter,amount FROM clawback_ledger WHERE employee_id=? AND target_year=?',(eid,year)):
            if rid not in current_return_ids:
                # Earlier-year returns may have been scheduled to this year.
                source_year=repo.connection.execute('SELECT year FROM return_events WHERE event_id=?',(rid,)).fetchone()[0]
                if source_year==year:
                    raise CalculationInputError('Изменен ранее учтенный возврат: требуется сверка')
                scheduled[q]+=Decimal(amount)
        calculation=calculate_events(events,returns,price.products,profile.plans,repo.role_policy(profile.role),
            aliases=aliases,calls_plans=profile.calls_plans,calls_facts=profile.calls_facts,
            paid_records=payments,previous_allocations=previous,manual_links=repo.manual_links(),
            opening_balance=opening,scheduled_clawbacks=scheduled,year=year,
            prior_audits=prior_audits,basis_adjustments=foreign_basis)
        corrections=[]
        paid_periods={(p.year,p.quarter) for p in payments}
        affected={a.original_year for a in calculation.allocations if not a.paid and a.original_year<year
                  and (a.original_year,a.original_quarter) not in paid_periods}
        all_parts={(a.return_id,a.shipment_id):a for a in (*previous,*calculation.allocations)}
        for old_year in sorted(affected):
            old=past[old_year]
            old_calc=calculate_events(tuple(a.event for a in old.calculation.event_audit),old.calculation.returns,
                old.price.products,old.profile.plans,old.policy,aliases=aliases,
                calls_plans=old.profile.calls_plans,calls_facts=old.profile.calls_facts,
                paid_records=payments,previous_allocations=tuple(all_parts.values()),manual_links=repo.manual_links(),
                opening_balance=old.calculation.quarters[0].carried_in,year=old_year,
                prior_audits=prior_audits,basis_adjustments=tuple(all_parts.values()))
            if old_calc!=old.calculation:corrections.append((old,old_calc))
        # New year and the draft corrections to earlier years commit together.
        with repo.connection:
            for old,old_calc in corrections:
                repo.save_workspace_calculation(old.employee,old.profile,old.policy,old.price,old.sales_import_id,old_calc,transaction=False)
            return repo.save_workspace_calculation(employee,profile,repo.role_policy(profile.role),price,active[0],calculation,transaction=False)

    def confirm_payment(self,calculation_id,quarter,paid_amount=None):
        if type(quarter) is not int or quarter not in (1,2,3,4):
            raise CalculationInputError('Выберите квартал Q1–Q4')
        snapshot=self.repository.workspace_snapshot(calculation_id)
        active=self.repository.latest_sales(snapshot.profile.year)
        if not active or active[0]!=snapshot.sales_import_id or self.repository.year_profile(snapshot.employee.id,snapshot.profile.year)!=snapshot.profile or self.repository.price_version().id!=snapshot.price.id or self.repository.role_policy(snapshot.profile.role)!=snapshot.policy:
            raise CalculationInputError('Данные изменились после расчета. Сначала выполните новый расчет')
        amount=snapshot.calculation.quarters[quarter-1].payable if paid_amount is None else paid_amount
        return self.repository.confirm_payment(snapshot,quarter,amount)

    def reset_history(self,*,confirmed=False,confirmed_again=False):
        self.auth.require()
        if not confirmed or not confirmed_again:
            raise CalculationInputError('Нужны два подтверждения сброса истории')
        return self.repository.reset_history()

    def export_data(self,path,year,*,employee_id=None,quarter=None):
        self.auth.require()
        from app.services.workspace_export import export_workspace
        return export_workspace(self.repository,path,year,employee_id,quarter)
