"""Monthly policy layer around the tested Decimal shipment audit/engine."""
from dataclasses import replace
from decimal import Decimal, localcontext, ROUND_HALF_UP
from app.models.domain import Shipment, ZERO, PremiumBlock
from app.models.events import EventAudit, EventCalculation, PeriodSummary, RolePolicy,ReturnReview
from app.services.calculation_engine import calculate_kpi
from app.services.sales_importer import client_identity
from app.services.return_reconciliation import allocate_returns
from app.utils.normalization import CalculationInputError, decimal_value
from app.utils.packaging import product_mapping_key


def calls_bonus(fact,plan,policy):
    fact,plan=decimal_value(fact),decimal_value(plan)
    if fact<0 or plan<=0:
        raise CalculationInputError('Факт звонков неотрицательный, план положительный')
    if not policy.calls_enabled or fact<plan*policy.calls_gate:
        return ZERO
    # Multiply before division: 700/750 * 30000 must be exactly 28000.
    with localcontext() as context:
        context.prec=50
        return min(policy.calls_max,policy.calls_max*fact/plan)


def calculate_events(shipments,returns,products,plans,policy,*,aliases=None,
                     calls_plans=None,calls_facts=None,paid_amounts=None,
                     paid_records=(),previous_allocations=(),manual_links=None,
                     opening_balance=ZERO,scheduled_clawbacks=None,year=2026,
                     prior_audits=(),basis_adjustments=()):
    with localcontext() as context:
        context.prec=50;context.rounding=ROUND_HALF_UP
        return _calculate(tuple(shipments),tuple(returns),tuple(products),plans,policy,aliases or {},
            calls_plans,calls_facts,paid_amounts,paid_records,previous_allocations,
            manual_links,decimal_value(opening_balance),scheduled_clawbacks or {},year,
            tuple(prior_audits),tuple(basis_adjustments))


