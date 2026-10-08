// Scripted demo walkthrough recorded as a video (backup for the live demo, and for the submission).
// Follows docs/PLAN.md section 11. Everything is real: the engine solves live, nothing is pre-rendered.
// Needs the engine serving the built UI (cd engine && uvicorn sarthi.api:app), then:  npm run demo-video
//   APP_URL   default http://127.0.0.1:8000/
//   VIDEO     output directory (default e2e/video); writes demo.webm and, if ffmpeg is installed, demo.mp4
//   CHROME    Chromium executable (default: preinstalled /opt/pw-browsers build, else Playwright's own)
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, renameSync, rmSync } from 'node:fs'
import { chromium } from 'playwright'

const URL = process.env.APP_URL ?? 'http://127.0.0.1:8000/'
const OUT = process.env.VIDEO ?? 'e2e/video'
const PREINSTALLED = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'
const exe = process.env.CHROME ?? (existsSync(PREINSTALLED) ? PREINSTALLED : undefined)
const SIZE = { width: 1600, height: 960 }
mkdirSync(OUT, { recursive: true })

// Reset the engine to the demo scenario before recording.
await fetch(`${URL}api/scenario`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"seed":7}' })

const browser = await chromium.launch({
  executablePath: exe,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
})
const context = await browser.newContext({ viewport: SIZE, recordVideo: { dir: OUT, size: SIZE } })
const page = await context.newPage()
const idle = () => page.waitForFunction(() => !document.querySelector('.busy-pill'), null, { timeout: 180000 })
const wait = (ms) => page.waitForTimeout(ms)

const OVERLAY = `
  #demo-cap { position: fixed; left: 50%; bottom: 26px; transform: translateX(-50%); z-index: 9999; max-width: 1100px;
    background: rgba(10,10,10,0.88); color: #fff; border: 1px solid rgba(255,255,255,0.18); border-radius: 10px;
    padding: 12px 18px; font: 600 20px/1.35 system-ui, sans-serif; text-align: center; pointer-events: none;
    box-shadow: 0 10px 30px rgba(0,0,0,0.6); transition: opacity .25s; }
  #demo-cap small { display: block; font-weight: 400; font-size: 15px; color: #c3c2b7; margin-top: 4px; }
  #demo-card { position: fixed; inset: 0; z-index: 10000; background: #121211; color: #fff; display: flex;
    flex-direction: column; align-items: center; justify-content: center; font-family: system-ui, sans-serif; text-align: center; }
  #demo-card h1 { font-size: 64px; margin: 0 0 10px; letter-spacing: .02em; }
  #demo-card p { font-size: 22px; color: #c3c2b7; margin: 6px 0; max-width: 1100px; }
  #demo-card .k { color: #86b6ef; font-size: 16px; letter-spacing: .12em; font-weight: 700; }`

async function caption(title, sub = '') {
  await page.evaluate(([t, s, css]) => {
    if (!document.getElementById('demo-style')) {
      const st = document.createElement('style')
      st.id = 'demo-style'
      st.textContent = css
      document.head.appendChild(st)
    }
    let el = document.getElementById('demo-cap')
    if (!el) {
      el = document.createElement('div')
      el.id = 'demo-cap'
      document.body.appendChild(el)
    }
    el.style.opacity = t ? '1' : '0'
    el.innerHTML = t ? `${t}${s ? `<small>${s}</small>` : ''}` : ''
  }, [title, sub, OVERLAY])
}

async function card(kicker, title, lines, ms) {
  await page.evaluate(([k, t, ls, css]) => {
    if (!document.getElementById('demo-style')) {
      const st = document.createElement('style')
      st.id = 'demo-style'
      st.textContent = css
      document.head.appendChild(st)
    }
    const el = document.createElement('div')
    el.id = 'demo-card'
    el.innerHTML = `<div class="k">${k}</div><h1>${t}</h1>${ls.map((l) => `<p>${l}</p>`).join('')}`
    document.body.appendChild(el)
  }, [kicker, title, lines, OVERLAY])
  if (!ms) return
  await wait(ms)
  await page.evaluate(() => document.getElementById('demo-card')?.remove())
}

const approve = async () => {
  await page.click('.proposal-head button:has-text("Approve")')
  await page.waitForSelector('.proposal-head', { state: 'detached', timeout: 60000 })
  await idle()
}
const statText = async () => ((await page.locator('.proposal-head .stat').first().textContent()) ?? '').replace(/(\d)([a-z])/, '$1 $2')

