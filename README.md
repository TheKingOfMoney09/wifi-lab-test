# Test retea WiFi + Moodle — laborator informatica

Aplicatie pentru masurarea calitatii retelei wireless (latenta, viteza, semnal WiFi)
si a timpului de acces la Moodle, cand toti cei ~30 de clienti Windows fac asta
simultan.

## Componente

- `server.py` — serverul HTTP (ruleaza pe acest laptop, doar Python 3 standard, fara
  instalare de pachete).
- `static/` — pagina pentru elevi (`/`) si panoul profesorului (`/admin`).
- `scripts/` — script Windows (`run_wifi_test.bat` + `wifi_signal.ps1`) pe care
  fiecare elev il ruleaza o data, ca sa trimita semnalul WiFi real (citit cu
  `netsh wlan show interfaces`) catre server. Un browser nu poate citi semnalul
  WiFi real din Windows, de aceea e nevoie de acest script separat.
- `data/` — rezultatele salvate automat (JSON), generate la rulare.
- `config.json` — adresa Moodle, portul serverului, numarul de clienti asteptati.

## Pasul 1 — porneste serverul (pe laptopul profesorului)

```
cd /home/gigi/automatizari/wifi
python3 server.py
```

Va afisa IP-ul si portul (implicit port **8090**). Afla IP-ul laptopului in reteaua
laboratorului cu `ip addr` (vezi adresa de la interfata WiFi, ex: `192.168.6.113`).

Daca IP-ul laptopului s-a schimbat fata de `192.168.6.113`, actualizeaza:
- `config.json` -> `moodle_url` (daca Moodle ruleaza pe acelasi laptop)
- `scripts/wifi_signal.ps1` -> variabila `$serverUrl`

## Pasul 2 — elevii se conecteaza

Pe fiecare laptop Windows, conectat la aceeasi retea WiFi, elevii deschid in browser:

```
http://<IP-ul-laptopului-profesorului>:8090/
```

Introduc un identificator (ex: `PC-12`) si apasa **Salveaza**. Apoi, o singura data, apasa
butonul **Descarca testul de semnal WiFi (.zip)** de pe aceeasi pagina, dezarhiveaza si
ruleaza (dublu-click) `run_wifi_test.bat` pentru a trimite semnalul WiFi real catre server.

## Pasul 3 — profesorul monitorizeaza

Profesorul deschide pe acelasi laptop:

```
http://localhost:8090/admin
```

Apasa **Porneste testul** — in acel moment, toate laptopurile conectate (care au
pagina deschisa) vor rula automat, simultan, testul de:
- latenta si jitter (10 ping-uri catre server)
- viteza de descarcare (2 MB)
- timp de incarcare a paginii Moodle (configurata in `config.json`)

Rezultatele apar live in panoul de admin (tabel + grafice + sumar), impreuna cu
semnalul WiFi trimis de fiecare laptop prin script.

## Export pentru analiza

Butonul **Exporta CSV** din `/admin` descarca toate rezultatele sesiunii curente
(latenta, viteza, timp Moodle, semnal WiFi, canal etc.) intr-un fisier CSV, pentru
analiza ulterioara (Excel, Python, etc.).

## Repetarea testului

Poti apasa din nou **Porneste testul** pentru o noua sesiune (de exemplu testand cu
toti elevii apropiati de router, apoi departe de router, etc.) — fiecare sesiune are
rezultatele ei separate, salvate in `data/session_<id>.json`.
