[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ApiBase,
    [Parameter(Mandatory)][string]$SideGuid,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$parsed = [guid]::Empty
if (-not [guid]::TryParse($SideGuid.Trim('{}'), [ref]$parsed)) {
    throw '蓝方 GUID 无效'
}
if ($ApiBase -notmatch '^https?://') { throw 'ApiBase 必须是 HTTP(S) 地址' }
$base = $ApiBase.TrimEnd('/')
$side = $parsed.ToString()

function Get-SideDoctrine {
    $uri = "$base/DoctrineInfo?Guid=$side&Type=Side&IsEscort=false"
    $response = Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 15
    if ($null -eq $response -or $response.code -ne 0 -or $null -eq $response.data.General.ROE) {
        throw "无法回读推演方条令：$($response | ConvertTo-Json -Depth 6 -Compress)"
    }
    return $response
}

$before = Get-SideDoctrine
if ($ValidateOnly) {
    @{ valid=$true; sideGuid=$side; currentValue=[string]$before.data.General.ROE.ShootTourists } |
        ConvertTo-Json -Compress -Depth 5
    return
}

$changed = [string]$before.data.General.ROE.ShootTourists -ne '1'
if ($changed) {
    # One ROE field per request. Do not combine General submodules: Mozi may
    # report code=0 while silently ignoring a cross-submodule batch.
    $body = @{
        Guid=$side; SubjectType='Side'; IsEscort=$false
        Value=@{ General=@{ ROE=@{ ShootTourists='1' } } }
    } | ConvertTo-Json -Depth 10 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($body)
    $posted = Invoke-RestMethod -Method Post -Uri "$base/DoctrineInfo" `
        -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec 20
    if ($null -eq $posted -or $posted.code -ne 0) {
        throw "设置接战临机目标失败：$($posted | ConvertTo-Json -Depth 6 -Compress)"
    }
}
$after = Get-SideDoctrine
for ($attempt = 0; $attempt -lt 6 -and [string]$after.data.General.ROE.ShootTourists -ne '1'; $attempt++) {
    # DoctrineInfo can acknowledge a write before the next GET reflects it.
    Start-Sleep -Milliseconds 500
    $after = Get-SideDoctrine
}
if ([string]$after.data.General.ROE.ShootTourists -ne '1') {
    throw '推演方条令回读仍不是“是（可以任何目标交战）”'
}
@{ success=$true; sideGuid=$side; setting='General.ROE.ShootTourists';
   value='1'; changed=$changed } | ConvertTo-Json -Compress -Depth 5
