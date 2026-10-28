import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import engine_config  # noqa: E402


class ExpandTests(unittest.TestCase):
    def test_fields_become_args_and_env(self):
        cfg = engine_config.expand({
            'log': '/data/x.log',
            'args': ['--kv', 'int8'],
            'env': {'HIP_FOO': 'bar'},
            'engine': {'conversation_cache_mib': 16384, 'spec_min_p': 0.7, 'resident_experts': True,
                       'vision': False, 'prefill': None},
            'engine_env': {'load_image': '/data/load-image/q2', 'unbuffered_load': True, 'off': False}})
        self.assertEqual(cfg, {
            'log': '/data/x.log',
            'args': ['--kv', 'int8', '--conversation-cache-mib', '16384', '--spec-min-p', '0.7', '--resident-experts'],
            'env': {'HIP_FOO': 'bar', 'STRATA_LOAD_IMAGE': '/data/load-image/q2', 'STRATA_UNBUFFERED_LOAD': '1'}})

    def test_option_set_twice_is_refused(self):
        with self.assertRaisesRegex(ValueError, '--kv'):
            engine_config.expand({'args': ['--kv', 'int8'], 'engine': {'kv': 'int8'}})
        with self.assertRaisesRegex(ValueError, 'STRATA_LOAD_IMAGE'):
            engine_config.expand({'env': {'STRATA_LOAD_IMAGE': 'a'}, 'engine_env': {'load_image': 'b'}})

    def test_list_value_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'engine.layer_split'):
            engine_config.expand({'engine': {'layer_split': [1, 2]}})

    def test_materialize_expands_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / 'src', Path(tmp) / 'out'
            src.mkdir()
            (src / 'server.json').write_text(json.dumps({'models': {'a': 'server-a.json'}, 'engine': {'spec': 4}}))
            (src / 'server-a.json').write_text(json.dumps({'engine': {'pack': '/data/pack-a'}}))
            main = engine_config.materialize(src / 'server.json', out)
            self.assertEqual(json.loads(main.read_text()), {'models': {'a': 'server-a.json'}, 'args': ['--spec', '4']})
            self.assertEqual(json.loads((out / 'server-a.json').read_text()), {'args': ['--pack', '/data/pack-a']})

    def test_checked_in_configs_expand(self):
        config = Path(__file__).parents[1] / 'config'
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('server.json', 'server-deepseek.json'):
                cfg = json.loads(engine_config.materialize(config / name, Path(tmp)).read_text())
                self.assertNotIn('engine', cfg)
            self.assertIn('--prompt-cache', json.loads((Path(tmp) / 'server.json').read_text())['args'])


if __name__ == '__main__':
    unittest.main()
