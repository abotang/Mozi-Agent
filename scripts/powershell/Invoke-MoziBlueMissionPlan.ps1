[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ConfigPath,

    [ValidateSet('airCombatPatrol','airAirStrike','airSeaStrike','airLandStrike','airEarlyWarning','shipSeaPatrol','shipSeaStrike')]
    [string[]]$Task,

    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Assert-Condition {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "配置错误：$Message" }
}

function Test-Guid {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    $trimmed = $Value.Trim('{}')
    $parsed = [guid]::Empty
    [guid]::TryParse($trimmed, [ref]$parsed)
}

function Resolve-AbsolutePath {
    param([string]$PathValue, [string]$BaseDirectory)
    if ([IO.Path]::IsPathRooted($PathValue)) { return $PathValue }
    [IO.Path]::GetFullPath((Join-Path $BaseDirectory $PathValue))
}

$resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
$configDirectory = Split-Path -Parent $resolvedConfig
$config = Get-Content -LiteralPath $resolvedConfig -Raw -Encoding UTF8 | ConvertFrom-Json

Assert-Condition ($config.schemaVersion -eq 1) 'schemaVersion 必须为 1'
Assert-Condition (-not [string]::IsNullOrWhiteSpace($config.apiBase)) 'apiBase 不能为空'
Assert-Condition (Test-Guid $config.sideGuid) 'sideGuid 必须是有效 GUID'
Assert-Condition (-not [string]::IsNullOrWhiteSpace($config.sideName)) 'sideName 不能为空'

$script:base = [string]$config.apiBase
$script:sideGuid = [string]$config.sideGuid
$script:sideName = [string]$config.sideName

function Invoke-MoziGet {
    param([Parameter(Mandatory)][string]$Path)
    Invoke-RestMethod -Method Get -Uri "$script:base/$Path" -TimeoutSec 25
}

