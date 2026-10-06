"""Test-only XLSM adapter reads original XML decimals without executing VBA.

Not a production importer. Reads shipments/prices only, never KPI plans.
"""
from decimal import Decimal
from pathlib import Path
from posixpath import join, normpath
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from app.models.domain import Product, Shipment

FIXTURE = Path(__file__).parent / 'fixtures' / 'KPI Трофимов Дмитрий 2026.xlsm'
N = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def source_sheet(path: Path, sheet_name: str) -> list[tuple[int, dict[str, str | Decimal | None]]]:
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        sheet = next(s for s in workbook.findall('s:sheets/s:sheet', N) if s.attrib['name'] == sheet_name)
        links = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        target = next(r.attrib['Target'] for r in links if r.attrib['Id'] == sheet.attrib[f'{{{REL}}}id'])
        target = target.lstrip('/') if target.startswith('/') else normpath(join('xl', target))
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(si.itertext()) for si in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        xml = ET.fromstring(archive.read(target))
        rows = []
        for row in xml.findall('s:sheetData/s:row', N):
            cells = {}
            for cell in row.findall('s:c', N):
                column = ''.join(c for c in cell.attrib['r'] if c.isalpha())
                # Cached numeric formula results are fixture inputs, never evaluated.
                value = cell.findtext('s:v', namespaces=N)
                kind = cell.attrib.get('t', 'n')
                if kind == 'inlineStr':
                    value = ''.join(cell.find('s:is', N).itertext())
                elif value is not None:
                    value = strings[int(value)] if kind == 's' else Decimal(value) if kind == 'n' else value
                cells[column] = value
            rows.append((int(row.attrib['r']), cells))
        return rows


def records(path: Path, sheet: str, required: set[str]):
    rows = source_sheet(path, sheet)
    for index, (_, cells) in enumerate(rows):
        headers = {col: ' '.join(value.split()).casefold() for col, value in cells.items() if isinstance(value, str)}
        if required <= set(headers.values()):
            return [(number, {name: cells.get(col) for col, name in headers.items()})
                    for number, cells in rows[index+1:] if any(v is not None for v in cells.values())]
    raise AssertionError(f'Missing fixture headers on {sheet}')


def read_fixture(path: Path = FIXTURE):
    prices = records(path, 'прайс', {'продукт','тип продукта','зеленая зона лпу','зеленая зона для дистров'})
    products = tuple(Product(str(number), row['продукт'], row['тип продукта'],
                             row['зеленая зона лпу'], row['зеленая зона для дистров']) for number,row in prices)
    data = records(path, 'отгрузки', {'период','тип клиента','номенклатура','количество','выручка','цена за 1 кг'})
    shipments = tuple(Shipment(number, row['период'], row['тип клиента'], row['номенклатура'],
                              row['количество'], row['выручка'], row['цена за 1 кг'],
                              row.get('клиент') or '', row.get('юл') or '',
                              row.get('задача в битрикс') or '', row.get('комментарии') or '') for number,row in data)
    return shipments, products
