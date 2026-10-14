param(
    [Parameter(Mandatory = $true)]
    [string]$UvExecutable
)

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectDirectory
$env:UV_CACHE_DIR = Join-Path $projectDirectory '.uv-cache'

& $UvExecutable run radar daily
exit $LASTEXITCODE
