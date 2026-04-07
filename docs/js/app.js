/* ============================================
   Solace Scoop — Agent Mesh Demo
   ============================================ */
(function () {
  'use strict';

  // ── Config ──────────────────────────────
  var PPLX_MODEL = 'sonar';
  var PPLX_URL = 'https://api.perplexity.ai/chat/completions';

  // Key from URL param (?key=...) or localStorage
  function getPplxKey() {
    var params = new URLSearchParams(window.location.search);
    var urlKey = params.get('key');
    if (urlKey) {
      localStorage.setItem('pplx_key', urlKey);
      // Clean URL without reloading
      window.history.replaceState({}, '', window.location.pathname);
      return urlKey;
    }
    return localStorage.getItem('pplx_key') || '';
  }

  var PPLX_KEY = getPplxKey();

  // ── Agents ──────────────────────────────
  var AGENTS = [
    {
      id: 'people',
      name: 'People Intel Agent',
      icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>',
      prompt: function (company, ctx) {
        return 'You are an executive intelligence analyst. Research ' + company + ' and provide a focused brief on KEY PEOPLE. Include:\n'
          + '- Current CEO, CFO, CTO and other C-suite (name, tenure, background)\n'
          + '- Recent executive changes in the last 6 months (departures, new hires, promotions)\n'
          + '- Key decision-makers for technology or enterprise purchases\n'
          + '- Board members or investors with notable influence\n'
          + (ctx ? '\nContext: ' + ctx + '\n' : '')
          + '\nBe specific with names, titles, and dates. Keep it factual and concise. Max 200 words.';
      }
    },
    {
      id: 'corporate',
      name: 'Corporate Intel Agent',
      icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2"/><line x1="12" y1="12" x2="12" y2="12.01"/></svg>',
      prompt: function (company, ctx) {
        return 'You are a corporate intelligence analyst. Research ' + company + ' and provide a focused brief on CORPORATE & FINANCIAL signals. Include:\n'
          + '- Revenue, valuation, funding status (latest round, investors)\n'
          + '- Recent earnings highlights or financial events\n'
          + '- M&A activity (acquisitions made or acquisition rumors)\n'
          + '- Major partnerships or strategic deals announced recently\n'
          + '- Expansion or contraction signals (hiring surges, layoffs, office changes)\n'
          + (ctx ? '\nContext: ' + ctx + '\n' : '')
          + '\nBe specific with numbers and dates. Keep it factual and concise. Max 200 words.';
      }
    },
    {
      id: 'market',
      name: 'Market & Competitive Agent',
      icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
      prompt: function (company, ctx) {
        return 'You are a competitive intelligence analyst. Research ' + company + ' and provide a focused brief on MARKET & COMPETITIVE POSITION. Include:\n'
          + '- Primary competitors and how ' + company + ' differentiates\n'
          + '- Market share or position in their industry\n'
          + '- Recent product launches or major feature announcements\n'
          + '- Industry trends affecting their business\n'
          + '- Customer sentiment or notable wins/losses\n'
          + (ctx ? '\nContext: ' + ctx + '\n' : '')
          + '\nBe specific and cite sources where possible. Max 200 words.';
      }
    },
    {
      id: 'risk',
      name: 'Risk & Compliance Agent',
      icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
      prompt: function (company, ctx) {
        return 'You are a risk and compliance analyst. Research ' + company + ' and provide a focused brief on RISK & COMPLIANCE signals. Include:\n'
          + '- Any regulatory actions, lawsuits, or compliance issues\n'
          + '- Data breaches or security incidents\n'
          + '- Negative press or reputational risks\n'
          + '- Financial risk signals (debt concerns, downgrades, revenue decline)\n'
          + '- Upcoming regulatory deadlines affecting their industry\n'
          + (ctx ? '\nContext: ' + ctx + '\n' : '')
          + '\nBe specific with dates and facts. If no major risks found, say so. Max 200 words.';
      }
    },
    {
      id: 'hiring',
      name: 'Hiring & Growth Agent',
      icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg>',
      prompt: function (company, ctx) {
        return 'You are a hiring trends analyst. Research ' + company + ' and provide a focused brief on HIRING & GROWTH SIGNALS. Include:\n'
          + '- Overall hiring velocity (are they growing, shrinking, or flat?)\n'
          + '- Key departments hiring heavily (engineering, sales, etc.)\n'
          + '- New office locations or geographic expansion\n'
          + '- Senior-level roles open that signal strategic initiatives\n'
          + '- Technology stack signals from job postings (what tools/platforms they use)\n'
          + (ctx ? '\nContext: ' + ctx + '\n' : '')
          + '\nBe specific. Max 200 words.';
      }
    }
  ];

  // ── State ───────────────────────────────
  var currentScreen = 'welcome';
  var history = [];
  var agentResults = {};

  // ── DOM ─────────────────────────────────
  var btnStart = document.getElementById('btn-start');
  var btnResearch = document.getElementById('btn-research');
  var btnAgain = document.getElementById('btn-again');
  var btnBack = document.getElementById('nav-back');
  var companyInput = document.getElementById('company-name');
  var contextInput = document.getElementById('context-input');
  var keyRow = document.getElementById('key-row');
  var pplxKeyInput = document.getElementById('pplx-key');
  var meshGrid = document.getElementById('mesh-grid');
  var meshCompany = document.getElementById('mesh-company');
  var resultsCompany = document.getElementById('results-company');
  var resultsGrid = document.getElementById('results-grid');
  var resultsStrategy = document.getElementById('results-strategy');
  var orchestratorStatus = document.getElementById('orchestrator-status');
  var orchestratorSpinner = document.getElementById('orchestrator-spinner');

  // ── Navigation ──────────────────────────
  function goToScreen(id) {
    var oldEl = document.getElementById('screen-' + currentScreen);
    var newEl = document.getElementById('screen-' + id);
    if (!oldEl || !newEl || currentScreen === id) return;

    history.push(currentScreen);
    oldEl.classList.add('exiting');

    setTimeout(function () {
      oldEl.classList.remove('active', 'exiting');
      newEl.classList.add('active');
      newEl.scrollTop = 0;
      currentScreen = id;
      btnBack.hidden = (id === 'welcome');
    }, 300);
  }

  function goBack() {
    if (!history.length) return;
    var prev = history.pop();
    var oldEl = document.getElementById('screen-' + currentScreen);
    var newEl = document.getElementById('screen-' + prev);

    oldEl.classList.add('exiting');
    setTimeout(function () {
      oldEl.classList.remove('active', 'exiting');
      newEl.classList.add('active');
      newEl.scrollTop = 0;
      currentScreen = prev;
      btnBack.hidden = (prev === 'welcome');
    }, 300);
  }

  // ── Event Listeners ─────────────────────
  btnStart.addEventListener('click', function () {
    goToScreen('input');
    // Show key input if no key saved
    if (!PPLX_KEY) {
      keyRow.hidden = false;
    }
  });
  btnBack.addEventListener('click', goBack);
  btnAgain.addEventListener('click', function () {
    history = [];
    var screens = document.querySelectorAll('.screen');
    screens.forEach(function (s) { s.classList.remove('active', 'exiting'); });
    document.getElementById('screen-input').classList.add('active');
    currentScreen = 'input';
    btnBack.hidden = false;
    companyInput.value = '';
    contextInput.value = '';
    companyInput.focus();
    btnResearch.disabled = true;
    keyRow.hidden = !!PPLX_KEY;
  });

  companyInput.addEventListener('input', function () {
    btnResearch.disabled = !companyInput.value.trim();
  });

  companyInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && companyInput.value.trim()) {
      e.preventDefault();
      launchMesh();
    }
  });

  btnResearch.addEventListener('click', function () {
    if (companyInput.value.trim()) launchMesh();
  });

  // ── Perplexity API ──────────────────────
  function callPerplexity(systemPrompt, userPrompt) {
    return fetch(PPLX_URL, {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + PPLX_KEY,
        'Content-Type': 'application/json'
      },
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
    .then(function (res) {
      if (!res.ok) throw new Error('API ' + res.status);
      return res.json();
    })
    .then(function (data) {
      return data.choices[0].message.content;
    });
  }

  // ── Render Agent Cards ──────────────────
  function renderMeshGrid() {
    meshGrid.innerHTML = '';
    AGENTS.forEach(function (agent) {
      var card = document.createElement('div');
      card.className = 'agent-card';
      card.id = 'agent-' + agent.id;
      card.innerHTML =
        '<div class="agent-card__icon">' + agent.icon + '</div>'
        + '<div class="agent-card__info">'
        +   '<h3 class="agent-card__name">' + agent.name + '</h3>'
        +   '<div class="agent-card__status" id="status-' + agent.id + '">Waiting...</div>'
        +   '<div class="agent-card__preview" id="preview-' + agent.id + '"></div>'
        + '</div>'
        + '<div class="agent-card__spinner"></div>'
        + '<svg class="agent-card__check" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>';
      meshGrid.appendChild(card);
    });
  }

  // ── Launch Agent Mesh ───────────────────
  function launchMesh() {
    var company = companyInput.value.trim();
    var ctx = contextInput.value.trim();
    if (!company) return;

    // Grab key from input if not already set
    if (!PPLX_KEY && pplxKeyInput) {
      var k = pplxKeyInput.value.trim();
      if (!k) { pplxKeyInput.focus(); return; }
      PPLX_KEY = k;
      localStorage.setItem('pplx_key', k);
    }

    agentResults = {};
    meshCompany.textContent = company;
    renderMeshGrid();

    // Reset orchestrator
    orchestratorStatus.textContent = 'Waiting for agents...';
    var orchCard = document.querySelector('.agent-card--orchestrator');
    orchCard.classList.remove('agent-card--done');
    orchCard.classList.add('agent-card--running');

    goToScreen('mesh');

    var completed = 0;

    // Launch all agents in parallel
    AGENTS.forEach(function (agent, idx) {
      // Stagger start slightly for visual effect
      setTimeout(function () {
        var card = document.getElementById('agent-' + agent.id);
        var status = document.getElementById('status-' + agent.id);
        var preview = document.getElementById('preview-' + agent.id);

        card.classList.add('agent-card--running');
        status.textContent = 'Researching...';

        var systemPrompt = 'You are a specialized research agent in an agent mesh architecture. You receive events via Solace PubSub+ and publish your findings back to the mesh. Be concise and factual.';
        var userPrompt = agent.prompt(company, ctx);

        callPerplexity(systemPrompt, userPrompt)
          .then(function (result) {
            agentResults[agent.id] = result;
            card.classList.remove('agent-card--running');
            card.classList.add('agent-card--done');
            status.textContent = 'Complete';
            // Show first ~120 chars as preview
            preview.textContent = result.substring(0, 150) + '...';
            completed++;
            orchestratorStatus.textContent = completed + '/' + AGENTS.length + ' agents reporting...';

            if (completed === AGENTS.length) {
              runOrchestrator(company, ctx);
            }
          })
          .catch(function (err) {
            card.classList.remove('agent-card--running');
            card.classList.add('agent-card--error');
            status.textContent = 'Error: ' + err.message;
            agentResults[agent.id] = '(Research failed for this agent)';
            completed++;
            if (completed === AGENTS.length) {
              runOrchestrator(company, ctx);
            }
          });
      }, idx * 200); // 200ms stagger
    });
  }

  // ── Orchestrator: Synthesize ────────────
  function runOrchestrator(company, ctx) {
    orchestratorStatus.textContent = 'Synthesizing intelligence brief...';

    var findings = '';
    AGENTS.forEach(function (agent) {
      findings += '\n\n## ' + agent.name + '\n' + (agentResults[agent.id] || '(no data)');
    });

    var systemPrompt = 'You are the Strategy Orchestrator in a Solace Agent Mesh. You receive research findings from 5 specialized agents via PubSub+ events and synthesize them into actionable positioning.';

    var userPrompt = 'Based on the following research from our agent mesh about ' + company + ', write a STRATEGIC POSITIONING BRIEF.\n\n'
      + 'AGENT FINDINGS:' + findings + '\n\n'
      + (ctx ? 'SELLER CONTEXT: ' + ctx + '\n\n' : '')
      + 'Write a brief with these sections:\n'
      + '1. **Executive Summary** (2-3 sentences on the company\'s current state)\n'
      + '2. **Key Opportunities** (3-4 bullet points on timing, openings, or leverage points)\n'
      + '3. **Watch Out For** (2-3 risks or objections to prepare for)\n'
      + '4. **Recommended Approach** (2-3 sentences on how to engage)\n'
      + '\nBe specific and actionable. Reference specific people, events, or data points from the research. Max 300 words.';

    callPerplexity(systemPrompt, userPrompt)
      .then(function (strategy) {
        agentResults['strategy'] = strategy;
        var orchCard = document.querySelector('.agent-card--orchestrator');
        orchCard.classList.remove('agent-card--running');
        orchCard.classList.add('agent-card--done');
        orchestratorStatus.textContent = 'Brief ready!';

        // Short delay then show results
        setTimeout(function () { showResults(company); }, 600);
      })
      .catch(function (err) {
        orchestratorStatus.textContent = 'Error synthesizing: ' + err.message;
        // Still show individual results
        setTimeout(function () { showResults(company); }, 1000);
      });
  }

  // ── Show Results ────────────────────────
  function showResults(company) {
    resultsCompany.textContent = company;
    resultsGrid.innerHTML = '';

    AGENTS.forEach(function (agent, idx) {
      var content = agentResults[agent.id] || '(No data)';
      var card = document.createElement('div');
      card.className = 'result-card';
      card.style.animationDelay = (idx * 0.08) + 's';

      card.innerHTML =
        '<div class="result-card__header">'
        +   '<div class="result-card__icon">' + agent.icon + '</div>'
        +   '<div class="result-card__agent">' + agent.name + '</div>'
        + '</div>'
        + '<div class="result-card__body">' + formatMarkdown(content) + '</div>';

      resultsGrid.appendChild(card);
    });

    // Strategy card
    var strategy = agentResults['strategy'] || '';
    if (strategy) {
      resultsStrategy.innerHTML =
        '<div class="result-card">'
        + '<div class="result-card__header">'
        +   '<div class="result-card__icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4m-2.83-7.17l-2.83 2.83m-8.48 8.48l-2.83 2.83m0-14.14l2.83 2.83m8.48 8.48l2.83 2.83"/></svg></div>'
        +   '<div class="result-card__agent">Strategy Orchestrator</div>'
        + '</div>'
        + '<div class="result-card__body">' + formatMarkdown(strategy) + '</div>'
        + '</div>';
    }

    goToScreen('results');
  }

  // ── Simple Markdown ─────────────────────
  function formatMarkdown(text) {
    if (!text) return '';
    return text
      // Escape HTML
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      // Bold
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      // Bullet points
      .replace(/^[-*] (.+)/gm, '<li>$1</li>')
      // Wrap consecutive <li> in <ul>
      .replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>')
      // Numbered lists
      .replace(/^\d+\.\s*\*?\*?(.+?)\*?\*?$/gm, '<li><strong>$1</strong></li>')
      // Paragraphs
      .replace(/\n{2,}/g, '</p><p>')
      .replace(/\n/g, '<br>')
      .replace(/^/, '<p>')
      .replace(/$/, '</p>')
      // Clean up empty paras
      .replace(/<p>\s*<\/p>/g, '');
  }

})();
