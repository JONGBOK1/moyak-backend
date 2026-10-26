param(
    [string]$FrontendPath = 'E:\Moyak\moyak-front',
    [string]$DeviceId,
    [string]$Adb = 'E:\AndroidSdk\platform-tools\adb.exe',
    [string]$Flutter = 'E:\flutter\bin\flutter.bat'
)

$ErrorActionPreference = 'Stop'
# Check the intended local backend before installing an app that points to it.
$schema = Invoke-RestMethod 'http://127.0.0.1:8000/openapi.json' -TimeoutSec 10
if ($null -eq $schema.paths.PSObject.Properties['/auth/me/care-profile']) {
    throw 'Start the updated Moyak backend on port 8000 first.'
}
if (-not $DeviceId) {
    $connectedDevices = @(& $Adb devices | Where-Object { $_ -match '^\S+\s+device$' } | ForEach-Object { ($_ -split '\s+')[0] })
    if ($connectedDevices.Count -ne 1) {
        throw 'Connect one authorized phone, or pass -DeviceId explicitly.'
    }
    $DeviceId = $connectedDevices[0]
}
& $Adb -s $DeviceId reverse tcp:8000 tcp:8000
if ($LASTEXITCODE -ne 0) { throw 'USB port forwarding failed.' }
$env:PUB_CACHE = 'E:\flutter-pub-cache'
$env:GRADLE_USER_HOME = 'E:\Android\.gradle'
Push-Location -LiteralPath $FrontendPath
try {
    & $Flutter run -d $DeviceId '--dart-define=API_BASE_URL=http://127.0.0.1:8000'
    if ($LASTEXITCODE -ne 0) { throw 'Flutter run failed.' }
} finally {
    Pop-Location
}
