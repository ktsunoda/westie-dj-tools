"""Match VirtualDJ plays to Spotify catalog tracks.

Adapted from Westie Radio at c10db79 and DJ Set Processor at b4c99ab.
Copyright (c) 2026 Koichi Tsunoda. Licensed under MIT.
"""

import re
import unicodedata

FEATURED = re.compile(r"\s*\((?:feat\.?|ft\.?|featuring)\s+([^()]+)\)", re.I)
CREDIT_SEPARATOR = re.compile(r"\s*[,;]\s*|\s+(?:&|and|x)\s+", re.I)
VERSION_SUFFIX = re.compile(
    r"\s*[\[(]([^()[\]]*\b(?:mix|remix|edit|version|live|acoustic|stripped|reimagined)\b"
    r"[^()[\]]*)[\])]$",
    re.I,
)
TRACKING_ROOM = re.compile(
    r"(?:\s*\(nashville['’]?s tracking room studios\)|"
    r"\s+-\s+recorded at the tracking room nashville)$", re.I
)

CREDIT_SUFFIX = re.compile(
    r"\s*(?:\((?:feat\.|ft\.|(?:feat|ft|featuring|with|duet with)\s+)\s*([^()]+)\)"
    r"|\[(?:feat\.|ft\.|(?:feat|ft|featuring|with|duet with)\s+)\s*([^\[\]]+)\])", re.I,
)
VERSUS_CREDIT = re.compile(r"\s*\(([^()]+\s+vs\.?\s+[^()]+)\)$", re.I)
INLINE_FEATURED = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+", re.I)
ALBUM_LABEL = re.compile(r"\s*(?:\(album version\)|\[album version\]| - album version)$", re.I)
SOUNDTRACK_LABEL = re.compile(
    r'\s*(?:[\[(]|\s+-\s+)'
    r'(?:original song\s+)?from\s*(?:the\s+)?'
    r'(?:original motion picture\s+|dreamworks animation[\'’]s\s+)?'
    r'[“"”].+[“"”]\s*/?\s*(?:soundtrack(?:\s+version)?|an hbo original series)?[\])]?$',
    re.I,
)
COVER_LABEL = re.compile(r"\s*[\[(](?:[^()[\]]+\s+)?cover[\])]$", re.I)
RELEASE_LABEL = re.compile(
    r"\s*(?:[\[(]|\s+-\s+)(?:single version|(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?)"
    r"(?:[\])]$|$)", re.I,
)
EDIT_LABEL = re.compile(r"\s*(?:[\[(]|\s+-\s+)(?:radio|short) edit[\])]?$", re.I)
CONTENT_LABEL = re.compile(
    r"\s*(?:[\[(]|\s+-\s+)(clean(?: version)?|explicit(?: version)?)"
    r"(?:[\])]$|$)", re.I,
)
COVER_VERSION = re.compile(
    r"\b(?:acoustic|live|remix|mix|edit|version|instrumental|karaoke|clean|explicit|"
    r"stripped|remaster(?:ed)?|unplugged|demo)\b", re.I,
)
LIVE_LOCATION = re.compile(
    r"\s+(?:live (?:from|at)|recorded at)\s+(?:the\s+)?"
    r"(spotify studios\b.*|late show with\b.*)$", re.I,
)
QUERY_SUFFIX = re.compile(r"\s*(?:\([^()]*\)|\[[^\[\]]*\]|\s+-\s+[^-]+)$")
RECOVERY_WORDS = {
    "one": "1", "two": "2", "ii": "2", "three": "3", "u": "you",
}
CATALOG_ARTIST_ALIASES = {
    "generation boys band": ("Boys Band Generation", "5ADNCw3WJaQXYaINuHP2gN"),
    "grace gaustad": ("Gaustad", "5MZxzpsn1zTTkiYwQ9Qz9b"),
    "kenny blues boss wayne": ('Kenny "Blue Boss" Wayne', "1X7dEf0PnxVCeaIfMXyanb"),
    "lostboycrow": ("Christopher Blair", "5PxCTrv3Y1xVACfngpt7D2"),
    "madilyn": ("Madilyn Bailey", "0NxhAEPOSeCg6vypFr7yjU"),
    "martha and the vandellas": ("Martha Reeves & The Vandellas", "1Pe5hlKMCTULjosqZ6KanP"),
    "mikolas josef": ("MIKOLAS", "6Kz7MXIUFMtp5Ts91lagtR"),
    "q": ("Q Marsden", "22vqVhfPJab5kkU9txDGX3"),
    "scott bradlees postmodern jukebox": ("Postmodern Jukebox", "5HYNPEO2NNBONQkp3Mvwvc"),
}
TITLE_ALIASES = {
    ("britney spears", "one more time"): "...Baby One More Time",
    ("nath brooks", "redbone"): "Redbone (Stay Woke)",
    ("syml", "dim recorded at st marks cathedral"): "Dim - Recorded Live at St. Mark's Cathedral, Seattle",
    ("syml", "fear of water"): "Fear of the Water",
    ("the script", "breakeven falling to pieces"): "Breakeven",
    ("the temptations", "aint to proud to beg"): "Ain't Too Proud To Beg",
}
# Exact source credits for recordings verified against a named release.
# These are fallbacks for title search ambiguity, not alternate versions.
VERIFIED_RECORDINGS = {
    ("aretha franklin", "baby i love you"): "USAT20801258",
    ("aretha franklin", "swanee remastered"): "USSM10600759",
    ("arlene zelina", "together again"): "TCAFS2104639",
    ("bob seger the silver bullet band", "old time rock and roll"): "USCA28600270",
    ("britney spears", "one more time"): "USJI19810404",
    ("c c music factory", "gonna make you sweat everybody dance now"): "USSM19000533",
    ("eva cassidy", "wade in the water"): "USA449845002",
    ("frank sinatra", "fly me to the moon feat count basie and his orchestra"): "USRH10800836",
    ("john mayer", "free fallin live"): "USSM10801204",
    ("julia sheer", "misery acoustic version feat eppic"): "GBDMT1200277",
}
ARTIST_ALIASES = {
    "jj grey and mofro": "jj grey mofro",
    "kool mo dee": "kool moe dee",
    "cee lo green": "ceelo green",
    "black eyed peas": "the black eyed peas",
    "isaiah": "isaiah firebrace",
    "janet": "janet jackson",
    "ke ha": "kesha",
    "keb mo": "kebmo",
    "ms lauryn hill": "lauryn hill",
    "ol db": "ol dirty bastard",
    "t mills": "travis mills",
}


