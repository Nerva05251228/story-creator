$ErrorActionPreference = 'Stop'

function Set-DefaultEnv {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $current = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($current)) {
        [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
    }
}

function Set-EnvIfMissing {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    Set-DefaultEnv -Name $Name -Value $Value
}

function ConvertFrom-EnvValue {
    param([Parameter(Mandatory = $true)][string]$Value)

    $trimmed = $Value.Trim()
    if ($trimmed.Length -ge 2) {
        $first = $trimmed.Substring(0, 1)
        $last = $trimmed.Substring($trimmed.Length - 1, 1)
        if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
            return $trimmed.Substring(1, $trimmed.Length - 2)
        }
    }
    return $trimmed
}

function Load-ProjectEnv {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $envPath = Join-Path $ProjectRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        return $false
    }

    foreach ($rawLine in Get-Content -LiteralPath $envPath -Encoding UTF8) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) {
            continue
        }
        if ($line.StartsWith('export ')) {
            $line = $line.Substring(7).Trim()
        }
        $separatorIndex = $line.IndexOf('=')
        if ($separatorIndex -lt 1) {
            continue
        }

        $name = $line.Substring(0, $separatorIndex).Trim()
        $value = ConvertFrom-EnvValue -Value $line.Substring($separatorIndex + 1)
        Set-EnvIfMissing -Name $name -Value $value
    }
    return $true
}

function Require-Env {
    param([Parameter(Mandatory = $true)][string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Name is required. Copy .env.example to .env and fill in local values."
    }
}

function Mask-DatabaseUrl {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return '[missing]'
    }
    return ($Value -replace '://([^:\s/@]+):([^@\s]+)@', '://$1:***@')
}

function Get-SafeEnvValue {
    param([Parameter(Mandatory = $true)][string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ($Name -eq 'DATABASE_URL') {
        return (Mask-DatabaseUrl -Value $value)
    }
    if ($Name -match '(TOKEN|API_KEY|SECRET|PASSWORD)') {
        if ([string]::IsNullOrWhiteSpace($value)) {
            return '[missing]'
        }
        return '[set]'
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        return '[missing]'
    }
    return $value
}

function Write-SafeEnvSummary {
    param([Parameter(Mandatory = $true)][string[]]$Keys)

    foreach ($key in $Keys) {
        Write-Host "$key=$(Get-SafeEnvValue -Name $key)"
    }
}
