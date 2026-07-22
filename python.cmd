@echo off
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
"%~dp0.tools\python-3.11.9-embed-amd64\python.exe" %*
