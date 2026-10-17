"""Render the Quadlet units for this checkout into the current user's Quadlet directory."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
UNITS = ('strata-data.volume', 'strata-model-init.container', 'strata-server.container')
GENERATORS = ('/usr/libexec/podman/quadlet', '/usr/lib/podman/quadlet')


def settings(env_file):
    """Read Compose-style KEY=VALUE lines; the process environment takes precedence, as in Compose."""
    values = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
                value = value[1:-1]
            values[key.strip()] = value
    return {**values, **{k: v for k, v in os.environ.items() if k.startswith('STRATA_')}}


def publish_lines(config):
    addresses = re.split(r'[\s,]+', config.get('STRATA_BIND_ADDRESS', '').strip())
    addresses = [a for a in addresses if a]
    if not addresses:
        raise SystemExit('Set STRATA_BIND_ADDRESS in .env')
    port = config.get('STRATA_PORT') or '9931'
    if not port.isdigit():
        raise SystemExit(f'Invalid STRATA_PORT: {port}')
    lines = []
    # Loopback is always published, as in compose.yaml.
    for address in dict.fromkeys(['127.0.0.1', *addresses]):
        host = f'[{address}]' if ':' in address else address
        lines.append(f'PublishPort={host}:{port}:8080')
    return '\n'.join(lines)


def render(config):
    replacements = {
        '@REPO@': str(REPO),
        '@PUBLISH@': publish_lines(config),
        '@GPU@': config.get('STRATA_GPU') or '0',
        '@VERIFY_EXISTING@': config.get('STRATA_VERIFY_EXISTING') or '0',
    }
    units = {}
    for name in UNITS:
        text = (REPO / 'quadlet' / name).read_text()
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        units[name] = text
    return units


def validate(units):
    """Run the Quadlet generator over the rendered units without installing them."""
    generator = next((g for g in GENERATORS if Path(g).exists()), None)
    if generator is None:
        raise SystemExit('The Podman Quadlet generator was not found')
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in units.items():
            (Path(tmp) / name).write_text(text)
        result = subprocess.run([generator, '-dryrun', '-user'], env=dict(os.environ, QUADLET_UNIT_DIRS=tmp),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise SystemExit(result.stderr)


def main():
    units = render(settings(REPO / '.env'))
    validate(units)
    if sys.argv[1:] == ['--check']:
        return
    config_home = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    target = config_home / 'containers/systemd/strata-local'
    target.mkdir(parents=True, exist_ok=True)
    for name, text in units.items():
        # Write beside the target and rename, so the generator never reads a partial unit.
        partial = target / f'.{name}.tmp'
        partial.write_text(text)
        partial.replace(target / name)
    print(f'Installed Quadlet units in {target}', file=sys.stderr)


if __name__ == '__main__':
    main()
