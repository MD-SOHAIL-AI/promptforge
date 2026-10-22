# ForgeX host-side release qualification. Run in PowerShell on Windows 10/11.
# No ESP32 device, serial port or physical flash is required.
# A failure in any step is a release blocker; missing tools are not silently skipped.
param([switch]$SkipDependencies, [switch]$SkipFrontendBuild)
$ErrorActionPreference = 'Stop'
$script:forgexFailed = $false
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$pythonExecutable = if ($env:FORGEX_PYTHON) { $env:FORGEX_PYTHON } elseif ($env:PYTHON) { $env:PYTHON } elseif (Test-Path -LiteralPath '.venv\Scripts\python.exe') { (Resolve-Path '.venv\Scripts\python.exe').Path } else { 'python' }
$results = New-Object System.Collections.ArrayList
function Check([string]$Title, [string]$Executable, [string[]]$Arguments) {
    Write-Host "`n==> $Title" -ForegroundColor Cyan
    $start = Get-Date
    try {
        & $Executable @Arguments
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 1 }
    } catch {
        Write-Host $_ -ForegroundColor Red
        $code = 1
    }
    [void]$results.Add([pscustomobject]@{ Step=$Title; ExitCode=$code; Seconds=[math]::Round(((Get-Date)-$start).TotalSeconds,1) })
    if ($code -ne 0) { throw "Verification blocked at '$Title' (exit $code)" }
}
try {
    Check 'Python version' $pythonExecutable @('--version')
    Check 'Node version' node @('--version')
    # Hard gate: this repo explicitly declares Node >= 22.19.0.
    $nodeVersion = [version]((& node --version).TrimStart('v').Split('-')[0])
    if ($nodeVersion -lt [version]'22.19.0') { throw 'Node 22.19.0 or newer is required' }
    if (-not $SkipDependencies) {
        Check 'Install model-worker and desktop dependencies' npm @('ci','--no-audit','--no-fund')
        Check 'Install frontend dependencies' npm @('--prefix','frontend','ci','--no-audit','--no-fund')
    }
    Check 'Model worker build' npm @('run','build:model-router')
    Check 'Python syntax' $pythonExecutable @('-m','compileall','-q','backend')
    Check 'Core Python and API regressions' $pythonExecutable @('-m','pytest','-q','tests/unit','tests/api','tests/integration')
    Check 'Model router tests' npm @('run','test:model-router')
    Check 'Electron tests' npm @('run','test:electron')
    Check 'Frontend typecheck' npm @('run','typecheck:frontend')
    Check 'Frontend state tests' npm @('run','test:ui-state')
    Check 'Release scripts tests' npm @('run','test:release-scripts')
    if (-not $SkipFrontendBuild) { Check 'Frontend production build' npm @('run','build:frontend') }
    Check 'Source release scan' npm @('run','scan:source-release')
    Write-Host "`nAll executed software checks passed. Real providers and Windows packaged installer remain separate qualification gates." -ForegroundColor Green
} catch {
    Write-Host "`nSOFTWARE QUALIFICATION FAILED: $_" -ForegroundColor Red
    $script:forgexFailed = $true
} finally {
    New-Item -ItemType Directory -Force -Path 'reports' | Out-Null
    $report = Join-Path 'reports' ('forgex-software-check-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
    @{
        CreatedAt=(Get-Date).ToUniversalTime().ToString('o');
        SoftwareQualified= -not [bool]$script:forgexFailed;
        HardwareQualified=$false;
        LiveProvidersQualified=$false;
        WindowsInstallerQualified=$false;
        Checks=@($results)
    } | ConvertTo-Json -Depth 5 | Set-Content -Path $report -Encoding utf8
    Write-Host "Evidence: $report"
}
if ($script:forgexFailed) { exit 1 }
