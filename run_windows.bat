@echo off
setlocal
cd /d "%~dp0"
python -m pip install -r requirements-windows.txt -q
start "" pythonw "%~dp0Windows\gamma_pet_windows.py"