def normalized(value):
    return " ".join(value.casefold().split())


def title_key(title):
    # Spotify uses both parentheses and a dash around the same version label.
    title = FEATURED.sub("", title)
    title = VERSION_SUFFIX.sub(r" - \1", title)
    return normalized(TRACKING_ROOM.sub(" - tracking room nashville", title))


def credits_match(credit, artists):
    if normalized(credit) in artists:
        return True
    names = [normalized(name) for name in CREDIT_SEPARATOR.split(credit)]
    return len(names) > 1 and all(name in artists for name in names)


def tolerant_key(text):
    folded = unicodedata.normalize("NFKD", text.casefold().replace("®", "").replace("™", ""))
    folded = "".join(char for char in folded
                     if not unicodedata.combining(char) and char not in "'’")
    return " ".join(re.sub(r"[^\w]+", " ", folded).split())


def artist_key(name):
    key = tolerant_key(name)
    if re.fullmatch(r"(?:[a-z] ){2,}[a-z]", key):
        key = key.replace(" ", "")
    return ARTIST_ALIASES.get(key, key)


def title_and_credits(title):
    credits = [first or second for first, second in CREDIT_SUFFIX.findall(title)]
    versus = VERSUS_CREDIT.search(title)
    if versus:
        credits.extend(re.split(r"\s+vs\.?\s+", versus[1], flags=re.I))
        title = title[:versus.start()]
    clean, *inline = INLINE_FEATURED.split(CREDIT_SUFFIX.sub("", title), maxsplit=1)
    clean = re.sub(r"\s+-\s+album version(?=\s+-\s+)", "", clean, flags=re.I)
    clean = ALBUM_LABEL.sub("", clean).strip()
    return clean, credits + inline


def recovery_title_key(title):
    without_soundtrack = SOUNDTRACK_LABEL.sub("", title).strip()
    return tolerant_key(title_key(RELEASE_LABEL.sub("", without_soundtrack)).replace("&", " and "))


def equivalent_title_key(title):
    key = recovery_title_key(title)
    words = LIVE_LOCATION.sub(r" live \1", key).split()
    if "ii" in words and "u" in words:
        words = ["to" if word == "ii" else word for word in words]
    return " ".join(RECOVERY_WORDS.get(word, word) for word in words)


def has_recovery_credit(credit, artists):
    if artist_key(credit) in artists:
        return True
    names = re.split(r"\s+with\s+", credit, flags=re.I)
    names = [name for part in names for name in CREDIT_SEPARATOR.split(part)]
    if len(names) > 1 and all(artist_key(name) in artists for name in names):
        return True
    # Some duo credits use one surname for both artists.
    if len(names) == 2 and len(names[0].split()) == 1 and len(names[1].split()) == 2:
        surname = names[1].split()[-1]
        return artist_key(names[1]) in artists and artist_key(f"{names[0]} {surname}") in artists
    return False


