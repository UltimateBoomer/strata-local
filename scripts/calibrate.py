"""Run PolyStrata's tools/calibrate.py on one model's run config (STRATA_VARIANT, else the first), made as serve.py
makes it."""
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_configs  # noqa: E402


def main():
    configs, first = run_configs.build(Path('/config'), os.environ.get('STRATA_MODEL', 'qwen'))
    name = os.environ.get('STRATA_VARIANT') or first
    if name not in configs:
        raise SystemExit(f'STRATA_VARIANT={name!r} is not one of {", ".join(configs)}.')
    path = run_configs.write({name: configs[name]}, Path('/tmp/calibrate'))[name]
    os.chdir('/opt/strata/terminal')
    os.execv(sys.executable, [sys.executable, 'tools/calibrate.py', str(path)])


if __name__ == '__main__':
    main()
