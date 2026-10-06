"""Frozen desktop entry plus an explicit CI-only packaged verification command."""
import argparse
import sys


def desktop_main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == '--smoke-test':
        parser = argparse.ArgumentParser(description='Packaged build verification')
        parser.add_argument('--smoke-test', required=True, metavar='REPORT')
        parser.add_argument('--fixture', required=True)
        parser.add_argument('--expected', required=True)
        args = parser.parse_args()
        from app.services.packaging_smoke import run_packaging_smoke
        return run_packaging_smoke(args.smoke_test,args.fixture,args.expected)
    from main import main
    return main()


if __name__ == '__main__':
    raise SystemExit(desktop_main())
