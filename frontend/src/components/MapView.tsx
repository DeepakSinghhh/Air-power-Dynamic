import { COORDINATE_SYSTEM, WebMercatorViewport, type MapViewState, type PickingInfo } from '@deck.gl/core'
import { PathStyleExtension } from '@deck.gl/extensions'
import {
  BitmapLayer,
  GeoJsonLayer,
  IconLayer,
  PathLayer,
  PolygonLayer,
  ScatterplotLayer,
  TextLayer,
} from '@deck.gl/layers'
import DeckGL from '@deck.gl/react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { eventTime, useStore, useView, type View } from '../store'
import { C, FAMILY_COLOR, FAMILY_LABEL, ROLE_FAMILY, STATUS_COLOR, STATUS_ICON, STATUS_LABEL, rgba, roleColor, type BaseStatus, type Family, type RGBA } from '../theme'
import type { Base, Hazard, Mission, Threat } from '../types'
import {
  baseStatus,
  circle,
  flightAt,
  fmtDur,
  fmtPct,
  fmtTime,
  routePath,
  serviceableAt,
  type Flight,
  type LonLat,
} from '../util'

// ---------- static basemap (offline, India point of view) ----------

type FC = { type: 'FeatureCollection'; features: { properties: Record<string, unknown> }[] }
let basemapPromise: Promise<{ countries: FC; rivers: FC }> | null = null
function loadBasemap() {
  basemapPromise ??= Promise.all([
    fetch('/geo/countries.json').then((r) => r.json()),
    fetch('/geo/rivers.json').then((r) => r.json()),
  ]).then(([countries, rivers]) => ({ countries, rivers }))
  return basemapPromise
}

const CITIES = [
  { name: 'New Delhi', lon: 77.21, lat: 28.61 },
  { name: 'Jaipur', lon: 75.79, lat: 26.91 },
  { name: 'Amritsar', lon: 74.87, lat: 31.63 },
  { name: 'Jammu', lon: 74.86, lat: 32.73 },
  { name: 'Lucknow', lon: 80.95, lat: 26.85 },
]

function graticule() {
  const lines: { path: LonLat[] }[] = []
  for (let lon = 60; lon <= 95; lon += 2) lines.push({ path: [[lon, 15], [lon, 42]] })
  for (let lat = 16; lat <= 42; lat += 2) lines.push({ path: [[60, lat], [95, lat]] })
  return lines
}
const GRATICULE = graticule()

// ---------- icons (inline SVG, no network) ----------

const svgUrl = (svg: string) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
const BASE_ICONS: Record<BaseStatus, string> = Object.fromEntries(
  (['open', 'closing', 'closed'] as BaseStatus[]).map((s) => [
    s,
    svgUrl(
      `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">
        <rect x="5" y="5" width="30" height="30" rx="7" fill="${STATUS_COLOR[s]}" stroke="${C.page}" stroke-width="4"/>
        <rect x="18" y="10" width="4" height="20" rx="1" fill="${C.page}" transform="rotate(35 20 20)"/>
      </svg>`,
    ),
  ]),
) as Record<BaseStatus, string>

/** Arrowhead track symbol pointing along `heading`, sized in screen pixels at `zoom`. */
function arrowhead(pos: LonLat, heading: number, px: number, zoom: number): LonLat[] {
  const degPerPx = 360 / (512 * 2 ** zoom)
  const h = (heading * Math.PI) / 180
  const cosLat = Math.cos((pos[1] * Math.PI) / 180)
  return [[0, 1.3], [0.85, -0.9], [0, -0.4], [-0.85, -0.9]].map(([x, y]) => {
    const xr = x * Math.cos(h) + y * Math.sin(h)
    const yr = -x * Math.sin(h) + y * Math.cos(h)
    return [pos[0] + xr * px * degPerPx, pos[1] + yr * px * degPerPx * cosLat] as LonLat
  })
}

// ---------- derived data ----------

interface RouteGroup {
  key: string
  mission: Mission
  base: Base
  path: LonLat[]
  km: number
  risk: number
  aircraft: string[]
  needsAar: boolean
}

function routeGroups(view: { world: View['world']; plan: View['plan'] }): RouteGroup[] {
  const out: RouteGroup[] = []
  const { world, plan } = view
  if (!plan) return out
  for (const a of Object.values(plan.assignments)) {
    const m = world.missions[a.mission]
    if (!m) continue
    const byBase = new Map<string, RouteGroup>()
    for (const s of a.sorties) {
      const g = byBase.get(s.base)
      if (g) {
        g.aircraft.push(s.tail)
        continue
      }
      const base = world.bases[s.base]
      byBase.set(s.base, {
        key: `${a.mission}|${s.base}`,
        mission: m,
        base,
        path: routePath(s, m, base),
        km: s.route_km,
        risk: s.risk,
        aircraft: [s.tail],
        needsAar: s.needs_aar,
      })
    }
    out.push(...byBase.values())
  }
  return out
}

