from decimal import Decimal
import pytest
from app.services.sales_importer import import_sales
from app.services.price_importer import import_price
from app.services.product_matcher import ProductMatcher
from tests.event_helpers import sales_book,price_book


@pytest.mark.parametrize('shift',[0,3,12])
def test_dynamic_headers_month_columns_and_return_months(tmp_path,shift):
    data=import_sales(sales_book(tmp_path/'sales.xlsx',shift=shift,
        months={2:('1.25','125.04'),10:('2','200')},returns={4:('1','100'),12:('0.25','25')}),2026)
    assert not data.errors
    assert [(e.month,e.quarter,e.quantity,e.revenue) for e in data.shipments]==[
        (2,1,Decimal('1.25'),Decimal('125.04')),(10,4,Decimal('2'),Decimal('200'))]
    assert [(e.month,e.quarter) for e in data.returns]==[(4,2),(12,4)]


def test_unknown_manager_retained_and_tender_excluded(tmp_path):
    unknown=import_sales(sales_book(tmp_path/'unknown.xlsx',manager='Новый менеджер'),2026)
    assert unknown.shipments[0].manager=='Новый менеджер'
    tender=import_sales(sales_book(tmp_path/'tender.xlsx',manager='  ТЕНДЕР '),2026)
    assert not tender.shipments and not tender.returns
    assert tender.excluded_tender_rows==2


def test_missing_metadata_requires_explicit_mapping(tmp_path):
    data=import_sales(sales_book(tmp_path/'sales.xlsx',missing_headers=True),2026)
    assert data.column_requests==('контракты ЭП',)
    assert data.errors


def test_return_hash_stable_when_rows_columns_move(tmp_path):
    first=import_sales(sales_book(tmp_path/'one.xlsx',returns={7:('2','200')}),2026)
    moved=import_sales(sales_book(tmp_path/'two.xlsx',shift=8,returns={7:('2.0','200.00')}),2026)
    assert first.returns[0].event_id==moved.returns[0].event_id


def test_external_price_quotes_and_category(tmp_path):
    data,products=import_price(price_book(tmp_path/'price.xlsx',category='ЭП'))
    assert products[0].category=='ЭП'
    from app.models.domain import Product
    matcher=ProductMatcher([Product('p','ВМК «НМ Плюс»','ЭП','100','100')])
    assert matcher.match('  вмк\u00a0"нм  плюс" ')[0].id=='p'
