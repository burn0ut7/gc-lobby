@echo off
setlocal DisableDelayedExpansion
rem Let Windows PowerShell discover its own modules when called from PowerShell 7.
set "PSModulePath="
if "%~1"=="" (
  echo Drop a GC mission header or its .launch.json on this file.
  echo Or run Start-GCMission.bat "path to mission" [-Config "path to server.json"] [-PlanOnly]
  exit /b 2
)
set "GC_LAUNCH_BUNDLE=%~f0"
set "GC_LAUNCH_DIRECTORY=%~dp0"
set "GC_LAUNCH_TEMP=%TEMP%\gc-launch-%RANDOM%-%RANDOM%-%RANDOM%.ps1"
set "GC_LAUNCH_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
"%GC_LAUNCH_PS%" -NoProfile -Command "$ErrorActionPreference='Stop'; $s=[IO.File]::ReadAllText($env:GC_LAUNCH_BUNDLE); $i=$s.IndexOf([char]10+'#requires -Version'); if ($i -lt 0) {throw 'Missing launcher payload'}; $b=[Text.Encoding]::UTF8.GetBytes($s.Substring($i+1)); $f=[IO.File]::Open($env:GC_LAUNCH_TEMP,'CreateNew','Write','None'); try {$f.Write($b,0,$b.Length)} finally {$f.Dispose()}"
if errorlevel 1 exit /b 1
"%GC_LAUNCH_PS%" -NoProfile -ExecutionPolicy RemoteSigned -File "%GC_LAUNCH_TEMP%" %*
set "GC_LAUNCH_EXIT=%ERRORLEVEL%"
"%GC_LAUNCH_PS%" -NoProfile -Command "[IO.File]::Delete($env:GC_LAUNCH_TEMP); if ($env:GC_LAUNCH_EXIT -ne '0') { $cmd=(Get-CimInstance Win32_Process -Filter ('ProcessId='+$PID)).ParentProcessId; $parent=(Get-CimInstance Win32_Process -Filter ('ProcessId='+$cmd)).ParentProcessId; if ((Get-Process -Id $parent -ErrorAction SilentlyContinue).ProcessName -eq 'explorer') { $null=Read-Host 'Press Enter to close' } }"
exit /b %GC_LAUNCH_EXIT%
#requires -Version 5.1
[CmdletBinding(DefaultParameterSetName='Launch')]
param(
    [Parameter(Mandatory,Position=0)][string]$Mission,
    [Parameter(ParameterSetName='Launch')][string]$Config,
    [Parameter(Mandatory,ParameterSetName='Export')][string]$ExportMetadata,
    [string[]]$AddonDirectory = @(),
    [string[]]$Project = @(),
    [string]$ServerDirectory = $env:GC_LAUNCH_DIRECTORY,
    [string]$ServerExe,
    [string]$ProfileDirectory,
    [string]$AddonDownloadDirectory,
    [switch]$PlanOnly,
    [switch]$Local,
    [string]$LocalBaseProject,
    [switch]$StopAfterReceipt,
    [ValidateRange(1,86400)][int]$StartupTimeoutSeconds = 300
)
$ErrorActionPreference = 'Stop'
if (-not ('GcNativeBlock' -as [type])) { Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text.RegularExpressions;
using System.Collections.Generic;

// Read immediate fields of saved Enfusion text containers. Quoted resource
// GUIDs, comments and fields in nested objects do not affect brace depth.
public sealed class GcNativeBlock
{
    static readonly Regex Tokens = new Regex(@"\G(?:\s+|//[^\n]*|/\*[\s\S]*?\*/|""(?:\\[\s\S]|[^""\\])*""|[{}]|[^\s{}""]+)", RegexOptions.Compiled);
    readonly List<string> tokens;
    readonly Dictionary<int, int> ends;
    readonly int first, end;

    GcNativeBlock(List<string> tokens, Dictionary<int, int> ends, int open)
    {
        this.tokens = tokens;
        this.ends = ends;
        first = open + 1;
        end = ends[open];
    }

    public static GcNativeBlock Read(string path, string type)
    {
        return ReadRoot(path, new[] { type });
    }

    public static GcNativeBlock ReadMissionHeader(string path)
    {
        return ReadRoot(path, new[] { "GC_MissionHeader", "GC_ScenarioFrameworkMissionHeader" });
    }

    static GcNativeBlock ReadRoot(string path, string[] types)
    {
        string source = File.ReadAllText(path);
        var tokens = new List<string>();
        var stack = new Stack<int>();
        var ends = new Dictionary<int, int>();
        int pos = 0;
        foreach (Match match in Tokens.Matches(source))
        {
            pos += match.Length;
            string s = match.Value;
            if (Char.IsWhiteSpace(s[0]) || s.StartsWith("//")) continue;
            if (s.StartsWith("/*"))
            {
                if (!s.EndsWith("*/")) throw new InvalidDataException("Unclosed comment in " + path);
                continue;
            }
            if (s == "{") stack.Push(tokens.Count);
            if (s == "}")
            {
                if (stack.Count == 0) throw new InvalidDataException("Unexpected closing brace in " + path);
                ends.Add(stack.Pop(), tokens.Count);
            }
            tokens.Add(s);
        }
        // Inherited root containers can have dependency-bearing fields in the
        // parent. Require a self-contained saved header/project for this reader.
        if (pos != source.Length || stack.Count != 0 || tokens.Count < 3 ||
            Array.IndexOf(types, tokens[0]) < 0 || tokens[1] != "{" || ends[1] != tokens.Count - 1)
            throw new InvalidDataException("Expected self-contained " + string.Join(" or ", types) + " in " + path);
        return new GcNativeBlock(tokens, ends, 1);
    }

    int Field(string name)
    {
        int found = -1;
        for (int i = first; i < end; i++)
        {
            if (tokens[i] == "{") i = ends[i];
            else if (tokens[i] == name)
            {
                if (found >= 0) throw new InvalidDataException("Duplicate field " + name);
                found = i + 1;
            }
        }
        return found;
    }

    static string Value(string s)
    {
        if (s == "{" || s == "}") throw new InvalidDataException("Expected native scalar");
        return s.StartsWith("\"") ? s.Substring(1, s.Length - 2) : s;
    }

    public string Text(string name)
    {
        int i = Field(name);
        return i < 0 ? null : Value(tokens[i]);
    }

    public GcNativeBlock Object(string name, string type)
    {
        int i = Field(name);
        if (i < 0) return null;
        if (type != null && type.Length > 0)
        {
            if (tokens[i++] != type) throw new InvalidDataException("Unexpected type of " + name);
            if (tokens[i].StartsWith("\"")) i++;
        }
        if (tokens[i] != "{") throw new InvalidDataException("Expected container " + name);
        return new GcNativeBlock(tokens, ends, i);
    }

    public string[] Strings(string name)
    {
        var block = Object(name, null);
        var result = new List<string>();
        if (block != null)
            for (int i = block.first; i < block.end; i++) result.Add(Value(tokens[i]));
        return result.ToArray();
    }

    public IEnumerable<GcNativeBlock> Rows(string type)
    {
        for (int i = first; i < end; i++)
        {
            if (tokens[i++] != type) throw new InvalidDataException("Expected " + type);
            if (tokens[i].StartsWith("\"")) i++;
            if (tokens[i] != "{") throw new InvalidDataException("Expected row");
            yield return new GcNativeBlock(tokens, ends, i);
            i = ends[i];
        }
    }
}
'@
 }
