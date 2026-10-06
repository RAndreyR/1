from pathlib import Path
import subprocess
import sys
from tests.excel_helpers import make_workbook
from tests.workbook_fixture import FIXTURE

PROJECT = Path(__file__).parents[1]


def run_cli(*args):
    return subprocess.run([sys.executable, str(PROJECT/'main.py'), *map(str,args)],
                          cwd=PROJECT, capture_output=True, text=True, encoding='utf-8')


def test_cli_check_real_fixture():
    result = run_cli('check', FIXTURE)
    assert result.returncode == 0, result.stderr
    assert '170' in result.stdout and '28' in result.stdout
    assert 'Готов к расчету: нет' in result.stdout
    assert 'row:103' in result.stdout


def test_cli_rejects_pending_mappings_without_explicit_decision():
    result = run_cli('calculate', FIXTURE, '--plans','20000000','10000000','0','0')
    assert result.returncode == 2
    assert 'разрешите все сопоставления' in result.stderr


def test_cli_real_fixture_explicitly_unmapped_regression():
    result = run_cli('calculate', FIXTURE, '--plans','20000000','10000000','0','0','--leave-all-unmatched')
    assert result.returncode == 0, result.stderr
    assert 'Готов к расчету: да' in result.stdout
    assert 'Q4:' in result.stdout and '112 087,85 ₽' in result.stdout
    assert 'Итого к выплате: 251 283,32 ₽' in result.stdout


def test_cli_remember_then_reload(tmp_path):
    rows = [['1 кв.', 'Клиент', 'ЛПУ', 'старый', 10, 1000, 100, None, None, None]]
    path = make_workbook(tmp_path/'input.xlsx',shipments=rows)
    db = tmp_path/'aliases.sqlite'
    result = run_cli('calculate',path,'--plans','0','0','0','0', '--db',db,
                     '--map','старый','Товар','--remember-mappings')
    assert result.returncode == 0, result.stderr
    result = run_cli('calculate',path,'--plans','0','0','0','0','--db',db)
    assert result.returncode == 0, result.stderr
    assert 'Готов к расчету: да' in result.stdout
    assert 'Итого к выплате: 50,00 ₽' in result.stdout


def test_cli_invalid_plans_do_not_save_alias(tmp_path):
    rows = [['1 кв.', 'Клиент', 'ЛПУ', 'старый', 10, 1000, 100, None, None, None]]
    path = make_workbook(tmp_path/'input.xlsx',shipments=rows)
    db = tmp_path/'aliases.sqlite'
    result = run_cli('calculate',path,'--plans','-1','0','0','0','--db',db,
                     '--map','старый','Товар','--remember-mappings')
    assert result.returncode == 2
    assert not db.exists()


def test_cli_blank_product_individual_mapping(tmp_path):
    rows = [['1 кв.', 'Клиент', 'ЛПУ', None, 10, 1000, 100, None, None, None],
            ['1 кв.', 'Клиент', 'ЛПУ', None, 10, 1000, 100, None, None, None]]
    path = make_workbook(tmp_path/'input.xlsx',shipments=rows)
    result = run_cli('calculate',path,'--plans','0','0','0','0', '--map-row','3','Товар','--leave-row','4')
    assert result.returncode == 0, result.stderr
    assert 'Итого к выплате: 50,00 ₽' in result.stdout
