"""将未引用的旧人物图移至本地备份，并生成小于 50 MB 的安装包。"""
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "data/assets"


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


if __name__ == "__main__":
    manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
    used = set(strings(manifest))
    backup = ROOT / "output/retired-portraits"
    moved = 0
    for path in (ASSETS / "characters").rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".webp", ".png", ".jpg", ".jpeg"}:
            continue
        relative = path.relative_to(ASSETS).as_posix()
        if relative in used:
            continue
        target = backup / path.relative_to(ASSETS)
        path.resolve().relative_to((ASSETS / "characters").resolve())
        target.resolve().relative_to(backup.resolve())
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        moved += 1
    output = ROOT / "output/phantom-archive-update.zip"
    paths = subprocess.check_output(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for relative in sorted(set(paths)):
            path = ROOT / relative
            if relative and path.is_file():
                archive.write(path, relative)
    size = output.stat().st_size
    print(f"Backed up {moved} unused images; ZIP {size} bytes ({size / 1e6:.2f} MB)")
    if size >= 50_000_000:
        raise ValueError("安装包仍超过 50 MB，不应发布")
