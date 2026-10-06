"""Verify frozen Qt/plugins/Excel/Decimal/SQLite in the actual built process.

Input fixture and frozen expectations are supplied externally by CI, not bundled.
No business formulas or alternate calculation engine live here.
"""
from decimal import Decimal
import json
import os
from pathlib import Path
import platform
import tempfile
import traceback


def run_packaging_smoke(report_path: str, fixture_path: str, expected_path: str) -> int:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    report = Path(report_path).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    status = {'status':'failed', 'platform':platform.system(), 'checks':[]}
    window = None
    try:
        from PySide6.QtCore import QElapsedTimer, QTimer
        from PySide6.QtWidgets import QApplication
        from app.ui.main_window import MainWindow
        from app.utils.normalization import format_money

        expected = json.loads(Path(expected_path).read_text(encoding='utf-8'))
        fixture = Path(fixture_path).resolve()
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(prefix='kpi-kam-build-') as directory:
            window = MainWindow(Path(directory)/'kpi_kam.db')
            window.new_page.employee.setText('Проверка сборки')
            window.new_page.year.setValue(2026)
            # Explicit regression-test plans; never read plans from the workbook.
            for field,value in zip(window.new_page.plan_inputs,('20000000','10000000','0','0')):
                field.setText(value)
            window.new_page.source.setText(str(fixture))
            window.show()
            timer = QElapsedTimer()
            timer.start()
            failures = []

            def finish_checks() -> None:
                if window._worker is not None:
                    if timer.elapsed() < 60_000:
                        QTimer.singleShot(25,finish_checks)
                        return
                    failures.append('Excel import did not finish within 60 seconds')
                    app.quit()
                    return
                try:
                    session = window.service.session
                    if session is None or session.imported.errors:
                        raise AssertionError(window.error_label.text() or 'Packaged Excel import failed')
                    if session.imported.source_hash != expected['fixture_sha256']:
                        raise AssertionError('Unexpected fixture hash')
                    if len(session.imported.shipments) != expected['shipments'] or len(session.imported.products) != expected['products']:
                        raise AssertionError('Unexpected imported row/product counts')
                    window.navigate(2)
                    for entry in session.mappings:
                        if entry.status == 'unresolved':
                            row = next(i for i,e in enumerate(session.mappings) if e.key == entry.key)
                            window.mapping_page.grid.selectRow(row)
                            window.mapping_page.leave_button.click()
                    if not window.mapping_page.calculate_button.isEnabled():
                        raise AssertionError('Packaged calculation button is not enabled')
                    window.mapping_page.calculate_button.click()
                    if window.saved is None:
                        raise AssertionError(window.error_label.text() or 'Packaged calculation failed')
                    result = window.saved.result
                    for quarter in result.quarters:
                        key = str(quarter.quarter)
                        if quarter.actual != Decimal(expected['quarter_actuals'][key]):
                            raise AssertionError(f'Actual regression mismatch Q{key}')
                        if quarter.calculated_premium != Decimal(expected['quarter_premiums'][key]):
                            raise AssertionError(f'Premium regression mismatch Q{key}')
                        if [b.base for b in quarter.blocks] != [Decimal(v) for v in expected['block_bases'][key]]:
                            raise AssertionError(f'Block regression mismatch Q{key}')
                    if len(result.included_rows) != expected['included_count']:
                        raise AssertionError('Included row count mismatch')
                    for row,reference in zip(result.audit,expected['audit'],strict=True):
                        if (row.shipment.source_row,row.eligible,row.exclusion_reason) != (reference['source_row'],reference['eligible'],reference['reason']):
                            raise AssertionError('Packaged source-row audit mismatch')
                    if window.repository.load_calculation(window.saved.id).result != result:
                        raise AssertionError('SQLite snapshot did not round-trip exactly')
                    window.open_history(window.saved.id)
                    window.show_premium(4)
                    if window.premium_page.cards['payable'].value.text() != format_money(result.quarters[3].payable):
                        raise AssertionError('Packaged premium display mismatch')
                    window.show_audit(result.quarters[0].blocks[0].source_rows,'Packaging verification')
                    if window.audit_page.selection.included_revenue != result.quarters[0].blocks[0].base:
                        raise AssertionError('Packaged drill-down mismatch')
                    if window.grab().isNull():
                        raise AssertionError('Qt rendering failed')
                    status.update(status='passed',checks=['Qt window/rendering','background Excel import',
                        'explicit product mapping','Decimal workbook regression','audit drill-down','SQLite history'],
                        shipment_count=len(result.audit),included_count=len(result.included_rows),
                        annual_actual=str(result.annual_actual),annual_premium=str(result.annual_calculated_premium))
                except Exception:
                    failures.append(traceback.format_exc())
                finally:
                    app.quit()

            window.new_page.check_button.click()
            QTimer.singleShot(25,finish_checks)
            app.exec()
            window.close()
            if failures:
                raise AssertionError('\n'.join(failures))
            if status['status'] != 'passed':
                raise AssertionError('Packaging checks did not complete')
        report.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
        return 0
    except Exception:
        if window is not None:
            window.close()
        status['error'] = traceback.format_exc()
        report.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
        return 1
