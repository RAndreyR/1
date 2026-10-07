"""Transactional, additive schema; existing version-1 snapshots are untouched."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION=2


def backup_database(path, label='backup'):
    path=Path(path)
    folder=path.parent/'backups';folder.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target=folder/f'{label}-{stamp}.db'
    with sqlite3.connect(path) as source,sqlite3.connect(target) as destination:
        source.backup(destination)
    return target


def backup_before_migration(path):
    path=Path(path)
    if not path.is_file() or path.stat().st_size==0:
        return None
    with sqlite3.connect(path) as connection:
        names={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'schema_version' in names:
            version=connection.execute('SELECT version FROM schema_version WHERE id=1').fetchone()
            if version and version[0]>=SCHEMA_VERSION:
                return None
    return backup_database(path,'before-migration')


TABLES=[
'''CREATE TABLE IF NOT EXISTS schema_version(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, migrated_at TEXT NOT NULL)''',
'''CREATE TABLE employee_aliases(alias_normalized TEXT PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id))''',
'''CREATE TABLE employee_year_profiles(employee_id INTEGER REFERENCES employees(id), year INTEGER, role TEXT NOT NULL CHECK(role IN ('KAM','SUPPORT')), plans_json TEXT, calls_plans_json TEXT NOT NULL, calls_facts_json TEXT NOT NULL, workflow_state TEXT NOT NULL DEFAULT 'draft', PRIMARY KEY(employee_id,year))''',
'''CREATE TABLE role_templates(role TEXT PRIMARY KEY, policy_json TEXT NOT NULL)''',
'''CREATE TABLE product_mapping_decisions(alias_normalized TEXT NOT NULL, price_version_id INTEGER NOT NULL, decision TEXT NOT NULL CHECK(decision='unmatched'), PRIMARY KEY(alias_normalized,price_version_id))''',
'''CREATE TABLE price_list_versions(id INTEGER PRIMARY KEY, source_file TEXT NOT NULL, source_hash TEXT NOT NULL, imported_at TEXT NOT NULL)''',
'''CREATE TABLE price_products(version_id INTEGER REFERENCES price_list_versions(id), product_id TEXT, canonical_name TEXT NOT NULL, category TEXT NOT NULL, threshold_lpu TEXT, threshold_distributor TEXT, PRIMARY KEY(version_id,product_id))''',
'''CREATE TABLE sales_imports(id INTEGER PRIMARY KEY, source_file TEXT NOT NULL, source_hash TEXT NOT NULL, year INTEGER NOT NULL, layout_hash TEXT NOT NULL, imported_at TEXT NOT NULL, payload_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE(source_hash,year,layout_hash))''',
'''CREATE TABLE contract_lines(line_key TEXT PRIMARY KEY, manager TEXT NOT NULL, source_sheet TEXT NOT NULL, contract TEXT NOT NULL, lpu TEXT NOT NULL, db TEXT NOT NULL, product_raw TEXT NOT NULL)''',
'''CREATE TABLE shipment_events(event_id TEXT PRIMARY KEY, line_key TEXT REFERENCES contract_lines(line_key), first_import_id INTEGER REFERENCES sales_imports(id), year INTEGER, month INTEGER, quantity TEXT NOT NULL, revenue TEXT NOT NULL, payload_json TEXT NOT NULL)''',
'''CREATE TABLE return_events(event_id TEXT PRIMARY KEY, line_key TEXT REFERENCES contract_lines(line_key), first_import_id INTEGER REFERENCES sales_imports(id), year INTEGER, month INTEGER, quantity TEXT NOT NULL, revenue TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Требует сверки возврата', payload_json TEXT NOT NULL)''',
'''CREATE TABLE sales_import_events(import_id INTEGER REFERENCES sales_imports(id), event_id TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY(import_id,event_id,kind))''',
'''CREATE TABLE return_allocations(return_id TEXT REFERENCES return_events(event_id), shipment_id TEXT REFERENCES shipment_events(event_id), payload_json TEXT NOT NULL, PRIMARY KEY(return_id,shipment_id))''',
'''CREATE TABLE return_manual_links(return_id TEXT PRIMARY KEY REFERENCES return_events(event_id), shipment_id TEXT NOT NULL REFERENCES shipment_events(event_id))''',
'''CREATE TABLE payment_records(id INTEGER PRIMARY KEY, calculation_id INTEGER NOT NULL REFERENCES calculations(id), employee_id INTEGER NOT NULL REFERENCES employees(id), year INTEGER NOT NULL, quarter INTEGER NOT NULL CHECK(quarter BETWEEN 1 AND 4), amount TEXT NOT NULL, paid_at TEXT NOT NULL, snapshot_json TEXT NOT NULL, UNIQUE(employee_id,year,quarter))''',
'''CREATE TABLE clawback_ledger(return_id TEXT NOT NULL REFERENCES return_events(event_id), shipment_id TEXT NOT NULL REFERENCES shipment_events(event_id), employee_id INTEGER NOT NULL REFERENCES employees(id), target_year INTEGER NOT NULL, target_quarter INTEGER NOT NULL, amount TEXT NOT NULL, PRIMARY KEY(return_id,shipment_id))''',
'''CREATE TABLE admin_credentials(id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB NOT NULL, password_hash BLOB NOT NULL, iterations INTEGER NOT NULL)''',
'''CREATE TABLE admin_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)''',
'''CREATE INDEX sales_imports_year ON sales_imports(year,active)''',
'''CREATE INDEX payment_employee_year ON payment_records(employee_id,year)''',
'''CREATE TRIGGER closed_calculation_immutable BEFORE UPDATE ON calculations WHEN OLD.workflow_status='paid/closed' BEGIN SELECT RAISE(ABORT,'Closed calculation is immutable'); END''',
'''CREATE TRIGGER price_products_immutable BEFORE UPDATE ON price_products BEGIN SELECT RAISE(ABORT,'Price version is immutable'); END''',
'''CREATE TRIGGER payments_immutable BEFORE UPDATE ON payment_records BEGIN SELECT RAISE(ABORT,'Payment is immutable'); END''',
]


def migrate(connection):
    connection.execute('BEGIN IMMEDIATE')
    try:
        exists=connection.execute("SELECT 1 FROM sqlite_master WHERE name='schema_version'").fetchone()
        version=connection.execute('SELECT version FROM schema_version WHERE id=1').fetchone() if exists else None
        if version and version[0]>SCHEMA_VERSION:
            raise RuntimeError('База создана более новой версией приложения')
        if not version:
            columns={r[1] for r in connection.execute('PRAGMA table_info(calculations)')}
            for name,definition in [('workflow_status',"TEXT NOT NULL DEFAULT 'calculated'"),
                                    ('workspace_snapshot_json','TEXT'),('price_version_id','INTEGER'),
                                    ('employee_id','INTEGER'),('year','INTEGER'),('role','TEXT'),('sales_import_id','INTEGER')]:
                if name not in columns:
                    connection.execute(f'ALTER TABLE calculations ADD COLUMN {name} {definition}')
            for statement in TABLES:
                connection.execute(statement)
            connection.execute('INSERT INTO schema_version VALUES (1,1,?)',(datetime.now(timezone.utc).isoformat(),))
        current=version[0] if version else 1
        if current<2:
            # Adding a default leaves old immutable price rows and snapshots intact.
            connection.execute("ALTER TABLE price_products ADD COLUMN packaging TEXT NOT NULL DEFAULT ''")
            connection.execute('UPDATE schema_version SET version=2,migrated_at=? WHERE id=1',
                               (datetime.now(timezone.utc).isoformat(),))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