$stock = @('5614BBCCBB55ED1C','58D0FB3206B6F859')

function Get-AddonGuid([string]$value) {
    if ($value -notmatch '^[a-f0-9]{16}$') { throw "Invalid addon GUID '$value'" }
    $value.ToUpperInvariant()
}

function Get-AddonVersionPins($values) {
    $pins = @{}
    foreach ($value in $values) {
        if ($value -isnot [string] -or $value -notmatch '^([0-9a-fA-F]{16})=([0-9]+(?:\.[0-9]+){1,3})$') {
            throw "Invalid required addon version '$value'"
        }
        $id = $Matches[1].ToUpperInvariant()
        $version = $Matches[2]
        if ($id -in $stock -or $pins.ContainsKey($id)) { throw "Duplicate or vanilla required addon version $id" }
        $pins[$id] = $version
    }
    return $pins
}

function Get-Resource([string]$value, [string]$extension) {
    if ($value -notmatch '^\{([0-9A-Fa-f]{16})\}([^:{}"<>|?*\x00-\x1f]+)$') { throw "Invalid resource '$value'" }
    $id = $Matches[1].ToUpperInvariant()
    $relative = $Matches[2].Replace('\','/')
    if (($relative.Split('/') | Where-Object { $_ -in @('', '.', '..') } | Measure-Object).Count -or
        [IO.Path]::GetExtension($relative) -ne $extension) { throw "Invalid resource path '$relative'" }
    [pscustomobject]@{ id = $id; path = $relative }
}

function Set-Field($object, $name, $value) {
    $object | Add-Member -MemberType NoteProperty -Name $name -Value $value -Force
}

function Read-MissionHeader([string]$path) {
    $header = Get-Item -LiteralPath $path
    $owner = $header.Directory
    while ($owner) {
        $projects = @(Get-ChildItem -LiteralPath $owner.FullName -File -Filter '*.gproj')
        if ($projects.Count -gt 1) { throw "Ambiguous mission owner in $($owner.FullName)" }
        if ($projects.Count -eq 1) { break }
        $owner = $owner.Parent
    }
    if (!$owner) { throw 'A loose .conf lacks its scenario and owner IDs. Drop the header from its source addon, or drop its exported .launch.json.' }
    $scenario = [GcNativeBlock]::Read("$($header.FullName).meta",'MetaFileClass').Text('Name')
    $resource = Get-Resource $scenario '.conf'
    if ([IO.Path]::GetFullPath((Join-Path $owner.FullName $resource.path)) -ne $header.FullName) { throw 'Scenario .meta does not match the deployed header path' }
    $body = [GcNativeBlock]::ReadMissionHeader($header.FullName)
    $world = $body.Text('World')
    $worldResource = Get-Resource $world '.ent'
    $catalog = $body.Object('m_GC_Catalog','GC_CatalogMissionMetadata')
    $generated = $body.Object('m_GC_GeneratedMetadata','GC_GeneratedMissionMetadata')
    if (!$generated -or $generated.Text('m_iSchemaVersion') -notin @('1','2') -or $generated.Text('m_sWorld') -ne $world) {
        throw 'Refresh metadata for this world with GC > Create Mission Data > Refresh metadata only'
    }
    $coverage = 'placed-prefab references only (schema 1)'
    if ($generated.Text('m_iSchemaVersion') -eq '2') {
        $coverage = $generated.Text('m_sResourceCoverage')
        if ($coverage -ne 'authored_container_graph_partial') { throw 'Unsupported saved resource coverage' }
    } else { Write-Warning "Legacy mission metadata: $coverage. Refresh for nested resource coverage." }
    $pins = Get-AddonVersionPins ($body.Strings('m_aRequiredAddonVersions'))
    $required = @($pins.Keys)
    if ($catalog) {
        if ($catalog.Text('m_sMissionType') -in @('Broken','BROKEN')) { throw 'Mission is marked broken' }
        $required += $catalog.Strings('m_aRequiredAddonIds')
    }
    $table = $generated.Object('m_aLoadedAddons','')
    $references = $generated.Object('m_aReferencedAddonIds','')
    if (!$table -or !$references) { throw 'Incomplete saved addon evidence' }
    $names = @{}
    $identities = @{}
    foreach ($row in $table.Rows('GC_MissionMetadataAddon')) {
        $name = $row.Text('m_sId')
        $id = Get-AddonGuid $row.Text('m_sGuid')
        if (!$name -or $names.ContainsKey($name) -or $identities.ContainsKey($id)) { throw "Ambiguous captured addon $name/$id" }
        $names[$name] = $id
        $identities[$id] = $name
    }
    foreach ($name in $generated.Strings('m_aReferencedAddonIds')) {
        if (!$names.ContainsKey($name)) { throw "No captured GUID for referenced addon $name" }
        $required += $names[$name]
    }

    $ownerProject = [GcNativeBlock]::Read($projects[0].FullName,'GameProject')
    $ownerId = Get-AddonGuid $ownerProject.Text('GUID')
    $required = @(@($ownerId) + $required | ForEach-Object { Get-AddonGuid $_ } | Where-Object { $_ -notin $stock } | Sort-Object -Unique)
    $knownWorldFile = $null
    $ownerMeta = Join-Path $owner.FullName ($worldResource.path + '.meta')
    if (Test-Path -LiteralPath $ownerMeta) {
        if ([GcNativeBlock]::Read($ownerMeta,'MetaFileClass').Text('Name') -ne $world) { throw 'World .meta identity mismatch' }
        $knownWorldFile = '$' + $ownerProject.Text('ID') + ':' + $worldResource.path
    }
    $metadata = [pscustomobject]@{
        format='gc-mission-launch-v1'; scenario=$scenario; world=$world; worldFile=$knownWorldFile
        coverage=$coverage; requiredAddonIds=$required; headerSha256=(Get-FileHash -LiteralPath $header.FullName).Hash
        ownerProject=$projects[0].FullName
    }
    if ($pins.Count) {
        $versions = @($pins.Keys | Sort-Object | ForEach-Object { "$_=$($pins[$_])" })
        Set-Field $metadata requiredAddonVersions $versions
    }
    $metadata
}

if ([IO.Path]::GetExtension($Mission) -eq '.json') {
    if ($Local -or $ExportMetadata) { throw 'Use the native mission header for export or local addon testing' }
    $metadata = Get-Content -LiteralPath $Mission -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($metadata.format -cne 'gc-mission-launch-v1' -or $metadata.requiredAddonIds -isnot [array] -or !$metadata.requiredAddonIds.Count) {
        throw 'Expected portable GC launch metadata'
    }
} else {
    $metadata = Read-MissionHeader $Mission
}
if ($ExportMetadata) {
    $portable = $metadata | Select-Object -Property * -ExcludeProperty ownerProject
    $destination = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ExportMetadata)
    if ([IO.Path]::GetExtension($destination) -ne '.json') { throw 'Export path must end in .json' }
    $temp = Join-Path (Split-Path $destination) ([guid]::NewGuid().ToString() + '.tmp')
    try {
        [IO.File]::WriteAllText($temp,($portable | ConvertTo-Json -Depth 5))
        if ([IO.File]::Exists($destination)) {
            $previous = [IO.File]::ReadAllText($destination) | ConvertFrom-Json
            if ($previous.format -cne $portable.format -or $previous.scenario -ne $portable.scenario) {
                throw 'Export destination already belongs to another file or mission'
            }
            [IO.File]::Replace($temp,$destination,[NullString]::Value)
        } else { [IO.File]::Move($temp,$destination) }
    } finally { [IO.File]::Delete($temp) }
    return
}
$scenario = $metadata.scenario
$resource = Get-Resource $scenario '.conf'
$world = $metadata.world
$worldResource = Get-Resource $world '.ent'
$knownWorldFile = $metadata.worldFile
$coverage = $metadata.coverage
$versionPins = Get-AddonVersionPins $metadata.requiredAddonVersions
$required = @(@($metadata.requiredAddonIds) + @($versionPins.Keys) |
    ForEach-Object { Get-AddonGuid $_ })
