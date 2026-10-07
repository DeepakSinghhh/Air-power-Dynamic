// End-to-end smoke test: load, plan, select, inject an event, review, approve, drop a SAM.
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

await page.goto(URL)
await page.waitForSelector('.tl-content', { timeout: 180000 })
await idle()
step(`plan loaded: ${await page.locator('.tile b').first().textContent()} fulfilment`)
await shot('01-plan')

// Select the highest-priority planned mission from the list.
await page.locator('.mrow').first().click()
await shot('02-mission-selected')

// Scrub to 02:00 and inject the fog preset.
await page.click('text=Jump to now')
await page.click('text=Inject event')
await page.waitForSelector('.menu-item:has-text("Fog forecast")', { timeout: 60000 })
await page.click('.menu-item:has-text("Fog forecast")')
await page.waitForSelector('.proposal-head', { timeout: 180000 })
await idle()
step(`proposal: ${(await page.locator('.proposal-head .stat').first().textContent())?.trim()}`)
await shot('03-proposal')

await page.click('.seg button:has-text("Aircraft")')
await shot('04-proposal-aircraft-view')

await page.click('text=Approve & issue changes')
await page.waitForSelector('.proposal-head', { state: 'detached', timeout: 60000 })
await idle()
await shot('05-approved')

// Map tool: drop a medium-range SAM in the middle of the map, then reject the proposal.
await page.click('.seg button:has-text("Missions")')
await page.click('text=Inject event')
await page.click('.menu-item:has-text("Drop a medium-range SAM")')
const box = await page.locator('.map-wrap').boundingBox()
await page.mouse.move(box.x + box.width * 0.35, box.y + box.height * 0.45)
await page.mouse.click(box.x + box.width * 0.35, box.y + box.height * 0.45)
await page.waitForSelector('.proposal-head', { timeout: 180000 })
await idle()
await page.click('.chip:has-text("Threat surface")')
await page.waitForTimeout(1500)
await shot('06-dropped-sam-hazard')
await page.click('text=Reject')
await page.waitForSelector('.proposal-head', { state: 'detached', timeout: 60000 })

// Playback: advance the clock and check aircraft are drawn.
await page.click('.seg button:has-text("Fit")')
await page.click('text=▶ Play')
await page.waitForTimeout(2500)
await page.click('text=❚❚ Pause')
step(`airborne: ${await page.locator('.view-clock').textContent()}`)
await shot('07-playback')

await browser.close()
if (errors.length) {
  console.error('Console errors:\n' + errors.join('\n'))
  process.exit(1)
}
console.log('E2E OK')
