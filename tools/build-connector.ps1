$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$target = Join-Path $PSScriptRoot '..\frontend\public\downloads\OfferlyConnect.exe'
New-Item -ItemType Directory -Path (Split-Path $target) -Force | Out-Null
& $compiler /nologo /target:winexe /codepage:65001 /optimize+ /platform:anycpu "/win32manifest:$PSScriptRoot\connector.manifest" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.dll /reference:System.Web.Extensions.dll "/out:$target" "$PSScriptRoot\OfferlyConnect.cs"
if ($LASTEXITCODE) { throw 'Connector build failed.' }
Get-Item -LiteralPath $target | Select-Object Name, Length
