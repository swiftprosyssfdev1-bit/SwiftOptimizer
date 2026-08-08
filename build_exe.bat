@echo off
:: ============================================================
:: Swift Optimizer — EXE Builder
:: Builds TWO executables:
::   1. SwiftOptimizer.exe  — Swift Optimizer, main app for employees (login + dashboard)
::   2. SwiftAgent.exe    — Swift Agent, silent background agent
::
:: Run this ONCE on the admin/dev PC.
:: Output: dist\SwiftOptimizer.exe and dist\SwiftAgent.exe
:: ============================================================

echo.
echo  Swift Optimizer — EXE Builder
echo  ==================================

:: Check PyInstaller is installed
python -m PyInstaller --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  PyInstaller not found. Installing...
    pip install pyinstaller
    if %errorlevel% neq 0 (
        echo  ERROR: Could not install PyInstaller. Make sure Python is installed.
        pause
        exit /b 1
    )
)

echo  Checking required packages...
pip install PyQt6 mysql-connector-python bcrypt wmi pywin32 --quiet

cd /d "%~dp0"

:: ── Build 1: Main App (SwiftOptimizer.exe — Swift Optimizer) ─────
echo.
echo  Building SwiftOptimizer.exe (Swift Optimizer, main app) ...
echo  This may take 1-2 minutes...
echo.

python -m PyInstaller ^
    --onefile ^
    --noconsole ^
    --name "SwiftOptimizer" ^
    --icon "assets\icon.ico" ^
    --version-file "version_info_optimizer.txt" ^
    --add-data "assets\logo.png;assets" ^
    --add-data "assets\icon.ico;assets" ^
    --add-data ".env;." ^
    --hidden-import "mysql.connector" ^
    --hidden-import "mysql.connector.plugins" ^
    --hidden-import "mysql.connector.plugins.mysql_native_password" ^
    --hidden-import "mysql.connector.locales" ^
    --hidden-import "mysql.connector.locales.eng" ^
    --hidden-import "mysql.connector.locales.eng.client_error" ^
    --collect-submodules "mysql.connector" ^
    --hidden-import "dotenv" ^
    --hidden-import "PyQt6.QtCore" ^
    --hidden-import "PyQt6.QtGui" ^
    --hidden-import "PyQt6.QtWidgets" ^
    --hidden-import "wmi" ^
    --hidden-import "win32com" ^
    --hidden-import "win32com.client" ^
    --hidden-import "win32event" ^
    --hidden-import "win32api" ^
    --hidden-import "win32timezone" ^
    --hidden-import "pythoncom" ^
    --hidden-import "pywintypes" ^
    --exclude-module "django" ^
    --exclude-module "mysql.connector.django" ^
    --exclude-module "mysql.connector.aio" ^
    --exclude-module "jinja2" ^
    --exclude-module "PIL" ^
    --exclude-module "numpy" ^
    --exclude-module "tzdata" ^
    main.py

if %errorlevel% neq 0 (
    echo.
    echo  ERROR: SwiftOptimizer.exe build failed.
    pause
    exit /b 1
)

echo.
echo  SwiftOptimizer.exe built successfully.

:: ── Build 2: Agent Service (SwiftAgent.exe — Windows Service) ──
echo.
echo  Building SwiftAgent.exe (Windows Service) ...
echo  This may take 1-2 minutes...
echo.

python -m PyInstaller ^
    --onefile ^
    --noconsole ^
    --icon "assets\icon.ico" ^
    --version-file "agent\version_info_agent.txt" ^
    --add-data "agent\agent_config.txt;." ^
    --add-data "assets\icon.ico;assets" ^
    --name "SwiftAgent" ^
    --hidden-import "mysql.connector" ^
    --hidden-import "mysql.connector.plugins" ^
    --hidden-import "mysql.connector.plugins.mysql_native_password" ^
    --hidden-import "mysql.connector.locales" ^
    --hidden-import "mysql.connector.locales.eng" ^
    --hidden-import "mysql.connector.locales.eng.client_error" ^
    --collect-submodules "mysql.connector" ^
    --hidden-import "wmi" ^
    --hidden-import "win32com" ^
    --hidden-import "win32com.client" ^
    --hidden-import "win32event" ^
    --hidden-import "win32api" ^
    --hidden-import "win32service" ^
    --hidden-import "win32serviceutil" ^
    --hidden-import "win32timezone" ^
    --hidden-import "servicemanager" ^
    --hidden-import "winerror" ^
    --hidden-import "pythoncom" ^
    --exclude-module "django" ^
    --exclude-module "mysql.connector.django" ^
    --exclude-module "mysql.connector.aio" ^
    --exclude-module "jinja2" ^
    --exclude-module "PIL" ^
    --exclude-module "numpy" ^
    --exclude-module "tzdata" ^
    --paths "." ^
    agent\swift_agent_service.py

if %errorlevel% neq 0 (
    echo.
    echo  ERROR: SwiftAgent.exe build failed.
    pause
    exit /b 1
)

echo.
echo  SwiftAgent.exe built successfully.
echo  Runs as a normal Windows Service:
echo    Service name : SwiftAgent
echo    Display name : Swift Agent
echo  Visible in services.msc and Task Manager under that name.

:: ── Done ─────────────────────────────────────────────────────
echo.
echo  ============================================================
echo   Both EXEs built successfully!
echo.
echo   dist\SwiftOptimizer.exe     — Swift Optimizer (login + dashboard)
echo   dist\SwiftAgent.exe          — Swift Agent (service)
echo.
echo   NEXT STEPS:
echo   1. Compile setup_usb_control.iss in Inno Setup Compiler.
echo      It picks up both EXEs from dist\ and produces one
echo      installer: Output\SwiftProsys_Swift_Optimizer_Setup.exe
echo   2. Run that installer as Administrator on each employee PC.
echo      It installs Swift Optimizer + the agent service into the
echo      same folder and registers SwiftAgent as a real,
echo      visibly-named Windows Service (auto-start, crash-recovery
echo      via `sc failure`) — no hidden task, no hidden files.
echo  ============================================================
echo.
pause
