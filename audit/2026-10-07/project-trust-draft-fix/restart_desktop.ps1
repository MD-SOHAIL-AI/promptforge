$ErrorActionPreference = 'Stop'
$taskRoot = 'D:\projects\ForgeX-Phase6-Final'
Set-Location -LiteralPath $taskRoot
$activeCount = @'
from pathlib import Path
import sqlite3
path=Path('.promptforge/state/agent-orchestration.sqlite3').resolve()
with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as conn:
 conn.execute('PRAGMA query_only=ON')
 print(conn.execute("SELECT COUNT(*) FROM agent_runs WHERE status IN ('running','queued','starting','awaiting_approval')").fetchone()[0])
'@ | & "$taskRoot\.venv\Scripts\python.exe" -
if ($LASTEXITCODE -ne 0 -or [int]$activeCount -ne 0) {
    throw 'An active agent run must finish before restarting ForgeX.'
}
$ownedWindows = @(Get-Process electron -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -eq "$taskRoot\node_modules\electron\dist\electron.exe" -and $_.MainWindowHandle -ne 0
})
$processInventory = @(Get-CimInstance Win32_Process)
$previousLaunchers = @()
foreach ($desktopProcess in $ownedWindows) {
    $ancestorPid = $desktopProcess.Id
    for ($depth = 0; $depth -lt 12 -and $ancestorPid -gt 0; $depth++) {
        $ancestor = $processInventory | Where-Object ProcessId -eq $ancestorPid | Select-Object -First 1
        if (-not $ancestor) { break }
        if ($ancestor.Name -eq 'node.exe' -and $ancestor.CommandLine -like '*scripts/dev-desktop.mjs*') {
            $previousLaunchers += Get-Process -Id $ancestor.ProcessId -ErrorAction SilentlyContinue
            break
        }
        $ancestorPid = $ancestor.ParentProcessId
    }
}
foreach ($desktopProcess in $ownedWindows) {
    if (-not $desktopProcess.CloseMainWindow()) { throw 'ForgeX did not accept the window-close request.' }
    if (-not $desktopProcess.WaitForExit(30000)) { throw 'ForgeX is still shutting down.' }
}
foreach ($previousLauncher in $previousLaunchers) {
    if (-not $previousLauncher.WaitForExit(30000)) { throw 'The previous desktop launcher is still shutting down.' }
}
$launchAudit = Join-Path $taskRoot ('audit\2026-10-07\project-trust-draft-fix\launch-' + (Get-Date -Format 'HHmmss'))
New-Item -ItemType Directory -Path $launchAudit | Out-Null
$hadNodeMode = Test-Path Env:ELECTRON_RUN_AS_NODE
$previousNodeMode = $env:ELECTRON_RUN_AS_NODE
try {
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    $launcher = Start-Process -FilePath 'node' -ArgumentList @('scripts/dev-desktop.mjs') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $launchAudit 'desktop.stdout.log') -RedirectStandardError (Join-Path $launchAudit 'desktop.stderr.log') -PassThru
} finally {
    if ($hadNodeMode) { $env:ELECTRON_RUN_AS_NODE = $previousNodeMode }
}
$receipt = @{ launcher_pid=$launcher.Id; launch_audit=$launchAudit; previous_window_pids=@($ownedWindows | ForEach-Object Id) }
$receipt | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $taskRoot 'audit\2026-10-07\project-trust-draft-fix\launch-receipt.json')
$receipt | ConvertTo-Json -Compress