function hazardImage(h: Hazard): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.width = h.nlon
  canvas.height = h.nlat
  const ctx = canvas.getContext('2d')!
  const img = ctx.createImageData(h.nlon, h.nlat)
  const raw = atob(h.data)
  const [r, g, b] = rgba(C.critical)
  for (let i = 0; i < h.nlat; i++) {
    const row = h.nlat - 1 - i // data rows run south -> north; canvas rows north -> south
    for (let j = 0; j < h.nlon; j++) {
      const v = raw.charCodeAt(i * h.nlon + j)
      const o = (row * h.nlon + j) * 4
      img.data[o] = r
      img.data[o + 1] = g
      img.data[o + 2] = b
      img.data[o + 3] = v ? Math.round(30 + (v / 255) * 190) : 0
    }
  }
  ctx.putImageData(img, 0, 0)
  return canvas
}

// ---------- tooltip ----------

interface Tip {
  x: number
  y: number
  body: ReactNode
}

function Line({ color, children }: { color?: string; children: ReactNode }) {
  return (
    <div className="line">
      {color && <span className="key" style={{ borderColor: color }} />}
      {children}
    </div>
  )
}

/** Map a pick on a label to the object it labels, so labels behave like their markers. */
function resolvePick(info: PickingInfo): { id: string; o: Record<string, unknown> | undefined } {
  const id = info.layer?.id ?? ''
  const o = info.object as Record<string, unknown> | undefined
  if ((id === 'labels' || id === 'labels-bold') && o) {
    const l = o as unknown as Label
    const target = { base: 'bases', objective: 'objectives', threat: 'threat-sites', zone: '', city: '' }[l.kind]
    return { id: target, o: l.ref as Record<string, unknown> | undefined }
  }
  return { id, o }
}

function tooltipFor(info: PickingInfo, view: View): ReactNode | null {
  const { id, o } = resolvePick(info)
  if (!o) return null
  const { world, plan, envelopes } = view
  if (id === 'bases') {
    const b = o as unknown as Base
    const st = baseStatus(b, useStore.getState().viewTime)
    const sv = serviceableAt(world, b.id)
    return (
      <>
        <div className="big">{b.name}</div>
        <Line color={STATUS_COLOR[st.status]}>{STATUS_ICON[st.status]} {st.text}</Line>
        <div className="sub">{sv.serviceable}/{sv.total} aircraft serviceable · reserve {b.fighter_reserve}</div>
        {Object.keys(b.stocks).length > 0 && (
          <div className="sub">{Object.entries(b.stocks).map(([k, v]) => `${k} ${v}`).join(' · ')}</div>
        )}
      </>
    )
  }
  if (id === 'threat-env' || id === 'threat-sites') {
    const t = o as unknown as Threat
    const env = envelopes[t.id]
    return (
      <>
        <div className="big">Pk {t.pk.toFixed(2)} · {t.radius_km.toFixed(0)} km</div>
        <Line color={C.critical}>{t.id} ({t.kind})</Line>
        {env && env.r_eff_km > t.radius_km + 0.5 && (
          <div className="sub">Intel {fmtDur(env.age_min)} old: may be anywhere within {env.r_eff_km.toFixed(0)} km</div>
        )}
      </>
    )
  }
  if (id === 'routes') {
    const g = o as unknown as RouteGroup
    return (
      <>
        <div className="big">Risk {fmtPct(g.risk, 1)} · {g.km.toFixed(0)} km</div>
        <Line color={roleColor(g.mission.role)}>{g.mission.id} from {g.base.name}</Line>
        <div className="sub">{g.aircraft.join(', ')}{g.needsAar ? ' · needs AAR' : ''}</div>
      </>
    )
  }
  if (id === 'objectives') {
    const m = o as unknown as Mission
    const a = plan?.assignments[m.id]
    return (
      <>
        <div className="big">{a ? `TOT ${fmtTime(a.tot)}` : 'Not planned'}</div>
        <Line color={roleColor(m.role)}>{m.id} · {m.role} · P{m.priority}</Line>
        <div className="sub">{m.label} · window {fmtTime(m.tot_earliest)}-{fmtTime(m.tot_latest)}</div>
        {!a && plan?.unassigned[m.id]?.[0] && <div className="sub">{plan.unassigned[m.id][0]}</div>}
      </>
    )
  }
  if (id === 'aircraft') {
    const f = o as unknown as Flight
    const m = world.missions[f.mission]
    return (
      <>
        <div className="big">{f.tail}</div>
        <Line color={m ? roleColor(f.tanker ? 'AAR' : m.role) : undefined}>
          {f.tanker ? `Tanker for ${f.mission}` : `${f.mission} · ${m?.role}`} · {f.phase}
        </Line>
      </>
    )
  }
  if (id === 'tanker-tracks') {
    const d = o as unknown as { tail: string; mission: string }
    return <Line color={FAMILY_COLOR.support}>AAR track · {d.tail} supporting {d.mission}</Line>
  }
  return null
}

