[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$MissionGuid,

    [string]$AreaGuid = '',

    [Parameter(Mandatory)]
    [string]$SideGuid,

    [Parameter(Mandatory)]
    [string]$SideName,

    [string]$ApiBase = 'http://127.0.0.1:8050/ApiCtrl/api',

    [string]$ReportPath = '',

    [switch]$AllowSharedAreaDeletion,

    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Test-Guid {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    $parsed = [guid]::Empty
    [guid]::TryParse($Value.Trim().Trim('{}'), [ref]$parsed)
}

function Get-NormalizedGuid {
    param([string]$Value)
    $Value.Trim().Trim('{}').ToLowerInvariant()
}

function Assert-Condition {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "参数错误：$Message" }
}

function Test-ObjectContainsGuid {
    param([object]$Value, [string]$Guid)
    if ($null -eq $Value) { return $false }
    $json = $Value | ConvertTo-Json -Depth 100 -Compress
    $json.IndexOf((Get-NormalizedGuid $Guid), [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Find-AreaRecord {
    param([object]$Node, [string]$Guid)
    if ($null -eq $Node -or $Node -is [string]) { return $null }

    if ($Node -is [System.Collections.IEnumerable] -and $Node -isnot [pscustomobject]) {
        foreach ($item in $Node) {
            $found = Find-AreaRecord -Node $item -Guid $Guid
            if ($null -ne $found) { return $found }
        }
        return $null
    }

    foreach ($propertyName in @('id', 'guid', 'area_id', 'areaId')) {
        $property = $Node.PSObject.Properties[$propertyName]
        if ($null -ne $property -and -not [string]::IsNullOrWhiteSpace([string]$property.Value)) {
            if ((Get-NormalizedGuid ([string]$property.Value)) -eq (Get-NormalizedGuid $Guid)) {
                return $Node
            }
        }
    }

    foreach ($property in $Node.PSObject.Properties) {
        $found = Find-AreaRecord -Node $property.Value -Guid $Guid
        if ($null -ne $found) { return $found }
    }
    $null
}

function Invoke-MoziGet {
    param([Parameter(Mandatory)][string]$Path)
    Invoke-RestMethod -Method Get -Uri "$ApiBase/$Path" -TimeoutSec 25
}

function Invoke-MoziPost {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][object]$Body)
    $json = $Body | ConvertTo-Json -Depth 60 -Compress
    $utf8Body = [Text.Encoding]::UTF8.GetBytes($json)
    Invoke-RestMethod -Method Post -Uri "$ApiBase/$Path" -ContentType 'application/json; charset=utf-8' `
        -Body $utf8Body -TimeoutSec 30
}

function Assert-MoziSuccess {
    param([object]$Response, [string]$Action)
    if ($null -eq $Response -or $Response.code -notin @(0, 200)) {
        $payload = if ($null -eq $Response) { '<null>' } else { $Response | ConvertTo-Json -Depth 20 -Compress }
        throw "$Action 失败：$payload"
    }
}

Assert-Condition (Test-Guid $MissionGuid) 'MissionGuid 必须是有效 GUID'
Assert-Condition (Test-Guid $SideGuid) 'SideGuid 必须是有效 GUID'
Assert-Condition (-not [string]::IsNullOrWhiteSpace($SideName)) 'SideName 不能为空'
Assert-Condition (-not [string]::IsNullOrWhiteSpace($ApiBase)) 'ApiBase 不能为空'
if (-not [string]::IsNullOrWhiteSpace($AreaGuid)) {
    Assert-Condition (Test-Guid $AreaGuid) 'AreaGuid 必须是有效 GUID；打击任务没有区域时可以留空'
}

$missionGuidNormalized = Get-NormalizedGuid $MissionGuid
$areaGuidNormalized = if ([string]::IsNullOrWhiteSpace($AreaGuid)) { '' } else { Get-NormalizedGuid $AreaGuid }
$areaGuidForApi = if ([string]::IsNullOrWhiteSpace($AreaGuid)) { '' } else { "{$areaGuidNormalized}" }

if ($ValidateOnly) {
    [ordered]@{
        valid = $true
        missionGuid = $missionGuidNormalized
        areaGuid = $areaGuidForApi
        areaDeletion = -not [string]::IsNullOrWhiteSpace($AreaGuid)
        sideGuid = Get-NormalizedGuid $SideGuid
        sideName = $SideName
        allowSharedAreaDeletion = [bool]$AllowSharedAreaDeletion
    } | ConvertTo-Json -Depth 10
    return
}

$result = [ordered]@{
    startedAt = (Get-Date).ToString('o')
    success = $false
    missionGuid = $missionGuidNormalized
    areaGuid = $areaGuidForApi
    missionDeleted = $false
    areaDeleted = $false
    areaDeletionSkipped = [string]::IsNullOrWhiteSpace($AreaGuid)
    error = $null
    responses = [ordered]@{}
}
$failure = $null

try {
    $encodedSideGuid = [uri]::EscapeDataString((Get-NormalizedGuid $SideGuid))
    $encodedSideName = [uri]::EscapeDataString($SideName)

    $missionsBefore = Invoke-MoziGet "MissionGant?SideGuid=$encodedSideGuid"
    Assert-MoziSuccess $missionsBefore '删除前读取任务列表'
    Assert-Condition (Test-ObjectContainsGuid $missionsBefore $MissionGuid) "蓝方任务列表中不存在任务 $MissionGuid"
    $result.responses.missionsBefore = $missionsBefore

    if (-not [string]::IsNullOrWhiteSpace($AreaGuid)) {
        $areasBefore = Invoke-MoziGet "ScenArea?type=$encodedSideName"
        Assert-MoziSuccess $areasBefore '删除前读取区域列表'
        $areaRecord = Find-AreaRecord -Node $areasBefore -Guid $AreaGuid
        Assert-Condition ($null -ne $areaRecord) "蓝方区域列表中不存在区域 $AreaGuid"

        $missionListProperty = $areaRecord.PSObject.Properties['missionList']
        if ($null -ne $missionListProperty) {
            $missionLinks = @($missionListProperty.Value)
            if ($missionLinks.Count -gt 0) {
                $linkedJson = $missionLinks | ConvertTo-Json -Depth 30 -Compress
                $linkedToDeletingMission = $linkedJson.IndexOf($missionGuidNormalized, [StringComparison]::OrdinalIgnoreCase) -ge 0
                $guidMatches = @([regex]::Matches($linkedJson, '(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}') | ForEach-Object { $_.Value.ToLowerInvariant() } | Sort-Object -Unique)
                $otherMissionGuids = @($guidMatches | Where-Object { $_ -ne $missionGuidNormalized -and $_ -ne $areaGuidNormalized })
                $appearsShared = $otherMissionGuids.Count -gt 0 -or ($missionLinks.Count -gt 1) -or (-not $linkedToDeletingMission)
                if ($appearsShared -and -not $AllowSharedAreaDeletion) {
                    throw '区域 missionList 显示它可能还被其他任务使用。为避免误删，脚本已停止；确认确实要连带删除时才使用 -AllowSharedAreaDeletion。'
                }
            }
        }
        $result.responses.areasBefore = $areasBefore
    }

    $deleteMission = Invoke-MoziPost 'MissionGant' @{
        SideGuid = (Get-NormalizedGuid $SideGuid)
        MissionGuid = $missionGuidNormalized
        MissionCommandType = 'DeleteMission'
    }
    Assert-MoziSuccess $deleteMission "删除任务 $MissionGuid"
    $result.responses.deleteMission = $deleteMission

    $missionsAfter = Invoke-MoziGet "MissionGant?SideGuid=$encodedSideGuid"
    Assert-MoziSuccess $missionsAfter '删除后回读任务列表'
    if (Test-ObjectContainsGuid $missionsAfter $MissionGuid) {
        throw "接口返回成功，但回读时任务 $MissionGuid 仍然存在"
    }
    $result.missionDeleted = $true
    $result.responses.missionsAfter = $missionsAfter

    if (-not [string]::IsNullOrWhiteSpace($AreaGuid)) {
        $deleteArea = Invoke-MoziPost 'ScenArea' @{
            option = 'delArea'
            side = $SideName
            area_id = $areaGuidForApi
        }
        Assert-MoziSuccess $deleteArea "删除区域 $AreaGuid"
        $result.responses.deleteArea = $deleteArea

        $areasAfter = Invoke-MoziGet "ScenArea?type=$encodedSideName"
        Assert-MoziSuccess $areasAfter '删除后回读区域列表'
        if (Test-ObjectContainsGuid $areasAfter $AreaGuid) {
            throw "接口返回成功，但回读时区域 $AreaGuid 仍然存在"
        }
        $result.areaDeleted = $true
        $result.responses.areasAfter = $areasAfter
    }

    $result.success = $true
}
catch {
    $result.error = $_.Exception.Message
    $failure = $_
}
finally {
    $result.completedAt = (Get-Date).ToString('o')
    if ([string]::IsNullOrWhiteSpace($ReportPath)) {
        $reportDirectory = Join-Path $PSScriptRoot 'reports'
        $ReportPath = Join-Path $reportDirectory ("delete-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    } else {
        $reportDirectory = Split-Path -Parent ([IO.Path]::GetFullPath($ReportPath))
    }
    if (-not [string]::IsNullOrWhiteSpace($reportDirectory)) {
        New-Item -ItemType Directory -Force -Path $reportDirectory | Out-Null
    }
    $result | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    $result | ConvertTo-Json -Depth 20
}

if ($null -ne $failure) { throw $failure }
