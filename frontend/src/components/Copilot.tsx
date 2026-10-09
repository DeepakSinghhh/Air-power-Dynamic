import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { useStore, useView } from '../store'
import type { CopilotAction, CopilotReply } from '../types'

/** **bold** spans inside a line; everything else is plain text (no HTML from the server is ever rendered). */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? <b key={i}>{part.slice(2, -2)}</b> : <Fragment key={i}>{part}</Fragment>,
  )
}

function Body({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let items: string[] = []
  const flush = () => {
    if (items.length) blocks.push(<ul key={`u${blocks.length}`}>{items.map((t, i) => <li key={i}>{inline(t)}</li>)}</ul>)
    items = []
  }
  for (const line of text.split('\n')) {
    if (line.startsWith('- ')) items.push(line.slice(2))
    else {
      flush()
      if (line.trim()) blocks.push(<p key={`p${blocks.length}`}>{inline(line)}</p>)
    }
  }
  flush()
  return <>{blocks}</>
}

function routerLabel(r: CopilotReply) {
  if (r.router === 'rules') return 'parser'
  if (r.router.startsWith('llm:')) return `local model ${r.router.slice(4)}`
  return 'not understood'
}

function Answer({ reply }: { reply: CopilotReply }) {
  const ask = useStore((s) => s.askCopilot)
  const select = useStore((s) => s.select)
  const propose = useStore((s) => s.propose)
  const setCoaOpen = useStore((s) => s.setCoaOpen)
  const setRobustOpen = useStore((s) => s.setRobustOpen)
  const setTimelineMode = useStore((s) => s.setTimelineMode)
  const busy = useStore((s) => s.busy)
  const pending = useStore((s) => !!s.proposal)
  const act = (a: CopilotAction) => {
    if (a.kind === 'ask' && a.text) void ask(a.text)
    else if (a.kind === 'select' && a.mission) select({ kind: 'mission', id: a.mission })
    else if (a.kind === 'propose' && a.events) void propose(a.events, a.text ?? a.label)
    else if (a.kind === 'open') {
      if (a.panel === 'coas') setCoaOpen(true)
      else if (a.panel === 'robustness') setRobustOpen(true)
      else if (a.panel === 'readiness') setTimelineMode('readiness')
    }
  }
  return (
    <div className="cp-msg cp-bot">
      <Body text={reply.text} />
      {reply.actions.length > 0 && (
        <div className="cp-actions">
          {reply.actions.map((a, i) => (
            <button key={i} className={`btn small${a.kind === 'propose' ? ' primary' : ''}`}
              disabled={!!busy || (a.kind === 'propose' && pending)} onClick={() => act(a)}>
              {a.label}{a.kind === 'open' ? ' ▸' : ''}
            </button>
          ))}
        </div>
      )}
      <div className="cp-meta" title="How the question was routed and which engine tools produced the answer">
        {routerLabel(reply)}{reply.tools.length ? ` · ${reply.tools.join(', ')}` : ''} · {reply.seconds.toFixed(1)} s
      </div>
    </div>
  )
}

export default function Copilot() {
  const view = useView()!
  const chat = useStore((s) => s.chat)
  const ask = useStore((s) => s.askCopilot)
  const status = useStore((s) => s.copilotStatus)
  const loadStatus = useStore((s) => s.loadCopilotStatus)
  const busy = useStore((s) => s.busy)
  const [text, setText] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!status) void loadStatus()
  }, [status, loadStatus])
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [chat.length])
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Starter questions built from the current plan.
  const suggestions = useMemo(() => {
    const { world, plan } = view
    const unplanned = Object.values(world.missions).filter((m) => !plan?.assignments[m.id]).sort((a, b) => b.priority - a.priority)
    const top = Object.values(world.missions).filter((m) => plan?.assignments[m.id]).sort((a, b) => b.priority - a.priority)[0]
    const load = new Map<string, number>()
    Object.values(plan?.assignments ?? {}).forEach((a) => a.sorties.forEach((s) => load.set(s.base, (load.get(s.base) ?? 0) + 1)))
    const busiest = [...load.entries()].sort((a, b) => b[1] - a[1])[0]?.[0]
    const out: string[] = []
    if (unplanned[0]) out.push(`Why isn't ${unplanned[0].id} planned?`, `What would it take to plan ${unplanned[0].id}?`)
    if (top) out.push(`Brief me on ${top.id}`)
    if (busiest) out.push(`What if ${world.bases[busiest].name} closes from 05:00 to 09:30?`)
    out.push('How robust is the plan?')
    if (world.scenario !== 'hadr') out.push('Compare courses of action')
    out.push('Status')
    return out
  }, [view])

  const send = (q: string) => {
    if (!q.trim() || busy) return
    setText('')
    void ask(q)
  }

  return (
    <div className="copilot">
      <div className="cp-log" aria-live="polite">
        <div className="cp-intro">
          Ask about the plan in plain language. Answers come only from the engine. Anything that would change the
          plan comes back as a proposal for you to approve or reject.
          <div className="muted cp-status">
            {status?.llm
              ? `Parser + local model: ${status.llm}${status.reachable === false ? ' (not reachable: parser only)' : ''}`
              : 'Offline parser (no language model configured)'}
          </div>
        </div>
        {chat.map((m, i) =>
          m.role === 'user' ? (
            <div key={i} className="cp-msg cp-user">{m.text}</div>
          ) : m.role === 'note' ? (
            <div key={i} className="cp-note">{m.text}</div>
          ) : (
            <Answer key={i} reply={m.reply} />
          ),
        )}
        {chat.length === 0 && (
          <div className="cp-suggest">
            {suggestions.map((q) => (
              <button key={q} className="btn small" disabled={!!busy} onClick={() => send(q)}>{q}</button>
            ))}
          </div>
        )}
        <div ref={endRef} />
      </div>
      <form className="cp-input" onSubmit={(e) => { e.preventDefault(); send(text) }}>
        <input ref={inputRef} value={text} onChange={(e) => setText(e.target.value)} maxLength={500}
          placeholder={view.world.scenario === 'hadr' ? 'e.g. what is not planned, and why?' : 'e.g. what if Halwara fogs in at 05:00?'}
          aria-label="Ask the copilot" />
        <button className="btn small primary" type="submit" disabled={!text.trim() || !!busy}>Ask</button>
      </form>
    </div>
  )
}
