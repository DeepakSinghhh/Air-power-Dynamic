// Slide screenshots at 2x (engine serving the built UI on :8000): clean plan, a pop-up SAM retask proposal,
// the earthquake plan and its thin-air card.  node tools/deck_assets/capture.mjs <out-dir>
// Crops used by build_sih_deck.py: retask.jpg = b-popup-proposal (580,168)-(3200,1800);
// quake.jpg = d-quake-thin-air (1280,168)-(3200,1028).
import { chromium } from '../../frontend/node_modules/playwright/index.mjs'

const OUT = process.argv[2]
const browser = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
})
const page = await browser.newPage({ viewport: { width: 1600, height: 960 }, deviceScaleFactor: 2 })
const idle = () => page.waitForFunction(() => !document.querySelector('.busy-pill'), null, { timeout: 180000 })
const shot = async (name) => {
  await page.waitForTimeout(1200)
  await page.screenshot({ path: `${OUT}/${name}.png` })
  console.log('shot', name)
}

await page.goto('http://127.0.0.1:8000/')
await page.waitForSelector('.tile', { timeout: 180000 })
await idle()
await shot('a-plan')

await page.click('.btn:has-text("Inject")')
await page.waitForSelector('.menu-item:has-text("Pop-up SAM")', { timeout: 60000 })
await page.click('.menu-item:has-text("Pop-up SAM")')
await page.waitForSelector('.proposal-head', { timeout: 180000 })
await idle()
await shot('b-popup-proposal')
await page.click('.proposal-head button:has-text("Reject")')
await page.waitForSelector('.proposal-head', { state: 'detached', timeout: 60000 })

await page.click('.btn:has-text("Scenario")')
await page.click('.seg button:has-text("Earthquake")')
await page.click('text=Generate & plan')
await page.waitForSelector('text=Earthquake relief (HADR)', { timeout: 180000 })
await idle()
await shot('c-quake-plan')
await page.locator('.mrow:has-text("Kedartal")').click()
await page.waitForSelector('dt:has-text("Thin air")', { timeout: 30000 })
await shot('d-quake-thin-air')
await browser.close()
