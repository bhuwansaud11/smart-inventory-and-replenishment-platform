$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) { python -m venv .venv }
& ./.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed. Check your internet connection.' }
Push-Location frontend
try {
    npm.cmd install
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
if (-not $env:SECRET_KEY) {
    $env:SECRET_KEY = & ./.venv/Scripts/python.exe -c 'import secrets; print(secrets.token_hex(32))'
}
if (-not (Test-Path 'backend/inventory.db') -and -not $env:DEMO_PASSWORD) {
    $secure = Read-Host 'Choose a demo password (at least 12 characters)' -AsSecureString
    $env:DEMO_PASSWORD = [System.Net.NetworkCredential]::new('', $secure).Password
}
Write-Host 'Open http://localhost:5000 after the server starts. Press Ctrl+C to stop.'
& ./.venv/Scripts/python.exe backend/app.py
