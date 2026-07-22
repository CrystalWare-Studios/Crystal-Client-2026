$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ChatboxDir = Join-Path $RootDir "Crystal-Chatbox-Source-Code\Crystal Chatbox"
$ChatboxVenv = Join-Path $RootDir ".venv"

function Resolve-Python {
    $candidates = @(
        @{ Command = "py"; Args = @("-3.12") },
        @{ Command = "py"; Args = @("-3.11") },
        @{ Command = "python"; Args = @() },
        @{ Command = "python3"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        $cmd = Get-Command $candidate.Command -ErrorAction SilentlyContinue
        if (-not $cmd) {
            continue
        }

        try {
            $version = & $candidate.Command @($candidate.Args + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)")) 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        } catch {
            continue
        }
    }

    throw "Python 3.11 or newer was not found. Install Python from https://www.python.org/downloads/windows/ and enable 'Add python.exe to PATH'."
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)] $Python,
        [Parameter(Mandatory = $true)] [string[]] $Arguments
    )
    & $Python.Command @($Python.Args + $Arguments)
}

if (-not (Test-Path $ChatboxDir)) {
    throw "Chatbox folder not found: $ChatboxDir"
}

$Python = Resolve-Python

if (-not (Test-Path $ChatboxVenv)) {
    Write-Host "Creating virtual environment..."
    Invoke-Python $Python @("-m", "venv", $ChatboxVenv)
}

$ChatboxPython = Join-Path $ChatboxVenv "Scripts\python.exe"
& $ChatboxPython -m pip install --upgrade pip
& $ChatboxPython -m pip install -r (Join-Path $ChatboxDir "requirements.txt")

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host ""
$DisplayPort = if ($env:PORT) { $env:PORT } else { "5000" }
Write-Host "Starting Crystal Client on http://127.0.0.1:$DisplayPort"
Write-Host ""

Set-Location $ChatboxDir
& $ChatboxPython "main.py" @args
