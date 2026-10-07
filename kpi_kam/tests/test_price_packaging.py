"""Explicit price packaging stays versioned and constrains sales matching."""
from decimal import Decimal
import json
import sqlite3

from openpyxl import Workbook
import pytest

from app.models.domain import Plans, Product
from app.models.events import ShipmentEvent, ReturnEvent, RolePolicy
from app.repositories.workspace_repository import WorkspaceRepository
from app.services.excel_importer import canonical_product_id
from app.services.event_calculation import calculate_events
from app.services.price_importer import import_price
from app.services.product_matcher import ProductMatcher
from app.services.snapshot_service import dump_object, load_object
from app.services.workspace_service import WorkspaceService
from app.utils.normalization import CalculationInputError
from tests.event_helpers import sales_book
from tests.test_packaging_mapping import packaged_sales, price_variants


def explicit_price(path, rows=None, *, shift=0, header='фасовка'):
    book=Workbook();sheet=book.active;sheet.title='прайс'
    # Match the updated workbook's five fields, with movable rows/columns.
    for _ in range(shift):sheet.append(['Комментарий'])
    sheet.append(['зеленая зона для дистрибьютеров',header,'Продукт','зеленая зона ЛПУ','Тип продукта'])
    for name,pack,category,lpu,distributor in rows or [
        ('Латема высокопитательная','400 г','Latema','800','743')]:
        sheet.append([distributor,pack,name,lpu,category])
    book.save(path);book.close();return path


@pytest.mark.parametrize('shift',[0,2,20])
def test_external_price_reads_separate_packaging_without_renaming_product(tmp_path,shift):
    _,products=import_price(explicit_price(tmp_path/'price.xlsx',shift=shift))
    assert len(products)==1
    assert products[0].canonical_name=='Латема высокопитательная'
    assert products[0].packaging=='400 г'
    assert products[0].category=='Latema'
    assert products[0].price_distributor==Decimal('743')


def test_same_price_name_with_distinct_packages_is_not_ambiguous(tmp_path):
    _,products=import_price(explicit_price(tmp_path/'price.xlsx',[
        ('Стандарт','200 мл','ЭП','222','154'),(' Стандарт ','0,5 л','ЭП','444','308')]))
    assert [p.packaging for p in products]==['200 мл','500 мл']
    assert len({p.id for p in products})==2
    matcher=ProductMatcher(products)
    assert matcher.match('СТАНДАРТ','200 мл')==(products[0],'exact')
    assert matcher.match('Стандарт','500 мл')==(products[1],'exact')
    assert matcher.match('Стандарт')==(None,None)
    assert matcher.match('Стандарт','1000 мл')==(None,None)


def test_duplicate_normalized_price_package_is_rejected(tmp_path):
    path=explicit_price(tmp_path/'price.xlsx',[
        ('Стандарт','500 мл','ЭП','1','1'),(' Стандарт ','0.5 л','ЭП','2','2')])
    with pytest.raises(CalculationInputError,match='Дублируется'):
        import_price(path)


def test_contradictory_name_and_price_package_is_rejected(tmp_path):
    with pytest.raises(CalculationInputError,match='фасовк'):
        import_price(explicit_price(tmp_path/'price.xlsx',[
            ('Стандарт 500 мл','200 мл','ЭП','1','1')]))


def test_existing_name_volume_keeps_id_and_empty_bulk_pack_is_valid(tmp_path):
    _,old=import_price(price_variants(tmp_path/'old.xlsx'))
    _,new=import_price(explicit_price(tmp_path/'new.xlsx',[
        (p.canonical_name,f'{size} мл',p.category,p.price_lpu,p.price_distributor)
        for size,p in zip((200,500,1000),old)]))
    assert [p.id for p in new]==[p.id for p in old]
    _,bulk=import_price(explicit_price(tmp_path/'bulk.xlsx',[
        ('Нутримикстура',None,'ВМК','5000','4000')]))
    assert bulk[0].packaging==''
    assert bulk[0].id==canonical_product_id('Нутримикстура')


@pytest.mark.parametrize('source,expected',[('400 г',True),('0.4 кг',True),('400',True),('400 мл',False),('200 мл',False)])
def test_matching_checks_price_column_when_name_has_no_package(tmp_path,source,expected):
    _,products=import_price(explicit_price(tmp_path/'price.xlsx'))
    assert (ProductMatcher(products).match(products[0].canonical_name,source)[0] is not None)==expected


def test_price_packaging_and_aliases_survive_restart_and_new_price(tmp_path):
    path=tmp_path/'app.db'
    with WorkspaceRepository(path) as repo:
        service=WorkspaceService(repo);assert service.login_admin('stopp')
        price=service.import_price(explicit_price(tmp_path/'price.xlsx'))
        with pytest.raises(CalculationInputError,match='Фасовка'):
            service.map_product('Коктейль',price.products[0].id,packaging='400 мл')
        service.map_product('Коктейль',price.products[0].id,packaging='400 г')
        employee=repo.resolve_employee('Трофимов')
        service.set_role(employee.id,2026,'KAM')
        service.save_inputs(employee.id,2026,Plans('0','0','0','0'))
        service.import_sales(packaged_sales(tmp_path/'sales.xlsx',sizes=('400 г',),months={1:('10','8000')}),2026)
        service.map_product('Стандарт',price.products[0].id,packaging='400 г')
        snapshot=service.calculate(employee.id,2026)
        assert snapshot.calculation.quarters[0].payable==Decimal('120')
        service.confirm_payment(snapshot.id,1)
        frozen=repo.workspace_snapshot(snapshot.id)
        later=service.import_price(explicit_price(tmp_path/'new.xlsx',[
            ('Латема высокопитательная','400 г','Novionta','900','850')]))
        assert later.id!=price.id
        assert repo.workspace_snapshot(snapshot.id)==frozen
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            repo.connection.execute('UPDATE price_products SET packaging=? WHERE version_id=?',('500 г',price.id))
    with WorkspaceRepository(path) as repo:
        assert repo.price_version(price.id).products[0].packaging=='400 г'
        assert repo.price_version().products[0].category=='Novionta'
        assert repo.payments(employee.id,2026)[0].snapshot.price.products[0].packaging=='400 г'
        assert ProductMatcher(repo.price_version().products,repo.aliases()).match('Коктейль','400 г')[1]=='alias'


