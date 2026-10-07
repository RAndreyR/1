"""Dynamic month groups and contract metadata; never read an embedded price."""
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
import re
from openpyxl.utils import get_column_letter
from app.models.events import SalesImport, ShipmentEvent, ReturnEvent
from app.models.import_data import SheetLayout, ValidationIssue
from app.services.excel_importer import _header, _parse_number
from app.services.workbook_reader import read_workbook
from app.utils.normalization import normalize_text, CalculationInputError

EMPTY_DB={'','-','—','нет','не указан','не указано','не задан','нет данных','n/a','na','none','null'}
FIELDS={'fo':'ФО','region':'Регион','manager':'Менеджер','lpu':'ЛПУ/КА','db':'ДБ',
        'legal_entity':'Отгрузка ЮЛ','contract':'Номер контракта','product_raw':'Наименование'}
REQUIRED=set(FIELDS)-{'fo','region'}
SHEETS=('контракты СБКС, ВМК','контракты ЭП')
MONTHS=('январ','феврал','март','апрел','ма','июн','июл','август','сентябр','октябр','ноябр','декабр')
QUANTITY={'кг','шт','количество','кол-во','количество, кг','количество, шт','кол-во, кг'}
AMOUNT={'сумма','руб','руб.','выручка','сумма/руб','сумма, руб'}


def client_identity(db, lpu):
    return ('ЛПУ',str(lpu or '').strip()) if normalize_text(str(db or '')) in EMPTY_DB else ('дистрибьютер',str(db).strip())


