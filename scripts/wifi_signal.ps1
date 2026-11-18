# Citeste semnalul WiFi real al acestui laptop (netsh) si il trimite serverului
# de test din laborator, ca sa apara in panoul profesorului.
#
# Varianta recomandata: dublu-click pe run_wifi_test.bat (sau pe fisierul .bat
# descarcat de pe pagina serverului). Manual, din PowerShell:
#   $env:WIFI_TEST_SERVER = "http://<IP-profesor>:8090/api/wifi-signal"
#   powershell -ExecutionPolicy Bypass -File .\wifi_signal.ps1
# Doar citire, fara trimitere (util pentru diagnostic):
#   $env:WIFI_TEST_DRYRUN = "1"
#
# ATENTIE: fisierul se pastreaza ASCII. Windows PowerShell 5.1 citeste fisierele
# fara BOM cu codepage-ul local, deci diacriticele ar iesi stricate la elev.
$ErrorActionPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Serverul de test scrie in linia urmatoare adresa lui reala in momentul
# descarcarii, deci arhiva ramane valida si daca IP-ul profesorului se schimba.
$serverUrl = "__SERVER_URL__"
if ($env:WIFI_TEST_SERVER) { $serverUrl = $env:WIFI_TEST_SERVER }
$dryRun = [bool]$env:WIFI_TEST_DRYRUN

if ($serverUrl -notmatch '^https?://[^/]+/') {
    Write-Host "Scriptul nu stie adresa serverului de test." -ForegroundColor Yellow
    Write-Host "Descarca-l de pe pagina serverului (butonul 'Descarca testul de semnal WiFi')"
    Write-Host "sau porneste-l asa:"
    Write-Host '  $env:WIFI_TEST_SERVER = "http://IP-PROFESOR:8090/api/wifi-signal"'
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\wifi_signal.ps1"
    Write-Host ""
    Write-Host "Apasa orice tasta pentru a inchide fereastra..."
    if (-not [Console]::IsInputRedirected) { [void][System.Console]::ReadKey($true) }
    exit 1
}

# netsh scrie numerele cu virgula sau cu punct, dupa limba Windows-ului.
function ConvertTo-Number($value) {
    if ("$value" -match '-?\d+(?:[.,]\d+)?') {
        return [double]($matches[0] -replace ',', '.')
    }
    return $null
}

$info = [ordered]@{
    client_id      = $env:COMPUTERNAME
    ssid           = $null
    bssid          = $null
    state          = $null
    band           = $null
    channel        = $null
    radio_type     = $null
    authentication = $null
    signal_percent = $null
    rssi_dbm       = $null
    rx_rate_mbps   = $null
    tx_rate_mbps   = $null
}

$lines = netsh wlan show interfaces
if (-not ($lines -match 'SSID')) {
    Write-Host "Nu am gasit o placa WiFi conectata pe acest laptop." -ForegroundColor Yellow
    Write-Host "Verifica: laptopul e conectat la reteaua WiFi a laboratorului?"
    Write-Host ""
    Write-Host "Iesirea bruta a lui netsh (pentru profesor):"
    netsh wlan show interfaces
    Write-Host ""
    Write-Host "Apasa orice tasta pentru a inchide fereastra..."
    if (-not [Console]::IsInputRedirected) { [void][System.Console]::ReadKey($true) }
    exit 2
}

foreach ($line in $lines) {
    # "Cheie : valoare" - prima linie care contine doua puncte
    if ($line -notmatch '^\s*([^:]+?)\s*:\s*(.*?)\s*$') { continue }
    $key = $matches[1]
    $value = $matches[2]
    switch -Regex ($key) {
        '^SSID$'                 { $info.ssid = $value; break }
        '^(AP BSSID|BSSID)$'     { $info.bssid = $value; break }
        '^(State|Stare)$'        { $info.state = $value; break }
        '^(Band|Banda)$'         { $info.band = $value; break }
        '^(Channel|Canal)$'      { $info.channel = [int](ConvertTo-Number $value); break }
        '^(Radio type|Tip radio)$'          { $info.radio_type = $value; break }
        '^(Authentication|Autentificare)$'  { $info.authentication = $value; break }
        '^(Signal|Semnal)$'      { $info.signal_percent = [int](ConvertTo-Number $value); break }
        '^(Rssi)$'               { $info.rssi_dbm = [int](ConvertTo-Number $value); break }
        '^(Receive rate|Viteza de receptie|Rata de receptie)'   { $info.rx_rate_mbps = ConvertTo-Number $value; break }
        '^(Transmit rate|Viteza de transmisie|Rata de transmisie)' { $info.tx_rate_mbps = ConvertTo-Number $value; break }
        '\(Mbps\)' {
            # fallback: orice linie cu (Mbps), in ordine receptie / transmisie
            if ($null -eq $info.rx_rate_mbps) { $info.rx_rate_mbps = ConvertTo-Number $value }
            else { $info.tx_rate_mbps = ConvertTo-Number $value }
            break
        }
    }
}

Write-Host "Laptop:        $($info.client_id)"
Write-Host "SSID:          $($info.ssid)  (stare: $($info.state))"
Write-Host "Semnal:        $($info.signal_percent)%   RSSI: $($info.rssi_dbm) dBm"
Write-Host "Canal / banda: $($info.channel) / $($info.band)   radio: $($info.radio_type)"
Write-Host "Viteza:        $($info.rx_rate_mbps) / $($info.tx_rate_mbps) Mbps (receptie/transmisie)"
Write-Host "BSSID:         $($info.bssid)"
Write-Host ""

if ($dryRun) {
    Write-Host "Mod diagnostic: nu trimit nimic catre server."
    Write-Host ($info | ConvertTo-Json)
} else {
    # Trimitem octeti UTF-8 explicit: PowerShell 5.1 nu codeaza corect un string
    # trimis ca -Body, iar serverul primeste JSON invalid si ignora cererea.
    $body = [System.Text.Encoding]::UTF8.GetBytes(($info | ConvertTo-Json))
    $sent = $false
    for ($attempt = 1; $attempt -le 3 -and -not $sent; $attempt++) {
        try {
            Invoke-RestMethod -Uri $serverUrl -Method Post -Body $body `
                -ContentType "application/json; charset=utf-8" -TimeoutSec 10 -ErrorAction Stop | Out-Null
            $sent = $true
        } catch {
            Write-Host "Incercarea $attempt a esuat: $($_.Exception.Message)" -ForegroundColor Yellow
            Start-Sleep -Seconds 2
        }
    }
    if ($sent) {
        Write-Host "Trimis cu succes catre $serverUrl" -ForegroundColor Green
    } else {
        Write-Host "EROARE: nu am putut trimite datele catre $serverUrl" -ForegroundColor Red
        Write-Host "Verifica daca esti in aceeasi retea cu laptopul profesorului si daca"
        Write-Host "serverul de test ruleaza (adresa apare in panoul profesorului)."
    }
}

Write-Host ""
Write-Host "Poti inchide aceasta fereastra."
if (-not [Console]::IsInputRedirected) { [void][System.Console]::ReadKey($true) }