def recording_key(track):
    return (track.get("external_ids", {}).get("isrc")
            or track.get("linked_from", {}).get("uri") or track["uri"])


def is_clean_counterpart(clean, explicit):
    if clean.get("explicit") is not False or explicit.get("explicit") is not True:
        return False
    if not clean.get("duration_ms") or not explicit.get("duration_ms"):
        return False
    delta = abs(clean["duration_ms"] - explicit["duration_ms"])
    if delta and (delta > 250 or not clean.get("album", {}).get("name")
                  or clean["album"]["name"] != explicit.get("album", {}).get("name")):
        return False
    if title_key(clean["name"]) != title_key(explicit["name"]):
        return False
    return ([a.get("id") or artist_key(a["name"]) for a in clean["artists"]]
            == [a.get("id") or artist_key(a["name"]) for a in explicit["artists"]])


def choose_recording(matches, duration_ms=None):
    if len(matches) == 1:
        return next(iter(matches.values()))["uri"]
    if len(matches) == 2:
        first, second = matches.values()
        if is_clean_counterpart(first, second):
            return first["uri"]
        if is_clean_counterpart(second, first):
            return second["uri"]
    if duration_ms is not None:
        for tolerance in (500, 2000):
            close = [track for track in matches.values() if track.get("duration_ms")
                     and abs(track["duration_ms"] - duration_ms) <= tolerance]
            if len(close) == 1:
                return close[0]["uri"]
            if close:
                break
    return None


def prefer_clean(track, candidates):
    if track.get("explicit") is True:
        for candidate in candidates:
            if is_clean_counterpart(candidate, track):
                return candidate["uri"]
    return track["uri"]


def recover_track(search, artist, names, candidates, duration_ms):
    lead, *inline_guests = INLINE_FEATURED.split(artist, maxsplit=1)
    expected = {}
    expected_edit = {}
    queries = []
    for name in names:
        cover = COVER_LABEL.search(name)
        lookup_name = name[:cover.start()].strip() if cover and not COVER_VERSION.search(cover[0]) else name
        clean, guests = title_and_credits(lookup_name)
        content = CONTENT_LABEL.search(clean)
        explicit = None
        if content:
            explicit = content[1].casefold().startswith("explicit")
            clean = clean[:content.start()].strip()
        catalog_title = TITLE_ALIASES.get((artist_key(lead), tolerant_key(clean)))
        if catalog_title:
            clean = catalog_title
        source = (clean, inline_guests + guests, lookup_name != name, explicit)
        expected.setdefault(equivalent_title_key(clean), []).append(source)
        core = EDIT_LABEL.sub("", clean).strip()
        expected_edit.setdefault(equivalent_title_key(core), []).append(source)
        if core != clean:
            queries.append(f"{lead} {core}")
        if clean != name or lead != artist:
            queries.append(f"track:{clean} artist:{lead}")
        shorter = RELEASE_LABEL.sub("", SOUNDTRACK_LABEL.sub("", clean)).strip()
        if shorter != clean:
            queries.append(f"track:{shorter} artist:{lead}")
        key = recovery_title_key(clean)
        equivalent = equivalent_title_key(clean)
        first_artist = CREDIT_SEPARATOR.split(lead, maxsplit=1)[0]
        if lookup_name != name or key != equivalent:
            query_title = clean if lookup_name != name else equivalent
            queries.append(f"{query_title} {first_artist}")
        elif guests or inline_guests:
            core = QUERY_SUFFIX.sub("", clean).strip() or clean
            queries.append(" ".join((first_artist, *inline_guests, *guests, core)))
        catalog_alias = CATALOG_ARTIST_ALIASES.get(artist_key(lead))
        if catalog_alias:
            catalog_artist, _ = catalog_alias
            queries.append(f"{catalog_artist} {clean}")

    for query in dict.fromkeys(queries):
        for track in search(query):
            candidates[track["uri"]] = track

    full_title_matches = {}
    shortened_title_matches = {}
    edit_matches = {}
    for track in candidates.values():
        clean, guests = title_and_credits(track["name"])
        sources = expected.get(equivalent_title_key(clean))
        alternate_edit = not sources
        if alternate_edit:
            core = EDIT_LABEL.sub("", clean).strip()
            sources = [
                source for source in expected_edit.get(equivalent_title_key(core), ())
                if bool(EDIT_LABEL.search(source[0])) != (core != clean)
            ]
        if not sources:
            continue
        credits = {artist_key(entry["name"]) for entry in track["artists"]}
        artist_ids = {entry.get("id") for entry in track["artists"]}
        credits.update(
            alias for alias, (_, artist_id) in CATALOG_ARTIST_ALIASES.items()
            if artist_id in artist_ids
        )
        if not has_recovery_credit(lead, credits):
            continue
        full_title = tolerant_key(title_key(clean))
        for source_title, source_guests, is_cover, explicit in sources:
            source_release = RELEASE_LABEL.search(source_title)
            if source_release and re.search(r"\d{4}", source_release[0]):
                track_release = RELEASE_LABEL.search(clean)
                if not track_release or re.findall(r"\d{4}", source_release[0]) != re.findall(r"\d{4}", track_release[0]):
                    continue
            if explicit is not None and track.get("explicit") is not explicit:
                continue
            if is_cover and artist_key(track["artists"][0]["name"]) != artist_key(lead):
                continue
            if all(has_recovery_credit(guest, credits) for guest in (*source_guests, *guests)):
                if alternate_edit:
                    matches = edit_matches
                elif full_title == tolerant_key(title_key(source_title)) or (
                    source_release and "remaster" in source_release[0].casefold()
                    and (track_release := RELEASE_LABEL.search(clean))
                    and "remaster" in track_release[0].casefold()
                ):
                    matches = full_title_matches
                else:
                    matches = shortened_title_matches
                matches.setdefault(recording_key(track), track)
    matches = full_title_matches or shortened_title_matches or edit_matches
    return choose_recording(matches, duration_ms)


