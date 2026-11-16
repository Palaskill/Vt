/* Motion enhances the interface only; all operational controls remain native. */
(() => {
  'use strict';

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
  let lenis = null;
  let modalOpen = false;
  let motionAllowed = !reduced.matches;
  let mediaContext = null;

  function configureScroll() {
    if (lenis) {
      lenis.destroy();
      lenis = null;
    }
    // Native touch scrolling is retained on phones and tablets.
    if (!reduced.matches && finePointer.matches && window.Lenis) {
      lenis = new window.Lenis({ autoRaf: true, smoothWheel: true, syncTouch: false, duration: 0.8 });
      if (modalOpen) lenis.stop();
    }
  }

  function preventNestedScroll() {
    document.querySelectorAll('.nav, .modal-body, .table-wrap, .import-preview')
      .forEach(element => element.setAttribute('data-lenis-prevent', ''));
  }

  function refresh() {
    preventNestedScroll();
    try { window.VetSpotlight?.refresh(); } catch (_) { /* Decorative only. */ }
    if (!motionAllowed || !window.gsap) return;
    const targets = document.querySelectorAll(
      '.auth-art .brand, .auth-art h1, .auth-art p, .auth-content h2, .auth-content form, .page .hero, .page .metric, .page .panel, .page .toolbar'
    );
    if (!targets.length) return;
    window.gsap.killTweensOf(targets);
    window.gsap.from(targets, {
      y: 12, autoAlpha: 0, duration: 0.38, ease: 'power2.out',
      stagger: { each: 0.035, amount: 0.22 }, clearProps: 'opacity,visibility,transform',
      overwrite: 'auto'
    });
  }

  function setModalOpen(open) {
    modalOpen = open;
    preventNestedScroll();
    if (lenis) open ? lenis.stop() : lenis.start();
    if (open && motionAllowed && window.gsap) {
      const dialog = document.querySelector('.modal');
      if (dialog) window.gsap.from(dialog, {
        y: 10, autoAlpha: 0, duration: 0.22, ease: 'power2.out',
        clearProps: 'opacity,visibility,transform', overwrite: 'auto'
      });
    }
  }

  function initialise() {
    if (window.gsap && window.gsap.matchMedia) {
      mediaContext = window.gsap.matchMedia();
      mediaContext.add('(prefers-reduced-motion: no-preference)', () => {
        motionAllowed = true;
        return () => { motionAllowed = false; };
      });
    }
    configureScroll();
  }

  function onPreferencesChange() {
    motionAllowed = !reduced.matches;
    if (!motionAllowed && window.gsap) {
      const animated = document.querySelectorAll('.auth-art .brand, .auth-art h1, .auth-art p, .auth-content h2, .auth-content form, .page .hero, .page .metric, .page .panel, .page .toolbar, .modal');
      window.gsap.killTweensOf(animated);
      window.gsap.set(animated, { clearProps: 'opacity,visibility,transform' });
    }
    configureScroll();
  }
  reduced.addEventListener?.('change', onPreferencesChange);
  finePointer.addEventListener?.('change', onPreferencesChange);
  window.addEventListener('pagehide', () => {
    if (lenis) lenis.destroy();
    lenis = null;
    mediaContext?.revert();
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) initialise();
  });

  window.VetDesign = { refresh, setModalOpen };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialise, { once: true });
  else initialise();
})();
