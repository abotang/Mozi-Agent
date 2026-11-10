# 水面舰艇任务 Skill

海军 Agent 只控制蓝方 `Ship`，不分配潜艇。当前注册两类任务：

| 任务目录 | Skill ID | 关键输入 |
| --- | --- | --- |
| [ship-sea-patrol/](ship-sea-patrol/README.md) | `navy.sea_patrol` | 舰艇 + 巡逻区域；航线可选，只在开局允许新建 |
| [ship-sea-strike/](ship-sea-strike/README.md) | `navy.sea_strike` | 舰艇 + 当前水面接触目标；无目标不创建 |

区域与航线不能互相代替。舰艇巡逻只在首次规划开放的规则位于 `rules/navy_patrol.py`；单位类型、目标接触和重复分配由 `runtime/planning/validation.py` 再次检查。详细参数以各任务目录的 `skill.json` 和 `SKILL.md` 为准。
