/**
 * TDD Regression Tests for UI Ticker Search and Strategy Gate Synchronization
 * Formally ratified under Red-Team Inquest Clauses A-F:
 * - Clause A: Anti-tautological real DOM assertion on renderActiveBoard()
 * - Clause B: Exact match visual priority (e.g. 'C' before 'CAT')
 * - Clause C: Cashtag sanitization ('$AAPL' -> 'AAPL')
 * - Clause D: Semantic index alignment between STRATEGY_HELP and FILTER_IDS
 * - Clause E: Dynamic toggle & localStorage backward compatibility
 * - Clause F: Universal dynamic colSpan across all 3 empty state branches
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const htmlPath = path.resolve(__dirname, '../src/leaps_scanner/api/static/index.html');
const htmlContent = fs.readFileSync(htmlPath, 'utf8');

const scriptMatch = htmlContent.match(/<script>([\s\S]*?)<\/script>/);
if (!scriptMatch) {
  throw new Error('Could not find <script> tag in index.html');
}
const scriptContent = scriptMatch[1];

function createSandbox(initialLocalStorage = {}) {
  const elements = {
    countPass: { innerText: '' },
    countWatch: { innerText: '' },
    countReject: { innerText: '' },
    filterMeta: { innerText: '' },
    tickerSearchMeta: { innerText: '' },
    tickerSearchClear: { style: { display: 'none' } },
    tickerSearchInput: { value: '' },
    tableHeader: { innerHTML: '' },
    tableBody: { innerHTML: '' },
    helpTitle: { innerText: '' },
    helpBody: { innerHTML: '' },
    alphaDisplay: { innerText: '' },
    sliderLegendRight: { innerText: '' },
    engineStatus: { innerText: '' },
    sourceHint: { innerText: '', className: '' },
    universeBadge: { innerText: '' },
    btnSync: { innerText: '' },
    btnScan: { innerText: '' },
    famLeaps: { className: '' },
    famCsp: { className: '' },
    langEn: { className: '' },
    langZh: { className: '' },
    pillAroc: { classList: { toggle: () => {} } },
    pillBuffer: { classList: { toggle: () => {} } },
    pillIvr: { classList: { toggle: () => {} } },
    pillPop: { classList: { toggle: () => {} } },
    pillEarnings: { classList: { toggle: () => {} } },
    pillLiquidity: { classList: { toggle: () => {} } },
    tierWatchlist: { className: '' },
    watchlistPanel: { style: { display: 'none' } },
    watchlistChips: { innerHTML: '' },
    watchlistInput: { value: '' },
    watchlistCount: { innerText: '' },
    watchlistHint: { style: { display: 'none' } },
    watchlistAddBtn: { innerText: '' },
    scanHint: { innerText: '' },
    btnViewGrouped: { classList: { _c: new Set(['on']), add: function(c){this._c.add(c);}, remove: function(c){this._c.delete(c);}, contains: function(c){return this._c.has(c);}, toggle: function(c){return this._c.has(c)?(this._c.delete(c),false):(this._c.add(c),true);} } },
    btnViewFlat: { classList: { _c: new Set(), add: function(c){this._c.add(c);}, remove: function(c){this._c.delete(c);}, contains: function(c){return this._c.has(c);}, toggle: function(c){return this._c.has(c)?(this._c.delete(c),false):(this._c.add(c),true);} } },
    btnToggleAll: { innerText: '', disabled: false, title: '', classList: { _c: new Set(), add: function(c){this._c.add(c);}, remove: function(c){this._c.delete(c);}, contains: function(c){return this._c.has(c);}, toggle: function(c){return this._c.has(c)?(this._c.delete(c),false):(this._c.add(c),true);} } }
  };

  const store = Object.assign({}, initialLocalStorage);

  function makeMockElement(id) {
    const classSet = new Set();
    return {
      id,
      innerText: '',
      innerHTML: '',
      value: '',
      style: {},
      classList: {
        _c: classSet,
        add: (c) => classSet.add(c),
        remove: (c) => classSet.delete(c),
        toggle: (c) => (classSet.has(c) ? (classSet.delete(c), false) : (classSet.add(c), true)),
        contains: (c) => classSet.has(c)
      }
    };
  }

  const sandbox = {
    console,
    document: {
      documentElement: { lang: 'en', dataset: {} },
      getElementById: (id) => elements[id] || (elements[id] = makeMockElement(id)),
      querySelectorAll: () => [],
      querySelector: () => ({ innerText: '' })
    },
    localStorage: {
      getItem: (key) => (store[key] !== undefined ? store[key] : null),
      setItem: (key, val) => { store[key] = String(val); }
    },
    navigator: { language: 'en-US' },
    window: {
      location: { search: '', pathname: '/' },
      history: { replaceState: () => {} },
      getSelection: () => ({ toString: () => '' })
    },
    URLSearchParams,
    performance: { now: () => Date.now() },
    setTimeout: (fn) => fn(),
    clearTimeout: () => {},
    alert: () => {},
    fetch: async () => ({ ok: true, json: async () => ({ boards: { deep_itm: [], vol_discount: [], oversold: [], csp_harvest: [], csp_wheel: [], csp_vol_rank: [] } }) }),
    __elements: elements,
    __store: store
  };

  const context = vm.createContext(sandbox);
  vm.runInContext(scriptContent, context);
  return { context, sandbox, elements, get: (expr) => vm.runInContext(expr, context) };
}

console.log('Running UI Filter & Search TDD Tests (Red-Team Clauses A-F)...');

// Clause D & Gate Sync: FILTER_IDS & STRATEGY_HELP semantic alignment
{
  console.log('Testing Clause D & Gate Sync: FILTER_IDS & STRATEGY_HELP alignment...');
  const { get } = createSandbox();
  const filterIds = get('FILTER_IDS');
  const strategyHelp = get('STRATEGY_HELP');
  const strategyHelpZh = get('STRATEGY_HELP_ZH');

  // vol_discount checks
  assert(filterIds.vol_discount.includes('leverage'), 'FILTER_IDS.vol_discount must include "leverage"');
  assert.strictEqual(strategyHelp.vol_discount.rows.length, filterIds.vol_discount.length, 'vol_discount EN rows must match filter count');
  assert.strictEqual(strategyHelpZh.vol_discount.rows.length, filterIds.vol_discount.length, 'vol_discount ZH rows must match filter count');
  assert.strictEqual(strategyHelp.vol_discount.rows[9][0], 'Effective leverage', 'vol_discount row 9 must be Effective leverage');
  assert(strategyHelpZh.vol_discount.rows[9][0].includes('有效杠杆'), 'vol_discount ZH row 9 must contain 有效杠杆');

  // oversold checks
  assert(filterIds.oversold.includes('leverage'), 'FILTER_IDS.oversold must include "leverage"');
  assert(filterIds.oversold.includes('carry'), 'FILTER_IDS.oversold must include "carry"');
  assert.strictEqual(strategyHelp.oversold.rows.length, filterIds.oversold.length, 'oversold EN rows must match filter count');
  assert.strictEqual(strategyHelpZh.oversold.rows.length, filterIds.oversold.length, 'oversold ZH rows must match filter count');
  assert.strictEqual(strategyHelp.oversold.rows[10][0], 'Effective leverage', 'oversold row 10 must be Effective leverage');
  assert(strategyHelpZh.oversold.rows[10][0].includes('有效杠杆'), 'oversold ZH row 10 must contain 有效杠杆');
  assert(strategyHelp.oversold.rows[11][0].startsWith('Carry'), 'oversold row 11 must start with Carry');
  assert(strategyHelpZh.oversold.rows[11][0].includes('Carry') || strategyHelpZh.oversold.rows[11][0].includes('时间价值损耗'), 'oversold ZH row 11 must mention Carry');

  console.log('✓ Clause D Passed: FILTER_IDS and STRATEGY_HELP semantically aligned.');
}

// Clause A & Clause B: Anti-Tautological DOM assertion & Exact Match Priority
{
  console.log('Testing Clause A & B: Real DOM renderActiveBoard() with exact match priority...');
  const { context, elements, get } = createSandbox();

  const leapsItems = [
    { symbol: 'CAT260116C00250000', underlying: 'CAT', strike: 250, spot: 300, dte: 350, bid: 60, ask: 65, p_exec: 62.5, delta: 0.8, effective_leverage: 3.8, intrinsic_per_share: 50, carry_cost: 0.08, status: 'PASS', reasons: [], open_interest: 500, volume: 100 },
    { symbol: 'C260116C00060000', underlying: 'C', strike: 60, spot: 70, dte: 350, bid: 12, ask: 14, p_exec: 13, delta: 0.78, effective_leverage: 4.2, intrinsic_per_share: 10, carry_cost: 0.09, status: 'PASS', reasons: [], open_interest: 500, volume: 100 },
    { symbol: 'CRM260116C00280000', underlying: 'CRM', strike: 280, spot: 310, dte: 350, bid: 45, ask: 50, p_exec: 47.5, delta: 0.75, effective_leverage: 4.9, intrinsic_per_share: 30, carry_cost: 0.12, status: 'PASS', reasons: [], open_interest: 500, volume: 100 }
  ];

  const cspItems = [
    { candidate: { symbol: 'XLI261016P00110500', underlying: 'XLI', strike: 110, spot: 125, dte: 30, bid: 1.2, ask: 1.3, delta: -0.25 }, status: 'PASS', p_exec: 1.25, aroc: 0.22, roc: 0.02, buffer: 0.12, pop: 0.85, recommended_contracts: 3, capital_required: 33000, max_loss: 32625, stress_pnl: 375 },
    { candidate: { symbol: 'SPY261016P00500000', underlying: 'SPY', strike: 500, spot: 550, dte: 30, bid: 3.5, ask: 3.7, delta: -0.20 }, status: 'PASS', p_exec: 3.6, aroc: 0.18, roc: 0.015, buffer: 0.09, pop: 0.88, recommended_contracts: 1, capital_required: 50000, max_loss: 49640, stress_pnl: 360 }
  ];

  vm.runInContext('cachedBoards = { deep_itm: ' + JSON.stringify(leapsItems) + ', csp_harvest: ' + JSON.stringify(cspItems) + ' };', context);

  // 1. LEAPS Search for underlying 'C' with exact match priority over 'CAT' and 'CRM'
  vm.runInContext("currentFamily = 'leaps'; currentBoardKey = 'deep_itm'; tickerQuery = 'C'; renderActiveBoard();", context);
  const leapsHtml = elements.tableBody.innerHTML;
  assert(leapsHtml.includes('C260116C00060000'), 'LEAPS search for "C" must render Citigroup contract');
  assert(leapsHtml.includes('CAT260116C00250000'), 'LEAPS search for "C" must also render prefix match CAT');
  // Exact match 'C' must precede prefix match 'CAT' in DOM
  const idxC = leapsHtml.indexOf('C260116C00060000');
  const idxCat = leapsHtml.indexOf('CAT260116C00250000');
  assert(idxC < idxCat, 'Exact match "C" must be rendered before prefix match "CAT" (Clause B)');

  // 2. CSP Search for 'XLI'
  vm.runInContext("currentFamily = 'csp'; currentBoardKey = 'csp_harvest'; tickerQuery = 'XLI'; renderActiveBoard();", context);
  const cspHtml = elements.tableBody.innerHTML;
  assert(cspHtml.includes('XLI261016P00110500'), 'CSP search for "XLI" must render XLI put contract in tableBody');
  assert(!cspHtml.includes('SPY261016P00500000'), 'CSP search for "XLI" must not render SPY');

  console.log('✓ Clause A & B Passed: Direct DOM assertions and exact-priority sorting verified.');
}

// Clause C: Cashtag sanitization
{
  console.log('Testing Clause C: Cashtag prefix $ stripped in handleTickerSearchInput...');
  const { context, elements } = createSandbox();
  elements.tickerSearchInput.value = '$AAPL';
  vm.runInContext('handleTickerSearchInput();', context);
  const query = vm.runInContext('tickerQuery;', context);
  assert.strictEqual(query, 'AAPL', 'Cashtag $AAPL must be sanitized to AAPL');
  console.log('✓ Clause C Passed: Cashtag sanitization verified.');
}

// Clause E: Dynamic toggle & LocalStorage backward compatibility
{
  console.log('Testing Clause E: Dynamic toggle & LocalStorage backward compatibility...');
  // 1. Backward compatibility: existing store without 'leverage' or 'carry'
  const legacyStore = {
    leaps_filters_v2: JSON.stringify({
      vol_discount: { strike: true, dte: true },
      oversold: { universe: true, bars: true }
    })
  };
  const { context } = createSandbox(legacyStore);
  const enabledVol = vm.runInContext("enabledFilters['vol_discount'];", context);
  const enabledOver = vm.runInContext("enabledFilters['oversold'];", context);
  assert(enabledVol.has('leverage'), 'Existing legacy localStorage must default newly introduced leverage gate to ENABLED');
  assert(enabledOver.has('leverage'), 'Existing legacy localStorage must default newly introduced leverage gate to ENABLED');
  assert(enabledOver.has('carry'), 'Existing legacy localStorage must default newly introduced carry gate to ENABLED');

  // 2. vol_discount evaluation with leverage REJECT
  vm.runInContext("currentFamily = 'leaps'; currentBoardKey = 'vol_discount';", context);
  const volItem = {
    symbol: 'AAPL260116C00150000',
    underlying: 'AAPL',
    status: 'REJECT',
    gates: {
      iv_history: 'PASS',
      iv_percentile: 'PASS',
      strike: 'PASS',
      dte: 'PASS',
      leverage: 'REJECT'
    }
  };
  let st = vm.runInContext(`effectiveStatus(${JSON.stringify(volItem)});`, context);
  assert.strictEqual(st, 'REJECT', 'vol_discount item with leverage REJECT must evaluate to REJECT');

  // 3. oversold evaluation with carry REJECT
  vm.runInContext("currentBoardKey = 'oversold';", context);
  const oversoldItem = {
    symbol: 'AAPL260116C00150000',
    underlying: 'AAPL',
    status: 'REJECT',
    gates: {
      universe: 'PASS',
      bars: 'PASS',
      confluence: 'PASS',
      strike: 'PASS',
      dte: 'PASS',
      leverage: 'PASS',
      carry: 'REJECT'
    },
    signal_points: { rsi: 1, dma: 1 },
    signal_core: { rsi: true }
  };
  st = vm.runInContext(`effectiveStatus(${JSON.stringify(oversoldItem)});`, context);
  assert.strictEqual(st, 'REJECT', 'oversold item with carry REJECT must evaluate to REJECT');

  // 4. Dynamic user toggle: unchecking carry filter converts status to PASS
  vm.runInContext("enabledFilters['oversold'].delete('carry');", context);
  st = vm.runInContext(`effectiveStatus(${JSON.stringify(oversoldItem)});`, context);
  assert.strictEqual(st, 'PASS', 'Unchecking carry filter must dynamically restore effectiveStatus to PASS');

  console.log('✓ Clause E Passed: Dynamic toggle and legacy localStorage compatibility verified.');
}

// Clause F: Dynamic colSpan across all 3 empty state branches
{
  console.log('Testing Clause F: Dynamic colSpan on empty state branches...');
  const { context, elements } = createSandbox();

  // 1. oversold board (8 columns) empty board
  vm.runInContext("currentFamily = 'leaps'; currentBoardKey = 'oversold'; cachedBoards = { oversold: [] }; tickerQuery = ''; renderActiveBoard();", context);
  assert(elements.tableBody.innerHTML.includes('colspan="8"'), 'oversold empty board must use colspan="8"');

  // 2. vol_discount board (9 columns) ticker no match
  vm.runInContext("currentBoardKey = 'vol_discount'; cachedBoards = { vol_discount: [{ symbol: 'AAPL', underlying: 'AAPL' }] }; tickerQuery = 'XYZ'; renderActiveBoard();", context);
  assert(elements.tableBody.innerHTML.includes('colspan="9"'), 'vol_discount no ticker match must use colspan="9"');

  // 3. deep_itm board (12 columns) status filter no match
  vm.runInContext("currentBoardKey = 'deep_itm'; cachedBoards = { deep_itm: [{ symbol: 'AAPL', underlying: 'AAPL', status: 'REJECT', gates: {} }] }; visibleStatuses.clear(); visibleStatuses.add('PASS'); tickerQuery = ''; renderActiveBoard();", context);
  assert(elements.tableBody.innerHTML.includes('colspan="12"'), 'deep_itm no visible status must use colspan="12"');

  // 4. csp board (13 columns) empty board
  vm.runInContext("currentFamily = 'csp'; currentBoardKey = 'csp_harvest'; cachedBoards = { csp_harvest: [] }; tickerQuery = ''; renderActiveBoard();", context);
  assert(elements.tableBody.innerHTML.includes('colspan="13"'), 'CSP empty board must use colspan="13"');

  console.log('✓ Clause F Passed: Dynamic colSpan correctly applied to all boards.');
}

// Clause G: Co-location of Status Filter and Ticker Search on the same row
{
  console.log('Testing Clause G: Status filter and ticker search co-located in filter-toolbar...');
  const toolbarMatch = htmlContent.match(/<div class="filter-toolbar"[^>]*>([\s\S]*?)<\/div>\s*<!-- Main Table/);
  assert(toolbarMatch, 'index.html must contain a .filter-toolbar container before the main table');
  const toolbarHtml = toolbarMatch[1];
  assert(toolbarHtml.includes('class="status-filter"'), '.filter-toolbar must contain .status-filter');
  assert(toolbarHtml.includes('class="ticker-search"'), '.filter-toolbar must contain .ticker-search');
  assert(htmlContent.includes('.filter-toolbar {'), 'CSS must define .filter-toolbar styles');
  console.log('✓ Clause G Passed: Status filter and ticker search co-located on the same row.');
}

// Clause H: Watchlist Universe & Ticker Management (Red-Team Clauses 7 & 8)
{
  console.log('Testing Clause H: Watchlist universe tier and DOM components...');
  const { context, sandbox, elements, get } = createSandbox();

  // 1. TIER_CONFIG contains watchlist
  const tierConfig = get('TIER_CONFIG');
  assert(tierConfig.watchlist, 'TIER_CONFIG must include "watchlist" entry');
  assert.strictEqual(tierConfig.watchlist.id, 'tierWatchlist', 'watchlist tier id must be tierWatchlist');

  // 2. canonicalizeTier handles watchlist
  const canonicalizeTier = get('canonicalizeTier');
  assert.strictEqual(canonicalizeTier('watchlist'), 'watchlist', 'canonicalizeTier must preserve "watchlist"');
  assert.strictEqual(canonicalizeTier('WATCHLIST'), 'watchlist', 'canonicalizeTier must handle case-insensitivity');
  assert.strictEqual(canonicalizeTier('watch'), 'watchlist', 'canonicalizeTier must handle "watch" alias');

  // 3. HTML markup contains tier button and watchlist panel
  assert(htmlContent.includes('id="tierWatchlist"'), 'index.html must include #tierWatchlist button');
  assert(htmlContent.includes('id="watchlistPanel"'), 'index.html must include #watchlistPanel');
  assert(htmlContent.includes('id="watchlistChips"'), 'index.html must include #watchlistChips container');
  assert(htmlContent.includes('id="watchlistInput"'), 'index.html must include #watchlistInput field');
  assert(htmlContent.includes('id="watchlistAddBtn"'), 'index.html must include #watchlistAddBtn');

  // 4. paintScanTier toggles #watchlistPanel display
  const paintScanTier = get('paintScanTier');
  paintScanTier('watchlist');
  assert.strictEqual(elements.watchlistPanel.style.display, 'flex', 'paintScanTier("watchlist") must show #watchlistPanel as flex');
  paintScanTier('etfs');
  assert.strictEqual(elements.watchlistPanel.style.display, 'none', 'paintScanTier("etfs") must hide #watchlistPanel');

  // 5. renderWatchlistChips updates chips and count
  const renderWatchlistChips = get('renderWatchlistChips');
  renderWatchlistChips(['AAPL', 'NVDA']);
  assert(elements.watchlistChips.innerHTML.includes('AAPL'), 'renderWatchlistChips must render AAPL chip');
  assert(elements.watchlistChips.innerHTML.includes('NVDA'), 'renderWatchlistChips must render NVDA chip');
  assert(elements.watchlistChips.innerHTML.includes('removeWatchlistTicker'), 'Chips must contain remove buttons');
  assert.strictEqual(elements.watchlistCount.innerText, '2 symbols', 'watchlistCount must display symbol count');
  assert.strictEqual(elements.watchlistHint.style.display, 'none', 'watchlistHint must be hidden when symbols present');

  // 6. renderWatchlistChips([]) reveals empty hint
  renderWatchlistChips([]);
  assert.strictEqual(elements.watchlistCount.innerText, '0 symbols', 'watchlistCount must display 0 symbols');
  assert.strictEqual(elements.watchlistHint.style.display, 'block', 'watchlistHint must be displayed when empty');

  // 7. paintScanTier with 0 watchlist symbols preserves 0 (no fallback to 5)
  paintScanTier('watchlist', { universe: { watchlist: 0 }, scan_status: 'idle' });
  assert(elements.scanHint.innerText.includes('0'), 'paintScanTier for watchlist with 0 symbols must not fallback to 5');

  // 8. fetchWatchlist with mock fetch
  (async () => {
    let lastUrl = '';
    let lastOptions = null;
    sandbox.fetch = async (url, options) => {
      lastUrl = url;
      lastOptions = options;
      if (url === '/api/v1/watchlist' && (!options || options.method === 'GET')) {
        return { ok: true, json: async () => ({ status: 'ok', symbols: ['MSFT', 'TSLA'], watchlist: ['MSFT', 'TSLA'] }) };
      }
      if (url === '/api/v1/watchlist' && options && options.method === 'POST') {
        const body = JSON.parse(options.body);
        return { ok: true, json: async () => ({ status: 'ok', symbols: ['MSFT', 'TSLA', body.symbol], watchlist: ['MSFT', 'TSLA', body.symbol] }) };
      }
      if (url.startsWith('/api/v1/watchlist') && options && options.method === 'DELETE') {
        return { ok: true, json: async () => ({ status: 'ok', symbols: ['MSFT'], watchlist: ['MSFT'] }) };
      }
      return { ok: true, json: async () => ({}) };
    };

    // Test fetchWatchlist()
    const fetchWatchlist = get('fetchWatchlist');
    await fetchWatchlist();
    assert(elements.watchlistChips.innerHTML.includes('MSFT'), 'fetchWatchlist must render MSFT');
    assert(elements.watchlistChips.innerHTML.includes('TSLA'), 'fetchWatchlist must render TSLA');
    assert.strictEqual(elements.watchlistCount.innerText, '2 symbols', 'watchlistCount must be 2 symbols');

    // Test addWatchlistTicker() with cashtag
    elements.watchlistInput.value = '$PLTR';
    const addWatchlistTicker = get('addWatchlistTicker');
    await addWatchlistTicker();
    assert.strictEqual(lastUrl, '/api/v1/watchlist');
    assert.strictEqual(lastOptions.method, 'POST');
    const sentBody = JSON.parse(lastOptions.body);
    assert.strictEqual(sentBody.symbol, 'PLTR', 'POST body must contain cleaned symbol');
    assert.strictEqual(sentBody.ticker, 'PLTR', 'POST body must contain ticker alias');
    assert(elements.watchlistChips.innerHTML.includes('PLTR'), 'Chips must now include PLTR');
    assert.strictEqual(elements.watchlistInput.value, '', 'Input must be cleared after adding');

    // Test removeWatchlistTicker()
    const removeWatchlistTicker = get('removeWatchlistTicker');
    await removeWatchlistTicker('TSLA');
    assert(lastUrl.includes('symbol=TSLA'), 'DELETE url must contain symbol parameter');
    assert(lastUrl.includes('ticker=TSLA'), 'DELETE url must contain ticker parameter');
    assert(!elements.watchlistChips.innerHTML.includes('TSLA'), 'Chips must no longer include TSLA');

    // Item 2: backend error message must surface in the alert, not the generic fallback
    let alerted = null;
    sandbox.alert = (msg) => { alerted = msg; };
    sandbox.fetch = async () => ({
      ok: false,
      json: async () => ({ status: 'error', message: 'AAPL already in watchlist' }),
    });
    elements.watchlistInput.value = 'AAPL';
    await addWatchlistTicker();
    assert(alerted && alerted.includes('already in watchlist'),
      'alert must show the backend message, got: ' + alerted);

    console.log('✓ Clause H Passed: Watchlist universe tier configuration, DOM elements, and mock API interactions verified.');
    
    // ==========================================
    // Clause I: Ticker Grouping & Option Chain Accordion
    // ==========================================
    console.log('Testing Clause I: Ticker Grouping & Option Chain Accordion...');
    
    // 1. groupItemsByUnderlying with LEAPS & CSP
    const groupItemsByUnderlying = get('groupItemsByUnderlying');
    assert(typeof groupItemsByUnderlying === 'function', 'groupItemsByUnderlying must be a defined function');

    const sampleLeaps = [
      {
        symbol: 'AAPL260116C00130000',
        underlying: 'AAPL',
        strike: 130,
        spot: 150,
        dte: 350,
        bid: 30,
        ask: 31,
        p_exec: 30.5,
        delta: 0.85,
        effective_leverage: 3.8,
        intrinsic_per_share: 20,
        carry_cost: 0.032,
        theta_daily_pct: -0.0001,
        status: 'PASS'
      },
      {
        symbol: 'AAPL260116C00140000',
        underlying: 'AAPL',
        strike: 140,
        spot: 150,
        dte: 400,
        bid: 22,
        ask: 23,
        p_exec: 22.5,
        delta: 0.80,
        effective_leverage: 3.2,
        intrinsic_per_share: 10,
        carry_cost: 0.045,
        theta_daily_pct: -0.00012,
        status: 'WATCH'
      },
      {
        symbol: 'NVDA260116C00100000',
        underlying: 'NVDA',
        strike: 100,
        spot: 120,
        dte: 500,
        bid: 35,
        ask: 36,
        p_exec: 35.5,
        delta: 0.88,
        effective_leverage: 2.8,
        intrinsic_per_share: 20,
        carry_cost: 0.05,
        theta_daily_pct: -0.00015,
        status: 'WATCH'
      }
    ];

    const leapsGroups = groupItemsByUnderlying(sampleLeaps, 'deep_itm', 'leaps');
    assert.strictEqual(leapsGroups.length, 2, 'Should create 2 ticker groups for AAPL and NVDA');

    const aaplGroup = leapsGroups.find(g => g.ticker === 'AAPL');
    assert(aaplGroup, 'AAPL group must exist');
    assert.strictEqual(aaplGroup.items.length, 2, 'AAPL group must have 2 items');
    assert.strictEqual(aaplGroup.spot, 150, 'AAPL spot must be 150');
    assert.strictEqual(aaplGroup.minStrike, 130, 'AAPL minStrike must be 130');
    assert.strictEqual(aaplGroup.maxStrike, 140, 'AAPL maxStrike must be 140');
    assert.strictEqual(aaplGroup.minDte, 350, 'AAPL minDte must be 350');
    assert.strictEqual(aaplGroup.maxDte, 400, 'AAPL maxDte must be 400');
    assert.strictEqual(aaplGroup.minLeverage, 3.2, 'AAPL minLeverage must be 3.2');
    assert.strictEqual(aaplGroup.maxLeverage, 3.8, 'AAPL maxLeverage must be 3.8');
    assert.strictEqual(aaplGroup.minCarry, 0.032, 'AAPL minCarry must be 0.032');
    assert.strictEqual(aaplGroup.maxCarry, 0.045, 'AAPL maxCarry must be 0.045');
    assert.strictEqual(aaplGroup.bestStatus, 'PASS', 'AAPL bestStatus must be PASS (PASS > WATCH)');
    assert.strictEqual(aaplGroup.passCount, 1, 'AAPL passCount must be 1');
    assert.strictEqual(aaplGroup.watchCount, 1, 'AAPL watchCount must be 1');
    assert.strictEqual(aaplGroup.bestItem.symbol, 'AAPL260116C00130000', 'bestItem should be the PASS contract');

    // 2. CSP Candidate format aggregation
    const sampleCsp = [
      {
        candidate: { symbol: 'MSFT260116P00380000', underlying: 'MSFT', strike: 380, spot: 420, dte: 45, bid: 5, ask: 5.5, delta: -0.22 },
        p_exec: 5.2,
        aroc: 0.22,
        buffer: 0.095,
        pop: 0.78,
        capital_info: { recommended_contracts: 2, required_capital_per_contract: 38000 },
        status: 'PASS'
      },
      {
        candidate: { symbol: 'MSFT260116P00370000', underlying: 'MSFT', strike: 370, spot: 420, dte: 45, bid: 3, ask: 3.5, delta: -0.18 },
        p_exec: 3.2,
        aroc: 0.16,
        buffer: 0.119,
        pop: 0.83,
        capital_info: { recommended_contracts: 2, required_capital_per_contract: 37000 },
        status: 'PASS'
      }
    ];

    const cspGroups = groupItemsByUnderlying(sampleCsp, 'csp_harvest', 'csp');
    assert.strictEqual(cspGroups.length, 1, 'CSP items must aggregate to 1 group for MSFT');
    assert.strictEqual(cspGroups[0].ticker, 'MSFT');
    assert.strictEqual(cspGroups[0].items.length, 2);
    assert.strictEqual(cspGroups[0].minAroc, 0.16);
    assert.strictEqual(cspGroups[0].maxAroc, 0.22);
    assert.strictEqual(cspGroups[0].minBuffer, 0.095);
    assert.strictEqual(cspGroups[0].maxBuffer, 0.119);

    // 3. View Mode switching & LocalStorage
    const setViewMode = get('setViewMode');
    assert(typeof setViewMode === 'function', 'setViewMode must be a function');
    assert.strictEqual(get('viewMode'), 'grouped', 'Default viewMode must be grouped');
    setViewMode('flat');
    assert.strictEqual(get('viewMode'), 'flat', 'viewMode should now be flat');
    assert.strictEqual(sandbox.localStorage.getItem('leaps_view_mode'), 'flat', 'localStorage leaps_view_mode should be flat');
    assert(elements.btnViewFlat.classList.contains('on'), 'Flat button should have .on class');
    assert(!elements.btnViewGrouped.classList.contains('on'), 'Grouped button should NOT have .on class');
    assert.strictEqual(elements.btnToggleAll.disabled, true, 'btnToggleAll must be disabled in flat mode');
    assert(elements.btnToggleAll.classList.contains('disabled'), 'btnToggleAll must have .disabled class in flat mode');

    setViewMode('grouped');
    assert.strictEqual(get('viewMode'), 'grouped', 'viewMode should now be grouped');
    assert.strictEqual(sandbox.localStorage.getItem('leaps_view_mode'), 'grouped', 'localStorage leaps_view_mode should be grouped');
    assert(elements.btnViewGrouped.classList.contains('on'), 'Grouped button should have .on class');
    assert(!elements.btnViewFlat.classList.contains('on'), 'Flat button should NOT have .on class');
    assert.strictEqual(elements.btnToggleAll.disabled, false, 'btnToggleAll must be enabled in grouped mode');
    assert(!elements.btnToggleAll.classList.contains('disabled'), 'btnToggleAll must NOT have .disabled class in grouped mode');

    // 4. Accordion Toggle & Batch Operations
    const toggleTickerExpand = get('toggleTickerExpand');
    const expandAllTickers = get('expandAllTickers');
    const collapseAllTickers = get('collapseAllTickers');
    assert(typeof toggleTickerExpand === 'function', 'toggleTickerExpand must be a function');
    assert(typeof expandAllTickers === 'function', 'expandAllTickers must be a function');
    assert(typeof collapseAllTickers === 'function', 'collapseAllTickers must be a function');

    toggleTickerExpand('AAPL');
    assert(get('expandedTickers').has('AAPL'), 'expandedTickers should contain AAPL after toggle');
    toggleTickerExpand('AAPL');
    assert(!get('expandedTickers').has('AAPL'), 'expandedTickers should NOT contain AAPL after second toggle');

    // 5. Clause 3: Transient Search Auto-Expansion Invariant
    // Populate cachedBoards with sample data
    vm.runInContext("cachedBoards = { deep_itm: " + JSON.stringify(sampleLeaps) + " };", context);
    vm.runInContext("currentBoardKey = 'deep_itm'; currentFamily = 'leaps';", context);
    vm.runInContext("visibleStatuses.add('PASS'); visibleStatuses.add('WATCH'); visibleStatuses.add('REJECT');", context);
    get('renderActiveBoard')();

    // Ensure expandedTickers is empty initially
    collapseAllTickers();
    assert.strictEqual(get('expandedTickers').size, 0, 'expandedTickers must be empty initially');

    // Search for NVDA (unique match)
    vm.runInContext("tickerQuery = 'NVDA';", context);
    get('renderActiveBoard')();
    // In grouped mode, NVDA detail row should be rendered and expanded
    assert(elements.tableBody.innerHTML.includes('detail-NVDA'), 'detail-NVDA row must be present');
    assert(elements.tableBody.innerHTML.includes('open') || !elements.tableBody.innerHTML.includes('style="display: none;" id="detail-NVDA"'), 'NVDA should be auto-expanded in view');
    // CRITICAL Clause 3 check: expandedTickers must NOT be polluted
    assert.strictEqual(get('expandedTickers').size, 0, 'Clause 3: expandedTickers must remain size 0 after transient auto-expand');

    // Clear search
    get('clearTickerSearch')();
    assert.strictEqual(get('expandedTickers').size, 0, 'expandedTickers remains 0');
    // NVDA detail row should now be collapsed (hidden)
    assert(elements.tableBody.innerHTML.includes('style="display: none;" id="detail-NVDA"') || elements.tableBody.innerHTML.includes('id="detail-NVDA" style="display: none;"'), 'NVDA must revert to collapsed');

    // 6. Clause 4: Dynamic ColSpan Verification
    const boardsColSpans = [
      { key: 'csp_harvest', family: 'csp', expected: 13 },
      { key: 'deep_itm', family: 'leaps', expected: 12 },
      { key: 'vol_discount', family: 'leaps', expected: 9 },
      { key: 'oversold', family: 'leaps', expected: 8 }
    ];

    for (const b of boardsColSpans) {
      vm.runInContext(`currentBoardKey = '${b.key}'; currentFamily = '${b.family}';`, context);
      vm.runInContext(`cachedBoards = { '${b.key}': [] };`, context);
      get('renderActiveBoard')();
      assert(elements.tableBody.innerHTML.includes(`colspan="${b.expected}"`), `Board ${b.key} must render empty state with colSpan ${b.expected}`);
    }

    console.log('✓ Clause I Passed: Ticker Grouping and Option Chain Accordion verified successfully.');
    console.log('All Red-Team clauses A-I verified successfully! 🎉');
  })().then(() => {
    // Finished successfully
  }).catch((err) => {
    console.error(err);
    process.exit(1);
  });
}

// Clause J: CSP DTE pills isolate client-side (do not wait on rerank)
{
  console.log('Testing Clause J: CSP DTE bucket isolate + client filter...');
  const { context, elements, get } = createSandbox();

  assert.strictEqual(get('getDteBucket(0.1)'), '<7');
  assert.strictEqual(get('getDteBucket(6.99)'), '<7');
  assert.strictEqual(get('getDteBucket(7)'), '7-14');
  assert.strictEqual(get('getDteBucket(28.1)'), '28-45');
  assert.strictEqual(get('getDteBucket(42.1)'), '28-45');
  assert.strictEqual(get('formatCspDte(0.1)'), '0.1d');
  assert.strictEqual(get('formatCspDte(3.1)'), '3.1d');
  assert.strictEqual(get('formatCspDte(28.1)'), '28d');
  assert.strictEqual(get('formatCspDte(42.1)'), '42d');

  function cspRow(und, dte, status, sym) {
    return {
      candidate: {
        underlying: und, symbol: sym, dte, strike: 100, spot: 110,
        bid: 1.2, ask: 1.4, delta: -0.22, earnings_status: 'CONFIRMED_SAFE'
      },
      p_exec: 1.3, roc: 0.013, aroc: 0.22, buffer: 0.09, pop: 0.8,
      capital_info: { recommended_contracts: 1, required_capital_per_contract: 10000, total_max_loss: 9870 },
      status
    };
  }

  const harvest = [
    cspRow('AAPL', 3.1, 'REJECT', 'AAPL260921P00100000'),
    cspRow('AAPL', 10.1, 'PASS', 'AAPL260925P00100000'),
    cspRow('AAPL', 21.1, 'PASS', 'AAPL261002P00100000'),
    cspRow('AAPL', 35.1, 'PASS', 'AAPL261023P00100000'),
    cspRow('MSFT', 0.1, 'REJECT', 'MSFT260918P00100000'),
    cspRow('MSFT', 42.1, 'WATCH', 'MSFT261030P00100000')
  ];

  vm.runInContext("currentFamily = 'csp'; currentBoardKey = 'csp_harvest'; viewMode = 'flat'; tickerQuery = '';", context);
  vm.runInContext('visibleStatuses.clear(); visibleStatuses.add("PASS"); visibleStatuses.add("WATCH");', context);
  vm.runInContext('cachedBoards = ' + JSON.stringify({ csp_harvest: harvest, harvest: harvest }) + ';', context);

  get('renderActiveBoard')();
  assert(elements.tableBody.innerHTML.includes('AAPL260925P00100000'), 'default view still shows 7-14 PASS');
  assert(elements.tableBody.innerHTML.includes('AAPL261023P00100000'), 'default view still shows 28-45 PASS');
  assert(!elements.tableBody.innerHTML.includes('AAPL260921P00100000'), 'default PASS+WATCH hides <7 REJECT');
  assert.strictEqual(elements.countDteUnder7.innerText, '2', '<7 pill must show scan count including REJECT');
  assert.strictEqual(elements.countDte28to45.innerText, '2', '28-45 pill must show scan count');

  get("toggleCspDteBucket('<7')");
  const isolatedShort = get('JSON.stringify(cspFilters.dte_buckets)');
  assert.strictEqual(JSON.parse(isolatedShort)['<7'], true);
  assert.strictEqual(JSON.parse(isolatedShort)['7-14'], false);
  assert.strictEqual(JSON.parse(isolatedShort)['28-45'], false);
  assert(elements.tableBody.innerHTML.includes('AAPL260921P00100000'), 'isolating <7 must surface weekly REJECT rows');
  assert(elements.tableBody.innerHTML.includes('0.1d') || elements.tableBody.innerHTML.includes('3.1d'), 'weekly DTE must render as tenths of a day');
  assert(!elements.tableBody.innerHTML.includes('AAPL260925P00100000'), 'isolating <7 must hide 7-14');
  assert(!elements.tableBody.innerHTML.includes('AAPL261023P00100000'), 'isolating <7 must hide 28-45');

  get("toggleCspDteBucket('28-45')");
  const isolatedLong = JSON.parse(get('JSON.stringify(cspFilters.dte_buckets)'));
  assert.strictEqual(isolatedLong['28-45'], true);
  assert.strictEqual(isolatedLong['<7'], false);
  assert(elements.tableBody.innerHTML.includes('AAPL261023P00100000'), 'clicking 28-45 must isolate that window');
  assert(elements.tableBody.innerHTML.includes('MSFT261030P00100000'), '42 DTE contract must remain visible in 28-45');
  assert(!elements.tableBody.innerHTML.includes('AAPL260925P00100000'), '28-45 isolate must drop 7-14');
  assert(!elements.tableBody.innerHTML.includes('AAPL260921P00100000'), '28-45 isolate must drop weeklies');

  get('selectAllCspDteBuckets()');
  const restored = JSON.parse(get('JSON.stringify(cspFilters.dte_buckets)'));
  assert.strictEqual(restored['<7'], true);
  assert.strictEqual(restored['28-45'], true);
  assert(elements.tableBody.innerHTML.includes('AAPL260925P00100000'), 'All restores 7-14');
  assert(elements.tableBody.innerHTML.includes('AAPL261023P00100000'), 'All restores 28-45');

  console.log('✓ Clause J Passed: CSP DTE isolate filter is client-side and visible.');
}

