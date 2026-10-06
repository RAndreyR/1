from dataclasses import replace
from decimal import Decimal
import sqlite3
import pytest
from app.models.domain import Plans
from app.repositories.alias_repository import AliasRepository, AliasStorageError, default_database_path
from app.services.excel_importer import import_excel
from app.services.mapping_service import ImportSession, ImportNotReadyError, mapping_key
from app.services.calculation_engine import calculate_kpi
from app.utils.normalization import CalculationInputError
from tests.excel_helpers import make_workbook


def unknown_import(tmp_path, name='  старый\u00a0 товар ', products=None):
    rows = [['1 кв.', 'Клиент', 'дистрибьютор', name, 10, 1000, 100, None, None, None]]
    return import_excel(make_workbook(tmp_path/'input.xlsx', shipments=rows, products=products))


def test_unresolved_blocks_and_explicit_leave_allows(tmp_path):
    imported = unknown_import(tmp_path)
    session = ImportSession(imported)
    assert not session.validation.can_calculate
    with pytest.raises(ImportNotReadyError):
        session.calculate(Plans('0','0','0','0'))
    session.leave_unmatched(session.mappings[0].key)
    assert session.validation.can_calculate
    result = session.calculate(Plans('0','0','0','0'))
    assert result.annual_actual == 1000 and result.annual_calculated_premium == 0
    assert result.excluded_rows[0].shipment.product_raw == '  старый\u00a0 товар '


def test_manual_mapping_preserves_raw_source_and_current_category(tmp_path):
    imported = unknown_import(tmp_path, products=[['Товар','Latema',100,100]])
    session = ImportSession(imported)
    session.select_product(session.mappings[0].key, imported.products[0].id)
    result = session.calculate(Plans('0','0','0','0'))
    assert session.validation.can_calculate
    assert result.audit[0].match_kind == 'manual'
    assert result.audit[0].shipment == imported.shipments[0]
    assert result.audit[0].product.category == 'Latema'
    assert result.quarters[0].blocks[2].base == 1000
    assert result.quarters[0].calculated_premium == 15


def test_remember_reopen_and_changed_price_list(tmp_path):
    imported = unknown_import(tmp_path)
    path = tmp_path/'data/kpi_kam.db'
    with AliasRepository(path) as repo:
        session = ImportSession(imported, repo)
        session.select_product(session.mappings[0].key, imported.products[0].id, remember=True)
        assert repo.aliases() == {'старый товар': imported.products[0].id}
    newer = unknown_import(tmp_path, name='СТАРЫЙ  ТОВАР', products=[['Другой','ВМК',99,99], [' ТОВАР ','ЭП',200,150]])
    with AliasRepository(path) as repo:
        session = ImportSession(newer, repo)
        assert session.mappings[0].status == 'saved_alias'
        assert session.validation.can_calculate
        result = session.calculate(Plans('0','0','0','0'))
        assert result.audit[0].match_kind == 'alias'
        assert result.audit[0].product.category == 'ЭП'
        assert result.audit[0].threshold_raw == Decimal('150')
        assert result.annual_calculated_premium == 0
        assert result.excluded_rows[0].exclusion_reason == 'Цена ниже зеленой зоны'
        stored = repo.connection.execute('SELECT category,price_distributor FROM products WHERE id=?', (newer.products[1].id,)).fetchone()
        assert stored == ('ЭП','150')


def test_temporary_selection_not_remembered(tmp_path):
    imported = unknown_import(tmp_path)
    with AliasRepository(tmp_path/'db.sqlite') as repo:
        session = ImportSession(imported, repo)
        session.select_product(session.mappings[0].key, imported.products[0].id)
        assert repo.aliases() == {}
        assert ImportSession(imported, repo).mappings[0].status == 'unresolved'


def test_stale_alias_pending_not_used_as_old_category(tmp_path):
    imported = unknown_import(tmp_path)
    with AliasRepository(tmp_path/'db.sqlite') as repo:
        session = ImportSession(imported, repo)
        session.select_product(session.mappings[0].key, imported.products[0].id, remember=True)
        newer = unknown_import(tmp_path, products=[['Другой','СБКС',100,100]])
        session = ImportSession(newer, repo)
        assert not session.validation.can_calculate
        assert session.mappings[0].product is None
        assert any(i.code == 'stale_alias' for i in session.validation.issues)
        assert repo.aliases()  # Preserve binding if the product returns in a later list.
        assert ImportSession(imported, repo).mappings[0].status == 'saved_alias'


def test_empty_names_are_individual_decisions(tmp_path):
    rows = [['1 кв.', 'Клиент', 'ЛПУ', None, 10, 1000, 100, None, None, None],
            ['1 кв.', 'Клиент', 'ЛПУ', ' ', 10, 1000, 100, None, None, None]]
    imported = import_excel(make_workbook(tmp_path/'input.xlsx', shipments=rows))
    with AliasRepository(tmp_path/'db.sqlite') as repo:
        session = ImportSession(imported, repo)
        assert [e.key for e in session.mappings] == ['row:3','row:4']
        with pytest.raises(CalculationInputError, match='Пустое'):
            session.select_product('row:3', imported.products[0].id, remember=True)
        assert session.mappings[0].status == 'unresolved'
        session.select_product('row:3', imported.products[0].id)
        assert session.validation.pending_mapping_rows == (4,)
        session.leave_unmatched('row:4')
        result = session.calculate(Plans('0','0','0','0'))
        assert len(result.included_rows) == 1 and len(result.excluded_rows) == 1
        assert result.audit[0].shipment.product_raw is None
        assert result.annual_actual == 2000 and result.annual_calculated_premium == 50


