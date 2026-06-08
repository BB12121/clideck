param(
    [int]$Port = 7878,
    [string]$HostName = "127.0.0.1",
    [switch]$Help,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if ($Help -or ($RemainingArgs -contains "--help")) {
    Write-Host "Usage: .\CliDeckDesktop.cmd [-Port 7878] [-HostName 127.0.0.1]"
    Write-Host ""
    Write-Host "Starts CliDeck and opens it in a desktop window."
    exit 0
}

if ($RemainingArgs.Count -gt 0) {
    throw "Unknown argument(s): $($RemainingArgs -join ' ')"
}

function Find-Python {
    $commands = @(
        @{ File = "py"; Args = @("-3") },
        @{ File = "python"; Args = @() },
        @{ File = "python3"; Args = @() }
    )

    foreach ($command in $commands) {
        $resolved = Get-Command $command.File -ErrorAction SilentlyContinue
        if (-not $resolved) {
            continue
        }

        try {
            & $command.File @($command.Args + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")) | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return $command
            }
        }
        catch {
            continue
        }
    }

    throw "Python 3.10 or newer was not found. Install Python from https://www.python.org/downloads/ and run CliDeckDesktop.cmd again."
}

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    $python = Find-Python
    Write-Host "Creating local virtual environment..."
    & $python.File @($python.Args + @("-m", "venv", ".venv"))
}

Write-Host "Installing CliDeck desktop dependencies..."
& $VenvPython -m pip install -e ".[desktop]"
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}

Write-Host "Opening CliDeck desktop window..."
& $VenvPython -m agent_console.desktop --host $HostName --port $Port
