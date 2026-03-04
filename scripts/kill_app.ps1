# scripts/kill_app.ps1
# Kill all running Flask/Python app processes for SEPA-StockLab
# Kills processes on port 5000 AND any python processes running app.py / start_web.py
# Usage: .\scripts\kill_app.ps1

param(
    [switch]$Force,    # Skip confirmation prompt
    [int]$Port = 5000  # Flask port (default 5000)
)

$killed = 0
$pids_to_kill = [System.Collections.Generic.HashSet[int]]::new()

Write-Host "`n=== SEPA-StockLab Kill App ===" -ForegroundColor Cyan

# ── 1. Find processes listening on port 5000 ─────────────────────────────────
Write-Host "`n[1] Checking port $Port..." -ForegroundColor Yellow
try {
    $netConns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $netConns) {
        $null = $pids_to_kill.Add($conn.OwningProcess)
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        $procName = if ($proc) { $proc.ProcessName } else { "unknown" }
        Write-Host "  Found: PID $($conn.OwningProcess) ($procName) on port $Port" -ForegroundColor Red
    }
    if ($netConns.Count -eq 0) {
        Write-Host "  No process listening on port $Port" -ForegroundColor Green
    }
} catch {
    Write-Host "  (Could not query port $Port - may need Admin)" -ForegroundColor Gray
}

# ── 2. Find Python processes running app.py or start_web.py ──────────────────
Write-Host "`n[2] Checking Python processes (app.py / start_web.py)..." -ForegroundColor Yellow
$pythonProcs = Get-WmiObject Win32_Process -Filter "Name='python.exe' OR Name='python3.exe'" -ErrorAction SilentlyContinue
foreach ($proc in $pythonProcs) {
    $cmdLine = $proc.CommandLine
    if ($cmdLine -match 'app\.py|start_web\.py|SEPA-StockLab') {
        $null = $pids_to_kill.Add([int]$proc.ProcessId)
        Write-Host "  Found: PID $($proc.ProcessId) — $($cmdLine -replace '.{0,60}(app\.py|start_web\.py)', '$1')" -ForegroundColor Red
    }
}

# ── 3. Also find any python.exe that has port 5000 open (belt-and-suspenders) ─
$allPython = Get-Process -Name "python", "python3" -ErrorAction SilentlyContinue
foreach ($proc in $allPython) {
    try {
        $connections = Get-NetTCPConnection -OwningProcess $proc.Id -ErrorAction SilentlyContinue
        foreach ($conn in $connections) {
            if ($conn.LocalPort -eq $Port -or $conn.RemotePort -eq $Port) {
                $null = $pids_to_kill.Add($proc.Id)
            }
        }
    } catch { }
}

# ── 4. Nothing to kill ───────────────────────────────────────────────────────
if ($pids_to_kill.Count -eq 0) {
    Write-Host "`n✅ No SEPA-StockLab processes found. Nothing to kill." -ForegroundColor Green
    exit 0
}

Write-Host "`nWill kill $($pids_to_kill.Count) process(es): $($pids_to_kill -join ', ')" -ForegroundColor Yellow

# ── 5. Confirm (unless -Force) ───────────────────────────────────────────────
if (-not $Force) {
    $confirm = Read-Host "Proceed? [Y/n]"
    if ($confirm -ne '' -and $confirm -notmatch '^[Yy]') {
        Write-Host "Cancelled." -ForegroundColor Gray
        exit 0
    }
}

# ── 6. Kill ──────────────────────────────────────────────────────────────────
foreach ($pid in $pids_to_kill) {
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($proc) {
        try {
            Stop-Process -Id $pid -Force -ErrorAction Stop
            Write-Host "  ✅ Killed PID $pid ($($proc.ProcessName))" -ForegroundColor Green
            $killed++
        } catch {
            Write-Host "  ❌ Failed to kill PID $pid : $_" -ForegroundColor Red
        }
    }
}

# ── 7. Verify port is free ───────────────────────────────────────────────────
Start-Sleep -Milliseconds 500
$stillListening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($stillListening) {
    Write-Host "`n⚠️  Port $Port is STILL in use. Try running as Administrator." -ForegroundColor Yellow
} else {
    Write-Host "`n✅ Port $Port is free." -ForegroundColor Green
}

Write-Host "`nDone. Killed $killed process(es).`n" -ForegroundColor Cyan
