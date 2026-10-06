"""Wait for a windowed executable and check its report (also works on Linux)."""
import argparse
import json
from pathlib import Path
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable',type=Path,required=True)
    parser.add_argument('--fixture',type=Path,required=True)
    parser.add_argument('--expected',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args = parser.parse_args()
    executable = args.executable.resolve()
    report = args.report.resolve()
    if not executable.is_file():
        parser.error('Built executable does not exist')
    if report.exists():
        report.unlink()  # Never accept a report from an earlier build.
    completed = subprocess.run([str(executable),'--smoke-test',str(report),
        '--fixture',str(args.fixture.resolve()),'--expected',str(args.expected.resolve())],
        cwd=executable.parent,timeout=120)
    if completed.returncode:
        raise SystemExit(f'Frozen verification failed with exit code {completed.returncode}')
    if not report.is_file():
        raise SystemExit('Frozen verification did not produce a report')
    result = json.loads(report.read_text(encoding='utf-8'))
    if result.get('status') != 'passed':
        raise SystemExit('Frozen verification report did not pass')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
