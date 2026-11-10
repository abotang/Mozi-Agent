# Skill 发现与参数编译

这里实现**通用机制**；七类任务的具体合同放在根目录 `skills/air/`、`skills/navy/`。

| 代码 | 输入 → 输出 |
| --- | --- |
| `registry.py` | 扫描 `skills/<军种>/<任务>/skill.json` 并验证对应 `SKILL.md` → `SkillSpec` 注册表及供 Agent 使用的精简目录 |
| `compiler.py` | 已校验的 `SkillCall` + 当前态势 → 每项任务独立 JSON 配置和 `MissionInvocation` |
| `__init__.py` | 包标记 |

增加任务时先写 `skill.json` 和 `SKILL.md`，再提供固定脚本；不要让 Agent 临时编写执行逻辑。`compiler.py` 在 `runs/<批次>/configs/` 输出配置，在 `reports/` 预留回读报告位置。脚本选择由 Skill 合同的 `script` 字段决定，详见 `docs/ADD_TASK_SKILL.md`。
