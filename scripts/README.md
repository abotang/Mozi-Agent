# 固定任务脚本

本目录放已实现的执行程序，不放 Agent 提示词。当前七类任务及删除入口在 [powershell/README.md](powershell/README.md)。

`runtime/skills/compiler.py` 按 `skill.json` 为每项调用生成独立配置；`runtime/execution/executor.py` 用项目内 PowerShell 7 执行对应 `.ps1`。任务脚本内部先校验配置，再调用墨子 REST API 并回读任务。命令行直接使用脚本时必须提供真实配置；示例 GUID 不能用于当前想定。新增任务按 `docs/ADD_TASK_SKILL.md` 扩展。
