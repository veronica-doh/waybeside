@echo off
chcp 65001 >nul
cd /d "%~dp0.."
rem 用户在Windows手动运行；不提权、不自启动、不静默安装Python。
py -3.11 --version
if errorlevel 1 (
  echo 请从Python官方网站安装Python 3.11，包含py启动器，然后重试。
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3.11 -m venv .venv
  if errorlevel 1 (
    echo 创建应用虚拟环境失败。
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo 安装依赖失败，请保留此窗口的错误；未启动应用。
  pause
  exit /b 1
)
echo 依赖已安装。请双击packaging中的start.cmd打开应用。
pause
