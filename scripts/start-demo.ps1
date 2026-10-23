# Full or minimal demo, with observability. Does not edit the demo.
param([ValidateSet("full", "minimal")][string]$mode = "full")
$ErrorActionPreference = "Stop"
$demoPath = Join-Path $PSScriptRoot "../opentelemetry-demo"
if (!(Test-Path $demoPath)) { throw "Clone opentelemetry-demo at the repository root first." }
Push-Location $demoPath
try {
    $files = @("compose.yaml", "compose.observability.yaml")
    if ($mode -eq "full") { $files += "compose.full.yaml" }
    foreach ($file in $files) {
        if (!(Test-Path $file)) { throw "Missing $file. Use the startup instructions for your demo revision." }
    }
    $composeArgs = @("compose", "-f", "compose.yaml")
    if ($mode -eq "full") { $composeArgs += @("-f", "compose.full.yaml") }
    $composeArgs += @("-f", "compose.observability.yaml", "up", "--detach")
    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
Write-Host "Shop: http://localhost:8080"
Write-Host "Jaeger: http://localhost:8080/jaeger/ui"
Write-Host "Grafana: http://localhost:8080/grafana/"
Write-Host "Locust: http://localhost:8080/loadgen/"