// ---------- component ----------

const dash = new PathStyleExtension({ dash: true })
// ---------- labels: greedy decluttering by priority ----------
// Overlap depends only on zoom (panning shifts every label equally), so this runs once per zoom step.
const LABEL_PRIORITY = { base: 800, focus: 700, mission: 100, zone: 20, threat: 10, city: 0 }

interface Label {
  key: string
  kind: 'base' | 'objective' | 'threat' | 'zone' | 'city'
  pos: LonLat
  text: string
  size: number
  priority: number
  offset: [number, number]
  anchor: 'start' | 'middle'
  color: RGBA
  bold?: boolean
  ref?: unknown
}

function worldPx(lon: number, lat: number, zoom: number): [number, number] {
  const scale = 512 * 2 ** zoom
  const s = Math.sin((lat * Math.PI) / 180)
  return [((lon + 180) / 360) * scale, (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * scale]
}

function declutter(labels: Label[], obstacles: { pos: LonLat; r: number }[], zoom: number): Label[] {
  const boxes: number[][] = obstacles.map(({ pos, r }) => {
    const [x, y] = worldPx(pos[0], pos[1], zoom)
    return [x - r, y - r, x + r, y + r]
  })
  const out: Label[] = []
  for (const l of [...labels].sort((a, b) => b.priority - a.priority)) {
    const [px, py] = worldPx(l.pos[0], l.pos[1], zoom)
    const w = l.text.length * l.size * (l.bold ? 0.62 : 0.56) + 4
    const h = l.size + 3
    const x0 = (l.anchor === 'start' ? px : px - w / 2) + l.offset[0]
    const y0 = py + l.offset[1] - h / 2
    const box = [x0, y0, x0 + w, y0 + h]
    if (boxes.some((b) => !(box[2] < b[0] || box[0] > b[2] || box[3] < b[1] || box[1] > b[3]))) continue
    boxes.push(box)
    out.push(l)
  }
  return out
}
const TEXT_BASE = {
  fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
  fontSettings: { sdf: true },
  outlineWidth: 3,
  outlineColor: [13, 13, 13, 235] as [number, number, number, number],
  characterSet: 'auto' as const,
}

export default function MapView() {
  const view = useView()!
  const selection = useStore((s) => s.selection)
  const hoverMission = useStore((s) => s.hoverMission)
  const viewTime = useStore((s) => s.viewTime)
  const layersOn = useStore((s) => s.layers)
  const toggleLayer = useStore((s) => s.toggleLayer)
  const tool = useStore((s) => s.tool)
  const hazard = useStore((s) => s.hazard)
  const loadHazard = useStore((s) => s.loadHazard)
  const committedVersion = useStore((s) => s.app?.version)
  const select = useStore((s) => s.select)
  const setHoverMission = useStore((s) => s.setHoverMission)
  const propose = useStore((s) => s.propose)

  const wrapRef = useRef<HTMLDivElement>(null)
  const [basemap, setBasemap] = useState<{ countries: FC; rivers: FC } | null>(null)
  const [viewState, setViewState] = useState<MapViewState | null>(null)
  const [tip, setTip] = useState<Tip | null>(null)
  const [cursor, setCursor] = useState<LonLat | null>(null)

  useEffect(() => {
    void loadBasemap().then(setBasemap)
  }, [])

  // Fit the theatre once we know the container size.
  useEffect(() => {
    const el = wrapRef.current
    if (!el || viewState) return
    const { width, height } = el.getBoundingClientRect()
    if (width < 50 || height < 50) return
    const pts = [
      ...Object.values(view.world.bases).map((b) => [b.lon, b.lat]),
      ...Object.values(view.world.threats).map((t) => [t.lon, t.lat]),
      ...Object.values(view.world.missions).map((m) => [m.lon, m.lat]),
    ]
    const lons = pts.map((p) => p[0])
    const lats = pts.map((p) => p[1])
    const vp = new WebMercatorViewport({ width, height }).fitBounds(
      [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
      { padding: 40 },
    )
    setViewState({ longitude: vp.longitude, latitude: vp.latitude, zoom: vp.zoom, pitch: 0, bearing: 0 })
  }, [view, viewState])

  // Threat surface follows whatever is displayed (committed plan or pending proposal).
  const proposalId = view.proposal?.id
  useEffect(() => {
    if (layersOn.hazard) void loadHazard()
  }, [layersOn.hazard, committedVersion, proposalId, loadHazard])

  const selectedMission = selection?.kind === 'mission' ? selection.id : null
  const focusMission = hoverMission ?? selectedMission

  const groups = useMemo(() => routeGroups(view), [view])

  // Routes from the committed plan that a pending proposal changes or removes.
  const ghosts = useMemo(() => {
    if (!view.previous || !view.proposal) return []
    const changed = new Set(view.proposal.diff.changes.filter((c) => c.change !== 'ADDED').map((c) => c.mission))
    const now = new Map(groups.map((g) => [g.key, g]))
    return routeGroups({ world: view.world, plan: view.previous }).filter((g) => {
      if (!changed.has(g.mission.id)) return false
      const cur = now.get(g.key)
      return !cur || Math.abs(cur.km - g.km) > 10
    })
  }, [view, groups])

  const newThreats = useMemo(() => {
    const committed = useStore.getState().app?.world.threats ?? {}
    return new Set(Object.keys(view.world.threats).filter((id) => !(id in committed)))
  }, [view])

  const staticLayers = useMemo(() => {
    const { world, plan, envelopes } = view
    const t = useStore.getState().viewTime
    const missions = Object.values(world.missions)
    const threats = Object.values(world.threats)
    const bases = Object.values(world.bases)
    const famRGBA = (m: Mission, a = 1) => rgba(roleColor(m.role), a)
    const dimmed = (missionId: string) => focusMission !== null && focusMission !== missionId
    const layers = []

    if (basemap) {
      layers.push(
        new GeoJsonLayer({
          id: 'countries',
          data: basemap.countries as never,
          filled: true,
          stroked: true,
          getFillColor: (f: { properties: { india?: boolean } }) => rgba(f.properties.india ? C.india : C.land),
          getLineColor: (f: { properties: { india?: boolean } }) => rgba(f.properties.india ? C.indiaBorder : C.border),
          getLineWidth: (f: { properties: { india?: boolean } }) => (f.properties.india ? 1.6 : 1),
          lineWidthUnits: 'pixels',
        }),
      )
      if (layersOn.rivers)
        layers.push(
          new GeoJsonLayer({
            id: 'rivers',
            data: basemap.rivers as never,
            stroked: true,
            filled: false,
            getLineColor: rgba(C.river),
            getLineWidth: 1.2,
            lineWidthUnits: 'pixels',
          }),
        )
    }
    layers.push(
      new PathLayer({
        id: 'graticule',
        data: GRATICULE,
        getPath: (d: { path: LonLat[] }) => d.path,
        getColor: rgba(C.grid, 0.7),
        getWidth: 1,
        widthUnits: 'pixels',
      }),
    )

    if (layersOn.hazard && hazard) {
      layers.push(
        new BitmapLayer({
          id: 'hazard',
          image: hazardImage(hazard),
          bounds: [
            hazard.lon0 - hazard.res / 2,
            hazard.lat0 - hazard.res / 2,
            hazard.lon0 + (hazard.nlon - 0.5) * hazard.res,
            hazard.lat0 + (hazard.nlat - 0.5) * hazard.res,
          ],
          _imageCoordinateSystem: COORDINATE_SYSTEM.LNGLAT,
          textureParameters: { minFilter: 'linear', magFilter: 'linear' },
        }),
      )
    }

    // Restricted airspace.
    const zones = Object.values(world.zones)
    layers.push(
      new PathLayer({
        id: 'zones',
        data: zones,
        getPath: (z) => circle(z.lat, z.lon, z.radius_km),
        getColor: rgba(C.ink2, 0.7),
        getWidth: 1.2,
        widthUnits: 'pixels',
        getDashArray: [4, 3],
        extensions: [dash],
      }),
    )

    // Threats: lethal envelope, intel-age uncertainty halo, site marker.
    if (layersOn.threats) {
      layers.push(
        new PolygonLayer({
          id: 'threat-env',
          data: threats,
          getPolygon: (th: Threat) => circle(th.lat, th.lon, th.radius_km),
          getFillColor: (th: Threat) => rgba(C.critical, 0.05 + 0.12 * th.pk),
          getLineColor: (th: Threat) => rgba(C.critical, newThreats.has(th.id) ? 1 : 0.75),
          getLineWidth: (th: Threat) =>
            selection?.kind === 'threat' && selection.id === th.id ? 3 : newThreats.has(th.id) ? 2.5 : 1.3,
          lineWidthUnits: 'pixels',
          pickable: true,
          updateTriggers: { getLineWidth: [selection, newThreats], getLineColor: [newThreats] },
        }),
        new PathLayer({
          id: 'threat-halo',
          data: threats.filter((th) => (envelopes[th.id]?.r_eff_km ?? 0) > th.radius_km + 0.5),
          getPath: (th: Threat) => circle(th.lat, th.lon, envelopes[th.id].r_eff_km),
          getColor: rgba(C.critical, 0.55),
          getWidth: 1,
          widthUnits: 'pixels',
          getDashArray: [3, 3],
          extensions: [dash],
        }),
        new ScatterplotLayer({
          id: 'threat-sites',
          data: threats,
          getPosition: (th: Threat) => [th.lon, th.lat],
          getRadius: 4.5,
          radiusUnits: 'pixels',
          getFillColor: rgba(C.critical),
          getLineColor: rgba(C.page),
          getLineWidth: 2,
          lineWidthUnits: 'pixels',
          stroked: true,
          pickable: true,
        }),
      )
    }

    // Routes and changes.
    if (layersOn.routes) {
      if (ghosts.length)
        layers.push(
          new PathLayer({
            id: 'ghost-routes',
            data: ghosts,
            getPath: (g: RouteGroup) => g.path,
            getColor: rgba(C.ink2, 0.55),
            getWidth: 1.6,
            widthUnits: 'pixels',
            getDashArray: [5, 4],
            extensions: [dash],
          }),
        )
      const tankers = plan
        ? Object.values(plan.assignments).flatMap((a) =>
            a.tanker_sorties.map((ts) => ({
              tail: ts.tail,
              mission: a.mission,
              path: ts.route.map(([la, lo]) => [lo, la] as LonLat),
            })),
          )
        : []
      layers.push(
        new PathLayer({
          id: 'tanker-tracks',
          data: tankers,
          getPath: (d) => d.path,
          getColor: (d) => rgba(FAMILY_COLOR.support, dimmed(d.mission) ? 0.2 : 0.8),
          getWidth: 1.5,
          widthUnits: 'pixels',
          getDashArray: [2, 3],
          extensions: [dash],
          pickable: true,
          updateTriggers: { getColor: [focusMission] },
        }),
        new PathLayer({
          id: 'tanker-orbits',
          data: tankers,
          getPath: (d) => circle(d.path[d.path.length - 1][1], d.path[d.path.length - 1][0], 15, 36),
          getColor: (d) => rgba(FAMILY_COLOR.support, dimmed(d.mission) ? 0.2 : 0.8),
          getWidth: 1.5,
          widthUnits: 'pixels',
          updateTriggers: { getColor: [focusMission] },
        }),
        new PathLayer({
          id: 'route-casing',
          data: groups,
          getPath: (g: RouteGroup) => g.path,
          getColor: rgba(C.page, 0.7),
          getWidth: (g: RouteGroup) => (focusMission === g.mission.id ? 6.5 : 4.5),
          widthUnits: 'pixels',
          capRounded: true,
          jointRounded: true,
          updateTriggers: { getWidth: [focusMission] },
        }),
        new PathLayer({
          id: 'routes',
          data: groups,
          getPath: (g: RouteGroup) => g.path,
          getColor: (g: RouteGroup) => famRGBA(g.mission, dimmed(g.mission.id) ? 0.18 : 0.9),
          getWidth: (g: RouteGroup) => (focusMission === g.mission.id ? 3.5 : 2),
          widthUnits: 'pixels',
          capRounded: true,
          jointRounded: true,
          pickable: true,
          updateTriggers: { getColor: [focusMission], getWidth: [focusMission] },
        }),
      )
    }

    // Mission objectives (orbits for station-keeping roles).
    const orbiting = missions.filter((m) => plan?.assignments[m.id] && ['DCA', 'AEW', 'ISR', 'CAS'].includes(m.role))
    layers.push(
      new PathLayer({
        id: 'orbits',
        data: orbiting,
        getPath: (m: Mission) => circle(m.lat, m.lon, 18, 48),
        getColor: (m: Mission) => famRGBA(m, dimmed(m.id) ? 0.15 : 0.55),
        getWidth: 1,
        widthUnits: 'pixels',
        updateTriggers: { getColor: [focusMission] },
      }),
      new ScatterplotLayer({
        id: 'objectives',
        data: missions,
        getPosition: (m: Mission) => [m.lon, m.lat],
        getRadius: (m: Mission) => (focusMission === m.id ? 8 : 5.5),
        radiusUnits: 'pixels',
        stroked: true,
        getFillColor: (m: Mission) => (plan?.assignments[m.id] ? famRGBA(m, dimmed(m.id) ? 0.35 : 1) : rgba(C.surface)),
        getLineColor: (m: Mission) => (plan?.assignments[m.id] ? rgba(C.page) : famRGBA(m)),
        getLineWidth: 2,
        lineWidthUnits: 'pixels',
        pickable: true,
        updateTriggers: { getRadius: [focusMission], getFillColor: [focusMission, plan], getLineColor: [plan] },
      }),
    )

    // Bases on top.
    layers.push(
      new IconLayer({
        id: 'bases',
        data: bases,
        getPosition: (b: Base) => [b.lon, b.lat],
        getIcon: (b: Base) => {
          const st = baseStatus(b, t).status
          return { id: st, url: BASE_ICONS[st], width: 40, height: 40 }
        },
        getSize: (b: Base) => (selection?.kind === 'base' && selection.id === b.id ? 26 : 19),
        sizeUnits: 'pixels',
        pickable: true,
        updateTriggers: { getIcon: [t, world], getSize: [selection] },
      }),
    )
    return layers
    // viewTime only matters here for base status; refresh it every 5 simulated minutes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, basemap, layersOn, hazard, groups, ghosts, focusMission, selection, newThreats, Math.floor(viewTime / 5)])

  const labels = useMemo(() => {
    const { world, plan } = view
    const t = useStore.getState().viewTime
    const out: Label[] = []
    for (const b of Object.values(world.bases)) {
      const st = baseStatus(b, t).status
      const sv = serviceableAt(world, b.id)
      const flag = st === 'open' ? '' : ` ${STATUS_ICON[st]} ${st === 'closed' ? 'CLOSED' : 'CLOSING'}`
      out.push({
        key: `b:${b.id}`, kind: 'base', pos: [b.lon, b.lat], size: 11, bold: true, ref: b,
        text: sv.total ? `${b.name} ${sv.serviceable}/${sv.total}${flag}` : `${b.name}${flag}`,
        priority: LABEL_PRIORITY.base, offset: [12, 0], anchor: 'start', color: rgba(C.ink),
      })
    }
    for (const c of CITIES)
      out.push({ key: `c:${c.name}`, kind: 'city', pos: [c.lon, c.lat], text: c.name, size: 11,
        priority: LABEL_PRIORITY.city, offset: [0, 0], anchor: 'middle', color: rgba(C.muted) })
    if (layersOn.labels) {
      for (const m of Object.values(world.missions)) {
        const planned = !!plan?.assignments[m.id]
        const focus = m.id === focusMission
        out.push({
          key: `m:${m.id}`, kind: 'objective', pos: [m.lon, m.lat], size: 10, ref: m,
          text: planned ? m.id : `${m.id} · not planned`,
          priority: focus ? LABEL_PRIORITY.focus : LABEL_PRIORITY.mission + m.priority * 5 + (planned ? 2 : 0),
          offset: [0, -14], anchor: 'middle',
          color: rgba(planned ? C.ink2 : C.muted, focusMission && !focus ? 0.45 : 1),
        })
      }
      if (layersOn.threats)
        for (const th of Object.values(world.threats)) {
          const fresh = newThreats.has(th.id)
          out.push({
            key: `t:${th.id}`, kind: 'threat', pos: [th.lon, th.lat], size: 10, ref: th,
            text: fresh ? `NEW ${th.id}` : th.id,
            priority: fresh ? LABEL_PRIORITY.focus : LABEL_PRIORITY.threat,
            offset: [0, 13], anchor: 'middle', color: rgba(fresh ? C.ink : C.ink2, fresh ? 1 : 0.75),
          })
        }
      for (const z of Object.values(world.zones))
        out.push({ key: `z:${z.id}`, kind: 'zone', pos: [z.lon, z.lat], text: z.reason, size: 10,
          priority: LABEL_PRIORITY.zone, offset: [0, 0], anchor: 'middle', color: rgba(C.muted) })
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, layersOn, focusMission, newThreats, Math.floor(viewTime / 5)])

  const obstacles = useMemo(
    () => [
      ...Object.values(view.world.bases).map((b) => ({ pos: [b.lon, b.lat] as LonLat, r: 10 })),
      ...Object.values(view.world.missions).map((m) => ({ pos: [m.lon, m.lat] as LonLat, r: 6 })),
    ],
    [view],
  )
  const zoomStep = viewState ? Math.round(viewState.zoom * 4) / 4 : 0
  const placed = useMemo(() => declutter(labels, obstacles, zoomStep), [labels, obstacles, zoomStep])

  const labelLayers = useMemo(
    () =>
      [false, true].map(
        (bold) =>
          new TextLayer({
            id: bold ? 'labels-bold' : 'labels',
            data: placed.filter((l) => !!l.bold === bold),
            getPosition: (l: Label) => l.pos,
            getText: (l: Label) => l.text,
            getColor: (l: Label) => l.color,
            getSize: (l: Label) => l.size,
            getPixelOffset: (l: Label) => l.offset,
            getTextAnchor: (l: Label) => l.anchor,
            getAlignmentBaseline: 'center',
            ...TEXT_BASE,
            fontWeight: bold ? 600 : 400,
            pickable: true,
          }),
      ),
    [placed],
  )

  const flights = useMemo(() => {
    if (!layersOn.aircraft || !view.plan) return []
    const out: Flight[] = []
    for (const a of Object.values(view.plan.assignments)) {
      for (const s of a.sorties) {
        const f = flightAt(view.world, a, s, viewTime)
        if (f && f.phase !== 'start-up') out.push(f)
      }
      for (const s of a.tanker_sorties) {
        const f = flightAt(view.world, a, s, viewTime, true)
        if (f && f.phase !== 'start-up') out.push(f)
      }
    }
    return out
  }, [view, viewTime, layersOn.aircraft])

  const dynamicLayers = useMemo(() => {
    const layers = []
    layers.push(
      new PolygonLayer({
        id: 'aircraft',
        data: flights.map((f) => ({ ...f, polygon: arrowhead(f.pos, f.heading, focusMission === f.mission ? 11 : 8.5, zoomStep) })),
        getPolygon: (d: { polygon: LonLat[] }) => d.polygon,
        getFillColor: (f: Flight) => {
          const m = view.world.missions[f.mission]
          const fam: Family = f.tanker ? 'support' : m ? ROLE_FAMILY[m.role] : 'support'
          return rgba(FAMILY_COLOR[fam], focusMission && focusMission !== f.mission ? 0.35 : 1)
        },
        getLineColor: rgba(C.ink, 0.9),
        getLineWidth: 1.2,
        lineWidthUnits: 'pixels',
        stroked: true,
        pickable: true,
      }),
    )
    if (focusMission)
      layers.push(
        new TextLayer({
          id: 'aircraft-labels',
          data: flights.filter((f) => f.mission === focusMission),
          getPosition: (f: Flight) => f.pos,
          getText: (f: Flight) => f.tail,
          getColor: rgba(C.ink),
          getSize: 10,
          getPixelOffset: [0, 16],
          ...TEXT_BASE,
        }),
      )
    if (tool && cursor) {
      const km = tool === 'SAM-LR' ? 110 : 45
      layers.push(
        new PolygonLayer({
          id: 'drop-preview',
          data: [cursor],
          getPolygon: (p: LonLat) => circle(p[1], p[0], km),
          getFillColor: rgba(C.critical, 0.12),
          getLineColor: rgba(C.critical, 0.9),
          getLineWidth: 1.5,
          lineWidthUnits: 'pixels',
        }),
      )
    }
    return layers
  }, [flights, focusMission, view, tool, cursor, zoomStep])

  // Pan to a selection made elsewhere (list/timeline) if it is off-screen.
  useEffect(() => {
    if (!selection || !viewState || !wrapRef.current) return
    const p =
      selection.kind === 'mission' ? view.world.missions[selection.id]
      : selection.kind === 'base' ? view.world.bases[selection.id]
      : selection.kind === 'threat' ? view.world.threats[selection.id]
      : null
    if (!p) return
    const { width, height } = wrapRef.current.getBoundingClientRect()
    const vp = new WebMercatorViewport({ ...viewState, width, height })
    const [x, y] = vp.project([p.lon, p.lat])
    if (x < 40 || y < 40 || x > width - 40 || y > height - 40)
      setViewState({ ...viewState, longitude: p.lon, latitude: p.lat, transitionDuration: 500 } as MapViewState)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection])

  const onClick = (info: PickingInfo) => {
    if (tool && info.coordinate) {
      const [lon, lat] = info.coordinate as LonLat
      const at = eventTime()
      let n = 1
      while (`POPUP-${String(n).padStart(2, '0')}` in view.world.threats) n++
      const id = `POPUP-${String(n).padStart(2, '0')}`
      const lr = tool === 'SAM-LR'
      void propose(
        [{
          kind: 'new_threat',
          at,
          threat: {
            id, kind: tool, lat: +lat.toFixed(3), lon: +lon.toFixed(3),
            radius_km: lr ? 110 : 45, pk: lr ? 0.55 : 0.5, observed_at: at, mobile_kmh: lr ? 0 : 6,
          },
        }],
        `Pop-up ${tool} at ${lat.toFixed(2)}N ${lon.toFixed(2)}E`,
      )
      return
    }
    const { id, o } = resolvePick(info)
    if (!o || !id) return select(null)
    if (id === 'bases') select({ kind: 'base', id: o.id as string })
    else if (id === 'threat-env' || id === 'threat-sites') select({ kind: 'threat', id: o.id as string })
    else if (id === 'objectives') select({ kind: 'mission', id: o.id as string })
    else if (id === 'routes') select({ kind: 'mission', id: (o as unknown as RouteGroup).mission.id })
    else if (id === 'aircraft' || id === 'tanker-tracks') select({ kind: 'mission', id: o.mission as string })
  }

  const onHover = (info: PickingInfo) => {
    if (tool) setCursor((info.coordinate as LonLat) ?? null)
    const body = info.object ? tooltipFor(info, view) : null
    setTip(body ? { x: info.x, y: info.y, body } : null)
    const { id, o } = resolvePick(info)
    const mission =
      id === 'routes' ? (o as unknown as RouteGroup).mission.id
      : id === 'objectives' ? (o?.id as string)
      : id === 'aircraft' || id === 'tanker-tracks' ? (o?.mission as string)
      : null
    if (mission !== useStore.getState().hoverMission) setHoverMission(mission ?? null)
  }

  return (
    <div ref={wrapRef} className={`map-wrap dim-when-busy${tool ? ' tool' : ''}`}>
      {viewState && (
        <DeckGL
          viewState={viewState}
          onViewStateChange={({ viewState: vs }) => setViewState(vs as MapViewState)}
          controller={{ doubleClickZoom: false }}
          layers={[...staticLayers, ...labelLayers, ...dynamicLayers]}
          parameters={{ depthCompare: 'always', depthWriteEnabled: false }}
          pickingRadius={10}
          onClick={onClick}
          onHover={onHover}
          getCursor={({ isDragging, isHovering }) => (tool ? 'crosshair' : isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab')}
        />
      )}
      <div className="map-overlay layer-toggles">
        {(
          [
            ['threats', 'Threats'],
            ['hazard', 'Threat surface'],
            ['routes', 'Routes'],
            ['aircraft', 'Aircraft'],
            ['labels', 'Labels'],
            ['rivers', 'Rivers'],
          ] as const
        ).map(([k, label]) => (
          <button key={k} className="chip" aria-pressed={layersOn[k]} onClick={() => toggleLayer(k)}>
            {label}
          </button>
        ))}
      </div>
      <div className="map-overlay view-clock">
        Map time <b>{fmtTime(viewTime)}</b> · {flights.length} airborne
      </div>
      {tool && (
        <div className="map-overlay tool-hint">
          Click the map to place a {tool === 'SAM-LR' ? 'long' : 'medium'}-range SAM · Esc to cancel
        </div>
      )}
      <Legend hazard={layersOn.hazard} />
      {tip && (
        <div className="tooltip" style={{ position: 'absolute', left: tip.x + 14, top: tip.y + 14 }}>
          {tip.body}
        </div>
      )}
    </div>
  )
}

function Legend({ hazard }: { hazard: boolean }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="map-overlay legend" aria-label="Map legend">
      <button className="legend-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        Legend {open ? '▾' : '▸'}
      </button>
      {open && (
        <>
          {(Object.keys(FAMILY_COLOR) as Family[]).map((f) => (
            <div key={f} className="row">
              <span className="key-line" style={{ borderColor: FAMILY_COLOR[f] }} />
              {FAMILY_LABEL[f]}
            </div>
          ))}
          <div className="row">
            <span className="swatch hollow" style={{ borderColor: C.ink2 }} /> Objective not planned
          </div>
          <div className="row">
            <span className="key-ring" style={{ background: 'rgba(208,59,59,0.15)', border: `1.5px solid ${C.critical}` }} />
            SAM envelope (dashed: intel age)
          </div>
          {hazard && (
            <div className="row">
              <span className="ramp" /> Threat surface: low → high
            </div>
          )}
          <div className="row">
            <span className="key-line" style={{ borderColor: C.ink2, borderTopStyle: 'dashed' }} /> Route before proposal
          </div>
          <div className="row">
            {(['open', 'closing', 'closed'] as BaseStatus[]).map((st) => (
              <span key={st} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginRight: 8 }}>
                <span className="swatch" style={{ background: STATUS_COLOR[st] }} /> {STATUS_ICON[st]} {STATUS_LABEL[st]}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
