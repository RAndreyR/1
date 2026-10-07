from pathlib import Path
import pytest
from app.models.domain import Plans
from app.repositories.workspace_repository import WorkspaceRepository
from app.services.workspace_service import WorkspaceService


def test_default_alias_profiles_and_restart(tmp_path):
    path=tmp_path/'existing.db'
    with WorkspaceRepository(path) as repo:
        employee=repo.resolve_employee(' Пермякова ')
        assert employee.name == 'Пермякова Анна'
        assert repo.resolve_employee('Филимонова').name=='Филимонова Анна'
        repo.save_year_profile(employee.id,2026,'KAM',Plans('1','2','3','4'))
        assert repo.resolve_employee('Неизвестный') is None
    with WorkspaceRepository(path) as repo:
        profile=repo.year_profile(employee.id,2026)
        assert profile.plans == Plans('1','2','3','4')
        assert profile.role == 'KAM'
        assert repo.connection.execute('SELECT version FROM schema_version').fetchone()[0] == 2


def test_admin_hash_and_session_permissions(tmp_path):
    path=tmp_path/'application.db'
    with WorkspaceRepository(path) as repo:
        service=WorkspaceService(repo)
        assert not service.is_admin
        with pytest.raises(PermissionError):
            service.add_employee('Новый Сотрудник')
        assert not service.login_admin('wrong')
        assert service.login_admin('stopp')
        service.add_employee('Новый Сотрудник')
        service.change_password('stopp','new-long-password')
        service.logout_admin()
        assert not service.login_admin('stopp')
        assert service.login_admin('new-long-password')
    assert b'stopp' not in path.read_bytes()
    assert b'new-long-password' not in path.read_bytes()
    with WorkspaceRepository(path) as repo:
        assert not WorkspaceService(repo).is_admin


def test_migration_preserves_legacy_snapshot_and_creates_backup(tmp_path):
    from app.repositories.application_repository import ApplicationRepository
    from app.services.application_service import ApplicationService
    from app.services.excel_importer import import_excel
    from tests.excel_helpers import make_workbook
    from decimal import Decimal
    path=tmp_path/'legacy.db'
    with ApplicationRepository(path) as old:
        service=ApplicationService(old)
        service.set_import(import_excel(make_workbook(tmp_path/'legacy.xlsx')))
        original=service.calculate('Трофимов Дмитрий',2026,Plans('0','0','0','0'),Decimal('.9'))
    with WorkspaceRepository(path) as upgraded:
        assert upgraded.load_calculation(original.id)==original
        assert upgraded.migration_backup.is_file()
    with WorkspaceRepository(path) as restarted:
        assert restarted.load_calculation(original.id)==original
        assert restarted.migration_backup is None
