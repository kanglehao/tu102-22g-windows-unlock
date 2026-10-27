@echo off
chcp 65001 >nul
rem ---------------------------------------------------------------
rem  TU102 unlock manager launcher
rem
rem  IMPORTANT: this file MUST stay pure ASCII. cmd.exe parses batch
rem  files byte-wise under the current OEM codepage (936 here); UTF-8
rem  CJK bytes make it split the command itself, not just mangle the
rem  text - you get '?echo' is not recognized errors that put in *not*
rem  fixable by chcp inside the same file. All Chinese lives in the
rem  Python script instead.
rem
rem  Copy this file to your Desktop and point SCRIPT at this repo.
rem ---------------------------------------------------------------
setlocal
title TU102 Unlock Manager

set "PY=C:\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
set "SCRIPT=%~dp0win\tu102_boot.py"

if not exist "%SCRIPT%" (
  echo [launcher] cannot find "%SCRIPT%"
  echo [launcher] edit SCRIPT inside this .cmd to point at your checkout.
  pause >nul
  exit /b 1
)

"%PY%" "%SCRIPT%" %*
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
  echo.
  echo [launcher] script exited with code %RC%
  pause >nul
)
endlocal
