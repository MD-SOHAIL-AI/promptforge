param(
  [switch]$Kill
)

$ErrorActionPreference = "Stop"
$ports = @(8000, 3000)

Write-Host "ForgeX dev port inspection"
Write-Host "No processes are killed unless -Kill is provided and each PID is confirmed."

foreach ($port in $ports) {
  Write-Host ""
  Write-Host "Port $port"
  $lines = netstat -ano | Select-String -Pattern "LISTENING" | Select-String -Pattern ":$port\s"
  if (-not $lines) {
    Write-Host "  No listener found."
    continue
  }

  foreach ($line in $lines) {
    $text = $line.Line.Trim()
    Write-Host "  $text"
    $parts = $text -split "\s+"
    $pidText = $parts[-1]
    $process = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
    if ($process) {
      Write-Host "  PID $pidText -> $($process.ProcessName)"
    }

    if ($Kill) {
      $answer = Read-Host "  Kill PID $pidText? Type the PID to confirm"
      if ($answer -eq $pidText) {
        Stop-Process -Id ([int]$pidText) -ErrorAction Stop
        Write-Host "  Killed PID $pidText"
      } else {
        Write-Host "  Skipped PID $pidText"
      }
    }
  }
}
