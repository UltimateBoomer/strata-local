import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import run_configs  # noqa: E402

CONFIG = Path(__file__).parents[1] / 'config'
# the fork's engine options and settings that PolyStrata's engine and server do not have
FORK_ONLY = ('--conversation-cache-dir', '--conversation-cache-skip-tail', '--conversation-cache-live-tail')


class RunConfigTests(unittest.TestCase):
    def test_checked_in_models(self):
        configs, first = run_configs.build(CONFIG)
        self.assertEqual(first, 'q1')
        self.assertEqual(set(configs), {'q1', 'q3', 'inkling'})
        for name, cfg in configs.items():
            self.assertTrue(cfg['switch_on_request'], name)
            self.assertIsInstance(cfg['args'], list, name)
            self.assertFalse({'engine', 'engine_env', 'models', 'default'} & set(cfg), name)
            self.assertFalse(set(FORK_ONLY) & set(cfg['args']), name)
            self.assertNotIn('STRATA_LOAD_IMAGE', cfg.get('env', {}), name)
        for q in ('q1', 'q3'):
            args = configs[q]['args']
            self.assertEqual(args[args.index('--pack') + 1], f'/data/pack-{q}')
            self.assertIn('--max-context', args)
            self.assertEqual(configs[q]['env']['STRATA_GFX11_WMMA_DEFAULTS'], '0')
            self.assertEqual(configs[q]['aliases'], [q])
            self.assertEqual(configs[q]['exe'], '/opt/strata/engine/strata')
        ink = configs['inkling']
        self.assertEqual(ink['exe'], '/opt/strata/engine/strata-poly')
        self.assertNotIn('env', ink)                     # none of the Qwen engine's switches
        self.assertNotIn('vision', ink)
        self.assertIn('--kv-whole', ink['args'])

    def test_merge(self):
        common = {'exe': 'a', 'backend': 'hip', 'engine': {'x': 1, 'y': 2}, 'models': {}, 'switch_on_request': True}
        self.assertEqual(run_configs.merge(common, {'engine': {'y': 3}}),
                         {'exe': 'a', 'backend': 'hip', 'engine': {'x': 1, 'y': 3}, 'switch_on_request': True})
        self.assertEqual(run_configs.merge(common, {'exe': 'b', 'args': []}),
                         {'backend': 'hip', 'switch_on_request': True, 'exe': 'b', 'args': []})

    def test_write_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'strata-old.json').write_text('{}')
            paths = run_configs.write({'a': {'exe': 'x', 'args': []}}, Path(tmp))
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ['strata-a.json'])
            self.assertEqual(json.loads(paths['a'].read_text())['exe'], 'x')

    def test_alone(self):
        configs, first = run_configs.build(CONFIG, 'inkling')
        self.assertEqual((list(configs), first), (['inkling'], 'inkling'))


if __name__ == '__main__':
    unittest.main()
