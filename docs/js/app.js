/* ============================================
   Solace Scoop - Agent Mesh Demo
   ============================================ */
(function () {
  'use strict';

  // ── Config ──────────────────────────────
  // Keys are loaded from the page or prompted — never hardcoded here.
  var PPLX_KEY = window.SCOOP_PPLX_KEY || '';
  var PPLX_MODEL = 'sonar';
  var PPLX_URL = 'https://api.perplexity.ai/chat/completions';

  var SUPABASE_URL = window.SCOOP_SUPABASE_URL || '';
  var SUPABASE_KEY = window.SCOOP_SUPABASE_KEY || '';

  // ── Helpers ─────────────────────────────
  // Extract a clean company name and domain from user input (URL or name)
  function parseCompanyInput(input) {
    input = input.trim();
    var domain = '';
    var name = input;
    // If it looks like a URL or domain
    var urlMatch = input.match(/(?:https?:\/\/)?(?:www\.)?([a-z0-9-]+(?:\.[a-z]{2,})+)/i);
    if (urlMatch) {
      domain = urlMatch[1];
      // Derive company name from domain (e.g. "solace.com" -> "Solace")
      name = domain.split('.')[0];
      name = name.charAt(0).toUpperCase() + name.slice(1);
    }
    return { name: name, domain: domain, raw: input };
  }

  // ── Solace context ──────────────────────
  var SOLACE_CONTEXT = 'Solace provides PubSub+ Event Broker (hardware, software, cloud), Event Portal for event-driven architecture design and governance, and Agent Mesh for agentic AI. '
    + 'Key use cases: event-driven integration, real-time data streaming, hybrid/multi-cloud messaging, microservices communication, IoT event processing, and AI agent orchestration. '
    + 'Competitors: Confluent/Kafka, IBM MQ, TIBCO, RabbitMQ, AWS EventBridge, Azure Service Bus, Google Pub/Sub. '
    + 'Key partners: Accenture, Deloitte, Boomi, SAP, Informatica. '
    + 'Buyer personas: IT Architects, Enterprise Architects, Integration Leads, Heads of Integration, CTOs, VPs of Engineering, Platform Engineering Leads, Directors of IT.';

  // ── Agents ──────────────────────────────
  var AGENTS = [
    { id: 'champions', name: 'Champions & Stakeholders',
      prompt: function (co, s, domain) {
        return 'Research ' + co + ': find the KEY PEOPLE who are champions or stakeholders for integration, middleware, and event-driven architecture decisions.\n'
          + 'I work at Solace and need to know who my champions and stakeholders are at ' + co + '.\n'
          + 'Find:\n'
          + '- IT Architects, Enterprise Architects, Solution Architects, Integration Architects\n'
          + '- CTOs, VPs of Engineering, Heads of Integration, Heads of Platform\n'
          + '- Directors of IT, Engineering Managers for integration/middleware/messaging teams\n'
          + '- Anyone with "integration", "middleware", "event-driven", "messaging", "API", or "platform" in their title\n'
          + '- Recent hires or role changes in these positions (last 6 months)\n'
          + '- Their background: where they came from, what technologies they know\n'
          + '- LinkedIn profiles or conference talks if available\n'
          + '\nIMPORTANT: Only include information from the last 2 weeks for news/moves. Background info can be older.\n'
          + '\nMarkdown with ## headers and bullets. Include names, titles, and context. Max 250 words.';
      }
    },
    { id: 'eda', name: 'EDA & Integration',
      prompt: function (co, s, domain) {
        var target = domain ? 'the website ' + domain + ' (company: ' + co + ')' : co;
        return 'Research ' + target + ': EVENT-DRIVEN ARCHITECTURE and INTEGRATION landscape.\n'
          + 'I work at Solace and need to understand how ' + co + ' handles integration and messaging.\n'
          + 'Find:\n'
          + '- What messaging, middleware, or event broker technologies does ' + co + ' use? (Kafka, MQ, TIBCO, RabbitMQ, Solace, etc.)\n'
          + '- Are they doing event-driven architecture, microservices, or real-time data streaming?\n'
          + '- What cloud providers do they use? (AWS, Azure, GCP, hybrid/multi-cloud)\n'
          + '- Any integration platforms (MuleSoft, Boomi, Informatica, SAP PI/PO)?\n'
          + '- Job postings mentioning: event-driven, messaging, middleware, integration, Kafka, MQ, pub/sub, streaming, API gateway\n'
          + '- Blog posts, conference talks, or case studies about their integration architecture\n'
          + (domain ? '\nCheck ' + domain + ' for technical documentation, architecture blogs, or technology pages.\n' : '')
          + '\nIMPORTANT: Only include news/announcements from the last 2 weeks. Technical stack info can be older.\n'
          + '\nMarkdown with ## headers and bullets. Be specific. Max 250 words.';
      }
    },
    { id: 'initiatives', name: 'IT Initiatives',
      prompt: function (co, s) {
        return 'Research ' + co + ': CURRENT IT & TECHNOLOGY INITIATIVES relevant to event-driven architecture and integration.\n'
          + 'I work at Solace and need to know what ' + co + ' is building that could use event brokers, messaging, or integration middleware.\n'
          + 'Find:\n'
          + '- Cloud migration projects (hybrid cloud, multi-cloud strategies)\n'
          + '- Digital transformation programs involving real-time data or event-driven patterns\n'
          + '- Modernization from legacy middleware (TIBCO, IBM MQ, MuleSoft) to cloud-native\n'
          + '- IoT or edge computing initiatives that need event streaming\n'
          + '- AI/ML data pipeline projects needing real-time event feeds\n'
          + '- Microservices or API modernization programs\n'
          + '- Budget announcements or investment areas in integration/middleware\n'
          + '\nIMPORTANT: Only include information from the last 2 weeks. Skip anything older.\n'
          + '\nMarkdown with ## headers and bullets. Be specific with project names and timelines. Max 200 words.';
      }
    },
    { id: 'partners', name: 'Partner Activity',
      prompt: function (co, s) {
        return 'Research ' + co + ': PARTNER AND SYSTEMS INTEGRATOR ACTIVITY.\n'
          + 'I work at Solace and need to know which consulting firms and technology partners are active at ' + co + '.\n'
          + 'Find:\n'
          + '- Is Accenture, Deloitte, Capgemini, Wipro, TCS, Infosys, or Cognizant working with ' + co + ' on integration or digital transformation?\n'
          + '- Any recent consulting engagements, RFPs, or project awards involving integration, middleware, or event-driven architecture?\n'
          + '- Technology partnerships with Confluent, TIBCO, IBM, MuleSoft, Boomi, SAP, Informatica?\n'
          + '- Any systems integrator hiring specifically for ' + co + ' projects?\n'
          + '- Conference co-presentations or joint case studies between ' + co + ' and any consulting firm or tech vendor?\n'
          + '\nIMPORTANT: Only include news from the last 2 weeks. Skip anything older.\n'
          + '\nMarkdown with ## headers and bullets. Name the partners and the nature of the engagement. Max 200 words.';
      }
    },
    { id: 'news', name: 'Recent News',
      prompt: function (co, s) {
        var cutoff = new Date(Date.now() - 14*24*60*60*1000).toISOString().split('T')[0];
        return 'Research the latest news about ' + co + ' from the LAST 2 WEEKS ONLY (after ' + cutoff + ').\n'
          + 'I work at Solace and need conversation openers with IT architects and integration leaders at ' + co + '.\n'
          + 'Focus on:\n'
          + '- Technology announcements, platform changes, architecture decisions\n'
          + '- Industry news affecting ' + co + ' (regulations, market shifts, M&A)\n'
          + '- Hiring or restructuring in IT, engineering, or architecture teams\n'
          + '- Partnerships, vendor selections, or integration project announcements\n'
          + '- Conference appearances or thought leadership by ' + co + ' tech leaders\n'
          + '- Anything related to event-driven architecture, messaging, integration, real-time data, microservices, or cloud migration\n'
          + '\nSTRICT RULE: Every item MUST have happened in the last 2 weeks. If nothing recent, say "No news in the last 2 weeks" -- do NOT backfill with older news.\n'
          + '\nMarkdown with ## headers and bullets. Include dates and sources. Max 200 words.';
      }
    },
    { id: 'risks', name: 'Risks & Competitive',
      prompt: function (co, s) {
        return 'Research ' + co + ': RISK SIGNALS and COMPETITIVE MOVES relevant to Solace.\n'
          + 'I work at Solace and need to protect my accounts and spot competitive threats.\n'
          + 'Find:\n'
          + '- Has ' + co + ' recently adopted or evaluated Confluent/Kafka, IBM MQ, TIBCO, RabbitMQ, AWS EventBridge, Azure Service Bus, or Google Pub/Sub?\n'
          + '- Job postings mentioning competitor product names (Kafka, TIBCO, IBM MQ, Confluent)\n'
          + '- Layoffs, hiring freezes, or budget cuts in IT/engineering at ' + co + '\n'
          + '- Key departures: architects, integration leads, or engineering managers leaving\n'
          + '- Restructuring or org changes in technology teams\n'
          + '- Signals that ' + co + ' is consolidating or switching middleware/messaging vendors\n'
          + '- Any complaints or migration away from current integration platform\n'
          + '\nIMPORTANT: Only include events from the last 2 weeks. If nothing concerning found, say "No risk signals detected."\n'
          + '\nMarkdown with ## headers and bullets. Be specific about scope and impact. Max 200 words.';
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
  var savedSellerDesc = '';
  var savedCompanies = [];

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

  // ── DOM: setup email ────────────────────
  var setupEmail = document.getElementById('setup-email');

  // ── Hero form: save email, scroll to setup ──
  heroForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var email = heroEmail.value.trim();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { heroEmail.focus(); return; }
    savedEmail = email;
    setupEmail.value = email;
    var target = document.getElementById('setup');
    window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - 80, behavior: 'smooth' });
    setTimeout(function () { document.getElementById('seller-desc').focus(); }, 600);
  });

  // ── Setup form: parse & launch ──────────
  setupForm.addEventListener('submit', function (e) {
    e.preventDefault();
    clearErrors();

    var email = setupEmail.value.trim();
    var sellerDesc = document.getElementById('seller-desc').value.trim();
    var sellerUrl = email.split('@')[1];
    var companiesRaw = document.getElementById('companies').value.trim();

    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { showError('setup-email', 'Enter a valid email so we can send your weekly briefs.'); return; }
    if (!sellerDesc) { showError('seller-desc', 'Tell us what you sell so agents can tailor the brief.'); return; }
    if (!companiesRaw) { showError('companies', 'Add at least one company.'); return; }

    var companiesParsed = companiesRaw.split('\n').map(function (s) { return s.trim(); }).filter(Boolean).slice(0, 10).map(parseCompanyInput);
    if (!companiesParsed.length) { showError('companies', 'Add at least one company or website.'); return; }

    savedEmail = email;
    savedSellerDesc = sellerDesc;
    savedCompanies = companiesParsed.map(function (c) { return c.raw; });
    var sellerContext = sellerDesc + (sellerUrl ? ' (' + sellerUrl + ')' : '');

    // Subscribe immediately
    subscribeToSupabase(savedEmail, savedSellerDesc, savedCompanies).catch(function (err) {
      console.error('Subscribe error:', err);
    });

    // Loading
    btnSubmit.querySelector('.btn__text').hidden = true;
    btnSubmit.querySelector('.btn__loader').hidden = false;
    btnSubmit.disabled = true;

    // Reset
    resultsContainer.innerHTML = '';
    resultsSection.hidden = false;

    setTimeout(function () { resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 200);

    // Launch per company
    var companiesLeft = companiesParsed.length;
    companiesParsed.forEach(function (company, idx) {
      setTimeout(function () {
        runCompanyMesh(company, sellerContext, idx, function () {
          companiesLeft--;
          if (companiesLeft === 0) {
            btnSubmit.querySelector('.btn__text').hidden = false;
            btnSubmit.querySelector('.btn__loader').hidden = true;
            btnSubmit.disabled = false;
          }
        });
      }, idx * 300);
    });
  });


  // ── Run mesh for one company ────────────
  function runCompanyMesh(company, sellerContext, companyIdx, onDone) {
    var companyName = company.name;
    var companyDomain = company.domain;
    var displayName = companyDomain || companyName;

    var block = document.createElement('div');
    block.className = 'company-block';
    block.id = 'block-' + companyIdx;

    block.innerHTML =
      '<div class="company-block__loading" id="loading-' + companyIdx + '">'
      +   '<div class="loading-dots"><span></span><span></span><span></span></div>'
      +   '<p class="company-block__loading-text" id="loading-text-' + companyIdx + '">Getting the scoop on <strong>' + esc(displayName) + '</strong>...</p>'
      + '</div>'
      + '<div class="results__strategy" id="strategy-' + companyIdx + '" hidden></div>';

    resultsContainer.appendChild(block);

    var agentResults = {};
    var completed = 0;
    var loadingText = document.getElementById('loading-text-' + companyIdx);
    var sysPrm = 'You are a specialized agent in a Solace Agent Mesh, helping Solace sales colleagues stay informed about their customer accounts. ' + SOLACE_CONTEXT + ' Focus on signals useful for talking to IT Architects, Integration Leads, CTOs, and Heads of Integration. Be concise, factual, and focus on event-driven architecture, integration, messaging, and real-time data signals. Only report information from the last 2 weeks for news items. Use markdown ## headers and bullet points.';

    AGENTS.forEach(function (agent, idx) {
      setTimeout(function () {
        var prompt = agent.id === 'website'
          ? agent.prompt(companyName, sellerContext, companyDomain)
          : agent.prompt(companyName, sellerContext);
        callPerplexity(sysPrm, prompt)
          .then(function (r) { agentResults[agent.id] = r; })
          .catch(function () { agentResults[agent.id] = '(Failed)'; })
          .then(function () {
            completed++;
            loadingText.innerHTML = 'Getting the scoop on <strong>' + esc(displayName) + '</strong>... ' + completed + '/' + AGENTS.length;
            if (completed === AGENTS.length) runOrchestrator(companyName, sellerContext, companyIdx, agentResults, onDone);
          });
      }, idx * 150);
    });
  }

  // ── Orchestrator ────────────────────────
  function runOrchestrator(company, sellerContext, companyIdx, agentResults, onDone) {
    var loadingText = document.getElementById('loading-text-' + companyIdx);
    loadingText.innerHTML = 'Synthesizing signals for <strong>' + esc(company) + '</strong>...';

    var findings = '';
    AGENTS.forEach(function (a) { findings += '\n\n## ' + a.name + '\n' + (agentResults[a.id] || '(no data)'); });

    var sysPrm = 'You are a sales intelligence agent helping a Solace colleague stay informed about their customer accounts. ' + SOLACE_CONTEXT + ' Produce ultra-concise account updates focused on what matters for talking to IT Architects, Integration Leads, CTOs, and Heads of Integration. Use markdown bullet points only. No headers. No preamble. No closing remarks.';
    var usrPrm = 'Based on these agent findings about ' + company + ', write EXACTLY 3 bullet points summarizing the most actionable signals for a Solace colleague.\n\nAGENT FINDINGS:' + findings + '\n\n'
      + 'CONTEXT: The Solace colleague sells PubSub+ Event Broker, Event Portal, and Agent Mesh.\n\n'
      + 'Rules:\n'
      + '- Exactly 3 bullet points, no more, no less\n'
      + '- Each bullet is one sentence, specific (names, dates, numbers)\n'
      + '- No headers, no sections, no preamble, no closing\n'
      + '- Prioritize: event-driven architecture moves, integration platform changes, messaging/middleware decisions, champion/stakeholder updates, partner activity (Accenture, Deloitte, etc.), and competitive threats (Confluent, TIBCO, IBM MQ)\n'
      + '- Frame each signal as something you could bring up with an IT Architect, Integration Lead, or CTO at ' + company + '\n'
      + '- ONLY include facts from the last 2 weeks for news items\n'
      + '- Max 75 words total';

    callPerplexity(sysPrm, usrPrm, 200)
      .then(function (s) { agentResults['strategy'] = s; })
      .catch(function () {})
      .then(function () {
        showCompanyResults(companyIdx, company, agentResults);
        onDone();
      });
  }

  // ── Show results ────────────────────────
  function showCompanyResults(companyIdx, company, agentResults) {
    // Hide loading
    var loading = document.getElementById('loading-' + companyIdx);
    if (loading) loading.hidden = true;

    var strategy = agentResults['strategy'] || '';
    var stratEl = document.getElementById('strategy-' + companyIdx);

    var ctaHtml =
      '<div class="result-card__cta">'
      + '<div class="result-card__subscribed">'
      + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>'
      + '<span>Subscribed! Your first full brief arrives Monday.</span>'
      + '</div>'
      + '</div>';

    stratEl.innerHTML =
      '<div class="result-card result-card--scoop">'
      + '<div class="result-card__header">'
      +   '<div class="result-card__icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4"/></svg></div>'
      +   '<div class="result-card__agent">The scoop on ' + esc(company) + '</div>'
      + '</div>'
      + '<div class="result-card__body">' + (strategy ? md(strategy) : '<p>No signals found.</p>') + '</div>'
      + ctaHtml
      + '</div>';
    stratEl.hidden = false;
  }

  // ── Perplexity API ──────────────────────
  function callPerplexity(sys, usr, maxTokens) {
    return fetch(PPLX_URL, {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + PPLX_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: PPLX_MODEL, messages: [{ role: 'system', content: sys }, { role: 'user', content: usr }], max_tokens: maxTokens || 600, temperature: 0.3 })
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

  // ── Supabase subscribe ─────────────────
  function supabaseHeaders() {
    return {
      'apikey': SUPABASE_KEY,
      'Authorization': 'Bearer ' + SUPABASE_KEY,
      'Content-Type': 'application/json',
      'Prefer': 'return=representation'
    };
  }

  function subscribeToSupabase(email, product, companies) {
    var upsertHeaders = supabaseHeaders();
    upsertHeaders['Prefer'] = 'return=representation,resolution=merge-duplicates';
    return fetch(SUPABASE_URL + '/rest/v1/users?on_conflict=email', {
      method: 'POST',
      headers: upsertHeaders,
      body: JSON.stringify({ email: email, product: product })
    })
    .then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error('User upsert failed: ' + t); });
      return r.json();
    })
    .then(function (rows) {
      var user = rows[0];
      if (!companies.length) return;
      var companyRows = companies.map(function (name) {
        return { user_id: user.id, name: name };
      });
      return fetch(SUPABASE_URL + '/rest/v1/companies', {
        method: 'POST',
        headers: supabaseHeaders(),
        body: JSON.stringify(companyRows)
      })
      .then(function (r) {
        if (!r.ok) return r.text().then(function (t) { throw new Error('Companies insert failed: ' + t); });
      });
    });
  }

})();
