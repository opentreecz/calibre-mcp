#!/usr/bin/env python3
"""Check a calibre library against the 2026/27 Czech maturita reading list.

Talks to a running calibre-mcp over streamable-http, so it needs nothing
but the standard library.

    python3 scripts/reading_list.py
    python3 scripts/reading_list.py --tag maturita
    python3 scripts/reading_list.py --tag maturita --saved-search "Literatura k maturite"
    python3 scripts/reading_list.py --show-tags maturita

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
    # A 4th element lists alternative titles: the work counts as present
    # if ANY of them matches. Genesis is not a standalone book in most
    # editions — it sits inside an Old Testament volume.
    (1, "Genesis", "bible", ["stary zakon", "bible"]),
    (2, "Dekameron", "boccaccio"),
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
SHOW_TAGS = None

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
    elif args[i] == "--show-tags" and i + 1 < len(args):
        SHOW_TAGS = args[i + 1]; i += 2
    else:
        raise SystemExit("unknown argument: %s" % args[i])

SESSION = None
PROTOCOL = None
_id = 0


def rpc(method, params=None, notify=False):
    global SESSION, PROTOCOL, _id
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
                 **({"MCP-Protocol-Version": PROTOCOL} if PROTOCOL else {}),
                 **({"mcp-session-id": SESSION} if SESSION else {})})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            SESSION = SESSION or r.headers.get("mcp-session-id")
            raw = r.read().decode()
    except urllib.error.URLError as e:
        raise SystemExit("Cannot reach %s: %s" % (URL, e))
    if notify:
        return None
    if raw.lstrip().startswith("{"):
        msg = json.loads(raw)
        if "error" in msg:
            raise SystemExit("MCP error: %s" % msg["error"])
        if method == "initialize":
            PROTOCOL = msg["result"]["protocolVersion"]
        return msg.get("result")
    for line in raw.splitlines():
        if line.startswith("data:"):
            msg = json.loads(line[5:].strip())
            if "error" in msg:
                raise SystemExit("MCP error: %s" % msg["error"])
            if method == "initialize":
                PROTOCOL = msg["result"]["protocolVersion"]
            return msg.get("result")
    raise SystemExit("Unexpected response: " + raw[:300])


def call(tool, **a):
    res = rpc("tools/call", {"name": tool, "arguments": a})
    text = "".join(c.get("text", "") for c in res.get("content", []))
    if res.get("isError"):
        raise RuntimeError(text)
    return text


def fold(s):
    """Lowercase, strip diacritics and punctuation: 'Čapek' -> 'capek'."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = "".join(c if c.isalnum() or c.isspace() else " " for c in s)
    return " ".join(s.split())   # collapse runs of spaces, so that
                                 # "R.U.R." and "R. U. R." both fold to "r u r"


def word_matches(tok, word):
    """Tolerate Czech declension: the list says 'Kvety zla', the library
    has 'Vybor z Kvetu zla' — 'kvety' vs 'kvetu' differ only in the
    ending. Author is matched separately, so this stays safe."""
    if word.startswith(tok) or tok.startswith(word):
        return True
    return len(tok) >= 4 and len(word) >= 4 and tok[:-1] == word[:-1]


def title_matches(toks, folded_title):
    words = folded_title.split()
    return all(any(word_matches(t, w) for w in words) for t in toks)


def tokens(s):
    """Significant words, so a subtitle or an extra preposition cannot
    break the match: 'V zamku a podzamci' still matches
    'V zamku a v podzamci', and 'Kytice' matches
    'Kytice z povesti narodnich'."""
    return [w for w in fold(s).split() if len(w) >= 3]


DRM_TAG = "drm"


def is_drm(book):
    """A book tagged DRM is in the library but cannot be opened on a PC —
    typically a title saved off a Kindle. It counts as owned, never as
    readable, so the report keeps the two apart."""
    return any((t or "").strip().lower() == DRM_TAG
               for t in (book.get("tags") or []))


