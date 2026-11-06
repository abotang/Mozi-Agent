[CmdletBinding()]
param([string]$ConfigPath = (Join-Path $PSScriptRoot '墨子蓝方七类任务配置.json'), [switch]$ValidateOnly)
& (Join-Path $PSScriptRoot 'Invoke-MoziBlueMissionPlan.ps1') -ConfigPath $ConfigPath -Task airSeaStrike -ValidateOnly:$ValidateOnly
