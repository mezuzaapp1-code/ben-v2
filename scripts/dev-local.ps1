# Clean local dev: stop stale listeners, start backend + frontend.
# Usage: .\scripts\dev-local.ps1
# Stop only: .\scripts\dev-local.ps1 -StopOnly

param([switch]$StopOnly)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Get-PortListeners {
    param([int]$Port)
    $seen = @{}
    $rows = @()
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $processId = $c.OwningProcess
        if (-not $processId -or $processId -eq 0 -or $seen.ContainsKey($processId)) { continue }
        $seen[$processId] = $true
        $procName = (Get-Process -Id $processId -ErrorAction SilentlyContinue).ProcessName
        $rows += [pscustomobject]@{ Port = $Port; PID = $processId; Process = $procName }
    }
    return $rows
}

function Report-ZombieListeners {
    param([int[]]$Ports)
    $all = @()
    foreach ($p in $Ports) {
        $all += Get-PortListeners -Port $p
    }
    if ($all.Count -eq 0) {
        Write-Host "No listeners on ports: $($Ports -join ', ')"
        return
    }
    Write-Host ""
    Write-Host "Port listeners detected (review before starting dev servers):"
    $all | Format-Table -AutoSize
    $multi = $all | Group-Object Port | Where-Object { $_.Count -gt 1 }
    if ($multi) {
        Write-Host "WARNING: Multiple processes on the same port (zombie/stale uvicorn risk)."
        Write-Host "  Run: .\scripts\dev-local.ps1 -StopOnly"
        Write-Host "  Or close the listed PIDs manually, then restart."
        Write-Host ""
    }
}

function Stop-PortListener {
    param([int]$Port)
    foreach ($row in Get-PortListeners -Port $Port) {
        Write-Host "Stopping PID $($row.PID) ($($row.Process)) on port $Port"
        Stop-Process -Id $row.PID -Force -ErrorAction SilentlyContinue
    }
}

# Use 8002 when 8000 has stuck zombie uvicorn listeners (common on Windows).
$BackendPort = if ($env:BEN_BACKEND_PORT) { [int]$env:BEN_BACKEND_PORT } else { 8002 }

Report-ZombieListeners -Ports @(8000, $BackendPort, 5173)

Stop-PortListener -Port 8000
Stop-PortListener -Port $BackendPort
Stop-PortListener -Port 5173
Start-Sleep -Seconds 1

if ($StopOnly) {
    Write-Host "Stopped listeners on 8000, $BackendPort, and 5173."
    exit 0
}

Write-Host "Starting backend on http://127.0.0.1:$BackendPort ..."
Start-Process -WorkingDirectory $Root -FilePath "$Root\.venv\Scripts\uvicorn.exe" `
    -ArgumentList "main:app", "--host", "127.0.0.1", "--port", "$BackendPort", "--reload" `
    -WindowStyle Normal

Write-Host "Starting frontend on http://localhost:5173 ..."
Start-Process -WorkingDirectory "$Root\frontend" -FilePath "npm.cmd" `
    -ArgumentList "run", "dev" `
    -WindowStyle Normal

Write-Host @"

Dev servers launched in separate windows.
  Frontend: http://localhost:5173
  Backend:  http://127.0.0.1:$BackendPort/health

Proxy: set frontend/.env.local -> VITE_DEV_API_PROXY=http://127.0.0.1:$BackendPort
To stop: .\scripts\dev-local.ps1 -StopOnly

"@
