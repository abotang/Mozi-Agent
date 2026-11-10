# 任务 Skill 合同

一个子目录代表一种可复用任务。Agent 只填合同允许的参数；`runtime/skills/registry.py` 启动时扫描 `skills/<军种>/<任务>/skill.json`，执行逻辑仍是 `scripts/powershell/` 中已经写好的脚本。

| 目录 | 当前内容 |
| --- | --- |
| [air/](air/README.md) | 五类航空任务，预警任务归预警机 Agent |
| [navy/](navy/README.md) | 两类水面舰艇任务 |
| [army/](army/README.md) | 陆军任务预留，目前没有可执行 Skill |

每个任务目录包含 `skill.json`（机器可读合同）、`SKILL.md`（Agent/开发者说明）和 `README.md`（这个目录的文件与调用路径）。注册表要求合同与 `SKILL.md` 同时存在。新增任务请看 `docs/ADD_TASK_SKILL.md`；不能只增加提示词而不提供合同与固定执行脚本。
