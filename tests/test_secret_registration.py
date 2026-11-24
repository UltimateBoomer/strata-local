import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).parents[1] / 'scripts'

# A fake podman: secrets and the driver volume as files under TEST_STATE, every call logged to TEST_STATE/calls.
FAKE_PODMAN = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
state = Path(os.environ['TEST_STATE'])
args = sys.argv[1:]
with open(state / 'calls', 'a') as calls:
    calls.write(' '.join(args) + '\n')
secrets = state / 'secrets'
secrets.mkdir(exist_ok=True)
if args[:2] == ['volume', 'create']:
    pass
elif args[:2] == ['volume', 'import']:
    sys.stdin.buffer.read()
elif args[:2] == ['volume', 'inspect']:
    print(state / 'driver-volume')
elif args[:2] == ['secret', 'inspect']:
    file = secrets / args[-1]
    if not file.exists():
        sys.exit(125)
    if '--showsecret' in args:
        if not (state / 'key-decrypts').exists():
            sys.exit(125)
        print('ok')
    else:
        print(json.loads(file.read_text())['spec'])
elif args[:2] == ['secret', 'rm']:
    (secrets / args[2]).unlink()
elif args[:2] == ['secret', 'create']:
    name = args[-2]
    file = secrets / name
    if '--ignore' in args and file.exists():
        sys.exit(0)
    options = args[args.index('--driver-opts') + 1] if '--driver-opts' in args else ''
    lookup = dict(o.split('=', 1) for o in options.split(',')).get('lookup', '') if options else ''
    driver = 'shell' if '--driver=shell' in args else 'file'
    file.write_text(json.dumps({'spec': f'{driver} {lookup}', 'data': sys.stdin.read()}))
else:
    sys.exit(2)
