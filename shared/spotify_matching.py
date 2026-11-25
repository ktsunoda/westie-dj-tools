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
    r"\s*(?:\((?:feat\.?|ft\.?|featuring|with)\s+([^()]+)\)"
    r"|\[(?:feat\.?|ft\.?|featuring|with)\s+([^\[\]]+)\])", re.I,
)
INLINE_FEATURED = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+", re.I)
ALBUM_LABEL = re.compile(r"\s*(?:\(album version\)|\[album version\]| - album version)$", re.I)
ARTIST_ALIASES = {"ms lauryn hill": "lauryn hill"}


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
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(char for char in folded
                     if not unicodedata.combining(char) and char not in "'’")
    return " ".join(re.sub(r"[^\w]+", " ", folded).split())


def artist_key(name):
    key = tolerant_key(name)
    return ARTIST_ALIASES.get(key, key)


def title_and_credits(title):
    credits = [first or second for first, second in CREDIT_SUFFIX.findall(title)]
    return ALBUM_LABEL.sub("", CREDIT_SUFFIX.sub("", title)).strip(), credits


def recording_key(track):
    return (track.get("external_ids", {}).get("isrc")
            or track.get("linked_from", {}).get("uri") or track["uri"])


def recover_track(api, artist, names, candidates):
    lead, *inline_guests = INLINE_FEATURED.split(artist, maxsplit=1)
    expected_names = {}
    for name in names:
        clean, guests = title_and_credits(name)
        expected_names[tolerant_key(title_key(clean))] = (clean, inline_guests + guests)

    def has_credit(credit, artists):
        if artist_key(credit) in artists:
            return True
        names = CREDIT_SEPARATOR.split(credit)
        return len(names) > 1 and all(artist_key(name) in artists for name in names)

    for clean, _ in expected_names.values():
        if clean in names and lead == artist:
            continue
        result = api("GET", "/search", params={
            "q": f"track:{clean} artist:{lead}", "type": "track", "limit": 10,
        })
        candidates.update((track["uri"], track) for track in result["tracks"]["items"])
    matches = {}
    for track in candidates.values():
        clean, guests = title_and_credits(track["name"])
        expected = expected_names.get(tolerant_key(title_key(clean)))
        if expected is None:
            continue
        credits = {artist_key(entry["name"]) for entry in track["artists"]}
        if has_credit(lead, credits) and all(has_credit(guest, credits)
                                            for guest in (*expected[1], *guests)):
            matches.setdefault(recording_key(track), track["uri"])
    return next(iter(matches.values())) if len(matches) == 1 else None


def find_track(api, artist, title, remix):
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

    fallback = None
    candidates = {}
    alias_matches = {}
    lead_matches = {}
    source_credit = normalized(artist)
    allow_lead_match = lead != artist or bool(featured)
    lead_credit = normalized(lead)
    for index, query in enumerate(dict.fromkeys(queries)):
        results = api(
            "GET", "/search",
            params={"q": query, "type": "track", "limit": 10},
        )["tracks"]["items"]
        for item in results:
            candidates[item["uri"]] = item
            artists = {normalized(entry["name"]) for entry in item["artists"]}
            if normalized(item["name"]) in exact_titles and normalized(artist) in artists:
                return item["uri"]
            if title_key(item["name"]) not in title_keys:
                continue
            if (fallback is None and credits_match(artist, artists)
                    and all(credits_match(name, artists) for name in featured)):
                fallback = item["uri"]
            uri = item["uri"]
            recording = recording_key(item)
            aliases = {alias.strip() for name in artists
                       for alias in re.findall(r"\(([^()]+)\)$", name)}
            if source_credit in aliases and all(credits_match(name, artists) for name in featured):
                alias_matches.setdefault(recording, uri)
            if allow_lead_match and lead_credit in artists:
                lead_matches.setdefault(recording, uri)
        if fallback is not None and index >= exact_query_count - 1:
            return fallback
    if alias_matches:
        return next(iter(alias_matches.values())) if len(alias_matches) == 1 else None
    if len(lead_matches) == 1:
        return next(iter(lead_matches.values()))
    if lead_matches:
        return None
    return recover_track(api, artist, names, candidates)
