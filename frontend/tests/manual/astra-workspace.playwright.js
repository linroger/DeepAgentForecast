// Run only against the verified offline fixture, using Playwright CLI run-code.
async (page) => {
  const origin = 'http://127.0.0.1:18743';
  const fixture = await (await page.request.get(origin + '/__qa')).json();
  if (fixture.fixture !== 'astra-launch-offline/v1') throw new Error('Wrong fixture');
  if (await page.evaluate(() => location.origin) !== origin) throw new Error('Wrong origin');
  const faults = [];
  page.on('pageerror', error => faults.push(String(error)));
  await page.context().route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
  await page.evaluate(() => { localStorage.clear(); localStorage.setItem('drf_locale', 'en'); });
  await page.request.post(origin + '/__qa/reset', {data: {drop_next: false}});
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const ready = () => page.getByText('Configuration ready', {exact: true}).waitFor();
  const question = () => page.getByRole('textbox', {name: 'What would you like to understand?'});
  const start = () => page.getByRole('button', {name: 'Start research & forecast', exact: true});

  await page.goto(origin + '/research');
  await ready();
  check(await page.evaluate(() => location.pathname) === '/', 'Research alias did not reach root');
  await question().fill('Offline workspace validation');
  check(await start().isEnabled(), 'Valid question did not enable launch');
  const advanced = page.getByRole('button', {name: /Advanced settings/});
  await advanced.focus();
  await page.keyboard.press('Enter');
  check(await advanced.getAttribute('aria-expanded') === 'true', 'Advanced is not keyboard-operable');
  check(await page.getByLabel('Research language', {exact: true}).isVisible(), 'Research language is not labelled');
  check(await page.getByLabel('Research model', {exact: true}).isVisible(), 'Research model is not labelled');
  await advanced.focus();
  await page.keyboard.press('Space');
  check(await advanced.getAttribute('aria-expanded') === 'false', 'Advanced did not close');
  const history = page.getByRole('button', {name: 'History', exact: true});
  await history.click();
  const dialog = page.getByRole('dialog', {name: 'Run history'});
  await dialog.waitFor();
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press(i % 2 ? 'Shift+Tab' : 'Tab');
    check(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]')), 'History focus escaped');
  }
  await page.keyboard.press('Escape');
  await dialog.waitFor({state: 'hidden'});
  check(await history.evaluate(element => element === document.activeElement), 'History focus was not restored');
  await page.getByRole('button', {name: 'DeepResearchForecast · New research'}).click();
  check(await page.evaluate(() => location.pathname) === '/', 'Brand left research');
  await page.goto(origin + '/legacy');
  await ready();
  check(await page.evaluate(() => location.pathname) === '/', 'Legacy entry did not redirect');

  const pattern = '**/api/research/preflight**';
  const pending = [];
  await page.route(pattern, route => pending.push(route));
  await page.reload();
  await page.getByText('Checking configuration…', {exact: true}).waitFor();
  await question().fill('Offline delayed readiness');
  check(await start().isDisabled(), 'Pending readiness enabled launch');
  await page.getByRole('button', {name: 'Research only', exact: true}).click();
  for (let i = 0; pending.length < 2 && i < 100; i++) await page.waitForTimeout(20);
  check(pending.length === 2, 'Expected one preflight request per mode');
  const current = pending.find(route => route.request().url().includes('mode=research_only'));
  const previous = pending.find(route => route !== current);
  check(!!current && !!previous, 'Missing captured modes');
  await current.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({success: true, data: {ready: false, errors: ['Offline mode blocked']}})});
  await page.getByText('Offline mode blocked', {exact: true}).waitFor();
  const oldResponse = page.waitForResponse(response => response.url() === previous.request().url());
  await previous.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({success: true, data: {ready: true, errors: []}})});
  await oldResponse;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
  check(await page.getByText('Offline mode blocked', {exact: true}).isVisible(), 'Stale mode response replaced current readiness');
  await page.unroute(pattern);

  await page.route(pattern, route => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({success: false, error: 'Offline readiness failure'})}));
  await page.reload();
  await page.getByText('Readiness check unavailable', {exact: true}).waitFor();
  await question().fill('Offline unavailable readiness');
  check(await start().isDisabled(), 'Unavailable readiness enabled launch');
  await page.unroute(pattern);
  await page.getByRole('button', {name: 'Retry', exact: true}).click();
  await ready();
  check(await start().isEnabled(), 'Retry did not restore valid readiness');
  await question().fill('');
  await page.setViewportSize({width: 1440, height: 1080});
  await page.screenshot({path: 'output/playwright/astra-workspace-desktop.png', fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Mobile overflow');
  await page.screenshot({path: 'output/playwright/astra-workspace-mobile.png', fullPage: true});
  await page.getByRole('button', {name: 'Switch to Chinese'}).click();
  check(await page.evaluate(() => document.documentElement.lang) === 'zh-CN', 'Chinese locale did not apply');
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Chinese mobile overflow');
  await page.screenshot({path: 'output/playwright/astra-workspace-mobile-zh.png', fullPage: true});
  const summary = await (await page.request.get(origin + '/__qa')).json();
  check(summary.requests.filter(request => request.method === 'POST').length === 0, 'Workspace navigation launched a pipeline');
  check(faults.length === 0, JSON.stringify(faults));
  return {root_alias_and_brand: true, keyboard_advanced: true, history_focus_trap_and_restore: true,
    pending_launch_blocked: true, stale_mode_response_ignored: true, unavailable_truthful: true,
    readiness_retry: true, mobile_overflow: false, chinese_mobile_overflow: false,
    pipeline_posts: 0, page_errors: faults};
}
