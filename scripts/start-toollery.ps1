# Windows x64 bootstrap. All changes are local to the launcher directory.
[CmdletBinding()]
param(
    [switch]$InstallOnly,
    [switch]$NoOpen,
    [int]$Port = 0,
    [string]$StateDirectory = ""
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
    throw 'This launcher currently supports Windows x64 only.'
}
if (-not $StateDirectory) { $StateDirectory = Join-Path $RepoRoot '.toollery-launcher' }
$StateDirectory = [IO.Path]::GetFullPath($StateDirectory)
$ToolsDirectory = Join-Path $StateDirectory 'tools'
New-Item -ItemType Directory -Force -Path $ToolsDirectory | Out-Null
# Two simultaneous installs must not partially overwrite the same runtimes.
$InstallLock = $null
try {
    $InstallLock = [IO.File]::Open((Join-Path $StateDirectory 'install.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
} catch { throw 'Another Toollery setup is running. Wait for it to finish, then retry.' }

function Download-Verified([string]$Url, [string]$Path, [string]$Sha256) {
    if ((Test-Path -LiteralPath $Path) -and (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $Sha256) { return }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile "$Path.part" -TimeoutSec 180
            if ((Get-FileHash -LiteralPath "$Path.part" -Algorithm SHA256).Hash -ne $Sha256) { throw 'Downloaded archive checksum mismatch.' }
            Move-Item -LiteralPath "$Path.part" -Destination $Path -Force
            return
        } catch {
            if ($Attempt -eq 3) { throw }
            Write-Host 'Download failed; retrying...'
        }
    }
}
function Run-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "A setup command failed (exit $LASTEXITCODE). Retry after checking network access and the message above." }
}

try {
    $UvExe = Join-Path $ToolsDirectory 'uv\uv.exe'
    if (-not (Test-Path -LiteralPath $UvExe)) {
        Write-Host '[1/4] Downloading pinned uv (Python environment manager)...'
        $UvArchive = Join-Path $ToolsDirectory 'uv-0.9.6.zip'
        Download-Verified 'https://github.com/astral-sh/uv/releases/download/0.9.6/uv-x86_64-pc-windows-msvc.zip' $UvArchive '4cde4a7907214053320d5ab4b6f9add77b7aca6eb39cc7e4928b61704c1328b1'
        Expand-Archive -LiteralPath $UvArchive -DestinationPath (Join-Path $ToolsDirectory 'uv') -Force
    }
    $NodeDirectory = Join-Path $ToolsDirectory 'node-v22.19.0-win-x64'
    $NodeExe = Join-Path $NodeDirectory 'node.exe'
    if (-not (Test-Path -LiteralPath $NodeExe)) {
        Write-Host '[2/4] Downloading Node.js 22.19.0...'
        $NodeArchive = Join-Path $ToolsDirectory 'node-22.19.0.zip'
        Download-Verified 'https://nodejs.org/dist/v22.19.0/node-v22.19.0-win-x64.zip' $NodeArchive 'ea3fad0e67a991d8477d8c01344b56e69c676ccb733f065b22436994b1253f86'
        Expand-Archive -LiteralPath $NodeArchive -DestinationPath $ToolsDirectory -Force
    }
    $PnpmDirectory = Join-Path $ToolsDirectory 'pnpm-11.25.0'
    # Process-local PATH only: no registry, shell profile, or global npm changes.
    $env:PATH = "$NodeDirectory;$(Join-Path $PnpmDirectory 'node_modules\.bin');$env:PATH"
    if (-not (Test-Path -LiteralPath (Join-Path $PnpmDirectory 'node_modules\pnpm\bin\pnpm.cjs'))) {
        Write-Host '[3/4] Installing pnpm 11.25.0...'
        Run-Checked $NodeExe @((Join-Path $NodeDirectory 'node_modules\npm\bin\npm-cli.js'), 'install', '--prefix', $PnpmDirectory, 'pnpm@11.25.0', '--ignore-scripts', '--no-audit', '--no-fund')
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $ToolsDirectory 'python'
    $env:UV_CACHE_DIR = Join-Path $StateDirectory 'uv-cache'
    $env:UV_PYTHON_DOWNLOADS = 'automatic'
    $PythonExe = Join-Path $StateDirectory 'venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        Write-Host '[4/4] Preparing private Python 3.12.12...'
        Run-Checked $UvExe @('venv', '--managed-python', '--python', '3.12.12', (Join-Path $StateDirectory 'venv'))
    }
    # Reinstall only when packaged source changes; do not require network on every launch.
    $SourceFiles = @((Get-Item -LiteralPath (Join-Path $RepoRoot 'pyproject.toml')))
    foreach ($DirectoryName in @('toollery', 'toollery_dsh')) {
        $SourceFiles += Get-ChildItem -LiteralPath (Join-Path $RepoRoot $DirectoryName) -File -Recurse | Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and $_.Extension -ne '.pyc' }
    }
    $SourceSignature = ($SourceFiles | Sort-Object FullName | ForEach-Object { $_.FullName.Substring($RepoRoot.Length) + ':' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }) -join "`n"
    $SignaturePath = Join-Path $StateDirectory 'installed-source.txt'
    if (-not (Test-Path -LiteralPath $SignaturePath) -or [IO.File]::ReadAllText($SignaturePath) -ne $SourceSignature) {
        Write-Host 'Installing Toollery and pinned DSH dependencies...'
        Run-Checked $UvExe @('pip', 'install', '--python', $PythonExe, '--reinstall-package', 'toollery-dsh', $RepoRoot)
        [IO.File]::WriteAllText($SignaturePath, $SourceSignature)
    }
} finally { if ($InstallLock) { $InstallLock.Dispose() } }

if ($InstallOnly) { Write-Host "Installation ready: $StateDirectory"; exit 0 }
$LauncherArguments = @('-m', 'toollery_dsh', 'launcher', '--directory', (Join-Path $StateDirectory 'workspace'), '--port', "$Port")
if ($NoOpen) { $LauncherArguments += '--no-open' }
Write-Host 'Starting the local setup page. Keep this window open; Ctrl+C stops the launcher and demo.'
Run-Checked $PythonExe $LauncherArguments
