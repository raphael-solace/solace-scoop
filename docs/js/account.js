/* ============================================
   Solace Scoop - Account Management
   Uses Supabase Auth (magic link) + RLS
   ============================================ */
(function () {
  'use strict';

  var SUPABASE_URL = window.SCOOP_SUPABASE_URL || '';
  var SUPABASE_ANON_KEY = window.SCOOP_SUPABASE_ANON_KEY || '';

  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
    console.error('Supabase not configured. Set SCOOP_SUPABASE_URL and SCOOP_SUPABASE_ANON_KEY in config.js');
    return;
  }

  var supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

  // ── DOM ─────────────────────────────────
  var loginSection = document.getElementById('login-section');
  var profileSection = document.getElementById('profile-section');
  var loadingSection = document.getElementById('loading-section');

  var loginForm = document.getElementById('login-form');
  var loginEmail = document.getElementById('login-email');
  var loginSent = document.getElementById('login-sent');
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

  // ── Nav scroll ──────────────────────────
  var nav = document.getElementById('nav');
  window.addEventListener('scroll', function () {
    nav.classList.toggle('nav--scrolled', window.scrollY > 10);
  }, { passive: true });

  // ── State management ────────────────────
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
  async function init() {
    // Check for auth callback (magic link redirect)
    var hash = window.location.hash;
    if (hash && hash.includes('access_token')) {
      show(loadingSection);
      // Supabase handles the token exchange automatically
      var _a = await supabase.auth.getSession();
      if (_a.data.session) {
        window.location.hash = '';
        await loadProfile();
        return;
      }
    }

    // Check existing session
    var _b = await supabase.auth.getSession();
    if (_b.data.session) {
      show(loadingSection);
      await loadProfile();
    } else {
      show(loginSection);
    }
  }

  // ── Magic link login ────────────────────
  loginForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    loginError.hidden = true;
    var email = loginEmail.value.trim().toLowerCase();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      loginEmail.focus();
      return;
    }

    setLoading(btnLogin, true);

    var _a = await supabase.auth.signInWithOtp({
      email: email,
      options: {
        emailRedirectTo: window.location.origin + window.location.pathname
      }
    });

    setLoading(btnLogin, false);

    if (_a.error) {
      loginErrorText.textContent = _a.error.message;
      loginError.hidden = false;
    } else {
      loginForm.hidden = true;
      loginSent.hidden = false;
    }
  });

  // ── Load profile data ───────────────────
  async function loadProfile() {
    var _a = await supabase.auth.getUser();
    if (!_a.data.user) {
      show(loginSection);
      return;
    }
    var authEmail = _a.data.user.email;

    // Fetch user + companies
    var _b = await supabase
      .from('users')
      .select('id, email, product, plan, created_at, companies(id, name)')
      .eq('email', authEmail)
      .single();

    if (_b.error || !_b.data) {
      // User has auth account but no app account yet
      profileName.textContent = authEmail.split('@')[0].replace(/\./g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();});
      profileEmail.textContent = authEmail;
      profileStats.innerHTML = '<span class="account-stat">New account</span>';
      profileProduct.value = '';
      profileCompanies.value = '';
      digestsEmpty.hidden = false;
      show(profileSection);
      return;
    }

    var user = _b.data;
    var name = user.email.split('@')[0].replace(/\./g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();});
    var companies = (user.companies || []).map(function(c){ return c.name; });
    var since = new Date(user.created_at).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });

    profileName.textContent = name;
    profileEmail.textContent = user.email;
    profileProduct.value = user.product || '';
    profileCompanies.value = companies.join('\n');

    // Fetch digest count
    var _c = await supabase
      .from('digests')
      .select('id', { count: 'exact', head: true })
      .eq('user_id', user.id);
    var digestCount = _c.count || 0;

    profileStats.innerHTML =
      '<span class="account-stat">' + companies.length + ' account' + (companies.length !== 1 ? 's' : '') + '</span>'
      + '<span class="account-stat">' + digestCount + ' digest' + (digestCount !== 1 ? 's' : '') + ' sent</span>'
      + '<span class="account-stat">Since ' + since + '</span>';

    // Load past digests
    await loadDigests(user.id);

    show(profileSection);
  }

  // ── Load past digests ───────────────────
  async function loadDigests(userId) {
    var _a = await supabase
      .from('digests')
      .select('sent_at, item_count, items')
      .eq('user_id', userId)
      .order('sent_at', { ascending: false })
      .limit(4);

    var digests = _a.data || [];
    if (!digests.length) {
      digestsEmpty.hidden = false;
      digestsContainer.innerHTML = '';
      return;
    }

    digestsEmpty.hidden = true;
    var html = '';

    digests.forEach(function(digest) {
      var dateStr = new Date(digest.sent_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
      var items = digest.items || [];

      html += '<div class="digest-card">';
      html += '<div class="digest-card__header">';
      html += '<span class="digest-card__date">' + dateStr + '</span>';
      html += '<span class="digest-card__count">' + items.length + ' signal' + (items.length !== 1 ? 's' : '') + '</span>';
      html += '</div>';

      items.forEach(function(item) {
        var sourceUrl = item.source_url || (item.sources && item.sources[0]) || '';
        var sourceLink = '';
        if (sourceUrl) {
          var domain = sourceUrl.split('//')[1];
          if (domain) domain = domain.split('/')[0].replace('www.', '');
          sourceLink = ' <a href="' + sourceUrl + '" target="_blank" style="color:#6366f1; text-decoration:none; font-size:12px;">' + (domain || 'source') + '</a>';
        }

        html += '<div class="digest-card__signal">';
        html += '<p class="digest-card__signal-header"><strong>' + esc(item.company || '') + '</strong> <span class="digest-card__tag">' + esc(item.tag || '') + '</span>' + sourceLink + '</p>';
        html += '<p class="digest-card__headline">' + esc(item.headline || '') + '</p>';
        if (item.why) {
          html += '<p class="digest-card__why">' + esc(item.why) + '</p>';
        }
        if (item.opening_line) {
          html += '<p class="digest-card__opener">💬 <em>"' + esc(item.opening_line) + '"</em></p>';
        }
        html += '</div>';
      });

      html += '</div>';
    });

    digestsContainer.innerHTML = html;
  }

  // ── Save changes ────────────────────────
  profileForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    saveSuccess.hidden = true;
    setLoading(btnSave, true);

    var product = profileProduct.value.trim();
    var companies = profileCompanies.value.trim().split('\n').map(function(s){return s.trim();}).filter(Boolean);

    var _a = await supabase.rpc('update_account', {
      p_product: product,
      p_companies: companies
    });

    setLoading(btnSave, false);

    if (_a.error) {
      alert('Failed to save: ' + _a.error.message);
    } else {
      saveSuccess.hidden = false;
      setTimeout(function(){ saveSuccess.hidden = true; }, 4000);
      // Reload to reflect changes
      await loadProfile();
    }
  });

  // ── Sign out ────────────────────────────
  btnSignout.addEventListener('click', async function () {
    await supabase.auth.signOut();
    show(loginSection);
    loginForm.hidden = false;
    loginSent.hidden = true;
  });

  // ── Helpers ─────────────────────────────
  function esc(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Auth state listener ─────────────────
  supabase.auth.onAuthStateChange(function(event, session) {
    if (event === 'SIGNED_IN' && session) {
      loadProfile();
    } else if (event === 'SIGNED_OUT') {
      show(loginSection);
    }
  });

  init();
})();
