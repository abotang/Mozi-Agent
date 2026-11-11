# 程序入口与自动运行

本目录负责启动方式和轮次编排，调用规划、规则与脚本层；不在这里定义某个任务的 API 请求体。

| 代码 | 作用 |
| --- | --- |
| `cli.py` | `run.py` 实际命令入口；无参数进入 `auto-run`，另提供 `doctor`、`interactive`、`validate`、`execute`、`plan` 等命令 |
| `daemon.py` | 自动循环：采集态势、更新压缩记忆、决定何时唤醒 Agent、去重、脚本预校验、并行执行、持久化结果和失败日志 |
| `api.py` | 供上层程序调用的 `dispatch` 与 `plan_and_dispatch`；`execute=False` 默认只校验 |
| `agent_monitor.py` | `monitor_agents.py` 的终端查看逻辑；读取本地 Agent JSONL 日志 |
| `__init__.py` | 包标记 |

`daemon.py` 的持久状态在 `runs/automatic_state.json`；脚本批次配置和报告在 `runs/<批次>/`。每项任务执行前都做本地校验；执行结果不确定时进入 `uncertain`，避免盲目重复创建。Agent 触发阈值和态势压缩规则见根目录 `rules/`，脚本并发数在 `config/deployment.json`。

规划或参数校验失败不会消耗本轮规划快照，下一次轮询可重试；通过校验后的脚本执行失败仍保留去重与 `uncertain` 锁。查询已有任务遇到接口业务错误时保留原记录，仅成功查询确认不存在时解除任务去重。

任务存在性核对：墨子明确返回 `code=101, message=任务不存在, data=null` 时，删除本地对应的历史任务记录；其它业务错误、格式不完整或网络失败仍保留记录。恢复历史报告时使用相同判断，避免已删除预警任务阻挡开局触发。
