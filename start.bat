@echo off
rem Start Snipera z jego wlasnym srodowiskiem .venv (bez recznego activate).
rem Uzycie: dwuklik albo w terminalu:  .\start.bat   (dodatkowe argumenty przechodza dalej,
rem np. .\start.bat account_session --login  ->  python -m sniper.account_session --login)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Brak .venv - najpierw: python -m venv .venv  i  .venv\Scripts\python.exe -m pip install -r sniper\requirements.txt
  pause
  exit /b 1
)
if "%~1"=="" (
  ".venv\Scripts\python.exe" -m sniper
) else (
  ".venv\Scripts\python.exe" -m sniper.%*
)
if errorlevel 1 pause
