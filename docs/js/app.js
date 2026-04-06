/* ============================================
   Scoop — Frontend Application
   ============================================ */

(function () {
  'use strict';

  // ── Configuration ──────────────────────────
  // When a backend is deployed, set this to the API URL.
  // For now, the form shows a static example digest.
  const CONFIG = {
    apiUrl: null, // e.g. 'https://api.getscoop.io'
    stripeCheckoutUrl: null, // e.g. 'https://checkout.stripe.com/...'
  };

  // ── DOM refs ───────────────────────────────
  const nav = document.getElementById('nav');
  const form = document.getElementById('signup-form');
  const btnStep1 = document.getElementById('btn-step1');
  const btnSubmit = document.getElementById('btn-submit');
  const emailInput = document.getElementById('email');
  const productInput = document.getElementById('product');
  const customersInput = document.getElementById('customers');
  const generatedDigest = document.getElementById('generated-digest');

  // ── Nav scroll behavior ────────────────────
  let lastScroll = 0;
  window.addEventListener('scroll', function () {
    const y = window.scrollY;
    nav.classList.toggle('nav--scrolled', y > 10);
    lastScroll = y;
  }, { passive: true });

  // ── Multi-step form ────────────────────────
  function showStep(n) {
    form.querySelectorAll('.form__step').forEach(function (el) {
      el.classList.remove('form__step--active');
    });
    var step = form.querySelector('[data-step="' + n + '"]');
    if (step) step.classList.add('form__step--active');
  }

  function validateEmail(email) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  }

  function showError(input, msg) {
    var existing = input.parentNode.querySelector('.form__error');
    if (existing) existing.remove();
    var el = document.createElement('div');
    el.className = 'form__error form__error--visible';
    el.textContent = msg;
    input.after(el);
  }

  function clearErrors() {
    form.querySelectorAll('.form__error').forEach(function (el) { el.remove(); });
  }

  btnStep1.addEventListener('click', function () {
    clearErrors();
    var email = emailInput.value.trim();
    if (!email || !validateEmail(email)) {
      showError(emailInput, 'Please enter a valid work email.');
      emailInput.focus();
      return;
    }
    showStep(2);
    productInput.focus();
  });

  // Allow Enter key on email to advance
  emailInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); btnStep1.click(); }
  });

  // ── Form submission ────────────────────────
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    clearErrors();

    var product = productInput.value.trim();
    var customersRaw = customersInput.value.trim();

    if (!product) {
      showError(productInput, 'Tell us what you sell so we can tailor the digest.');
      productInput.focus();
      return;
    }
    if (!customersRaw) {
      showError(customersInput, 'Add at least one company to monitor.');
      customersInput.focus();
      return;
    }

    var companies = customersRaw
      .split('\n')
      .map(function (s) { return s.trim(); })
      .filter(Boolean)
      .slice(0, 10);

    if (companies.length === 0) {
      showError(customersInput, 'Add at least one company to monitor.');
      customersInput.focus();
      return;
    }

    // Show loading state
    btnSubmit.querySelector('.btn__text').hidden = true;
    btnSubmit.querySelector('.btn__loader').hidden = false;
    btnSubmit.disabled = true;

    // If there's an API backend, call it. Otherwise show static example.
    if (CONFIG.apiUrl) {
      callApi(emailInput.value.trim(), product, companies);
    } else {
      // Simulate a brief delay for realism, then show static example
      setTimeout(function () {
        renderExampleDigest(companies, product);
        showStep(3);
      }, 1800);
    }
  });

  // ── API call (when backend is deployed) ────
  function callApi(email, product, companies) {
    fetch(CONFIG.apiUrl + '/api/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, product: product, companies: companies }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error('API error');
        return res.json();
      })
      .then(function (data) {
        renderApiDigest(data);
        showStep(3);
      })
      .catch(function () {
        // Fallback to static example on error
        renderExampleDigest(companies, product);
        showStep(3);
      });
  }

  // ── Render API digest (from backend) ───────
  function renderApiDigest(data) {
    // data.items = [{ company, tag, tag_color, headline, why }]
    var html = buildDigestHeader(data.date || todayFormatted());
    html += '<p class="digest__intro">Here are the most important signals from your accounts.</p>';
    data.items.forEach(function (item) {
      html += buildDigestItem(item.company, item.tag, item.tag_color, item.headline, item.why);
    });
    html += buildDigestFooter(data.company_count || 3);
    generatedDigest.innerHTML = html;
  }

  // ── Render static example digest ───────────
  function renderExampleDigest(companies, product) {
    var signals = getExampleSignals(companies, product);
    var html = buildDigestHeader(todayFormatted());
    html += '<p class="digest__intro">Here\'s a preview based on your first ' + signals.length + ' accounts.</p>';
    signals.forEach(function (s) {
      html += buildDigestItem(s.company, s.tag, s.color, s.headline, s.why);
    });
    html += buildDigestFooter(companies.length);
    generatedDigest.innerHTML = html;
  }

  // ── Digest HTML builders ───────────────────
  function buildDigestHeader(date) {
    return '<div class="digest__header">' +
      '<div class="digest__logo"><svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="gd" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#6366f1"/><stop offset="100%" stop-color="#4f46e5"/></linearGradient></defs><rect width="32" height="32" rx="8" fill="url(#gd)"/><path d="M16 7a9 9 0 0 1 0 18 9 9 0 0 1 0-18zm0 3a6 6 0 0 0 0 12 6 6 0 0 0 0-12zm0 3a3 3 0 1 1 0 6 3 3 0 0 1 0-6z" fill="white" opacity="0.95"/><circle cx="16" cy="16" r="1.2" fill="white"/></svg></div>' +
      '<div><div class="digest__title">Your Sample Scoop</div>' +
      '<div class="digest__date">' + date + '</div></div></div>';
  }

  function buildDigestItem(company, tag, color, headline, why) {
    return '<div class="digest__item">' +
      '<div class="digest__signal">' +
      '<span class="digest__tag digest__tag--' + color + '">' + escapeHtml(tag) + '</span>' +
      '<span class="digest__company">' + escapeHtml(company) + '</span>' +
      '</div>' +
      '<p class="digest__body">' + escapeHtml(headline) + '</p>' +
      '<div class="digest__why"><strong>Why this matters:</strong> ' + escapeHtml(why) + '</div>' +
      '</div>';
  }

  function buildDigestFooter(count) {
    return '<div class="digest__footer"><p>Tracking ' + count + ' accounts &middot; Full digest every Monday at 7am</p></div>';
  }

  // ── Example signal generator ───────────────
  // Uses the user's actual company names to make the example feel personalized.
  function getExampleSignals(companies, product) {
    var templates = [
      {
        tag: 'Executive Change',
        color: 'red',
        headline: function (c) { return 'New VP of Engineering appointed at ' + c + ', signaling a strategic shift in technical direction.'; },
        why: function (c, p) { return 'Leadership changes often reset vendor evaluations. Reach out early to introduce ' + p + ' before the new VP sets their stack preferences.'; },
      },
      {
        tag: 'Funding',
        color: 'green',
        headline: function (c) { return c + ' closed a significant growth round, with plans to expand their enterprise team.'; },
        why: function (c, p) { return 'New capital means new budget. ' + c + ' is likely evaluating tools like ' + p + ' \u2014 this is the ideal time to propose a pilot.'; },
      },
      {
        tag: 'Strategic',
        color: 'blue',
        headline: function (c) { return c + ' announced a major platform migration initiative for the next two quarters.'; },
        why: function (c, p) { return 'Platform migrations create procurement windows. Position ' + p + ' as part of their modernization roadmap before RFPs go out.'; },
      },
      {
        tag: 'Competitive',
        color: 'amber',
        headline: function (c) { return c + ' was spotted evaluating a competitor product in a recent industry benchmark report.'; },
        why: function (c, p) { return 'Your deal may be at risk. Schedule a value review with your champion at ' + c + ' to reinforce why ' + p + ' is the right choice.'; },
      },
      {
        tag: 'Expansion',
        color: 'green',
        headline: function (c) { return c + ' opened new offices in three markets and is hiring aggressively across sales and ops.'; },
        why: function (c, p) { return 'Rapid expansion means scaling pain. ' + c + ' will need infrastructure that grows with them \u2014 lead with your scaling story.'; },
      },
    ];

    // Use up to 3 of the user's companies
    var count = Math.min(companies.length, 3);
    var signals = [];
    for (var i = 0; i < count; i++) {
      var t = templates[i % templates.length];
      signals.push({
        company: companies[i],
        tag: t.tag,
        color: t.color,
        headline: t.headline(companies[i]),
        why: t.why(companies[i], product),
      });
    }
    return signals;
  }

  // ── Utilities ──────────────────────────────
  function todayFormatted() {
    var d = new Date();
    var months = ['January', 'February', 'March', 'April', 'May', 'June',
      'July', 'August', 'September', 'October', 'November', 'December'];
    return 'Preview \u2014 ' + months[d.getMonth()] + ' ' + d.getDate() + ', ' + d.getFullYear();
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  // ── Smooth scroll for anchor links ─────────
  document.querySelectorAll('a[href^="#"]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      var target = document.querySelector(this.getAttribute('href'));
      if (target) {
        e.preventDefault();
        var offset = 80; // nav height
        var top = target.getBoundingClientRect().top + window.scrollY - offset;
        window.scrollTo({ top: top, behavior: 'smooth' });
      }
    });
  });

})();
