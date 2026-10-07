"""Only the independently supplied price workbook defines product groups."""
from app.models.domain import Product
from app.models.import_data import ValidationIssue
from app.services.excel_importer import PRICE_HEADERS, _layout, _data_rows, _parse_number, canonical_product_id
from app.services.workbook_reader import read_workbook
from app.utils.normalization import CalculationInputError, normalize_text


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
    products=[]
    seen=set()
    for row,values in _data_rows(rows,layout):
        try:
            raw=values['canonical_name']
            if not isinstance(raw,str) or not normalize_text(raw):
                raise CalculationInputError('Не задан продукт')
            if normalize_text(raw) in seen:
                raise CalculationInputError('Дублируется нормализованное название продукта')
            seen.add(normalize_text(raw))
            products.append(Product(canonical_product_id(raw),raw,values['category'],
                                    _parse_number(values['price_lpu'],True),
                                    _parse_number(values['price_distributor'],True)))
        except (ValueError,TypeError,AttributeError) as exc:
            raise CalculationInputError(f'Прайс: лист {name}, строка {row}: {exc}') from exc
    if not products:
        raise CalculationInputError('Внешний прайс пуст')
    return data,tuple(products)