function Invoke-MoziPost {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][object]$Body
    )
    $json = $Body | ConvertTo-Json -Depth 60 -Compress
    $utf8Body = [Text.Encoding]::UTF8.GetBytes($json)
    Invoke-RestMethod -Method Post -Uri "$script:base/$Path" -ContentType 'application/json; charset=utf-8' `
        -Body $utf8Body -TimeoutSec 30
}

function Assert-MoziSuccess {
    param([object]$Response, [string]$Action)
    if ($null -eq $Response -or $Response.code -ne 0) {
        $payload = if ($null -eq $Response) { '<null>' } else { $Response | ConvertTo-Json -Depth 20 -Compress }
        throw "$Action 失败：$payload"
    }
}

function Test-EnabledTask {
    param([object]$Task)
    $null -ne $Task -and $Task.enabled -eq $true
}

function Test-Placeholder {
    param([string]$Value)
    [string]::IsNullOrWhiteSpace($Value) -or $Value -match '请填写|PLACEHOLDER|TARGET_GUID|UNIT_GUID'
}

function Test-TaskConfig {
    param([string]$Key, [object]$Task)
    if (-not (Test-EnabledTask $Task)) { return }
    Assert-Condition (-not [string]::IsNullOrWhiteSpace($Task.name)) "$Key.name 不能为空"
    Assert-Condition ($Task.missionType -in @('Patrol','Strike','Support')) "$Key.missionType 不受本脚本支持"
    Assert-Condition (@($Task.unitGuids).Count -gt 0) "$Key.unitGuids 至少需要一个单元"
    foreach ($unit in @($Task.unitGuids)) {
        Assert-Condition ((-not (Test-Placeholder $unit)) -and (Test-Guid $unit)) "$Key.unitGuids 包含无效 GUID"
    }
    foreach ($escort in @($Task.escortUnitGuids)) {
        Assert-Condition ((-not (Test-Placeholder $escort)) -and (Test-Guid $escort)) "$Key.escortUnitGuids 包含无效 GUID"
    }
    if ($Task.missionType -eq 'Strike') {
        Assert-Condition (@($Task.targetContactGuids).Count -gt 0) "$Key 是打击任务，必须填写蓝方接触 targetContactGuids"
        foreach ($target in @($Task.targetContactGuids)) {
            Assert-Condition ((-not (Test-Placeholder $target)) -and (Test-Guid $target)) "$Key.targetContactGuids 包含无效 GUID"
        }
        Assert-Condition ($Task.targetKind -in @('Aircraft','Ship','Facility','Submarine')) "$Key.targetKind 无效"
    }
    if ($Task.missionType -in @('Patrol','Support')) {
        Assert-Condition ($null -ne $Task.area) "$Key 必须配置 area"
        if ([string]::IsNullOrWhiteSpace([string]$Task.area.existingAreaGuid)) {
            Assert-Condition (@($Task.area.points).Count -ge 3) "$Key.area.points 至少 3 点"
            foreach ($point in @($Task.area.points)) {
                Assert-Condition ($null -ne $point.lon -and $null -ne $point.lat) "$Key.area.points 必须同时有 lon/lat"
            }
        } else {
            Assert-Condition (Test-Guid ([string]$Task.area.existingAreaGuid)) "$Key.area.existingAreaGuid 无效"
        }
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$Task.existingMissionGuid)) {
        Assert-Condition (Test-Guid ([string]$Task.existingMissionGuid)) "$Key.existingMissionGuid 无效"
    }
}

$taskPairs = @(
    @{ key='airCombatPatrol'; task=$config.tasks.airCombatPatrol },
    @{ key='airAirStrike'; task=$config.tasks.airAirStrike },
    @{ key='airSeaStrike'; task=$config.tasks.airSeaStrike },
    @{ key='airLandStrike'; task=$config.tasks.airLandStrike },
    @{ key='airEarlyWarning'; task=$config.tasks.airEarlyWarning },
    @{ key='shipSeaPatrol'; task=$config.tasks.shipSeaPatrol },
    @{ key='shipSeaStrike'; task=$config.tasks.shipSeaStrike }
)
if (@($Task).Count -gt 0) {
    $selected = @($Task)
    $taskPairs = @($taskPairs | Where-Object { $_.key -in $selected })
}
foreach ($pair in $taskPairs) { Test-TaskConfig $pair.key $pair.task }

if ($ValidateOnly) {
    [ordered]@{
        valid = $true
        config = $resolvedConfig
        enabledTasks = @($taskPairs | Where-Object { Test-EnabledTask $_.task } | ForEach-Object { $_.key })
    } | ConvertTo-Json -Depth 10
    return
}

function Set-AircraftLoadoutIfRequested {
    param([object]$Task)
    if ([string]::IsNullOrWhiteSpace([string]$Task.loadoutGuid)) { return @() }
    $changes = @()
    foreach ($unit in @($Task.unitGuids) + @($Task.escortUnitGuids)) {
        $before = Invoke-MoziGet "air-operations/loadout?Guid=$unit"
        $response = Invoke-MoziPost 'air-operations/ConfirmDP' @{
            Units=@([string]$unit); loadGuid=[string]$Task.loadoutGuid
            option='automatic'; quickly=$false; index=-1
        }
        Assert-MoziSuccess $response "飞机 $unit 换挂"
        $after = Invoke-MoziGet "air-operations/loadout?Guid=$unit"
        $changes += [ordered]@{
            unit=[string]$unit; before=$before.data.LoadoutName; after=$after.data.LoadoutName; response=$response
        }
    }
    $changes
}

function New-ScenarioArea {
    param([object]$Area)
    if (-not [string]::IsNullOrWhiteSpace([string]$Area.existingAreaGuid)) {
        $normalized = ([string]$Area.existingAreaGuid).Trim().Trim('{}')
        return [ordered]@{ guid="{$normalized}"; reused=$true; name=[string]$Area.name }
    }
    $values = @()
    $index = 0
    foreach ($point in @($Area.points)) {
        $index++
        $values += @{ Lon=[string]$point.lon; Lat=[string]$point.lat; Name="$($Area.name)-RP$index" }
    }
    $reference = Invoke-MoziPost 'ReferencePoint' @{
        Type='AddReferencePoint'; SideGuid=$script:sideGuid; Values=$values
    }
    Assert-MoziSuccess $reference "区域 $($Area.name) 创建参考点"
    Assert-Condition (@($reference.data).Count -eq $values.Count) "区域 $($Area.name) 参考点返回数量不一致"
    $areaPoints = for ($i=0; $i -lt $values.Count; $i++) {
        @{ id=$reference.data[$i]; x=[double]$values[$i].Lon; y=[double]$values[$i].Lat }
    }
    $create = Invoke-MoziPost 'ScenArea' @{
        option='addArea'; side=$script:sideName; areaType=[string]$Area.typeId
        area_name=[string]$Area.name; area_remark=[string]$Area.remark; area_points=$areaPoints
    }
    Assert-MoziSuccess $create "创建区域 $($Area.name)"
    $journalPath = (Resolve-AbsolutePath ([string]$config.reportPath) $configDirectory) + '.area.json'
    $journalDirectory = Split-Path -Parent $journalPath
    if (-not (Test-Path -LiteralPath $journalDirectory)) {
        New-Item -ItemType Directory -Path $journalDirectory -Force | Out-Null
    }
    [ordered]@{
        schemaVersion=1; created=$true; sideGuid=$script:sideGuid
        areaGuid=[string]$create.data.id; referenceGuids=@($reference.data)
    } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $journalPath -Encoding UTF8
    [ordered]@{ guid=[string]$create.data.id; reused=$false; name=[string]$Area.name; reference=$reference; create=$create }
}

function New-MissionRoute {
    param([object]$Route)
    if ($null -eq $Route) { return $null }
    if (-not [string]::IsNullOrWhiteSpace([string]$Route.existingRouteGuid)) {
        return [ordered]@{ id=[string]$Route.existingRouteGuid; reused=$true; name=[string]$Route.name }
    }
    Assert-Condition (@($Route.points).Count -ge 2) "航线 $($Route.name) 至少需要两个点"
    $create = Invoke-MoziPost 'routeLineManage/routeLine-status' @{
        sideGuid=$script:sideGuid; name=[string]$Route.name; routeType='0'; type='createRouteLine'
    }
    Assert-MoziSuccess $create "创建航线 $($Route.name)"
    $routes = Invoke-MoziGet "routeLineManage?SideGuid=$script:sideGuid"
    Assert-MoziSuccess $routes '读取航线列表'
    $routeItem = @($routes.data.routeList | Where-Object name -eq $Route.name | Select-Object -Last 1)[0]
    Assert-Condition (-not [string]::IsNullOrWhiteSpace([string]$routeItem.id)) "找不到新建航线 $($Route.name)"
    $routeId = [string]$routeItem.id
    $planPoints = @($Route.points | ForEach-Object { @{ longitude=[string]$_.lon; latitude=[string]$_.lat } })
    $add = Invoke-MoziPost 'routeLineManage/wayPoints-status' @{
        sideGuid=$script:sideGuid; type='addPlanWayPoint'; id=$routeId; planWayPoints=$planPoints
    }
    Assert-MoziSuccess $add "航线 $($Route.name) 添加航线点"
    $detail = Invoke-MoziGet "routeDetailList?Guid=$routeId"
    Assert-MoziSuccess $detail "航线 $($Route.name) 回读"
    $throttle = if ($null -ne $Route.throttle) { [string]$Route.throttle } else { '2' }
    $waypoints = @($detail.data.currentRouteDetailList | ForEach-Object {
        @{
            id=$_.id; wayPointGuid=$_.id; name=$_.name; longitude=$_.longitude; latitude=$_.latitude
            altitude='0'; throttle=$throttle; radar='1'; sonar='1'; oecm='1'
        }
    })
    $update = Invoke-MoziPost 'routeLineManage/wayPoints-status' @{
        sideGuid=$script:sideGuid; type='updataPlanWayPoint'; id=$routeId; wayPointInformations=$waypoints
    }
    Assert-MoziSuccess $update "航线 $($Route.name) 设置油门"
    if ($Route.show -eq $true) {
        $show = Invoke-MoziPost 'routeLineManage/routeLine-status' @{
            sideGuid=$script:sideGuid; type='routeLineIsShow'; id=$routeId; isShow=$true
        }
        Assert-MoziSuccess $show "显示航线 $($Route.name)"
    }
    [ordered]@{ id=$routeId; reused=$false; name=[string]$Route.name; create=$create; add=$add; update=$update }
}

function Get-OrCreateMission {
    param([object]$Task, [string]$TaskKey)
    if (-not [string]::IsNullOrWhiteSpace([string]$Task.existingMissionGuid)) {
        $detail = Invoke-MoziGet "Mission?Guid=$($Task.existingMissionGuid)"
        Assert-MoziSuccess $detail "读取已有任务 $($Task.name)"
        return [ordered]@{ guid=[string]$Task.existingMissionGuid; reused=$true; response=$detail }
    }
    $create = Invoke-MoziPost 'Mission' @{
        type='createMission'
        createMission=@{
            name=[string]$Task.name; sideGuid=$script:sideGuid; status=$true
            type=[string]$Task.missionType; classify=[string]$Task.classify
            isOpenMissionWindows=$false; task_id="$TaskKey-v1"; TreeParentID='0'
        }
    }
    Assert-MoziSuccess $create "创建任务 $($Task.name)"
    Assert-Condition (-not [string]::IsNullOrWhiteSpace([string]$create.data.id)) "创建任务 $($Task.name) 未返回 GUID"
    [ordered]@{ guid=[string]$create.data.id; reused=$false; response=$create }
}

function Set-PatrolRules {
    param([string]$TaskKey, [object]$Task, [string]$MissionGuid, [string]$AreaGuid, [object]$Outbound, [object]$OnStation)
    $rules = @{
        name=[string]$Task.name; startTime=[string]$config.startTime; endTime=[string]$config.endTime
        PatrolArea_ID=$AreaGuid; AlertArea_ID=$AreaGuid
        OTR=($TaskKey -like 'air*'); IOPA=$false; IWWR=$false; AEOIPA=$false
        FlightSize='1'; AirFuel='0'; MinAircraftReq='0'; UseFlightSizeHardLimit=$false
        GroupSize='1'; UseGroupSizeHardLimit=$false
        TransitThrottle_Aircraft='2'; StationThrottle_Aircraft='1'; AttackThrottle_Aircraft='3'
        TransitThrottle_Ship='2'; StationThrottle_Ship='2'; AttackThrottle_Ship='3'
    }
    if ($null -ne $Outbound) { $rules.SideWay = $Outbound.id }
    if ($null -ne $OnStation) { $rules.PatrolAreaSideWay = $OnStation.id }
    $response = Invoke-MoziPost 'Mission' @{ type='PatrolMissionRules'; MissionGuid=$MissionGuid; Rules=$rules }
    Assert-MoziSuccess $response "任务 $($Task.name) 写入巡逻规则"
    $response
}

function Set-SupportRules {
    param([object]$Task, [string]$MissionGuid, [string]$AreaGuid)
    $response = Invoke-MoziPost 'Mission' @{
        type='SupportMissionRules'; MissionGuid=$MissionGuid
        Rules=@{
            name=[string]$Task.name; startTime=[string]$config.startTime; endTime=[string]$config.endTime
            area_ID=$AreaGuid; MNOS='1'; OTR=$true; OTO=$false; AEOIPA=$false
            OneTankingCycleOnly=$false; status='1'; FlightSize='1'; AirFuel='0'
            MinAircraftReq='1'; UseFlightSizeHardLimit=$false
            TransitThrottle_Aircraft='2'; TransitAltitude_Aircraft='2'
            StationThrottle_Aircraft='1'; StationAltitude_Aircraft='2'
        }
    }
    Assert-MoziSuccess $response "任务 $($Task.name) 写入支援规则"
    $response
}

function Invoke-MissionOperation {
    param([string]$Type, [string]$MissionGuid, [string[]]$Ids, [string]$Description)
    if (@($Ids).Count -eq 0) { return $null }
    $response = Invoke-MoziPost 'Mission' @{ type=$Type; MissionGuid=$MissionGuid; Units=@($Ids) }
    Assert-MoziSuccess $response $Description
    $response
}

function Invoke-OneTask {
    param([string]$Key, [object]$Task)
    $loadouts = Set-AircraftLoadoutIfRequested $Task
    $mission = Get-OrCreateMission $Task $Key
    $area = $null
    $outbound = $null
    $onStation = $null
    $rulesResponse = $null

    if ($Task.missionType -in @('Patrol','Support')) {
        $area = New-ScenarioArea $Task.area
    }
    if ($Task.missionType -eq 'Patrol') {
        if ($null -ne $Task.routes) {
            $outbound = New-MissionRoute $Task.routes.outbound
            $onStation = New-MissionRoute $Task.routes.onStation
        }
        $rulesResponse = Set-PatrolRules $Key $Task $mission.guid $area.guid $outbound $onStation
    } elseif ($Task.missionType -eq 'Support') {
        $rulesResponse = Set-SupportRules $Task $mission.guid $area.guid
    }

    # 规则更新可能清空分配，所以分配必须在规则之后。
    $relieved = ''
    if ($null -ne $Task.parameters -and $null -ne $Task.parameters.PSObject.Properties['relieved_unit_guid']) {
        $relieved = [string]$Task.parameters.relieved_unit_guid
    }
    if (-not [string]::IsNullOrWhiteSpace($relieved)) {
        Assert-Condition (Test-Guid $relieved) "任务 $($Task.name) 换班旧飞机 GUID 无效"
        $unassign = Invoke-MissionOperation 'UnAssignUnitToMission' $mission.guid @($relieved) "任务 $($Task.name) 移除返航飞机"
    }
    $assign = Invoke-MissionOperation 'AssignUnitToMission' $mission.guid @($Task.unitGuids) "任务 $($Task.name) 分配主任务单元"
    $escort = Invoke-MissionOperation 'AssignUnitToEscort' $mission.guid @($Task.escortUnitGuids) "任务 $($Task.name) 分配护航单元"
    $target = Invoke-MissionOperation 'AssignUnitAsTarget' $mission.guid @($Task.targetContactGuids) "任务 $($Task.name) 分配目标"
    $verify = Invoke-MoziGet "Mission?Guid=$($mission.guid)"
    Assert-MoziSuccess $verify "任务 $($Task.name) 回读"

    if ($Task.missionType -eq 'Patrol') {
        Assert-Condition (@($verify.data.patrolmissionData.PatrolArea).Count -ge 3) "任务 $($Task.name) 未回读到巡逻区"
        if ($Key -like 'air*') {
            Assert-Condition ($verify.data.patrolmissionData.OTR -eq $true) "任务 $($Task.name) 未启用1/3规则"
        }
    } elseif ($Task.missionType -eq 'Strike') {
        Assert-Condition (@($verify.data.strikeMissionData.targetList).Count -gt 0) "任务 $($Task.name) 未回读到打击目标"
    } elseif ($Task.missionType -eq 'Support') {
        Assert-Condition (@($verify.data.supportmissionData.area).Count -ge 1) "任务 $($Task.name) 未回读到支援区域"
    }

    [ordered]@{
        key=$Key; name=[string]$Task.name; missionGuid=$mission.guid; reused=$mission.reused
        loadouts=@($loadouts); area=$area; outboundRoute=$outbound; onStationRoute=$onStation
        rules=$rulesResponse; assign=$assign; escort=$escort; target=$target; verification=$verify
    }
}

$runResult = [ordered]@{
    schemaVersion=1
    executedAt=(Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')
    config=$resolvedConfig
    side=@{ name=$script:sideName; guid=$script:sideGuid }
    tasks=@()
}

foreach ($pair in $taskPairs) {
    if (Test-EnabledTask $pair.task) {
        $runResult.tasks += Invoke-OneTask $pair.key $pair.task
    }
}

$reportPath = Resolve-AbsolutePath ([string]$config.reportPath) $configDirectory
$reportDirectory = Split-Path -Parent $reportPath
if (-not (Test-Path -LiteralPath $reportDirectory)) {
    New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
}
$runResult | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $reportPath -Encoding UTF8

[ordered]@{
    success=$true
    report=$reportPath
    tasks=@($runResult.tasks | ForEach-Object {
        $data = $_.verification.data
        $targetCount = 0
        $patrolPointCount = 0
        $supportPointCount = 0
        if ($null -ne $data.PSObject.Properties['strikeMissionData']) {
            $targetCount = @($data.strikeMissionData.targetList).Count
        }
        if ($null -ne $data.PSObject.Properties['patrolmissionData']) {
            $patrolPointCount = @($data.patrolmissionData.PatrolArea).Count
        }
        if ($null -ne $data.PSObject.Properties['supportmissionData']) {
            $supportPointCount = @($data.supportmissionData.area).Count
        }
        @{
            key=$_.key; name=$_.name; missionGuid=$_.missionGuid; reused=$_.reused
            targetCount=$targetCount
            patrolPointCount=$patrolPointCount
            supportPointCount=$supportPointCount
        }
    })
} | ConvertTo-Json -Depth 20
