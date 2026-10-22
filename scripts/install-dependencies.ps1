# Invoked by install_dependencies.bat. No application tests are run.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$transcriptStarted = $false
$installExitCode = 1
Push-Location -LiteralPath $projectRoot

function Invoke-InstallStep {
    param([string]$Title, [string]$Executable, [string[]]$CommandArguments)
    Write-Host "`n==> $Title" -ForegroundColor Cyan
    & $Executable @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Title failed with exit code $LASTEXITCODE. Fix the reported error, then run install_dependencies.bat again."
    }
}

function Refresh-ProcessPath {
    $env:Path = (@(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        $env:Path
    ) | Where-Object { $_ }) -join ';'
}

function Install-Runtime {
    param([string]$PackageId, [string]$DisplayName, [string]$ManualUrl)
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "$DisplayName is required. Install it from $ManualUrl, then run this batch file again. Automatic installation requires WinGet (Windows App Installer)."
    }
    Invoke-InstallStep "Install or update $DisplayName (Windows may request administrator approval)" $winget.Source @(
        'install', '--exact', '--id', $PackageId, '--source', 'winget',
        '--silent', '--accept-package-agreements', '--accept-source-agreements',
        '--disable-interactivity'
    )
    Refresh-ProcessPath
}

function Test-Python313 {
    param([string]$Executable)
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    try {
        & $Executable -B -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)' *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-Python313 {
    $candidates = @()
    if ($env:FORGEX_PYTHON) { $candidates += $env:FORGEX_PYTHON }
    if ($env:PYTHON) { $candidates += $env:PYTHON }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $detected = & $launcher.Source -3.13 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $detected) { $candidates += [string]($detected | Select-Object -Last 1) }
        } catch { }
    }
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { $candidates += $pythonCommand.Source }
    if ($env:LOCALAPPDATA) { $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe' }
    if ($env:ProgramFiles) { $candidates += Join-Path $env:ProgramFiles 'Python313\python.exe' }
    foreach ($candidate in $candidates) {
        if (Test-Python313 $candidate) { return $candidate }
    }
    return $null
}

function Find-CompatibleNode {
    foreach ($nodeCommand in @(Get-Command node.exe -All -ErrorAction SilentlyContinue)) {
        try {
            $versionText = & $nodeCommand.Source --version
            if ($LASTEXITCODE -ne 0) { continue }
            $nodeVersion = [version](([string]$versionText).Trim().TrimStart('v'))
            # The locked ini@7 package is stricter than the root package.json.
            if (($nodeVersion.Major -eq 22 -and $nodeVersion -ge [version]'22.22.2') -or
                ($nodeVersion.Major -eq 24 -and $nodeVersion -ge [version]'24.15.0') -or
                $nodeVersion.Major -ge 26) {
                return $nodeCommand.Source
            }
        } catch { }
    }
    return $null
}

function Install-PythonDependencies {
    param([string]$Python, [string]$LocalCheckLog)
    $pythonInstallArguments = @('-m', 'pip', '--disable-pip-version-check', 'install', '-r', 'requirements.txt', '-r', 'requirements-runtime.in')
    Write-Host "`n==> Check installed Python and PlatformIO dependencies without contacting a registry" -ForegroundColor Cyan
    $localPythonReady = $false
    try {
        & $Python @pythonInstallArguments --no-index *> $LocalCheckLog
        $localPythonReady = ($LASTEXITCODE -eq 0)
    } catch {
        # Windows PowerShell can raise on redirected native stderr, including
        # pip's expected 'not found locally' response. Continue to normal install.
        $localPythonReady = $false
    }
    if ($localPythonReady) {
        Write-Host 'Reusing installed Python and PlatformIO packages; no download or reinstall.'
    } else {
        # No --upgrade, --force-reinstall, or --ignore-installed: pip preserves
        # satisfied requirements and uses its normal shared download/wheel cache.
        Invoke-InstallStep 'Install only missing or mismatched Python dependencies' $Python $pythonInstallArguments
    }
}

function Install-NodeDependencies {
    param([string]$Directory, [string]$Label)
    Write-Host "`n==> Check locally installed $Label dependencies" -ForegroundColor Cyan
    & $nodeExecutable (Join-Path $projectRoot 'scripts\check-local-node-dependencies.mjs') $Directory
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Reusing installed $Label packages; no download or reinstall."
        return
    }
    # npm install retains matching node_modules entries; npm ci would delete them.
    # Try the project's previous cache, then npm's configured shared cache, before
    # permitting registry access. Failed offline attempts can still add cached
    # packages, which the next incremental attempt retains.
    $installArguments = @('--prefix', $Directory, 'install', '--include=dev', '--no-audit', '--no-fund')
    $projectCache = Join-Path $projectRoot '.cache\npm'
    if (Test-Path -LiteralPath (Join-Path $projectCache '_cacache\content-v2')) {
        Write-Host "Reusing the project npm cache for $Label."
        & $npmExecutable @installArguments --offline --cache $projectCache
        if ($LASTEXITCODE -eq 0) { return }
    }
    Write-Host "Trying npm's configured local cache for $Label."
    & $npmExecutable @installArguments --offline
    if ($LASTEXITCODE -eq 0) { return }
    Invoke-InstallStep "Install only missing or mismatched $Label dependencies (prefer cached packages)" $npmExecutable ($installArguments + @('--prefer-offline'))
}

