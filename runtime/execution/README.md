# 固定脚本执行层

本目录接收已经校验并编译好的任务调用，运行 PowerShell 7，并核对执行结果。Agent 不在这里临时生成代码。

| 代码 | 输入 → 输出 |
| --- | --- |
| `orchestrator.py` | 结构化方案与态势 → 校验、编译、脚本执行及 `audit.json`；手动批量入口 |
| `executor.py` | `MissionInvocation` → PowerShell 7 进程、stdout JSON、任务 GUID 和报告路径校验；定义 `ScriptExecutionError` |
| `area_cleanup.py` | 本项目创建区域的报告 + 当前墨子任务关联 → 仅删除无任务引用的孤立区域与专属参考点 |
| `jammer_controller.py` | 已创建的飞机打击任务 + 实时飞机态势 → 空闲干扰机出动、后方伴随、设备开启与停止；调用 `09-电子干扰伴随.ps1` |
| `__init__.py` | 包标记 |

自动推演的任务级并发由 `runtime/app/daemon.py` 管理；单项任务内部仍按区域、任务、规则、单位与目标分配、回读顺序执行。具体墨子 API 步骤在 `scripts/powershell/Invoke-MoziBlueMissionPlan.ps1`。区域清理只处理报告可证明由本项目创建、且墨子确认没有任务引用的区域。
