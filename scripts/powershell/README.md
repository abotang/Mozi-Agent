# PowerShell 7 任务执行脚本

01–07 是按任务分开的固定入口，接收由 Python 编译的 `-ConfigPath`；`-ValidateOnly` 只校验，不创建任务。它们转交同目录的通用执行器，任务细节来自对应 Skill 合同。请通过 `run.py` 或 `runtime.dispatch` 调用，不要使用脚本默认的示例配置文件名执行真实想定。

| 脚本 | 对应 Skill | 区域或目标 |
| --- | --- | --- |
| `01-飞机战斗巡逻.ps1` | `air.combat_patrol` | 必须有巡逻区；启用 1/3 规则 |
| `02-飞机对空打击.ps1` | `air.air_strike` | 当前空中接触目标 |
| `03-飞机对海打击.ps1` | `air.sea_strike` | 当前水面接触目标 |
| `04-飞机对地打击.ps1` | `air.land_strike` | 当前地面接触目标 |
| `05-空中预警.ps1` | `air.early_warning` | 预警机支援区域；固定高度和雷达规则 |
| `06-舰艇海上巡逻.ps1` | `navy.sea_patrol` | 必须有海上巡逻区；航线可选 |
| `07-舰艇对海攻击.ps1` | `navy.sea_strike` | 当前水面接触目标 |
| `08-删除任务及对应区域.ps1` | 独立维护入口 | 删除指定任务；区域仅在满足安全条件时删除 |
| `09-电子干扰伴随.ps1` | 自动推演的独立控制入口 | 打击任务触发后请求干扰机起飞；升空后尾随并开启雷达/电子干扰；结束时关闭设备并请求返航 |
| `Invoke-MoziBlueMissionPlan.ps1` | 七类任务共用 | 校验配置，按顺序创建/更新区域、任务、规则、分配单元和目标，并回读报告 |

所有 POST 请求体由执行器以 UTF-8 字节发送。打击任务必须回读到目标；巡逻任务必须回读到区域。脚本报告和任务 GUID 位于 `runs/<批次>/reports/`。修改 API 调用顺序时检查 `tests/test_powershell_http_utf8.py` 和 `tests/test_script_skill_runtime.py`。
