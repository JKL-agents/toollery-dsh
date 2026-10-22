"""Download the two official ToolRet evaluation datasets at pinned revisions.

Run with the project's Python. Downloads require network access; analysis does not.
Only Python's standard library is needed for this acquisition step.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen


DATASETS = {
    "ToolRet-Tools": "e06c38c75612b6536bd959e08cdd345894aba6a7",
    "ToolRet-Queries": "b8c76ad3349ff17497b6bdb28bb5b8f61a0f6445",
}
ROOT = Path(__file__).resolve().parents[1]


def fetch(url: str) -> bytes:
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "toollery-benchmark-acquisition"})
            with urlopen(request, timeout=120) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmark" / "toolret")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    old_path = output / "SOURCE.json"
    old = json.loads(old_path.read_text(encoding="utf-8")) if old_path.exists() else {}
    cached = {item["path"]: item for item in old.get("files", [])}
    jobs = []
    datasets = []
    for name, revision in DATASETS.items():
        repo = f"mangopy/{name}"
        metadata = json.loads(fetch(f"https://huggingface.co/api/datasets/{repo}/revision/{revision}"))
        if metadata["sha"] != revision:
            raise ValueError(f"Revision mismatch for {repo}")
        metadata_path = output / "metadata" / f"{name}.json"
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        datasets.append({"repository": repo, "revision": revision, "metadata": metadata_path.relative_to(output).as_posix()})
        for sibling in metadata["siblings"]:
            relative = sibling["rfilename"]
            if relative.endswith(".parquet") or relative in {"README.md", ".gitattributes"}:
                destination = output / "raw" / name / relative
                url = f"https://huggingface.co/datasets/{repo}/resolve/{revision}/{relative}"
                jobs.append((repo, revision, relative, destination, url))

    def download(job: tuple) -> dict:
        repo, revision, relative, destination, url = job
        local = destination.relative_to(output).as_posix()
        prior = cached.get(local)
        if destination.exists() and prior and prior.get("revision") == revision:
            content = destination.read_bytes()
            if hashlib.sha256(content).hexdigest() == prior["sha256"]:
                return prior
        content = fetch(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".part")
        temporary.write_bytes(content)
        temporary.replace(destination)
        return {"path": local, "repository": repo, "revision": revision,
                "source_path": relative, "source_url": url, "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest()}

    files = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        for item in executor.map(download, jobs):
            files.append(item)
            print(f"{item['path']}: {item['bytes']:,} bytes", flush=True)
    manifest = {
        "dataset": "ToolRet official evaluation corpus and queries",
        "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
        "datasets": datasets,
        "files": sorted(files, key=lambda item: item["path"]),
        "raw_bytes": sum(item["bytes"] for item in files),
        "notes": ["Original Parquet files preserved without filtering or relabeling.",
                  "ToolRet-Training-20w and precomputed reranker candidates are not part of this snapshot.",
                  "Repository license metadata, when available, is recorded in metadata; code license is not a dataset license."],
    }
    old_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Complete: {len(files)} files, {manifest['raw_bytes']:,} bytes. Manifest: {old_path}")


if __name__ == "__main__":
    main()
