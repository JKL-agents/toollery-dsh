"""Seed the isolated MCP playground without overwriting existing user files."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def setup():
    target = ROOT / 'work/dsh-filesystem-playground'
    target.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / 'examples/dsh/playground').iterdir():
        destination = target / source.name
        if source.is_file() and not destination.exists():
            shutil.copyfile(source, destination)
    return target


if __name__ == '__main__':
    print(setup())
