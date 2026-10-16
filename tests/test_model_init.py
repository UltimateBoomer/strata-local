import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('model_init', Path(__file__).parents[1] / 'scripts/model-init.py')
init = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init)


class PreparationTests(unittest.TestCase):
    def response(self, content, status=200, headers=None):
        result = Mock(status_code=status, headers=headers or {})
        result.__enter__ = Mock(return_value=result)
        result.__exit__ = Mock(return_value=False)
        result.iter_content.return_value = [content]
        return result

    def test_resume_checks_hash_before_publishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / 'model.gguf'
            dst.with_suffix('.gguf.partial').write_bytes(b'abc')
            session = Mock()
            session.get.return_value = self.response(b'def', 206, {'Content-Range': 'bytes 3-5/6'})
            init.download(session, 'https://example.com/model', dst, 6, hashlib.sha256(b'abcdef').hexdigest())
            self.assertEqual(dst.read_bytes(), b'abcdef')
            self.assertEqual(session.get.call_args.kwargs['headers'], {'Range': 'bytes=3-'})

    def test_ignored_range_preserves_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / 'model.gguf'
            part = dst.with_suffix('.gguf.partial')
            part.write_bytes(b'abc')
            session = Mock()
            session.get.return_value = self.response(b'abcdef')
            with self.assertRaisesRegex(RuntimeError, 'resume range'):
                init.download(session, 'https://example.com/model', dst, 6, 'unused')
            self.assertEqual(part.read_bytes(), b'abc')
            self.assertFalse(dst.exists())

    def test_corrupt_download_is_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / 'model.gguf'
            session = Mock()
            session.get.return_value = self.response(b'wrong')
            with self.assertRaisesRegex(RuntimeError, 'verification'):
                init.download(session, 'https://example.com/model', dst, 5, hashlib.sha256(b'right').hexdigest())
            self.assertFalse(dst.exists())

    @patch.dict(os.environ, {'STRATA_VERIFY_EXISTING': '1'})
    def test_existing_file_is_verified_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / 'model.gguf'
            dst.write_bytes(b'wrong')
            session = Mock()
            with self.assertRaisesRegex(RuntimeError, 'verification'):
                init.download(session, 'https://example.com/model', dst, 5, hashlib.sha256(b'right').hexdigest())
            session.get.assert_not_called()

    def test_existing_file_skips_checksum_by_default(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            dst = Path(tmp) / 'model.gguf'
            dst.write_bytes(b'wrong')
            session = Mock()
            with patch.object(init, 'digest') as digest:
                init.download(session, 'https://example.com/model', dst, 5, 'unused')
            digest.assert_not_called()
            session.get.assert_not_called()

    @patch.dict(os.environ, {'STRATA_VERIFY_EXISTING': '0'})
    def test_existing_file_with_wrong_size_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / 'model.gguf'
            dst.write_bytes(b'short')
            session = Mock()
            with self.assertRaisesRegex(RuntimeError, 'verification'):
                init.download(session, 'https://example.com/model', dst, 6, 'unused')
            session.get.assert_not_called()

    def test_native_config_uses_stable_artifact_paths(self):
        root = Path(__file__).parents[1]
        cfg = json.loads((root / 'config/server.json').read_text())
        model = json.loads((root / 'config/model.json').read_text())
        args = cfg['args']
        self.assertGreaterEqual(cfg['parallel'], 1)
        self.assertEqual(cfg['tokenizer'], '/data/pack/tokenizer')
        self.assertEqual(args[args.index('--pack') + 1], '/data/pack')
        # Strata finds PLE in the native shards; an alias would duplicate shard 2.
        self.assertNotIn('--ple-gguf', args)
        self.assertEqual(args[args.index('--native') + 1],
                         '/data/model/' + Path(model['files'][0]).name)
        self.assertEqual(cfg['vision']['mmproj'], '/data/model/' + model['vision_file'])
        self.assertEqual(args[args.index('--max-context') + 1], str(cfg['max_context']))

    def test_generic_repository_resolves_revision_and_reuses_preparation(self):
        for files in (['single.gguf'], ['q/part1.gguf', 'q/part2.gguf', 'q/part3.gguf']):
            with self.subTest(files=files), tempfile.TemporaryDirectory() as tmp:
                cfg = {'repository': 'someone/arbitrary-model', 'files': files,
                       'vision_file': 'custom-projector.gguf'}
                sha = 'a' * 40
                session = Mock()
                session.get.return_value.json.return_value = {
                    'sha': sha, 'siblings': [
                        {'rfilename': f, 'lfs': {'size': 3, 'sha256': 'hash'}}
                        for f in files + [cfg['vision_file']]]}
                def downloaded(session, url, target, size, checksum):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b'abc')
                with patch.object(init, 'DATA', Path(tmp)), patch.object(init, 'download', side_effect=downloaded) as download, patch.object(init, 'tool') as tool:
                    init.initialize(cfg, session)
                    self.assertEqual(download.call_count, len(files) + 1)
                    for call in download.call_args_list:
                        self.assertIn('/resolve/' + sha + '/', call.args[1])
                    tool.assert_not_called()
                    self.assertTrue((Path(tmp) / 'model' / Path(files[0]).name).exists())
                    self.assertTrue((Path(tmp) / 'pack' / '.complete').exists())

    def test_preparation_is_explicit_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, pack = Path(tmp) / 'model', Path(tmp) / 'pack'
            cfg = {'files': ['nested/model.gguf'], 'prepare': [
                ['custom.py', '--input', '{first_file}', '--out', '{pack}']]}
            with patch.object(init, 'tool') as tool:
                init.prepare(cfg, folder, pack)
                init.prepare(cfg, folder, pack)
                tool.assert_called_once_with('custom.py', '--input', str(folder / 'model.gguf'), '--out', str(pack))

    def test_preparation_changes_reuse_original_download_directory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(init, 'DATA', Path(tmp)):
            cfg = {'repository': 'owner/model', 'revision': 'a' * 40,
                   'files': ['q/model.gguf']}
            identity = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]
            folder = Path(tmp) / 'models' / identity
            folder.mkdir(parents=True)
            (folder / 'model.gguf').write_bytes(b'abc')
            session = Mock()
            session.get.return_value.json.return_value = {
                'sha': cfg['revision'], 'siblings': [{'rfilename': 'q/model.gguf',
                    'lfs': {'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}}]}
            with patch.object(init, 'tool') as tool, patch.dict(os.environ, {'STRATA_VERIFY_EXISTING': '0'}):
                init.initialize(cfg, session)
                original_pack = (Path(tmp) / 'pack').resolve()
                init.initialize({**cfg, 'prepare': [['custom.py']]}, session)
            # Only metadata requests: no file GETs, even after preparation changes.
            self.assertEqual(session.get.call_count, 2)
            self.assertEqual((Path(tmp) / 'model').resolve(), folder)
            self.assertNotEqual((Path(tmp) / 'pack').resolve(), original_pack)
            tool.assert_called_once_with('custom.py')

    def test_old_cache_file_reused_only_with_matching_checksum_and_size(self):
        for checksum, content, reused in [('expected', b'abc', True),
                                           ('different', b'abc', False),
                                           ('expected', b'ab', False)]:
            with self.subTest(checksum=checksum, content=content), tempfile.TemporaryDirectory() as tmp, patch.object(init, 'DATA', Path(tmp)):
                old = Path(tmp) / 'models' / 'old-config-hash'
                old.mkdir(parents=True)
                source = old / 'model.gguf'
                source.write_bytes(content)
                (old / 'manifest.json').write_text(json.dumps({'siblings': [
                    {'rfilename': 'q/model.gguf', 'lfs': {'sha256': checksum}}]}))
                target = Path(tmp) / 'models' / 'stable-hash' / 'model.gguf'
                init.reuse_cached_file(target, 'q/model.gguf', {'sha256': 'expected', 'size': 3})
                self.assertEqual(target.exists(), reused)
                if reused:
                    self.assertEqual(target.stat().st_ino, source.stat().st_ino)

    @patch.dict(os.environ, {'STRATA_VERIFY_EXISTING': '1'})
    def test_recovered_cache_file_still_obeys_verification_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(init, 'DATA', Path(tmp)):
            old = Path(tmp) / 'models' / 'old'
            old.mkdir(parents=True)
            (old / 'model.gguf').write_bytes(b'bad')
            expected = hashlib.sha256(b'abc').hexdigest()
            (old / 'manifest.json').write_text(json.dumps({'siblings': [
                {'rfilename': 'model.gguf', 'lfs': {'sha256': expected}}]}))
            target = Path(tmp) / 'models' / 'new' / 'model.gguf'
            init.reuse_cached_file(target, 'model.gguf', {'sha256': expected, 'size': 3})
            with self.assertRaisesRegex(RuntimeError, 'verification'):
                init.download(Mock(), 'unused', target, 3, expected)

    def test_failed_preparation_is_not_marked_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / 'pack'
            with patch.object(init, 'tool', side_effect=RuntimeError('failed')):
                with self.assertRaises(RuntimeError):
                    init.prepare({'files': ['model.gguf'], 'prepare': [['custom.py']]}, Path(tmp), pack)
            self.assertFalse((pack / '.complete').exists())

    def test_invalid_file_lists_are_rejected(self):
        for files in ([], ['../model.gguf'], ['/model.gguf'], ['a/m.gguf', 'b/m.gguf'], ['manifest.json'], ['m.gguf.partial']):
            with self.subTest(files=files), self.assertRaises(ValueError):
                init.model_files({'files': files})


if __name__ == '__main__':
    unittest.main()
