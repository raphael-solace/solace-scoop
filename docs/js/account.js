/* ============================================
   Solace Scoop - Account Management
   OTP via backend API (our Gmail), data via Supabase anon key
   ============================================ */
(function () {
  'use strict';

  var SUPABASE_URL = window.SCOOP_SUPABASE_URL || '';
  var SUPABASE_KEY = window.SCOOP_SUPABASE_KEY || '';
  var API_URL = window.SCOOP_API_URL || '';
  if (!SUPABASE_URL || !SUPABASE_KEY) return;

  function sbFetch(path, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    opts.headers['apikey'] = SUPABASE_KEY;
    opts.headers['Authorization'] = 'Bearer ' + SUPABASE_KEY;
    opts.headers['Content-Type'] = 'application/json';
    return fetch(SUPABASE_URL + '/rest/v1/' + path, opts).then(function(r) { return r.json(); });
  }

  var pendingEmail = '';

  // ── DOM ─────────────────────────────────
  var emailSection = document.getElementById('email-section');
  var codeSection = document.getElementById('code-section');
  var profileSection = document.getElementById('profile-section');
  var loadingSection = document.getElementById('loading-section');

  var emailForm = document.getElementById('email-form');
  var loginEmail = document.getElementById('login-email');
  var btnSendCode = document.getElementById('btn-send-code');
  var emailError = document.getElementById('email-error');
  var emailErrorText = document.getElementById('email-error-text');

  var codeForm = document.getElementById('code-form');
  var otpCode = document.getElementById('otp-code');
  var codeEmailDisplay = document.getElementById('code-email-display');
  var btnVerify = document.getElementById('btn-verify');
  var btnBack = document.getElementById('btn-back');
  var codeError = document.getElementById('code-error');
  var codeErrorText = document.getElementById('code-error-text');

  var profileName = document.getElementById('profile-name');
  var profileEmail = document.getElementById('profile-email');
  var profileStats = document.getElementById('profile-stats');
  var profileProduct = document.getElementById('profile-product');
  var profileCompanies = document.getElementById('profile-companies');
  var profileForm = document.getElementById('profile-form');
  var btnSave = document.getElementById('btn-save');
  var btnSignout = document.getElementById('btn-signout');
  var saveSuccess = document.getElementById('save-success');
  var digestsContainer = document.getElementById('digests-container');
  var digestsEmpty = document.getElementById('digests-empty');

  var nav = document.getElementById('nav');
  window.addEventListener('scroll', function () {
    nav.classList.toggle('nav--scrolled', window.scrollY > 10);
  }, { passive: true });

  function show(s) {
    emailSection.hidden = codeSection.hidden = profileSection.hidden = loadingSection.hidden = true;
    s.hidden = false;
  }
  function setLoading(btn, on) {
    btn.querySelector('.btn__text').hidden = on;
    btn.querySelector('.btn__loader').hidden = !on;
    btn.disabled = on;
  }

  // ── Init ────────────────────────────────
  function init() {
    var token = localStorage.getItem('scoop_token');
    var email = localStorage.getItem('scoop_email');
    if (token && email) {
      show(loadingSection);
      verifySession(email, token);
    } else {
      show(emailSection);
    }
  }

  // ── Step 1: Request OTP ─────────────────
  emailForm.addEventListener('submit', function (e) {
    e.preventDefault();
    emailError.hidden = true;
    var email = loginEmail.value.trim().toLowerCase();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { loginEmail.focus(); return; }

    setLoading(btnSendCode, true);

    fetch(API_URL + '/api/auth/send-otp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email })
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      setLoading(btnSendCode, false);
      if (data.error) {
        emailErrorText.textContent = data.error;
        emailError.hidden = false;
        return;
      }
      pendingEmail = email;
      codeEmailDisplay.textContent = email;
      show(codeSection);
      otpCode.focus();
    })
    .catch(function () {
      setLoading(btnSendCode, false);
      emailErrorText.textContent = 'Connection error. Try again.';
      emailError.hidden = false;
    });
  });

  // ── Step 2: Verify OTP ──────────────────
  codeForm.addEventListener('submit', function (e) {
    e.preventDefault();
    codeError.hidden = true;
    var code = otpCode.value.trim();
    if (!code || code.length !== 6) { otpCode.focus(); return; }

    setLoading(btnVerify, true);

    fetch(API_URL + '/api/auth/verify-otp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: pendingEmail, code: code })
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      setLoading(btnVerify, false);
      if (data.error) {
        codeErrorText.textContent = data.error;
        codeError.hidden = false;
        return;
      }
      localStorage.setItem('scoop_token', data.token);
      localStorage.setItem('scoop_email', pendingEmail);
      show(loadingSection);
      loadProfile(pendingEmail);
    })
    .catch(function () {
      setLoading(btnVerify, false);
      codeErrorText.textContent = 'Connection error. Try again.';
      codeError.hidden = false;
    });
  });

  btnBack.addEventListener('click', function (e) {
    e.preventDefault();
    otpCode.value = '';
    show(emailSection);
  });

  // ── Verify saved session ────────────────
  function verifySession(email, token) {
    fetch(API_URL + '/api/auth/verify-session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, token: token })
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.valid) {
        loadProfile(email);
      } else {
        localStorage.removeItem('scoop_token');
        localStorage.removeItem('scoop_email');
        show(emailSection);
      }
    })
    .catch(function () {
      // If backend is down, still allow cached access
      loadProfile(email);
    });
  }

  // ── Load profile ────────────────────────
  function loadProfile(email) {
    sbFetch('users?email=eq.' + encodeURIComponent(email) + '&select=id,email,product,plan,created_at,companies(id,name)')
    .then(function (rows) {
      if (!rows || !rows.length) {
        profileName.textContent = email.split('@')[0].replace(/\./g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();});
        profileEmail.textContent = email;
        profileStats.innerHTML = '<span class="account-stat">New account</span>';
        digestsEmpty.hidden = false;
        show(profileSection);
        return;
      }

      var user = rows[0];
      var name = user.email.split('@')[0].replace(/\./g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();});
      var companies = (user.companies || []).map(function(c){ return c.name; });
      var since = new Date(user.created_at).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });

      profileName.textContent = name;
      profileEmail.textContent = user.email;
      profileProduct.value = user.product || '';
      profileCompanies.value = companies.join('\n');

      sbFetch('digests?user_id=eq.' + user.id + '&select=id')
      .then(function (digests) {
        var dc = (digests || []).length;
        profileStats.innerHTML =
          '<span class="account-stat">' + companies.length + ' account' + (companies.length !== 1 ? 's' : '') + '</span>'
          + '<span class="account-stat">' + dc + ' digest' + (dc !== 1 ? 's' : '') + ' sent</span>'
          + '<span class="account-stat">Since ' + since + '</span>';
      });

      loadDigests(user.id);
      show(profileSection);
    })
    .catch(function () { show(emailSection); });
  }

  // ── Digests ─────────────────────────────
  function loadDigests(userId) {
    sbFetch('digests?user_id=eq.' + userId + '&select=sent_at,item_count,items&order=sent_at.desc&limit=4')
    .then(function (digests) {
      if (!digests || !digests.length) { digestsEmpty.hidden = false; digestsContainer.innerHTML = ''; return; }
      digestsEmpty.hidden = true;
      var html = '';
      digests.forEach(function(d) {
        var dateStr = new Date(d.sent_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        var items = d.items || [];
        html += '<div class="digest-card"><div class="digest-card__header"><span class="digest-card__date">' + dateStr + '</span><span class="digest-card__count">' + items.length + ' signal' + (items.length !== 1 ? 's' : '') + '</span></div>';
        items.forEach(function(item) {
          var src = item.source_url || (item.sources && item.sources[0]) || '';
          var link = '';
          if (src) { var dom = (src.split('//')[1]||'').split('/')[0].replace('www.',''); link = ' <a href="' + esc(src) + '" target="_blank" style="color:#6366f1;text-decoration:none;font-size:12px">' + esc(dom) + '</a>'; }
          html += '<div class="digest-card__signal">';
          html += '<p class="digest-card__signal-header"><strong>' + esc(item.company||'') + '</strong> <span class="digest-card__tag">' + esc(item.tag||'') + '</span>' + link + '</p>';
          html += '<p class="digest-card__headline">' + esc(item.headline||'') + '</p>';
          if (item.why) html += '<p class="digest-card__why">' + esc(item.why) + '</p>';
          if (item.opening_line) html += '<p class="digest-card__opener">💬 <em>"' + esc(item.opening_line) + '"</em></p>';
          html += '</div>';
        });
        html += '</div>';
      });
      digestsContainer.innerHTML = html;
    });
  }

  // ── Save ────────────────────────────────
  profileForm.addEventListener('submit', function (e) {
    e.preventDefault();
    saveSuccess.hidden = true;
    setLoading(btnSave, true);
    var email = localStorage.getItem('scoop_email');
    var product = profileProduct.value.trim();
    var companies = profileCompanies.value.trim().split('\n').map(function(s){return s.trim();}).filter(Boolean);

    sbFetch('users?email=eq.' + encodeURIComponent(email) + '&select=id')
    .then(function (rows) {
      if (!rows || !rows.length) throw new Error('User not found');
      var userId = rows[0].id;

      // Delete old companies
      return fetch(SUPABASE_URL + '/rest/v1/companies?user_id=eq.' + userId, {
        method: 'DELETE', headers: { 'apikey': SUPABASE_KEY, 'Authorization': 'Bearer ' + SUPABASE_KEY }
      }).then(function () {
        // Insert new companies
        if (companies.length) {
          var body = companies.map(function(n) { return { user_id: userId, name: n }; });
          return fetch(SUPABASE_URL + '/rest/v1/companies', {
            method: 'POST',
            headers: { 'apikey': SUPABASE_KEY, 'Authorization': 'Bearer ' + SUPABASE_KEY, 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
          });
        }
      }).then(function () {
        // Update product
        return fetch(SUPABASE_URL + '/rest/v1/users?id=eq.' + userId, {
          method: 'PATCH',
          headers: { 'apikey': SUPABASE_KEY, 'Authorization': 'Bearer ' + SUPABASE_KEY, 'Content-Type': 'application/json', 'Prefer': 'return=representation' },
          body: JSON.stringify({ product: product, updated_at: new Date().toISOString() })
        });
      });
    })
    .then(function () {
      setLoading(btnSave, false);
      saveSuccess.hidden = false;
      setTimeout(function(){ saveSuccess.hidden = true; }, 4000);
      loadProfile(email);
    })
    .catch(function (err) {
      setLoading(btnSave, false);
      alert('Failed to save: ' + err.message);
    });
  });

  // ── Sign out ────────────────────────────
  btnSignout.addEventListener('click', function () {
    localStorage.removeItem('scoop_token');
    localStorage.removeItem('scoop_email');
    otpCode.value = '';
    loginEmail.value = '';
    show(emailSection);
  });

  function esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  init();
})();
