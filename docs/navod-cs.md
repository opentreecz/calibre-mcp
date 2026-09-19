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
docker compose build
docker compose up -d
python3 scripts/smoke.py
```

V `.env` je na začátku `CALIBRE_READONLY=1`. Až ověříte čtení, přepněte
na `0` a pusťte `docker compose up -d` znovu.

## Napojení na Claude

```bash
claude mcp add --transport http calibre http://127.0.0.1:8765/mcp
```

Pro Claude Desktop a další varianty (včetně stdio přes `docker run`) viz
[README](../README.md#adding-it-to-claude).

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
