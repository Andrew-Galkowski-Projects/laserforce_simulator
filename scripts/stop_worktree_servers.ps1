# Stop every process running out of one git worktree (dev servers and their children).
#
# On Windows, stopping a background `ng serve` (or any npm script) kills only the npm
# wrapper: its node, esbuild and dart-sass children keep running, hold files open, and make
# `git worktree remove` fail with "Invalid argument" / "Access denied". This stops every
# process whose executable path or command line lies under the given worktree, except this
# shell and its ancestors. Processes from other worktrees and the main checkout are untouched.
#
# Run (through the PowerShell tool; the worktree guard refuses `powershell -File` in Bash):
#   powershell -NoProfile -File scripts/stop_worktree_servers.ps1                    # this worktree
#   powershell -NoProfile -File scripts/stop_worktree_servers.ps1 -Worktree <path>   # another one
# -Worktree defaults to the current directory, so a call from inside a worktree needs no path.

param(
    [string]$Worktree = (Get-Location).Path
)

$root = (Resolve-Path -LiteralPath $Worktree -ErrorAction Stop).Path.TrimEnd('\') + '\'
if ($root -notmatch '\\\.claude\\worktrees\\[^\\]+\\$') {
    Write-Output "Not a .claude\worktrees\<name> folder: $root; nothing stopped."
    exit 1
}

$procs = @{}
Get-CimInstance Win32_Process | ForEach-Object { $procs[[int]$_.ProcessId] = $_ }

$self = @{}
$id = $PID
while ($procs.ContainsKey($id) -and -not $self.ContainsKey($id)) {
    $self[$id] = $true
    $id = [int]$procs[$id].ParentProcessId
}

# A command line may spell the worktree with forward slashes (`<worktree>/laserforce_simulator/
# manage.py runserver`, as frontend/CLAUDE.md starts Django), so compare it with `/` as `\`.
$targets = $procs.Values | Where-Object {
    -not $self.ContainsKey([int]$_.ProcessId) -and (
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($root, 'OrdinalIgnoreCase')) -or
        ($_.CommandLine -and $_.CommandLine.Replace('/', '\').IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0)
    )
}

if (-not $targets) {
    Write-Output "No processes running from $root"
    exit 0
}
foreach ($t in $targets) {
    # Stopping a parent often takes its children with it; skip the ones already gone.
    if (-not (Get-Process -Id $t.ProcessId -ErrorAction SilentlyContinue)) { continue }
    try {
        Stop-Process -Id $t.ProcessId -Force -ErrorAction Stop
        Write-Output "Stopped $($t.Name) (pid $($t.ProcessId))."
    } catch {
        Write-Output "Could not stop $($t.Name) (pid $($t.ProcessId)): $($_.Exception.Message)"
    }
}
