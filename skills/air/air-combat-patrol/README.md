# `air.combat_patrol`：飞机战斗巡逻

由空军 Agent 选择蓝方非预警飞机和多边形巡逻区；脚本创建/复用区域、创建 Patrol 任务、分配飞机，并启用墨子“1/3规则”。巡逻区不是航线，任务不选打击目标。

| 本目录文件 | 作用 |
| --- | --- |
| `skill.json` | 机器合同：军种、任务类型、区域要求、脚本文件和可填参数 |
| `SKILL.md` | 巡逻区点、已有区域、换挂等使用规则与示例 |
| `README.md` | 文件定位与执行链说明 |

执行链：`runtime/planning/validation.py` 校验 → `runtime/skills/compiler.py` 编译配置 → `scripts/powershell/01-飞机战斗巡逻.ps1` → 共用执行器回读任务和区域。多次创建只需多条不同任务名与参数的 Skill 调用。
