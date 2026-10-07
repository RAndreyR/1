"""Versioned, explicit Decimal/dataclass snapshots; never recalculate on restore."""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from decimal import Decimal
import json

from app.models.domain import (AuditRow, CalculationResult, Plans, PremiumBlock,
                               Product, QuarterResult, Rules, Shipment)
from app.models.import_data import ProductMapping
from app.utils.normalization import decimal_value

TYPES = {cls.__name__: cls for cls in (AuditRow, CalculationResult, Plans, PremiumBlock,
                                      Product, QuarterResult, Rules, Shipment, ProductMapping)}
from app.models.events import (ShipmentEvent, ReturnEvent, Employee, YearProfile, RolePolicy,
                              PriceVersion, SalesImport, EventAudit, ReturnAllocation,
                              PeriodSummary, EventCalculation, WorkspaceSnapshot, PaymentRecord)
from app.models.events import ReturnReview
from app.models.import_data import SheetLayout, ValidationIssue
TYPES.update({cls.__name__:cls for cls in (ShipmentEvent,ReturnEvent,Employee,YearProfile,
    RolePolicy,PriceVersion,SalesImport,EventAudit,ReturnAllocation,PeriodSummary,
    EventCalculation,WorkspaceSnapshot,PaymentRecord,SheetLayout,ValidationIssue,ReturnReview)})


def dump_object(value):
    return json.dumps({'version':2,'value':_encode(value)},ensure_ascii=False,separators=(',',':'))


def load_object(payload):
    data=json.loads(payload)
    if not isinstance(data,dict) or set(data)!={'version','value'} or type(data['version']) is not int or data['version']!=2:
        raise ValueError('Неподдерживаемая версия снимка событий')
    return _decode(data['value'])


def _encode(value: object) -> object:
    if isinstance(value, Decimal):
        return {'decimal': str(value)}
    if is_dataclass(value):
        return {'type': type(value).__name__, 'fields': {f.name: _encode(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, Mapping):
        return {'mapping': [[_encode(k), _encode(v)] for k, v in value.items()]}
    if isinstance(value, tuple):
        return {'tuple': [_encode(v) for v in value]}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise ValueError('Неподдерживаемый тип в снимке расчета')


def _decode(value: object) -> object:
    if not isinstance(value, dict):
        if value is None or isinstance(value, (str, bool, int)):
            return value
        raise ValueError('Некорректное значение в снимке')
    if set(value) == {'decimal'}:
        return decimal_value(value['decimal'])
    if set(value) == {'tuple'}:
        return tuple(_decode(v) for v in value['tuple'])
    if set(value) == {'mapping'}:
        return {_decode(k): _decode(v) for k, v in value['mapping']}
    if set(value) == {'type', 'fields'} and value['type'] in TYPES:
        cls = TYPES[value['type']]
        expected={f.name for f in fields(cls)}
        # Additive fields in old persisted events/layouts have explicit defaults.
        optional={'Product':{'packaging'},'ShipmentEvent':{'packaging','legacy_line_key'},'ReturnEvent':{'packaging','legacy_line_key'},
                  'SheetLayout':{'packaging_unit'}}.get(value['type'],set())
        present=set(value['fields'])
        if present-expected or expected-present-optional:
            raise ValueError('Несовместимая структура снимка')
        return cls(**{k: _decode(v) for k, v in value['fields'].items()})
    raise ValueError('Неизвестный тип снимка')


def dump_snapshot(result: CalculationResult, mappings: tuple[ProductMapping, ...]) -> str:
    return json.dumps({'version': 1, 'result': _encode(result), 'mappings': _encode(mappings)},
                      ensure_ascii=False, separators=(',', ':'))


def load_snapshot(payload: str) -> tuple[CalculationResult, tuple[ProductMapping, ...]]:
    data = json.loads(payload)
    if not isinstance(data, dict) or set(data) != {'version','result','mappings'} or type(data['version']) is not int or data['version'] != 1:
        raise ValueError('Неподдерживаемая версия снимка')
    result, mappings = _decode(data['result']), _decode(data['mappings'])
    if not isinstance(result, CalculationResult) or not isinstance(mappings, tuple) or any(not isinstance(m, ProductMapping) for m in mappings):
        raise ValueError('Некорректный снимок расчета')
    return result, mappings
