/* ============================================
   Solace Scoop — Agent Mesh Demo
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
    {
      id: 'people',
      name: 'People Intel',
      icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>',
      prompt: function (company, seller) {
        return 'Research ' + company + ' and provide an update on KEY PEOPLE.\n'
          + '- Current CEO, CFO, CTO and C-suite (name, tenure, background)\n'
          + '- Recent executive changes in the last 6 months\n'
          + '- Key decision-makers for enterprise/technology purchases\n'
          + (seller ? '\nSeller context: ' + seller + '\n' : '')
          + '\nUse markdown headers and bullet points. Be specific with names and dates. Max 200 words.';
      }
    },
    {
      id: 'corporate',
      name: 'Corporate Signals',
      icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2"/></svg>',
      prompt: function (company, seller) {
        return 'Research ' + company + ' and provide an update on CORPORATE & FINANCIAL signals.\n'
          + '- Revenue, valuation, funding status\n'
          + '- Recent earnings or financial events\n'
          + '- M&A activity\n'
          + '- Major partnerships or strategic deals\n'
          + '- Expansion or contraction signals\n'
          + (seller ? '\nSeller context: ' + seller + '\n' : '')
          + '\nUse markdown headers and bullet points. Be specific with numbers and dates. Max 200 words.';
      }
    },
    {
      id: 'market',
      name: 'Market & Competitive',
      icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
      prompt: function (company, seller) {
        return 'Research ' + company + ' and provide an update on MARKET & COMPETITIVE POSITION.\n'
          + '- Primary competitors and differentiation\n'
          + '- Market share or industry position\n'
          + '- Recent product launches or announcements\n'
          + '- Industry trends affecting the business\n'
          + (seller ? '\nSeller context: ' + seller + '\n' : '')
          + '\nUse markdown headers and bullet points. Be specific. Max 200 words.';
      }
    },
    {
      id: 'risk',
      name: 'Risk & Compliance',
      icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
      prompt: function (company, seller) {
        return 'Research ' + company + ' and provide an update on RISK & COMPLIANCE.\n'
          + '- Regulatory actions, lawsuits, or compliance issues\n'
          + '- Data breaches or security incidents\n'
          + '- Negative press or reputational risks\n'
          + '- Financial risk signals\n'
          + (seller ? '\nSeller context: ' + seller + '\n' : '')
          + '\nUse markdown headers and bullet points. If no major risks, say so. Max 200 words.';
      }
    },
    {
      id: 'hiring',
      name: 'Hiring & Growth',
      icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg>',
      prompt: function (company, seller) {
        return 'Research ' + company + ' and provide an update on HIRING & GROWTH.\n'
          + '- Hiring velocity (growing, shrinking, flat?)\n'
          + '- Key departments hiring heavily\n'
          + '- Geographic expansion\n'
          + '- Senior roles signaling strategic shifts\n'
          + '- Technology stack signals from job postings\n'
          + (seller ? '\nSeller context: ' + seller + '\n' : '')
          + '\nUse markdown headers and bullet points. Be specific. Max 200 words.';
      }
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

  var savedEmail = '';

  // ── Nav scroll ──────────────────────────
  window.addEventListener('scroll', function () {
    nav.classList.toggle('nav--scrolled', window.scrollY > 10);
  }, { passive: true });

  // ── Smooth scroll ───────────────────────
  document.querySelectorAll('a[href^="#"]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      var target = document.querySelector(this.getAttribute('href'));
      if (target) {
        e.preventDefault();
        window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - 80, behavior: 'smooth' });
      }
    });
  });

  // ── Hero form: scroll to setup ──────────
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

    // Loading state
    btnSubmit.querySelector('.btn__text').hidden = true;
    btnSubmit.querySelector('.btn__loader').hidden = false;
    btnSubmit.disabled = true;

    // Clear previous results
    resultsContainer.innerHTML = '';
    resultsSection.hidden = false;

    // Scroll to results
    setTimeout(function () {
      resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 200);

    // Launch mesh for each company
    var companiesLeft = companies.length;
    companies.forEach(function (company, companyIdx) {
      setTimeout(function () {
        runCompanyMesh(company, sellerContext, companyIdx, function () {
          companiesLeft--;
          if (companiesLeft === 0) {
            btnSubmit.querySelector('.btn__text').hidden = false;
            btnSubmit.querySelector('.btn__loader').hidden = true;
            btnSubmit.disabled = false;
          }
        });
      }, companyIdx * 300); // stagger company starts
    });
  });

  // ── Run mesh for one company ────────────
  function runCompanyMesh(company, sellerContext, companyIdx, onDone) {
    var block = document.createElement('div');
    block.className = 'company-block';
    block.id = 'block-' + companyIdx;

    // Header
    block.innerHTML =
      '<div class="company-block__header">'
      +   '<h3 class="company-block__title">The scoop on <em>' + escHtml(company) + '</em></h3>'
      +   '<p class="company-block__subtitle">5 agents via Solace Agent Mesh</p>'
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
      + '<div class="results__grid" id="results-grid-' + companyIdx + '" hidden></div>'
      + '<div class="results__strategy" id="results-strategy-' + companyIdx + '" hidden></div>';

    resultsContainer.appendChild(block);

    // Render agent cards
    var grid = document.getElementById('grid-' + companyIdx);
    AGENTS.forEach(function (agent) {
      var card = document.createElement('div');
      card.className = 'agent-card';
      card.id = 'agent-' + companyIdx + '-' + agent.id;
      card.innerHTML =
        '<div class="agent-card__icon">' + agent.icon + '</div>'
        + '<div class="agent-card__info">'
        +   '<h3 class="agent-card__name">' + agent.name + '</h3>'
        +   '<div class="agent-card__status" id="status-' + companyIdx + '-' + agent.id + '">Waiting...</div>'
        + '</div>'
        + '<div class="agent-card__spinner"></div>'
        + '<svg class="agent-card__check" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>';
      grid.appendChild(card);
    });

    var agentResults = {};
    var completed = 0;
    var orchStatus = document.getElementById('orch-status-' + companyIdx);
    var systemPrompt = 'You are a specialized agent in a Solace Agent Mesh. Be concise, factual, and use markdown formatting with headers (##) and bullet points.';

    AGENTS.forEach(function (agent, idx) {
      setTimeout(function () {
        var card = document.getElementById('agent-' + companyIdx + '-' + agent.id);
        var status = document.getElementById('status-' + companyIdx + '-' + agent.id);
        card.classList.add('agent-card--running');
        status.textContent = 'Researching...';

        callPerplexity(systemPrompt, agent.prompt(company, sellerContext))
          .then(function (result) {
            agentResults[agent.id] = result;
            card.classList.remove('agent-card--running');
            card.classList.add('agent-card--done');
            status.textContent = 'Done';
          })
          .catch(function (err) {
            card.classList.remove('agent-card--running');
            card.classList.add('agent-card--error');
            status.textContent = 'Error';
            agentResults[agent.id] = '(Failed to fetch data)';
          })
          .then(function () {
            completed++;
            orchStatus.textContent = completed + '/' + AGENTS.length + ' done...';
            if (completed === AGENTS.length) {
              runOrchestrator(company, sellerContext, companyIdx, agentResults, onDone);
            }
          });
      }, idx * 150);
    });
  }

  // ── Orchestrator ────────────────────────
  function runOrchestrator(company, sellerContext, companyIdx, agentResults, onDone) {
    var orchStatus = document.getElementById('orch-status-' + companyIdx);
    orchStatus.textContent = 'Synthesizing...';

    var findings = '';
    AGENTS.forEach(function (agent) {
      findings += '\n\n## ' + agent.name + '\n' + (agentResults[agent.id] || '(no data)');
    });

    var systemPrompt = 'You are the Strategy Orchestrator in a Solace Agent Mesh. Synthesize agent findings into a clear, actionable brief. Use markdown with ## headers and bullet points.';
    var userPrompt = 'Based on these agent findings about ' + company + ', write an ACCOUNT BRIEF.\n\n'
      + 'AGENT FINDINGS:' + findings + '\n\n'
      + (sellerContext ? 'SELLER CONTEXT: ' + sellerContext + '\n\n' : '')
      + 'Write with these sections:\n'
      + '## The Big Picture\n2-3 sentences.\n'
      + '## What to Watch\n3-4 key signals as bullet points.\n'
      + '## Risks & Headwinds\n2-3 bullet points.\n'
      + '## Bottom Line\n1-2 sentences on what to do with this info.\n'
      + '\nReference specific people, numbers, dates. Max 250 words.';

    callPerplexity(systemPrompt, userPrompt)
      .then(function (strategy) { agentResults['strategy'] = strategy; })
      .catch(function () {})
      .then(function () {
        // Mark orchestrator done
        var orchCard = document.getElementById('orch-' + companyIdx);
        orchCard.classList.remove('agent-card--running');
        orchCard.classList.add('agent-card--done');
        orchStatus.textContent = 'Brief ready';

        // Render results
        showCompanyResults(companyIdx, agentResults);
        onDone();
      });
  }

  // ── Show results for one company ────────
  function showCompanyResults(companyIdx, agentResults) {
    var grid = document.getElementById('results-grid-' + companyIdx);
    grid.innerHTML = '';

    AGENTS.forEach(function (agent, idx) {
      var content = agentResults[agent.id] || '(No data)';
      var card = document.createElement('div');
      card.className = 'result-card';
      card.style.animationDelay = (idx * 0.06) + 's';
      card.innerHTML =
        '<div class="result-card__header">'
        +   '<div class="result-card__icon">' + agent.icon + '</div>'
        +   '<div class="result-card__agent">' + agent.name + '</div>'
        + '</div>'
        + '<div class="result-card__body">' + renderMarkdown(content) + '</div>';
      grid.appendChild(card);
    });
    grid.hidden = false;

    var strategy = agentResults['strategy'] || '';
    if (strategy) {
      var stratEl = document.getElementById('results-strategy-' + companyIdx);
      stratEl.innerHTML =
        '<div class="result-card">'
        + '<div class="result-card__header">'
        +   '<div class="result-card__icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4"/></svg></div>'
        +   '<div class="result-card__agent">Strategy Brief</div>'
        + '</div>'
        + '<div class="result-card__body">' + renderMarkdown(strategy) + '</div>'
        + '</div>';
      stratEl.hidden = false;
    }

    // Hide mesh cards
    var mesh = document.getElementById('mesh-' + companyIdx);
    if (mesh) mesh.style.display = 'none';
  }

  // ── Perplexity API ──────────────────────
  function callPerplexity(systemPrompt, userPrompt) {
    return fetch(PPLX_URL, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + PPLX_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: PPLX_MODEL,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt }
        ],
        max_tokens: 600,
        temperature: 0.3
      })
    })
    .then(function (res) { if (!res.ok) throw new Error('API ' + res.status); return res.json(); })
    .then(function (data) { return data.choices[0].message.content; });
  }

  // ── Markdown renderer ───────────────────
  function renderMarkdown(text) {
    if (!text) return '';
    var html = escHtml(text);

    // Headers: ## Title or **Title:** at start of line
    html = html.replace(/^#{1,3}\s+(.+)$/gm, function (_, t) {
      return '<h3>' + t.replace(/\*\*/g, '') + '</h3>';
    });

    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Italic
    html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');

    // Bullet points (- or *)
    html = html.replace(/^[-*]\s+(.+)/gm, '<!LI>$1</li>');
    // Wrap consecutive <li> in <ul>
    html = html.replace(/((?:<!LI>.*<\/li>\n?)+)/g, function (block) {
      return '<ul>' + block.replace(/<!LI>/g, '<li>') + '</ul>';
    });

    // Numbered lists
    html = html.replace(/^\d+\.\s+(.+)/gm, '<!OLI>$1</li>');
    html = html.replace(/((?:<!OLI>.*<\/li>\n?)+)/g, function (block) {
      return '<ol>' + block.replace(/<!OLI>/g, '<li>') + '</ol>';
    });

    // Citation references like [1][2] — strip them
    html = html.replace(/\[(\d+)\]/g, '');

    // Word count note — strip
    html = html.replace(/\(\d+ words?\)/gi, '');

    // Paragraphs: double newline
    html = html.replace(/\n{2,}/g, '</p><p>');
    // Single newlines (not inside lists)
    html = html.replace(/\n/g, '<br>');

    // Wrap in <p>
    html = '<p>' + html + '</p>';

    // Clean up empty elements
    html = html.replace(/<p>\s*<\/p>/g, '');
    html = html.replace(/<p>\s*(<[huo])/g, '$1');
    html = html.replace(/(<\/[huo]l>|<\/h3>)\s*<\/p>/g, '$1');
    html = html.replace(/<br>\s*(<[huo])/g, '$1');
    html = html.replace(/(<\/[huo]l>|<\/h3>)\s*<br>/g, '$1');

    return html;
  }

  function escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ── Form helpers ────────────────────────
  function showError(fieldId, msg) {
    var field = document.getElementById(fieldId);
    var el = document.createElement('div');
    el.className = 'setup-form__error';
    el.textContent = msg;
    field.after(el);
    field.focus();
  }

  function clearErrors() {
    setupForm.querySelectorAll('.setup-form__error').forEach(function (el) { el.remove(); });
  }

})();
