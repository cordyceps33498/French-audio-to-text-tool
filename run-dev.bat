@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "FR_TRANSLATOR_ROOT=%~dp0"
set "PYTHONPATH=%~dp0src"
title fr-translator (dev)

if exist ".venv\Scripts\python.exe" goto :run

echo.
echo [fr-translator] 未找到开发虚拟环境 .venv\Scripts\python.exe
echo                 需要 Python 3.10（64 位）和本项目依赖。
echo.
echo                 将执行：
echo                   py -3.10 -m venv .venv
echo                   .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
choice /c YN /n /m "现在创建虚拟环境并安装依赖吗？[Y/N] "
if errorlevel 2 goto :nopython

py -3.10 -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [fr-translator] 未找到 Python 3.10。请先安装 Python 3.10 x64 并勾选 py launcher。
    goto :failed
)
py -3.10 -m venv .venv
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :failed

:run
".venv\Scripts\python.exe" -m fr_translator %*
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" echo [fr-translator] 退出码：%CODE%
pause
exit /b %CODE%

:nopython
echo.
echo [fr-translator] 已取消。也可手动执行：
echo     py -3.10 -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
echo     run-dev.bat
pause
exit /b 1

:failed
echo.
echo [fr-translator] 环境准备失败，请根据上面的提示排查。
pause
exit /b 1
