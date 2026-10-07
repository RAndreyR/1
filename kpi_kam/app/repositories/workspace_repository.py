"""Versioned common-sales storage additive to the application's existing DB."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from contextlib import nullcontext
from app.models.domain import Plans, Product, ZERO
from app.models.events import Employee, YearProfile, RolePolicy, PriceVersion, PaymentRecord, WorkspaceSnapshot
from app.models.import_data import ExcelImport
from app.repositories.application_repository import ApplicationRepository
from app.repositories.alias_repository import default_database_path
from app.repositories.migrations import backup_before_migration, migrate, backup_database
from app.services.snapshot_service import dump_object, load_object, dump_snapshot
from app.utils.normalization import normalize_text, CalculationInputError, decimal_value
from app.utils.packaging import product_mapping_key,name_packaging

INITIAL_EMPLOYEES=('Пермякова Анна','Трофимов Дмитрий','Погорельцева Елена',
                   'Долгополова Мария','Гайдина Юлия','Петрова Елена','Филимонова Анна')


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkspaceRepository(ApplicationRepository):
    def __init__(self,path=None):
        self.migration_backup=backup_before_migration(path or default_database_path())
        super().__init__(path)
        try:
            migrate(self.connection)
            with self.connection:
                for policy in (RolePolicy.kam(),RolePolicy.support()):
                    self.connection.execute('INSERT OR IGNORE INTO role_templates VALUES (?,?)',(policy.role,dump_object(policy)))
                for name in INITIAL_EMPLOYEES:
                    self.connection.execute('INSERT OR IGNORE INTO employees(name,normalized_name) VALUES (?,?)',(name,normalize_text(name)))
                    eid=self.connection.execute('SELECT id FROM employees WHERE normalized_name=?',(normalize_text(name),)).fetchone()[0]
                    self.connection.execute('INSERT OR IGNORE INTO employee_aliases VALUES (?,?)',(normalize_text(name.split()[0]),eid))
        except Exception:
            self.close()
            raise

    def employees(self,include_inactive=False):
        sql='SELECT id,name,active FROM employees'+('' if include_inactive else ' WHERE active=1')+' ORDER BY name'
        return tuple(Employee(r[0],r[1],bool(r[2])) for r in self.connection.execute(sql))

    def resolve_employee(self,raw):
        key=normalize_text(raw)
        rows=self.connection.execute('''SELECT e.id,e.name,e.active FROM employees e WHERE e.normalized_name=?
            UNION SELECT e.id,e.name,e.active FROM employees e JOIN employee_aliases a ON e.id=a.employee_id WHERE a.alias_normalized=?''',(key,key)).fetchall()
        if len(rows)>1:
            raise CalculationInputError('Неоднозначное сопоставление сотрудника')
        return Employee(rows[0][0],rows[0][1],bool(rows[0][2])) if rows else None

    def add_employee(self,name):
        name=' '.join(name.split())
        if not name:
            raise CalculationInputError('Укажите ФИО')
        with self.connection:
            cursor=self.connection.execute('INSERT INTO employees(name,normalized_name) VALUES (?,?)',(name,normalize_text(name)))
        return Employee(cursor.lastrowid,name)

    def update_employee(self,employee_id,name,active=True):
        if not normalize_text(name):
            raise CalculationInputError('Укажите ФИО')
        with self.connection:
            old=self.connection.execute('SELECT normalized_name FROM employees WHERE id=?',(employee_id,)).fetchone()
            if old:
                self.connection.execute('INSERT OR IGNORE INTO employee_aliases VALUES (?,?)',(old[0],employee_id))
            self.connection.execute('UPDATE employees SET name=?,normalized_name=?,active=? WHERE id=?',
                                    (' '.join(name.split()),normalize_text(name),int(active),employee_id))

    def employee_alias(self,raw,employee_id):
        if not normalize_text(raw):
            raise CalculationInputError('Пустой source alias')
        current=self.resolve_employee(raw)
        if current and current.id!=employee_id:
            raise CalculationInputError('Алиас уже принадлежит другому сотруднику')
        with self.connection:
            self.connection.execute('INSERT INTO employee_aliases VALUES (?,?) ON CONFLICT(alias_normalized) DO UPDATE SET employee_id=excluded.employee_id',
                                    (normalize_text(raw),employee_id))

    def set_year_role(self,employee_id,year,role):
        policy=self.role_policy(role)
        if type(year) is not int or not 1900<=year<=2100:
            raise CalculationInputError('Некорректный год')
        with self.connection:
            self.connection.execute('''INSERT INTO employee_year_profiles(employee_id,year,role,plans_json,calls_plans_json,calls_facts_json) VALUES (?,?,?,NULL,?,?)
                ON CONFLICT(employee_id,year) DO UPDATE SET role=excluded.role''',
                (employee_id,year,role,dump_object((policy.default_calls_plan,)*4),dump_object((ZERO,)*4)))

    def year_role(self,employee_id,year):
        row=self.connection.execute('SELECT role FROM employee_year_profiles WHERE employee_id=? AND year=?',(employee_id,year)).fetchone()
        return row[0] if row else None

    def save_year_profile(self,employee_id,year,role,plans,calls_plans=None,calls_facts=None):
        previous=self.year_profile(employee_id,year)
        policy=self.role_policy(role)
        profile=YearProfile(employee_id,year,role,plans,
            tuple(calls_plans) if calls_plans is not None else previous.calls_plans if previous else (policy.default_calls_plan,)*4,
            tuple(calls_facts) if calls_facts is not None else previous.calls_facts if previous else (ZERO,)*4)
        with self.connection:
            self.connection.execute('''INSERT INTO employee_year_profiles(employee_id,year,role,plans_json,calls_plans_json,calls_facts_json) VALUES (?,?,?,?,?,?)
                ON CONFLICT(employee_id,year) DO UPDATE SET role=excluded.role,plans_json=excluded.plans_json,
                calls_plans_json=excluded.calls_plans_json,calls_facts_json=excluded.calls_facts_json''',
                (employee_id,year,role,dump_object(plans),dump_object(profile.calls_plans),dump_object(profile.calls_facts)))
            self.connection.execute("UPDATE employee_year_profiles SET workflow_state='draft' WHERE employee_id=? AND year=?",(employee_id,year))
        return profile

    def year_profile(self,employee_id,year):
        row=self.connection.execute('SELECT role,plans_json,calls_plans_json,calls_facts_json FROM employee_year_profiles WHERE employee_id=? AND year=?',(employee_id,year)).fetchone()
        return YearProfile(employee_id,year,row[0],load_object(row[1]),load_object(row[2]),load_object(row[3])) if row and row[1] else None

    def role_policy(self,role):
        row=self.connection.execute('SELECT policy_json FROM role_templates WHERE role=?',(role,)).fetchone()
        if not row:
            raise CalculationInputError('Укажите должность')
        return load_object(row[0])

    def save_role_policy(self,policy):
        with self.connection:
            self.connection.execute('UPDATE role_templates SET policy_json=? WHERE role=?',(dump_object(policy),policy.role))

    def save_price(self,data,products):
        products=tuple(products)
        from app.services.product_matcher import ProductMatcher
        ProductMatcher(products)
        timestamp=now()
        with self.connection:
            previous_price=self.price_version()
            previous_products=previous_price.products if previous_price else tuple(Product(*r) for r in
                self.connection.execute('SELECT id,canonical_name,category,price_lpu,price_distributor FROM products'))
            old={normalize_text(product_mapping_key(p.canonical_name,p.packaging or name_packaging(p.canonical_name))):p.id
                 for p in previous_products}
            vid=self.connection.execute('INSERT INTO price_list_versions(source_file,source_hash,imported_at) VALUES (?,?,?)',
                                        (data.source_file,data.source_hash,timestamp)).lastrowid
            self.connection.executemany('''INSERT INTO price_products
                (version_id,product_id,canonical_name,category,threshold_lpu,threshold_distributor,packaging)
                VALUES (?,?,?,?,?,?,?)''',
                [(vid,p.id,p.canonical_name,p.category,str(p.price_lpu) if p.price_lpu is not None else None,
                  str(p.price_distributor) if p.price_distributor is not None else None,p.packaging) for p in products])
            # An alias can move only to the same canonical name and package.
            self.connection.execute('UPDATE products SET active=0')
            for p in products:
                self.connection.execute('''INSERT INTO products VALUES (?,?,?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET
                    canonical_name=excluded.canonical_name,category=excluded.category,price_lpu=excluded.price_lpu,
                    price_distributor=excluded.price_distributor,active=1,import_id=excluded.import_id''',
                    (p.id,p.canonical_name,p.category,str(p.price_lpu),str(p.price_distributor),str(vid)))
                previous=old.get(normalize_text(product_mapping_key(p.canonical_name,p.packaging or name_packaging(p.canonical_name))))
                if previous and previous!=p.id:
                    self.connection.execute('UPDATE product_aliases SET product_id=? WHERE product_id=?',(p.id,previous))
        return PriceVersion(vid,data.source_file,data.source_hash,timestamp,tuple(products))

    def price_version(self,version_id=None):
        row=self.connection.execute('SELECT id,source_file,source_hash,imported_at FROM price_list_versions '+
            ('ORDER BY id DESC LIMIT 1' if version_id is None else 'WHERE id=?'),() if version_id is None else (version_id,)).fetchone()
        if not row:
            return None
        products=tuple(Product(*r) for r in self.connection.execute('SELECT product_id,canonical_name,category,threshold_lpu,threshold_distributor,packaging FROM price_products WHERE version_id=? ORDER BY rowid',(row[0],)))
        return PriceVersion(*row,products)

    def save_sales(self,imported):
        if imported.errors:
            raise CalculationInputError('Исправьте структуру продаж перед сохранением')
        layout_hash=sha256(dump_object(imported.layouts).encode()).hexdigest()
        with self.connection:
            imported=self._retain_legacy_event_ids(imported)
            self.connection.execute('UPDATE sales_imports SET active=0 WHERE year=?',(imported.year,))
            self.connection.execute('''INSERT INTO sales_imports(source_file,source_hash,year,layout_hash,imported_at,payload_json)
                VALUES (?,?,?,?,?,?) ON CONFLICT(source_hash,year,layout_hash) DO UPDATE SET active=1''',
                (imported.source_file,imported.source_hash,imported.year,layout_hash,now(),dump_object(imported)))
            iid=self.connection.execute('SELECT id FROM sales_imports WHERE source_hash=? AND year=? AND layout_hash=?',
                                        (imported.source_hash,imported.year,layout_hash)).fetchone()[0]
            for events,table,kind in ((imported.shipments,'shipment_events','shipment'),(imported.returns,'return_events','return')):
                for e in events:
                    self.connection.execute('INSERT OR IGNORE INTO contract_lines VALUES (?,?,?,?,?,?,?)',
                        (e.line_key,e.manager,e.source_sheet,e.contract,e.lpu,e.db,e.product_raw))
                    self.connection.execute(f'''INSERT OR IGNORE INTO {table}
                        (event_id,line_key,first_import_id,year,month,quantity,revenue,payload_json) VALUES (?,?,?,?,?,?,?,?)''',
                        (e.event_id,e.line_key,iid,e.year,e.month,str(e.quantity),str(e.revenue),dump_object(e)))
                    # Add packaging to event metadata while keeping closed snapshots untouched.
                    old=self.connection.execute(f'SELECT payload_json FROM {table} WHERE event_id=?',(e.event_id,)).fetchone()
                    if old and e.packaging and not load_object(old[0]).packaging:
                        self.connection.execute(f'UPDATE {table} SET payload_json=? WHERE event_id=?',(dump_object(e),e.event_id))
                    self.connection.execute('INSERT OR IGNORE INTO sales_import_events VALUES (?,?,?)',(iid,e.event_id,kind))
        return iid

    def _retain_legacy_event_ids(self,imported):
        """First package-aware reimport must not turn existing paid sales into new sales."""
        if not any(e.legacy_line_key for e in (*imported.shipments,*imported.returns)):
            return imported
        from app.services.sales_importer import fingerprint,decimal_identity
        cache={}
        # Converted old contract rows retain their identity even if rows of two
        # different package sizes are subsequently reordered in Excel.
        known={}
        def business(event):
            return tuple(normalize_text(getattr(event,k)) for k in
                ('source_sheet','manager','contract','lpu','db','product_raw','legal_entity'))+(event.packaging,)
        for (payload,) in self.connection.execute('SELECT payload_json FROM shipment_events UNION ALL SELECT payload_json FROM return_events'):
            previous=load_object(payload)
            if previous.packaging and previous.legacy_line_key==previous.line_key:
                known.setdefault(business(previous),set()).add(previous.line_key)
        def retain(event,is_return):
            if not event.legacy_line_key:return event
            line=event.legacy_line_key
            compatible=known.get(business(event),set())
            if len(compatible)==1:
                line=next(iter(compatible))
            if line not in cache:
                stored=self.connection.execute('SELECT payload_json FROM shipment_events WHERE line_key=? UNION ALL SELECT payload_json FROM return_events WHERE line_key=?',(line,line)).fetchall()
                cache[line]=tuple(load_object(r[0]) for r in stored)
            previous=cache[line]
            if not previous:return event
            sizes={e.packaging for e in previous if e.packaging}
            if sizes and event.packaging not in sizes:return event
            return replace(event,line_key=line,legacy_line_key=line,event_id=fingerprint(line,event.year,event.month,is_return,
                decimal_identity(event.quantity),decimal_identity(event.revenue)))
        return replace(imported,shipments=tuple(retain(e,False) for e in imported.shipments),
            returns=tuple(retain(e,True) for e in imported.returns))

    def latest_sales(self,year):
        row=self.connection.execute('SELECT id,payload_json FROM sales_imports WHERE year=? AND active=1 ORDER BY id DESC LIMIT 1',(year,)).fetchone()
        return (row[0],load_object(row[1])) if row else None

    def allocations(self,return_ids=None):
        ids=set(return_ids) if return_ids is not None else None
        return tuple(load_object(payload) for rid,payload in self.connection.execute('SELECT return_id,payload_json FROM return_allocations') if ids is None or rid in ids)

    def save_allocations(self,employee_id,calculation):
        for a in calculation.allocations:
            self.connection.execute('INSERT INTO return_allocations VALUES (?,?,?) ON CONFLICT(return_id,shipment_id) DO UPDATE SET payload_json=excluded.payload_json',
                                    (a.return_id,a.shipment_id,dump_object(a)))
            if a.clawback:
                self.connection.execute('INSERT OR IGNORE INTO clawback_ledger VALUES (?,?,?,?,?,?)',
                                        (a.return_id,a.shipment_id,employee_id,a.target_year,a.target_quarter,str(a.clawback)))
        for r in calculation.returns:
            self.connection.execute('UPDATE return_events SET status=? WHERE event_id=?',
                                    ('Требует сверки возврата' if r.event_id in calculation.unallocated_returns else 'Распределен',r.event_id))

    def manual_links(self):
        return dict(self.connection.execute('SELECT return_id,shipment_id FROM return_manual_links'))

    def save_manual_link(self,return_id,shipment_id):
        with self.connection:
            self.connection.execute('INSERT INTO return_manual_links VALUES (?,?) ON CONFLICT(return_id) DO UPDATE SET shipment_id=excluded.shipment_id',(return_id,shipment_id))

    def save_workspace_calculation(self,employee,profile,policy,price,import_id,calculation,*,transaction=True):
        timestamp=now();result=calculation.result
        sales=self.connection.execute('SELECT source_file,source_hash FROM sales_imports WHERE id=?',(import_id,)).fetchone()
        with self.connection if transaction else nullcontext():
            legacy_import_id=self.connection.execute('INSERT INTO imports(employee_id,year,source_file,source_hash,imported_at) VALUES (?,?,?,?,?)',
                (employee.id,profile.year,*sales,timestamp)).lastrowid
            columns=[f'q{q}_{field}' for field in ('actual','calculated_premium','payable') for q in range(1,5)]
            values=[str(getattr(q,field)) for field in ('actual','calculated_premium','payable') for q in result.quarters]
            cid=self.connection.execute(f'''INSERT INTO calculations(import_id,employee_name_snapshot,profile_snapshot_json,
                plan_snapshot_json,result_snapshot_json,calculated_at,annual_actual,annual_payable,{','.join(columns)},
                price_version_id,employee_id,year,role,sales_import_id) VALUES ({','.join('?' for _ in range(25))})''',
                (legacy_import_id,employee.name,dump_object(policy),dump_object(profile.plans),dump_snapshot(result,()),timestamp,
                 str(result.annual_actual),str(result.annual_payable),*values,price.id,employee.id,profile.year,profile.role,import_id)).lastrowid
            snapshot=WorkspaceSnapshot(cid,employee,profile,policy,price,import_id,calculation,timestamp)
            self.connection.execute('UPDATE calculations SET workspace_snapshot_json=? WHERE id=?',(dump_object(snapshot),cid))
            self.connection.execute('INSERT INTO calculation_plans VALUES (?,?,?,?,?,?)',(cid,*(str(v) for v in profile.plans.values),str(profile.plans.annual)))
            self.save_allocations(employee.id,calculation)
            self.connection.execute("UPDATE employee_year_profiles SET workflow_state='calculated' WHERE employee_id=? AND year=?",(employee.id,profile.year))
        return snapshot

    def workspace_snapshot(self,calculation_id):
        row=self.connection.execute('SELECT workspace_snapshot_json,workflow_status FROM calculations WHERE id=?',(calculation_id,)).fetchone()
        if not row or not row[0]:
            raise CalculationInputError('Это расчет предыдущего формата; откройте старую историю')
        return replace(load_object(row[0]),status=row[1])

    def workspace_history(self,employee_id=None,year=None):
        clauses=['workspace_snapshot_json IS NOT NULL'];params=[]
        for col,value in (('employee_id',employee_id),('year',year)):
            if value is not None:
                clauses.append(col+'=?');params.append(value)
        return tuple(self.connection.execute('SELECT id,employee_name_snapshot,year,role,workflow_status,calculated_at FROM calculations WHERE '+' AND '.join(clauses)+' ORDER BY id DESC',params))

    def payments(self,employee_id,year=None):
        sql='SELECT id,employee_id,year,quarter,amount,paid_at,snapshot_json FROM payment_records WHERE employee_id=?'
        params=[employee_id]
        if year is not None:
            sql+=' AND year=?';params.append(year)
        return tuple(PaymentRecord(*r[:4],decimal_value(r[4]),r[5],load_object(r[6])) for r in self.connection.execute(sql+' ORDER BY year,quarter',params))

    def confirm_payment(self,snapshot,quarter,amount):
        amount=decimal_value(amount)
        if amount<0 or type(quarter) is not int or quarter not in (1,2,3,4):
            raise CalculationInputError('Некорректная выплата')
        with self.connection:
            current=self.workspace_snapshot(snapshot.id)
            if current.status=='paid/closed':
                raise CalculationInputError('Расчет уже закрыт; выполните новый расчет для следующего квартала')
            snapshot=current
            if any(p.quarter==quarter for p in self.payments(snapshot.employee.id,snapshot.profile.year)):
                raise CalculationInputError('Выплата этого квартала уже подтверждена')
            if any(p.quarter>quarter for p in self.payments(snapshot.employee.id,snapshot.profile.year)):
                raise CalculationInputError('Выплаты подтверждаются в хронологическом порядке')
            row=self.connection.execute('INSERT INTO payment_records(calculation_id,employee_id,year,quarter,amount,paid_at,snapshot_json) VALUES (?,?,?,?,?,?,?)',
                (snapshot.id,snapshot.employee.id,snapshot.profile.year,quarter,str(amount),now(),dump_object(replace(snapshot,status='paid/closed'))))
            self.connection.execute("UPDATE calculations SET workflow_status='paid/closed' WHERE id=?",(snapshot.id,))
            self.connection.execute("UPDATE employee_year_profiles SET workflow_state='paid/closed' WHERE employee_id=? AND year=?",(snapshot.employee.id,snapshot.profile.year))
        return row.lastrowid

    def reset_history(self):
        target=backup_database(self.path,'before-reset')
        with self.connection:
            for table in ('payment_records','clawback_ledger','return_manual_links','return_allocations','sales_import_events',
                          'return_events','shipment_events','contract_lines','sales_imports','shipments','calculation_plans','calculations','imports'):
                self.connection.execute(f'DELETE FROM {table}')
            self.connection.execute("UPDATE employee_year_profiles SET workflow_state='draft'")
        return target
