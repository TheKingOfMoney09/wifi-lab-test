# Diagnostic — test retea WiFi si acces Moodle

Ghid pentru cazul in care ceva nu functioneaza in laborator. E scris in ordinea in
care apar problemele in practica: intai reteaua, apoi paginile, apoi Moodle.

Inainte de orice: `python server.py` afiseaza la pornire adresele reale si daca
Moodle raspunde de pe laptopul profesorului. Daca acolo apare un `ATENTIE`, rezolva
mai intai asta.

---

## 0. Verificare rapida, fara 30 de laptopuri

```bash
python selftest.py                # 30 de elevi simulati + un Moodle fals
```

Testul porneste serverul si un Moodle fals pe porturi libere, simuleaza 30 de
elevi (ping, 2 MB, incarcare Moodle, rezultate, semnal WiFi) si verifica
agregarea, exportul CSV si scripturile descarcabile. Daca trece tot, problema e
in retea sau in Moodle, nu in cod.

---

## 1. Elevii nu deschid pagina

| Verificare | Comanda |
|---|---|
| IP-ul laptopului profesorului | `ip addr` (Linux) / `ipconfig` (Windows) |
| Serverul asculta pe toate interfetele | `ss -lntp \| grep 8090` (Linux) |
| Portul e deschis in firewall | `sudo ufw allow 8090/tcp` |
| Elevul ajunge la server | `Test-NetConnection 192.168.6.113 -Port 8090` (PowerShell) |

Pe Windows firewall-ul se deschide asa (PowerShell, ca Administrator):

```powershell
New-NetFirewallRule -DisplayName "Test WiFi lab" -Direction Inbound -Protocol TCP `
  -LocalPort 8090 -Action Allow -Profile Private
```

Cauze frecvente:

- **Laptopul profesorului e pe alt subnet sau pe hotspot.** Elevii si profesorul
  trebuie sa fie in aceeasi retea WiFi. Verifica primele trei numere din IP.
- **AP isolation / client isolation** pe routerul scolii: laptopurile se vad intre
  ele, dar nu pot vorbi cu profesorul. Se dezactiveaza din setarile routerului;
  daca nu ai acces la el, foloseste un router propriu in laborator.
- **IP-ul se schimba** (DHCP). Pune o rezervare DHCP sau IP static pentru laptopul
  profesorului. Scriptul de semnal WiFi isi ia adresa din pagina de descarcare,
  deci dupa schimbarea IP-ului trebuie **redescarcat** (nu editat de mana).
- **Portul e ocupat** de o instanta veche: serverul spune acum explicit
  `Nu pot porni serverul pe portul 8090`.

---

## 2. Elevii au pagina deschisa, dar „Laptopuri finalizate" ramane 0

- **Fila e in fundal.** Browserele limiteaza timer-ele din filele ascunse la un
  poll pe minut, deci testul porneste cu intarziere. Cere elevilor sa tina fila
  in prim-plan (pagina scrie si ea asta).
- **Apasa din nou „Porneste testul"** dupa ce toti au pagina deschisa: fiecare
  apasare creeaza o sesiune noua si doar cei care au primit deja sesiunea ruleaza.
- **JavaScript blocat** (extensii, „Do not track", antivirus). Deschide consola
  browserului (F12) la un elev: orice eroare rosie inseamna asta.
- **Ceasul calculatorului elevului** nu conteaza (masuram durate, nu ore), dar un
  browser foarte vechi (Internet Explorer) nu poate rula pagina.

---

## 3. Coloana „Semnal (%)" e goala

Semnalul WiFi nu poate fi citit din browser — vine din scriptul `.bat` care
ruleaza pe fiecare laptop. Verifica, in ordine:

1. **Elevul a rulat scriptul?** Fara el, serverul nu are cum sa stie semnalul.
2. **A rulat din arhiva, fara sa o dezarhiveze?** Butonul nou descarca un singur
   fisier `.bat`, care nu mai trebuie dezarhivat.
3. **Scriptul a ajuns la server?** In fereastra lui trebuie sa scrie
   `Trimis cu succes catre http://.../api/wifi-signal`. Daca scrie `EROARE`,
   laptopul nu e in aceeasi retea sau serverul nu ruleaza.
4. **Laptopul e pe cablu, nu pe WiFi.** `netsh wlan show interfaces` raspunde
   „nu am gasit o placa WiFi conectata" — scriptul spune si el asta.
5. **Windows a blocat fisierul.** „Windows protected your PC" → *More info* →
   *Run anyway*.

Scriptul se poate rula si manual, pentru diagnostic, fara sa trimita nimic:

```powershell
$env:WIFI_TEST_DRYRUN = "1"
powershell -ExecutionPolicy Bypass -File .\wifi_signal.ps1
```

Afiseaza exact ce a citit din `netsh` (SSID, semnal, RSSI, canal, banda, rate).

> Versiunea veche a scriptului nu gasea BSSID pe Windows 11 (acolo linia se
> numeste `AP BSSID`) si nu intelegea ieșirea in romana (`Semnal`, `Canal`,
> `Autentificare`, `Viteza de receptie (Mbps)`). Acum le citeste pe toate, plus
> `RSSI` si banda.

---

## 4. „Timp incarcare Moodle" e timeout la toti

Aproape intotdeauna e adresa din `config.json`, nu Moodle.

1. **`moodle_url` trebuie sa fie adresa la care ajung ELEVII**, nu `localhost`.
   Panoul arata un avertisment clar daca ai pus `localhost` sau `127.0.0.1`.
