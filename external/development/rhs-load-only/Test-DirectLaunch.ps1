#requires -Version 5.1
# Exercise Workshop argument/config/receipt handling without a backend or published fixture.
$ErrorActionPreference = 'Stop'
$repo = (Get-Item "$PSScriptRoot\..\..\..").FullName
$launcher = "$repo\external\source\mission-launcher\Start-GCMission.ps1"
$rhs = "$PSScriptRoot\Configs\Missions\RhsLoadOnly.conf"
$arland = "$repo\Configs\Missions\Gray\ArlandLobby.conf"
$root = [IO.Directory]::CreateDirectory((Join-Path $env:TEMP ("GC launch test " + [guid]::NewGuid()))).FullName
$profileDir = Join-Path $root 'profile'
$config = Join-Path $root 'shared.json'
function Check($condition, $message) { if (!$condition) { throw $message } }
function Save-TestJson($path, $value) { [IO.File]::WriteAllText($path, ($value | ConvertTo-Json -Depth 10)) }
$baseline = '0000000000000001'
$dependency = '0000000000000002'
$rhsId = '595F2BF2F44836FB'
Save-TestJson $config @{ game=@{scenarioId='existing';name='Keep name';gameProperties=@{fastValidation=$true};mods=@(
    @{modId=$baseline;name='Retained entry';version='1.2.3';required=$false}
    @{modId='6A115D5DFF7ED870';required=$false}
)};bindPort=23512 }
$originalHash = (Get-FileHash $config).Hash
$stub = Join-Path $root 'TestServer.exe'
Add-Type -OutputType ConsoleApplication -OutputAssembly $stub -TypeDefinition @"
using System;
using System.IO;
using System.Threading;
public class TestServer {
    public static int Main(string[] args) {
        string profile = args[Array.IndexOf(args, "-profile") + 1];
        string nonce = args[Array.IndexOf(args, "-gcLaunchId") + 1];
        if (Array.IndexOf(args, "-config") < 0 || Array.IndexOf(args, "-addons") >= 0 ||
            Array.IndexOf(args, "-addonDownloadDir") < 0) return 2;
        string receipts = Directory.CreateDirectory(Path.Combine(profile, "profile")).FullName;
        File.WriteAllText(Path.Combine(receipts, "GCLaunchReceipt.json"),
            File.ReadAllText(Path.Combine(profile, "expected.json")).Replace("TEST_NONCE", nonce));
        Thread.Sleep(20000);
        return 0;
    }
}
"@
$profileDir = [IO.Directory]::CreateDirectory($profileDir).FullName
$cache = [IO.Directory]::CreateDirectory((Join-Path $root 'mod cache\addons')).FullName
foreach ($missionFile in @($rhs,$arland)) {
    $options = @{Mission=$missionFile; Config=$config; ServerExe=$stub; ProfileDirectory=$profileDir; AddonDownloadDirectory=(Split-Path $cache)}
    $plan = & $launcher @options -PlanOnly
    $export = Join-Path $root ((Get-Item $missionFile).BaseName + '.launch.json')
    & $launcher -Mission $missionFile -ExportMetadata $export
    & $launcher -Mission $missionFile -ExportMetadata $export
    $options.Mission = $export
    $portable = & $launcher @options -PlanOnly
    Check (($plan | ConvertTo-Json -Depth 5) -ceq ($portable | ConvertTo-Json -Depth 5)) 'Portable launch differs from source header'
    Check (!([IO.File]::ReadAllText($export).Contains($repo))) 'Portable metadata contains an author-machine path'
    Check ($plan.mods.Count -eq @($plan.mods.modId | Sort-Object -Unique).Count) 'Duplicate mod in merged config'
    Check (($rhsId -in $plan.mods.modId) -eq ($missionFile -eq $rhs)) 'Mission selection carried mods across launches'
    Check (($plan.mods | Where-Object modId -eq '6A115D5DFF7ED870').required) 'Optional baseline overlap must become required for this mission'
    $ids = @($plan.mods.modId) + $dependency
    foreach ($id in $ids) {
        $dir = [IO.Directory]::CreateDirectory((Join-Path $cache $id)).FullName
        $deps = if ($id -eq $baseline) { '"' + $dependency + '"' } else { '' }
        [IO.File]::WriteAllText("$dir\addon.gproj",'GameProject { ID "Mod' + $id + '" GUID "' + $id + '" Dependencies { ' + $deps + ' } }')
        Save-TestJson "$dir\ServerData.json" @{id=$id;revision=@{version='1.2.3'}}
    }
    $receipt = @{version=1;launchId='TEST_NONCE';world=$plan.world;worldFile=$plan.worldFile;scenario=$plan.scenario;addonGuids=$ids}
    Save-TestJson "$profileDir\expected.json" $receipt
    & $launcher @options -StopAfterReceipt
    $generated = Get-Content -Raw "$profileDir\gc-launch.generated.json" | ConvertFrom-Json
    Check ($generated.game.name -eq 'Keep name' -and $generated.bindPort -eq 23512 -and $generated.game.gameProperties.fastValidation) 'Lost host settings'
    $entry = $generated.game.mods | Where-Object modId -eq $baseline
    Check ($entry.version -eq '1.2.3' -and !$entry.required -and $entry.name -eq 'Retained entry') 'Changed baseline fields'
    Check ($generated.game.scenarioId -eq $plan.scenario -and (Get-FileHash $config).Hash -eq $originalHash) 'Changed input or wrong scenario'
}
$receipt.addonGuids += 'FFFFFFFFFFFFFFFF'
Save-TestJson "$profileDir\expected.json" $receipt
$rejected = $false
try { & $launcher @options -StopAfterReceipt } catch { $rejected = $_.Exception.Message -like 'Server receipt differs*' }
Check $rejected 'Accepted unexpected loaded addon'
Check (!(Test-Path "$profileDir\gc-launch.active")) 'Failed server was left active'
$box = [IO.Directory]::CreateDirectory((Join-Path $root 'dedicated box')).FullName
$batch = Join-Path $box 'Start-GCMission.bat'
Copy-Item -LiteralPath "$repo\external\Start-GCMission.bat" -Destination $batch
$rebuilt = Join-Path $root 'rebuilt.bat'
& "$repo\external\source\mission-launcher\Build-Launcher.ps1" -Output $rebuilt
Check ((Get-FileHash $rebuilt).Hash -eq (Get-FileHash $batch).Hash) 'Single-file distribution is stale'
Copy-Item -LiteralPath $config -Destination "$box\community.json"
Copy-Item -LiteralPath $stub -Destination "$box\ArmaReforgerServer.exe"
$receipt.addonGuids = $ids
Save-TestJson "$profileDir\expected.json" $receipt
# Explorer passes the dropped file as the first argument. Run from a different
# working directory, with only the bundled launcher alongside the executable.
& $batch $arland -ProfileDirectory $profileDir -AddonDownloadDirectory (Split-Path $cache) -StopAfterReceipt
Check ($LASTEXITCODE -eq 0) 'Positional mission or automatic server/config discovery failed'
Copy-Item -LiteralPath $config -Destination "$box\second.json"
$ErrorActionPreference = 'Continue'
try { $errorOutput = & $batch $export -PlanOnly 2>&1 } finally { $ErrorActionPreference = 'Stop' }
Check ($LASTEXITCODE -ne 0 -and "$errorOutput" -like '*2 server configs*') 'Ambiguous configuration was silently selected'
& $batch $export -Config "$box\community.json" -PlanOnly | Out-Null
Check ($LASTEXITCODE -eq 0) 'Explicit config override failed'
Write-Output "Passed shared config, two missions, version pin, optional overlap, transitive receipt and wrong-addon rejection. Evidence: $root"
