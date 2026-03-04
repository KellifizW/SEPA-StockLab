@echo off
echo Killing Flask app on port 5000...
for /f "tokens=5" %%a in ('netstat -aon ^| find ":5000" ^| find "LISTENING"') do (
    echo Found PID: %%a
    taskkill /PID %%a /F
)
for /f "tokens=1" %%a in ('wmic process where "name='python.exe' and commandline like '%%app.py%%'" get processid ^| findstr /r "[0-9]"') do (
    echo Killing python PID: %%a
    taskkill /PID %%a /F 2>nul
)
for /f "tokens=1" %%a in ('wmic process where "name='python.exe' and commandline like '%%start_web%%'" get processid ^| findstr /r "[0-9]"') do (
    echo Killing python PID: %%a
    taskkill /PID %%a /F 2>nul
)
echo Done.
timeout /t 2
