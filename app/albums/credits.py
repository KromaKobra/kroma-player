"""Album credits ranked consistently for detail pages and collection summaries."""


def contributing_artists(rows, total: int) -> list[dict]:
    seen: dict[int, dict] = {}
    for row in rows:
        artist_id = row["artist_id"]
        if artist_id not in seen:
            seen[artist_id] = {**row, "track_count": 0}
        seen[artist_id]["track_count"] += 1
    ordered = sorted(seen.values(), key=lambda row: row["track_count"], reverse=True)
    for row in ordered:
        row["is_main"] = row["track_count"] * 2 > total
    return ordered


def primary_artists(credits: list[dict]) -> list[dict]:
    """Majority contributors, or every contributor tied for the largest share."""
    main = [row for row in credits if row["is_main"]]
    if main or not credits:
        return main
    return [row for row in credits if row["track_count"] == credits[0]["track_count"]]
