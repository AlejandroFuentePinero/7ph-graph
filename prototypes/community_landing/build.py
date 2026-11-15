"""PROTOTYPE (issue #339): the community landing page, two variants, throwaway.

Reads the live graph and writes one self-contained ``index.html`` beside this
file. Switch variants with ``?variant=A`` or ``?variant=B``, or the arrow keys.

Run from the main checkout, so ``graph7ph`` and ``data/graph`` resolve:

    uv run python ../graph-7ph-339/prototypes/community_landing/build.py
    python3 -m http.server 8339 -d ../graph-7ph-339/prototypes/community_landing

Not production code: no tests, no error handling, numbers computed here rather
than by the build's figures.
"""

import base64
import json
from collections import defaultdict
from pathlib import Path

from graph7ph import db, trends

HERE = Path(__file__).parent
FONTS = Path("src/graph7ph/assets/fonts")
conn = db.open_for_reading(Path("data/graph"))


def rows(q, p=None):
    r = conn.execute(q, p or {})
    out = []
    while r.has_next():
        out.append(r.get_next())
    return out


events = {
    e: {"type": t, "field": f, "imputed": imp is not None, "year": y}
    for e, t, f, imp, y in rows(
        "MATCH (e:Event)-[:IN_YEAR]->(y:Year) "
        "RETURN e.event, e.eventType, e.fieldSize, e.fieldImputed, y.year"
    )
}
# Ladybug nulls a min/max that follows a DISTINCT aggregate, so each is its own query.
for e, lo in rows("MATCH (d:Deck)-[:PLAYED_AT]->(e:Event) RETURN e.event, min(d.createdAt)"):
    events[e]["date"] = lo
for e, n in rows("MATCH (d:Deck)-[:PLAYED_AT]->(e:Event) RETURN e.event, count(d)"):
    events[e]["decks"] = n
for e, n in rows(
    "MATCH (p:Pilot)<-[:PILOTED_BY]-(:Deck)-[:PLAYED_AT]->(e:Event) RETURN e.event, count(DISTINCT p)"
):
    events[e]["pilots"] = n

# A major is the leaderboard's: a field over the cut that published its field.
cut_only = set(trends._cut_only_events(conn))
for e, v in events.items():
    v["major"] = v["field"] is not None and v["field"] > trends.MAJOR_FIELD_SIZE and e not in cut_only
    # People, not ranking slots: a Teams field counts teams, so read its pilots.
    v["players"] = v["pilots"] if v["type"] == "Teams" else v["field"]

seen_at = defaultdict(list)
for p, e in rows(
    "MATCH (p:Pilot)<-[:PILOTED_BY]-(:Deck)-[:PLAYED_AT]->(e:Event) RETURN DISTINCT p.pilot, e.event"
):
    seen_at[p].append(e)

years = sorted({v["year"] for v in events.values()})
played = {p: {events[e]["year"] for e in es} for p, es in seen_at.items()}
first_year = {p: min(ys) for p, ys in played.items()}
first_date = sorted(min(events[e]["date"] for e in es) for es in seen_at.values())

cohorts = [
    {
        "year": y,
        "total": sum(1 for ys in played.values() if y in ys),
        "by": {c: sum(1 for p, ys in played.items() if y in ys and first_year[p] == c)
               for c in years if c <= y},
    }
    for y in years
]

# The cumulative line: one step per day a pilot was first recorded.
curve, n = [], 0
for d in first_date:
    n += 1
    stamp = int(d.timestamp() * 1000)
    if curve and curve[-1][0] == stamp:
        curve[-1][1] = n
    else:
        curve.append([stamp, n])

anchor = max(v["date"] for v in events.values())
latest = years[-1]
largest = max(events.items(), key=lambda kv: kv[1]["players"])
snapshot = sorted(p.name for p in Path("snapshots").iterdir() if p.name[:1].isdigit())[-1]

data = {
    "anchor": int(anchor.timestamp() * 1000),
    "anchorLabel": f"{anchor.day} {anchor:%b}",
    "updated": f"{int(snapshot[6:8])} {anchor.replace(month=int(snapshot[4:6])):%b} {snapshot[:4]}",
    "latest": latest,
    "years": years,
    "totals": {
        "pilots": len(seen_at),
        "events": len(events),
        "decks": sum(v["decks"] for v in events.values()),
        "largest": largest[1]["players"],
        "largestName": largest[0],
        "largestYear": largest[1]["year"],
        "majors": sum(1 for v in events.values() if v["major"]),
        "archetypes": rows(
            "MATCH (:Deck)-[h:HAS_ARCHETYPE]->(a:Archetype) WHERE h.isPrimary RETURN count(DISTINCT a)"
        )[0][0],
        "cards": rows("MATCH (c:Card)<-[:CONTAINS]-(:Deck) RETURN count(DISTINCT c)")[0][0],
    },
    "thisYear": {
        "pilots": cohorts[-1]["total"],
        "events": sum(1 for v in events.values() if v["year"] == latest),
        "decks": sum(v["decks"] for v in events.values() if v["year"] == latest),
        "returning": cohorts[-1]["total"] - cohorts[-1]["by"][latest],
        "archetypes": rows(
            "MATCH (:Year {year:$y})<-[:IN_YEAR]-(:Event)<-[:PLAYED_AT]-(:Deck)-[h:HAS_ARCHETYPE]->(a:Archetype) "
            "WHERE h.isPrimary RETURN count(DISTINCT a)", {"y": latest})[0][0],
        "cards": rows(
            "MATCH (:Year {year:$y})<-[:IN_YEAR]-(:Event)<-[:PLAYED_AT]-(:Deck)-[:CONTAINS]->(c:Card) "
            "RETURN count(DISTINCT c)", {"y": latest})[0][0],
    },
    "everyYear": sum(1 for ys in played.values() if ys == set(years)),
    "events": sorted(
        (
            {
                "t": int(v["date"].timestamp() * 1000),
                "n": v["players"],
                "y": v["year"],
                "major": v["major"],
                # Only a major carries its name onto the page.
                "name": e if v["major"] else None,
                "imputed": v["imputed"],
            }
            for e, v in events.items()
        ),
        key=lambda d: d["t"],
    ),
    "cohorts": cohorts,
    "curve": curve,
}


def font(name):
    return base64.b64encode((FONTS / name).read_bytes()).decode()


html = (HERE / "template.html").read_text()
html = html.replace("__DATA__", json.dumps(data, separators=(",", ":")))
html = html.replace("__FRAUNCES__", font("fraunces-600.woff2"))
html = html.replace("__HANKEN__", font("hanken.woff2"))
(HERE / "index.html").write_text(html)
print(json.dumps({k: data[k] for k in ("totals", "thisYear", "everyYear", "cohorts", "updated", "anchorLabel")}, indent=1))
print("events", len(data["events"]), "majors named", sum(1 for e in data["events"] if e["name"]), "curve points", len(curve))