try {
    foreach ($requiredFile in @('package-lock.json', 'frontend\package-lock.json', 'requirements.txt', 'requirements-runtime.in')) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Missing $requiredFile. Keep this batch file and its scripts folder inside the complete ForgeX project."
        }
    }
    New-Item -ItemType Directory -Force -Path 'reports' | Out-Null
    $logPath = Join-Path $projectRoot ('reports\install-dependencies-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
    Start-Transcript -Path $logPath | Out-Null
    $transcriptStarted = $true
    Write-Host 'ForgeX dependency installer' -ForegroundColor Cyan
    Write-Host 'Existing compatible runtimes and installed packages are reused. Caches are tried before downloads.'
    Write-Host 'Only missing or incompatible dependencies need internet access. Missing runtimes use WinGet.'
    Write-Host "Project: $projectRoot"
    Write-Host "Log: $logPath"

    $nodeExecutable = Find-CompatibleNode
    if (-not $nodeExecutable) {
        Install-Runtime 'OpenJS.NodeJS.LTS' 'Node.js LTS' 'https://nodejs.org/en/download'
        $nodeExecutable = Find-CompatibleNode
    }
    if (-not $nodeExecutable) {
        throw 'Node.js 22.22.2+, 24.15.0+, or 26+ is required by the locked dependencies. Install a compatible version, reopen the batch file, and retry.'
    }
    # Match npm to the selected Node installation instead of an unrelated PATH entry.
    $npmExecutable = Join-Path (Split-Path -Parent $nodeExecutable) 'npm.cmd'
    if (-not (Test-Path -LiteralPath $npmExecutable -PathType Leaf)) {
        throw "npm.cmd is missing beside $nodeExecutable. Repair the Node.js installation and retry."
    }
    $env:Path = (Split-Path -Parent $nodeExecutable) + ';' + $env:Path

    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath '.venv') {
        if (-not (Test-Python313 $venvPython)) {
            throw 'The existing .venv is incomplete or uses a different Python version. Rename .venv as a backup, then rerun this installer to create a Python 3.13 environment.'
        }
    } else {
        $pythonExecutable = Find-Python313
        if (-not $pythonExecutable) {
            Install-Runtime 'Python.Python.3.13' 'Python 3.13' 'https://www.python.org/downloads/windows/'
            $pythonExecutable = Find-Python313
        }
        if (-not $pythonExecutable) {
            throw 'Python 3.13 could not be located after installation. Reopen this batch file after the Python installer finishes.'
        }
        # Matching packages already installed for this Python 3.13 interpreter
        # remain available; other versions are installed locally inside .venv.
        Invoke-InstallStep 'Create the project Python environment using available local packages' $pythonExecutable @('-m', 'venv', '--system-site-packages', '.venv')
    }

    Invoke-InstallStep 'Confirm existing pip (without upgrading)' $venvPython @('-m', 'pip', '--version')
    $localPythonLog = [IO.Path]::ChangeExtension($logPath, 'python-local.log')
    Install-PythonDependencies $venvPython $localPythonLog
    Install-NodeDependencies $projectRoot 'Electron and model-worker'
    Install-NodeDependencies (Join-Path $projectRoot 'frontend') 'frontend'
    if (-not (Test-Path -LiteralPath 'node_modules\electron\dist\electron.exe' -PathType Leaf)) {
        Invoke-InstallStep 'Restore the missing Electron executable using its cache when available' $nodeExecutable @('node_modules/electron/install.js')
    } else {
        Write-Host 'Reusing the installed Electron executable.'
    }
    Invoke-InstallStep 'Prepare Monaco editor assets' $nodeExecutable @('scripts/prepare-frontend-assets.mjs')
    Invoke-InstallStep 'Build the model worker, harness, and Electron shell' $npmExecutable @('run', 'build:electron')
    Invoke-InstallStep 'Confirm Python dependency compatibility' $venvPython @('-m', 'pip', 'check')
    Invoke-InstallStep 'Confirm PlatformIO installation' $venvPython @('-m', 'platformio', '--version')
    if (-not (Test-Path -LiteralPath 'node_modules\electron\dist\electron.exe' -PathType Leaf)) {
        throw 'The Electron executable was not downloaded. Remove ELECTRON_SKIP_BINARY_DOWNLOAD from this terminal environment if set, then rerun the installer.'
    }

    Write-Host "`nForgeX dependencies and desktop components are ready." -ForegroundColor Green
    Write-Host 'To launch ForgeX, open a terminal in this folder and run: npm run dev:desktop'
    $installExitCode = 0
} catch {
    Write-Host "`nINSTALLATION FAILED: $($_.Exception.Message)" -ForegroundColor Red
    if ($logPath) { Write-Host "Installation log: $logPath" }
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
    Pop-Location
}
exit $installExitCode
