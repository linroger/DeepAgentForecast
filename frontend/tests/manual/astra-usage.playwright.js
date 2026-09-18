// Run with the isolated ASTRA fixture and Playwright CLI run-code.
// The receipt in docs/research/astra-usage-browser.json records reproduction.
async (page) => {
  const origin = 'http://127.0.0.1:18743';
  const pipelineId = 'pipe_usageqa1';
  const fixture = 'astra-launch-offline/v1';
  const errors = [];
  const blockedRequests = [];
  const statusReads = {known: 0, unavailable: 0, zero: 0};
  let phase = 'known';
  page.on('pageerror', error => errors.push(String(error)));
  const status = () => {
    const available = phase !== 'unavailable';
    const tokens = phase === 'known' ? 2500 : available ? 0 : null;
    return {success: true, data: {
      pipeline_id: pipelineId, mode: 'full', status: 'running', prompt: 'Offline usage transition fixture',
      stages: {}, current_stage: 'research', global_progress: 1,
      live: {
        elapsed_s: 60, heartbeat_age_s: 1, owner_alive: true,
        spend_so_far: {
          available, tokens, cost_usd: phase === 'known' ? 0.75 : available ? 0 : null,
          source: available ? 'durable_ledger' : null,
          coverage: available ? 'recorded_observations' : 'unavailable', usage_complete: false
        },
        budget: {
          available, limit_tokens: 10000, spent_tokens: tokens,
          remaining_tokens: available ? 10000 - tokens : null,
          coverage: available ? 'recorded_observations' : 'unavailable', usage_complete: false
        }
      }
    }};
  };

  // Install the origin/method guard before any page navigation. This fixture
  // has no outbound proxy, and the scenario must never POST a launch.
  await page.context().route('**/*', async route => {
    const request = route.request();
    if (!request.url().startsWith(origin + '/') || request.method() !== 'GET') {
      blockedRequests.push({method: request.method(), url: request.url()});
      return route.abort();
    }
    if (request.url().split('?')[0] === `${origin}/api/research/status/${pipelineId}`) {
      statusReads[phase] += 1;
      return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(status())});
    }
    return route.continue();
  });
  await page.goto(origin + '/__qa');
  const health = await page.evaluate(() => JSON.parse(document.body.innerText));
  if (health.fixture !== fixture) throw new Error('Offline fixture identity was not verified');
  await page.evaluate(({expectedOrigin, expectedFixture, id}) => {
    if (location.origin !== expectedOrigin || JSON.parse(document.body.innerText).fixture !== expectedFixture) {
      throw new Error('Fixture origin or identity changed before storage setup');
    }
    localStorage.removeItem('drf_launch_intent_v1');
    localStorage.setItem('drf_active_pipeline', id);
    localStorage.setItem('drf_locale', 'en');
  }, {expectedOrigin: origin, expectedFixture: fixture, id: pipelineId});
  await page.goto(origin);

  const vitals = page.locator('.run-vitals');
  const fill = vitals.locator('.rv-budget-fill');
  const budget = vitals.locator('.rv-budget');
  const read = async () => ({
    text: await vitals.innerText(),
    budget_title: await budget.getAttribute('title'),
    fill: await fill.evaluate(el => ({
      inline_width: el.style.width,
      same_node: el.dataset.usageFixture === 'retained',
      computed_width: getComputedStyle(el).width,
      display: getComputedStyle(el).display
    }))
  });
  const waitFor = async condition => {
    try {
      await page.waitForFunction(condition, undefined, {timeout: 20000});
    } catch (error) {
      throw new Error(JSON.stringify({phase, statusReads, errors, blockedRequests,
        visible: await vitals.count() ? await read() : await page.locator('body').innerText(),
        cause: String(error)}));
    }
  };
  await waitFor(() => document.querySelector('.rv-budget-fill')?.style.width === '25%');
  await fill.evaluate(el => { el.dataset.usageFixture = 'retained'; });
  const known = await read();
  if (!known.text.includes('2.5K tok') || !known.text.includes('$0.75') || !known.text.includes('7.5K')) {
    throw new Error('Known cumulative spend did not render');
  }

  phase = 'unavailable';
  await waitFor(() => {
    const block = document.querySelector('.run-vitals');
    const bar = block?.querySelector('.rv-budget-fill');
    return block?.textContent.includes('unknown tok') && bar?.style.width === '' && getComputedStyle(bar).display === 'none';
  });
  const unavailable = await read();
  if (!unavailable.fill.same_node || unavailable.fill.inline_width !== '') throw new Error('Stale inline budget width was not cleared on the existing node');
  if (unavailable.fill.display !== 'none') throw new Error('Unknown fill must not imply a full spent budget through auto width');
  if (!unavailable.budget_title.startsWith('unknown / 10K tokens') || !unavailable.text.includes('unknown')) throw new Error('Unknown budget labels are absent');
  if (/2\.5K|7\.5K|\$0\.75|\$0\.00|\b0 tok\b/.test(unavailable.text) || /null%|0%/.test(unavailable.budget_title)) {
    throw new Error('Unavailable accounting retained or invented numeric usage');
  }

  phase = 'zero';
  await waitFor(() => {
    const block = document.querySelector('.run-vitals');
    return block?.textContent.includes('$0.00') && block?.querySelector('.rv-budget-fill')?.style.width === '0%';
  });
  const zero = await read();
  if (!zero.fill.same_node || zero.fill.display === 'none' || !zero.text.includes('0 tok') || !zero.text.includes('10K') || zero.text.includes('unknown')) throw new Error('Recorded zero did not replace unknown accounting');
  if (!zero.budget_title.includes('coverage is incomplete')) throw new Error('Recorded zero omitted incomplete-coverage disclosure');
  if (errors.length || blockedRequests.length) throw new Error(JSON.stringify({errors, blockedRequests}));
  return {
    fixture_origin: origin, fixture_marker: fixture,
    scenarios: {known, unavailable, zero}, status_reads: statusReads,
    assertions: {same_dom_node: true, stale_inline_width_cleared: true,
      unavailable_fill_hidden: true,
      unavailable_labels_explicit: true, unavailable_has_no_numeric_spend: true,
      recorded_zero_visible: true, incomplete_coverage_disclosed: true},
    page_errors: errors, blocked_requests: blockedRequests,
    browser: await page.context().browser().version()
  };
}