def test_normalized_alias_group_all_rows(tmp_path):
    rows = [['1 кв.', 'Клиент', 'ЛПУ', value, 10, 1000, 100, None, None, None] for value in [' old ', 'OLD\u00a0']]
    imported = import_excel(make_workbook(tmp_path/'input.xlsx', shipments=rows))
    session = ImportSession(imported)
    assert len(session.mappings) == 1 and session.mappings[0].source_rows == (3,4)
    session.select_product(session.mappings[0].key, imported.products[0].id)
    result = session.calculate(Plans('0','0','0','0'))
    assert len(result.included_rows) == 2
    assert result.annual_calculated_premium == 100


def test_exact_has_priority_over_saved_alias(tmp_path):
    imported = import_excel(make_workbook(tmp_path/'input.xlsx', products=[['Товар','СБКС',100,100], ['Другой','ЭП',100,100]]))
    with AliasRepository(tmp_path/'db.sqlite') as repo:
        repo.sync_products(imported.products, imported.source_hash)
        repo.remember('товар', imported.products[1].id)
        session = ImportSession(imported, repo)
        assert session.mappings[0].status == 'exact'
        assert session.mappings[0].product == imported.products[0]
        with pytest.raises(CalculationInputError):
            session.select_product(session.mappings[0].key, imported.products[1].id)
        with pytest.raises(CalculationInputError):
            session.leave_unmatched(session.mappings[0].key)


def test_alias_explicit_remap_and_quote_sql_safe(tmp_path):
    imported = unknown_import(tmp_path, name="старый'); DROP TABLE products;--", products=[['Товар','СБКС',100,100], ['Другой','ЭП',100,100]])
    with AliasRepository(tmp_path/'db.sqlite') as repo:
        session = ImportSession(imported, repo)
        key = session.mappings[0].key
        session.select_product(key, imported.products[0].id, remember=True)
        session.select_product(key, imported.products[1].id, remember=True)
        assert list(repo.aliases().values()) == [imported.products[1].id]
        assert repo.connection.execute('SELECT COUNT(*) FROM products').fetchone()[0] == 2
        assert ImportSession(imported, repo).mappings[0].product.id == imported.products[1].id


def test_storage_failure_does_not_commit_session_decision(tmp_path, monkeypatch):
    imported = unknown_import(tmp_path)
    with AliasRepository(tmp_path/'db.sqlite') as repo:
        session = ImportSession(imported, repo)
        def fail(*args):
            raise AliasStorageError('disk full')
        monkeypatch.setattr(repo, 'remember', fail)
        with pytest.raises(AliasStorageError):
            session.select_product(session.mappings[0].key, imported.products[0].id, remember=True)
        assert session.mappings[0].status == 'unresolved'


def test_repository_rejects_unknown_ids_and_bad_aliases(tmp_path):
    with AliasRepository(tmp_path/'db.sqlite') as repo:
        with pytest.raises(CalculationInputError):
            repo.remember('alias','unknown')
        with pytest.raises(CalculationInputError):
            repo.remember('  ','unknown')
        assert repo.aliases() == {}


def test_default_windows_path(monkeypatch, tmp_path):
    monkeypatch.setenv('APPDATA',str(tmp_path))
    assert default_database_path() == tmp_path/'KPI_KAM/kpi_kam.db'


@pytest.mark.parametrize('assignments', [{99:'p'}, {3:'missing'}, {True:'p'}])
def test_engine_assignment_validation(shipment, product, plans, assignments):
    with pytest.raises(CalculationInputError):
        calculate_kpi([shipment()], [product], plans, product_assignments=assignments)


def test_invalid_import_does_not_replace_saved_catalog(tmp_path):
    imported = unknown_import(tmp_path)
    with AliasRepository(tmp_path/'db.sqlite') as repo:
        session = ImportSession(imported, repo)
        session.select_product(session.mappings[0].key, imported.products[0].id, remember=True)
        rows = [['1 кв.', 'Клиент', 'ЛПУ', 'Unknown', 10, 'bad revenue', 100, None, None, None]]
        bad = import_excel(make_workbook(tmp_path/'bad.xlsx', shipments=rows, products=[['Другой','ЭП',100,100]]))
        assert bad.errors
        ImportSession(bad, repo)
        stored = repo.connection.execute('SELECT id FROM products WHERE active=1').fetchall()
        assert stored == [(imported.products[0].id,)]
        assert repo.aliases() == {'старый товар': imported.products[0].id}


def test_bad_database_gives_meaningful_error(tmp_path):
    path = tmp_path/'db.sqlite'
    path.write_bytes(b'not a database')
    with pytest.raises(AliasStorageError):
        AliasRepository(path)


def test_session_without_storage_rejects_remember(tmp_path):
    imported = unknown_import(tmp_path)
    session = ImportSession(imported)
    with pytest.raises(CalculationInputError, match='хранилище'):
        session.select_product(session.mappings[0].key, imported.products[0].id, remember=True)
    assert not session.validation.can_calculate
