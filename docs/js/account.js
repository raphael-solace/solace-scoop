/* ============================================
   Solace Scoop - Account Management
   ============================================ */
(function () {
  'use strict';

  // Backend API URL — update this after deploying to Railway
  var API_URL = window.SCOOP_API_URL || 'https://solace-scoop-production.up.railway.app';

  // ── DOM ─────────────────────────────────
  var loginSection = document.getElementById('login-section');
  var accountSection = document.getElementById('account-section');
  var loadingSection = document.getElementById('loading-section');

  var loginForm = document.getElementById('login-form');
  var loginEmail = document.getElementById('login-email');
  var loginSent = document.getElementById('login-sent');
  var btnLogin = document.getElementById('btn-login');

  var accountName = document.getElementById('account-name');
  var accountEmail = document.getElementById('account-email');
  var accountStats = document.getElementById('account-stats');
  var accountProduct = document.getElementById('account-product');
  var accountCompanies = document.getElementById('account-companies');
  var accountForm = document.getElementById('account-form');
  var btnSave = document.getElementById('btn-save');
  var btnSignout = document.getElementById('btn-signout');
  var saveSuccess = document.getElementById('save-success');

  var sessionToken = localStorage.getItem('scoop_session');

  // ── Nav scroll ──────────────────────────
  var nav = document.getElementById('nav');
  window.addEventListener('scroll', function () {
    nav.classList.toggle('nav--scrolled', window.scrollY > 10);
  }, { passive: true });

  // ── Init ────────────────────────────────
  function init() {
    var params = new URLSearchParams(window.location.search);
    var token = params.get('token');

    if (token) {
      // Arriving from magic link
      showLoading();
      verifyToken(token);
    } else if (sessionToken) {
      // Returning with saved session
      showLoading();
      loadAccount();
    } else {
      showLogin();
    }
  }

  function showLogin() {
    loginSection.hidden = false;
    accountSection.hidden = true;
    loadingSection.hidden = true;
  }

  function showAccount() {
    loginSection.hidden = true;
    accountSection.hidden = false;
    loadingSection.hidden = true;
  }

  function showLoading() {
    loginSection.hidden = true;
    accountSection.hidden = true;
    loadingSection.hidden = false;
  }

  // ── Magic link flow ─────────────────────

  loginForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var email = loginEmail.value.trim();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      loginEmail.focus();
      return;
    }

    setLoading(btnLogin, true);

    fetch(API_URL + '/api/auth/magic-link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email })
    })
    .then(function () {
      loginForm.hidden = true;
      loginSent.hidden = false;
    })
    .catch(function () {
      loginForm.hidden = true;
      loginSent.hidden = false;
    })
    .then(function () { setLoading(btnLogin, false); });
  });

  // ── Token verification ──────────────────

  function verifyToken(token) {
    fetch(API_URL + '/api/auth/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token })
    })
    .then(function (r) {
      if (!r.ok) throw new Error('Invalid token');
      return r.json();
    })
    .then(function (data) {
      sessionToken = data.session_token;
      localStorage.setItem('scoop_session', sessionToken);
      // Clean URL
      window.history.replaceState({}, '', 'account.html');
      loadAccount();
    })
    .catch(function () {
      localStorage.removeItem('scoop_session');
      sessionToken = null;
      showLogin();
    });
  }

  // ── Load account data ───────────────────

  function loadAccount() {
    fetch(API_URL + '/api/account', {
      headers: { 'Authorization': 'Bearer ' + sessionToken }
    })
    .then(function (r) {
      if (!r.ok) throw new Error('Unauthorized');
      return r.json();
    })
    .then(function (user) {
      renderAccount(user);
      showAccount();
    })
    .catch(function () {
      localStorage.removeItem('scoop_session');
      sessionToken = null;
      showLogin();
    });
  }

  function renderAccount(user) {
    var name = user.email.split('@')[0].replace(/\./g, ' ');
    name = name.replace(/\b\w/g, function (c) { return c.toUpperCase(); });

    accountName.textContent = name;
    accountEmail.textContent = user.email;
    accountProduct.value = user.product || '';

    var companies = (user.companies || []).map(function (c) { return c.name; });
    accountCompanies.value = companies.join('\n');

    var since = new Date(user.created_at).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
    var count = user.digest_count || 0;
    accountStats.innerHTML =
      '<span class="account-stat">' + companies.length + ' account' + (companies.length !== 1 ? 's' : '') + '</span>'
      + '<span class="account-stat">' + count + ' digest' + (count !== 1 ? 's' : '') + ' sent</span>'
      + '<span class="account-stat">Since ' + since + '</span>';
  }

  // ── Save changes ────────────────────────

  accountForm.addEventListener('submit', function (e) {
    e.preventDefault();
    saveSuccess.hidden = true;

    var product = accountProduct.value.trim();
    var companiesRaw = accountCompanies.value.trim();
    var companies = companiesRaw.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);

    setLoading(btnSave, true);

    fetch(API_URL + '/api/account/update', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + sessionToken
      },
      body: JSON.stringify({ product: product, companies: companies })
    })
    .then(function (r) {
      if (!r.ok) throw new Error('Save failed');
      return r.json();
    })
    .then(function (user) {
      renderAccount(user);
      saveSuccess.hidden = false;
      setTimeout(function () { saveSuccess.hidden = true; }, 4000);
    })
    .catch(function (err) {
      alert('Failed to save: ' + err.message);
    })
    .then(function () { setLoading(btnSave, false); });
  });

  // ── Sign out ────────────────────────────

  btnSignout.addEventListener('click', function () {
    localStorage.removeItem('scoop_session');
    sessionToken = null;
    showLogin();
  });

  // ── Helpers ─────────────────────────────

  function setLoading(btn, loading) {
    btn.querySelector('.btn__text').hidden = loading;
    btn.querySelector('.btn__loader').hidden = !loading;
    btn.disabled = loading;
  }

  init();
})();
