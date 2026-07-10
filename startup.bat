@echo off
title Policy AGENT Startup Utility
echo ===================================================
echo             Policy AGENT Startup Utility           
echo ===================================================
echo.

:: 1. Clean up ports 8000, 5173, and 8080
echo [1/5] Freeing up ports 8000, 5173, and 8080...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000') do (
    echo Killing process on port 8000 (PID: %%a)...
    taskkill /f /pid %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5173') do (
    echo Killing process on port 5173 (PID: %%a)...
    taskkill /f /pid %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8080') do (
    echo Killing process on port 8080 (PID: %%a)...
    taskkill /f /pid %%a >nul 2>&1
)

:: 2. Start Docker Compose services
echo.
echo [2/5] Starting database services...
where docker >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo Docker detected. Starting Docker containers...
    docker compose -f infra/docker-compose.yml up -d postgres neo4j redis
) else (
    echo Docker not found in PATH. Checking for native PostgreSQL and Redis services...
    sc query postgresql-x64-16 | findstr RUNNING >nul
    set PG_OK=%ERRORLEVEL%
    sc query Redis | findstr RUNNING >nul
    set REDIS_OK=%ERRORLEVEL%
    
    if %PG_OK% equ 0 (
        if %REDIS_OK% equ 0 (
            echo Native PostgreSQL and Redis services are running.
        ) else (
            echo [ERROR] Redis service is not running. Please start it.
            pause
            exit /b 1
        )
    ) else (
        echo [ERROR] PostgreSQL service is not running. Please start it.
        pause
        exit /b 1
    )
)

:: 3. Wait for databases to be ready
echo.
echo [3/5] Waiting for databases to initialize (20 seconds)...
timeout /t 20 /nobreak >nul

:: 4. Database migrations & seeding
echo.
echo [4/5] Running database migrations and seeding...
set PYTHONUTF8=1
.\venv\Scripts\python.exe backend/db_init.py
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Database initialization failed.
    pause
    exit /b %ERRORLEVEL%
)
.\venv\Scripts\python.exe backend/reseed.py
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Reseeding database failed.
    pause
    exit /b %ERRORLEVEL%
)

:: 5. Launch Backend, Frontend, and Llama.cpp Server in separate windows
echo.
echo [5/5] Launching services...

echo Starting llama.cpp Server (Port 8080)...
start "Policy AGENT Llama Server" cmd /k ".\phi3\llama-b9075-bin-win-cuda-12.4-x64\llama-server.exe --model phi3\models\Phi-3-mini-4k-instruct-q4.gguf --port 8080 --embeddings --ctx-size 4096 -ngl 33"

echo Starting FastAPI Backend (Port 8000)...
start "Policy AGENT Backend" cmd /k "set PYTHONUTF8=1 && .\venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000 --app-dir backend --timeout-keep-alive 300"

echo Starting React Frontend (Port 5173)...
start "Policy AGENT Frontend" cmd /k "cd frontend && npm run dev"

echo.
echo ===================================================
echo  Startup completed successfully!
echo  - Backend: http://localhost:8000
echo  - Frontend: http://localhost:5173
echo  - Llama.cpp (Phi-3): http://localhost:8080
echo ===================================================
echo.
pause
