param([int]$Port = 9189, [string]$PhoneHost = '', [int]$PhonePort = 9190, [string]$PhoneCertificate = '', [string]$PhoneKey = '', [switch]$PublicPhone, [switch]$LocalPhone, [string]$TunnelExecutable = '')
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:ANONYMIZED_TELEMETRY = 'false'
$env:BROWSER_USE_CLOUD_SYNC = 'false'
$env:BROWSER_USE_VERSION_CHECK = 'false'
$env:BROWSER_USE_CONFIG_DIR = Join-Path $PSScriptRoot 'data\browser-use'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = $PSScriptRoot
$bundledTunnel = Join-Path $PSScriptRoot 'tools\cloudflared.exe'
if (-not $LocalPhone -and (Test-Path -LiteralPath $bundledTunnel)) { $PublicPhone = $true }
if ($LocalPhone -and $PublicPhone) { throw 'Choose either -LocalPhone or -PublicPhone.' }
$env:TEMP = Join-Path $PSScriptRoot '.cache\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$pythonRuntime = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonRuntime)) { throw 'Run uv sync --frozen in this folder first.' }
foreach ($listenPort in @($Port, $PhonePort)) {
    if (Get-NetTCPConnection -LocalPort $listenPort -State Listen -ErrorAction SilentlyContinue) { throw "Port $listenPort is already in use; choose other ports." }
}
if (-not $PhoneHost) {
    $networkRoute = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1
    if ($networkRoute) {
        $PhoneHost = (Get-NetIPAddress -InterfaceIndex $networkRoute.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1).IPAddress
    }
}
if (-not $PhoneHost) {
    $PhoneHost = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254.*' -and $_.InterfaceAlias -notmatch 'VMware|vEthernet|VirtualBox|Loopback' } | Select-Object -First 1).IPAddress
}
$serverArgs = @('-B', '-m', 'frontend.serve', '--port', $Port)
if ($PhoneHost) { $serverArgs += @('--phone-host', $PhoneHost, '--phone-port', $PhonePort) }
if ($PublicPhone) {
    if ($PhoneCertificate -or $PhoneKey) { throw 'Public phone and local TLS cannot be used together.' }
    if (-not $PhoneHost) { throw 'A physical LAN address is required.' }
    if (-not $TunnelExecutable) {
        $TunnelExecutable = if ($env:OFFERLY_CLOUDFLARED) { $env:OFFERLY_CLOUDFLARED } elseif (Test-Path -LiteralPath $bundledTunnel) { $bundledTunnel } else { (Get-Command cloudflared -ErrorAction Stop).Source }
    }
    $serverArgs += @('--phone-tunnel', (Get-Item -LiteralPath $TunnelExecutable -ErrorAction Stop).FullName)
}
if ($PhoneCertificate -or $PhoneKey) {
    if (-not $PhoneCertificate -or -not $PhoneKey) { throw 'Provide both -PhoneCertificate and -PhoneKey.' }
    $serverArgs += @('--phone-cert', $PhoneCertificate, '--phone-key', $PhoneKey)
}
Push-Location $PSScriptRoot
try { & $pythonRuntime @serverArgs; if ($LASTEXITCODE) { throw "Offerly exited with code $LASTEXITCODE" } }
finally { Pop-Location }