await page.goto(URL)
const titleCard = card('SMART INDIA HACKATHON 2026 · PS 26250', 'VAYU-SARTHI',
  ['An explainable decision engine for dynamic air operations.', 'Plan, predict, re-plan in seconds. The commander decides.',
    '<span style="font-size:16px;color:#898781">All scenario data notional · running live on one laptop, offline</span>'], 0)
await page.waitForSelector('.tl-content', { timeout: 180000 })
await idle()
await titleCard
await wait(3000)
await page.evaluate(() => document.getElementById('demo-card')?.remove())

// 1. The plan.
await caption('A notional air tasking day: 11 bases, 96 aircraft, 31 missions',
  'Planned by one optimisation model (aircraft, crew fatigue, weapons, tankers, weather, threats, alert reserve)')
await wait(5500)
await page.locator('.mrow').first().click()
await caption('Select a strike: its threat-avoiding route, its SEAD escort and its package',
  'Map, synchronisation matrix and mission card stay linked')
await wait(5000)
await page.keyboard.press('Escape')

// 2. Why not?
const unplanned = page.locator('.mrow:has-text("not planned")').first()
if (await unplanned.count()) {
  const unId = ((await unplanned.locator('.id').textContent()) ?? 'STK-06').trim()
  await unplanned.click()
  await caption('Every unplanned mission says why', 'e.g. least-risk route above the acceptable risk; a SEAD package would open options')
  await wait(5000)
  await caption('…and what it would take', 'Single relaxations re-solved in parallel, each with its price')
  await page.click('text=/Find what gets/')
  await page.waitForSelector('.whatif-row', { timeout: 120000 })
  await idle()
  await caption('Accept a little more risk: planned, 2 aircraft changed, nothing dropped',
    'The machine shows the price; the commander decides whether to pay it')
  await wait(6500)
  await page.keyboard.press('Escape')

  // 2b. The copilot: the same questions in plain language, answered only from the engine.
  await page.keyboard.press('/')
  await page.waitForSelector('.cp-input input')
  await caption('Or just ask the copilot', 'It answers only from the engine; the model, if any, only picks the tool')
  const askCp = async (q, ms) => {
    const n = await page.locator('.cp-bot').count()
    await page.fill('.cp-input input', q)
    await wait(600)
    await page.press('.cp-input input', 'Enter')
    await page.waitForFunction((k) => document.querySelectorAll('.cp-bot').length > k, n, { timeout: 180000 })
    await idle()
    await wait(ms)
  }
  await askCp(`why isn't ${unId} planned?`, 3500)
  await caption('A follow-up in plain words', 'Every answer names the engine tools behind it')
  await askCp('can we squeeze it in?', 5500)
  await page.click('.panel-tabs button:has-text("Missions")')
}

// 4. Pop-up SAM.
await page.click('.btn:has-text("Inject")')
await page.click('.menu-item:has-text("Drop a medium-range SAM")')
const box = await page.locator('.map-wrap').boundingBox()
await page.mouse.move(box.x + box.width * 0.35, box.y + box.height * 0.45)
await caption('A pop-up SAM, placed by hand on the map')
await wait(1500)
await page.mouse.click(box.x + box.width * 0.35, box.y + box.height * 0.45)
await page.waitForSelector('.proposal-head', { timeout: 180000 })
await idle()
await page.click('.chip:has-text("Threat surface")')
await caption('Routes bend around the new threat (old routes dashed); the threat surface shows why',
  `${await statText()}`)
await wait(6500)
await page.click('.chip:has-text("Threat surface")')
await page.click('.proposal-head button:has-text("Reject")')
await page.waitForSelector('.proposal-head', { state: 'detached', timeout: 60000 })
await idle()

// 5. Time-sensitive target.
await caption('New tasking: a time-sensitive target, priority 10', 'Re-optimising with minimal disruption')
await page.click('.btn:has-text("Inject")')
await page.waitForSelector('.menu-item:has-text("Time-sensitive target")', { timeout: 60000 })
await page.click('.menu-item:has-text("Time-sensitive target")')
await page.waitForSelector('.proposal-head', { timeout: 180000 })
await idle()
await caption('A time-sensitive target, priority 10: a pair is found, almost nothing else moves', await statText())
await wait(5500)
await approve()
await page.click('.seg button:has-text("Fit")')
await page.click('text=▶ Play')
await caption('Play the day: aircraft fly their routes on the map')
await wait(5000)
await page.click('text=❚❚ Pause')

