#requires -Version 5.1
# Bundle the editable sources for single-file installation on Windows.
param([string]$Output = (Join-Path $PSScriptRoot '..\..\Start-GCMission.bat'))
$ErrorActionPreference = 'Stop'
$script = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'Start-GCMission.ps1'))
$reader = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'GcNativeBlock.cs')).TrimEnd()
$loader = 'Add-Type -Path "$PSScriptRoot\GcNativeBlock.cs"'
if (!$script.Contains($loader)) { throw 'Cannot locate native reader loader' }
$script = $script.Replace($loader, "Add-Type -TypeDefinition @'" + [Environment]::NewLine + $reader + [Environment]::NewLine + "'@" + [Environment]::NewLine)
$script = $script.Replace('[string]$ServerDirectory = $PSScriptRoot','[string]$ServerDirectory = $env:GC_LAUNCH_DIRECTORY')
$stub = @'
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
'@
$bundle = ($stub + [Environment]::NewLine + $script) -replace '\r?\n', "`r`n"
[IO.File]::WriteAllText([IO.Path]::GetFullPath($Output),$bundle,[Text.UTF8Encoding]::new($false))