'''


def stub(root, name, body):
    file = root / name
    file.write_text(body if body.startswith('#!') else '#!/bin/sh\n' + body + '\n')
    file.chmod(0o755)


def environment(root, **extra):
    # Keep credential files out of the real home directory, and select the local mode even when the caller targets a
    # remote Podman or the deploy host.
    env = {k: v for k, v in os.environ.items() if k not in ('CONTAINER_CONNECTION', 'CONTAINER_HOST', 'DEPLOY_SSH')}
    return dict(env, PATH=f'{root}:' + os.environ['PATH'], TEST_STATE=str(root),
                XDG_CONFIG_HOME=str(root / 'config'), XDG_DATA_HOME=str(root / 'data'), **extra)


class SecretRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        stub(self.root, 'podman', FAKE_PODMAN)
        self.driver = f'{self.root}/driver-volume/podman-secret-driver.sh'

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args, check=True, **kwargs):
        return subprocess.run([str(SCRIPTS / 'register-podman-secrets.sh'), *args], env=environment(self.root),
                              check=check, capture_output=True, **kwargs)

    def secret(self, name):
        return json.loads((self.root / 'secrets' / name).read_text())

    def calls(self):
        return (self.root / 'calls').read_text().splitlines()

    def test_registration_names_the_driver_volume_and_reuses_ids(self):
        (self.root / 'key-decrypts').touch()
        for _ in range(2):
            self.run_script()
        self.assertEqual(self.secret('strata-local-api-key'),
                         {'spec': f'shell {self.driver} lookup strata_api_key', 'data': 'registered'})
        self.assertEqual(self.secret('strata-local-hf-token')['spec'], f'shell {self.driver} lookup hf_token')
        self.assertFalse([c for c in self.calls() if c.startswith('secret rm')])
        self.assertIn('volume import strata-local-driver -', self.calls())

    def test_registration_replaces_one_naming_another_driver(self):
        (self.root / 'key-decrypts').touch()
        (self.root / 'secrets').mkdir()
        (self.root / 'secrets/strata-local-api-key').write_text(
            json.dumps({'spec': 'shell /old/checkout/scripts/podman-secret-driver.sh lookup strata_api_key', 'data': ''}))
        self.run_script()
        self.assertIn('secret rm strata-local-api-key', self.calls())
        self.assertEqual(self.secret('strata-local-api-key')['spec'], f'shell {self.driver} lookup strata_api_key')

    def test_undecryptable_key_fails_with_hint(self):
        result = self.run_script(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'make set-api-key', result.stderr)

    def test_set_sends_the_value_to_the_driver(self):
        self.run_script('set', 'strata_api_key', input=b'test-key\n')
        secret = self.secret('strata-local-api-key')
        self.assertEqual(secret, {'spec': f'shell {self.driver} lookup strata_api_key', 'data': 'test-key'})
        self.assertIn('--replace', [c for c in self.calls() if c.startswith('secret create')][0].split())

    def test_set_empty_optional_value_sends_remove(self):
        self.run_script('set', 'hf_token', input=b'\n')
        self.assertEqual(self.secret('strata-local-hf-token')['data'], 'strata-local:remove')

    def test_set_empty_api_key_fails_before_podman(self):
        result = self.run_script('set', 'strata_api_key', input=b'\n', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse([c for c in self.calls() if c.startswith('secret create')])

    def test_remote_without_deploy_host_stores_values_with_default_driver(self):
        credentials = self.root / 'config/strata-local/credentials'
        credentials.mkdir(parents=True)
        (credentials / 'strata_api_key.cred').write_text('encrypted\n')
        # base64 of "test-key", as the system credential service returns it.
        stub(self.root, 'varlinkctl', 'printf %s \'{"data": "dGVzdC1rZXk="}\'')
        subprocess.run([str(SCRIPTS / 'register-podman-secrets.sh')],
                       env=environment(self.root, CONTAINER_CONNECTION='remote'), check=True, capture_output=True)
        self.assertEqual(self.calls(), ['secret create --replace strata-local-api-key -',
                                        'secret create --replace strata-local-hf-token -'])
        self.assertEqual(self.secret('strata-local-api-key')['data'], 'test-key\n')
        # The absent optional token is stored as a newline, which Podman accepts.
        self.assertEqual(self.secret('strata-local-hf-token')['data'], '\n')


class SecretDriverStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.credentials = self.root / 'config/strata-local/credentials'
        # The credential service: records its argv and the parameters it reads on stdin.
        stub(self.root, 'varlinkctl', 'printf "%s\\n" "$*" > "$TEST_STATE/varlink-args"; '
                                      'cat > "$TEST_STATE/varlink-stdin"; printf %s \'{"blob": "QkxPQg=="}\'')

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, name, value):
        subprocess.run([str(SCRIPTS / 'podman-secret-driver.sh'), 'store', name], input=value,
                       env=environment(self.root, SECRET_ID='abc123'), check=True, capture_output=True)

    def test_registration_marker_leaves_the_credential(self):
        self.store('strata_api_key', b'registered')
        self.assertFalse((self.root / 'varlink-args').exists())
        self.assertFalse((self.credentials / 'strata_api_key.cred').exists())
        self.assertTrue((self.root / 'data/strata-local/podman-secret-ids/abc123').exists())

    def test_value_is_encrypted_without_reaching_argv(self):
        self.store('strata_api_key', b'new-key')
        self.assertEqual((self.credentials / 'strata_api_key.cred').read_text(), 'QkxPQg==\n')
        self.assertNotIn('new-key', (self.root / 'varlink-args').read_text())
        self.assertNotIn('bmV3LWtleQ', (self.root / 'varlink-args').read_text())
        self.assertEqual(json.loads((self.root / 'varlink-stdin').read_text()),
                         {'name': 'strata_api_key', 'data': 'bmV3LWtleQ==', 'scope': 'user'})

    def test_remove_marker_removes_an_optional_credential(self):
        self.credentials.mkdir(parents=True)
        (self.credentials / 'hf_token.cred').write_text('old\n')
        self.store('hf_token', b'strata-local:remove')
        self.assertFalse((self.credentials / 'hf_token.cred').exists())


if __name__ == '__main__':
    unittest.main()
