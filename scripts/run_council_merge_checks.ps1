# Pre-merge checks: clean Postgres + alembic head + council verification (HTTP mocked).
# Requires Docker Desktop (Linux containers) running.
$ErrorActionPreference = "Stop"

$container = "ben-verify-pg"
$port = 55432
$dbUrl = "postgresql+asyncpg://verify:verify@127.0.0.1:${port}/verify"

docker rm -f $container 2>$null | Out-Null
docker run -d --name $container `
  -e POSTGRES_PASSWORD=verify `
  -e POSTGRES_USER=verify `
  -e POSTGRES_DB=verify `
  -p "${port}:5432" `
  postgres:16-alpine

$ready = $false
for ($i = 0; $i -lt 40; $i++) {
  $out = docker exec $container pg_isready -U verify 2>$null
  if ($LASTEXITCODE -eq 0 -and $out -match "accepting") { $ready = $true; break }
  Start-Sleep -Seconds 1
}
if (-not $ready) { throw "Postgres did not become ready in time." }

$env:DATABASE_URL = $dbUrl
Set-Location $PSScriptRoot\..

& .\.venv\Scripts\python.exe -m alembic -c database/migrations/alembic.ini upgrade head
& .\.venv\Scripts\python.exe scripts\verify_council_prerelease.py

Write-Host "All merge checks passed."
