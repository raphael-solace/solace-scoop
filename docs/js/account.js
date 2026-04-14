/* ============================================
   Solace Scoop - Account Management
   Email-based login using Supabase REST API
   ============================================ */
(function () {
  'use strict';

  var SUPABASE_URL = window.SCOOP_SUPABASE_URL || '';
  var SUPABASE_KEY = window.SCOOP_SUPABASE_KEY || '';

  if (!SUPABASE_URL || !SUPABASE_KEY) {
    console.error('Supabase not configured.');
    return;
  }

  function sbHeaders() {
    return {
      'apikey': SUPABASE_KEY,
      'Authorization': 'Bearer ' + SUPABASE_KEY,
      'Content-Type': 'application/json'
    };
  }

  // ── DOM ─────────────────────────────────
  var loginSection = document.getElementById('login-section');
  var profileSection = document.getElementById('profile-section');
  var loadingSection = document.getElementById('loading-section');

  var loginForm = document.getElementById('login-form');
  var loginEmail = document.getElementById('login-email');
  var loginError = document.getElementById('login-error');
  var loginErrorText = document.getElementById('login-error-text');
  var btnLogin = document.getElementById('btn-login');

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

  var currentUser = null;

  // ── Nav scroll ──────────────────────────
  var nav = document.getElementById('nav');
  window.addEventListener('scroll', function () {
    nav.classList.toggle('nav--scrolled', window.scrollY > 10);
  }, { passive: true });

  // ── State ───────────────────────────────
  function show(section) {
    loginSection.hidden = true;
    profileSection.hidden = true;
    loadingSection.hidden = true;
    section.hidden = false;
  }

  function setLoading(btn, loading) {
    btn.querySelector('.btn__text').hidden = loading;
    btn.querySelector('.btn__loader').hidden = !loading;
    btn.disabled = loading;
  }

  // ── Init ────────────────────────────────
  function init() {
    var saved = localStorage.getItem('scoop_email');
    if (saved) {
      show(loadingSection);
      loadProfile(saved);
    } else {
      show(loginSection);
    }
  }

  // ── Login ───────────────────────────────
  loginForm.addEventListener('submit', function (e) {
    e.preventDefault();
    loginError.hidden = true;
    var email = loginEmail.value.trim().toLowerCase();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      loginEmail.focus();
      return;
    }
    setLoading(btnLogin, true);
    loadProfile(email);
  });

  // ── Load profile ────────────────────────
  function loadProfile(email) {
    fetch(SUPABASE_URL + '/rest/v1/users?email=eq.' + encodeURIComponent(email) + '&select=id,email,product,plan,created_at,companies(id,name)', {
      headers: sbHeaders()
    })
    .then(function (r) { return r.json(); })
    .then(function (rows) {
      setLoading(btnLogin, false);
      if (!rows || !rows.length) {
        loginErrorText.textContent = 'No account found for ' + email + '. Sign up on the home page first.';
        loginError.hidden = false;
        show(loginSection);
        return;
      }

      currentUser = rows[0];
      localStorage.setItem('scoop_email', email);

      var name = email.split('@')[0].replace(/\./g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
      var companies = (currentUser.companies || []).map(function (c) { return c.name; });
      var since = new Date(currentUser.created_at).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });

      profileName.textContent = name;
      profileEmail.textContent = email;
      profileProduct.value = currentUser.product || '';
      profileCompanies.value = companies.join('\n');

      // Digest count
      fetch(SUPABASE_URL + '/rest/v1/digests?user_id=eq.' + currentUser.id + '&select=id', {
        headers: sbHeaders()
      })
      .then(function (r) { return r.json(); })
      .then(function (digests) {
        var dc = digests.length || 0;
        profileStats.innerHTML =
          '<span class="account-stat">' + companies.length + ' account' + (companies.length !== 1 ? 's' : '') + '</span>'
          + '<span class="account-stat">' + dc + ' digest' + (dc !== 1 ? 's' : '') + ' sent</span>'
          + '<span class="account-stat">Since ' + since + '</span>';
      });

      loadDigests(currentUser.id);
      show(profileSection);
    })
    .catch(function (err) {
      setLoading(btnLogin, false);
      loginErrorText.textContent = 'Connection error. Try again.';
      loginError.hidden = false;
      show(loginSection);
    });
  }

  // ── Load past digests ───────────────────
  function loadDigests(userId) {
    fetch(SUPABASE_URL + '/rest/v1/digests?user_id=eq.' + userId + '&select=sent_at,item_count,items&order=sent_at.desc&limit=4', {
      headers: sbHeaders()
    })
    .then(function (r) { return r.json(); })
    .then(function (digests) {
      if (!digests || !digests.length) {
        digestsEmpty.hidden = false;
        digestsContainer.innerHTML = '';
        return;
      }

      digestsEmpty.hidden = true;
      var html = '';

      digests.forEach(function (digest) {
        var dateStr = new Date(digest.sent_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        var items = digest.items || [];

        html += '<div class="digest-card">';
        html += '<div class="digest-card__header">';
        html += '<span class="digest-card__date">' + dateStr + '</span>';
        html += '<span class="digest-card__count">' + items.length + ' signal' + (items.length !== 1 ? 's' : '') + '</span>';
        html += '</div>';

        items.forEach(function (item) {
          var sourceUrl = item.source_url || (item.sources && item.sources[0]) || '';
          var sourceLink = '';
          if (sourceUrl) {
            var domain = sourceUrl.split('//')[1];
            if (domain) domain = domain.split('/')[0].replace('www.', '');
            sourceLink = ' <a href="' + esc(sourceUrl) + '" target="_blank" style="color:#6366f1; text-decoration:none; font-size:12px;">' + esc(domain || 'source') + '</a>';
          }

          html += '<div class="digest-card__signal">';
          html += '<p class="digest-card__signal-header"><strong>' + esc(item.company || '') + '</strong> <span class="digest-card__tag">' + esc(item.tag || '') + '</span>' + sourceLink + '</p>';
          html += '<p class="digest-card__headline">' + esc(item.headline || '') + '</p>';
          if (item.why) html += '<p class="digest-card__why">' + esc(item.why) + '</p>';
          if (item.opening_line) html += '<p class="digest-card__opener">💬 <em>"' + esc(item.opening_line) + '"</em></p>';
          html += '</div>';
        });

        html += '</div>';
      });

      digestsContainer.innerHTML = html;
    });
  }

  // ── Save changes ────────────────────────
  profileForm.addEventListener('submit', function (e) {
    e.preventDefault();
    saveSuccess.hidden = true;
    setLoading(btnSave, true);

    var product = profileProduct.value.trim();
    var companiesRaw = profileCompanies.value.trim();
    var companies = companiesRaw.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);

    // Delete old companies, insert new ones
    var userId = currentUser.id;

    fetch(SUPABASE_URL + '/rest/v1/companies?user_id=eq.' + userId, {
      method: 'DELETE',
      headers: sbHeaders()
    })
    .then(function () {
      if (!companies.length) return;
      var rows = companies.map(function (name) { return { user_id: userId, name: name }; });
      return fetch(SUPABASE_URL + '/rest/v1/companies', {
        method: 'POST',
        headers: sbHeaders(),
        body: JSON.stringify(rows)
      });
    })
    .then(function () {
      // Update product via upsert
      var h = sbHeaders();
      h['Prefer'] = 'return=representation,resolution=merge-duplicates';
      return fetch(SUPABASE_URL + '/rest/v1/users?on_conflict=email', {
        method: 'POST',
        headers: h,
        body: JSON.stringify({ email: currentUser.email, product: product })
      });
    })
    .then(function () {
      setLoading(btnSave, false);
      saveSuccess.hidden = false;
      setTimeout(function () { saveSuccess.hidden = true; }, 4000);
      loadProfile(currentUser.email);
    })
    .catch(function (err) {
      setLoading(btnSave, false);
      alert('Failed to save: ' + err.message);
    });
  });

  // ── Sign out ────────────────────────────
  btnSignout.addEventListener('click', function () {
    localStorage.removeItem('scoop_email');
    currentUser = null;
    loginForm.hidden = false;
    loginError.hidden = true;
    show(loginSection);
  });

  // ── Helpers ─────────────────────────────
  function esc(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  init();
})();
