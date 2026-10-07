// End-to-end smoke test: load, plan, COAs, robustness + ground spares, fog forecast -> closures -> approve, presets,
// drop a SAM, playback.
// Needs the engine serving the built UI:  (cd engine && uvicorn sarthi.api:app)  then  npm run e2e
//   APP_URL   default http://127.0.0.1:8000/
//   SHOTS     directory for screenshots (default e2e/shots)
//   CHROME    Chromium executable (default: preinstalled /opt/pw-browsers build, else Playwright's own)
import { existsSync, mkdirSync } from 'node:fs'
import { chromium } from 'playwright'

const URL = process.env.APP_URL ?? 'http://127.0.0.1:8000/'
const OUT = process.env.SHOTS ?? 'e2e/shots'
const PREINSTALLED = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'
const exe = process.env.CHROME ?? (existsSync(PREINSTALLED) ? PREINSTALLED : undefined)
mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch({
  executablePath: exe,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
})
const page = await browser.newPage({ viewport: { width: 1600, height: 960 } })
const errors = []
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

const idle = () => page.waitForFunction(() => !document.querySelector('.busy-pill'), null, { timeout: 180000 })
const shot = async (name) => {
  await page.waitForTimeout(800)
  await page.screenshot({ path: `${OUT}/${name}.png` })
  console.log(`shot ${name}`)
}
const step = (msg) => console.log(`- ${msg}`)
const approve = async () => {
  await page.click('text=Approve & issue changes')
  await page.waitForSelector('.proposal-head', { state: 'detached', timeout: 60000 })
  await idle()
}
const openWeather = async () => {
  await page.click('.btn:has-text("Weather")')
  await page.waitForSelector('.wx-row', { timeout: 60000 })
}