2. **Moodle asculta doar pe loopback.** Daca `curl http://IP:8083` merge de pe
   laptopul profesorului dar nu de pe alt calculator, serviciul e legat pe
   `127.0.0.1`. Pentru Apache: `Listen 0.0.0.0:8083`; pentru PHP built-in:
   `php -S 0.0.0.0:8083`.
3. **Portul 8083 e blocat in firewall** (vezi regula de la punctul 1, schimba
   portul).
4. **Test rapid de pe un telefon** conectat la acelasi WiFi: deschide
   `http://IP:8083` in browser. Daca nu se incarca, problema nu e in test.

Daca Moodle raspunde de pe telefon dar testul da timeout, verifica daca pagina
Moodle redirectioneaza catre alt host (de exemplu `localhost` in configurarea
Moodle — `wwwroot`) sau catre HTTPS cu certificat invalid.

---

## 5. Moodle raspunde, dar se incarca foarte greu cand intra 30 de elevi

Aici e chiar obiectul testului, deci merita urmarit in paralel pe serverul Moodle:

| Simptom in test | Cauza uzuala in Moodle |
|---|---|
| toate laptopurile au ~acelasi timp mare | PHP-FPM are prea putini workeri: `pm.max_children` (30 de elevi cer cel putin 30) |
| cateva laptopuri lente, restul OK | semnal WiFi slab la acele laptopuri (vezi coloana Semnal) |
| timpul creste in timpul testului | cache-ul Moodle e rece sau `opcache` e dezactivat |
| erori 500 la Moodle | `max_connections` in MySQL/MariaDB, sau disc plin |

Comenzi utile pe serverul Moodle, in timpul testului:

```bash
ss -s                                  # conexiuni
ps aux | grep php-fpm | wc -l          # procese PHP
tail -f /var/log/apache2/error.log     # erori
mysqladmin status                      # conexiuni MySQL
```

Masurarea din browser e „timp pana la primul octet + descarcare" pe pagina
Moodle. Daca vrei si timpul de randare complet, compara cu un test manual din
acelasi laptop: `curl -o /dev/null -s -w "%{time_total}\n" http://IP:8083/`.

---

## 6. Cifrele par ciudate

- **Latenta** e masurata de browser catre laptopul profesorului, deci include si
  munca serverului. 5–20 ms pe WiFi e normal. Daca vrei separat reteaua, ruleaza
  `ping IP-profesor` din alt terminal: diferenta e timpul pierdut in server.
- **Jitter** = variatia dintre esantioane; sub 5 ms e bine. Prima cerere e
  ignorata (handshake TCP), restul de 15 sunt masurate.
- **Viteza** e un transfer de 2 MB de pe laptopul profesorului, simultan de pe
  toate laptopurile — nu e viteza internetului. Un rezultat de 20–30 Mbps pe un
  AP de 2.4 GHz cu 30 de clienti e normal.
- **Semnalul** e procentul din Windows, nu dBm. Sub 50% apare portocaliu; RSSI
  (negativ, in dBm) e in CSV daca vrei cifra fizica.
- **„Moodle: esecuri"** numara laptopurile la care masuratoarea a esuat, nu
  erorile Moodle.

---

## 7. Rezultate lipsa, duplicate, sau dupa restart

- Rezultatele se grupeaza **dupa IP**, nu dupa numele calculatorului: daca doua
  laptopuri au acelasi IP (DHCP stricat, VM clonate), se suprascriu reciproc.
  In panou apare o singura linie — verifica cu `ipconfig` la elevi.
- Aceeasi apasare de „Porneste testul" creeaza o sesiune noua; rezultatele vechi
  raman in `data/session_<id>.json`. Le poti lista cu
  `curl http://IP:8090/api/sessions` si exporta cu
  `curl -o vechi.csv "http://IP:8090/api/export.csv?session_id=<id>"`.
- Fisierele se scriu atomic (intai `.tmp`, apoi redenumire): o intrerupere de
  curent nu mai lasa JSON trunchiat care sa impiedice pornirea serverului.

---

## 8. Ce a fost reparat (v1.1)

1. Scriptul de semnal avea IP-ul profesorului scris de mana; daca DHCP-ul il
   schimba, elevii trimiteau in gol. Acum serverul injecteaza adresa reala in
   momentul descarcarii.
2. Descarcarea e un singur `.bat` (fara dezarhivare), varianta `.zip` ramane.
3. BSSID nu era citit pe Windows 11 (`AP BSSID`), ieșirea în română nu era
   înțeleasă, ratele erau trunchiate la întreg.
4. Corpurile JSON cu BOM sau alt encoding (PowerShell 5.1) erau aruncate in
   tacere — acum se decodifica tolerant.
5. Semnalul putea ajunge string, iar panoul concatena valorile („88" + "72")
   in loc sa le adune.
6. Conexiunile keep-alive ale elevilor care inchideau pagina blocau un thread la
   infinit (serverul se sufoca treptat) — acum au timeout.
7. Scrierile in socket puteau arunca exceptii si umplea consola cu traceback-uri.
8. CSV-ul nu cita valorile (nume cu virgula strica coloanele) si nu avea BOM
   pentru Excel.
9. Masurarea Moodle lua doar antetele (`fetch` no-cors); acum foloseste Resource
   Timing pentru descarcarea completa si raporteaza motivul eșecului
   (timeout / retea / blocat de browser / URL lipsa).
10. Latenta avea 10 esantioane, cu prima poluata de handshake; acum are o
    cerere de incalzire, 15 esantioane si p95.
11. Panoul afiseaza starea instalarii (server, Moodle, avertismente de
    configurare) si motivele eșecului, nu doar „—".
