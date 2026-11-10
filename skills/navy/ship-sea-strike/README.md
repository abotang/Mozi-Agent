# `navy.sea_strike`：舰艇对海攻击

海军 Agent 选蓝方水面舰艇，以及**当前** `contacts.sea` 中的敌方水面接触 GUID。脚本创建 Strike/SEA 任务、分配舰艇并绑定目标；任务不使用巡逻区域。

| 本目录文件 | 作用 |
| --- | --- |
| `skill.json` | 机器合同：Ship 单元、Ship 接触目标及固定脚本 |
| `SKILL.md` | 当前接触目标的选择规则与禁用条件 |
| `README.md` | 文件定位与执行链说明 |

执行链：目标和军种校验 → 配置编译 → `scripts/powershell/07-舰艇对海攻击.ps1` → 墨子目标分配与回读。没有当前水面目标时不创建；红方真实单元 GUID 不能代替蓝方接触 GUID。