if (!$Config) {
    $gcConfig = Join-Path $ServerDirectory 'configs\new-modpack.json'
    if (Test-Path -LiteralPath $gcConfig -PathType Leaf) { $Config = $gcConfig }
}
if (!$Config) {
    $candidates = @(
        foreach ($file in Get-ChildItem -LiteralPath $ServerDirectory -File -Filter '*.json') {
            if ($file.Name -eq 'gc-launch.generated.json') { continue }
            try { $candidate = [IO.File]::ReadAllText($file.FullName) | ConvertFrom-Json } catch { continue }
            if ($candidate.game -is [pscustomobject]) { $file.FullName }
        }
    )
    if ($candidates.Count -ne 1) { throw "Found $($candidates.Count) server configs in $ServerDirectory. Select the server JSON with -Config." }
    $Config = $candidates[0]
}
$configFile = (Get-Item -LiteralPath $Config).FullName
$template = Get-Content -LiteralPath $configFile -Encoding UTF8 -Raw | ConvertFrom-Json
if ($template.game -isnot [pscustomobject]) { throw 'Server JSON must contain a game object' }
$entries = [ordered]@{}
foreach ($entry in $template.game.mods) {
    $id = Get-AddonGuid $entry.modId
    if ($entries.Contains($id) -or $id -in $stock) { throw "Duplicate or vanilla baseline entry $id" }
    $entries[$id] = $entry
}
$baseline = @($entries.Keys)
foreach ($id in $required) {
    if (!$entries.Contains($id)) { $entries[$id] = [pscustomobject]@{modId=$id} }
    Set-Field $entries[$id] required $true
}
foreach ($id in $versionPins.Keys) {
    $expected = $versionPins[$id]
    if ($entries[$id].version -and $entries[$id].version -cne $expected) {
        throw "Server template version conflicts with the mission requirement for ${id}=$expected"
    }
    Set-Field $entries[$id] version $expected
}
Set-Field $template.game scenarioId $scenario
Set-Field $template.game mods @($entries.Values)
function Get-Projects($paths, $directories) {
    foreach ($dir in $directories) {
        foreach ($child in Get-ChildItem -Directory -LiteralPath $dir) {
            $paths += @(Get-ChildItem -LiteralPath $child.FullName -File -Filter '*.gproj' | ForEach-Object FullName)
        }
    }
    $index = @{}
    foreach ($path in @($paths | Where-Object { $_ } | ForEach-Object { (Get-Item -LiteralPath $_).FullName } | Sort-Object -Unique)) {
        $native = [GcNativeBlock]::Read($path,'GameProject')
        $id = Get-AddonGuid $native.Text('GUID')
        $name = $native.Text('ID')
        if ($index.ContainsKey($id) -or $name -notmatch '^[a-z0-9_.-]{1,128}$' -or $name -in @('.','..')) {
            throw "Invalid or duplicate addon project $name/$id"
        }
        $index[$id] = [pscustomobject]@{name=$name; directory=(Split-Path $path); dependencies=$native.Strings('Dependencies')}
    }
    $index
}

