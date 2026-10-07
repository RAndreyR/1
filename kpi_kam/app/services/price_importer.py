"""Only the independently supplied price workbook defines product groups."""
from app.models.domain import Product
import re
from dataclasses import replace
from app.services.excel_importer import PRICE_HEADERS, _layout, _data_rows, _parse_number, canonical_product_id, _header
from app.services.sales_importer import PACKAGING_HEADERS
from app.services.workbook_reader import read_workbook
from app.utils.normalization import CalculationInputError, normalize_text
from app.utils.packaging import normalize_packaging, name_packaging, product_mapping_key, packaging_compatible


def import_price(path):
    data=read_workbook(path)
    headers={**PRICE_HEADERS,
             'category':(*PRICE_HEADERS['category'],'Группа','Группа продукта'),
             'price_distributor':(*PRICE_HEADERS['price_distributor'],'Зеленая зона дистрибьюторов')}
    candidates=[]
    for name,rows in data.sheets.items():
        issues=[]
        layout=_layout(rows[:30],name,headers,issues)
        if layout is not None and not issues:
            candidates.append((name,rows,layout))
    if len(candidates)!=1:
        raise CalculationInputError('Во внешнем прайсе нужен один однозначный лист с четырьмя полями: продукт, группа, зеленая зона ЛПУ и дистрибьюторов')
    name,rows,layout=candidates[0]
    cells=next(cells for number,cells in rows if number==layout.header_row)
    packing_columns=[col for col,value in cells.items() if _header(value) in PACKAGING_HEADERS]
    if len(packing_columns)>1:
        raise CalculationInputError(f'Прайс: лист {name}, строка {layout.header_row}: повторяется столбец фасовки')
    unit=''
    if packing_columns:
        column=packing_columns[0]
        header_text=_header(cells[column])
        unit=next((u for u in ('мл','кг','гр','г','л') if re.search(r'(?<!\w)'+u+r'(?!\w)',header_text)),'')
        if 'кг' in header_text and ('гр' in header_text or re.search(r'\bг\b',header_text)):unit=''
        layout=replace(layout,columns={**layout.columns,'packaging':column})
    products=[]
    seen={}
    for row,values in _data_rows(rows,layout):
        try:
            raw=values['canonical_name']
            if not isinstance(raw,str) or not normalize_text(raw):
                raise CalculationInputError('Не задан продукт')
            pack=normalize_packaging(values.get('packaging'),unit)
            named_pack=name_packaging(raw)
            if pack and not packaging_compatible(product_mapping_key(raw,pack),raw):
                raise CalculationInputError('Название продукта и поле фасовки противоречат друг другу')
            identity_pack=pack or named_pack
            previous=seen.setdefault(normalize_text(raw),set())
            if previous and (not identity_pack or '' in previous or identity_pack in previous):
                raise CalculationInputError('Дублируется нормализованное название продукта и фасовка')
            previous.add(identity_pack)
            # Existing volume-in-name IDs survive the new optional price column.
            identity=raw if not pack or named_pack else product_mapping_key(raw,pack)
            products.append(Product(canonical_product_id(identity),raw,values['category'],
                                    _parse_number(values['price_lpu'],True),
                                    _parse_number(values['price_distributor'],True),pack))
        except (ValueError,TypeError,AttributeError) as exc:
            raise CalculationInputError(f'Прайс: лист {name}, строка {row}: {exc}') from exc
    if not products:
        raise CalculationInputError('Внешний прайс пуст')
    return data,tuple(products)
