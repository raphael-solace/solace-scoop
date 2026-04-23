/* ============================================
   Solace Scoop - Account Dashboard
   ============================================ */
(function () {
  'use strict';

  var SB_URL = window.SCOOP_SUPABASE_URL || '';
  var SB_KEY = window.SCOOP_SUPABASE_KEY || '';
  var API = window.SCOOP_API_URL || '';
  if (!SB_URL || !SB_KEY) return;

  function sb(path) {
    return fetch(SB_URL + '/rest/v1/' + path, {
      headers: { apikey: SB_KEY, Authorization: 'Bearer ' + SB_KEY, 'Content-Type': 'application/json' }
    }).then(function(r) { return r.json(); });
  }
  function sbW(path, m, b) {
    var h = { apikey: SB_KEY, Authorization: 'Bearer ' + SB_KEY, 'Content-Type': 'application/json' };
    if (m === 'PATCH') h.Prefer = 'return=representation';
    return fetch(SB_URL + '/rest/v1/' + path, { method: m, headers: h, body: b ? JSON.stringify(b) : undefined });
  }

  var $ = document.getElementById.bind(document);
  var pendingEmail = '', userId = '';

  // ── Sections ────────────────────────
  var sections = ['auth-email', 'auth-otp', 'auth-loading', 'dashboard'];
  function show(id) { sections.forEach(function(s) { $(s).hidden = s !== id; }); $('footer').hidden = id !== 'dashboard'; }
  function btnLoad(btn, on) { btn.querySelector('.btn__text').hidden = on; btn.querySelector('.btn__loader').hidden = !on; btn.disabled = on; }

  // ── Nav ─────────────────────────────
  var nav = $('nav');
  window.addEventListener('scroll', function() { nav.classList.toggle('nav--scrolled', window.scrollY > 10); }, { passive: true });

  // ── Init ────────────────────────────
  (function init() {
    var t = localStorage.getItem('scoop_token'), e = localStorage.getItem('scoop_email');
    if (t && e) { show('auth-loading'); verify(e, t); }
    else show('auth-email');
  })();

  // ── Step 1: Email ───────────────────
  $('email-form').addEventListener('submit', function(ev) {
    ev.preventDefault(); $('email-error').hidden = true;
    var e = $('login-email').value.trim().toLowerCase();
    if (!e || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e)) return;
    var btn = $('btn-send-code'); btnLoad(btn, true);
    fetch(API + '/api/auth/send-otp', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: e }) })
      .then(function(r) { return r.json(); })
      .then(function(d) { btnLoad(btn, false); if (d.error) { err('email', d.error); return; } pendingEmail = e; $('otp-email-display').textContent = e; show('auth-otp'); $('otp-code').focus(); })
      .catch(function() { btnLoad(btn, false); err('email', 'Could not connect to server.'); });
  });

  // ── Step 2: OTP ─────────────────────
  $('otp-form').addEventListener('submit', function(ev) {
    ev.preventDefault(); $('otp-error').hidden = true;
    var c = $('otp-code').value.trim();
    if (!c || c.length !== 6) return;
    var btn = $('btn-verify'); btnLoad(btn, true);
    fetch(API + '/api/auth/verify-otp', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: pendingEmail, code: c }) })
      .then(function(r) { return r.json(); })
      .then(function(d) { btnLoad(btn, false); if (d.error) { err('otp', d.error); return; } localStorage.setItem('scoop_token', d.token); localStorage.setItem('scoop_email', pendingEmail); show('auth-loading'); load(pendingEmail); })
      .catch(function() { btnLoad(btn, false); err('otp', 'Could not connect to server.'); });
  });
  $('btn-back').addEventListener('click', function(ev) { ev.preventDefault(); $('otp-code').value = ''; show('auth-email'); });

  function err(section, msg) { $(section + '-error-text').textContent = msg; $(section + '-error').hidden = false; }

  // ── Session verify ──────────────────
  function verify(e, t) {
    fetch(API + '/api/auth/verify-session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: e, token: t }) })
      .then(function(r) { return r.json(); })
      .then(function(d) { if (d.valid) load(e); else { localStorage.clear(); show('auth-email'); } })
      .catch(function() { localStorage.clear(); show('auth-email'); });
  }

  // ── Load dashboard ──────────────────
  function load(email) {
    sb('users?email=eq.' + encodeURIComponent(email) + '&select=id,email,product,plan,created_at,companies(id,name)')
    .then(function(rows) {
      if (!rows || !rows.length) { localStorage.clear(); show('auth-email'); return; }
      var u = rows[0]; userId = u.id;
      var name = u.email.split('@')[0].replace(/\./g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
      var cos = (u.companies || []).map(function(c) { return c.name; });
      var since = new Date(u.created_at).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });

      $('dash-name').textContent = name;
      $('dash-email').textContent = u.email;
      $('profile-product').value = u.product || '';
      $('profile-companies').value = cos.join('\n');

      sb('digests?user_id=eq.' + u.id + '&select=id').then(function(d) {
        $('dash-stats').innerHTML =
          '<span class="dash__stat"><b>' + cos.length + '</b> accounts</span>' +
          '<span class="dash__stat"><b>' + (d || []).length + '</b> digests sent</span>' +
          '<span class="dash__stat">Since ' + since + '</span>';
      });

      loadNews(u.id, cos);
      loadPeople(u.id);
      buildMeetingButtons();
      show('dashboard');
    })
    .catch(function() { localStorage.clear(); show('auth-email'); });
  }

  // ── Tabs ────────────────────────────
  document.querySelectorAll('.dash__tab').forEach(function(tab) {
    tab.addEventListener('click', function() {
      document.querySelectorAll('.dash__tab').forEach(function(t) { t.classList.remove('dash__tab--active'); });
      tab.classList.add('dash__tab--active');
      document.querySelectorAll('.dash__panel').forEach(function(p) { p.hidden = true; });
      $('panel-' + tab.dataset.tab).hidden = false;
    });
  });

  // ── News by account ─────────────────
  function loadNews(uid, companyNames) {
    sb('digests?user_id=eq.' + uid + '&select=items,sent_at&order=sent_at.desc&limit=4')
    .then(function(digests) {
      var byco = {};
      companyNames.forEach(function(n) { byco[n] = []; });
      (digests || []).forEach(function(d) {
        (d.items || []).forEach(function(item) {
          var co = item.company || '';
          if (!byco[co]) byco[co] = [];
          if (!byco[co].some(function(x) { return x.headline === item.headline; })) byco[co].push(item);
        });
      });

      var sorted = Object.keys(byco).sort(function(a, b) { return byco[b].length - byco[a].length; });
      var html = '';
      var hasAny = false;

      sorted.forEach(function(co) {
        var signals = byco[co];
        var count = signals.length;
        var badge = count
          ? '<span class="acct-card__badge acct-card__badge--has">' + count + ' signal' + (count !== 1 ? 's' : '') + '</span>'
          : '<span class="acct-card__badge acct-card__badge--empty">No news</span>';

        html += '<div class="acct-card">';
        html += '<div class="acct-card__head" onclick="this.nextElementSibling.hidden=!this.nextElementSibling.hidden">';
        html += '<span class="acct-card__name">' + esc(co) + '</span>' + badge;
        html += '</div>';
        html += '<div class="acct-card__body"' + (count ? '' : ' hidden') + '>';

        if (count) {
          hasAny = true;
          signals.slice(0, 6).forEach(function(s) {
            var src = s.source_url || (s.sources && s.sources[0]) || '';
            var link = '', dom = '';
            if (src) { dom = (src.split('//')[1] || '').split('/')[0].replace('www.', ''); link = '<a href="' + esc(src) + '" target="_blank" class="signal__source">' + esc(dom) + '</a>'; }

            var tagColor = '';
            var tag = (s.tag || '').toLowerCase();
            if (tag.indexOf('risk') >= 0 || tag.indexOf('layoff') >= 0 || tag.indexOf('reorg') >= 0) tagColor = ' signal__tag--red';
            else if (tag.indexOf('partner') >= 0 || tag.indexOf('expan') >= 0 || tag.indexOf('fund') >= 0 || tag.indexOf('hiring') >= 0) tagColor = ' signal__tag--green';
            else if (tag.indexOf('compet') >= 0 || tag.indexOf('m&a') >= 0 || tag.indexOf('regulat') >= 0) tagColor = ' signal__tag--amber';

            html += '<div class="signal">';
            html += '<div class="signal__meta"><span class="signal__tag' + tagColor + '">' + esc(s.tag || '') + '</span>' + link;
            if (s.date) html += '<span class="signal__date">' + esc(s.date) + '</span>';
            html += '</div>';
            html += '<p class="signal__headline">' + esc(s.headline || '') + '</p>';
            if (s.why) html += '<p class="signal__why">' + esc(s.why) + '</p>';
            if (s.opening_line) html += '<div class="signal__opener">"' + esc(s.opening_line) + '"</div>';
            html += '</div>';
          });
        } else {
          html += '<div class="signal"><p class="signal__why">No recent signals for this account.</p></div>';
        }

        html += '</div></div>';
      });

      $('news-container').innerHTML = html;
      $('news-empty').hidden = hasAny || sorted.length > 0;
    });
  }

  // ── Save accounts ───────────────────
  $('profile-form').addEventListener('submit', function(ev) {
    ev.preventDefault();
    $('save-ok').hidden = true;
    var btn = $('btn-save'); btnLoad(btn, true);
    var email = localStorage.getItem('scoop_email');
    var product = $('profile-product').value.trim();
    var cos = $('profile-companies').value.trim().split('\n').map(function(s) { return s.trim(); }).filter(Boolean);

    sb('users?email=eq.' + encodeURIComponent(email) + '&select=id')
    .then(function(rows) {
      if (!rows || !rows.length) throw new Error('Not found');
      var uid = rows[0].id;
      return sbW('companies?user_id=eq.' + uid, 'DELETE')
        .then(function() { if (cos.length) return sbW('companies', 'POST', cos.map(function(n) { return { user_id: uid, name: n }; })); })
        .then(function() { return sbW('users?id=eq.' + uid, 'PATCH', { product: product, updated_at: new Date().toISOString() }); });
    })
    .then(function() { btnLoad(btn, false); $('save-ok').hidden = false; setTimeout(function() { $('save-ok').hidden = true; }, 4000); load(email); })
    .catch(function(e) { btnLoad(btn, false); alert('Failed: ' + e.message); });
  });

  // ── People ──────────────────────────
  function loadPeople(uid) {
    sb('people?user_id=eq.' + uid + '&select=id,company,name,title,email,linkedin,salesforce_url&order=company,name')
    .then(function(people) {
      if (!people || !people.length) {
        $('people-list').innerHTML = '<p class="dash__empty">No contacts tracked yet. Add your first contact below.</p>';
        return;
      }

      var html = '';
      people.forEach(function(p) {
        var links = '';
        if (p.linkedin) links += '<a href="' + esc(p.linkedin) + '" target="_blank" class="signal__source" style="margin-right:8px;">LinkedIn</a>';
        if (p.email) links += '<a href="mailto:' + esc(p.email) + '" class="signal__source" style="margin-right:8px;">' + esc(p.email) + '</a>';
        if (p.salesforce_url) links += '<a href="' + esc(p.salesforce_url) + '" target="_blank" class="signal__source">Salesforce</a>';

        html += '<div class="acct-card" style="margin-bottom:0.75rem;">';
        html += '<div style="padding:0.875rem 1.25rem; display:flex; align-items:center; justify-content:space-between;">';
        html += '<div>';
        html += '<p style="margin:0; font-weight:700; color:var(--navy); font-size:0.875rem;">' + esc(p.name) + '</p>';
        html += '<p style="margin:2px 0 0; font-size:0.75rem; color:var(--gray-600);">' + esc(p.title || '') + ' at ' + esc(p.company) + '</p>';
        if (links) html += '<p style="margin:4px 0 0;">' + links + '</p>';
        html += '</div>';
        html += '<button onclick="deletePerson(\'' + p.id + '\')" style="background:none; border:none; cursor:pointer; color:var(--gray-400); font-size:1.25rem; padding:0 0.5rem;" title="Remove">&times;</button>';
        html += '</div></div>';
      });

      $('people-list').innerHTML = html;
    });
  }

  // Global delete function (needs to be accessible from onclick)
  window.deletePerson = function(id) {
    sbW('people?id=eq.' + id, 'DELETE').then(function() { loadPeople(userId); });
  };

  // Add person form
  $('people-form').addEventListener('submit', function(ev) {
    ev.preventDefault();
    $('people-ok').hidden = true;
    var name = $('person-name').value.trim();
    var company = $('person-company').value.trim();
    var title = $('person-title').value.trim();
    var linkedin = $('person-linkedin').value.trim();
    if (!name || !company) return;

    sbW('people', 'POST', [{ user_id: userId, name: name, company: company, title: title, linkedin: linkedin }])
    .then(function() {
      $('person-name').value = '';
      $('person-company').value = '';
      $('person-title').value = '';
      $('person-linkedin').value = '';
      $('people-ok').hidden = false;
      setTimeout(function() { $('people-ok').hidden = true; }, 4000);
      loadPeople(userId);
    })
    .catch(function(e) { alert('Failed: ' + e.message); });
  });

  // ── Chat ────────────────────────────
  var chatMessages = $('chat-messages');
  var chatForm = $('chat-form');
  var chatInput = $('chat-input');
  var chatSend = $('chat-send');

  // Simple markdown to HTML
  function md(text) {
    return text
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/`(.+?)`/g, '<code style="background:#f1f5f4; padding:1px 4px; border-radius:3px; font-size:0.8em;">$1</code>')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" style="color:#6366f1; text-decoration:underline;">$1</a>')
      .replace(/^### (.+)$/gm, '<strong style="font-size:0.9375rem; color:var(--navy); display:block; margin:8px 0 4px;">$1</strong>')
      .replace(/^## (.+)$/gm, '<strong style="font-size:1rem; color:var(--navy); display:block; margin:10px 0 4px;">$1</strong>')
      .replace(/^- (.+)$/gm, '<span style="display:block; padding-left:1rem; position:relative;">&#8226; $1</span>')
      .replace(/\n\n/g, '</p><p style="margin:6px 0;">')
      .replace(/\n/g, '<br>');
  }

  function addMsg(text, type) {
    var div = document.createElement('div');
    div.className = 'chat__msg chat__msg--' + type;
    if (type === 'user') {
      div.innerHTML = '<p>' + esc(text) + '</p>';
    } else if (type === 'loading') {
      div.innerHTML = '<p style="color:var(--gray-400);"><em>' + text + '</em></p>';
    } else {
      div.innerHTML = '<p style="margin:0;">' + md(text) + '</p>';
    }
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return div;
  }

  function addSources(sources) {
    if (!sources || !sources.length) return;
    var div = document.createElement('div');
    div.className = 'chat__msg chat__msg--bot';
    var html = '<p style="margin:0 0 4px; font-size:0.6875rem; font-weight:600; color:var(--gray-400); text-transform:uppercase; letter-spacing:0.05em;">Sources</p>';
    sources.forEach(function(s) {
      if (typeof s === 'string' && s.startsWith('http')) {
        var domain = s.split('//')[1]; if (domain) domain = domain.split('/')[0].replace('www.','');
        html += '<a href="' + esc(s) + '" target="_blank" style="display:inline-block; margin:2px 4px 2px 0; padding:2px 8px; background:var(--gray-50); border:1px solid var(--gray-200); border-radius:100px; font-size:0.6875rem; color:#6366f1; text-decoration:none;">' + esc(domain) + '</a>';
      }
    });
    div.innerHTML = html;
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function sendChat(q) {
    addMsg(q, 'user');
    chatInput.value = '';
    chatSend.disabled = true;
    var loading = addMsg('Researching...', 'loading');

    var email = localStorage.getItem('scoop_email') || '';
    var accounts = ($('profile-companies').value || '').split('\n').filter(Boolean).join(', ');

    fetch(API + '/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q, email: email, accounts: accounts })
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      loading.remove();
      chatSend.disabled = false;
      if (d.answer) {
        addMsg(d.answer, 'bot');
        if (d.sources) addSources(d.sources);
      } else if (d.error) {
        addMsg('Sorry, I couldn\'t process that. ' + d.error, 'bot');
      }
    })
    .catch(function() {
      loading.remove();
      chatSend.disabled = false;
      addMsg('Could not connect to Scoop. Please try again.', 'bot');
    });
  }

  chatForm.addEventListener('submit', function(ev) {
    ev.preventDefault();
    var q = chatInput.value.trim();
    if (!q) return;
    sendChat(q);
  });

  // Meeting prep quick buttons (populated from user's accounts)
  function buildMeetingButtons() {
    var companies = ($('profile-companies').value || '').split('\n').map(function(s){return s.trim();}).filter(Boolean);
    var container = $('chat-quick-buttons');
    if (!container || !companies.length) return;
    var html = '<p style="font-size:0.6875rem; font-weight:600; color:var(--gray-400); text-transform:uppercase; letter-spacing:0.05em; margin:0 0 6px;">Quick meeting prep</p>';
    companies.slice(0, 8).forEach(function(co) {
      html += '<button class="chat__quick-btn" data-company="' + esc(co) + '">' + esc(co) + '</button>';
    });
    container.innerHTML = html;

    container.querySelectorAll('.chat__quick-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
        sendChat('I have a meeting with ' + btn.dataset.company + ' tomorrow. Give me a quick prep: recent news, key contacts to mention, talking points, and anything I should watch out for.');
      });
    });
  }

  // ── Sign out ────────────────────────
  $('btn-signout').addEventListener('click', function() {
    localStorage.removeItem('scoop_token'); localStorage.removeItem('scoop_email');
    $('otp-code').value = ''; $('login-email').value = '';
    show('auth-email');
  });

  function esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
})();