def fingerprint(*values):
    return sha256(json.dumps(values,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()


def decimal_identity(value):
    text=format(value,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _month(value):
    if isinstance(value,(date,datetime)) and value.day==1:
        return value.year,value.month,False
    text=_header(value)
    match=re.fullmatch(r'возврат\s+([а-я]+)(?:\s+(\d{4}))?',text)
    if match:
        for i,name in enumerate(MONTHS,1):
            if match[1].startswith(name):
                return int(match[2]) if match[2] else None,i,True
    return None


def _groups(rows, year, sheet, issues, merged_spans):
    groups=[]
    for row,cells in rows[:30]:
        found=[(col,_month(value)) for col,value in cells.items() if _month(value)]
        if not found:
            continue
        # Choose the group row containing monthly dates, not a summary/duplicate.
        if not any(not info[2] for _,info in found):
            continue
        below=rows[row][1] if row<len(rows) else {}
        used=set()
        for col,(date_year,month,is_return) in found:
            if col in used:
                continue
            y=date_year or year
            if y!=year:
                issues.append(ValidationIssue('wrong_year','Год месячной группы отличается от выбранного года','error',sheet,row))
                continue
            next_group=next((other for other,_ in found if other>col),
                            max(max(c,default=0) for _,c in rows[:30])+1)
            if (row,col) in merged_spans:
                next_group=min(next_group,merged_spans[row,col]+1)
            duplicated=is_return and cells.get(col+1)==cells.get(col)
            if duplicated:
                quantity,amount=col,col+1
                used.add(col+1)
            else:
                columns=range(col,next_group)
                quantities=[c for c in columns if _header(below.get(c)) in QUANTITY]
                amounts=[c for c in columns if _header(below.get(c)) in AMOUNT]
                if not quantities and not amounts and merged_spans.get((row,col))==col+1:
                    # The Excel month explicitly spans a two-cell quantity/amount pair.
                    quantity,amount=col,col+1
                elif len(quantities)!=1 or len(amounts)!=1:
                    issues.append(ValidationIssue('month_columns','Не найдена однозначная пара количество/сумма для месяца','error',sheet,row))
                    continue
                else:
                    quantity,amount=quantities[0],amounts[0]
            groups.append((month,is_return,quantity,amount))
        if len({(m,r) for m,r,_,_ in groups})!=len(groups):
            issues.append(ValidationIssue('duplicate_month','Повторяется месячная группа','error',sheet,row))
        return row,groups
    issues.append(ValidationIssue('months_missing','В первых 30 строках не найдены месячные Excel-даты','error',sheet))
    return 0,[]


def import_sales(path, year, *, column_mappings=None):
    if type(year) is not int or not 1900<=year<=2100:
        raise CalculationInputError('Некорректный год')
    data=read_workbook(path)
    shipments,returns,issues,layouts,requests=[],[],[],[],[]
    excluded_tender=0
    for expected in SHEETS:
        names=[n for n in data.sheets if normalize_text(n)==normalize_text(expected)]
        if len(names)!=1:
            issues.append(ValidationIssue('missing_sheet',f'Не найден лист {expected}','error',expected))
            continue
        name=names[0];rows=data.sheets[name]
        month_row,groups=_groups(rows,year,name,issues,data.merged_spans[name])
        candidates=[]
        for number,cells in rows[:30]:
            found={field:[col for col,value in cells.items() if _header(value)==_header(label)] for field,label in FIELDS.items()}
            if all(len(found[k])==1 for k in REQUIRED):
                candidates.append(SheetLayout(name,number,{k:v[0] for k,v in found.items() if v}))
        explicit=(column_mappings or {}).get(name)
        if explicit is not None:
            layout=explicit
            if not REQUIRED<=set(layout.columns) or len(set(layout.columns.values()))!=len(layout.columns) or not 1<=layout.header_row<=30:
                raise CalculationInputError('Некорректное ручное сопоставление колонок')
            if any(type(c) is not int or c<1 or c>max(max(cells,default=0) for _,cells in rows) for c in layout.columns.values()):
                raise CalculationInputError('Выберите существующие колонки листа')
        elif len(candidates)==1:
            layout=candidates[0]
        else:
            requests.append(name)
            issues.append(ValidationIssue('metadata_headers',f'Лист {name}: нужны заголовки или явное сопоставление колонок','error',name))
            continue
        layouts.append(layout)
        occurrences={}
        for number,cells in rows:
            if number<=max(layout.header_row,month_row):
                continue
            metadata={k:str(cells.get(c,'') or '').strip() for k,c in layout.columns.items()}
            for field in FIELDS:
                metadata.setdefault(field,'')
            if normalize_text(metadata['manager'])=='тендер':
                excluded_tender+=1
                continue
            pairs=[]
            for month,is_return,qcol,acol in groups:
                coords=[f'{get_column_letter(c)}{number}' for c in (qcol,acol)]
                if any(c in data.formulas_without_cache[name] for c in coords):
                    issues.append(ValidationIssue('uncached_formula','Нет сохраненного результата месячной формулы','error',name,number))
                    continue
                try:
                    qty=_parse_number(cells.get(qcol),False) or Decimal(0)
                    amount=_parse_number(cells.get(acol),False) or Decimal(0)
                    if qty==0 and amount==0:
                        continue
                    if not is_return and (qty<0 or amount<0):
                        raise CalculationInputError('Отрицательная отгрузка: укажите отдельный возврат')
                    pairs.append((month,is_return,abs(qty),abs(amount)))
                except CalculationInputError as exc:
                    issues.append(ValidationIssue('invalid_monthly_number',str(exc),'error',name,number))
            if not pairs:
                continue
            if not metadata['manager'] or not metadata['product_raw']:
                issues.append(ValidationIssue('missing_event_identity','У денежной строки не задан менеджер или продукт','error',name,number))
            business=fingerprint(normalize_text(name),*(normalize_text(metadata[k]) for k in ('manager','contract','lpu','db','product_raw','legal_entity')))
            occurrence=occurrences.get(business,0);occurrences[business]=occurrence+1
            line=fingerprint(business,occurrence)
            for month,is_return,qty,amount in pairs:
                event_id=fingerprint(line,year,month,is_return,decimal_identity(qty),decimal_identity(amount))
                cls=ReturnEvent if is_return else ShipmentEvent
                event=cls(event_id,line,name,number,metadata['manager'],year,month,metadata['product_raw'],qty,amount,
                          metadata['lpu'],metadata['db'],metadata['legal_entity'],metadata['contract'],metadata['fo'],metadata['region'])
                (returns if is_return else shipments).append(event)
                if qty==0:
                    issues.append(ValidationIssue('zero_quantity','Нулевая месячная численность: зеленая зона недоступна','warning',name,number))
    return SalesImport(data.source_file,data.source_hash,year,tuple(shipments),tuple(returns),tuple(issues),tuple(layouts),tuple(requests),excluded_tender)
