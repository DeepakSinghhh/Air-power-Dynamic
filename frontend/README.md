# VAYU-SARTHI UI

React + TypeScript + deck.gl front end for the engine. It is fully offline: the basemap is bundled
GeoJSON (Natural Earth, India point of view), fonts are system fonts, and there are no map-tile
or CDN requests, so it runs on an air-gapped laptop at the venue.

## Run

```bash
# Demo mode: one process serves API + UI at http://127.0.0.1:8000
cd frontend && npm install && npm run build
cd ../engine && pip install -e ".[api]" && uvicorn sarthi.api:app

# Dev mode: hot reload at http://127.0.0.1:5173 (proxies /api to :8000)
cd engine && uvicorn sarthi.api:app --reload      # terminal 1
cd frontend && npm run dev                         # terminal 2
```

Demo video (backup for the live demo): with the server running, `npm run demo-video` records a captioned ~4-minute
walkthrough of docs/PLAN.md section 11 to `e2e/video/demo.mp4` (Playwright + ffmpeg; everything solves live).

Checks: `npm run typecheck`, `npm run build`, and with the server running `npm run e2e`
(Playwright: plan → what would it take → propose → reject → copilot (starter question, what-if → proposal → reject) →
COA compare → adopt Min risk → approve → intent persists → robustness → ground spares → approve →
what-if aircraft lost → reject → fog forecast → propose closures →
approve → no duplicate closures → threshold change → time-sensitive target → drop SAM + threat surface → base fog
card → playback → flood-relief scenario → breach rescue → approve → thunderstorm cell; fails on any console error and
saves a failure screenshot).

## Screens

**Common operational picture (map)**
- Offline basemap with India's boundary as officially depicted; rivers, graticule, reference cities.
- Bases as airfield symbols in reserved status colours (open / closure ahead / closed) with an icon and label, plus serviceable counts.
- SAM envelopes shaded by Pk, with a dashed **intel-age halo** showing where a mobile SAM may have moved. There is an optional **threat surface** heatmap.
- Routes and objectives coloured by mission family (counter-air, offensive, support). They are validated colour-blind-safe for map use, and every marker also carries a text label.
- Tanker tracks and orbits; CAP/AEW/ISR orbits; aircraft drawn as heading arrowheads that move with the timeline cursor.
- Pending retask: changed routes appear as dashed ghosts of the old plan. New threats are labelled NEW.
- Click to select; hover for tooltips. **Inject event → Drop a SAM** lets you place a threat anywhere.
- Labels are decluttered by priority (bases > focus > mission priority > threats > cities).

**Synchronisation matrix (timeline)**
- *Missions* view: TOT window band, package bar (ingress | on station | egress), TOT diamond,
  SEAD → strike sequencing arrows, tanker support ticks, unplanned rows.
- *Aircraft* view: lanes grouped by base, with phases, hatched turnaround, base closures hatched across the
  base's lanes, U/S aircraft, tanker sorties.
- NOW line (missions launched before it are frozen), draggable view-time cursor that drives the map, play/speed, zoom.
- Pending retask: old sorties dashed, new ones outlined green, change badges on rows.

**Weather (fog forecast)**
- *Weather* panel: P(visibility < 1 km) per base per hour from the MOS fog model. Choose the source (cached
  real dense-fog night, works offline / live Open-Meteo) and the commander's risk threshold (≥30/50/70%), then
  *Propose closures*, which go through the normal approve/reject flow. Already-closed windows are not re-proposed.
- Observed METAR is shown under the bars where a nearby station reported (white = fog, grey = clear, no tick = no report).
- Base card: larger fog chart with threshold. Timeline aircraft view: blue P(fog) cells in each base's header row.
- Model skill on a held-out winter is printed in the panel (Brier skill +44%, AUC 0.91; fog hours detected 70% vs 26% from raw model visibility).

**Courses of action**
- The *Intent* chip in the top bar opens the COA comparison: the current situation planned under three commander's
  intents (Max effect / Min risk / Defensive posture), solved in parallel in ~6 s.
- A scatter of mission fulfilment against expected aircraft losses (up and to the left is better; current intent ringed).
- One computed sentence per COA states the trade against the current intent, e.g. *"Min risk gives up 17.4 pts of
  effect (6 missions); in return it cuts expected losses 70%…"*. Below that is a full table with "best" tags.
- **Adopt** creates a normal proposal (diff, KPI deltas). Nothing changes until you approve it. The approved intent then
  applies to every later retask, and the base cards show the alert reserve it implies.

**Robustness**
- *Robustness ▸* runs the committed plan through 2,000 simulated days with no replanning. Aircraft can be
  unserviceable at start-up or lost before the target, tankers can fail to turn up, and a strike aborts if its SEAD
  failed. The mean equals the Expected value tile.
