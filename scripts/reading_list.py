#!/usr/bin/env python3
"""Check a calibre library against the 2026/27 Czech maturita reading list.

Talks to a running calibre-mcp over streamable-http, so it needs nothing
but the standard library.

    python3 scripts/reading_list.py
    python3 scripts/reading_list.py --tag maturita
    python3 scripts/reading_list.py --tag maturita --saved-search "Literatura k maturite"

Without --tag nothing is modified. --tag adds that tag to every book found
(existing tags are preserved). --saved-search then creates a saved search
over it, which the calibre GUI can turn into a Virtual Library.
"""
import json
import sys
import unicodedata
import urllib.error
import urllib.request

# (number, title as it appears in the list, author surname used for matching)
READING_LIST = [
    (1, "Genesis", "bible"), (2, "Dekameron", "boccaccio"),
    (3, "Romeo a Julie", "shakespeare"), (4, "Zkrocení zlé ženy", "shakespeare"),
    (5, "Lakomec", "moliere"), (6, "Robinson Crusoe", "defoe"),
    (7, "Utrpení mladého Werthera", "goethe"),
    (8, "Chrám Matky Boží v Paříži", "hugo"), (9, "Bídníci", "hugo"),
    (10, "Máj", "mácha"), (11, "Strakonický dudák", "tyl"),
    (12, "Fidlovačka", "tyl"), (13, "Kytice", "erben"),
    (14, "Tyrolské elegie", "havlíček"), (15, "V zámku a podzámčí", "němcová"),
    (16, "Babička", "němcová"), (17, "Divá Bára", "němcová"),
    (18, "Oliver Twist", "dickens"), (19, "Zabiják", "zola"),
    (20, "Nana", "zola"), (21, "Revizor", "gogol"),
    (22, "Zločin a trest", "dostojevsk"), (23, "Povídky malostranské", "neruda"),
    (24, "Balady a romance", "neruda"), (25, "Noc na Karlštejně", "vrchlick"),
    (26, "Okna v bouři", "vrchlick"), (27, "Staré pověsti české", "jirásek"),
    (28, "Psohlavci", "jirásek"), (29, "Maryša", "mrštík"),
    (30, "Květy zla", "baudelaire"), (31, "Malý princ", "exupéry"),
    (32, "Stařec a moře", "hemingway"), (33, "Velký Gatsby", "fitzgerald"),
    (34, "Petr a Lucie", "rolland"), (35, "Na západní frontě klid", "remarque"),
    (36, "Pygmalion", "shaw"), (37, "Sophiina volba", "styron"),
    (38, "Farma zvířat", "orwell"), (39, "Jeden den Ivana Děnisoviče", "solženicyn"),
    (40, "Lolita", "nabokov"), (41, "Slezské písně", "bezruč"),
    (42, "Stříbrný vítr", "šrámek"), (43, "Krysař", "dyk"),
    (44, "Maminka", "seifert"), (45, "Všechny krásy světa", "seifert"),
    (46, "Těžká hodina", "wolker"), (47, "Můj obchod se psy", "hašek"),
    (48, "Osudy dobrého vojáka Švejka", "hašek"), (49, "Bylo nás pět", "poláček"),
    (50, "Saturnin", "jirotka"), (51, "R.U.R.", "čapek"),
    (52, "Bílá nemoc", "čapek"), (53, "Válka s mloky", "čapek"),
    (54, "Nikola Šuhaj loupežník", "olbracht"), (55, "Golet v údolí", "olbracht"),
    (56, "Ostře sledované vlaky", "hrabal"), (57, "Postřižiny", "hrabal"),
    (58, "Audience", "havel"), (59, "Smrt krásných srnců", "pavel"),
    (60, "Jak jsem potkal ryby", "pavel"), (61, "Báječná léta pod psa", "viewegh"),
    (62, "Účastníci zájezdu", "viewegh"), (63, "Hovno hoří", "šabach"),
    (64, "Hana", "mornštajnová"), (65, "Listopád", "mornštajnová"),
    (66, "Fimfárum", "werich"), (67, "Dobře mi tak", "třeštíková"),
    (68, "Bábovky", "třeštíková"), (69, "Souostroví Gulag", "solženicyn"),
    (70, "Mistr a Markétka", "bulgakov"), (71, "Letnice", "hlaučo"),
    (72, "Sto roků samoty", "márquez"), (73, "Nespoutaná", "doyle"),
    (74, "Coura", "bernášková"), (75, "Černí baroni", "švandrlík"),
    (76, "Norské dřevo", "murakami"), (77, "Kvílení", "ginsberg"),
    (78, "Proměna", "kafka"), (79, "Kde zpívají raci", "owens"),
    (80, "Marťan", "weir"), (81, "Spalovač mrtvol", "fuks"),
    (82, "Žert", "kundera"), (83, "Vyhnání Gerty Schnirch", "tučková"),
    (84, "Šikmý kostel", "lednická"), (85, "Pěšky mezi buddhisty a komunisty", "zibura"),
    (86, "Zuzanin dech", "katalpa"), (87, "Paměť mojí babičce", "hůlová"),
    (88, "Poslední přání", "sapkowski"),
]

