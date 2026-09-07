# Install the codemem client on Windows (native Claude Code). Run in PowerShell.
# Needs python on PATH and the claude CLI.
$Url  = if ($env:CODEMEM_URL) { $env:CODEMEM_URL } else { "http://localhost:8055" }
$Dest = Join-Path $env:USERPROFILE ".codemem"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Copy-Item "$PSScriptRoot\codemem_hook.py","$PSScriptRoot\codemem_agent.py" $Dest -Force
Write-Host "client scripts -> $Dest"
$cmds = Join-Path $env:USERPROFILE ".claude\commands"
New-Item -ItemType Directory -Force -Path $cmds | Out-Null
Copy-Item "$PSScriptRoot\commands\codemem.md" $cmds -Force
Write-Host "slash command -> $cmds\codemem.md"

if (Get-Command claude -ErrorAction SilentlyContinue) {
    claude mcp remove -s user codemem 2>$null | Out-Null
    claude mcp add --transport http --scope user codemem "$Url/mcp"
    Write-Host "registered MCP server codemem -> $Url/mcp"
} else { Write-Warning "claude CLI not found; run: claude mcp add --transport http --scope user codemem $Url/mcp" }

$settings = Join-Path $env:USERPROFILE ".claude\settings.json"
$s = if (Test-Path $settings) { Get-Content $settings -Raw | ConvertFrom-Json -AsHashtable } else { @{} }
if (-not $s.hooks) { $s.hooks = @{} }
$hook = "$Dest\codemem_hook.py".Replace("\", "/")
$cmd  = "set CODEMEM_URL=$Url&& python `"$hook`""
foreach ($ev in "SessionStart","SessionEnd") {
    $list = @($s.hooks[$ev] | Where-Object { ($_ | ConvertTo-Json -Depth 5) -notmatch "codemem_hook" })
    $list += @{ matcher = ""; hooks = @(@{ type = "command"; command = $cmd; timeout = 10 }) }
    $s.hooks[$ev] = $list
}
New-Item -ItemType Directory -Force -Path (Split-Path $settings) | Out-Null
$s | ConvertTo-Json -Depth 10 | Set-Content $settings -Encoding UTF8
Write-Host "hooks written to $settings"
Write-Host "Test: Invoke-RestMethod $Url/health"
