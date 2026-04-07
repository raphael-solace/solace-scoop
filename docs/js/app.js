/* ============================================
   Solace Scoop - Agent Mesh Demo
   ============================================ */
(function () {
  'use strict';

  // ── Config ──────────────────────────────
  var _t = [REDACTED_KEY_BYTES];
  var PPLX_KEY = atob(_t.map(function(c){return String.fromCharCode(c)}).join(''));
  var PPLX_MODEL = 'sonar';
  var PPLX_URL = 'https://api.perplexity.ai/chat/completions';

  // ── Agents ──────────────────────────────
  var AGENTS = [
    { id: 'people', name: 'People Intel',
      icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>',
      prompt: function (co, s) { return 'Research ' + co + ': KEY PEOPLE.\n- Current C-suite (name, tenure, background)\n- Recent executive changes (last 6 months): departures, new hires, promotions\n- Who makes enterprise/technology purchasing decisions?\n' + (s ? '\nThe seller context is: ' + s + '. Identify which people at ' + co + ' would be the right contacts for this product.\n' : '') + '\nMarkdown with ## headers and bullets. Be specific with names, titles, and dates. Max 200 words.'; }
    },
    { id: 'corporate', name: 'Corporate Signals',
      icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2"/></svg>',
      prompt: function (co, s) { return 'Research ' + co + ': CORPORATE & FINANCIAL signals.\n- Revenue, valuation, recent funding\n- Earnings, M&A activity, strategic deals\n- Expansion or contraction signals (hiring surges, layoffs, new offices)\n' + (s ? '\nThe seller context is: ' + s + '. Highlight any financial signals that would create an opening to sell this product to ' + co + '.\n' : '') + '\nMarkdown with ## headers and bullets. Be specific with numbers and dates. Max 200 words.'; }
    },
    { id: 'market', name: 'Market & Competitive',
      icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
      prompt: function (co, s) { return 'Research ' + co + ': MARKET & COMPETITIVE position.\n- Primary competitors and how ' + co + ' differentiates\n- Recent product launches or major announcements\n- Industry trends affecting their business\n' + (s ? '\nThe seller context is: ' + s + '. Identify competitive dynamics that would make ' + co + ' a good prospect for this product.\n' : '') + '\nMarkdown with ## headers and bullets. Max 200 words.'; }
    },
    { id: 'risk', name: 'Risk & Compliance',
      icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
      prompt: function (co, s) { return 'Research ' + co + ': RISK & COMPLIANCE.\n- Lawsuits, regulatory actions, compliance issues\n- Data breaches, security incidents\n- Reputational risks, financial risk signals\n' + (s ? '\nThe seller context is: ' + s + '. Note any risks that could either block a deal or create urgency to buy.\n' : '') + '\nMarkdown with ## headers and bullets. If no major risks, say so. Max 200 words.'; }
    },
    { id: 'hiring', name: 'Hiring & Growth',
      icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg>',
      prompt: function (co, s) { return 'Research ' + co + ': HIRING & GROWTH signals.\n- Hiring velocity (growing, shrinking, flat?)\n- Key departments hiring heavily\n- Geographic expansion, new offices\n- Senior roles that signal strategic initiatives\n' + (s ? '\nThe seller context is: ' + s + '. Highlight hiring patterns that suggest ' + co + ' is investing in areas where this product would help.\n' : '') + '\nMarkdown with ## headers and bullets. Max 200 words.'; }
    },
    { id: 'news', name: 'Recent News',
      icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 22h16a2 2 0 002-2V4a2 2 0 00-2-2H8a2 2 0 00-2 2v16a2 2 0 01-2 2zm0 0a2 2 0 01-2-2v-9c0-1.1.9-2 2-2h2"/><line x1="10" y1="6" x2="18" y2="6"/><line x1="10" y1="10" x2="18" y2="10"/><line x1="10" y1="14" x2="14" y2="14"/></svg>',
      prompt: function (co, s) { return 'Research the latest news about ' + co + ' from the past 30 days.\n- Major announcements, press releases, product updates\n- Notable media coverage or analyst commentary\n- Anything a salesperson should reference in a conversation with ' + co + '\n' + (s ? '\nThe seller context is: ' + s + '. Focus on news that creates a natural conversation opener when selling this product to ' + co + '.\n' : '') + '\nMarkdown with ## headers and bullets. Be specific with dates and sources. Max 200 words.'; }
    }
  ];

  // ── DOM ─────────────────────────────────
  var nav = document.getElementById('nav');
  var heroForm = document.getElementById('hero-form');
  var heroEmail = document.getElementById('hero-email');
  var setupForm = document.getElementById('setup-form');
  var btnSubmit = document.getElementById('btn-submit');
  var resultsSection = document.getElementById('results-section');
  var resultsContainer = document.getElementById('results-container');
  var subscribeCta = document.getElementById('subscribe-cta');
  var subscribeEmailDisplay = document.getElementById('subscribe-email-display');
  var btnSubscribe = document.getElementById('btn-subscribe');
  var btnNoThanks = document.getElementById('btn-no-thanks');
  var subscribeConfirmed = document.getElementById('subscribe-confirmed');

  var savedEmail = '';

  // ── Nav scroll ──────────────────────────
  window.addEventListener('scroll', function () {
    nav.classList.toggle('nav--scrolled', window.scrollY > 10);
  }, { passive: true });

  // ── Smooth scroll ───────────────────────
  document.querySelectorAll('a[href^="#"]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      var target = document.querySelector(this.getAttribute('href'));
      if (target) { e.preventDefault(); window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - 80, behavior: 'smooth' }); }
    });
  });

  // ── Hero form: save email, scroll to setup ──
  heroForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var email = heroEmail.value.trim();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { heroEmail.focus(); return; }
    savedEmail = email;
    var target = document.getElementById('setup');
    window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - 80, behavior: 'smooth' });
    setTimeout(function () { document.getElementById('seller-desc').focus(); }, 600);
  });

  // ── Setup form: parse & launch ──────────
  setupForm.addEventListener('submit', function (e) {
    e.preventDefault();
    clearErrors();

    var sellerDesc = document.getElementById('seller-desc').value.trim();
    var sellerUrl = document.getElementById('seller-url').value.trim();
    var companiesRaw = document.getElementById('companies').value.trim();

    if (!sellerDesc) { showError('seller-desc', 'Tell us what you sell so agents can tailor the brief.'); return; }
    if (!companiesRaw) { showError('companies', 'Add at least one company.'); return; }

    var companies = companiesRaw.split('\n').map(function (s) { return s.trim(); }).filter(Boolean).slice(0, 10);
    if (!companies.length) { showError('companies', 'Add at least one company.'); return; }

    var sellerContext = sellerDesc + (sellerUrl ? ' (' + sellerUrl + ')' : '');

    // Loading
    btnSubmit.querySelector('.btn__text').hidden = true;
    btnSubmit.querySelector('.btn__loader').hidden = false;
    btnSubmit.disabled = true;

    // Reset
    resultsContainer.innerHTML = '';
    subscribeCta.hidden = true;
    subscribeConfirmed.hidden = true;
    resultsSection.hidden = false;

    setTimeout(function () { resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 200);

    // Launch per company
    var companiesLeft = companies.length;
    companies.forEach(function (company, idx) {
      setTimeout(function () {
        runCompanyMesh(company, sellerContext, idx, function () {
          companiesLeft--;
          if (companiesLeft === 0) {
            btnSubmit.querySelector('.btn__text').hidden = false;
            btnSubmit.querySelector('.btn__loader').hidden = true;
            btnSubmit.disabled = false;
            // Show subscribe CTA
            if (savedEmail) {
              subscribeEmailDisplay.textContent = savedEmail;
              subscribeCta.hidden = false;
              setTimeout(function () { subscribeCta.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, 300);
            }
          }
        });
      }, idx * 300);
    });
  });

  // ── Subscribe buttons ───────────────────
  btnSubscribe.addEventListener('click', function () {
    btnSubscribe.hidden = true;
    btnNoThanks.hidden = true;
    subscribeConfirmed.hidden = false;
  });

  btnNoThanks.addEventListener('click', function () {
    subscribeCta.hidden = true;
  });

  // ── Run mesh for one company ────────────
  function runCompanyMesh(company, sellerContext, companyIdx, onDone) {
    var block = document.createElement('div');
    block.className = 'company-block';
    block.id = 'block-' + companyIdx;

    block.innerHTML =
      '<div class="company-block__header">'
      +   '<h3 class="company-block__title">The scoop on <em>' + esc(company) + '</em></h3>'
      +   '<p class="company-block__subtitle">6 agents via Solace Agent Mesh</p>'
      + '</div>'
      + '<div class="mesh" id="mesh-' + companyIdx + '">'
      +   '<div class="mesh__grid" id="grid-' + companyIdx + '"></div>'
      +   '<div class="mesh__orchestrator">'
      +     '<div class="agent-card agent-card--orchestrator agent-card--running" id="orch-' + companyIdx + '">'
      +       '<div class="agent-card__icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4"/></svg></div>'
      +       '<div class="agent-card__info"><h3 class="agent-card__name">Strategy Orchestrator</h3><div class="agent-card__status" id="orch-status-' + companyIdx + '">Waiting...</div></div>'
      +       '<div class="agent-card__spinner"></div>'
      +     '</div>'
      +   '</div>'
      + '</div>'
      // Strategy brief (primary output)
      + '<div class="results__strategy" id="strategy-' + companyIdx + '" hidden></div>'
      // Actions (PDF)
      + '<div class="results__actions" id="actions-' + companyIdx + '" hidden>'
      +   '<button class="btn-outline" onclick="window.print()"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9V2h12v7M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg> Download PDF</button>'
      + '</div>'
      // Agent details (collapsed)
      + '<div class="agent-details" id="details-' + companyIdx + '" hidden></div>';

    resultsContainer.appendChild(block);

    // Render mesh cards
    var grid = document.getElementById('grid-' + companyIdx);
    AGENTS.forEach(function (agent) {
      var card = document.createElement('div');
      card.className = 'agent-card';
      card.id = 'agent-' + companyIdx + '-' + agent.id;
      card.innerHTML =
        '<div class="agent-card__icon">' + agent.icon + '</div>'
        + '<div class="agent-card__info"><h3 class="agent-card__name">' + agent.name + '</h3>'
        +   '<div class="agent-card__status" id="status-' + companyIdx + '-' + agent.id + '">Waiting...</div></div>'
        + '<div class="agent-card__spinner"></div>'
        + '<svg class="agent-card__check" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>';
      grid.appendChild(card);
    });

    var agentResults = {};
    var completed = 0;
    var orchStatus = document.getElementById('orch-status-' + companyIdx);
    var sysPrm = 'You are a specialized agent in a Solace Agent Mesh, helping salespeople stay informed about their accounts. Be concise, factual, and focus on signals that matter for sales conversations. Use markdown ## headers and bullet points.';

    AGENTS.forEach(function (agent, idx) {
      setTimeout(function () {
        var card = document.getElementById('agent-' + companyIdx + '-' + agent.id);
        var status = document.getElementById('status-' + companyIdx + '-' + agent.id);
        card.classList.add('agent-card--running');
        status.textContent = 'Researching...';

        callPerplexity(sysPrm, agent.prompt(company, sellerContext))
          .then(function (r) { agentResults[agent.id] = r; card.classList.remove('agent-card--running'); card.classList.add('agent-card--done'); status.textContent = 'Done'; })
          .catch(function () { card.classList.remove('agent-card--running'); card.classList.add('agent-card--error'); status.textContent = 'Error'; agentResults[agent.id] = '(Failed)'; })
          .then(function () {
            completed++;
            orchStatus.textContent = completed + '/' + AGENTS.length + ' done...';
            if (completed === AGENTS.length) runOrchestrator(company, sellerContext, companyIdx, agentResults, onDone);
          });
      }, idx * 150);
    });
  }

  // ── Orchestrator ────────────────────────
  function runOrchestrator(company, sellerContext, companyIdx, agentResults, onDone) {
    var orchStatus = document.getElementById('orch-status-' + companyIdx);
    orchStatus.textContent = 'Synthesizing...';

    var findings = '';
    AGENTS.forEach(function (a) { findings += '\n\n## ' + a.name + '\n' + (agentResults[a.id] || '(no data)'); });

    var sysPrm = 'You are the Strategy Orchestrator in a Solace Agent Mesh. You help salespeople prepare for conversations with their accounts. Synthesize agent findings into actionable sales angles. Use markdown ## headers and bullet points.';
    var usrPrm = 'Based on these agent findings about ' + company + ', write a SALES BRIEF that helps a salesperson prepare for a conversation with ' + company + '.\n\nAGENT FINDINGS:' + findings + '\n\n'
      + (sellerContext ? 'THE SALESPERSON SELLS: ' + sellerContext + '\n\n' : '')
      + 'Write the brief with these sections:\n'
      + '## The Big Picture\n2-3 sentences on ' + company + '\'s current state and what matters most right now.\n'
      + '## Conversation Openers\n3-4 bullet points: specific recent events, news, or signals the salesperson can reference to start a relevant conversation. Each should be a natural talking point.\n'
      + '## Buying Signals\n2-3 bullet points: reasons ' + company + ' might need the seller\'s product right now.\n'
      + '## Watch Out For\n2-3 bullet points: objections, risks, or bad timing to prepare for.\n'
      + '## Recommended Next Step\n1-2 sentences: who to contact and what to say.\n'
      + '\nBe specific. Name people, cite numbers, reference dates. Max 300 words.';

    callPerplexity(sysPrm, usrPrm)
      .then(function (s) { agentResults['strategy'] = s; })
      .catch(function () {})
      .then(function () {
        var orchCard = document.getElementById('orch-' + companyIdx);
        orchCard.classList.remove('agent-card--running');
        orchCard.classList.add('agent-card--done');
        orchStatus.textContent = 'Brief ready';

        showCompanyResults(companyIdx, company, agentResults);
        onDone();
      });
  }

  // ── Show results ────────────────────────
  function showCompanyResults(companyIdx, company, agentResults) {
    // Hide mesh
    var mesh = document.getElementById('mesh-' + companyIdx);
    if (mesh) mesh.style.display = 'none';

    // Strategy brief (primary, visible)
    var strategy = agentResults['strategy'] || '';
    if (strategy) {
      var stratEl = document.getElementById('strategy-' + companyIdx);
      stratEl.innerHTML =
        '<div class="result-card">'
        + '<div class="result-card__header">'
        +   '<div class="result-card__icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4"/></svg></div>'
        +   '<div class="result-card__agent">Account Brief: ' + esc(company) + '</div>'
        + '</div>'
        + '<div class="result-card__body">' + md(strategy) + '</div>'
        + '</div>';
      stratEl.hidden = false;
    }

    // Show PDF button
    document.getElementById('actions-' + companyIdx).hidden = false;

    // Agent details (collapsed dropdowns)
    var detailsEl = document.getElementById('details-' + companyIdx);
    detailsEl.innerHTML = '<p style="font-size:0.75rem;font-weight:600;color:var(--gray-400);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:0.5rem;">Agent details</p>';

    AGENTS.forEach(function (agent) {
      var content = agentResults[agent.id] || '(No data)';
      var d = document.createElement('details');
      d.innerHTML =
        '<summary><span class="agent-details__icon">' + agent.icon + '</span> ' + agent.name + '</summary>'
        + '<div class="agent-details__body result-card__body">' + md(content) + '</div>';
      detailsEl.appendChild(d);
    });
    detailsEl.hidden = false;
  }

  // ── Perplexity API ──────────────────────
  function callPerplexity(sys, usr) {
    return fetch(PPLX_URL, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + PPLX_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: PPLX_MODEL, messages: [{ role: 'system', content: sys }, { role: 'user', content: usr }], max_tokens: 600, temperature: 0.3 })
    })
    .then(function (r) { if (!r.ok) throw new Error('API ' + r.status); return r.json(); })
    .then(function (d) { return d.choices[0].message.content; });
  }

  // ── Markdown ────────────────────────────
  function md(text) {
    if (!text) return '';
    var h = esc(text);
    h = h.replace(/^#{1,3}\s+(.+)$/gm, function (_, t) { return '<h3>' + t.replace(/\*\*/g, '') + '</h3>'; });
    h = h.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    h = h.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
    h = h.replace(/^[-*]\s+(.+)/gm, '<!LI>$1</li>');
    h = h.replace(/((?:<!LI>.*<\/li>\n?)+)/g, function (b) { return '<ul>' + b.replace(/<!LI>/g, '<li>') + '</ul>'; });
    h = h.replace(/^\d+\.\s+(.+)/gm, '<!OLI>$1</li>');
    h = h.replace(/((?:<!OLI>.*<\/li>\n?)+)/g, function (b) { return '<ol>' + b.replace(/<!OLI>/g, '<li>') + '</ol>'; });
    h = h.replace(/\[(\d+)\]/g, '');
    h = h.replace(/\(\d+ words?\)/gi, '');
    h = h.replace(/\n{2,}/g, '</p><p>');
    h = h.replace(/\n/g, '<br>');
    h = '<p>' + h + '</p>';
    h = h.replace(/<p>\s*<\/p>/g, '');
    h = h.replace(/<p>\s*(<[huo])/g, '$1');
    h = h.replace(/(<\/[huo]l>|<\/h3>)\s*<\/p>/g, '$1');
    h = h.replace(/<br>\s*(<[huo])/g, '$1');
    h = h.replace(/(<\/[huo]l>|<\/h3>)\s*<br>/g, '$1');
    return h;
  }

  function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

  // ── Form helpers ────────────────────────
  function showError(id, msg) {
    var f = document.getElementById(id);
    var el = document.createElement('div');
    el.className = 'setup-form__error';
    el.textContent = msg;
    f.after(el);
    f.focus();
  }

  function clearErrors() {
    setupForm.querySelectorAll('.setup-form__error').forEach(function (el) { el.remove(); });
  }

})();
