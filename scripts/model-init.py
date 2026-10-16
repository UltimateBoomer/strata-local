"""Prepare pinned GGUFs and Strata artifacts without running upstream setup.py."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import quote

import requests

ROOT = Path('/opt/strata')
DATA = Path('/data')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as src:
        for block in iter(lambda: src.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def download(session, url, target, size, sha):
    """Reuse existing files by size; always verify downloads before publication."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        verify = os.environ.get('STRATA_VERIFY_EXISTING', '0') == '1'
        if target.stat().st_size == size and (not verify or digest(target) == sha):
            print(f'{"Verified" if verify else "Reusing"} {target.name}', flush=True)
            return
        raise RuntimeError(f'{target} failed verification; remove it and retry')
    partial = target.with_name(target.name + '.partial')
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > size:
        raise RuntimeError(f'{partial} is too large; remove it and retry')
    if offset < size:
        with session.get(url, headers={'Range': f'bytes={offset}-'} if offset else {},
                         stream=True, timeout=(30, 120)) as response:
            response.raise_for_status()
            if offset and (response.status_code != 206 or not
                           response.headers.get('Content-Range', '').startswith(f'bytes {offset}-')):
                raise RuntimeError('Server did not honor the download resume range')
            print(f'Downloading {target.name} from byte {offset}', flush=True)
            with partial.open('ab' if offset else 'wb') as out:
                for block in response.iter_content(8 * 1024 * 1024):
                    out.write(block)
    if partial.stat().st_size != size or digest(partial) != sha:
        raise RuntimeError(f'{partial} failed verification; remove it and retry')
    partial.replace(target)


def tool(name, *args):
    subprocess.run([sys.executable, str(ROOT / 'tools' / name), *map(str, args)],
                   cwd=ROOT, check=True)


def model_files(cfg):
    if not isinstance(cfg.get('files'), list) or not cfg['files'] or not all(isinstance(f, str) for f in cfg['files']):
        raise ValueError('files must be a nonempty list of repository filenames')
    files = list(cfg['files'])
    if cfg.get('vision_file'):
        files.append(cfg['vision_file'])
    if not files:
        raise ValueError('At least one model file is required')
    names = set()
    for filename in files:
        path = Path(filename)
        if path.is_absolute() or '..' in path.parts or not path.name:
            raise ValueError('Invalid model filename')
        if path.name in names or path.name == 'manifest.json' or path.name.endswith('.partial'):
            raise ValueError('Model filenames must have unique, non-reserved basenames')
        names.add(path.name)
    return files


def prepare(cfg, folder, pack):
    """Run explicitly configured tools once per model/preparation configuration."""
    pack.mkdir(parents=True, exist_ok=True)
    complete = pack / '.complete'
    if complete.exists():
        return
    values = {'model': str(folder), 'pack': str(pack), 'root': str(ROOT),
              'first_file': str(folder / Path(cfg['files'][0]).name)}
    for command in cfg.get('prepare', []):
        if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
            raise ValueError('Each prepare command must be a nonempty list of strings')
        name, *args = [arg.format_map(values) for arg in command]
        if Path(name).name != name or not name.endswith('.py'):
            raise ValueError('Preparation tools must be Python filenames in Strata tools/')
        tool(name, *args)
    for copy in cfg.get('copies', []):
        source = Path(copy['source'].format_map(values))
        target = Path(copy['target'].format_map(values))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    complete.write_text('complete\n')


def reuse_cached_file(target, filename, info):
    """Recover downloads from older cache layouts without copying large files."""
    if target.exists():
        return
    for candidate in sorted((DATA / 'models').glob('*/manifest.json')):
        if candidate.parent == target.parent:
            continue
        try:
            metadata = json.loads(candidate.read_text())
        except (OSError, ValueError):
            continue
        matching = any(entry.get('rfilename') == filename and
                       entry.get('lfs', {}).get('sha256') == info['sha256']
                       for entry in metadata.get('siblings', []))
        source = candidate.parent / Path(filename).name
        if matching and source.is_file() and source.stat().st_size == info['size']:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.hardlink_to(source)
            print(f'Reusing cached {target.name} from {candidate.parent.name}', flush=True)
            return


def initialize(cfg, session):
    files = model_files(cfg)
    revision = cfg.get('revision', 'main')
    base = f"https://huggingface.co/{quote(cfg['repository'], safe='/')}"
    response = session.get(
        f"https://huggingface.co/api/models/{quote(cfg['repository'], safe='/')}/revision/{quote(revision, safe='')}",
        params={'blobs': 'true'}, timeout=60)
    response.raise_for_status()
    metadata = response.json()
    resolved = metadata['sha']
    if not re.fullmatch(r'[0-9a-f]{40}', resolved):
        raise RuntimeError('Hugging Face returned an invalid revision')
    if re.fullmatch(r'[0-9a-f]{40}', revision) and resolved != revision:
        raise RuntimeError('Hugging Face returned a different revision')
    # Preserve the original download-cache key. Preparation and projector changes
    # must not invalidate already downloaded model shards.
    identity = hashlib.sha256(json.dumps({
        'repository': cfg['repository'], 'revision': resolved, 'files': cfg['files'],
    }, sort_keys=True).encode()).hexdigest()[:16]
    preparation_identity = hashlib.sha256(json.dumps(
        {**cfg, 'revision': resolved}, sort_keys=True).encode()).hexdigest()[:16]
    folder = DATA / 'models' / identity
    pack = DATA / 'packs' / preparation_identity
    entries = {item['rfilename']: item for item in metadata['siblings']}
    for filename in files:
        if filename not in entries:
            raise ValueError(f'Model file not found in repository: {filename}')
        info = entries[filename].get('lfs')
        if not info or not info.get('sha256') or 'size' not in info:
            raise ValueError(f'Model file requires LFS size and SHA-256 metadata: {filename}')
        target = folder / Path(filename).name
        reuse_cached_file(target, filename, info)
        download(session, f"{base}/resolve/{resolved}/{quote(filename, safe='/')}",
                 target, info['size'], info['sha256'])
    (folder / 'manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    prepare(cfg, folder, pack)
    for name, target in (('model', folder), ('pack', pack)):
        link = DATA / name
        temporary = DATA / (name + '.tmp')
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target, target_is_directory=True)
        temporary.replace(link)
    print('Strata model artifacts ready.', flush=True)


def main():
    cfg = json.loads(Path('/config/model.json').read_text())
    with requests.Session() as session:
        token_file = Path('/run/secrets/hf_token')
        token = token_file.read_text().strip() if token_file.exists() else ''
        if token:
            session.headers['Authorization'] = f'Bearer {token}'
        initialize(cfg, session)


if __name__ == '__main__':
    main()
