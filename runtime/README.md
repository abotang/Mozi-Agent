# 运行时总览

`runtime/` 把当前墨子态势、五个 Agent、任务 Skill 和固定脚本串成可执行链；本目录不保存提示词或某项任务的专属规则。

| 代码/目录 | 职责 |
| --- | --- |
| `__init__.py` | 导出 `dispatch`、`plan_and_dispatch` 和核心数据类型，供其他 Python 代码调用 |
| `__main__.py` | `python -m runtime` 的命令行入口 |
| `contracts.py` | `SkillSpec`、`SkillCall`、`MissionInvocation`、`ExecutionResult` 等跨层数据合同与军种类型边界 |
| `app/` | 用户命令、自动推演循环、监控终端及稳定 API；见 [app/README.md](app/README.md) |
| `mozi/` | 读取墨子实时态势；见 [mozi/README.md](mozi/README.md) |
| `planning/` | 联合/专业 Agent 规划、格式转换和参数校验；见 [planning/README.md](planning/README.md) |
| `skills/` | 扫描 Skill 合同并编译脚本配置；见 [skills/README.md](skills/README.md) |
| `execution/` | PowerShell 7 执行、结果审计和孤立区域清理；见 [execution/README.md](execution/README.md) |

自动链路：`mozi.live_situation` → `app.daemon` → `planning.planner` → `planning.validation` → `skills.compiler` → `execution.executor`。输入输出监控由 `agents/monitor_log.py` 旁路记录。修改某类任务的硬规则请到根目录 `rules/`。
