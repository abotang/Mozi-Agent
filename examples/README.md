# 离线调用示例

这些文件用于学习结构化参数和做本地预校验。示例中的 GUID 是占位样例，不属于当前墨子想定；执行真实任务必须换成当轮蓝方单位与接触 GUID。

| 文件 | 作用 |
| --- | --- |
| `situation.example.json` | 蓝方、己方单位、敌方接触、想定时间的示例态势 |
| `plan.multiple.example.json` | 多条 Skill 调用的结构化方案；演示巡逻区与打击目标的不同字段 |
| `agent_dispatch.py` | 调用 `runtime.dispatch(..., execute=False)` 做脚本参数校验，不创建墨子任务 |

在项目根目录运行 `python examples/agent_dispatch.py` 可查看校验结果。需要真实执行时，先从当前想定采集态势并完成参数校验，再显式使用执行入口；不要直接拿示例 GUID 下发。
