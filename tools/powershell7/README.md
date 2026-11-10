# PowerShell 7 运行时

这里是项目附带的第三方 PowerShell 程序及其标准模块、语言资源和运行库。`pwsh.exe` 由 `runtime/execution/executor.py` 找到并启动，用于运行 `scripts/powershell/*.ps1`；这些 PowerShell 随附文件不属于本项目的任务逻辑。

`en-US/`、`Modules/`、`preview/`、`ref/`、`Schemas/` 等子目录由 PowerShell 包提供，不在其中逐个添加项目 README，也不要手工改其二进制或模块文件。要改墨子 API 调用，请到 `scripts/powershell/Invoke-MoziBlueMissionPlan.ps1`；要改脚本选择，请到 `runtime/execution/executor.py` 和 `config/deployment.json`。

随附文件中，`Install-PowerShellRemoting.ps1` 配置远程管理，`InstallPSCorePolicyDefinitions.ps1` 安装策略定义，`RegisterManifest.ps1` 注册清单，`RegisterMicrosoftUpdate.ps1` 注册更新；这些安装维护脚本均不在墨子任务执行链上。`pwsh.profile.resource.ps1` 与 `pwsh.profile.dsc.resource.json` 是随附的配置资源；`powershell.config.json`、`pwsh.deps.json`、`pwsh.runtimeconfig.json` 是 PowerShell 自身的配置和 .NET 依赖声明。项目任务代码没有写在这些文件中。
