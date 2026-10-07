"""Exercise the diagnostic entry point in a separate real Qt process."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT / 'tests/fixtures/KPI Трофимов Дмитрий 2026.xlsm'
EXPECTED = PROJECT / 'tests/fixtures/trofimov_expected.json'


def run_diagnostic(report, fixture=FIXTURE, expected=EXPECTED):
    environment = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'PYTHONUTF8': '1'}
    # Use another cwd to catch paths accidentally dependent on the source tree.
    return subprocess.run(
        [sys.executable, str(PROJECT / 'desktop_entry.py'),
         '--smoke-test', str(report), '--fixture', str(fixture),
         '--expected', str(expected)],
        cwd=report.parent, env=environment, capture_output=True, text=True,
        encoding='utf-8', timeout=120,
    )


def test_packaging_diagnostic_runs_real_gui_and_regression(tmp_path):
    directory = tmp_path / 'Проверка сборки с пробелами'
    directory.mkdir()
    report = directory / 'report.json'
    completed = run_diagnostic(report)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(report.read_text(encoding='utf-8'))
    assert result['status'] == 'passed'
    assert result['shipment_count'] == 170
    assert result['included_count'] == 64
    assert result['annual_premium'] == '251283.3171100000090'
    assert 'SQLite history' in result['checks']
    assert 'common sales and external price' in result['checks']
    assert 'paid return clawback' in result['checks']
    assert list(directory.iterdir()) == [report]


@pytest.mark.parametrize('failure', ['missing_workbook', 'wrong_regression_hash'])
def test_packaging_diagnostic_returns_failure_report(tmp_path, failure):
    report = tmp_path / 'report.json'
    fixture = FIXTURE
    expected = EXPECTED
    if failure == 'missing_workbook':
        fixture = tmp_path / 'missing.xlsm'
    else:
        data = json.loads(EXPECTED.read_text(encoding='utf-8'))
        data['fixture_sha256'] = 'wrong-hash'
        expected = tmp_path / 'wrong-expected.json'
        expected.write_text(json.dumps(data), encoding='utf-8')
    completed = run_diagnostic(report, fixture, expected)
    assert completed.returncode == 1, completed.stderr
    result = json.loads(report.read_text(encoding='utf-8'))
    assert result['status'] == 'failed'
    assert result['checks'] == []
    assert result['error']
