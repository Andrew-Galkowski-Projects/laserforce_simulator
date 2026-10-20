# Close the Chrome that this Claude session's chrome-devtools MCP server launched.
#
# The MCP keeps its browser open until the session ends, and its tools cannot
# close it (close_page refuses the last tab, chrome:// URLs are blocked). This
# stops only the automation Chrome (--remote-debugging-pipe, not a renderer)
# whose parent chain reaches the same claude.exe as this shell, so the owner's
# own Chrome and other sessions' browsers are untouched. The MCP launches a
# fresh browser on its next tool call.
#
# Run: powershell -NoProfile -File scripts/close_mcp_browser.ps1

$procs = @{}
Get-CimInstance Win32_Process | ForEach-Object { $procs[[int]$_.ProcessId] = $_ }

function Get-Ancestors([int]$id) {
    $ids = @()
    $seen = @{}
    while ($procs.ContainsKey($id) -and -not $seen.ContainsKey($id)) {
        $seen[$id] = $true
        $ids += $id
        $id = [int]$procs[$id].ParentProcessId
    }
    $ids
}

$session = Get-Ancestors $PID | Where-Object { $procs[$_].Name -eq 'claude.exe' } | Select-Object -First 1
if (-not $session) {
    Write-Output "No claude.exe ancestor found; nothing closed."
    exit 1
}

$browsers = $procs.Values | Where-Object {
    $_.Name -eq 'chrome.exe' -and
    $_.CommandLine -match '--remote-debugging-pipe' -and
    $_.CommandLine -notmatch '--type=' -and
    (Get-Ancestors ([int]$_.ProcessId)) -contains $session
}

if (-not $browsers) {
    Write-Output "No MCP browser open for this session."
    exit 0
}
foreach ($b in $browsers) {
    Stop-Process -Id $b.ProcessId -Force
    Write-Output "Closed MCP browser (pid $($b.ProcessId))."
}
