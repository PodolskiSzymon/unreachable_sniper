@echo off
rem Start Snipera. Uzywa Pythona z .venv, a gdy tam brak bibliotek - Pythona systemowego (python z PATH).
rem Uzycie: dwuklik albo w terminalu:  .\start.bat   (argumenty przechodza dalej,
rem np. .\start.bat account_session --login  ->  python -m sniper.account_session --login)
cd /d "%~dp0"
set "PY="
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import aiosmtplib, httpx, playwright" >nul 2>&1 && set "PY=.venv\Scripts\python.exe"
)
if not defined PY (
  python -c "import aiosmtplib, httpx, playwright" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo Brak bibliotek Snipera. Zainstaluj je raz:
  echo     python -m pip install -r sniper\requirements.txt
  pause
  exit /b 1
)
echo [start.bat] Python: %PY%
if "%~1"=="" (
  "%PY%" -m sniper
) else (
  "%PY%" -m sniper.%*
)
if errorlevel 1 pause
