"""Persistent settings and exact history, additive to the Phase 2 alias schema."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from app.models.application import AppSettings, HistoryEntry, SavedCalculation
from app.models.domain import CalculationResult, Rules
from app.models.import_data import ExcelImport, ProductMapping
from app.repositories.alias_repository import AliasRepository
from app.services.snapshot_service import dump_snapshot, load_snapshot
from app.utils.normalization import CalculationInputError, decimal_value, normalize_text


class ApplicationStorageError(RuntimeError):
    """Settings/history could not be saved or restored."""


class ApplicationRepository(AliasRepository):
    def __init__(self, path: str | Path | None = None) -> None:
        super().__init__(path)
        quarter_columns = ', '.join(f'q{q}_{field} TEXT NOT NULL' for field in ('actual', 'calculated_premium', 'payable') for q in range(1, 5))
        try:
            self.connection.executescript(f'''
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS kpi_profiles (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, plan_gate TEXT NOT NULL,
                    rate_block_1 TEXT NOT NULL, rate_block_2 TEXT NOT NULL,
                    rate_block_3 TEXT NOT NULL, rate_block_4 TEXT NOT NULL,
                    price_precision INTEGER NOT NULL CHECK(price_precision=1),
                    default_export_folder TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS imports (
                    id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
                    year INTEGER NOT NULL, source_file TEXT NOT NULL, source_hash TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calculations (
                    id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES imports(id),
                    employee_name_snapshot TEXT NOT NULL,
                    profile_snapshot_json TEXT NOT NULL, plan_snapshot_json TEXT NOT NULL,
                    result_snapshot_json TEXT NOT NULL, calculated_at TEXT NOT NULL,
                    annual_actual TEXT NOT NULL, annual_payable TEXT NOT NULL,
                    {quarter_columns}
                );
                CREATE TABLE IF NOT EXISTS calculation_plans (
                    calculation_id INTEGER PRIMARY KEY REFERENCES calculations(id),
                    q1_plan TEXT NOT NULL, q2_plan TEXT NOT NULL, q3_plan TEXT NOT NULL,
                    q4_plan TEXT NOT NULL, annual_plan TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shipments (
                    id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES imports(id),
                    source_row INTEGER NOT NULL, quarter INTEGER, client TEXT NOT NULL,
                    client_type TEXT, product_raw TEXT, product_id TEXT,
                    quantity TEXT NOT NULL, revenue TEXT NOT NULL,
                    unit_price_raw TEXT, unit_price_rounded TEXT, threshold_raw TEXT,
                    threshold_rounded TEXT, price_delta TEXT, legal_entity TEXT NOT NULL,
                    comment TEXT NOT NULL, bitrix_task TEXT NOT NULL,
                    eligible INTEGER NOT NULL, exclusion_reason TEXT,
                    UNIQUE(import_id, source_row)
                );
                CREATE INDEX IF NOT EXISTS calculations_import ON calculations(import_id);
            ''')
        except sqlite3.Error as exc:
            self.close()
            raise ApplicationStorageError('Не удалось подготовить хранилище истории') from exc

    def settings(self) -> AppSettings:
        try:
            row = self.connection.execute('''SELECT plan_gate, rate_block_1, rate_block_2,
                       rate_block_3, rate_block_4, default_export_folder, price_precision
                       FROM kpi_profiles WHERE id=1''').fetchone()
            return AppSettings(Rules(*row[:5]), row[5], row[6]) if row else AppSettings()
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise ApplicationStorageError('Не удалось прочитать настройки') from exc

    def save_settings(self, settings: AppSettings) -> None:
        try:
            with self.connection:
                self.connection.execute('''
                    INSERT INTO kpi_profiles VALUES (1, 'Основной', ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        plan_gate=excluded.plan_gate, rate_block_1=excluded.rate_block_1,
                        rate_block_2=excluded.rate_block_2, rate_block_3=excluded.rate_block_3,
                        rate_block_4=excluded.rate_block_4, price_precision=excluded.price_precision,
                        default_export_folder=excluded.default_export_folder
                ''', tuple(str(v) for v in (settings.rules.plan_gate, *settings.rules.rates)) +
                     (settings.price_precision, settings.export_folder))
        except sqlite3.Error as exc:
            raise ApplicationStorageError('Не удалось сохранить настройки') from exc

    def save_calculation(self, imported: ExcelImport, employee: str, year: int,
                         result: CalculationResult, mappings: tuple[ProductMapping, ...]) -> SavedCalculation:
        employee = ' '.join(employee.split())
        if not employee or type(year) is not int or not 1900 <= year <= 2100:
            raise CalculationInputError('Укажите сотрудника и корректный год')
        timestamp = datetime.now(timezone.utc).isoformat()
        payload = dump_snapshot(result, mappings)
        rules = result.rules
        profile = {'plan_gate': str(rules.plan_gate), 'rates': [str(v) for v in rules.rates], 'price_precision': 1}
        plan = {f'q{q}': str(v) for q, v in enumerate(result.plans.values, 1)}
        try:
            with self.connection:
                self.connection.execute('''INSERT INTO employees(name,normalized_name) VALUES (?,?)
                    ON CONFLICT(normalized_name) DO UPDATE SET name=excluded.name, active=1''',
                    (employee, normalize_text(employee)))
                employee_id = self.connection.execute('SELECT id FROM employees WHERE normalized_name=?', (normalize_text(employee),)).fetchone()[0]
                import_id = self.connection.execute('''INSERT INTO imports(employee_id,year,source_file,source_hash,imported_at)
                    VALUES (?,?,?,?,?)''', (employee_id, year, imported.source_file, imported.source_hash, timestamp)).lastrowid
                cols = [f'q{q}_{field}' for field in ('actual','calculated_premium','payable') for q in range(1,5)]
                values = [str(getattr(quarter, field)) for field in ('actual','calculated_premium','payable') for quarter in result.quarters]
                cursor = self.connection.execute(f'''INSERT INTO calculations
                    (import_id,employee_name_snapshot,profile_snapshot_json,plan_snapshot_json,result_snapshot_json,calculated_at,
                     annual_actual,annual_payable,{','.join(cols)}) VALUES ({','.join('?' for _ in range(8+len(cols)))})''',
                    [import_id, employee, json.dumps(profile), json.dumps(plan), payload, timestamp,
                     str(result.annual_actual), str(result.annual_payable), *values])
                calculation_id = cursor.lastrowid
                self.connection.execute('INSERT INTO calculation_plans VALUES (?,?,?,?,?,?)',
                    (calculation_id, *(str(v) for v in result.plans.values), str(result.plans.annual)))
                for a in result.audit:
                    s = a.shipment
                    prices = [str(v) if v is not None else None for v in
                              (a.actual_price_raw,a.actual_price_rounded,a.threshold_raw,a.threshold_rounded,a.price_delta)]
                    self.connection.execute('''INSERT INTO shipments
                        (import_id,source_row,quarter,client,client_type,product_raw,product_id,quantity,revenue,
                         unit_price_raw,unit_price_rounded,threshold_raw,threshold_rounded,price_delta,
                         legal_entity,comment,bitrix_task,eligible,exclusion_reason)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (import_id,s.source_row,a.quarter,s.client,a.client_type,s.product_raw,
                         a.product.id if a.product else None,str(s.quantity),str(s.revenue),*prices,
                         s.legal_entity,s.comment,s.bitrix_task,int(a.eligible),a.exclusion_reason))
        except sqlite3.Error as exc:
            raise ApplicationStorageError('Не удалось сохранить расчет в историю') from exc
        return SavedCalculation(calculation_id, employee, year, imported.source_file,
                                imported.source_hash, timestamp, result, mappings)

    def history(self) -> tuple[HistoryEntry, ...]:
        """List summaries without decoding every shipment snapshot on the UI thread."""
        try:
            rows = self.connection.execute('''SELECT c.id,c.employee_name_snapshot,i.year,i.source_file,
                       c.calculated_at,c.q1_payable,c.q2_payable,c.q3_payable,c.q4_payable,c.annual_payable
                       FROM calculations c JOIN imports i ON i.id=c.import_id
                       ORDER BY c.id DESC''').fetchall()
            return tuple(HistoryEntry(*row[:5], tuple(decimal_value(v) for v in row[5:9]),
                                      decimal_value(row[9])) for row in rows)
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise ApplicationStorageError('Не удалось открыть историю расчетов') from exc

    def load_calculation(self, calculation_id: int) -> SavedCalculation:
        try:
            row = self.connection.execute('''SELECT c.id,c.employee_name_snapshot,i.year,i.source_file,i.source_hash,
                       c.calculated_at,c.result_snapshot_json FROM calculations c
                       JOIN imports i ON i.id=c.import_id JOIN employees e ON e.id=i.employee_id
                       WHERE c.id=?''', (calculation_id,)).fetchone()
            if row is None:
                raise ValueError('Расчет отсутствует')
            return self._restore(row)
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise ApplicationStorageError('Не удалось открыть сохраненный расчет') from exc

    @staticmethod
    def _restore(row: tuple) -> SavedCalculation:
        result, mappings = load_snapshot(row[6])
        return SavedCalculation(*row[:6], result, mappings)
