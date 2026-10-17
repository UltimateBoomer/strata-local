import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('install_quadlet', Path(__file__).parents[1] / 'scripts/install-quadlet.py')
quadlet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quadlet)


class QuadletTests(unittest.TestCase):
    def test_publishes_loopback_once_and_brackets_ipv6(self):
        lines = quadlet.publish_lines({'STRATA_BIND_ADDRESS': '127.0.0.1, 192.168.1.10 fd00::1', 'STRATA_PORT': '9000'})
        self.assertEqual(lines.splitlines(), ['PublishPort=127.0.0.1:9000:8080',
                                              'PublishPort=192.168.1.10:9000:8080',
                                              'PublishPort=[fd00::1]:9000:8080'])

    def test_requires_bind_address(self):
        with self.assertRaises(SystemExit):
            quadlet.publish_lines({})

    def test_environment_overrides_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / '.env'
            env_file.write_text('# comment\nSTRATA_BIND_ADDRESS="10.0.0.2"\nSTRATA_GPU=1\n')
            with patch.dict('os.environ', {'STRATA_GPU': '2'}, clear=True):
                config = quadlet.settings(env_file)
        self.assertEqual(config['STRATA_BIND_ADDRESS'], '10.0.0.2')
        self.assertEqual(config['STRATA_GPU'], '2')

    def test_renders_every_placeholder(self):
        units = quadlet.render({'STRATA_BIND_ADDRESS': '10.0.0.2'})
        for text in units.values():
            self.assertNotIn('@', text)
        self.assertIn(f'Volume={quadlet.REPO}/config/server.json:', units['strata-server.container'])


if __name__ == '__main__':
    unittest.main()
