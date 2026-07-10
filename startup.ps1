# Policy AGENT Startup Script (PowerShell)
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "            Policy AGENT Startup Utility           " -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Clean up ports 8000, 5173, and 8080
Write-Host "[1/5] Freeing up ports 8000, 5173, and 8080..." -ForegroundColor Yellow
$ports = @(8000, 5173, 8080)
foreach ($port in $ports) {
    $processes = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($processes) {
        foreach ($proc in $processes) {
            $pidToKill = $proc.OwningProcess
            if ($pidToKill) {
                Write-Host "Killing process on port $port (PID: $pidToKill)..." -ForegroundColor Magenta
                Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

# 2. Start database services
Write-Host ""
Write-Host "[2/5] Starting database services..." -ForegroundColor Yellow
$dockerAvailable = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerAvailable) {
    Write-Host "Docker detected. Starting Docker containers..." -ForegroundColor Gray
    docker compose -f infra/docker-compose.yml up -d postgres neo4j redis
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to start Docker containers." -ForegroundColor Red
        Pause
        exit $LASTEXITCODE
    }
} else {
    Write-Host "Docker not found in PATH. Checking for native PostgreSQL and Redis services..." -ForegroundColor Gray
    $pgService = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue | Where-Object {$_.Status -eq "Running"}
    $redisService = Get-Service -Name "*redis*" -ErrorAction SilentlyContinue | Where-Object {$_.Status -eq "Running"}
    if ($pgService -and $redisService) {
        Write-Host "Native PostgreSQL and Redis services are already running." -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Neither Docker nor native PostgreSQL/Redis services are running." -ForegroundColor Red
        Write-Host "Please start PostgreSQL and Redis manually, or ensure Docker is running." -ForegroundColor Red
        Pause
        exit 1
    }
}

# 3. Wait for databases to be ready
Write-Host ""
Write-Host "[3/5] Waiting for databases to initialize (20 seconds)..." -ForegroundColor Yellow
Start-Sleep -Seconds 20

# 4. Database migrations & seeding
Write-Host ""
Write-Host "[4/5] Running database migrations and seeding..." -ForegroundColor Yellow
$env:PYTHONUTF8=1
& .\venv\Scripts\python.exe backend/db_init.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Database initialization failed." -ForegroundColor Red
    Pause
    exit $LASTEXITCODE
}
& .\venv\Scripts\python.exe backend/reseed.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Reseeding database failed." -ForegroundColor Red
    Pause
    exit $LASTEXITCODE
}

# 5. Launch Backend, Frontend, and Llama.cpp Server in separate windows
Write-Host ""
Write-Host "[5/5] Launching services..." -ForegroundColor Yellow

Write-Host "Starting llama.cpp Server (Port 8080)..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit -Command & .\phi3\llama-b9075-bin-win-cuda-12.4-x64\llama-server.exe --model phi3\models\Phi-3-mini-4k-instruct-q4.gguf --port 8080 --embeddings --ctx-size 4096 -ngl 33"

Write-Host "Starting FastAPI Backend (Port 8000)..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit -Command `$env:PYTHONUTF8=1; & .\venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000 --app-dir backend --timeout-keep-alive 300"

Write-Host "Starting React Frontend (Port 5173)..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit -Command cd frontend; npm run dev"

Write-Host ""
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host " Startup completed successfully!" -ForegroundColor Cyan
Write-Host " - Backend: http://localhost:8000" -ForegroundColor Cyan
Write-Host " - Frontend: http://localhost:5173" -ForegroundColor Cyan
Write-Host " - Llama.cpp (Phi-3): http://localhost:8080" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""
