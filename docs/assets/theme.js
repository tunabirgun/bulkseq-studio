(() => {
  const root = document.documentElement;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  // No stored choice means follow the operating system; the toggle then stores an explicit one.
  let preference = 'system';
  const stored = value => value === 'light' || value === 'dark';
  try { const saved = localStorage.getItem('bulkseq-theme'); if (stored(saved)) preference = saved; } catch {}
  const resolved = () => (preference === 'system' ? (media.matches ? 'dark' : 'light') : preference);
  const apply = () => { root.dataset.theme = resolved(); };
  apply();
  media.addEventListener('change', () => { if (preference === 'system') apply(); });
  window.addEventListener('DOMContentLoaded', () => {
    const button = document.getElementById('theme-trigger');
    if (!button) return;
    const name = button.querySelector('.theme-name');
    const hint = button.querySelector('.visually-hidden');
    // The label names the theme now showing; the hidden half says what a click does.
    const update = () => {
      const dark = resolved() === 'dark';
      name.textContent = dark ? 'Dark theme' : 'Light theme';
      hint.textContent = dark ? ' — switch to light' : ' — switch to dark';
      button.title = dark ? 'Switch to the light theme' : 'Switch to the dark theme';
    };
    update();
    button.addEventListener('click', () => {
      preference = resolved() === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem('bulkseq-theme', preference); } catch {}
      apply(); update();
    });
    window.addEventListener('storage', event => {
      if (event.key !== 'bulkseq-theme' && event.key !== null) return;
      preference = stored(event.newValue) ? event.newValue : 'system';
      apply(); update();
    });
  });
})();
