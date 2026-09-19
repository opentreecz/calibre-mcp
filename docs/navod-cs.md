# Calibre MCP přes HTTP API — nasazení na Debianu

MCP server, který mluví s běžícím **Calibre Content Serverem** po HTTP.
Nevolá `calibredb`. Grafické Calibre může být zapnuté a knihovna může
běžet na úplně jiném stroji.

---

## 0. Nejdřív dvě věci, které je potřeba vědět

### Content Server ≠ Calibre-Web

Zmínil jste obojí. Jsou to dva různé projekty s různým API:

- **calibre Content Server** — vestavěný v Calibre (`calibre-server`, nebo
  zapnutý přímo v GUI). Má endpointy `/ajax/…` pro čtení a `/cdb/…` pro
  zápis. **Tenhle server cílí sem.**
- **Calibre-Web** — samostatná aplikace třetí strany nad stejnou knihovnou.
  Jiné API (hlavně OPDS), jiná autentizace, zápis metadat přes něj je
  omezený. Pro tenhle účel je Content Server jednoznačně lepší volba —
  je oficiální, umí zápis a nepřidává další komponentu.

### Převod formátů běží na serveru

Content Server má vlastní konverzní endpointy `srv/convert.py`:
`/conversion/book-data/{id}`, `/conversion/start/{id}` a
`/conversion/status/{job}`. Server úlohu zařadí do fronty, zpracuje ji
a **hotový formát si sám přidá ke knize** — klient jen spustí a doptává se
na průběh.

Prakticky to znamená, že tenhle MCP server **nepotřebuje lokálně
nainstalované Calibre ani `ebook-convert`**. Všechno je čisté HTTP, což se
hodí hlavně v Dockeru — obraz zůstane malý.

> Dřívější verze tohoto návodu tvrdila, že převod přes API nejde. Bylo to
> špatně: hledal jsem konverzní endpoint v `ajax.py`, `cdb.py` a
> `content.py`, ale bydlí v samostatném `convert.py`.

---

## 1. Použité endpointy

Ověřeno proti zdrojovým kódům calibre:

| Endpoint | K čemu |
|---|---|
| `GET /ajax/library-info` | seznam knihoven a výchozí knihovna |
| `GET /ajax/search/{lib}?query=&num=&offset=&sort=` | hledání → seznam ID |
| `GET /ajax/books/{lib}?ids=1,2,3` | metadata víc knih najednou |
| `GET /ajax/book/{id}/{lib}` | metadata jedné knihy |
| `GET /get/{fmt}/{id}/{lib}` | stažení souboru (i `cover`, `opf`) |
| `POST /cdb/set-fields/{id}/{lib}` | změna metadat, přidání formátu |
| `POST /cdb/add-book/{job}/{dup}/{filename}/{lib}` | nahrání nové knihy |

Přidání formátu jde přes `set-fields` se speciálním polem `added_formats`,
kde se data posílají jako base64 data URL:

```json
{"changes": {"added_formats": [
  {"ext": "mobi", "data_url": "data:application/octet-stream;base64,…"}
]}}
```

---

## 2. Příprava Content Serveru

### Varianta A — server v GUI (když chcete mít Calibre otevřené)

1. Calibre → Předvolby → **Sdílení po síti**
2. Zapnout server, port `8080`
3. Zaškrtnout „Spouštět server automaticky při startu calibre"
4. Pro zápis: buď zaškrtnout povolení změn z nepřihlášených lokálních
   spojení, nebo založit uživatele s právem zápisu

### Varianta B — samostatný server (GUI neběží)

```bash
calibre-server --port 8080 --enable-local-write ~/Calibre\ Library
```

