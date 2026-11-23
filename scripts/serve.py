"""Load the Podman secret without persisting it or passing it in argv, write the run configs and start the server."""
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_configs  # noqa: E402

RUN_DIR = Path('/tmp/strata-run')   # the run configs PolyStrata's server reads (/config is read-only)


def main():
    key = Path('/run/secrets/strata_api_key').read_text().strip()
    if not key:
        raise SystemExit('The Strata API key is empty; run make set-api-key.')
    # qwen: /config/server.json and the models it lists, one run config each, the server switching between them
    # ("switch_on_request"); STRATA_VARIANT names the one to start with. Another model: /config/server-<model>.json alone.
    model = os.environ.get('STRATA_MODEL', 'qwen')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', model):
        raise SystemExit(f'Invalid STRATA_MODEL: {model!r}')
    try:
        configs, first = run_configs.build(Path('/config'), model)
    except (OSError, ValueError) as e:
        raise SystemExit(f'Cannot read the configs in /config: {e}')
    start = os.environ.get('STRATA_VARIANT') or first
    if start not in configs:
        raise SystemExit(f'STRATA_VARIANT={start!r} is not one of {", ".join(configs)}.')
    paths = run_configs.write(configs, RUN_DIR)
    os.environ['STRATA_API_KEY'] = key
    os.chdir('/opt/strata/terminal')
    os.execv(sys.executable, [sys.executable, '-m', 'serve.server',
                            '--engine', 'strata', '--config', str(paths[start]),
                            '--host', '0.0.0.0', '--port', '8080'])


if __name__ == '__main__':
    main()
