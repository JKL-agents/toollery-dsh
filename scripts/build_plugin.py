"""Build an installable DSH bundle; generated copies stay under build/."""
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile


def build(root, *, build_wheel=False):
    root = Path(root).resolve()
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    manifest = json.loads((root / 'plugin/package.json').read_text(encoding='utf-8'))
    if manifest['version'] != version:
        raise ValueError('Plugin and worker versions must match')
    if build_wheel:
        subprocess.run([sys.executable, '-m', 'build', '--wheel'], cwd=root, check=True)
    wheel = root / 'dist' / f'toollery_dsh-{version}-py3-none-any.whl'
    if not wheel.is_file():
        raise FileNotFoundError('Build the Python wheel first, or pass --build-wheel')
    # The npm package embeds this wheel. Reject a stale one instead of shipping
    # updated JavaScript alongside an older Python worker.
    with zipfile.ZipFile(wheel) as archive:
        packaged = set(archive.namelist())
        for package in ('toollery', 'toollery_dsh'):
            for source in (root / package).rglob('*.py'):
                if '__pycache__' in source.parts:
                    continue
                name = source.relative_to(root).as_posix()
                if name not in packaged or archive.read(name) != source.read_bytes():
                    raise ValueError(f'Stale Python wheel at {name}; rerun with --build-wheel')
    stage = root / 'build' / 'plugin'
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(root / 'plugin', stage)
    assets = root / 'toollery_dsh/assets/dsh'
    (stage / 'shared').mkdir()
    for name in ('toollery.mjs', 'sources.mjs', 'skills.mjs'):
        shutil.copyfile(assets / name, stage / 'shared' / name)
    shutil.copytree(assets / 'ui', stage / 'ui', ignore=shutil.ignore_patterns('package.json'))
    client = stage / 'ui/client.js'
    client.write_text(client.read_text(encoding='utf-8').replace("id: '@local/toollery-ui'", "id: '@jkl-agents/dsh-toollery'"), encoding='utf-8')
    shutil.copyfile(root / 'LICENSE', stage / 'LICENSE')
    (stage / 'worker').mkdir()
    shutil.copyfile(wheel, stage / 'worker' / wheel.name)
    output = root / 'dist' / f'jkl-agents-dsh-toollery-{version}.tgz'
    with tarfile.open(output, 'w:gz') as archive:
        archive.add(stage, arcname='package')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-wheel', action='store_true')
    args = parser.parse_args()
    print(build(Path(__file__).resolve().parents[1], build_wheel=args.build_wheel))
