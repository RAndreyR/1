"""Deterministic FIFO with frozen original terms and explicit manual links."""
from dataclasses import replace
from decimal import Decimal
from app.models.domain import ZERO
from app.models.events import ReturnAllocation
from app.utils.normalization import CalculationInputError
from app.utils.packaging import normalize_packaging,name_packaging


def allocate_returns(audits,returns,*,paid_quarters=(),previous=(),manual_links=None,year=2026):
    sources={a.event.event_id:a for a in audits}
    paid=set(paid_quarters)
    manual_links=manual_links or {}
    remaining={key:[a.event.quantity,a.event.revenue] for key,a in sources.items()}
    current_returns={r.event_id for r in returns}
    frozen={}
    for a in previous:
        if a.paid or (a.original_year,a.original_quarter) in paid or (a.original_year<year and a.return_id not in current_returns):
            frozen.setdefault(a.return_id,[]).append(a)
    allocations=[];unallocated=[]
    # Reserve every closed allocation before considering any newly imported return,
    # including a late discovery whose physical month precedes a frozen return.
    for parts in frozen.values():
        for a in parts:
            if a.shipment_id in remaining:
                remaining[a.shipment_id][0]-=a.quantity
                remaining[a.shipment_id][1]-=a.revenue
    if any(q<0 or amount<0 for q,amount in remaining.values()):
        raise CalculationInputError('Сохраненные возвраты превышают исходную отгрузку')
    for ret in sorted(returns,key=lambda r:(r.year,r.month,r.source_row,r.event_id)):
        qty,amount=ret.quantity,ret.revenue
        for a in frozen.get(ret.event_id,[]):
            if a.shipment_id not in sources:
                raise CalculationInputError('Исходная отгрузка распределенного возврата отсутствует')
            allocations.append(a)
            qty-=a.quantity;amount-=a.revenue
        if qty<=0 and amount<=0:
            continue
        explicit=manual_links.get(ret.event_id)
        if explicit and explicit not in sources:
            raise CalculationInputError('Выбранная исходная отгрузка не найдена')
        def same_packaging(source):
            original=normalize_packaging(source.event.packaging) or name_packaging(source.event.product_raw)
            returned=normalize_packaging(ret.packaging) or name_packaging(ret.product_raw)
            if not original and source.audit.product:
                original=name_packaging(source.audit.product.canonical_name)
            return not returned or not original or original==returned
        if explicit and not same_packaging(sources[explicit]):
            raise CalculationInputError('Фасовка возврата и исходной отгрузки различается')
        candidates=[a for a in sources.values() if
                    ((a.event.event_id==explicit) if explicit else a.event.line_key==ret.line_key)
                    and same_packaging(a)
                    and (a.event.year,a.event.month)<=(ret.year,ret.month)]
        candidates.sort(key=lambda a:(a.event.year,a.event.month,a.event.source_row,a.event.event_id))
        for source in candidates:
            available_qty,available_amount=remaining[source.event.event_id]
            if available_qty<=0 or (qty==0 and not explicit):
                continue
            part_qty=min(qty,available_qty)
            part_amount=amount*part_qty/qty if qty else amount
            part_amount=min(part_amount,max(ZERO,available_amount))
            if part_qty==0 and part_amount==0:
                continue
            is_paid=(source.event.year,source.event.quarter) in paid
            target_year,target_quarter=ret.year,ret.quarter
            while (target_year,target_quarter) in paid:
                target_quarter+=1
                if target_quarter==5:
                    target_year+=1;target_quarter=1
            row=source.audit
            clawback=part_amount*source.rate if is_paid and row.eligible else ZERO
            allocation=ReturnAllocation(ret.event_id,source.event.event_id,part_qty,part_amount,row.eligible,
                row.product,row.product.category if row.product else None,row.client_type,row.block,
                source.rate,source.event.quarter,source.event.year,source.role,is_paid,clawback,target_year,target_quarter)
            allocations.append(allocation)
            remaining[source.event.event_id][0]-=part_qty;remaining[source.event.event_id][1]-=part_amount
            qty-=part_qty;amount-=part_amount
            if qty<=0 and amount<=0:
                break
        if qty>0 or amount>0:
            unallocated.append(ret.event_id)
    return tuple(allocations),tuple(unallocated)