Jako služba pro vašeho uživatele:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/calibre-server.service <<'EOF'
[Unit]
Description=Calibre Content Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/calibre-server --port 8080 --enable-local-write %h/Calibre Library
Restart=on-failure

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now calibre-server
```

> **Nikdy obojí najednou.** Buď server v GUI, nebo samostatný — ne oba na
> stejnou knihovnu.

> `--enable-local-write` povoluje zápis bez přihlášení. Nechte server
> poslouchat na localhostu. Když ho vystavujete do sítě, použijte místo
> toho uživatele s heslem (`--enable-auth`, `--manage-users`).

### Ověření, že API odpovídá

```bash
curl -s http://localhost:8080/ajax/library-info | python3 -m json.tool
```

Vypíše `library_map` a `default_library`. To ID z `library_map` půjde
do `CALIBRE_LIBRARY_ID`.

---

## 3. Instalace MCP serveru

Debian 12+ má PEP 668, takže venv:

```bash
mkdir -p ~/.local/share/calibre-api-mcp
cd ~/.local/share/calibre-api-mcp
cp /cesta/ke/calibre_api_mcp.py .

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install "mcp[cli]" requests
```

Kontrola:

```bash
CALIBRE_URL=http://localhost:8080 .venv/bin/python -c "
import asyncio, calibre_api_mcp as m
for t in asyncio.run(m.mcp.list_tools()): print('-', t.name)
"
```

Má vypsat devět nástrojů.

---

## 4. Konfigurace klienta

### Claude Desktop (Linux) — `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "calibre": {
      "command": "/home/lukas/.local/share/calibre-api-mcp/.venv/bin/python",
      "args": ["/home/lukas/.local/share/calibre-api-mcp/calibre_api_mcp.py"],
      "env": {
        "CALIBRE_URL": "http://localhost:8080",
        "CALIBRE_LIBRARY_ID": "Calibre_Library"
      }
    }
  }
}
```

### Claude Code / Cowork

```bash
claude mcp add calibre \
  --env CALIBRE_URL=http://localhost:8080 \
  --env CALIBRE_LIBRARY_ID=Calibre_Library \
  -- /home/lukas/.local/share/calibre-api-mcp/.venv/bin/python \
     /home/lukas/.local/share/calibre-api-mcp/calibre_api_mcp.py
```

Cesty absolutní, `~` se nerozvíjí.

### Proměnné prostředí

| Proměnná | Význam |
|---|---|
| `CALIBRE_URL` | **povinné**, např. `http://localhost:8080` |
| `CALIBRE_LIBRARY_ID` | **výchozí** knihovna; přebít jde parametrem `library` |
| `CALIBRE_USER` / `CALIBRE_PASSWORD` | když má server autentizaci |
| `CALIBRE_AUTH` | `digest` (výchozí) \| `basic` \| `none` |
| `CALIBRE_READONLY` | `1` zakáže zápisové nástroje |
| `CALIBRE_TIMEOUT` | timeout HTTP, výchozí 120 s |
| `CALIBRE_VERIFY_TLS` | `0` pro self-signed certifikát v LAN |

Calibre má ve výchozím stavu **digest** autentizaci. Za reverzní proxy se
obvykle přepíná na `basic` (`--auth-mode=basic`) — pak nastavte
`CALIBRE_AUTH=basic`.

---

## 5. Nástroje

| Nástroj | Přes API? | K čemu |
|---|---|---|
| `list_libraries` | ano | knihovny na serveru a ID pro konfiguraci |
| `search_books` | ano | hledání v syntaxi Calibre + metadata |
| `search_all_libraries` | ano | hledá napříč **všemi** knihovnami najednou |
| `copy_to_library` | ano | kopie/přesun knih mezi knihovnami |
| `get_book` | ano | kompletní metadata jedné knihy |
| `library_stats` | ano | počet knih a rozpad formátů |
| `download_book` | ano | stažení souboru do adresáře |
| `set_metadata` | ano | změna názvu, autorů, tagů, série, hodnocení |
| `add_book` | ano | nahrání nové knihy |
| `add_format` | ano | přidání dalšího formátu k existující knize |
| `conversion_options` | ano | jaké formáty a volby kniha pro převod nabízí |
| `convert_book` | ano | zařadí převod na serveru a počká na dokončení |
| `conversion_job_status` | ano | průběh / zrušení úlohy spuštěné s `wait=False` |

### Dvě knihovny

