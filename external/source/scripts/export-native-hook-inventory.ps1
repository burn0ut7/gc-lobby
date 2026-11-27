#requires -Version 7.0
param(
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$BaselineSha256,
    [Parameter(Mandatory)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$sourceRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'native/WorkbenchAuthoring'
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $outputRoot) { throw 'Use a new evidence directory.' }
$snapshotRoot = Join-Path $outputRoot 'source'
New-Item -ItemType Directory -Path $snapshotRoot | Out-Null
$uses = @{}
$sourceHashes = @()
foreach ($file in Get-ChildItem -LiteralPath $sourceRoot -File | Sort-Object Name) {
    $snapshot = Join-Path $snapshotRoot $file.Name
    Copy-Item -LiteralPath $file.FullName -Destination $snapshot
    $hash = (Get-FileHash -LiteralPath $snapshot -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()) { throw "Source changed: $($file.Name)" }
    $sourceHashes += @{ path = $file.Name; sha256 = $hash }
    if ($file.Extension -notin @('.cpp', '.h') -or $file.Name -match 'Test\.') { continue }
    $lines = @(Get-Content -LiteralPath $snapshot)
    for ($line = 0; $line -lt $lines.Count; $line++) {
        $code = ($lines[$line] -split '//', 2)[0]
        foreach ($match in [regex]::Matches($code, '0x[0-9a-fA-F]{5,}')) {
            $rva = [Convert]::ToUInt32($match.Value.Substring(2), 16)
            # RTTI type ID, not an address. Keep object offsets/instruction bytes out.
            if ($rva -eq 0x71a6fe80) { continue }
            $key = $rva.ToString('x')
            if (!$uses.ContainsKey($key)) { $uses[$key] = @() }
            $uses[$key] += @{ file = $file.Name; line = $line + 1; context = $lines[$line].Trim() }
        }
    }
}
$compatibility = Get-Content -LiteralPath (Join-Path $snapshotRoot 'Compatibility.cpp') -Raw
if (!$compatibility.Contains($BaselineSha256.ToLowerInvariant())) { throw 'Baseline is not the source compatibility guard.' }
$hooks = @($uses.Keys | Sort-Object { [Convert]::ToUInt32($_, 16) } | ForEach-Object {
    @{ id = "gc_workbench_$_"; rva = $_; uses = @($uses[$_]) }
})
@{ schema = 1; sourceRoot = $snapshotRoot; inventoryBaselineSha256 = $BaselineSha256.ToLowerInvariant(); explicitHooks = $hooks } |
    ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $outputRoot 'inventory.json') -Encoding utf8
$sourceHashes | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $outputRoot 'source-hashes.json') -Encoding utf8
Write-Output "Exported $($hooks.Count) native operands. Data only; review candidates and test before changing compatibility."
