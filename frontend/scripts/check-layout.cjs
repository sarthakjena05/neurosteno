// Render the real frontend with isolated test snapshots; never contacts a backend.
// PLAYWRIGHT_MODULE may point at an existing Playwright installation.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const fs = require('node:fs')
const path = require('node:path')

async function main() {
  const out = process.env.LAYOUT_OUTPUT || path.resolve('build/layout-artifacts')
  fs.mkdirSync(out, { recursive: true })
  const browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const results = []
  try {
    for (const [width, height] of [[1920, 1080], [1366, 768], [1366, 650]]) {
      const page = await browser.newPage({ viewport: { width, height } })
      await page.addInitScript(enabled => localStorage.setItem('ns.flicker', enabled ? '1' : '0'), process.env.LAYOUT_FLICKER === '1')
      await page.route('**/api/**', route => route.fulfill({ contentType: 'application/json', body: '[]' }))
      await page.addInitScript(() => {
        class FixtureSocket {
          static OPEN = 1
          readyState = 1
          constructor() {
            import('/src/test/mockState.ts').then(({ mockState }) => {
              window.layoutSnapshot = (n, confirm = false) => {
                const s = mockState({}, n)
                const all = mockState().screen.slots
                if (n === 3) s.screen.slots = [
                  { ...all[0], key: 'group:medical', kind: 'group', label: 'Medical', items: all.slice(0, 2).flatMap(x => x.items) },
                  { ...all[2], index: 1, key: 'group:daily', kind: 'group', label: 'Daily needs', items: all.slice(2, 4).flatMap(x => x.items) },
                  { ...all[4], index: 2 },
                ]
                s.phase = confirm ? 'PREDICTION_HIGHLIGHTED' : 'EVIDENCE_COLLECTION'
                s.quality = { signal_ok: true }
                s.config.auto_adapt = true
                s.profile.display_name = 'Alex (synthetic) - new patient'
                s.routing = { mode: 'adaptive', n, expected: { selections: 5.4, static_selections: 9.1 }, shortcuts: ['comfort/position/turn-left'], urgent: ['breathing/suction-mouth/now'] }
                s.adaptation.last_change = { from: n === 5 ? 3 : 5, to: n, reason: 'The signal-adaptive model updated the available choice count using current evidence.' }
                s.attempt = { kind: confirm ? 'confirm' : 'choose', targets: confirm
                  ? [{ key: 'yes', label: 'Yes', freq: 7.5 }, { key: 'no', label: 'No', freq: 12 }]
                  : s.screen.slots.map((x, i) => ({ key: x.key, label: x.label, freq: [6.67, 7.5, 8.57, 10, 12][i] })), elapsed_s: .8, max_window_s: 5, min_window_s: 1.5, calibrated: false, last: null }
                s.highlight = confirm ? { slot_index: 0, key: s.screen.slots[0].key, label: s.screen.slots[0].label, margin: .8, window_s: 2, provisional: false, provisional_remaining_s: null } : null
                this.current = s
                this.onmessage?.({ data: JSON.stringify({ type: 'state', state: s }) })
              }
              this.onopen?.()
              window.layoutSnapshot(5)
              this.timer = setInterval(() => this.onmessage?.({ data: JSON.stringify({ type: 'state', state: this.current }) }), 250)
            })
          }
          close() { clearInterval(this.timer); this.readyState = 3 }
        }
        window.WebSocket = FixtureSocket
      })
      await page.goto('http://127.0.0.1:5175/#demo')
      await page.getByTestId('slot-4').waitFor()
      for (const controls of [false, true]) {
      if (controls) await page.getByRole('button', { name: 'Controls', exact: true }).click()
      for (const confirm of [false, true]) for (const n of [5, 3, 2]) {
        await page.evaluate(({ n, confirm }) => window.layoutSnapshot(n, confirm), { n, confirm })
        await page.waitForFunction(n => document.querySelectorAll('.choice-card').length === n, n)
        await page.evaluate(() => document.fonts.ready)
        const report = await page.evaluate(() => {
          const rect = el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height } }
          const cards = [...document.querySelectorAll('.choice-card')]
          const panel = document.querySelector('.patient-screen')
          const footer = document.querySelector('.patient-actions')
          const issues = []
          cards.forEach((a, i) => {
            const r = rect(a)
            if (r.y < 0 || r.bottom > innerHeight || r.x < 0 || r.right > innerWidth) issues.push('card outside viewport: ' + i)
            cards.slice(i + 1).forEach((b, j) => {
              const t = rect(b)
              if (Math.min(r.right, t.right) - Math.max(r.x, t.x) > 1 && Math.min(r.bottom, t.bottom) - Math.max(r.y, t.y) > 1) issues.push('overlap: ' + i + '/' + (i + j + 1))
            })
            if (a.scrollHeight > a.clientHeight + 1) issues.push('clipped card content: ' + i)
          })
          const rowSizes = []
          cards.forEach(c => {
            const y = Math.round(rect(c).y)
            let row = rowSizes.find(r => r.y === y)
            if (!row) rowSizes.push(row = { y, count: 0 })
            row.count++
          })
          const expectedRows = cards.length === 5 ? [3, 2] : [cards.length]
          if (JSON.stringify(rowSizes.map(r => r.count)) !== JSON.stringify(expectedRows)) issues.push('incorrect grid rows')
          if (panel.scrollHeight > panel.clientHeight + 1) issues.push('patient panel scrolls')
          if (rect(footer).bottom > innerHeight || cards.some(c => rect(c).bottom > rect(footer).y)) issues.push('footer obscured')
          if (document.documentElement.scrollWidth > innerWidth) issues.push('horizontal overflow')
          return { cards: cards.map(rect), footer: rect(footer), rows: getComputedStyle(document.querySelector('.choice-grid')).gridTemplateRows, panelHeight: panel.clientHeight, panelScrollHeight: panel.scrollHeight, issues }
        })
        const name = `${width}x${height}-${n}-${confirm ? 'confirm' : 'choose'}${controls ? '-controls' : ''}`
        await page.screenshot({ path: path.join(out, name + '.png'), fullPage: true })
        results.push({ name, ...report })
        console.log(name, JSON.stringify({ rows: report.rows, issues: report.issues }))
      }
      }
      await page.close()
    }
    fs.writeFileSync(path.join(out, 'geometry.json'), JSON.stringify(results, null, 2))
    if (results.some(x => x.issues.length)) process.exitCode = 1
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
