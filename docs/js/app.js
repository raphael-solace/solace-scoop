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
      icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>',
      prompt: function (company) {
        return 'Research ' + company + ' and provide a focused update on KEY PEOPLE. Include:\n'
          + '- Current CEO, CFO, CTO and other C-suite (name, tenure, background)\n'
          + '- Recent executive changes in the last 6 months\n'
          + '- Key decision-makers for technology or enterprise purchases\n'
          + '- Board members or investors with notable influence\n'
          + '\nBe specific with names, titles, and dates. Max 200 words.';
      }
    },
    {
      id: 'corporate',
      name: 'Corporate Signals',
      icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2"/></svg>',
      prompt: function (company) {
        return 'Research ' + company + ' and provide a focused update on CORPORATE & FINANCIAL signals. Include:\n'
          + '- Revenue, valuation, funding status (latest round, investors)\n'
          + '- Recent earnings highlights or financial events\n'
          + '- M&A activity (acquisitions made or rumors)\n'
          + '- Major partnerships or strategic deals\n'
          + '- Expansion or contraction signals (hiring surges, layoffs, office changes)\n'
          + '\nBe specific with numbers and dates. Max 200 words.';
      }
    },
    {
      id: 'market',
      name: 'Market & Competitive',
      icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
      prompt: function (company) {
        return 'Research ' + company + ' and provide a focused update on MARKET & COMPETITIVE POSITION. Include:\n'
          + '- Primary competitors and how ' + company + ' differentiates\n'
          + '- Market share or position in their industry\n'
          + '- Recent product launches or major announcements\n'
          + '- Industry trends affecting their business\n'
          + '- Customer sentiment or notable wins/losses\n'
          + '\nBe specific. Max 200 words.';
      }
    },
    {
      id: 'risk',
      name: 'Risk & Compliance',
      icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
      prompt: function (company) {
        return 'Research ' + company + ' and provide a focused update on RISK & COMPLIANCE. Include:\n'
          + '- Any regulatory actions, lawsuits, or compliance issues\n'
          + '- Data breaches or security incidents\n'
          + '- Negative press or reputational risks\n'
          + '- Financial risk signals (debt, downgrades, revenue decline)\n'
          + '- Upcoming regulatory deadlines\n'
          + '\nBe specific. If no major risks found, say so. Max 200 words.';
      }
    },
    {
      id: 'hiring',
      name: 'Hiring & Growth',
      icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg>',
      prompt: function (company) {
        return 'Research ' + company + ' and provide a focused update on HIRING & GROWTH. Include:\n'
          + '- Overall hiring velocity (growing, shrinking, or flat?)\n'
          + '- Key departments hiring heavily\n'
          + '- New office locations or geographic expansion\n'
          + '- Senior-level roles open that signal strategic initiatives\n'
          + '- Technology stack signals from job postings\n'
          + '\nBe specific. Max 200 words.';
      }
    }
  ];

  // ── State ───────────────────────────────
  var agentResults = {};
  var isRunning = false;

  // ── DOM ─────────────────────────────────
  var nav = document.getElementById('nav');
  var heroForm = document.getElementById('hero-form');
  var heroCompany = document.getElementById('hero-company');
  var heroBtn = document.getElementById('hero-btn');
  var demoForm = document.getElementById('demo-form');
  var demoCompany = document.getElementById('demo-company');
  var demoBtn = document.getElementById('demo-btn');
  var meshSection = document.getElementById('mesh-section');
  var meshGrid = document.getElementById('mesh-grid');
  var meshCompanyEl = document.getElementById('mesh-company');
  var resultsSection = document.getElementById('results-section');
  var resultsCompanyEl = document.getElementById('results-company');
  var resultsGrid = document.getElementById('results-grid');
  var resultsStrategy = document.getElementById('results-strategy');
  var orchestratorStatus = document.getElementById('orchestrator-status');

  // ── Nav scroll ──────────────────────────
  window.addEventListener('scroll', function () {
    nav.classList.toggle('nav--scrolled', window.scrollY > 10);
  }, { passive: true });

  // ── Smooth scroll anchors ───────────────
  document.querySelectorAll('a[href^="#"]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      var target = document.querySelector(this.getAttribute('href'));
      if (target) {
        e.preventDefault();
        window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - 80, behavior: 'smooth' });
      }
    });
  });

  // ── Enable buttons on input ─────────────
  heroCompany.addEventListener('input', function () {
    heroBtn.disabled = !heroCompany.value.trim();
  });
  demoCompany.addEventListener('input', function () {
    demoBtn.disabled = !demoCompany.value.trim();
  });

  // ── Hero form: scroll to demo, fill, launch ──
  heroForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var company = heroCompany.value.trim();
    if (!company) return;
    demoCompany.value = company;
    demoBtn.disabled = false;
    var target = document.getElementById('demo');
    window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - 80, behavior: 'smooth' });
    setTimeout(function () { launchMesh(company); }, 500);
  });

  // ── Demo form: launch directly ──────────
  demoForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var company = demoCompany.value.trim();
    if (!company || isRunning) return;
    launchMesh(company);
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
        + '<svg class="agent-card__check" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>';
      meshGrid.appendChild(card);
    });
  }

  // ── Launch Agent Mesh ───────────────────
  function launchMesh(company) {
    if (isRunning) return;
    isRunning = true;
    agentResults = {};

    // Update UI
    demoBtn.querySelector('.btn__text').hidden = true;
    demoBtn.querySelector('.btn__loader').hidden = false;
    demoBtn.disabled = true;

    meshCompanyEl.textContent = company;
    renderMeshGrid();

    // Reset orchestrator
    orchestratorStatus.textContent = 'Waiting for agents...';
    var orchCard = document.querySelector('.agent-card--orchestrator');
    orchCard.classList.remove('agent-card--done');
    orchCard.classList.add('agent-card--running');

    // Show mesh, hide old results
    meshSection.hidden = false;
    resultsSection.hidden = true;

    // Scroll to mesh
    setTimeout(function () {
      meshSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 100);

    var completed = 0;
    var systemPrompt = 'You are a specialized agent in a Solace Agent Mesh. You receive events via Solace PubSub+ and publish your findings back to the mesh. Be concise and factual.';

    // Launch all agents in parallel
    AGENTS.forEach(function (agent, idx) {
      setTimeout(function () {
        var card = document.getElementById('agent-' + agent.id);
        var status = document.getElementById('status-' + agent.id);
        var preview = document.getElementById('preview-' + agent.id);

        card.classList.add('agent-card--running');
        status.textContent = 'Researching...';

        callPerplexity(systemPrompt, agent.prompt(company))
          .then(function (result) {
            agentResults[agent.id] = result;
            card.classList.remove('agent-card--running');
            card.classList.add('agent-card--done');
            status.textContent = 'Done';
            preview.textContent = result.substring(0, 120) + '...';
            completed++;
            orchestratorStatus.textContent = completed + '/' + AGENTS.length + ' agents done...';
            if (completed === AGENTS.length) runOrchestrator(company);
          })
          .catch(function (err) {
            card.classList.remove('agent-card--running');
            card.classList.add('agent-card--error');
            status.textContent = 'Error: ' + err.message;
            agentResults[agent.id] = '(Failed to fetch data for this agent)';
            completed++;
            if (completed === AGENTS.length) runOrchestrator(company);
          });
      }, idx * 150);
    });
  }

  // ── Orchestrator: Synthesize ────────────
  function runOrchestrator(company) {
    orchestratorStatus.textContent = 'Synthesizing brief...';

    var findings = '';
    AGENTS.forEach(function (agent) {
      findings += '\n\n## ' + agent.name + '\n' + (agentResults[agent.id] || '(no data)');
    });

    var systemPrompt = 'You are the Strategy Orchestrator in a Solace Agent Mesh. You receive findings from 5 agents via PubSub+ events and synthesize them into an actionable summary.';

    var userPrompt = 'Based on these agent findings about ' + company + ', write a concise ACCOUNT BRIEF.\n\n'
      + 'AGENT FINDINGS:' + findings + '\n\n'
      + 'Write a brief with:\n'
      + '1. **The Big Picture** (2-3 sentences on where this company is right now)\n'
      + '2. **What to Watch** (3-4 bullet points — the signals that matter most)\n'
      + '3. **Risks & Headwinds** (2-3 bullet points)\n'
      + '4. **Bottom Line** (1-2 sentences — what would you do with this info?)\n'
      + '\nBe specific. Reference people, numbers, and dates from the findings. Max 250 words.';

    callPerplexity(systemPrompt, userPrompt)
      .then(function (strategy) {
        agentResults['strategy'] = strategy;
        finishMesh(company);
      })
      .catch(function () {
        finishMesh(company);
      });
  }

  function finishMesh(company) {
    var orchCard = document.querySelector('.agent-card--orchestrator');
    orchCard.classList.remove('agent-card--running');
    orchCard.classList.add('agent-card--done');
    orchestratorStatus.textContent = 'Brief ready';

    // Reset button
    demoBtn.querySelector('.btn__text').hidden = false;
    demoBtn.querySelector('.btn__loader').hidden = true;
    demoBtn.disabled = false;
    isRunning = false;

    // Show results
    setTimeout(function () { showResults(company); }, 400);
  }

  // ── Show Results ────────────────────────
  function showResults(company) {
    resultsCompanyEl.textContent = company;
    resultsGrid.innerHTML = '';

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
        + '<div class="result-card__body">' + formatMarkdown(content) + '</div>';
      resultsGrid.appendChild(card);
    });

    var strategy = agentResults['strategy'] || '';
    if (strategy) {
      resultsStrategy.innerHTML =
        '<div class="result-card">'
        + '<div class="result-card__header">'
        +   '<div class="result-card__icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4"/></svg></div>'
        +   '<div class="result-card__agent">Strategy Brief</div>'
        + '</div>'
        + '<div class="result-card__body">' + formatMarkdown(strategy) + '</div>'
        + '</div>';
    }

    resultsSection.hidden = false;
    setTimeout(function () {
      resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 100);
  }

  // ── Simple Markdown ─────────────────────
  function formatMarkdown(text) {
    if (!text) return '';
    return text
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/^[-*] (.+)/gm, '<li>$1</li>')
      .replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>')
      .replace(/^\d+\.\s*\*?\*?(.+?)\*?\*?$/gm, '<li><strong>$1</strong></li>')
      .replace(/\n{2,}/g, '</p><p>')
      .replace(/\n/g, '<br>')
      .replace(/^/, '<p>').replace(/$/, '</p>')
      .replace(/<p>\s*<\/p>/g, '');
  }

})();
