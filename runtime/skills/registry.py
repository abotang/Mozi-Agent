"""任务 Skill 注册表：目录即能力，manifest 即执行合同。"""

from __future__ import annotations

import json
from pathlib import Path
import re

from runtime.contracts import AGENT_OWNERS, SERVICES, SkillSpec


_SKILL_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class SkillRegistry:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self._skills: dict[str, SkillSpec] = {}

    def load(self) -> "SkillRegistry":
        if not self.root.is_dir():
            raise ValueError(f"Skill 根目录不存在：{self.root}")
        skills: dict[str, SkillSpec] = {}
        for service in SERVICES:
            service_root = self.root / service
            if not service_root.is_dir():
                raise ValueError(f"缺少军种 Skill 目录：{service_root}")
            for manifest in sorted(service_root.glob("*/skill.json")):
                data = json.loads(manifest.read_text(encoding="utf-8-sig"))
                spec = SkillSpec.from_dict(data, manifest)
                if spec.service != service:
                    raise ValueError(f"{manifest} 所在目录与 service 不一致")
                if not _SKILL_ID.fullmatch(spec.skill_id):
                    raise ValueError(f"{manifest} skill_id 格式无效")
                if spec.skill_id in skills:
                    raise ValueError(f"Skill ID 重复：{spec.skill_id}")
                if not (manifest.parent / "SKILL.md").is_file():
                    raise ValueError(f"Skill 缺少 SKILL.md：{manifest.parent}")
                skills[spec.skill_id] = spec
        self._skills = skills
        return self

    def get(self, skill_id: str) -> SkillSpec:
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise ValueError(f"未注册的任务 Skill：{skill_id}") from exc

    def for_service(self, service: str) -> list[SkillSpec]:
        if service not in SERVICES:
            raise ValueError(f"未知军种：{service}")
        return sorted(
            (spec for spec in self._skills.values() if spec.service == service),
            key=lambda item: item.skill_id,
        )

    def all(self) -> list[SkillSpec]:
        return sorted(self._skills.values(), key=lambda item: item.skill_id)

    def for_agent(self, owner: str) -> list[SkillSpec]:
        if owner not in AGENT_OWNERS:
            raise ValueError(f"未知 Agent：{owner}")
        return [spec for spec in self.all() if spec.agent_owner == owner]

    def public_catalog_for_agent(self, owner: str) -> list[dict]:
        return [spec.public_contract() for spec in self.for_agent(owner)]

    def public_catalog(self, service: str) -> list[dict]:
        return [spec.public_contract() for spec in self.for_service(service)]

    def __len__(self) -> int:
        return len(self._skills)