def pair_by_author(missing, stray):
    """A book tagged by hand is a statement that the work IS in the library,
    even when its title in calibre looks nothing like the one on the list
    (other edition, omnibus volume, subtitle). Trust the tag: pair each
    leftover tagged book with the one missing work by the same author.

    Only a 1:1 pairing counts — two candidates for the same author stay
    unresolved rather than being guessed at. Returns
    (paired, still_missing, leftover) and does not modify its arguments.
    """
    stray = list(stray)
    paired, rest = [], []
    for num, title, author, others in missing:
        a = fold(author).strip()
        cands = [b for b in stray if a in fold(" ".join(b.get("authors") or []))]
        competing = [entry for entry in missing
                     if cands and fold(entry[2]).strip() in
                     fold(" ".join(cands[0].get("authors") or []))]
        if len(cands) == 1 and len(competing) == 1:
            paired.append((num, title, cands[0]))
            stray.remove(cands[0])
        else:
            rest.append((num, title, author, others))
    return paired, rest, stray


rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "reading-list", "version": "1"}})
rpc("notifications/initialized", notify=True)

if SHOW_TAGS:
    # Read-only audit: did adding the tag cost any book its other tags?
    res = json.loads(call("search_books", query="tags:%s" % SHOW_TAGS,
                          limit=100000, library=LIBRARY))
    books = res.get("books") or []
    only, extra = [], []
    for b in books:
        tags = b.get("tags") or []
        (only if [t for t in tags if t != SHOW_TAGS] == [] else extra).append(b)
    print("books tagged %r: %d\n" % (SHOW_TAGS, len(books)))
    print("with other tags as well (%d) — proof nothing was overwritten:"
          % len(extra))
    for b in sorted(extra, key=lambda x: x["id"])[:40]:
        print("  id=%-5s %-38s %s" % (b["id"], (b.get("title") or "")[:38],
                                      ", ".join(b.get("tags") or [])))
    print("\nwith %r only (%d) — these simply had no tags before:"
          % (SHOW_TAGS, len(only)))
    for b in sorted(only, key=lambda x: x["id"])[:40]:
        print("  id=%-5s %s" % (b["id"], (b.get("title") or "")[:52]))

    # Anything tagged that the matcher does not recognise is either a
    # manual addition or a blind spot in the matching — worth seeing.
    whole = json.loads(call("search_books", query="", limit=100000,
                            library=LIBRARY)).get("books") or []
    by_id = {b["id"]: b for b in whole}
    for b in by_id.values():
        b["_t"], b["_a"] = fold(b.get("title")), fold(" ".join(b.get("authors") or []))
    matched = set()
    for entry in READING_LIST:
        author = entry[2]
        variants = [entry[1]] + list(entry[3] if len(entry) > 3 else [])
        for b in by_id.values():
            if fold(author).strip() not in b["_a"]:
                continue
            for v in variants:
                tk = tokens(v)
                if (tk and title_matches(tk, b["_t"])) or \
                   (not tk and fold(v).strip() in b["_t"]):
                    matched.add(b["id"]); break
    stray = [b for b in books if b["id"] not in matched]
    print("\ntagged but NOT matched by the list (%d) — manual additions, "
          "or gaps in the matching:" % len(stray))
    for b in sorted(stray, key=lambda x: x["id"]):
        print("  id=%-5s %-40s %s" % (b["id"], (b.get("title") or "")[:40],
                                      ", ".join(b.get("authors") or [])))
    raise SystemExit(0)

# One pass over the whole library, then match locally. Far more reliable
# than 88 calibre queries whose syntax has to survive quoting and accents.
print("fetching %s ..." % LIBRARY, flush=True)
lib = json.loads(call("search_books", query="", limit=100000,
                      library=LIBRARY))
shelf = lib.get("books") or []
print("library: %s   %d books   list: %d works\n"
      % (LIBRARY, len(shelf), len(READING_LIST)))

for b in shelf:
    b["_t"] = fold(b.get("title"))
    b["_a"] = fold(" ".join(b.get("authors") or []))

