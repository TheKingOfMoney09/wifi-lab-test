# Citeste semnalul WiFi real al acestui laptop Windows (netsh) si il trimite
# catre serverul de test din laborator.
#
# IMPORTANT: schimba adresa de mai jos daca IP-ul laptopului profesorului e altul.

$ErrorActionPreference = "SilentlyContinue"
$serverUrl = "http://192.168.6.113:8090/api/wifi-signal"

$clientId = $env:COMPUTERNAME
$output = netsh wlan show interfaces

$ssid = $null; $bssid = $null; $signal = $null; $channel = $null
$radioType = $null; $rx = $null; $tx = $null; $auth = $null
$mbpsSeen = 0

foreach ($line in $output) {
    if ($line -match ':\s*(\d+)\s*%\s*$') { $signal = [int]$matches[1]; continue }
    if ($line -match '^\s*BSSID\s*:\s*(.+)$') { $bssid = $matches[1].Trim(); continue }
    if ($line -match '^\s*SSID\s*:\s*(.+)$') { $ssid = $matches[1].Trim(); continue }
    if ($line -match '(802\.\d+\w*)') { $radioType = $matches[1]; continue }
    if ($line -match '\(Mbps\)\s*:\s*(\d+)') {
        $mbpsSeen++
        if ($mbpsSeen -eq 1) { $rx = [int]$matches[1] } else { $tx = [int]$matches[1] }
        continue
    }
    if ($line -match '(?i)(canal|channel)\s*:\s*(\d+)') { $channel = [int]$matches[2]; continue }
    if ($line -match '(?i)(autent\w*|authentication)\s*:\s*(.+)$') { $auth = $matches[2].Trim(); continue }
}

$payload = @{
    client_id       = $clientId
    ssid            = $ssid
    bssid           = $bssid
    signal_percent  = $signal
    channel         = $channel
    radio_type      = $radioType
    rx_rate_mbps    = $rx
    tx_rate_mbps    = $tx
    authentication  = $auth
} | ConvertTo-Json

Write-Host "Laptop: $clientId"
Write-Host "SSID: $ssid | Semnal: $signal% | Canal: $channel | Tip radio: $radioType"
Write-Host "Viteza receptie/transmisie (Mbps): $rx / $tx"
Write-Host ""

try {
    Invoke-RestMethod -Uri $serverUrl -Method Post -Body $payload -ContentType "application/json; charset=utf-8" | Out-Null
    Write-Host "Trimis cu succes catre $serverUrl"
} catch {
    Write-Host "EROARE la trimitere catre $serverUrl"
    Write-Host $_.Exception.Message
    Write-Host "Verifica daca esti conectat la aceeasi retea WiFi si daca adresa din script e corecta."
}

Write-Host ""
Write-Host "Apasa orice tasta pentru a inchide fereastra..."
[void][System.Console]::ReadKey($true)
