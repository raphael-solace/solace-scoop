/* ============================================
   Solace Scoop - Account Management
   OTP auth via backend, data via Supabase anon key
   ============================================ */
(function () {
  'use strict';

  var SUPABASE_URL = window.SCOOP_SUPABASE_URL || '';
  var SUPABASE_KEY = window.SCOOP_SUPABASE_KEY || '';
  var API_URL = window.SCOOP_API_URL || '';
  if (!SUPABASE_URL || !SUPABASE_KEY) return;

  function sbFetch(path) {
    return fetch(SUPABASE_URL + '/rest/v1/' + path, {
      headers: { 'apikey': SUPABASE_KEY, 'Authorization': 'Bearer ' + SUPABASE_KEY, 'Content-Type': 'application/json' }
    }).then(function(r) { return r.json(); });
  }
  function sbWrite(path, method, body) {
    var h = { 'apikey': SUPABASE_KEY, 'Authorization': 'Bearer ' + SUPABASE_KEY, 'Content-Type': 'application/json' };
    if (method === 'PATCH') h['Prefer'] = 'return=representation';
    return fetch(SUPABASE_URL + '/rest/v1/' + path, { method: method, headers: h, body: body ? JSON.stringify(body) : undefined });
  }

  var pendingEmail = '';
  var currentUserId = '';

  // ── DOM ─────────────────────────────────
  var emailSection = document.getElementById('email-section');
  var codeSection = document.getElementById('code-section');
  var profileSection = document.getElementById('profile-section');
  var loadingSection = document.getElementById('loading-section');

  var nav = document.getElementById('nav');
  window.addEventListener('scroll', function () { nav.classList.toggle('nav--scrolled', window.scrollY > 10); }, { passive: true });

  function show(s) { emailSection.hidden = codeSection.hidden = profileSection.hidden = loadingSection.hidden = true; s.hidden = false; }
  function setLoading(btn, on) { btn.querySelector('.btn__text').hidden = on; btn.querySelector('.btn__loader').hidden = !on; btn.disabled = on; }

  // ── Init ────────────────────────────────
  function init() {
    var token = localStorage.getItem('scoop_token');
    var email = localStorage.getItem('scoop_email');
    if (token && email) { show(loadingSection); verifySession(email, token); }
    else { show(emailSection); }
  }

  // ── Step 1: Send OTP ────────────────────
  document.getElementById('email-form').addEventListener('submit', function (e) {
    e.preventDefault();
    document.getElementById('email-error').hidden = true;
    var email = document.getElementById('login-email').value.trim().toLowerCase();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return;

    var btn = document.getElementById('btn-send-code');
    setLoading(btn, true);
    fetch(API_URL + '/api/auth/send-otp', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email }) })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      setLoading(btn, false);
      if (d.error) { document.getElementById('email-error-text').textContent = d.error; document.getElementById('email-error').hidden = false; return; }
      pendingEmail = email;
      document.getElementById('code-email-display').textContent = email;
      show(codeSection);
      document.getElementById('otp-code').focus();
    })
    .catch(function() { setLoading(btn, false); document.getElementById('email-error-text').textContent = 'Connection error. Try again.'; document.getElementById('email-error').hidden = false; });
  });

  // ── Step 2: Verify OTP ──────────────────
  document.getElementById('code-form').addEventListener('submit', function (e) {
    e.preventDefault();
    document.getElementById('code-error').hidden = true;
    var code = document.getElementById('otp-code').value.trim();
    if (!code || code.length !== 6) return;

    var btn = document.getElementById('btn-verify');
    setLoading(btn, true);
    fetch(API_URL + '/api/auth/verify-otp', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: pendingEmail, code: code }) })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      setLoading(btn, false);
      if (d.error) { document.getElementById('code-error-text').textContent = d.error; document.getElementById('code-error').hidden = false; return; }
      localStorage.setItem('scoop_token', d.token);
      localStorage.setItem('scoop_email', pendingEmail);
      show(loadingSection);
      loadProfile(pendingEmail);
    })
    .catch(function() { setLoading(btn, false); document.getElementById('code-error-text').textContent = 'Connection error.'; document.getElementById('code-error').hidden = false; });
  });

  document.getElementById('btn-back').addEventListener('click', function (e) { e.preventDefault(); show(emailSection); });

  // ── Session verify ──────────────────────
  function verifySession(email, token) {
    fetch(API_URL + '/api/auth/verify-session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email, token: token }) })
    .then(function(r) { return r.json(); })
    .then(function(d) { if (d.valid) loadProfile(email); else { localStorage.clear(); show(emailSection); } })
    .catch(function() { loadProfile(email); }); // offline fallback
  }

  // ── Load profile ────────────────────────
  function loadProfile(email) {
    sbFetch('users?email=eq.' + encodeURIComponent(email) + '&select=id,email,product,plan,created_at,companies(id,name)')
    .then(function(rows) {
      if (!rows || !rows.length) { show(emailSection); return; }
      var user = rows[0];
      currentUserId = user.id;
      var name = user.email.split('@')[0].replace(/\./g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();});
      var companies = (user.companies || []).map(function(c){ return c.name; });
      var since = new Date(user.created_at).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });

      document.getElementById('profile-name').textContent = name;
      document.getElementById('profile-email').textContent = user.email;
      document.getElementById('profile-product').value = user.product || '';
      document.getElementById('profile-companies').value = companies.join('\n');

      sbFetch('digests?user_id=eq.' + user.id + '&select=id').then(function(d) {
        var dc = (d || []).length;
        document.getElementById('profile-stats').innerHTML =
          '<span class="account-stat">' + companies.length + ' account' + (companies.length !== 1 ? 's' : '') + '</span>'
          + '<span class="account-stat">' + dc + ' digest' + (dc !== 1 ? 's' : '') + ' sent</span>'
          + '<span class="account-stat">Since ' + since + '</span>';
      });

      loadNewsByAccount(user.id, companies);
      show(profileSection);
    })
    .catch(function() { show(emailSection); });
  }

  // ── Tabs ────────────────────────────────
  document.querySelectorAll('.profile-tab').forEach(function(tab) {
    tab.addEventListener('click', function() {
      document.querySelectorAll('.profile-tab').forEach(function(t) { t.classList.remove('profile-tab--active'); });
      tab.classList.add('profile-tab--active');
      document.querySelectorAll('.profile-panel').forEach(function(p) { p.hidden = true; });
      document.getElementById('panel-' + tab.dataset.tab).hidden = false;
    });
  });

  // ── News by account ─────────────────────
  function loadNewsByAccount(userId, companyNames) {
    var container = document.getElementById('news-container');
    var empty = document.getElementById('news-empty');

    sbFetch('digests?user_id=eq.' + userId + '&select=items,sent_at&order=sent_at.desc&limit=4')
    .then(function(digests) {
      // Collect all signals, group by company
      var byCompany = {};
      companyNames.forEach(function(name) { byCompany[name] = []; });

      (digests || []).forEach(function(d) {
        (d.items || []).forEach(function(item) {
          var co = item.company || '';
          if (!byCompany[co]) byCompany[co] = [];
          // Avoid duplicates by headline
          var dominated = byCompany[co].some(function(existing) { return existing.headline === item.headline; });
          if (!dominated) byCompany[co].push(item);
        });
      });

      // Render
      var html = '';
      var hasNews = false;

      // Sort companies: those with news first
      var sorted = Object.keys(byCompany).sort(function(a, b) { return byCompany[b].length - byCompany[a].length; });

      sorted.forEach(function(company) {
        var signals = byCompany[company];
        if (!signals.length) {
          // Show company with "no recent news" indicator
          html += '<div class="news-account">';
          html += '<h3 class="news-account__header">' + esc(company) + ' <span style="font-size:0.75rem; color:var(--gray-400); font-family:var(--font-body); font-weight:400;">no recent news</span></h3>';
          html += '</div>';
          return;
        }
        hasNews = true;
        html += '<div class="news-account">';
        html += '<h3 class="news-account__header">' + esc(company) + ' <span style="font-size:0.75rem; color:var(--green); font-family:var(--font-body); font-weight:600;">' + signals.length + ' signal' + (signals.length !== 1 ? 's' : '') + '</span></h3>';

        signals.slice(0, 5).forEach(function(item) {
          var src = item.source_url || (item.sources && item.sources[0]) || '';
          var linkHtml = '';
          if (src) {
            var dom = (src.split('//')[1] || '').split('/')[0].replace('www.', '');
            linkHtml = '<a href="' + esc(src) + '" target="_blank" class="news-signal__link">' + esc(dom) + '</a>';
          }
          var dateHtml = item.date ? '<span class="news-signal__date">' + esc(item.date) + '</span>' : '';

          html += '<div class="news-signal">';
          html += '<span class="news-signal__tag">' + esc(item.tag || '') + '</span>' + linkHtml + dateHtml;
          html += '<p class="news-signal__headline">' + esc(item.headline || '') + '</p>';
          if (item.why) html += '<p class="news-signal__why">' + esc(item.why) + '</p>';
          if (item.opening_line) html += '<p class="news-signal__opener">💬 <em>"' + esc(item.opening_line) + '"</em></p>';
          html += '</div>';
        });

        html += '</div>';
      });

      container.innerHTML = html;
      empty.hidden = hasNews;
    });
  }

  // ── Save accounts ───────────────────────
  document.getElementById('profile-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var success = document.getElementById('save-success');
    success.hidden = true;
    var btn = document.getElementById('btn-save');
    setLoading(btn, true);

    var email = localStorage.getItem('scoop_email');
    var product = document.getElementById('profile-product').value.trim();
    var companies = document.getElementById('profile-companies').value.trim().split('\n').map(function(s){return s.trim();}).filter(Boolean);

    sbFetch('users?email=eq.' + encodeURIComponent(email) + '&select=id')
    .then(function(rows) {
      if (!rows || !rows.length) throw new Error('Not found');
      var userId = rows[0].id;
      return sbWrite('companies?user_id=eq.' + userId, 'DELETE')
        .then(function() {
          if (companies.length) return sbWrite('companies', 'POST', companies.map(function(n) { return { user_id: userId, name: n }; }));
        })
        .then(function() { return sbWrite('users?id=eq.' + userId, 'PATCH', { product: product, updated_at: new Date().toISOString() }); });
    })
    .then(function() {
      setLoading(btn, false);
      success.hidden = false;
      setTimeout(function() { success.hidden = true; }, 4000);
      loadProfile(email);
    })
    .catch(function(err) { setLoading(btn, false); alert('Failed: ' + err.message); });
  });

  // ── Sign out ────────────────────────────
  document.getElementById('btn-signout').addEventListener('click', function () {
    localStorage.removeItem('scoop_token');
    localStorage.removeItem('scoop_email');
    show(emailSection);
  });

  function esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  init();
})();
