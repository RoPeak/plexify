param(
    [string]$OutputPath = "exports\plexify-clean.zip"
)

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$output = Join-Path $repoRoot $OutputPath
$outputDir = Split-Path -Parent $output
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$staging = Join-Path $env:TEMP ("plexify-export-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $staging | Out-Null

$excludePatterns = @(
    ".git",
    ".venv",
    "venv",
    ".pytest_cache",
    ".pytest_tmp",
    ".tmp",
    ".plexify",
    "__pycache__",
    "*.pyc",
    "exports",
    "artifacts"
)

Get-ChildItem -Path $repoRoot -Force | ForEach-Object {
    $name = $_.Name
    if ($excludePatterns -contains $name) {
        return
    }
    Copy-Item -Recurse -Force -Path $_.FullName -Destination (Join-Path $staging $name)
}

if (Test-Path $output) {
    Remove-Item -Force $output
}

Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $output -CompressionLevel Optimal
Remove-Item -Recurse -Force $staging

Write-Output "Created clean export: $output"
