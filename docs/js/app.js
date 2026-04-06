/* ============================================
   Scoop — Frontend
   ============================================ */

(function () {
  'use strict';

  var API_URL = null; // Set to backend URL when deployed

  var nav = document.getElementById('nav');
  var heroForm = document.getElementById('hero-form');
  var heroEmail = document.getElementById('hero-email');
  var signupForm = document.getElementById('signup-form');
  var btnSubmit = document.getElementById('btn-submit');
  var successEl = document.getElementById('signup-success');

  var savedEmail = '';

  // Nav scroll
  window.addEventListener('scroll', function () {
    nav.classList.toggle('nav--scrolled', window.scrollY > 10);
  }, { passive: true });

  // Hero: email + button -> scroll to setup
  heroForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var email = heroEmail.value.trim();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      heroEmail.focus();
      return;
    }
    savedEmail = email;
    var target = document.getElementById('setup');
    window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - 80, behavior: 'smooth' });
    setTimeout(function () { document.getElementById('product').focus(); }, 600);
  });

  // Setup form: product + accounts -> subscribe
  signupForm.addEventListener('submit', function (e) {
    e.preventDefault();
    clearErrors();

    var product = document.getElementById('product').value.trim();
    var customersRaw = document.getElementById('customers').value.trim();

    if (!product) { showError('product', 'Tell us what you sell so we can tailor the digest.'); return; }
    if (!customersRaw) { showError('customers', 'Add at least one company to monitor.'); return; }

    var companies = customersRaw.split('\n').map(function (s) { return s.trim(); }).filter(Boolean).slice(0, 10);
    if (!companies.length) { showError('customers', 'Add at least one company to monitor.'); return; }

    // Loading
    btnSubmit.querySelector('.btn__text').hidden = true;
    btnSubmit.querySelector('.btn__loader').hidden = false;
    btnSubmit.disabled = true;

    if (API_URL) {
      fetch(API_URL + '/api/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: savedEmail, product: product, companies: companies }),
      })
        .then(function (res) { if (!res.ok) throw new Error(); showSuccess(); })
        .catch(showSuccess);
    } else {
      setTimeout(showSuccess, 1200);
    }
  });

  function showSuccess() {
    signupForm.hidden = true;
    successEl.hidden = false;
  }

  function showError(fieldId, msg) {
    var field = document.getElementById(fieldId);
    var el = document.createElement('div');
    el.className = 'signup-form__error';
    el.textContent = msg;
    field.after(el);
    field.focus();
  }

  function clearErrors() {
    signupForm.querySelectorAll('.signup-form__error').forEach(function (el) { el.remove(); });
  }

  // Smooth scroll
  document.querySelectorAll('a[href^="#"]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      var target = document.querySelector(this.getAttribute('href'));
      if (target) {
        e.preventDefault();
        window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - 80, behavior: 'smooth' });
      }
    });
  });

})();
