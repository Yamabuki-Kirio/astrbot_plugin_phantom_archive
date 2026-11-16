"""本地官方角色信息图；别名精确匹配，避免特殊形态命中原版。"""

from __future__ import annotations

import json
from pathlib import Path
import unicodedata


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).casefold()
    return "".join(c for c in text if not c.isspace() and c not in "·•・.-_/")


def find_profile_images(data_dir: Path, name: str) -> tuple[Path, ...]:
    key = normalize_name(name)
    if not key:
        return ()
    root = (data_dir / "official_profiles").resolve()
    try:
        profiles = json.loads((root / "index.json").read_text(encoding="utf-8"))
        for profile in profiles:
            names = [profile["name"], *profile.get("aliases", [])]
            if key not in {normalize_name(value) for value in names}:
                continue
            images = tuple((root / value).resolve() for value in profile["images"])
            if images and all(path.is_relative_to(root) and path.is_file() for path in images):
                return images
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return ()
