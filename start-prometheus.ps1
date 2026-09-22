$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$prometheusExe = Join-Path $projectRoot "tools\prometheus-3.13.1.windows-amd64\prometheus.exe"
$configFile = Join-Path $projectRoot "prometheus\prometheus.yml"
$storagePath = Join-Path $projectRoot "prometheus\data_current"

if (-not (Test-Path -LiteralPath $prometheusExe)) {
    throw "Prometheus bulunamadı: $prometheusExe"
}

New-Item -ItemType Directory -Path $storagePath -Force | Out-Null
& $prometheusExe `
    "--config.file=$configFile" `
    "--storage.tsdb.path=$storagePath" `
    "--web.listen-address=127.0.0.1:9091"
