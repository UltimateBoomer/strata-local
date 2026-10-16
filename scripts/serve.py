"""Load the Podman secret without persisting it or passing it in argv."""
import os
from pathlib import Path
import sys


def main():
    key = Path('/run/secrets/strata_api_key').read_text().strip()
    if not key:
        raise SystemExit('The Strata API key is empty; run make set-api-key.')
    if not Path('/config/server.json').is_file():
        raise SystemExit('Missing native Strata config at /config/server.json.')
    os.environ['STRATA_API_KEY'] = key
    os.chdir('/opt/strata')
    os.execv(sys.executable, [sys.executable, '-m', 'serve.server',
                            '--engine', 'strata', '--config', '/config/server.json',
                            '--host', '0.0.0.0', '--port', '8080'])


if __name__ == '__main__':
    main()