// 6. Courses of action.
await caption('What does the commander want? Plan the same situation three ways', 'Max effect · Min risk · Defensive posture')
await page.click('.intent-chip')
await page.waitForSelector('.coa-table', { timeout: 180000 })
await idle()
await caption('Three courses of action from the commander’s intent, solved in parallel',
  'Each trade in one sentence. The machine does not pick the intent; the commander does.')
await wait(8000)
await caption('Adopt Min risk')
await page.locator('.coa-table button:has-text("Adopt")').nth(1).click()
await page.waitForSelector('.proposal-head', { timeout: 60000 })
await idle()
await caption('Adopting Min risk is just another proposal to approve', await statText())
await wait(4500)
await approve()

// 7. Robustness and ground spares.
await caption('How does this plan hold up on a bad day?', 'Executing it 2,000 times with random unserviceability and losses')
await page.click('.robust-chip')
await page.waitForSelector('.robust-grid', { timeout: 180000 })
await idle()
await caption('2,000 simulated days: what fails, why, and which aircraft the plan leans on',
  'Grey: the plan as it is. Blue: with idle aircraft held as ground spares.')
await wait(7500)
await caption('Hold idle aircraft as ground spares')
await page.click('text=Propose ground spares')
await page.waitForSelector('.proposal-head', { timeout: 60000 })
await idle()
await caption('Ground spares from idle aircraft: zero flying changes, a better bad day', await statText())
await wait(5000)
await approve()

// 7b. Fog, predicted (late in the demo: a dense-fog night grounds most northern bases until midday).
await page.click('text=Jump to now')
await page.click('.btn:has-text("Weather")')
await page.waitForSelector('.wx-row', { timeout: 60000 })
await caption('Fog forecast for every base: a real night (3 Feb 2026) the model never saw',
  'Trained on real airport METARs: catches 70% of fog hours vs 26% from raw model visibility')
await wait(6500)
await caption('Propose the base closures the forecast implies at 50% probability')
await page.click('.wx-menu .btn.primary')
await page.waitForSelector('.proposal-head', { timeout: 180000 })
await idle()
await caption(`Dense fog grounds the northern bases until midday; the engine saves what it can: ${await statText()}`,
  'Only what must change, changes. Launched missions are frozen. Nothing happens until a human approves.')
await wait(6500)
await page.click('.seg button:has-text("Aircraft")')
await caption('Aircraft view: closures hatched across each base, old sorties dashed, new ones outlined')
await wait(5000)
await approve()
await page.click('.seg button:has-text("Missions")')

// 8. Same engine, flood relief.
await caption('Now a different problem for the same engine', 'Switching to a flood-relief scenario')
await page.click('.btn:has-text("Scenario")')
await page.click('.seg button:has-text("Flood relief")')
await page.click('text=Generate & plan')
await page.waitForFunction(() => document.querySelector('.kpis')?.textContent?.includes('Relief lifted'), null, { timeout: 180000 })
await idle()
await wait(3000)
await caption('Same engine, flood relief in Assam and Bihar',
  'NDRF lift to forward airfields, helicopter rescue and relief drops where there is no runway, Indian airspace only')
await wait(7000)
await caption('An embankment breach is reported: about 60 people marooned')
await page.click('.btn:has-text("Inject")')
await page.waitForSelector('.menu-item:has-text("Embankment breach")', { timeout: 60000 })
await page.click('.menu-item:has-text("Embankment breach")')
await page.waitForSelector('.proposal-head', { timeout: 180000 })
await idle()
await caption('An embankment breach: a priority-10 rescue, fitted in with minimal disruption', await statText())
await wait(6000)
await approve()
await caption('')

await card('MEASURED ON THE PROTOTYPE', 'VAYU-SARTHI', [
  '+12.4 pts mission fulfilment vs a manual-style plan (20 scenarios)',
  '71% fewer aircraft reassigned on a retask, in under 3 s',
  'Fog: 70% of fog hours forecast vs 26% from raw model visibility',
  'Flood relief: 94.5% of requested tonnage planned vs 81.6% (same fleet)',
], 6000)

await context.close()
await browser.close()

const raw = await page.video()?.path()
if (raw && existsSync(raw)) {
  const webm = `${OUT}/demo.webm`
  renameSync(raw, webm)
  try {
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', webm, '-c:v', 'libx264', '-crf', '28', '-preset', 'slow',
      '-pix_fmt', 'yuv420p', '-movflags', '+faststart', `${OUT}/demo.mp4`])
    rmSync(webm)
    console.log(`video: ${OUT}/demo.mp4`)
  } catch {
    console.log(`video: ${webm} (install ffmpeg for mp4)`)
  }
}
