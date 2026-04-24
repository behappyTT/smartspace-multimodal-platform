Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location 'D:\smartspace-platform'; .\.venv\Scripts\python.exe run.py"
Start-Sleep -Seconds 4
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location 'D:\smartspace-platform'; .\.venv\Scripts\python.exe bluetooth_node\uploader.py"
Write-Host "后端页面请访问: http://127.0.0.1:8000/"
