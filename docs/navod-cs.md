# calibre-mcp — český rychlostart

Podrobná dokumentace je v [README.md](../README.md) (anglicky). Tady je
jen to, na čem se to nejčastěji zasekne.

## Tři věci, bez kterých to nepojede

**1. Účet na Content Serveru je povinný.** Volba „Povolit neověřeným
místním připojením provést změny" se na kontejner **nevztahuje** —
přichází z docker bridge sítě (172.x.x.x) a Calibre ho za místní spojení
nepovažuje. Zákeřné je, že čtení funguje i bez účtu, takže se to projeví
až u prvního zápisu.

Účet založíte přes `calibre-server --manage-users`, nebo v GUI
v Předvolby → Sdílení po síti → Uživatelské účty.

**2. Ten účet musí mít právo zápisu.** Jinak projde přihlášení a každá
změna skončí na HTTP 403.

**3. Knihovnu pojmenujte výslovně.** Výchozí knihovna serveru nemusí být
ta, kterou chcete. Na stroji, kde se tohle vyvíjelo, byla výchozí
`calibre_pdb` (vedlejší archiv), zatímco skutečná sbírka je
`Calibre_Library`. Proto:

```yaml
CALIBRE_LIBRARY_ID: "Calibre_Library"
```

Když to necháte prázdné, všechno tiše spadne do výchozí knihovny serveru.
Skutečná id vypíše nástroj `list_libraries` — jsou to id, ne zobrazované
názvy.

## Spuštění

```bash
cp .env.example .env     # doplnit CALIBRE_USER a CALIBRE_PASSWORD
docker compose pull
docker compose up -d --no-build
python3 scripts/smoke.py
```

V `.env` je na začátku `CALIBRE_READONLY=1`. Až ověříte čtení, přepněte
na `0` a pusťte `docker compose up -d` znovu.

Image `ghcr.io/opentreecz/calibre-mcp:latest` používá Python 3.14.
Pro konkrétní verzi nastavte v `.env` například `CALIBRE_MCP_VERSION=0.1.0`.
Lokální sestavení: `docker compose up -d --build`. Adresu Calibre a ID knihovny
upravte v `docker-compose.yml`.

## Napojení na Claude

```bash
claude mcp add --transport http calibre http://127.0.0.1:8765/mcp
```

Příkaz výše je pro **Claude Code**, nikoli Desktop.

### Claude Desktop (macOS a Windows)

1. Spusťte Docker Desktop a stáhněte image příkazem
   `docker pull ghcr.io/opentreecz/calibre-mcp:latest`.
2. Vytvořte soubor s přihlašovacími údaji a `CALIBRE_READONLY=1`.
3. V Claude otevřete **Settings → Developer → Edit Config**.
4. Přidejte server s `command: "docker"` a argumenty `run --rm -i`,
   `--env-file` s absolutní cestou, `-e MCP_TRANSPORT=stdio` a názvem image.
5. Claude úplně ukončete a znovu spusťte. Požádejte o seznam knihoven.

Kompletní JSON, cesty pro oba systémy a řešení chyb najdete v
[podrobném návodu pro Claude Desktop](claude-desktop.md).
Desktop kontejner spouští sám; `docker compose up` pro tuto variantu není třeba.
Pro zápis změňte `CALIBRE_READONLY=0` a restartujte Desktop.
HTTP konfigurace `type`/`url` nepatří do lokálního Desktop JSON.

## Víc knihoven najednou

Není potřeba pouštět server dvakrát:

```
search_books(query="author:Čapek")                    výchozí knihovna
search_books(query="manuál", library="calibre_pdb")   konkrétní
search_books(query="Máj", library="all")              všechny naráz
libraries_overview()                                  přehled přes všechny
```

## Převod formátů

Běží na straně Calibre a hotový formát si server sám přidá ke knize —
v kontejneru žádné Calibre ani `ebook-convert` není.

```
convert_book(book_id=896, to_format="azw3")
```
