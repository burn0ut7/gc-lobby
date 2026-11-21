#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Missions,
    [Parameter(Mandatory)][string]$Plan,
    [Parameter(Mandatory)][string]$Output,
    [string[]]$Header
)
$ErrorActionPreference = 'Stop'
$missionsRoot = (Resolve-Path -LiteralPath $Missions).Path
$outputRoot = [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output))
$rootPrefix = $missionsRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
if ($outputRoot -eq $missionsRoot -or $outputRoot.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Export portable metadata outside the mission checkout.'
}
$batch = Get-Content -LiteralPath $Plan -Raw -Encoding UTF8 | ConvertFrom-Json
$revision = git -C $missionsRoot rev-parse --verify HEAD
if ($LASTEXITCODE -ne 0 -or $revision -cne $batch.target) {
    throw 'The audit plan describes a different mission revision.'
}
$changes = git -C $missionsRoot status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $changes) {
    throw 'Use a clean committed mission checkout for the export.'
}

$checks = $batch.committedLauncherMetadata.PSObject.Properties
$ready = @($checks | Where-Object {
    $_.Value.registeredLauncher -and $_.Value.registeredWorld -and
    $_.Value.gcMissionHeader -and $_.Value.generatedSnapshot
} | ForEach-Object Name | Sort-Object)
if ($Header) {
    $incomplete = @($Header | Where-Object { $_ -cnotin $ready })
    if ($incomplete.Count) {
        throw ('These launchers lack an audited GC snapshot: ' + ($incomplete -join ', '))
    }
    $ready = @($Header | Sort-Object -Unique)
}
if (!$ready.Count) {
    throw 'No file-cleared GC mission headers in this plan have generated snapshots.'
}

$exporter = Join-Path $PSScriptRoot '../../source/mission-launcher/Start-GCMission.ps1'
foreach ($relative in $ready) {
    $source = [IO.Path]::GetFullPath((Join-Path $missionsRoot $relative))
    if (!$source.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetExtension($source) -cne '.conf') {
        throw "Invalid header path '$relative' in the plan."
    }
    $destination = Join-Path $outputRoot ([IO.Path]::ChangeExtension($relative, 'launch.json'))
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    & $exporter $source -ExportMetadata $destination
}
Write-Output "Exported $($ready.Count) portable GC mission metadata files."
