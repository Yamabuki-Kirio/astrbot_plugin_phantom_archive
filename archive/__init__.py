"""夜幕档案查询层。

对外只暴露 ArchiveService；内部模块职责：
    database.py    只读连接、路径解析、结构校验
    repository.py  参数化 SQL
    search.py      分级检索
    renderer.py    带预算的排版
    notices.py     提示文案唯一出处
    models.py      行模型
"""

from .models import Character, CharacterProfile, SearchResult, Skill, Weapon
from .notices import DISCLAIMER, NUMERIC_CONFLICT_WARNING
from .service import ArchiveService

__all__ = [
    "ArchiveService",
    "Character",
    "CharacterProfile",
    "SearchResult",
    "Skill",
    "Weapon",
    "DISCLAIMER",
    "NUMERIC_CONFLICT_WARNING",
]
