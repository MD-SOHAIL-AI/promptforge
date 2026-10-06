param([Parameter(Mandatory=$true)][string]$Installer)
$ErrorActionPreference = "Stop"
$signature = Get-AuthenticodeSignature -LiteralPath $Installer
if ($signature.Status -ne "Valid") { throw "RELEASE_SIGNATURE_INVALID: $($signature.Status)" }
Write-Output "Installer Authenticode signature is valid."
