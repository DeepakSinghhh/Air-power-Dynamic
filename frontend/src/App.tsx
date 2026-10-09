import { useEffect, useRef } from 'react'

import MapView from './components/MapView'
import MissionList from './components/MissionList'
import SidePanel from './components/SidePanel'
import Timeline from './components/Timeline'
import TopBar from './components/TopBar'
import { useStore, useView } from './store'

export default function App() {
  const init = useStore((s) => s.init)
  const busy = useStore((s) => s.busy)
  const error = useStore((s) => s.error)
  const dismissError = useStore((s) => s.dismissError)
  const view = useView()
  const appRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void init()
  }, [init])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target
      if (t instanceof HTMLInputElement || t instanceof HTMLSelectElement) return
      const s = useStore.getState()
      if (e.key === 'Escape') {
        s.setTool(null)
        s.select(null)
      } else if (e.key === '/') {
        e.preventDefault()
        s.setLeftTab('copilot')
        setTimeout(() => document.querySelector<HTMLInputElement>('.cp-input input')?.focus(), 0)
      } else if (e.key === ' ' && !(t instanceof HTMLButtonElement)) {
        e.preventDefault()
        s.setPlaying(!s.playing)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const startResize = (e: React.PointerEvent) => {
    const el = appRef.current
    if (!el) return
    const startY = e.clientY
    const startPx = el.querySelector('.timeline')?.getBoundingClientRect().height ?? 300
    const move = (ev: PointerEvent) => {
      const h = Math.min(window.innerHeight * 0.75, Math.max(160, startPx - (ev.clientY - startY)))
      el.style.setProperty('--tl-h', `${h}px`)
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <div ref={appRef} className={`app${busy ? ' busy' : ''}`}>
      <div className="banner">UNCLASSIFIED // EXERCISE - NOTIONAL DATA</div>
      <TopBar />
      {view?.plan ? (
        <>
          <MissionList />
          <MapView />
          <SidePanel />
          <div className="splitter" onPointerDown={startResize} title="Drag to resize timeline" />
          <Timeline />
        </>
      ) : (
        <div className="empty" style={{ gridColumn: '1 / -1' }}>
          {busy ? `${busy.label}...` : 'Waiting for the engine (is `uvicorn sarthi.api:app` running?)'}
        </div>
      )}
      {error && (
        <div className="toast" role="alert">
          <span>{error}</span>
          <button className="btn small" onClick={dismissError}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  )
}