def recover_verified_recording(search, artist, title):
    isrc = VERIFIED_RECORDINGS.get((artist_key(artist), tolerant_key(title)))
    if not isrc:
        return None
    for track in search(f"isrc:{isrc}"):
        if (track.get("external_ids", {}).get("isrc") == isrc
                and track.get("is_playable") is not False
                and track.get("available_markets", True)
                and not track.get("restrictions")):
            return track["uri"]
    return None


def find_track(api, artist, title, remix, *, duration_ms=None):
    """Return a matching Spotify track URI, or None if no match is found.

    The caller supplies an authenticated api(method, path, **kwargs) function.
    Pass an empty string when the source has no separate remix field.
    """
    # VirtualDJ can put the same version in both title and remix fields.
    if remix:
        base = re.sub(
            rf"(?:\s*\({re.escape(remix)}\)|\s*\[{re.escape(remix)}\]|"
            rf"\s+-\s+{re.escape(remix)})$", "", title, flags=re.I
        ).rstrip()
        names = tuple(dict.fromkeys((f"{base} ({remix})", f"{base} - {remix}")))
        if base != title:
            names = (title, *[name for name in names if name != title])
    else:
        names = (title,)
    exact_titles = {normalized(name) for name in names}
    title_keys = {title_key(name) for name in names}
    featured = FEATURED.findall(title)
    queries = [f"track:{name} artist:{artist}" for name in names]
    exact_query_count = len(queries)
    lead = CREDIT_SEPARATOR.split(artist, maxsplit=1)[0]
    for name in names:
        clean = FEATURED.sub("", name)
        queries.extend((f"track:{clean} artist:{artist}", f"track:{clean} artist:{lead}"))
    queries.extend(f"{FEATURED.sub('', name)} {artist}" for name in names)

    search_results = {}

    def search(query):
        if query not in search_results:
            search_results[query] = api(
                "GET", "/search", params={"q": query, "type": "track", "limit": 10},
            )["tracks"]["items"]
        return search_results[query]

    fallback = None
    candidates = {}
    alias_matches = {}
    lead_matches = {}
    source_credit = normalized(artist)
    allow_lead_match = lead != artist or bool(featured)
    lead_credit = normalized(lead)
    for index, query in enumerate(dict.fromkeys(queries)):
        items = search(query)
        for item in items:
            candidates[item["uri"]] = item
            artists = {normalized(entry["name"]) for entry in item["artists"]}
            if normalized(item["name"]) in exact_titles and normalized(artist) in artists:
                return prefer_clean(item, items)
            if title_key(item["name"]) not in title_keys:
                continue
            if (fallback is None and credits_match(artist, artists)
                    and all(credits_match(name, artists) for name in featured)):
                fallback = item
            recording = recording_key(item)
            aliases = {alias.strip() for name in artists
                       for alias in re.findall(r"\(([^()]+)\)$", name)}
            if source_credit in aliases and all(credits_match(name, artists) for name in featured):
                alias_matches.setdefault(recording, item)
            if allow_lead_match and lead_credit in artists:
                lead_matches.setdefault(recording, item)
        if fallback is not None and index >= exact_query_count - 1:
            return prefer_clean(fallback, candidates.values())
    if alias_matches or lead_matches:
        matched = choose_recording(alias_matches or lead_matches, duration_ms)
    else:
        matched = recover_track(search, artist, names, candidates, duration_ms)
    if matched or remix:
        return matched
    return recover_verified_recording(search, artist, title)
