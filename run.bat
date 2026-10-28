@echo off
chcp 65001 >nul
setlocal

rem 优先用 DSH 自带的 Python（已装好 ddddocr / requests / lxml）
set "PY=C:\Users\Weeee\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~dp0kb_update.py" --push %*
set ERR=%ERRORLEVEL%

echo.
if %ERR%==0 (echo [OK] 课表已同步) else (echo [FAIL] 退出码 %ERR%)
exit /b %ERR%
