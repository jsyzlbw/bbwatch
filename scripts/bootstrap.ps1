$ErrorActionPreference = "Stop"

if (-not $env:CLAUDE_PLUGIN_ROOT) {
    throw "CLAUDE_PLUGIN_ROOT is required"
}
if (-not $env:CLAUDE_PLUGIN_DATA) {
    throw "CLAUDE_PLUGIN_DATA is required"
}

$script = Join-Path $env:CLAUDE_PLUGIN_ROOT "scripts\bootstrap.py"
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    & $python.Source $script
} else {
    $launcher = (Get-Command py -ErrorAction Stop).Source
    & $launcher -3.11 $script
}
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
