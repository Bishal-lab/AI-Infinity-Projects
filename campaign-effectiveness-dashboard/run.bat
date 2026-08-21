@echo off
REM One-command start on Windows. Creates a virtual environment on first run.
setlocal
cd /d "%~dp0"

if not exist ".venv" (
  echo Creating virtual environment...
  python -m venv .venv
  if exist "wheelhouse" (
    .venv\Scripts\pip install --no-index --find-links=wheelhouse -r requirements.txt
  ) else (
    .venv\Scripts\pip install -r requirements.txt
  )
)

.venv\Scripts\streamlit run app\main.py %*
