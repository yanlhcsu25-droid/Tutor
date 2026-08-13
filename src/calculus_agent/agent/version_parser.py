"""Narrow deterministic parser for existing paper-version operations."""

import re

from pydantic import BaseModel, Field
from typing import Literal


class VersionOperationIntent(BaseModel):
    action: Literal["undo", "redo", "restore"]
    target_version: int | None = Field(default=None, ge=1)
    need_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)


def parse_version_operation_intent(message: str) -> VersionOperationIntent | None:
    text = message.strip() if isinstance(message, str) else ""
    if text in {"撤销", "撤销刚才的修改", "撤回刚才的修改", "回到修改前"}:
        return VersionOperationIntent(action="undo")
    if text in {"重做", "恢复刚才撤销的修改", "重新应用刚才的修改"}:
        return VersionOperationIntent(action="redo")
    if text == "恢复到上一版":
        return VersionOperationIntent(action="undo")
    match = re.fullmatch(r"(?:恢复到|回到)\s*(?:第|版本)?\s*(\d+)\s*(?:版)?", text)
    if match:
        return VersionOperationIntent(action="restore", target_version=int(match.group(1)))
    return None
