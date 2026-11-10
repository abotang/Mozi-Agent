# `air.land_strike`：飞机对地打击

这是空军飞机执行的任务，不归陆军 Agent。空军 Agent 从**当前** `contacts.land` 选择地面设施接触，给适合的飞机创建 Strike/land 任务；不填写巡逻区域。

| 本目录文件 | 作用 |
| --- | --- |
| `skill.json` | 机器合同：Facility 目标类型、单位类型、脚本映射 |
| `SKILL.md` | 地面目标选择与禁用条件 |
| `README.md` | 文件定位与执行链说明 |

执行链：规划校验 → 配置编译 → `scripts/powershell/04-飞机对地打击.ps1` → 目标绑定与回读。没有当前合法地面接触时不创建任务。
