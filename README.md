# Test retea WiFi + Moodle — laborator informatica

Aplicatie pentru masurarea calitatii retelei wireless (latenta, viteza, semnal WiFi)
si a timpului de acces la Moodle, cand toti cei ~30 de clienti Windows fac asta
simultan.

Daca ceva nu functioneaza, vezi **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** —
are lista de verificare pentru firewall, IP schimbat, Moodle pe localhost,
scriptul de semnal si cifre care par ciudate.

## Componente

- `server.py` — serverul HTTP (ruleaza pe acest laptop, doar Python 3 standard, fara
  instalare de pachete).
- `static/` — pagina pentru elevi (`/`) si panoul profesorului (`/admin`).
- `scripts/wifi_signal.ps1` — scriptul Windows care citeste semnalul WiFi real cu
  `netsh wlan show interfaces` si il trimite serverului. Un browser nu poate citi
  semnalul WiFi real din Windows, de aceea e nevoie de acest script separat.
  Serverul il livreaza fie ca un singur `.bat` (dublu-click, fara dezarhivare),
  fie ca arhiva `.zip`, cu adresa serverului deja completata.
- `data/` — rezultatele salvate automat (JSON), generate la rulare.
- `config.json` — adresa Moodle, portul serverului, numarul de clienti asteptati.
- `selftest.py` — autotest: 30 de elevi simulati + un Moodle fals, fara sa fie
  nevoie de laptopurile din laborator.

## Pasul 1 — porneste serverul (pe laptopul profesorului)

```
cd /home/gigi/automatizari/wifi
python3 server.py
```

La pornire afiseaza adresele reale (`http://<IP>:8090/` pentru elevi,
`http://<IP>:8090/admin` pentru profesor), calea unde salveaza rezultatele si daca
Moodle raspunde de pe acest laptop. Daca Moodle nu raspunde sau `config.json` are
`localhost`, apare un `ATENTIE` explicat.

Portul implicit e **8090**. Daca IP-ul laptopului s-a schimbat, nu trebuie editat
nimic in scripturi: ele isi iau adresa din pagina de descarcare. Trebuie doar
actualizat `config.json` -> `moodle_url` daca Moodle ruleaza pe acest laptop.

Variabile de mediu utile (fara sa modifici `config.json`):

```
LABTEST_PORT=8091 LABTEST_MOODLE_URL=http://192.168.6.113:8083 python3 server.py
LABTEST_HOST=127.0.0.1      # doar pentru teste locale
LABTEST_DATA_DIR=/tmp/lab   # alt folder pentru rezultate
LABTEST_EXPECTED_CLIENTS=28
LABTEST_PAYLOAD_MB=10
```

## Pasul 2 — elevii se conecteaza

Pe fiecare laptop Windows, conectat la aceeasi retea WiFi, elevii deschid in browser:

```
http://<IP-ul-laptopului-profesorului>:8090/
```

Introduc un identificator (ex: `PC-12`) si apasa **Salveaza**. Apoi, o singura data,
apasa butonul **Descarca testul de semnal WiFi (.bat)**, il salveaza si face
dublu-click pe el. Scriptul citeste semnalul si il trimite serverului; fereastra
spune daca a reusit.

## Pasul 3 — profesorul monitorizeaza

Profesorul deschide pe acelasi laptop:

```
http://<IP>:8090/admin
```

Panoul arata intai **starea instalarii**: daca serverul raspunde, daca Moodle
raspunde de pe laptopul profesorului (si in cat timp) si eventuale probleme de
configurare.

Apasa **Porneste testul** — in acel moment, toate laptopurile conectate (care au
pagina deschisa) vor rula automat, simultan, testul de:

- latenta, jitter, p95 si pierderi (o cerere de incalzire + 15 ping-uri catre server)
- viteza de descarcare (2 MB)
- timp de incarcare a paginii Moodle (configurata in `config.json`), cu motivul
  eșecului daca nu raspunde

Rezultatele apar live in panoul de admin (tabel + grafice + sumar), impreuna cu
semnalul WiFi trimis de fiecare laptop prin script.

## Export pentru analiza

Butonul **Exporta CSV** din `/admin` descarca toate rezultatele sesiunii curente
(latenta, p95, jitter, pierderi, viteza, timp Moodle + motiv eșec, semnal WiFi,
RSSI, canal, banda, rate) intr-un fisier CSV cu BOM, care se deschide corect in
Excel. Sesiunile vechi raman in `data/session_<id>.json`; lista lor:
`curl http://IP:8090/api/sessions`.

## Repetarea testului

Poti apasa din nou **Porneste testul** pentru o noua sesiune (de exemplu testand cu
toti elevii apropiati de router, apoi departe de router, etc.) — fiecare sesiune are
rezultatele ei separate, salvate in `data/session_<id>.json`.

## Verificare fara laborator

```
python selftest.py            # 30 de elevi simulati, Moodle fals, 27 de verificari
python selftest.py --clients 8
```

Testul isi porneste propriile porturi, deci poate rula si in timpul orei, pe
laptopul profesorului, fara sa deranjeze un server deja pornit pe 8090.

## API (pentru depanare)

| Ruta | Ce face |
|---|---|
| `GET /api/health` | stare server, verificare Moodle, avertismente de configurare |
| `GET /api/session` | sesiunea curenta + `moodle_url` |
| `GET /api/results` | rezultatele sesiunii curente + semnalul WiFi |
| `GET /api/sessions` | sesiunile salvate pe disc |
| `GET /api/export.csv?session_id=` | export CSV |
| `POST /api/session/start` / `stop` | control din panoul profesorului |
| `POST /api/results` / `POST /api/wifi-signal` | ce trimit elevii |
| `GET /download/test-wifi.bat` / `.zip` | scriptul de semnal, cu adresa serverului |
