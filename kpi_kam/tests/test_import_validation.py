from decimal import Decimal
import pytest
from app.models.domain import Plans
from app.services.excel_importer import import_excel
from app.services.mapping_service import ImportSession, ImportNotReadyError
from app.services.validation_service import validate_import
from app.utils.normalization import CalculationInputError
from tests.excel_helpers import make_workbook, SHIP_HEADERS


def test_all_business_issues_reported_and_revenue_retained(tmp_path):
    rows = [['bad', 'Клиент', 'unknown', 'unknown', 0, 1000, None, None, None, None]]
    imported = import_excel(make_workbook(tmp_path/'input.xlsx', shipments=rows))
    report = validate_import(imported)
    assert not report.errors
    assert report.invalid_quarters == (3,)
    assert report.unknown_client_types == (3,)
    assert report.unmatched_rows == (3,)
    assert report.missing_price_rows == (3,)
    assert report.pending_mapping_rows == (3,)
    assert {i.code for i in report.issues} == {'invalid_quarter', 'unknown_client_type', 'unmatched_product', 'missing_price'}
    assert imported.shipments[0].revenue == Decimal('1000')


def test_fallback_and_missing_threshold(tmp_path):
    rows = [['1 кв.', 'Клиент', 'ЛПУ', 'Товар', 10, 1000, 0, None, None, None]]
    imported = import_excel(make_workbook(tmp_path/'input.xlsx', shipments=rows, products=[['Товар','СБКС',None,100]]))
    report = validate_import(imported)
    assert report.missing_price_rows == ()
    assert report.missing_threshold_rows == (3,)
    assert report.can_calculate
    result = ImportSession(imported).calculate(Plans('0','0','0','0'))
    assert result.annual_actual == 1000 and result.annual_calculated_premium == 0
    assert result.audit[0].actual_price_raw == 100


def test_structure_errors_prevent_partial_calculation(tmp_path):
    headers = list(SHIP_HEADERS)
    headers[5] = 'No revenue'
    session = ImportSession(import_excel(make_workbook(tmp_path/'input.xlsx', ship_headers=headers)))
    assert session.validation.errors and not session.validation.can_calculate
    with pytest.raises(ImportNotReadyError):
        session.calculate(Plans('0','0','0','0'))


def test_invalid_period_and_unknown_client_are_audit_exclusions(tmp_path):
    rows = [['5 кв.', 'Клиент', 'ЛПУ', 'Товар', 10, 1000, 100, None, None, None],
            ['1 кв.', 'Клиент', 'unknown', 'Товар', 10, 2000, 100, None, None, None]]
    session = ImportSession(import_excel(make_workbook(tmp_path/'input.xlsx', shipments=rows)))
    assert session.validation.can_calculate
    result = session.calculate(Plans('0','0','0','0'))
    assert result.annual_actual == 2000
    assert len(result.excluded_rows) == 2


@pytest.mark.parametrize('decisions', [
    {'product_assignments': {999:'p'}}, {'left_unmatched_rows': [999]},
    {'product_assignments': {3:'p'}, 'left_unmatched_rows': [3]},
    {'left_unmatched_rows': [3]},
])
def test_bad_decisions_rejected(tmp_path, decisions):
    imported = import_excel(make_workbook(tmp_path/'input.xlsx'))
    with pytest.raises(CalculationInputError):
        validate_import(imported, **decisions)
