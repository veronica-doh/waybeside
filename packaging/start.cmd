@echo off
chcp 65001 >nul
cd /d "%~dp0.."
rem Windows 双击草稿：未在 Windows 实机验证，不自动安装或提权。
if not exist ".venv\Scripts\python.exe" (
  echo 请先按 README 配置 Python 3.11 和应用专用虚拟环境。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py
if errorlevel 1 pause
