@echo off
REM One command: sets itself up the first time, then starts Stat Asker and opens it in your browser.
REM   run.bat                     quick start (play-by-play for the last 10 seasons)
REM   run.bat --history full      also index 1950 onward (about an hour, ~4 GB, runs in the background)
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.11+ is required: https://www.python.org/downloads/
  exit /b 1
)
if not exist .venv (
  echo First run: creating a private Python environment...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -c "import fastapi, uvicorn, httpx" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies ^(a few seconds, nothing is compiled^)...
  pip install --quiet --disable-pip-version-check --only-binary=:all: -r requirements.txt
)
python -m mlbasker serve --open %*
