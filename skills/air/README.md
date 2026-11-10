# 航空任务 Skill

这里的五个任务都由飞机执行，但规划归属分开：空军 Agent 负责非预警飞机，预警机 Agent 独占 `air.early_warning`。每个子目录的 `skill.json` 对应一个固定 PowerShell 入口。

| 任务目录 | Skill ID | 关键输入 |
| --- | --- | --- |
| [air-combat-patrol/](air-combat-patrol/README.md) | `air.combat_patrol` | 飞机 + 巡逻区域 |
| [air-air-strike/](air-air-strike/README.md) | `air.air_strike` | 飞机 + 当前空中接触目标 |
| [air-sea-strike/](air-sea-strike/README.md) | `air.sea_strike` | 飞机 + 当前水面接触目标 |
| [air-land-strike/](air-land-strike/README.md) | `air.land_strike` | 飞机 + 当前地面接触目标 |
| [air-early-warning/](air-early-warning/README.md) | `air.early_warning` | 预警机 + 跑马道部署参数 |

巡逻/预警需区域，打击需目标接触，不能互相代替。目标 GUID 只能从当轮蓝方 `contacts` 取得；历史态势仅供判断。统一的目标、区域和军种校验在 `runtime/planning/validation.py`，预警专属几何与触发条件在 `rules/`。
