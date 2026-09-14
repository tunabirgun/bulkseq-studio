(() => {
  const root = document.documentElement;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const choices = ['light', 'dark', 'system'];
  let preference = 'system';
  try { const saved = localStorage.getItem('bulkseq-theme'); if (choices.includes(saved)) preference = saved; } catch {}
  const apply = () => { root.dataset.theme = preference === 'system' ? (media.matches ? 'dark' : 'light') : preference; };
  apply();
  media.addEventListener('change', () => { if (preference === 'system') apply(); });
  window.addEventListener('DOMContentLoaded', () => {
    const trigger = document.getElementById('theme-trigger');
    const panel = document.getElementById('theme-options');
    const buttons = [...panel.querySelectorAll('[data-theme-choice]')];
    const update = () => buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.themeChoice === preference)));
    const close = (restore = false) => { panel.hidden = true; trigger.setAttribute('aria-expanded', 'false'); if (restore) trigger.focus(); };
    update();
    trigger.addEventListener('click', () => {
      if (!panel.hidden) { close(); return; }
      panel.hidden = false; trigger.setAttribute('aria-expanded', 'true');
      buttons.find(button => button.dataset.themeChoice === preference).focus();
    });
    buttons.forEach(button => button.addEventListener('click', () => {
      preference = button.dataset.themeChoice;
      try { localStorage.setItem('bulkseq-theme', preference); } catch {}
      apply(); update(); close(true);
    }));
    panel.addEventListener('keydown', event => {
      const index = buttons.indexOf(document.activeElement);
      if (['ArrowDown', 'ArrowRight', 'ArrowUp', 'ArrowLeft', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : -1) + buttons.length) % buttons.length;
        buttons[next].focus();
      }
    });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) { event.preventDefault(); close(true); } });
    document.addEventListener('pointerdown', event => { if (!panel.hidden && !event.target.closest('.theme-control')) close(); });
    document.addEventListener('focusin', event => { if (!panel.hidden && !event.target.closest('.theme-control')) close(); });
    window.addEventListener('storage', event => { if (event.key === 'bulkseq-theme' || event.key === null) { preference = choices.includes(event.newValue) ? event.newValue : 'system'; apply(); update(); } });
  });
})();