function Get-Dependencies($index, $roots) {
    $closure = @{}
    $namesSeen = @{}
    $pending = [Collections.Generic.Stack[string]]::new()
    foreach ($value in $roots) { $pending.Push($value) }
    while ($pending.Count) {
        $id = Get-AddonGuid $pending.Pop()
        if ($id -in $stock -or $closure.ContainsKey($id)) { continue }
        if (!$index.ContainsKey($id)) { throw "Cannot verify dependency $id. Its addon.gproj is missing." }
        $addon = $index[$id]
        if ($namesSeen.ContainsKey($addon.name) -or $addon.name -match '^GlobalConflicts(Workshop|Workbench)(Dev)?$') {
            throw "Ambiguous addon name or authoring collection: $($addon.name)"
        }
        $namesSeen[$addon.name] = $true
        $closure[$id] = $addon
        foreach ($dependency in $addon.dependencies) { $pending.Push($dependency) }
    }
    $closure
}

function Assert-AddonVersion($addon, [string]$id, [string]$version) {
    $path = Join-Path $addon.directory 'ServerData.json'
    if (!(Test-Path -LiteralPath $path)) { throw "Cannot verify required addon version ${id}=$version" }
    $data = Get-Content -LiteralPath $path -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($data.id -ne $id -or $data.revision.version -cne $version) {
        throw "Installed addon version mismatch for ${id}=$version"
    }
}

