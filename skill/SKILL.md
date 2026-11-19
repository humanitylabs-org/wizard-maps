---
name: maps
description: Use when the owner asks where a place is, how to get somewhere, or about his saved places or map regions.
version: 0.1.2
author: Hermes Agent (Oscar, Miguel's agent)
license: GPL-3.0-only
platforms: [linux, macos]
metadata:
  hermes:
    tags: [maps, directions, places, transit]
    related_skills: []
---
# Maps (the owner's own map server)

Tools come through the single Wizard MCP gateway (`mcp__wizard__maps__<tool>`). All data stays on the owner's box.

- `search_places` {q, lat?, lon?, limit?, lang?}: places/addresses in the regions the owner added. Pass lat/lon to
  prefer nearby results (e.g. his current area). Also returns matching saved places.
- `reverse_geocode` {lat, lon}: what is at a point.
- `route` {from, to, mode?, depart_at?}: from/to are `"lat,lon"` or a place name (first search hit is used: prefer
  coordinates from `search_places` when the name is ambiguous). mode car (default), motorbike, bike, walk, transit.
  Transit uses official timetables (`depart_at` ISO with offset, e.g. `2026-11-19T09:00:00+07:00`). No live traffic:
  say so when giving drive times.
- `save_place` {name, lat, lon, address?, note?, list?, id?}: default list "Saved"; a new list name creates it. Reuse the
  returned id to change it (idempotent). `list_places` {list?, q?}, `delete_place` {id}.
- `regions`: what is on the server and build progress (state queued/building/ready/failed, step, pct, message).
- `find_region` {q | lat+lon} then `add_region` {id}: adds a country/state; it builds in the background (minutes for a
  small country, up to an hour for a big one). Ask before adding large regions (disk: roughly 3x the OSM extract size).
  `refresh_region` {id} rebuilds now; `remove_region` {id} only after the owner confirms.

If search or routing says "not ready", check `regions`: the area may not be added or still building. Report exact
errors; never invent addresses, distances or times. Links for the owner: the app opens in Wizard at `?open=maps`.