- Distribution of mission success: the current plan (grey bars) vs with ground spares (blue line), with p05 marked.
  Mean, p05, p50 and p95 are shown before and after.
- *What fails most* gives each mission's failure share and its first cause. *Single points of failure* lists assets
  whose loss fails the most value (SEAD lost → strike lost); **What if?** turns that into a retask proposal.
- **Propose ground spares** holds idle aircraft as spares (0 flying changes). In the aircraft view a spare is a dashed bar
  with its stand-by start-up filled. The mission card lists its spares, and the Sorties tile shows the spare count.

**Flood relief (HADR)**
- *Scenario ▾ → Flood relief (HADR)* plans a monsoon flood day in Assam and Bihar with the same engine. The map refits
  to the theatre. The last KPI tile becomes *Relief lifted* (tonnes planned of tonnes requested). The COA and fog
  panels are hidden (no adversary; the fog model is for north Indian winters).
- Thunderstorm cells are dashed circles that routes avoid. *Inject event → Draw a thunderstorm cell* places one, and the
  HADR presets add a breach rescue, rain at the busiest airfield, a cell on a route, helicopters U/S and a cancelled
  road convoy. Mission cards show the landing constraint (runway length, or helicopters only).

**Copilot** (left panel tab, or press `/`): ask in plain language, for example *why isn't STK-06 planned?*, *can we
squeeze it in?*, *what if Halwara fogs in between 0500 and 0930?*, *how robust is the plan?* or *readiness at Jodhpur*.
- **How it answers:** only from the engine. Each answer shows how it was routed (parser, or the local model if one is
  configured) and which engine tools produced it, plus follow-up buttons (ask, show on map, open a panel, propose).
- **Changes to the plan:** what-if questions come back as a normal proposal for approval on the right. The copilot
  won't stack a second proposal on a pending one.

**Readiness board** (timeline tab *Readiness*): one row per base, at the view time. It shows:
- aircraft serviceable and tasked by type, mean P(serviceable), ground spares and the alert reserve;
- crews fit now, plus a 24-hour strip of crews fit per hour (fatigue model and night currency);
- weapons left after the plan (▲ when low) and closures.

Above the table, a badge for each data feed says how old it is (● fresh, ▲ stale, ✕ old). Events refresh the feed
they come from; for example, a maintenance alert refreshes *Maintenance status*. Click a row to open the base card.

**What would it take?** On an unplanned mission's card, *Find what gets … planned* tries single relaxations in
parallel: accept more route risk, raise to P10, widen the TOT window, resupply weapons. Each comes back with its
cost (aircraft changed, missions dropped, fulfilment before/after) and a **Propose** button.

**Retask workflow (human in the loop)**: Inject event → proposal (diff, *naive re-plan would change N*,
KPI deltas vs current plan) → **Approve & issue** or **Reject**. Every decision is logged.
Detail cards for missions, bases, threats and aircraft include "what-if" actions (close a base,
ground an aircraft, raise priority, cancel mission) that go through the same proposal flow.

## Structure

| File | Purpose |
|---|---|
| `src/store.ts` | Zustand store: committed state, pending proposal, selection, view time, layers |
| `src/api.ts`, `src/types.ts` | Typed client for the engine's `/api` |
| `src/theme.ts` | Colour tokens: validated mission-family palette, reserved status colours |
| `src/util.ts` | Time formatting, geometry, aircraft position along route at time t |
| `src/components/MapView.tsx` | deck.gl COP map |
| `src/components/Timeline.tsx` | SVG synchronisation matrix |
| `src/components/TopBar.tsx` | KPI tiles, event injection, scenario menu |
| `src/components/SidePanel.tsx` | Proposal review, detail cards, decision log |
| `src/components/MissionList.tsx` | Missions by priority |
| `src/components/Weather.tsx` | Fog forecast panel and the reusable P(fog) chart |
| `src/components/CoaPanel.tsx` | Courses-of-action comparison (scatter, trade sentences, table, adopt) |
| `src/components/Copilot.tsx` | Copilot chat: grounded answers, router and tool labels, follow-up actions |
| `src/components/Readiness.tsx` | Readiness board and data-feed freshness |
| `src/components/RobustPanel.tsx` | Robustness: outcome distribution, fragile missions, single points of failure, ground spares |
| `e2e/smoke.mjs` | Browser end-to-end test with screenshots |
| `e2e/demo.mjs` | Captioned demo walkthrough recorded to video |

The basemap is rebuilt from Natural Earth with `python -I tools/build_basemap.py` (see that file).