found, missing = [], []
for entry in READING_LIST:
    num, title, author = entry[0], entry[1], entry[2]
    variants = [title] + list(entry[3] if len(entry) > 3 else [])
    hits = []
    for b in shelf:
        if fold(author).strip() not in b["_a"]:
            continue
        for v in variants:
            toks = tokens(v)
            if (toks and title_matches(toks, b["_t"])) or \
               (not toks and fold(v).strip() in b["_t"]):
                hits.append(b)
                break
    if hits:
        hits = sorted(hits, key=lambda x: len(x["_t"]))
        found.append((num, title, hits))
        for n, b in enumerate(hits):
            print("  ok %2s  %-42s id=%-5s %-30s %s"
                  % (num if n == 0 else "", title if n == 0 else "",
                     b["id"], (b.get("title") or "")[:30],
                     ",".join(b.get("formats") or [])))
    else:
        others = [b for b in shelf if fold(author).strip() in b["_a"]]
        missing.append((num, title, author, others))

# Books already carrying the tag that the matcher did not recognise —
# see pair_by_author().
reconciled, stray = [], []
if TAG:
    matched_ids = {b["id"] for _, _, hits in found for b in hits}
    tagged = [b for b in shelf
              if TAG in (b.get("tags") or []) and b["id"] not in matched_ids]
    reconciled, missing, stray = pair_by_author(missing, tagged)

# Every copy of the work is DRM-locked -> owned, but not readable here.
locked = [(num, title, hits) for num, title, hits in found
          if hits and all(is_drm(b) for b in hits)]
locked += [(num, title, [b]) for num, title, b in reconciled if is_drm(b)]

print("\n" + "=" * 74)
print("IN THE LIBRARY: %d of %d works   (%d books, multi-volume counted)"
      % (len(found) + len(reconciled), len(READING_LIST),
         sum(len(h) for _, _, h in found) + len(reconciled)))
if locked:
    print("READABLE ON A PC: %d of %d   (%d only as a DRM-locked copy)"
          % (len(found) + len(reconciled) - len(locked), len(READING_LIST),
             len(locked)))
print("=" * 74)
print("\nMISSING (%d):" % len(missing))
for num, title, author, others in missing:
    print("  %2d  %-44s %s" % (num, title, author))
    for o in others[:3]:
        print("        same author in library: id=%-5s %s"
              % (o["id"], (o.get("title") or "")[:52]))

if reconciled:
    print("\nRECOGNISED BY THE TAG ONLY (%d) — tagged by hand; the title in "
          "calibre differs from the one on the list:" % len(reconciled))
    for num, title, b in reconciled:
        print("  %2d  %-34s id=%-5s %-34s %s"
              % (num, title[:34], b["id"], (b.get("title") or "")[:34],
                 ", ".join(b.get("authors") or [])))

if locked:
    print("\nDRM-LOCKED (%d) — in the library, but the file cannot be opened "
          "on a PC; a second, DRM-free copy is what is missing:" % len(locked))
    for num, title, hits in sorted(locked):
        for b in hits:
            print("  %2d  %-34s id=%-5s %-30s %s"
                  % (num, title[:34], b["id"], (b.get("title") or "")[:30],
                     ",".join(b.get("formats") or [])))

if stray:
    print("\nTAGGED BUT UNRECOGNISED (%d) — either not on the list, or a gap "
          "in the matching:" % len(stray))
    for b in sorted(stray, key=lambda x: x["id"]):
        print("  id=%-5s %-40s %s" % (b["id"], (b.get("title") or "")[:40],
                                      ", ".join(b.get("authors") or [])))

if TAG:
    print("\n" + "=" * 70)
    books = [b for _, _, hits in found for b in hits]
    books += [b for _, _, b in reconciled]
    print("Tagging %d books with %r (existing tags kept)" % (len(books), TAG))
    ok = skipped = 0
    for b in books:
        tags = list(b.get("tags") or [])
        if TAG in tags:
            skipped += 1
            continue
        tags.append(TAG)
        try:
            call("set_metadata", book_id=b["id"], fields={"tags": tags},
                 library=LIBRARY)
            ok += 1
        except RuntimeError as e:
            print("  ! id=%s %s" % (b["id"], e))
    print("newly tagged: %d   already had it: %d   total: %d"
          % (ok, skipped, len(books)))

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