async function run() {
await page.goto(URL)
await page.waitForSelector('.tl-content', { timeout: 180000 })
await idle()
step(`plan loaded: ${await page.locator('.tile b').first().textContent()} fulfilment`)
await shot('01-plan')

await page.locator('.mrow').first().click()
await shot('02-mission-selected')
await page.keyboard.press('Escape')

// Courses of action: compare three intents, adopt Min risk, approve; the intent then persists.
await page.click('.intent-chip')
await page.waitForSelector('.coa-table', { timeout: 120000 })
await idle()
step(`COAs: ${(await page.locator('.coa-name').allTextContents()).join(' | ')}`)
await shot('02b-coa-compare')
const adoptButtons = page.locator('.coa-table button:has-text("Adopt")')
await adoptButtons.nth(1).click() // columns: Max effect, Min risk, Defensive posture
await page.waitForSelector('.proposal-head', { timeout: 60000 })
await idle()
step(`COA proposal: ${(await page.locator('.proposal-head h2').textContent())?.trim()}`)
await shot('02c-coa-proposal')
await approve()
const chip = (await page.locator('.intent-chip').textContent()) ?? ''
step(`intent chip: ${chip.trim()}`)
if (!chip.includes('Min risk')) throw new Error('adopted intent not shown')

// Robustness: simulate execution, hold ground spares (no flying changes), then ask "what if" a key asset is lost.
await page.click('.robust-chip')
await page.waitForSelector('.robust-grid', { timeout: 120000 })
await idle()
step(`robustness: ${(await page.locator('.robust-grid .stat').allTextContents()).slice(0, 2).join(' | ')}`)
await shot('02d-robustness')
await page.click('text=Propose ground spares')
await page.waitForSelector('.proposal-head', { timeout: 60000 })
await idle()
const spareStat = (await page.locator('.proposal-head .stat').first().textContent()) ?? ''
step(`spares proposal: ${spareStat.trim()}`)
if (!spareStat.includes('0 flying aircraft reassigned')) throw new Error('spares proposal changed flying aircraft')
await approve()
const sorties = (await page.locator('.tile').nth(3).textContent()) ?? ''
if (!sorties.includes('spares')) throw new Error('spares not shown after approval')
await page.click('.robust-chip')
await page.waitForSelector('.robust-row.static button', { timeout: 120000 })
await idle()
await page.locator('.robust-row.static button').first().click()
await page.waitForSelector('.proposal-head', { timeout: 60000 })
await idle()
const whatIf = (await page.locator('.change li').allTextContents()).join(' | ')
step(`what-if: ${(await page.locator('.proposal-head h2').textContent())?.trim()} -> ${whatIf.slice(0, 160)}`)
await shot('02e-what-if-spare-steps-in')
await page.click('text=Reject')
await page.waitForSelector('.proposal-head', { state: 'detached', timeout: 60000 })
await idle()

// Fog forecast -> closures -> proposal -> approve.
await page.click('text=Jump to now')
await openWeather()
step(`weather: ${await page.locator('.wx-row').count()} bases, button "${(await page.locator('.wx-menu .btn.primary').textContent())?.trim()}"`)
await shot('03-weather-panel')
await page.click('.wx-menu .btn.primary')
await page.waitForSelector('.proposal-head', { timeout: 180000 })
await idle()
step(`fog proposal: ${(await page.locator('.proposal-head .stat').first().textContent())?.trim()}`)
await shot('04-fog-proposal')
await page.click('.seg button:has-text("Aircraft")')
await shot('05-fog-proposal-aircraft-view')
await approve()
await shot('06-fog-approved')

// Same forecast again proposes nothing new; a lower threshold proposes more.
await openWeather()
const again = (await page.locator('.wx-menu .btn.primary').textContent())?.trim()
step(`after approval at 50%: "${again}"`)
if (!again?.startsWith('No new closures')) throw new Error('approved closures were proposed again')
await page.click('.wx-menu .seg button:has-text("≥30%")')
await page.waitForFunction(() => !document.querySelector('.wx-menu .btn.primary')?.textContent?.startsWith('No new'), null, { timeout: 30000 }).catch(() => {})
step(`at 30%: "${(await page.locator('.wx-menu .btn.primary').textContent())?.trim()}"`)
await page.click('.wx-menu .seg button:has-text("≥50%")')
await page.keyboard.press('Escape')
await page.mouse.click(5, 300)

// A preset: time-sensitive target.
await page.click('.seg button:has-text("Missions")')
await page.click('text=Inject event')
await page.waitForSelector('.menu-item:has-text("Time-sensitive target")', { timeout: 60000 })
await page.click('.menu-item:has-text("Time-sensitive target")')
await page.waitForSelector('.proposal-head', { timeout: 180000 })
await idle()
step(`TST proposal: ${(await page.locator('.proposal-head .stat').first().textContent())?.trim()}`)
await approve()

// Map tool: drop a medium-range SAM, show the threat surface, then reject.
await page.click('text=Inject event')
await page.click('.menu-item:has-text("Drop a medium-range SAM")')
const box = await page.locator('.map-wrap').boundingBox()
await page.mouse.move(box.x + box.width * 0.35, box.y + box.height * 0.45)
await page.mouse.click(box.x + box.width * 0.35, box.y + box.height * 0.45)
await page.waitForSelector('.proposal-head', { timeout: 180000 })
await idle()
await page.click('.chip:has-text("Threat surface")')
await page.waitForTimeout(1500)
await shot('07-dropped-sam-hazard')
await page.click('text=Reject')
await page.waitForSelector('.proposal-head', { state: 'detached', timeout: 60000 })

// Base card with fog chart (select a base from its timeline group header).
await page.click('.chip:has-text("Threat surface")')
await page.click('.seg button:has-text("Aircraft")')
await page.locator('.tl-label.group:has-text("HALWARA")').click()
await page.waitForSelector('text=Fog forecast', { timeout: 30000 })
await shot('08-base-fog-card')
await page.keyboard.press('Escape')
await page.click('text=Jump to now')

// Playback: advance the clock and check aircraft are drawn.
await page.click('.seg button:has-text("Fit")')
await page.click('text=▶ Play')
await page.waitForTimeout(2500)
await page.click('text=❚❚ Pause')
step(`airborne: ${await page.locator('.view-clock').textContent()}`)
await shot('09-playback')
}

try {
  await run()
} catch (e) {
  console.error('FAILED:', e?.message?.split('\n')[0] ?? e)
  const toast = await page.locator('.toast').textContent({ timeout: 1000 }).catch(() => null)
  if (toast) console.error('toast:', toast)
  await page.screenshot({ path: `${OUT}/failure.png` }).catch(() => {})
  console.error(`failure screenshot: ${OUT}/failure.png`)
  await browser.close()
  process.exit(1)
}
await browser.close()
if (errors.length) {
  console.error('Console errors:\n' + errors.join('\n'))
  process.exit(1)
}
console.log('E2E OK')
