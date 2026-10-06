from dataclasses import replace
from decimal import Decimal
import json
import pytest
from app.models.application import AppSettings
from app.models.domain import Plans, Rules
from app.repositories.application_repository import ApplicationRepository, ApplicationStorageError
from app.services.application_service import ApplicationService
from app.services.excel_importer import import_excel
from app.services.snapshot_service import load_snapshot
from tests.excel_helpers import make_workbook
from tests.workbook_fixture import FIXTURE


def prepared_service(tmp_path):
    repo = ApplicationRepository(tmp_path/'application.db')
    service = ApplicationService(repo)
    session = service.set_import(import_excel(FIXTURE))
    for entry in session.mappings:
        if entry.status == 'unresolved':
            session.leave_unmatched(entry.key)
    return repo, service


def test_full_snapshot_roundtrip_and_immutable_history(tmp_path):
    repo,service = prepared_service(tmp_path)
    with repo:
        original = service.calculate('Трофимов Дмитрий',2026,Plans('20000000','10000000','0','0'),Decimal('0.90'))
        service.save_settings(AppSettings(Rules(plan_gate='0.5',rate_block_1='0.1'),str(tmp_path/'exports')))
        loaded = repo.load_calculation(original.id)
        assert loaded == original
        assert loaded.result.plans.q1 == Decimal('20000000')
        assert loaded.result.rules.rate_block_1 == Decimal('0.05')
        assert loaded.result.annual_actual == Decimal('28473035.803325146570')
        assert loaded.result.annual_calculated_premium == Decimal('251283.3171100000090')
        assert loaded.result.audit[0].shipment.source_row == 3
        assert len(loaded.result.audit) == 170
        assert any(m.status == 'left_unmatched' for m in loaded.mappings)
        assert repo.settings().rules.rate_block_1 == Decimal('0.1')
        assert repo.connection.execute('SELECT count(*) FROM shipments').fetchone()[0] == 170
        assert repo.connection.execute('SELECT q1_plan FROM calculation_plans').fetchone()[0] == '20000000'
    with ApplicationRepository(tmp_path/'application.db') as repo:
        assert repo.load_calculation(original.id) == original
        assert repo.history()[0].id == original.id
        assert repo.history()[0].quarter_payables == tuple(q.payable for q in original.result.quarters)
        assert repo.history()[0].annual_payable == original.result.annual_payable
        assert repo.settings().export_folder == str(tmp_path/'exports')


def test_reopen_without_source_or_current_price_list(tmp_path):
    path = make_workbook(tmp_path/'input.xlsx')
    with ApplicationRepository(tmp_path/'application.db') as repo:
        service = ApplicationService(repo)
        service.set_import(import_excel(path))
        original = service.calculate('Сотрудник',2026,Plans('0','0','0','0'),Decimal('0.9'))
        path.unlink()
        repo.sync_products([], 'later-import')
        loaded = repo.load_calculation(original.id)
        assert loaded.result == original.result
        assert loaded.result.audit[0].product.category == 'СБКС'
        assert loaded.result.audit[0].product.price_distributor == Decimal('100')


def test_employee_metadata_is_snapshot_not_mutable_profile(tmp_path):
    repo,service = prepared_service(tmp_path)
    with repo:
        first = service.calculate('Сотрудник',2025,Plans('0','0','0','0'),Decimal('0.9'))
        second = service.calculate(' СОТРУДНИК ',2026,Plans('1','2','3','4'),Decimal('0.8'))
        assert repo.load_calculation(first.id).employee == 'Сотрудник'
        assert repo.load_calculation(first.id).year == 2025
        assert repo.load_calculation(second.id).employee == 'СОТРУДНИК'
        assert [entry.id for entry in repo.history()] == [second.id,first.id]


def test_failed_snapshot_transaction_rolls_back_all_rows(tmp_path):
    repo,service = prepared_service(tmp_path)
    with repo:
        repo.connection.execute("CREATE TRIGGER fail_save BEFORE INSERT ON shipments BEGIN SELECT RAISE(ABORT,'disk error'); END")
        with pytest.raises(ApplicationStorageError):
            service.calculate('Сотрудник',2026,Plans('0','0','0','0'),Decimal('0.9'))
        for table in ('calculations','calculation_plans','imports','employees','shipments'):
            assert repo.connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0


def test_unknown_or_corrupted_snapshot_is_reported(tmp_path):
    repo,service = prepared_service(tmp_path)
    with repo:
        original = service.calculate('Сотрудник',2026,Plans('0','0','0','0'),Decimal('0.9'))
        repo.connection.execute("UPDATE calculations SET result_snapshot_json='[]' WHERE id=?",(original.id,))
        repo.connection.commit()
        with pytest.raises(ApplicationStorageError):
            repo.load_calculation(original.id)
        with pytest.raises(ApplicationStorageError):
            repo.load_calculation(999)


@pytest.mark.parametrize('payload', ['{"version":2}', '[]', '{"version":true,"result":null,"mappings":null}',
    '{"version":1,"result":{"type":"os.system","fields":{}},"mappings":null}'])
def test_snapshot_versions_and_types_are_explicit(payload):
    with pytest.raises(ValueError):
        load_snapshot(payload)


def test_phase2_alias_database_is_preserved_on_gui_upgrade(tmp_path):
    from app.repositories.alias_repository import AliasRepository
    path = make_workbook(tmp_path/'input.xlsx')
    imported = import_excel(path)
    database = tmp_path/'phase2.db'
    with AliasRepository(database) as repo:
        repo.sync_products(imported.products,imported.source_hash)
        repo.remember('старое название',imported.products[0].id)
    with ApplicationRepository(database) as repo:
        assert repo.aliases() == {'старое название':imported.products[0].id}
        assert repo.settings() == AppSettings()
        assert repo.history() == ()
