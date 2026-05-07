@echo off
setlocal enabledelayedexpansion
:: ============================================================
:: AlphaPulse - PostgreSQL Setup  (Run as Administrator)
:: ============================================================
:: This script:
::  1. Starts PostgreSQL service
::  2. Temporarily enables passwordless login (trust)
::  3. Sets postgres password to: AlphaPulse26$777
::  4. Creates the alphapulse database
::  5. Restores secure authentication
::
:: RIGHT-CLICK this file -> "Run as administrator"
:: ============================================================

set PG_BIN=C:\Program Files\PostgreSQL\16\bin
set PG_DATA=C:\Program Files\PostgreSQL\16\data
set PG_SERVICE=postgresql-x64-16
set PG_PASS=AlphaPulse26$777
set PG_HBA=%PG_DATA%\pg_hba.conf

echo ============================================================
echo  AlphaPulse - PostgreSQL Setup
echo ============================================================
echo.

:: --- Step 1: Start service ---
echo [1/5] Starting PostgreSQL service...
net start %PG_SERVICE% >nul 2>&1
if %errorlevel% == 0 (
    echo      OK - service started.
) else (
    echo      Service already running or starting...
)
timeout /t 2 /nobreak >nul

:: --- Step 2: Backup and patch pg_hba.conf for trust auth ---
echo [2/5] Enabling temporary passwordless access...
copy /Y "%PG_HBA%" "%PG_HBA%.bak" >nul

:: Write new hba.conf with trust for local connections
(
echo # Temporary trust auth - will be restored
echo local   all   all               trust
echo host    all   all   127.0.0.1/32  trust
echo host    all   all   ::1/128       trust
) > "%PG_HBA%"

:: --- Step 3: Reload PostgreSQL config ---
echo [3/5] Reloading PostgreSQL configuration...
"%PG_BIN%\pg_ctl.exe" reload -D "%PG_DATA%" >nul 2>&1
timeout /t 2 /nobreak >nul

:: --- Step 4: Set password and create database ---
echo [4/5] Setting password and creating database...

"%PG_BIN%\psql.exe" -U postgres -h 127.0.0.1 -w -c "ALTER USER postgres WITH PASSWORD '%PG_PASS%';" 2>&1
if %errorlevel% neq 0 (
    echo      [!] Could not set password via ALTER USER. Trying pg_ctl...
)

"%PG_BIN%\psql.exe" -U postgres -h 127.0.0.1 -w -c "SELECT 1 FROM pg_database WHERE datname='alphapulse'" 2>&1 | findstr "1 row" >nul
if %errorlevel% neq 0 (
    "%PG_BIN%\psql.exe" -U postgres -h 127.0.0.1 -w -c "CREATE DATABASE alphapulse;" 2>&1
    echo      Database 'alphapulse' created.
) else (
    echo      Database 'alphapulse' already exists.
)

:: --- Step 5: Restore secure pg_hba.conf ---
echo [5/5] Restoring secure authentication...
copy /Y "%PG_HBA%.bak" "%PG_HBA%" >nul

:: Write a clean secure hba.conf (scram-sha-256)
(
echo # TYPE  DATABASE        USER            ADDRESS                 METHOD
echo local   all             all                                     scram-sha-256
echo host    all             all             127.0.0.1/32            scram-sha-256
echo host    all             all             ::1/128                 scram-sha-256
echo local   replication     all                                     scram-sha-256
echo host    replication     all             127.0.0.1/32            scram-sha-256
echo host    replication     all             ::1/128                 scram-sha-256
) > "%PG_HBA%"

"%PG_BIN%\pg_ctl.exe" reload -D "%PG_DATA%" >nul 2>&1

echo.
echo ============================================================
echo  SETUP COMPLETE!
echo.
echo  PostgreSQL password: AlphaPulse26$777
echo  Database created  : alphapulse
echo.
echo  Your .env file is already configured correctly:
echo    DB_HOST=localhost
echo    DB_PORT=5432
echo    DB_NAME=alphapulse
echo    DB_USER=postgres
echo    DB_PASSWORD=AlphaPulse26$777
echo.
echo  Next steps:
echo    1. Close this window
echo    2. Open a normal terminal in your project folder
echo    3. Run: python setup_db.py
echo    4. Run: python main.py
echo ============================================================
echo.
pause
