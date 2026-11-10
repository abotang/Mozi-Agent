# 随项目携带的工具

| 目录 | 用途 |
| --- | --- |
| [powershell7/](powershell7/README.md) | 项目自带的 PowerShell 7 运行时，供固定任务脚本调用 |

工具目录属于部署依赖，不定义任务规则。Python 的 PowerShell 路径选择在 `runtime/execution/executor.py`，可通过 `config/deployment.json` 的 `powershell_executable` 覆盖。换电脑时随项目复制整个 `tools/powershell7/`；不要把 Agent 提示词或任务 Skill 放在这里。