URL = "http://127.0.0.1:8765/mcp"
LIBRARY = "Calibre_Library"
TAG = None
SAVED_SEARCH = None

args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "--url" and i + 1 < len(args):
        URL = args[i + 1]; i += 2
    elif args[i] == "--library" and i + 1 < len(args):
        LIBRARY = args[i + 1]; i += 2
    elif args[i] == "--tag" and i + 1 < len(args):
        TAG = args[i + 1]; i += 2
    elif args[i] == "--saved-search" and i + 1 < len(args):
        SAVED_SEARCH = args[i + 1]; i += 2
    else:
        raise SystemExit("unknown argument: %s" % args[i])

SESSION = None
_id = 0


def rpc(method, params=None, notify=False):
    global SESSION, _id
    body = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if not notify:
        _id += 1
        body["id"] = _id
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 **({"mcp-session-id": SESSION} if SESSION else {})})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            SESSION = SESSION or r.headers.get("mcp-session-id")
            raw = r.read().decode()
    except urllib.error.URLError as e:
        raise SystemExit("Cannot reach %s: %s" % (URL, e))
    if notify:
        return None
    for line in raw.splitlines():
        if line.startswith("data:"):
            msg = json.loads(line[5:].strip())
            if "error" in msg:
                raise SystemExit("MCP error: %s" % msg["error"])
            return msg.get("result")
    raise SystemExit("Unexpected response: " + raw[:300])


def call(tool, **a):
    res = rpc("tools/call", {"name": tool, "arguments": a})
    text = "".join(c.get("text", "") for c in res.get("content", []))
    if res.get("isError"):
        raise RuntimeError(text)
    return text


def fold(s):
    """Lowercase and strip diacritics, so 'Čapek' matches 'capek'."""
    s = unicodedata.normalize("NFD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "reading-list", "version": "1"}})
rpc("notifications/initialized", notify=True)
print("library: %s   list: %d works\n" % (LIBRARY, len(READING_LIST)))

found, missing = [], []
for num, title, author in READING_LIST:
    try:
        res = json.loads(call("search_books", query='title:"%s"' % title,
                              limit=25, library=LIBRARY))
    except RuntimeError as e:
        print("  ! %2d %-42s search failed: %s" % (num, title, e)); continue
    hits = [b for b in res.get("books") or []
            if fold(author) in fold(" ".join(b.get("authors") or []))]
    if hits:
        b = hits[0]
        found.append((num, title, b))
        print("  ok %2d  %-42s id=%-5s %s" % (num, title, b["id"],
                                              ",".join(b.get("formats") or [])))
    else:
        missing.append((num, title, author))

print("\n" + "=" * 70)
print("IN THE LIBRARY: %d of %d" % (len(found), len(READING_LIST)))
print("=" * 70)
print("\nMISSING (%d):" % len(missing))
for num, title, author in missing:
    print("  %2d  %-46s %s" % (num, title, author))

if TAG:
    print("\n" + "=" * 70)
    print("Tagging %d books with %r (existing tags kept)" % (len(found), TAG))
    ok = 0
    for num, title, b in found:
        tags = list(b.get("tags") or [])
        if TAG in tags:
            ok += 1
            continue
        tags.append(TAG)
        try:
            call("set_metadata", book_id=b["id"], fields={"tags": tags},
                 library=LIBRARY)
            ok += 1
        except RuntimeError as e:
            print("  ! id=%s %s" % (b["id"], e))
    print("tagged: %d/%d" % (ok, len(found)))

    if SAVED_SEARCH:
        expr = "tags:%s" % TAG
        try:
            call("create_saved_search", name=SAVED_SEARCH, expression=expr,
                 library=LIBRARY)
            print("\nsaved search %r = %r created." % (SAVED_SEARCH, expr))
            print("In the calibre GUI: Virtual library -> Create library,")
            print("then pick this saved search to get a Virtual Library.")
        except RuntimeError as e:
            print("\nsaved search failed: %s" % e)
elif SAVED_SEARCH:
    print("\n--saved-search needs --tag as well (the search is built on it).")
