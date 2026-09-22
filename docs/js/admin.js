/* ============================================
   Solace Scoop - Secure Admin Dashboard
   ============================================ */
(function () {
  'use strict';

  var API_BASE = String(window.SCOOP_API_URL || '').replace(/\/$/, '');
  var TOKEN_KEY = 'scoop_admin_access_token';
  var EXPIRY_KEY = 'scoop_admin_expires_at';
  var PAGE_SIZE = 25;
  var accessToken = '';
  var expiresAt = 0;
  var expiryTimer = 0;
  var summaryData = null;

  var subscriberPages = createPager('subscriber', '/api/admin/subscribers', renderSubscribers, 6);
  var deliveryPages = createPager('delivery', '/api/admin/deliveries', renderDeliveries, 5);

  function byId(id) { return document.getElementById(id); }

  function storageGet(key) {
    try { return window.sessionStorage.getItem(key) || ''; }
    catch (error) { return ''; }
  }

  function storageSet(key, value) {
    try {
      window.sessionStorage.setItem(key, value);
      return true;
    } catch (error) {
      return false;
    }
  }

  function storageRemove(key) {
    try { window.sessionStorage.removeItem(key); }
    catch (error) { /* The in-memory token is still cleared below. */ }
  }

  function saveSession(token, lifetimeSeconds) {
    accessToken = token;
    expiresAt = Date.now() + (lifetimeSeconds * 1000);
    storageSet(TOKEN_KEY, accessToken);
    storageSet(EXPIRY_KEY, String(expiresAt));
    scheduleExpiry();
  }

  function restoreSession() {
    var token = storageGet(TOKEN_KEY);
    var expiry = Number(storageGet(EXPIRY_KEY));
    if (!token || !Number.isFinite(expiry) || expiry <= Date.now()) {
      clearSession();
      return false;
    }
    accessToken = token;
    expiresAt = expiry;
    scheduleExpiry();
    return true;
  }

  function clearSession() {
    accessToken = '';
    expiresAt = 0;
    if (expiryTimer) window.clearTimeout(expiryTimer);
    expiryTimer = 0;
    storageRemove(TOKEN_KEY);
    storageRemove(EXPIRY_KEY);
  }

  function scheduleExpiry() {
    if (expiryTimer) window.clearTimeout(expiryTimer);
    var remaining = expiresAt - Date.now();
    if (remaining <= 0) {
      expireSession();
      return;
    }
    expiryTimer = window.setTimeout(expireSession, Math.min(remaining, 2147483647));
  }

  function expireSession() {
    clearSession();
    showLogin('Your admin session expired. Sign in again to continue.', 'expired');
  }

  function showLogin(message, kind) {
    byId('dashboard-view').hidden = true;
    byId('logout-button').hidden = true;
    byId('login-view').hidden = false;
    setLoginLoading(false);
    byId('admin-password').value = '';
    setLoginMessage(message, kind);
    window.requestAnimationFrame(function () { byId('admin-username').focus(); });
  }

  function showDashboard() {
    byId('login-view').hidden = true;
    byId('dashboard-view').hidden = false;
    byId('logout-button').hidden = false;
  }

  function setLoginMessage(message, kind) {
    var box = byId('login-message');
    box.className = 'admin-message';
    if (!message) {
      box.hidden = true;
      byId('login-message-text').textContent = '';
      return;
    }
    box.classList.add(kind === 'expired' ? 'admin-message--expired' : kind === 'warning' ? 'admin-message--warning' : 'admin-message--error');
    byId('login-message-text').textContent = message;
    box.hidden = false;
  }

  function setLoginLoading(loading) {
    var button = byId('login-button');
    button.disabled = loading;
    button.querySelector('.admin-button__label').hidden = loading;
    button.querySelector('.admin-button__loading').hidden = !loading;
    byId('admin-username').disabled = loading;
    byId('admin-password').disabled = loading;
  }

  function ApiError(message, status, code, retryAfter) {
    this.name = 'ApiError';
    this.message = message;
    this.status = status || 0;
    this.code = code || '';
    this.retryAfter = retryAfter || 0;
  }
  ApiError.prototype = Object.create(Error.prototype);

  function parseRetryAfter(response) {
    var value = response.headers.get('Retry-After');
    if (!value) return 0;
    var seconds = Number(value);
    if (Number.isFinite(seconds)) return Math.max(0, Math.ceil(seconds));
    var date = Date.parse(value);
    return Number.isFinite(date) ? Math.max(0, Math.ceil((date - Date.now()) / 1000)) : 0;
  }

  async function request(path, options) {
    var settings = options || {};
    var authenticated = settings.authenticated !== false;

    if (authenticated && (!accessToken || expiresAt <= Date.now())) {
      expireSession();
      throw new ApiError('Session expired', 401, 'auth');
    }

    var headers = { 'Accept': 'application/json' };
    if (settings.body !== undefined) headers['Content-Type'] = 'application/json';
    if (authenticated) headers.Authorization = 'Bearer ' + accessToken;

    var response;
    try {
      response = await window.fetch(API_BASE + path, {
        method: settings.method || 'GET',
        headers: headers,
        body: settings.body === undefined ? undefined : JSON.stringify(settings.body),
        cache: 'no-store',
        credentials: 'omit',
        referrerPolicy: 'no-referrer'
      });
    } catch (error) {
      throw new ApiError('Unable to reach the Scoop API.', 0, 'network');
    }

    var data = null;
    try { data = await response.json(); }
    catch (error) { data = null; }

    if (!response.ok) {
      var message = data && (data.error || data.detail || data.message);
      if (authenticated && (response.status === 401 || response.status === 403)) {
        clearSession();
        showLogin('Your admin session is no longer valid. Sign in again.', 'expired');
        throw new ApiError('Session expired', response.status, 'auth');
      }
      if (response.status === 429) {
        throw new ApiError(message || 'Too many requests.', response.status, 'rate-limit', parseRetryAfter(response));
      }
      throw new ApiError(message || 'The request could not be completed.', response.status, 'api');
    }

    return data || {};
  }

  function formatCount(value) {
    var number = Number(value);
    return Number.isFinite(number) ? new Intl.NumberFormat('en-US').format(number) : '0';
  }

  function formatCompactCount(value) {
    var number = Number(value);
    if (!Number.isFinite(number)) return '0';
    return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(number);
  }

  function formatDateTime(value, timezone) {
    if (!value) return 'Never';
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'Unknown';
    var options = {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit', timeZoneName: 'short'
    };
    if (timezone) options.timeZone = timezone;
    try { return new Intl.DateTimeFormat('en-US', options).format(date); }
    catch (error) {
      delete options.timeZone;
      return new Intl.DateTimeFormat('en-US', options).format(date);
    }
  }

  function formatWeek(value) {
    if (!value) return 'Unknown week';
    var date = new Date(String(value) + (String(value).indexOf('T') === -1 ? 'T00:00:00Z' : ''));
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(date);
  }

  function rateLimitMessage(error) {
    return error.retryAfter > 0
      ? 'Request limit reached. Try again in ' + error.retryAfter + ' seconds.'
      : 'Request limit reached. Please wait a moment and try again.';
  }

  function showDashboardMessage(message, kind, retry) {
    var box = byId('dashboard-message');
    box.replaceChildren();
    box.className = 'admin-message';
    if (!message) {
      box.hidden = true;
      return;
    }
    if (kind === 'error') box.classList.add('admin-message--error');
    if (kind === 'warning') box.classList.add('admin-message--warning');
    var text = document.createElement('p');
    text.textContent = message;
    box.appendChild(text);
    if (typeof retry === 'function') {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'admin-button admin-button--quiet';
      button.textContent = 'Retry';
      button.addEventListener('click', retry, { once: true });
      box.appendChild(button);
    }
    box.hidden = false;
  }

  function createPager(prefix, path, renderer, columnCount) {
    return {
      prefix: prefix,
      path: path,
      renderer: renderer,
      columnCount: columnCount,
      cursor: '',
      nextCursor: '',
      history: [],
      page: 1,
      itemCount: null,
      loading: false
    };
  }

  function resetPager(pager) {
    pager.cursor = '';
    pager.nextCursor = '';
    pager.history = [];
    pager.page = 1;
    pager.itemCount = null;
    pager.loading = false;
  }

  function setPagerControls(pager, itemCount) {
    if (typeof itemCount === 'number') pager.itemCount = itemCount;
    byId(pager.prefix + '-prev').disabled = pager.loading || pager.history.length === 0;
    byId(pager.prefix + '-next').disabled = pager.loading || !pager.nextCursor;
    var label = 'Page ' + pager.page;
    if (typeof pager.itemCount === 'number') label += ' · ' + pager.itemCount + (pager.itemCount === 1 ? ' record' : ' records');
    byId(pager.prefix + '-page-status').textContent = label;
  }

  function renderTableState(pager, message, retry) {
    var body = byId(pager.prefix + '-table-body');
    body.replaceChildren();
    var row = document.createElement('tr');
    row.className = 'admin-table__empty';
    var cell = document.createElement('td');
    cell.colSpan = pager.columnCount;
    cell.textContent = message;
    if (typeof retry === 'function') {
      cell.appendChild(document.createTextNode(' '));
      var button = document.createElement('button');
      button.className = 'admin-button admin-button--quiet';
      button.type = 'button';
      button.textContent = 'Retry';
      button.addEventListener('click', retry, { once: true });
      cell.appendChild(button);
    }
    row.appendChild(cell);
    body.appendChild(row);
  }

  async function loadPage(pager) {
    if (pager.loading) return;
    pager.loading = true;
    renderTableState(pager, 'Loading…');
    setPagerControls(pager);
    var query = '?limit=' + PAGE_SIZE;
    if (pager.cursor) query += '&cursor=' + encodeURIComponent(pager.cursor);

    try {
      var data = await request(pager.path + query);
      var items = Array.isArray(data.items) ? data.items : [];
      pager.nextCursor = typeof data.next_cursor === 'string' ? data.next_cursor : '';
      pager.renderer(items);
      setPagerControls(pager, items.length);
    } catch (error) {
      if (error.code === 'auth') return;
      var message = error.code === 'rate-limit' ? rateLimitMessage(error) : error.code === 'network'
        ? 'Network connection failed.' : 'Could not load this table.';
      renderTableState(pager, message, function () { loadPage(pager); });
      setPagerControls(pager);
    } finally {
      pager.loading = false;
      setPagerControls(pager);
    }
  }

  function nextPage(pager) {
    if (!pager.nextCursor || pager.loading) return;
    pager.history.push(pager.cursor);
    pager.cursor = pager.nextCursor;
    pager.page += 1;
    loadPage(pager);
  }

  function previousPage(pager) {
    if (!pager.history.length || pager.loading) return;
    pager.cursor = pager.history.pop();
    pager.page = Math.max(1, pager.page - 1);
    loadPage(pager);
  }

  function appendCell(row, value, className) {
    var cell = document.createElement('td');
    cell.textContent = value;
    if (className) cell.className = className;
    row.appendChild(cell);
    return cell;
  }

  function renderSubscribers(items) {
    var body = byId('subscriber-table-body');
    body.replaceChildren();
    if (!items.length) {
      renderTableState(subscriberPages, subscriberPages.page === 1 ? 'No subscribers found.' : 'No subscribers on this page.');
      return;
    }
    items.forEach(function (subscriber) {
      var row = document.createElement('tr');
      appendCell(row, subscriber.email || 'Unknown', 'admin-table__primary');
      appendCell(row, subscriber.product || 'Not set', subscriber.product ? '' : 'admin-table__muted');
      var accounts = Array.isArray(subscriber.accounts) ? subscriber.accounts : [];
      appendCell(
        row,
        accounts.length ? accounts.join(', ') : 'No accounts',
        accounts.length ? 'admin-table__accounts' : 'admin-table__muted'
      );
      appendCell(row, formatCount(subscriber.articles_this_week), 'admin-table__number');
      appendCell(row, formatCount(subscriber.delivered_email_count), 'admin-table__number');
      appendCell(row, formatDateTime(subscriber.last_delivered_at, getTimezone()), subscriber.last_delivered_at ? '' : 'admin-table__muted');
      body.appendChild(row);
    });
  }

  function renderDeliveries(items) {
    var body = byId('delivery-table-body');
    body.replaceChildren();
    if (!items.length) {
      renderTableState(deliveryPages, deliveryPages.page === 1 ? 'No delivered emails found.' : 'No delivered emails on this page.');
      return;
    }
    items.forEach(function (delivery) {
      var row = document.createElement('tr');
      appendCell(row, delivery.subscriber_email || 'Unknown', 'admin-table__primary');
      appendCell(row, formatDateTime(delivery.delivered_at, getTimezone()));
      appendCell(row, formatCount(delivery.item_count), 'admin-table__number');
      appendCell(row, delivery.is_breaking ? 'Breaking' : 'Weekly', 'admin-type');
      var statusCell = document.createElement('td');
      var status = document.createElement('span');
      var statusName = String(delivery.status || 'unknown').toLowerCase();
      status.className = 'admin-status admin-status--' + statusName.replace(/[^a-z-]/g, '');
      status.textContent = statusName;
      statusCell.appendChild(status);
      row.appendChild(statusCell);
      body.appendChild(row);
    });
  }

  function getTimezone() {
    return summaryData && summaryData.period && summaryData.period.timezone ? summaryData.period.timezone : '';
  }

  function niceMaximum(value) {
    if (value <= 0) return 4;
    var rough = value / 4;
    var power = Math.pow(10, Math.floor(Math.log10(rough)));
    var normalized = rough / power;
    var step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    return step * power * 4;
  }

  function showChartTooltip(button, point, event) {
    var tooltip = byId('chart-tooltip');
    byId('chart-tooltip-value').textContent = formatCount(point.article_count) + (Number(point.article_count) === 1 ? ' article' : ' articles');
    byId('chart-tooltip-label').textContent = 'Week of ' + formatWeek(point.week_start);
    tooltip.hidden = false;

    var rect = button.getBoundingClientRect();
    var x = event && Number.isFinite(event.clientX) ? event.clientX : rect.left + (rect.width / 2);
    var y = event && Number.isFinite(event.clientY) ? event.clientY : rect.top;
    var tooltipRect = tooltip.getBoundingClientRect();
    var left = Math.min(window.innerWidth - tooltipRect.width - 10, Math.max(10, x - (tooltipRect.width / 2)));
    var top = y - tooltipRect.height - 10;
    if (top < 10) top = Math.min(window.innerHeight - tooltipRect.height - 10, y + 14);
    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
  }

  function hideChartTooltip() { byId('chart-tooltip').hidden = true; }

  function renderChart(points) {
    var chart = byId('weekly-chart');
    var state = byId('chart-state');
    var details = byId('chart-data');
    var bars = byId('chart-bars');
    var labels = byId('chart-x-axis');
    var gridlines = byId('chart-gridlines');
    var yAxis = byId('chart-y-axis');
    var tableBody = byId('chart-table-body');
    bars.replaceChildren();
    labels.replaceChildren();
    gridlines.replaceChildren();
    yAxis.replaceChildren();
    tableBody.replaceChildren();

    if (!points.length) {
      chart.hidden = true;
      details.hidden = true;
      state.textContent = 'No delivered article history is available yet.';
      state.hidden = false;
      return;
    }

    var maximum = niceMaximum(points.reduce(function (max, point) {
      return Math.max(max, Number(point.article_count) || 0);
    }, 0));

    for (var tick = 0; tick <= 4; tick += 1) {
      var value = maximum * (tick / 4);
      var bottom = tick * 25;
      var line = document.createElement('span');
      line.className = 'admin-chart__gridline';
      line.style.bottom = bottom + '%';
      gridlines.appendChild(line);
      var tickLabel = document.createElement('span');
      tickLabel.className = 'admin-chart__tick-label';
      tickLabel.style.bottom = bottom + '%';
      tickLabel.textContent = formatCompactCount(value);
      yAxis.appendChild(tickLabel);
    }

    points.forEach(function (point) {
      var value = Math.max(0, Number(point.article_count) || 0);
      var slot = document.createElement('div');
      slot.className = 'admin-chart__bar-slot';
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'admin-chart__bar-hit';
      button.style.setProperty(
        '--bar-height',
        (value === 0 ? 0 : Math.max(0.8, (value / maximum) * 100)) + '%'
      );
      button.setAttribute('aria-label', 'Week of ' + formatWeek(point.week_start) + ': ' + formatCount(value) + (value === 1 ? ' article delivered' : ' articles delivered'));
      var mark = document.createElement('span');
      mark.className = 'admin-chart__bar-mark';
      mark.setAttribute('aria-hidden', 'true');
      button.appendChild(mark);
      button.addEventListener('mouseenter', function (event) { showChartTooltip(button, point, event); });
      button.addEventListener('mousemove', function (event) { showChartTooltip(button, point, event); });
      button.addEventListener('mouseleave', hideChartTooltip);
      button.addEventListener('focus', function () { showChartTooltip(button, point); });
      button.addEventListener('blur', hideChartTooltip);
      slot.appendChild(button);
      bars.appendChild(slot);

      var label = document.createElement('span');
      label.className = 'admin-chart__x-label';
      label.textContent = formatWeek(point.week_start);
      label.title = 'Week of ' + formatWeek(point.week_start);
      labels.appendChild(label);

      var row = document.createElement('tr');
      var weekCell = document.createElement('th');
      weekCell.scope = 'row';
      weekCell.textContent = 'Week of ' + formatWeek(point.week_start);
      row.appendChild(weekCell);
      appendCell(row, formatCount(value), 'admin-table__number');
      tableBody.appendChild(row);
    });

    var minWidth = Math.max(682, 42 + (points.length * 64));
    byId('chart-canvas').style.minWidth = minWidth + 'px';
    state.hidden = true;
    chart.hidden = false;
    details.hidden = false;
  }

  function renderSummary(data) {
    summaryData = data;
    var totals = data.totals || {};
    byId('kpi-subscribers').textContent = formatCount(totals.subscribers);
    byId('kpi-accounts').textContent = formatCount(totals.tracked_accounts);
    byId('kpi-articles').textContent = formatCount(totals.articles_this_week);
    byId('kpi-emails').textContent = formatCount(totals.delivered_emails);
    byId('kpi-grid').setAttribute('aria-busy', 'false');
    byId('last-updated').textContent = data.generated_at
      ? 'Updated ' + formatDateTime(data.generated_at, getTimezone())
      : 'Current delivery data';
    renderChart(Array.isArray(data.weekly_articles) ? data.weekly_articles : []);
  }

  async function loadSummary() {
    byId('kpi-grid').setAttribute('aria-busy', 'true');
    byId('chart-state').textContent = 'Loading weekly article counts…';
    byId('chart-state').hidden = false;
    byId('weekly-chart').hidden = true;
    byId('chart-data').hidden = true;
    try {
      var data = await request('/api/admin/summary');
      renderSummary(data);
      showDashboardMessage('', '');
    } catch (error) {
      if (error.code === 'auth') return;
      byId('kpi-grid').setAttribute('aria-busy', 'false');
      byId('chart-state').textContent = 'Weekly article counts could not be loaded.';
      var message = error.code === 'rate-limit' ? rateLimitMessage(error) : error.code === 'network'
        ? 'The dashboard could not reach the Scoop API.' : 'Dashboard data could not be loaded.';
      showDashboardMessage(message, error.code === 'rate-limit' ? 'warning' : 'error', loadAll);
    }
  }

  function loadAll() {
    if (!accessToken) return;
    showDashboardMessage('', '');
    resetPager(subscriberPages);
    resetPager(deliveryPages);
    loadSummary();
    loadPage(subscriberPages);
    loadPage(deliveryPages);
  }

  async function handleLogin(event) {
    event.preventDefault();
    setLoginMessage('', '');
    var username = byId('admin-username').value.trim();
    var password = byId('admin-password').value;
    if (!username || !password) {
      setLoginMessage('Enter both your username and password.', 'error');
      return;
    }

    setLoginLoading(true);
    try {
      var data = await request('/api/admin/login', {
        method: 'POST',
        authenticated: false,
        body: { username: username, password: password }
      });
      var lifetime = Number(data.expires_in);
      if (!data.access_token || String(data.token_type || '').toLowerCase() !== 'bearer' || !Number.isFinite(lifetime) || lifetime <= 0) {
        throw new ApiError('The server returned an invalid session.', 500, 'api');
      }
      saveSession(String(data.access_token), lifetime);
      byId('admin-password').value = '';
      showDashboard();
      loadAll();
    } catch (error) {
      if (error.code === 'rate-limit') setLoginMessage(rateLimitMessage(error), 'warning');
      else if (error.code === 'network') setLoginMessage('Unable to reach the Scoop API. Check your connection and try again.', 'error');
      else if (error.status === 401 || error.status === 403) setLoginMessage('Incorrect administrator username or password.', 'error');
      else setLoginMessage(error.message || 'Sign-in failed. Please try again.', 'error');
    } finally {
      setLoginLoading(false);
    }
  }

  byId('login-form').addEventListener('submit', handleLogin);
  byId('logout-button').addEventListener('click', function () {
    clearSession();
    byId('admin-username').value = '';
    showLogin('', '');
  });
  byId('refresh-button').addEventListener('click', loadAll);
  byId('subscriber-next').addEventListener('click', function () { nextPage(subscriberPages); });
  byId('subscriber-prev').addEventListener('click', function () { previousPage(subscriberPages); });
  byId('delivery-next').addEventListener('click', function () { nextPage(deliveryPages); });
  byId('delivery-prev').addEventListener('click', function () { previousPage(deliveryPages); });

  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && accessToken && expiresAt <= Date.now()) expireSession();
  });

  if (restoreSession()) {
    showDashboard();
    loadAll();
  } else {
    showLogin('', '');
  }
})();
