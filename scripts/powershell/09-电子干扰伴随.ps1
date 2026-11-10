[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ConfigPath,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Assert-Condition {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "干扰机配置错误：$Message" }
}

function Test-Guid {
    param([string]$Value)
    $parsed = [guid]::Empty
    -not [string]::IsNullOrWhiteSpace($Value) -and [guid]::TryParse($Value.Trim('{}'), [ref]$parsed)
}

function Invoke-MoziGet {
    param([string]$Path)
    Invoke-RestMethod -Method Get -Uri "$($config.apiBase.TrimEnd('/'))/$Path" -TimeoutSec 15
}

function Invoke-MoziPost {
    param([string]$Path, [object]$Body)
    $json = $Body | ConvertTo-Json -Depth 20 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    $response = Invoke-RestMethod -Method Post -Uri "$($config.apiBase.TrimEnd('/'))/$Path" `
        -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec 20
    Assert-Condition ($null -ne $response -and $response.code -eq 0) "$Path 请求失败：$($response | ConvertTo-Json -Depth 8 -Compress)"
    $response
}

$config = Get-Content -LiteralPath (Resolve-Path -LiteralPath $ConfigPath) -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-Condition ($config.schemaVersion -eq 1) 'schemaVersion 必须为 1'
Assert-Condition ($config.mode -in @('takeoff', 'follow', 'finish')) 'mode 必须为 takeoff/follow/finish'
Assert-Condition (-not [string]::IsNullOrWhiteSpace([string]$config.apiBase)) 'apiBase 不能为空'
Assert-Condition (Test-Guid ([string]$config.sideGuid)) 'sideGuid 无效'
Assert-Condition (Test-Guid ([string]$config.missionGuid)) 'missionGuid 无效'
Assert-Condition (Test-Guid ([string]$config.jammerGuid)) 'jammerGuid 无效'
if ($null -ne $config.waypoint) {
    $latitude = [double]$config.waypoint.lat
    $longitude = [double]$config.waypoint.lon
    Assert-Condition ([double]::IsFinite($latitude) -and [double]::IsFinite($longitude) -and
        $latitude -ge -85 -and $latitude -le 85 -and $longitude -ge -180 -and $longitude -le 180) 'waypoint 经纬度无效'
}

if ($ValidateOnly) {
    @{ valid=$true; mode=$config.mode; missionGuid=$config.missionGuid; jammerGuid=$config.jammerGuid } |
        ConvertTo-Json -Compress
    return
}

$responses = [ordered]@{}
if ($config.mode -eq 'takeoff') {
    # Mission creation does not prove a sortie began; the Python rule waits for
    # live-situation confirmation before enabling emitters or steering.
    $responses.takeoff = Invoke-MoziPost 'air-operations/aircraft-status' @{
        id=@([string]$config.jammerGuid); type='singleOperation'; params=''
    }
} elseif ($config.mode -eq 'follow') {
    $before = Invoke-MoziGet "unitSensors?guid=$($config.jammerGuid)"
    Assert-Condition ($before.code -eq 0 -and $null -ne $before.data) '读取干扰机传感器失败'
    $sensors = @($before.data.sensorList)
    Assert-Condition (@($sensors | Where-Object { [string]$_.type -match 'ECM|电子干扰|电子对抗' }).Count -gt 0) '当前干扰机没有可识别的电子干扰设备'
    Assert-Condition (@($sensors | Where-Object { [string]$_.type -match '雷达|Radar' }).Count -gt 0) '当前干扰机没有可识别的雷达'
    $responses.emcon = Invoke-MoziPost 'unitSensors' @{
        guid=[string]$config.jammerGuid; type='setEMCON'; complyElectromagnetic='false'
    }
    $responses.radar = Invoke-MoziPost 'unitSensors' @{
        guid=[string]$config.jammerGuid; type='setCategorySwitch'; categorySwitch='RADER=true'
    }
    $responses.jamming = Invoke-MoziPost 'unitSensors' @{
        guid=[string]$config.jammerGuid; type='setCategorySwitch'; categorySwitch='OECM=true'
    }
    if ($null -ne $config.waypoint) {
        $responses.course = Invoke-MoziPost 'PlottedCourse' @{
            Guid=[string]$config.jammerGuid
            Courses=@(@{lat=[double]$config.waypoint.lat; lon=[double]$config.waypoint.lon})
        }
    }
    $after = Invoke-MoziGet "unitSensors?guid=$($config.jammerGuid)"
    Assert-Condition ($after.code -eq 0 -and $null -ne $after.data) '控制后传感器回读失败'
    Assert-Condition ([string]$after.data.complyElectromagnetic -ieq 'false') '电磁管控未回读到允许主动辐射'
    $responses.sensorReadback = $after.data
} else {
    $responses.jammingOff = Invoke-MoziPost 'unitSensors' @{
        guid=[string]$config.jammerGuid; type='setCategorySwitch'; categorySwitch='OECM=false'
    }
    $responses.radarOff = Invoke-MoziPost 'unitSensors' @{
        guid=[string]$config.jammerGuid; type='setCategorySwitch'; categorySwitch='RADER=false'
    }
    if ($config.airborne -eq $true) {
        $responses.return = Invoke-MoziPost 'air-operations/aircraft-status' @{
            id=@([string]$config.jammerGuid); type='terminationOfOperation'; params=''
        }
    }
}

[ordered]@{
    success=$true; mode=[string]$config.mode; missionGuid=[string]$config.missionGuid
    jammerGuid=[string]$config.jammerGuid; waypoint=$config.waypoint
    responses=$responses
} | ConvertTo-Json -Depth 30 -Compress
