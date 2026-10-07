from decimal import Decimal
import pytest
from openpyxl import load_workbook
from tests.test_payments_returns import ready
from tests.event_helpers import sales_book


def test_admin_export_values_filters_details_and_formula_text(tmp_path):
    repo,service,employee=ready(tmp_path)
    with repo:
        first=service.calculate(employee.id,2026);service.confirm_payment(first.id,1,Decimal('40'))
        service.import_sales(sales_book(tmp_path/'update.xlsx',returns={7:('2','200')}),2026)
        service.calculate(employee.id,2026)
        output=tmp_path/'export.xlsx'
        service.export_data(output,2026,employee_id=employee.id,quarter=3)
        book=load_workbook(output,data_only=False)
        assert book.sheetnames==['Сводка','Включенные отгрузки','Исключенные отгрузки','Возвраты','Корректировки','Планы','Параметры']
        rows=list(book['Сводка'].values);values=dict(zip(rows[0],rows[1]))
        assert len(rows)==2
        assert values['ФИО']=='Трофимов Дмитрий'
        assert values['returns']==200
        assert values['actual after returns']==-200
        assert values['return clawback']==10
        assert values['carried clawback']==10
        assert book['Возвраты'].max_row==2
        assert book['Включенные отгрузки'].max_row==1
        service.export_data(tmp_path/'all.xlsx',2026)
        assert load_workbook(tmp_path/'all.xlsx')['Сводка'].max_row==5
        # Export source names as text even when Excel would treat them as formulas.
        service.update_employee(employee.id,'=HYPERLINK("bad")',True)
        service.export_data(tmp_path/'text.xlsx',2026)
        cell=load_workbook(tmp_path/'text.xlsx',data_only=False)['Сводка']['A2']
        assert cell.data_type=='s'
        service.logout_admin()
        with pytest.raises(PermissionError):service.export_data(tmp_path/'denied.xlsx',2026)
        assert not (tmp_path/'denied.xlsx').exists()


def test_export_cannot_overwrite_source(tmp_path):
    repo,service,employee=ready(tmp_path)
    with repo:
        service.calculate(employee.id,2026)
        source=tmp_path/'sales.xlsx';original=source.read_bytes()
        with pytest.raises(ValueError):service.export_data(source,2026)
        assert source.read_bytes()==original


def test_all_employee_export_is_numeric_rounded_and_keeps_engine_precision(tmp_path):
    from app.models.domain import Plans
    repo,service,employee=ready(tmp_path)
    with repo:
        service.import_sales(sales_book(tmp_path/'fractional.xlsx',months={1:('10.25','1234.55')},returns={7:('0.25','30.45')}),2026)
        service.save_inputs(employee.id,2026,Plans('1500','1000','1000','1000'))
        snapshot=service.calculate(employee.id,2026)
        service.confirm_payment(snapshot.id,1,Decimal('40.05'))
        latest=service.calculate(employee.id,2026)
        second=repo.resolve_employee('Гайдина')
        service.set_role(second.id,2026,'SUPPORT')
        service.save_inputs(second.id,2026,Plans('1000','1000','1000','1000'),calls_plans=('1000','800','600','900'),calls_facts=('900','0','0','0'))
        service.calculate(second.id,2026)
        output=tmp_path/'all.xlsx';service.export_data(output,2026)
        with_book=load_workbook(output,data_only=False)
        try:
            sheet=with_book['Сводка'];columns={c.value:c.column for c in sheet[1]}
            assert sheet.max_row==9
            rows={sheet.cell(row,columns['квартал']).value:row for row in range(2,sheet.max_row+1)
                  if sheet.cell(row,columns['ФИО']).value==employee.name}
            assert sheet.cell(rows[1],columns['positive sales']).value==1234.6
            assert sheet.cell(rows[3],columns['returns']).value==30.5
            assert sheet.cell(rows[3],columns['actual after returns']).value==-30.5
            assert sheet.cell(rows[1],columns['paid']).value==40.1
            money=sheet.cell(rows[1],columns['positive sales'])
            assert money.data_type=='n' and money.number_format=='[$-419]0.0'
            percent=sheet.cell(rows[1],columns['% выполнения'])
            assert percent.data_type=='n' and percent.value==0.823
            assert percent.number_format=='[$-419]0.0%'
            for row in sheet.iter_rows(min_row=2):
                for header in ('план','positive sales','returns','actual after returns','calls bonus','calculated premium','return clawback','carried clawback','carried in','payable','paid',*(f'eligible base B{i}' for i in range(1,5))):
                    cell=row[columns[header]-1]
                    assert cell.data_type=='n',(header,cell.value)
                    assert cell.number_format=='[$-419]0.0'
            for title in ('Включенные отгрузки','Возвраты','Корректировки','Планы'):
                detail=with_book[title]
                headers={c.value:c.column for c in detail[1]}
                for header in ('Выручка','Сумма','Остаток суммы','Clawback','Цена 1 знак','Порог 1 знак','План отгрузок'):
                    if header in headers:
                        for row in range(2,detail.max_row+1):
                            cell=detail.cell(row,headers[header])
                            assert cell.data_type=='n' and cell.number_format=='[$-419]0.0'
            shipment=with_book['Включенные отгрузки']
            headers={c.value:c.column for c in shipment[1]}
            assert shipment.cell(2,headers['Количество']).value==10.25
            rate=shipment.cell(2,headers['Ставка'])
            assert rate.data_type=='n' and rate.value==0.05
            assert rate.number_format=='[$-419]0.0%'
        finally:with_book.close()
        assert repo.workspace_snapshot(latest.id)==latest
        assert repo.workspace_snapshot(snapshot.id).calculation.quarters[0].actual==Decimal('1234.55')
