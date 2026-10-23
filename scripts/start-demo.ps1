# Start the OpenTelemetry demo from the repo root.
#   .\scripts\start-demo.ps1          -> full demo (needs ~6 GB RAM)
#   .\scripts\start-demo.ps1 minimal  -> minimal (~3 GB, no Kafka)
param([string]$mode = "full")
Push-Location opentelemetry-demo
if ($mode -eq "minimal") {
  docker compose -f compose.yaml -f compose.observability.yaml up --force-recreate --remove-orphans --detach
} else {
  docker compose -f compose.yaml -f compose.full.yaml -f compose.observability.yaml up --force-recreate --remove-orphans --detach
}
Pop-Location
Write-Host "Shop:    http://localhost:8080"
Write-Host "Jaeger:  http://localhost:8080/jaeger/ui"
Write-Host "Grafana: http://localhost:8080/grafana/"
Write-Host "Locust:  http://localhost:8080/loadgen/"
