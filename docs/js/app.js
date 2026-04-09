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

  var SUPABASE_URL = 'https://REDACTED_SUPABASE_URL';
  var SUPABASE_KEY = 'REDACTED_SUPABASE_KEY';

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

  // ── Agents ──────────────────────────────
  var AGENTS = [
    { id: 'website', name: 'Website Intel',
      prompt: function (co, s, domain) {
        var target = domain ? 'the website ' + domain + ' (company: ' + co + ')' : co;
        return 'Research ' + target + ' thoroughly.\n'
          + '## What to find:\n'
          + '- What does this company DO? What products/services do they offer?\n'
          + '- What INDUSTRY are they in? (IT, finance, healthcare, manufacturing, retail, etc.)\n'
          + '- What TECHNOLOGY STACK or platforms do they use or sell?\n'
          + '- What are their main USE CASES and who are THEIR customers?\n'
          + '- What technical challenges or initiatives are they likely working on? (cloud migration, API modernization, real-time data, microservices, etc.)\n'
          + '- Company size, headquarters, and key markets\n'
          + (domain ? '\nLook at ' + domain + ' directly for product pages, blog posts, case studies, and technical documentation to understand their technology focus.\n' : '')
          + (s ? '\nThe seller sells: ' + s + '. Based on what you learn about ' + co + ', identify which parts of their business would benefit from this product and why.\n' : '')
          + '\nMarkdown with ## headers and bullets. Be specific. Max 250 words.';
      }
    },
    { id: 'people', name: 'People Intel',
      prompt: function (co, s) {
        return 'Research ' + co + ': KEY CONTACTS for a mid-level sales engagement.\n'
          + 'I am NOT selling to the CEO or board. I need the people who actually evaluate and buy technology:\n'
          + '- Directors, VPs, and Heads of IT, Engineering, Architecture, Integration, Data, or Platform teams\n'
          + '- Technical decision-makers: Enterprise Architects, Solution Architects, Integration Leads\n'
          + '- Recent hires or role changes in these mid-level leadership positions (last 6 months)\n'
          + '- Who owns the budget for technology/infrastructure purchases at ' + co + '?\n'
          + '- LinkedIn profiles or professional background if available\n'
          + (s ? '\nThe seller sells: ' + s + '. Identify the specific people at ' + co + ' who would evaluate and champion this kind of product. Focus on technical leaders, not executives.\n' : '')
          + '\nMarkdown with ## headers and bullets. Be specific with names, titles, and dates. Max 200 words.';
      }
    },
    { id: 'techstack', name: 'Tech & Architecture',
      prompt: function (co, s) {
        return 'Research ' + co + ': TECHNOLOGY & ARCHITECTURE signals.\n'
          + '- What technology platforms, middleware, cloud providers, or integration tools does ' + co + ' use?\n'
          + '- Any recent blog posts, conference talks, or job postings that reveal their tech stack?\n'
          + '- Are they going through a cloud migration, modernization, or digital transformation?\n'
          + '- What integration challenges do they likely face given their industry and size?\n'
          + '- Any public architecture decisions, open-source contributions, or tech blog posts?\n'
          + '- Job postings that mention specific technologies (Kafka, MQ, API gateways, microservices, event-driven, etc.)\n'
          + (s ? '\nThe seller sells: ' + s + '. Identify technology signals at ' + co + ' that suggest they need this product or are evaluating alternatives. Look for gaps in their stack.\n' : '')
          + '\nMarkdown with ## headers and bullets. Max 200 words.';
      }
    },
    { id: 'initiatives', name: 'IT Initiatives',
      prompt: function (co, s) {
        return 'Research ' + co + ': CURRENT IT & BUSINESS INITIATIVES.\n'
          + '- Digital transformation programs, cloud migration projects, modernization efforts\n'
          + '- New product launches or platform changes that require integration work\n'
          + '- Partnerships, vendor selections, or RFPs related to IT infrastructure\n'
          + '- Conference presentations, webinars, or case studies by ' + co + ' employees about ongoing projects\n'
          + '- Budget announcements or investment areas ("investing in real-time", "modernizing our architecture", etc.)\n'
          + '- Any press releases about technology partnerships or platform changes\n'
          + (s ? '\nThe seller sells: ' + s + '. Focus on initiatives at ' + co + ' where this product would be relevant. What projects are they working on that need this?\n' : '')
          + '\nMarkdown with ## headers and bullets. Be specific with project names, timelines, and scope. Max 200 words.';
      }
    },
    { id: 'hiring', name: 'Hiring Signals',
      prompt: function (co, s) {
        return 'Research ' + co + ': HIRING PATTERNS that signal technology investments.\n'
          + '- Volume of open roles in IT, engineering, architecture, integration, data, platform, DevOps\n'
          + '- Clusters of similar roles (e.g., "8 data engineers in 2 weeks" = building something)\n'
          + '- New team formation (first-ever "Head of Platform" = new initiative)\n'
          + '- Job descriptions mentioning specific technologies, tools, or migration keywords\n'
          + '- Contractor/consultant postings (often precede major projects)\n'
          + '- Hiring freezes or sudden posting removals (risk signal)\n'
          + (s ? '\nThe seller sells: ' + s + '. Focus on hiring patterns at ' + co + ' that suggest they are investing in areas where this product fits. Keywords in job descriptions are gold.\n' : '')
          + '\nMarkdown with ## headers and bullets. Patterns matter more than individual postings. Max 200 words.';
      }
    },
    { id: 'news', name: 'Industry News',
      prompt: function (co, s) {
        return 'Research the latest news about ' + co + ' from the past 30 days, with a focus on IT, technology, and industry developments.\n'
          + '- Technology announcements, platform updates, product launches\n'
          + '- Industry news that affects ' + co + ' (regulations, market shifts, competitor moves)\n'
          + '- Conference appearances, keynotes, or thought leadership by ' + co + ' employees\n'
          + '- Partnerships, integrations, or ecosystem announcements\n'
          + '- Anything a mid-level salesperson could reference in a conversation with a Director or VP of IT/Engineering at ' + co + '\n'
          + (s ? '\nThe seller sells: ' + s + '. Focus on news that creates a natural, relevant conversation opener when talking to a technical leader at ' + co + '. Skip generic corporate announcements unless they reveal technology direction.\n' : '')
          + '\nMarkdown with ## headers and bullets. Be specific with dates and sources. Max 200 words.';
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

    var companiesParsed = companiesRaw.split('\n').map(function (s) { return s.trim(); }).filter(Boolean).slice(0, 10).map(parseCompanyInput);
    if (!companiesParsed.length) { showError('companies', 'Add at least one company or website.'); return; }

    savedSellerDesc = sellerDesc;
    savedCompanies = companiesParsed.map(function (c) { return c.raw; });
    var sellerContext = sellerDesc + (sellerUrl ? ' (' + sellerUrl + ')' : '');

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

  // ── Subscribe handler (event delegation) ─
  resultsContainer.addEventListener('click', function (e) {
    var btn = e.target.closest('.scoop-subscribe-btn');
    if (!btn || btn.disabled) return;

    btn.disabled = true;
    btn.textContent = 'Subscribing...';

    subscribeToSupabase(savedEmail, savedSellerDesc, savedCompanies)
      .then(function () {
        // Update ALL subscribe CTAs across all cards
        resultsContainer.querySelectorAll('.result-card__cta').forEach(function (cta) {
          cta.innerHTML =
            '<div class="result-card__subscribed">'
            + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>'
            + '<span>Subscribed! Your first full brief arrives Monday.</span>'
            + '</div>';
        });
      })
      .catch(function (err) {
        console.error('Subscribe error:', err);
        btn.disabled = false;
        btn.textContent = 'Subscribe for weekly briefs';
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
    var sysPrm = 'You are a specialized agent in a Solace Agent Mesh, helping mid-level B2B salespeople stay informed about their accounts. Focus on signals useful for talking to Directors, VPs, and technical leaders -- not the CEO or board. Be concise, factual, and focus on technology, architecture, and operational signals. Use markdown ## headers and bullet points.';

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

    var sysPrm = 'You are a sales intelligence agent helping a mid-level B2B salesperson. Produce ultra-concise account updates focused on what matters for talking to Directors, VPs, and technical leaders. Use markdown bullet points only. No headers. No preamble. No closing remarks.';
    var usrPrm = 'Based on these agent findings about ' + company + ', write EXACTLY 3 bullet points summarizing the most actionable signals for a salesperson who talks to technical leaders and mid-level decision-makers (NOT the CEO/CTO).\n\nAGENT FINDINGS:' + findings + '\n\n'
      + (sellerContext ? 'THE SALESPERSON SELLS: ' + sellerContext + '\n\n' : '')
      + 'Rules:\n'
      + '- Exactly 3 bullet points, no more, no less\n'
      + '- Each bullet is one sentence, specific (names, dates, numbers)\n'
      + '- No headers, no sections, no preamble, no closing\n'
      + '- Prioritize: technology initiatives, architecture changes, hiring patterns, integration projects, IT modernization signals\n'
      + '- Frame each signal as something you could bring up in a conversation with a Director of IT, VP of Engineering, or Enterprise Architect\n'
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

    var ctaHtml = '';
    if (savedEmail) {
      ctaHtml =
        '<div class="result-card__cta">'
        + '<p class="result-card__cta-text">This is just a preview. Subscribe to get the full weekly brief with conversation openers, buying signals, risk alerts, and next steps for <strong>' + esc(company) + '</strong>.</p>'
        + '<button class="btn btn--lg scoop-subscribe-btn">Subscribe for weekly briefs</button>'
        + '</div>';
    }

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