def test_old_product_snapshot_loads_with_default_packaging():
    old=Product('old','Стандарт 200 мл','ЭП','222','154')
    payload=json.loads(dump_object(old))
    payload['value']['fields'].pop('packaging',None)
    restored=load_object(json.dumps(payload))
    assert restored.packaging==''
    assert restored==old
    assert ProductMatcher([restored]).match(restored.canonical_name,'200 мл')==(restored,'exact')
    assert ProductMatcher([restored]).match(restored.canonical_name,'500 мл')==(None,None)


@pytest.mark.parametrize('source_pack,returned,eligible',[
    ('','400 г',True),('','400',True),('','400 мл',False),
    ('400','400 мл',False),('400','400 г',True)])
def test_fifo_checks_original_price_packaging(tmp_path,source_pack,returned,eligible):
    _,products=import_price(explicit_price(tmp_path/'price.xlsx'))
    source=ShipmentEvent('s','line','sheet',1,'Трофимов',2026,1,products[0].canonical_name,'10','8000',packaging=source_pack)
    refund=ReturnEvent('r','line','sheet',1,'Трофимов',2026,7,products[0].canonical_name,'2','1600',packaging=returned)
    result=calculate_events([source],[refund],products,Plans('0','0','0','0'),RolePolicy.kam())
    assert bool(result.allocations)==eligible
    assert result.quarters[2].actual==Decimal('-1600')
    if eligible:
        assert result.allocations[0].product.packaging=='400 г'
    else:
        assert result.unallocated_returns==('r',)


def test_version_one_migration_keeps_prices_profiles_and_closed_payments(tmp_path):
    path=tmp_path/'old.db'
    with WorkspaceRepository(path) as repo:
        service=WorkspaceService(repo);assert service.login_admin('stopp')
        employee=repo.resolve_employee('Трофимов')
        service.set_role(employee.id,2026,'KAM');service.save_inputs(employee.id,2026,Plans('0','0','0','0'))
        price=service.import_price(price_variants(tmp_path/'old-price.xlsx'))
        service.import_sales(sales_book(tmp_path/'sales.xlsx',raw=price.products[0].canonical_name),2026)
        snapshot=service.calculate(employee.id,2026);service.confirm_payment(snapshot.id,1)
        payment=repo.connection.execute('SELECT snapshot_json FROM payment_records').fetchone()[0]
    with sqlite3.connect(path) as old:
        if 'packaging' in {r[1] for r in old.execute('PRAGMA table_info(price_products)')}:
            old.execute('ALTER TABLE price_products DROP COLUMN packaging')
        old.execute('UPDATE schema_version SET version=1')
    with WorkspaceRepository(path) as migrated:
        assert migrated.connection.execute('SELECT version FROM schema_version').fetchone()[0]==2
        assert migrated.migration_backup.is_file()
        assert migrated.price_version(price.id).products==price.products
        assert migrated.year_profile(employee.id,2026).plans==snapshot.profile.plans
        assert migrated.connection.execute('SELECT snapshot_json FROM payment_records').fetchone()[0]==payment
        assert migrated.payments(employee.id,2026)[0].amount==snapshot.calculation.quarters[0].payable
    with WorkspaceRepository(path) as restarted:
        assert restarted.migration_backup is None


def test_gui_shows_price_package_and_filters_by_it(tmp_path):
    from PySide6.QtWidgets import QApplication
    from app.ui.workspace_window import WorkspaceWindow
    app=QApplication.instance() or QApplication([])
    window=WorkspaceWindow(tmp_path/'gui.db')
    try:
        window.workflow.login_admin('stopp')
        price=window.workflow.import_price(explicit_price(tmp_path/'price.xlsx'))
        window.refresh_workspace()
        window.refresh_admin()
        headers=[window.admin.price_grid.horizontalHeaderItem(i).text() for i in range(window.admin.price_grid.columnCount())]
        assert window.admin.price_grid.item(0,headers.index('Фасовка')).text()=='400 г'
        window.mappings.set_rows([['Коктейль','400 мл','','',''],['Коктейль','400 г','','','']],
            ['Коктейль ⟦фасовка: 400 мл⟧','Коктейль ⟦фасовка: 400 г⟧'])
        window.mappings.grid.selectRow(0)
        assert window.mappings.products.count()==1
        window.mappings.grid.selectRow(1)
        assert window.mappings.products.count()==2
        assert '400 г' in window.mappings.products.itemText(1)
        assert window.mappings.products.itemData(1)==price.products[0].id
    finally:
        window.close();app.processEvents()
