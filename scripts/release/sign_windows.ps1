param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$ErrorActionPreference = "Stop"
$certificateBase64 = $env:WINDOWS_SIGNING_PFX_BASE64
$certificatePassword = $env:WINDOWS_SIGNING_PFX_PASSWORD

if ([string]::IsNullOrWhiteSpace($certificateBase64)) {
    Write-Host "WINDOWS_SIGNING_PFX_BASE64 is not configured; leaving installer unsigned."
    exit 0
}
if ([string]::IsNullOrWhiteSpace($certificatePassword)) {
    throw "WINDOWS_SIGNING_PFX_PASSWORD must be configured when a signing certificate is supplied."
}

$resolvedInstaller = (Resolve-Path -LiteralPath $Path).Path
$pfxPath = Join-Path $env:RUNNER_TEMP "bandscope-signing.pfx"
$timestampUrl = if ([string]::IsNullOrWhiteSpace($env:WINDOWS_TIMESTAMP_URL)) {
    "http://timestamp.digicert.com"
} else {
    $env:WINDOWS_TIMESTAMP_URL
}

try {
    [System.IO.File]::WriteAllBytes(
        $pfxPath,
        [System.Convert]::FromBase64String($certificateBase64)
    )

    $kitsRoot = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    $signTool = Get-ChildItem -LiteralPath $kitsRoot -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
        Sort-Object FullName -Descending |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $signTool) {
        throw "signtool.exe was not found on the GitHub Actions runner."
    }

    & $signTool sign /fd SHA256 /td SHA256 /tr $timestampUrl /f $pfxPath /p $certificatePassword $resolvedInstaller
    if ($LASTEXITCODE -ne 0) {
        throw "signtool sign failed with exit code $LASTEXITCODE."
    }

    & $signTool verify /pa /v $resolvedInstaller
    if ($LASTEXITCODE -ne 0) {
        throw "signtool verification failed with exit code $LASTEXITCODE."
    }
} finally {
    Remove-Item -LiteralPath $pfxPath -Force -ErrorAction SilentlyContinue
}