Server zvládá víc knihoven najednou — **není potřeba pouštět dvě instance**.
Každý nástroj má parametr `library`; prázdný znamená výchozí
(`CALIBRE_LIBRARY_ID`, a když není, výchozí knihovna serveru).

```
search_books(query="author:Čapek")                      → výchozí knihovna
search_books(query="manuál", library="calibre_pdb")     → druhá knihovna
search_all_libraries(query="Máj")                       → obě najednou
copy_to_library(book_ids=[7], target_library="Calibre_Library",
                library="calibre_pdb")                  → z pdb do hlavní
```

Když zadáte id, které na serveru není, dostanete rovnou seznam těch
platných místo záhadné chyby 404.

`copy_to_library` s `move=True` knihy ze zdrojové knihovny **odstraní**.
Výchozí je kopírování.

U `set_metadata` se autoři, tagy a jazyky zadávají jako **seznam**:

```json
{"title": "Máj", "authors": ["Karel Hynek Mácha"],
 "tags": ["maturita", "poezie"], "rating": 8, "#status": "přečteno"}
```

`convert_book` se `save_to` uloží výsledek jen do adresáře a do knihovny
nesáhne — hodí se, když chcete jen soubor do čtečky.

Mazání knih server záměrně neumí, i když endpoint `/cdb/delete-books`
existuje. Nevratná operace spuštěná omylem z chatu je špatný nápad.

---

## 6. Co bylo otestováno

Server jsem prohnal proti napodobenině Content Serveru, která implementuje
výše uvedené endpointy. Prošlo:

- všech 11 nástrojů se zaregistruje s platnými schématy
- směrování na dvě knihovny (`Calibre_Library` a `calibre_pdb`): hledání,
  metadata, stažení i převod trefí správnou knihovnu — ověřeno tím, že
  stažený obsah nese id knihovny, ze které přišel
- neznámé id knihovny vrátí seznam platných místo 404
- `search_all_libraries` projde obě a vrátí výsledky zvlášť
- `copy_to_library` pošle správnou cestu i tělo; kopii do téže knihovny odmítne
- hledání, metadata, statistiky, stažení souboru
- změna metadat přes `set-fields`
- nahrání nové knihy i nového formátu (base64 data URL dorazí nepoškozená)
- celý převod: stažení → konverze → nahrání zpět; nový formát se propsal
  do seznamu formátů knihy
- odmítnutí převodu do formátu, který kniha už má
- odmítnutí neexistujícího zdrojového formátu
- chybová hláška, když chybí `ebook-convert`
- režim `CALIBRE_READONLY`

**Netestováno proti skutečnému Calibre** — na to jsem se z tohohle prostředí
nedostal. Napodobenina ověřuje logiku klienta (sestavení URL, tvary JSON,
kódování), ne to, že reálný server odpoví přesně stejně. První ostrý běh
dělejte se zálohou a s `CALIBRE_READONLY=1`.

---

## 7. Když to nejede

| Příznak | Příčina |
|---|---|
| `Není nastavena proměnná CALIBRE_URL` | chybí `env` v konfiguraci klienta |
| `401` | špatné jméno/heslo, nebo `CALIBRE_AUTH` (digest vs basic) |
| `403` | uživatel nemá právo zápisu; povolte zápis nebo `--enable-local-write` |
| `Spojení selhalo` | server neběží, nebo poslouchá na jiném portu |
| `Server nevrátil JSON` | `CALIBRE_URL` míří jinam než na Content Server |
| prázdné výsledky | špatné `CALIBRE_LIBRARY_ID` — ověřte `list_libraries` |

Rychlý test mimo MCP:

```bash
curl -s "http://localhost:8080/ajax/search?query=author:Čapek&num=3" \
  | python3 -m json.tool
```

Když neprojde tohle, chyba není v MCP serveru.

---

## Zdroje

- [Content server — manuál](https://manual.calibre-ebook.com/server.html)
- [calibre-server — volby](https://manual.calibre-ebook.com/generated/en/calibre-server.html)
- [srv/ajax.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/srv/ajax.py)
- [srv/cdb.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/srv/cdb.py)
- [srv/content.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/srv/content.py)
- [srv/convert.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/srv/convert.py)
