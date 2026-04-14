/* ============================================
   Solace Scoop - Account Management
   Supabase Auth OTP (6-digit email code)
   ============================================ */
(function () {
  'use strict';

  var SUPABASE_URL = window.SCOOP_SUPABASE_URL || '';
  var SUPABASE_KEY = window.SCOOP_SUPABASE_KEY || '';
  if (!SUPABASE_URL || !SUPABASE_KEY) return;

  var sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY);
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
  async function init() {
    var r = await sb.auth.getSession();
    if (r.data.session) {
      show(loadingSection);
      loadProfile(r.data.session.user.email);
    } else {
      show(emailSection);
    }
  }

  // ── Step 1: Send OTP ────────────────────
  emailForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    emailError.hidden = true;
    var email = loginEmail.value.trim().toLowerCase();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { loginEmail.focus(); return; }

    setLoading(btnSendCode, true);
    var r = await sb.auth.signInWithOtp({ email: email });
    setLoading(btnSendCode, false);

    if (r.error) {
      emailErrorText.textContent = r.error.message;
      emailError.hidden = false;
      return;
    }

    pendingEmail = email;
    codeEmailDisplay.textContent = email;
    show(codeSection);
    otpCode.focus();
  });

  // ── Step 2: Verify OTP ──────────────────
  codeForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    codeError.hidden = true;
    var code = otpCode.value.trim();
    if (!code || code.length !== 6) { otpCode.focus(); return; }

    setLoading(btnVerify, true);
    var r = await sb.auth.verifyOtp({ email: pendingEmail, token: code, type: 'email' });
    setLoading(btnVerify, false);

    if (r.error) {
      codeErrorText.textContent = r.error.message;
      codeError.hidden = false;
      return;
    }

    show(loadingSection);
    loadProfile(pendingEmail);
  });

  btnBack.addEventListener('click', function (e) {
    e.preventDefault();
    otpCode.value = '';
    show(emailSection);
  });

  // ── Load profile (authenticated) ────────
  async function loadProfile(authEmail) {
    // Use service-role-free approach: query via Supabase client (RLS scoped)
    var r = await sb.from('users')
      .select('id, email, product, plan, created_at, companies(id, name)')
      .eq('email', authEmail)
      .single();

    if (r.error || !r.data) {
      profileName.textContent = authEmail.split('@')[0].replace(/\./g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();});
      profileEmail.textContent = authEmail;
      profileStats.innerHTML = '<span class="account-stat">New account</span>';
      digestsEmpty.hidden = false;
      show(profileSection);
      return;
    }

    var user = r.data;
    var name = user.email.split('@')[0].replace(/\./g, ' ').replace(/\b\w/g, function(c){return c.toUpperCase();});
    var companies = (user.companies || []).map(function(c){ return c.name; });
    var since = new Date(user.created_at).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });

    profileName.textContent = name;
    profileEmail.textContent = user.email;
    profileProduct.value = user.product || '';
    profileCompanies.value = companies.join('\n');

    var dc = await sb.from('digests').select('id', { count: 'exact', head: true }).eq('user_id', user.id);
    var digestCount = dc.count || 0;

    profileStats.innerHTML =
      '<span class="account-stat">' + companies.length + ' account' + (companies.length !== 1 ? 's' : '') + '</span>'
      + '<span class="account-stat">' + digestCount + ' digest' + (digestCount !== 1 ? 's' : '') + ' sent</span>'
      + '<span class="account-stat">Since ' + since + '</span>';

    loadDigests(user.id);
    show(profileSection);
  }

  // ── Digests ─────────────────────────────
  async function loadDigests(userId) {
    var r = await sb.from('digests').select('sent_at, item_count, items').eq('user_id', userId).order('sent_at', { ascending: false }).limit(4);
    var digests = r.data || [];
    if (!digests.length) { digestsEmpty.hidden = false; digestsContainer.innerHTML = ''; return; }

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
  }

  // ── Save ────────────────────────────────
  profileForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    saveSuccess.hidden = true;
    setLoading(btnSave, true);

    var product = profileProduct.value.trim();
    var companies = profileCompanies.value.trim().split('\n').map(function(s){return s.trim();}).filter(Boolean);

    // Get user ID
    var session = (await sb.auth.getSession()).data.session;
    if (!session) { show(emailSection); return; }
    var email = session.user.email;

    var ur = await sb.from('users').select('id').eq('email', email).single();
    if (!ur.data) { setLoading(btnSave, false); alert('Account not found'); return; }
    var userId = ur.data.id;

    // Update product
    await sb.from('users').update({ product: product, updated_at: new Date().toISOString() }).eq('id', userId);

    // Replace companies
    await sb.from('companies').delete().eq('user_id', userId);
    if (companies.length) {
      var rows = companies.map(function(name) { return { user_id: userId, name: name }; });
      await sb.from('companies').insert(rows);
    }

    setLoading(btnSave, false);
    saveSuccess.hidden = false;
    setTimeout(function(){ saveSuccess.hidden = true; }, 4000);
    loadProfile(email);
  });

  // ── Sign out ────────────────────────────
  btnSignout.addEventListener('click', async function () {
    await sb.auth.signOut();
    otpCode.value = '';
    loginEmail.value = '';
    show(emailSection);
  });

  function esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  sb.auth.onAuthStateChange(function(event) {
    if (event === 'SIGNED_OUT') show(emailSection);
  });

  init();
})();
