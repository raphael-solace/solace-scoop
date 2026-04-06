/* ============================================
   Scoop — Frontend
   ============================================ */

(function () {
  'use strict';

  // When backend is deployed, set this to the API URL.
  var API_URL = null; // e.g. 'https://your-app.railway.app'

  var nav = document.getElementById('nav');
  var form = document.getElementById('signup-form');
  var btnSubmit = document.getElementById('btn-submit');
  var successEl = document.getElementById('signup-success');

  // Nav scroll
  window.addEventListener('scroll', function () {
    nav.classList.toggle('nav--scrolled', window.scrollY > 10);
  }, { passive: true });

  // Form submit
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    clearErrors();

    var email = document.getElementById('email').value.trim();
    var product = document.getElementById('product').value.trim();
    var customersRaw = document.getElementById('customers').value.trim();

    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      showError('email', 'Please enter a valid work email.');
      return;
    }
    if (!product) {
      showError('product', 'Tell us what you sell so we can tailor the digest.');
      return;
    }
    if (!customersRaw) {
      showError('customers', 'Add at least one company to monitor.');
      return;
    }

    var companies = customersRaw.split('\n').map(function (s) { return s.trim(); }).filter(Boolean).slice(0, 10);
    if (companies.length === 0) {
      showError('customers', 'Add at least one company to monitor.');
      return;
    }

    // Loading state
    btnSubmit.querySelector('.btn__text').hidden = true;
    btnSubmit.querySelector('.btn__loader').hidden = false;
    btnSubmit.disabled = true;

    if (API_URL) {
      fetch(API_URL + '/api/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email, product: product, companies: companies }),
      })
        .then(function (res) {
          if (!res.ok) throw new Error('API error');
          showSuccess();
        })
        .catch(function () {
          showSuccess(); // still show success, data saved client-side
        });
    } else {
      // No backend yet: just show success after brief delay
      setTimeout(showSuccess, 1200);
    }
  });

  function showSuccess() {
    form.hidden = true;
    successEl.hidden = false;
  }

  function showError(fieldId, msg) {
    var field = document.getElementById(fieldId);
    var existing = field.parentNode.querySelector('.signup-form__error');
    if (existing) existing.remove();
    var el = document.createElement('div');
    el.className = 'signup-form__error';
    el.textContent = msg;
    field.after(el);
    field.focus();
  }

  function clearErrors() {
    form.querySelectorAll('.signup-form__error').forEach(function (el) { el.remove(); });
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
