import json
from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP
import pytest
from app.models.domain import Product,Plans
from app.models.events import ShipmentEvent,RolePolicy
from app.services.event_calculation import calculate_events


@pytest.mark.parametrize('name,expected',[
    ('Трофимов Дмитрий',('112087.85','139195.47')),
    ('Пермякова Анна',('149093.92','215902.16')),
    ('Гайдина Юлия',('239892.53','164728.27')),
])
def test_financial_rows_from_uploaded_kpi_books(name,expected):
    data=json.loads((Path(__file__).parent/'fixtures/policy_regressions.json').read_text(encoding='utf-8'))[f'KPI {name} 2026.xlsm']
    products=[Product(*r) for r in data['products']]
    events=[ShipmentEvent(*r) for r in data['events']]
    policy=RolePolicy.support() if data['role']=='SUPPORT' else RolePolicy.kam()
    result=calculate_events(events,[],products,Plans('0','0','0','0'),policy,calls_facts=('605','178','0','0'))
    assert len(result.event_audit)==data['source_rows']
    assert tuple(q.calculated_premium.quantize(Decimal('.01'),rounding=ROUND_HALF_UP) for q in result.result.quarters[:2])==tuple(Decimal(v) for v in expected)
    if data['role']=='SUPPORT':
        assert [q.calls_bonus for q in result.quarters[:2]]==[Decimal(0),Decimal(0)]