def _calculate(events,returns,products,plans,policy,aliases,calls_plans,calls_facts,paid_amounts,
               paid_records,previous_allocations,manual_links,opening_balance,scheduled,year,
               prior_audits,basis_adjustments):
    if any(e.year!=year for e in (*events,*returns)):
        raise CalculationInputError('В расчете должны быть события выбранного года')
    if len({e.event_id for e in (*events,*returns)})!=len(events)+len(returns):
        raise CalculationInputError('Повторяется событие продаж/возвратов')
    calls_plans=tuple(decimal_value(v) for v in (calls_plans or (policy.default_calls_plan,)*4))
    calls_facts=tuple(decimal_value(v) for v in (calls_facts or (ZERO,)*4))
    if len(calls_plans)!=4 or len(calls_facts)!=4:
        raise CalculationInputError('Нужны Q1–Q4 показатели звонков')
    inputs=[]
    for i,e in enumerate(events,1):
        kind,client=client_identity(e.db,e.lpu)
        inputs.append(Shipment(i,e.quarter,kind,product_mapping_key(e.product_raw,e.packaging),e.quantity,e.revenue,None,client,e.legal_entity,e.contract))
    history=dict(paid_amounts or {})
    current_payments={p.quarter:p for p in paid_records if p.year==year}
    history.update({q:p.amount for q,p in current_payments.items() if q<=3})
    result=calculate_kpi(inputs,products,plans,policy.rules,{q:v for q,v in history.items() if q<=3},aliases=aliases)
    audits=[EventAudit(e,replace(a,shipment=replace(a.shipment,product_raw=e.product_raw)),
        policy.role,policy.rules.rates[a.block-1] if a.block else ZERO) for e,a in zip(events,result.audit,strict=True)]
    frozen_sources={a.event.event_id:a for p in paid_records for a in p.snapshot.calculation.event_audit
                    if a.event.year==p.year and a.event.quarter==p.quarter}
    # Any changed/deleted paid source is a reconciliation issue, never a new sale.
    current_ids={e.event_id for e in events}
    if any(a.event.year==year and key not in current_ids for key,a in frozen_sources.items()):
        raise CalculationInputError('Изменена или удалена оплаченная отгрузка. Требуется сверка исходного файла')
    audits=[frozen_sources.get(a.event.event_id,a) for a in audits]
    source_audits={a.event.event_id:a for a in audits}
    source_audits.update({a.event.event_id:a for a in prior_audits if a.event.year<year})
    source_audits.update(frozen_sources)
    paid_quarters={(p.year,p.quarter) for p in paid_records}
    allocations,unallocated=allocate_returns(tuple(source_audits.values()),returns,paid_quarters=paid_quarters,
        previous=previous_allocations,manual_links=manual_links,year=year)
    basis_parts={(a.return_id,a.shipment_id):a for a in (*basis_adjustments,*allocations)
                 if not a.paid and a.original_year==year}
    local_audits={a.event.event_id:a for a in audits}
    def reduction_source(part):
        source=local_audits.get(part.shipment_id)
        return source.audit if source and source.audit.eligible else None
    quarter_results=[];calls=[]
    green={key:ZERO for key in result.green_totals}
    for a in audits:
        if a.audit.eligible:
            green[a.event.quarter,a.audit.client_type,a.audit.product.category]+=a.event.revenue
    for q,original in enumerate(result.quarters,1):
        positive=sum((e.revenue for e in events if e.quarter==q),ZERO)
        refund=sum((r.revenue for r in returns if r.quarter==q),ZERO)
        actual=positive-refund
        reductions={number:sum((a.revenue for a in basis_parts.values() if a.original_quarter==q
            and reduction_source(a) and reduction_source(a).block==number),ZERO) for number in range(1,5)}
        blocks=tuple(replace(b,base=b.base-reductions[b.number],premium=(b.base-reductions[b.number])*b.rate) for b in original.blocks)
        bonus=calls_bonus(calls_facts[q-1],calls_plans[q-1],policy)
        if q in current_payments:
            frozen=current_payments[q].snapshot.calculation
            blocks=frozen.result.quarters[q-1].blocks;bonus=frozen.quarters[q-1].calls_bonus
        premium=sum((b.premium for b in blocks),ZERO)+bonus
        passed=actual>=original.gate_threshold
        quarter_results.append(replace(original,actual=actual,achievement=actual/original.plan if original.plan else None,
            quarter_pass=passed,blocks=blocks,calculated_premium=premium,payable=max(ZERO,premium) if passed else ZERO,
            payable_reason='quarter_gate' if passed else 'gate_failed'))
        calls.append(bonus)
    annual_actual=sum((q.actual for q in quarter_results),ZERO)
    annual_premium=sum((q.calculated_premium for q in quarter_results),ZERO)
    annual_pass=annual_actual>=plans.annual*policy.rules.plan_gate
    actually_paid=sum((history.get(q,ZERO) for q in range(1,4)),ZERO)
    catch_up=max(ZERO,annual_premium-actually_paid) if annual_pass else None
    if annual_pass:
        quarter_results[3]=replace(quarter_results[3],payable=catch_up,payable_reason='annual_catch_up')
    summaries=[];balance=opening_balance
    for q,quarter in enumerate(quarter_results,1):
        adjustment=sum((a.clawback for a in allocations if a.target_year==year and a.target_quarter==q),ZERO)+scheduled.get(q,ZERO)
        carry_in=balance
        base=quarter.payable
        if q in current_payments:
            frozen=current_payments[q].snapshot.calculation.quarters[q-1]
            base=frozen.base_payable;adjustment=frozen.clawback;carry_in=frozen.carried_in
            payable=frozen.payable;balance=frozen.carried_out
        else:
            payable=max(ZERO,base-adjustment-carry_in)
            balance=max(ZERO,adjustment+carry_in-base)
        quarter_results[q-1]=replace(quarter,payable=payable)
        summaries.append(PeriodSummary(q,sum((e.revenue for e in events if e.quarter==q),ZERO),
            sum((r.revenue for r in returns if r.quarter==q),ZERO),quarter.actual,calls_plans[q-1],calls_facts[q-1],calls[q-1],
            quarter.calculated_premium,base,adjustment,carry_in,balance,payable,
            current_payments[q].amount if q in current_payments else ZERO,'paid/closed' if q in current_payments else 'calculated'))
    for a in basis_parts.values():
        source=reduction_source(a)
        if source:
            green[a.original_quarter,source.client_type,source.product.category]-=a.revenue
    # Paid sales retain their original audit/price/rate even after a new price import.
    result=replace(result,quarters=tuple(quarter_results),annual_actual=annual_actual,
        annual_achievement=annual_actual/plans.annual if plans.annual else None,annual_pass=annual_pass,
        annual_calculated_premium=annual_premium,paid_q1_q3=actually_paid,annual_catch_up=catch_up,
        annual_payable=actually_paid+quarter_results[3].payable,green_totals=green,
        audit=tuple(a.audit for a in audits))
    reviews=[]
    for r in returns:
        parts=[a for a in allocations if a.return_id==r.event_id]
        reviews.append(ReturnReview(r,r.quantity-sum((a.quantity for a in parts),ZERO),
            r.revenue-sum((a.revenue for a in parts),ZERO),sum((a.clawback for a in parts),ZERO),
            'Требует сверки возврата' if r.event_id in unallocated else 'Распределен'))
    return EventCalculation(result,tuple(audits),returns,allocations,tuple(summaries),unallocated,policy.role,year,tuple(reviews))
