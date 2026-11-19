---
wizard: 0.2
id: maps
name: Maps
type: service
category: app
version: 0.1.1
summary: Maps for anywhere in the world on your own server, with search, directions, transit, saved places and offline areas on your phone.
license: GPL-3.0-only
source: https://github.com/humanitylabs-org/wizard-maps
runtime:
  package: source
  kind: runtipi
  config: runtipi/config.json
  compose: runtipi/docker-compose.yml
api:
  port: 8799
  path: /api
  spec: openapi.json
mcp:
  path: /mcp
  port: 8799
  transport: streamable-http
skill: skill/SKILL.md
view:
  path: /
  port: 8799
  source:
    path: web
    build: python3 app/build_view.py
    dist: web
provides:
- maps
- directions
- place-search
system: false
uses: []
needs: []
permissions:
- geolocation
health: /healthz
switches:
  contract: wizard.app-switches/1
  path: /api/switches
---

# Maps

A private Google Maps on your own server. Out of the box you get a world overview map. Add the countries or regions
you care about (Regions); your server then builds, in the background and one at a time, the detailed map, place search
(Photon), directions by car, motorbike, bike and on foot (Valhalla) and public transport from official timetables
(MOTIS, feeds found through the Mobility Database). Progress is shown in the app and to your agent, and every region
refreshes itself monthly (transit weekly).

On the phone: search as you type, long-press to drop a pin, save places to lists, share a location, directions with
alternatives, your GPS position with follow-me, and "Download for offline" to keep the area you're looking at on the
phone (with your saved places) for use without a connection.

Server cost, measured for Thailand: about 4 GB of disk and 1.5 GB of RAM while serving; a build takes 15-25 minutes on a
busy box. Engines run with memory and CPU caps and low priority. Map data © OpenStreetMap contributors (ODbL); basemap by
Protomaps; transit timetables under each feed's own licence (shown in the app's About sheet).