if ($entries.Contains('6B8E05204D634FA9')) { throw 'GC Workshop is an authoring collection' }
$selected = $null
if ($Local) {
    $paths = @($metadata.ownerProject) + $Project
    $installed = Get-Projects $paths $AddonDirectory
    $selected = Get-Dependencies $installed $entries.Keys
    foreach ($id in $versionPins.Keys) { Assert-AddonVersion $selected[$id] $id $versionPins[$id] }
}
$plan = [pscustomobject]@{scenario=$scenario; world=$world; worldFile=$knownWorldFile; coverage=$coverage; mods=@($entries.Values); baseline=$baseline}
if ($Local) { Set-Field $plan localAddonIds @($selected.Keys | Sort-Object) }
if ($PlanOnly) { return $plan }

if (!$ServerExe) { $ServerExe = Join-Path $ServerDirectory 'ArmaReforgerServer.exe' }
$exe = (Get-Item -LiteralPath $ServerExe).FullName
if (!$ProfileDirectory) { $ProfileDirectory = Join-Path (Split-Path $configFile) 'gc-server-profile' }
$profilePath = [IO.Directory]::CreateDirectory($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ProfileDirectory)).FullName
if (!$AddonDownloadDirectory) { $AddonDownloadDirectory = Join-Path $profilePath 'workshop' }
$cache = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($AddonDownloadDirectory)
$generatedPath = Join-Path $profilePath 'gc-launch.generated.json'
if ($generatedPath -eq $configFile) { throw 'Choose a base config distinct from the generated output' }
$lock = [IO.File]::Open((Join-Path $profilePath 'gc-launch.lock'),'OpenOrCreate','ReadWrite','None')
$child = $null
$stage = $null
$active = Join-Path $profilePath 'gc-launch.active'
$ownsMarker = $false
try {
    if (Test-Path -LiteralPath $active) { throw "Previous launcher exited without cleanup. Check its server and reconcile $active before restarting." }
    $nonce = [guid]::NewGuid().ToString()
    $arguments = @('-profile',$profilePath,'-gcLaunchId',$nonce,'-maxFPS','60')
    if ($Local) {
        Write-Warning 'Local addon test uses -server and does not apply server JSON hosting settings.'
        $stage = [IO.Directory]::CreateDirectory((Join-Path $profilePath "local-$nonce")).FullName
        foreach ($addon in $selected.Values) {
            $null = New-Item -ItemType Junction -Path (Join-Path $stage $addon.name) -Target $addon.directory
        }
        if ($LocalBaseProject) { $arguments += @('-gproj',(Get-Item -LiteralPath $LocalBaseProject).FullName) }
        $localIds = foreach ($id in $selected.Keys | Sort-Object) { $selected[$id].name }
        $arguments += @('-addonsDir',$stage,'-addons',($localIds -join ','),'-server',$scenario)
    } else {
        $json = $template | ConvertTo-Json -Depth 100
        $tempConfig = Join-Path $profilePath "$nonce.tmp"
        [IO.File]::WriteAllText($tempConfig,$json)
        if ([IO.File]::Exists($generatedPath)) { [IO.File]::Replace($tempConfig,$generatedPath,[NullString]::Value) }
        else { [IO.File]::Move($tempConfig,$generatedPath) }
        $arguments += @('-config',$generatedPath,'-addonDownloadDir',$cache)
    }
    # Arguments are fixed switches, validated native IDs and Windows paths.
    $quoted = foreach ($arg in $arguments) { '"' + $arg.Replace('"','\"') + '"' }
    [IO.File]::WriteAllText($active,"Launch $nonce, scenario $scenario, profile $profilePath")
    $ownsMarker = $true
    $child = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) -ArgumentList ($quoted -join ' ') -WindowStyle Hidden -PassThru
    $receiptPath = Join-Path $profilePath 'profile\GCLaunchReceipt.json'
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        if ($child.HasExited) { throw "Reforger exited during startup ($($child.ExitCode)). See $profilePath." }
        if ($timer.Elapsed.TotalSeconds -ge $StartupTimeoutSeconds) { throw "Timed out waiting for startup receipt. See $profilePath." }
        $receipt = $null
        if ([IO.File]::Exists($receiptPath)) {
            try { $receipt = [IO.File]::ReadAllText($receiptPath) | ConvertFrom-Json } catch { }
        }
        if ($receipt -and $receipt.launchId -ceq $nonce) {
            $actual = @($receipt.addonGuids | Where-Object { $_ -notin $stock } | Sort-Object -Unique)
            if (!$Local) {
                # The engine has fetched the declared Workshop mods and their native dependencies.
                $installed = Get-Projects @() @((Join-Path $cache 'addons'))
                $roots = foreach ($id in $entries.Keys) {
                    if ($entries[$id].required -isnot [bool] -or $entries[$id].required -or $id -in $actual) { $id }
                }
                $selected = Get-Dependencies $installed $roots
                foreach ($id in $entries.Keys) {
                    if ($entries[$id].version -and $id -in $actual) {
                        Assert-AddonVersion $selected[$id] $id $entries[$id].version
                    }
                }
            }
            $allowedWorlds = @($knownWorldFile)
            if (!$knownWorldFile) {
                $allowedWorlds = foreach ($name in @('core','ArmaReforger') + @($selected.Values.name)) { '$' + $name + ':' + $worldResource.path }
            }
            if ($receipt.version -ne 1 -or $receipt.scenario -notin @($scenario,$resource.id) -or
                $receipt.world -notin @($world,$worldResource.id) -or $receipt.worldFile -notin $allowedWorlds -or
                (Compare-Object @($selected.Keys) $actual)) { throw 'Server receipt differs from the requested scenario, world or addon set' }
            break
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Output "Ready: $scenario ($($selected.Count) addons), PID $($child.Id)"
    if (!$StopAfterReceipt) {
        while (!$child.WaitForExit(500)) { }
        if ($child.ExitCode -ne 0) { throw "Server exited with code $($child.ExitCode)" }
    }
} finally {
    if ($child -and !$child.HasExited) { $child.Kill(); $child.WaitForExit() }
    if ($ownsMarker) { [IO.File]::Delete($active) }
    if ($tempConfig -and [IO.File]::Exists($tempConfig)) { [IO.File]::Delete($tempConfig) }
    if ($stage) {
        foreach ($junction in [IO.Directory]::GetDirectories($stage)) { [IO.Directory]::Delete($junction) }
        [IO.Directory]::Delete($stage)
    }
    $lock.Dispose()
}
