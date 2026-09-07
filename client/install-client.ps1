# Install the codemem client on Windows. Run in PowerShell (5.1 or 7). Needs python on PATH.
# All the work is in install_client.py so the same logic runs on every OS.
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Error "python not found on PATH; install Python 3 and re-run"; exit 1 }
& python "$PSScriptRoot\install_client.py"
