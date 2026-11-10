# `air.early_warning`：空中预警

此任务**只由预警机 Agent** 调用，一项任务使用一架真实预警机。Agent 填跑马道部署意图；`rules/awacs_area.py` 用最近机场/航母生成区域，`rules/awacs_triggers.py` 决定开局、敌情调整、300 km 威胁回撤和返航换班。预警任务不选打击目标。

| 本目录文件 | 作用 |
| --- | --- |
| `skill.json` | 机器合同：`agent_owner=awacs`、Support 任务和区域参数 |
| `SKILL.md` | 跑马道尺寸、进攻轴向、平台距离、双机间距等详细规则 |
| `README.md` | 文件定位与执行链说明 |

执行链：预警触发 → 区域生成/校验 → `scripts/powershell/05-空中预警.ps1` → 分配预警机并回读高度、雷达与区域规则。脚本中的固定高度和电磁辐射设置不由 Agent 临时编写。
