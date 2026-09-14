document.documentElement.classList.add('js');
const $ = selector => document.querySelector(selector);
const searchDialog = $('#search-dialog'), menuDialog = $('#menu-dialog'), imageDialog = $('#image-dialog');
let opener;
function openDialog(dialog, trigger) { opener = trigger || document.activeElement; dialog.showModal(); }
document.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
document.querySelectorAll('dialog').forEach(dialog => {
  dialog.addEventListener('close', () => opener?.focus());
  dialog.addEventListener('click', event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } });
});
document.querySelectorAll('[data-open-search]').forEach(button => button.addEventListener('click', () => { openDialog(searchDialog, button); $('#search-input').focus(); }));
$('[data-open-menu]').addEventListener('click', event => openDialog(menuDialog, event.currentTarget));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') { const dialog = document.querySelector('dialog[open]'); if (dialog) { event.preventDefault(); dialog.close(); } return; }
  const editing = /INPUT|TEXTAREA|SELECT/.test(event.target.tagName) || event.target.isContentEditable;
  if ((event.key.toLowerCase() === 'k' && (event.ctrlKey || event.metaKey)) || (event.key === '/' && !editing)) {
    if (document.querySelector('dialog[open]')) return;
    event.preventDefault(); openDialog(searchDialog); $('#search-input').focus();
  }
});
$('#search-input').addEventListener('input', event => {
  const query = event.target.value.trim().toLowerCase(), words = query.split(/\s+/), list = $('#search-results'); list.replaceChildren();
  if (!query) { $('#search-status').textContent = 'Type to search the complete manual.'; return; }
  const results = (window.manualSearch || []).filter(item => words.every(word => `${item.title} ${item.text}`.toLowerCase().includes(word))).sort((a,b) => Number(b.title.toLowerCase().includes(query)) - Number(a.title.toLowerCase().includes(query)));
  $('#search-status').textContent = results.length ? `${results.length} matching chapter${results.length === 1 ? '' : 's'}.` : 'No matching chapters. Try a broader term, such as counts, design or plots.';
  for (const item of results) {
    const li = document.createElement('li'), link = document.createElement('a'), summary = document.createElement('p');
    link.href = item.url; link.textContent = item.title;
    const position = item.text.toLowerCase().indexOf(words[0]);
    summary.textContent = position >= 0 ? `${position > 60 ? '…' : ''}${item.text.slice(Math.max(0,position-60),position+170)}…` : item.summary;
    li.append(link, summary); list.append(li);
  }
});
const tooltip = $('#term-tooltip'); let activeTerm, hideTimer;
function hideTerm() { tooltip.hidden = true; if (activeTerm) { activeTerm.removeAttribute('aria-describedby'); activeTerm.dataset.open = ''; } activeTerm = null; }
function showTerm(term) {
  clearTimeout(hideTimer); activeTerm?.removeAttribute('aria-describedby'); activeTerm = term;
  const key = term.dataset.term, definition = window.manualGlossary?.[key] || Object.entries(window.manualGlossary || {}).find(([name]) => name.toLowerCase() === key.toLowerCase())?.[1];
  if (!definition) return;
  tooltip.textContent = typeof definition === 'string' ? definition : definition.definition || definition.description || '';
  tooltip.hidden = false; term.setAttribute('aria-describedby', 'term-tooltip');
  const rect = term.getBoundingClientRect(), box = tooltip.getBoundingClientRect();
  tooltip.style.left = `${Math.max(12,Math.min(rect.left,innerWidth-box.width-12))}px`;
  tooltip.style.top = `${Math.max(12,rect.bottom + box.height + 12 < innerHeight ? rect.bottom+7 : rect.top-box.height-7)}px`;
}
document.querySelectorAll('[data-term]').forEach(term => {
  term.addEventListener('mouseenter', () => showTerm(term)); term.addEventListener('focus', () => showTerm(term));
  term.addEventListener('mouseleave', () => { hideTimer = setTimeout(hideTerm,180); });
  term.addEventListener('blur', hideTerm);
  term.addEventListener('click', event => { event.preventDefault(); if (activeTerm === term && !tooltip.hidden && term.dataset.open === 'yes') { hideTerm(); term.dataset.open = ''; } else { showTerm(term); term.dataset.open = 'yes'; } });
});
tooltip.addEventListener('mouseenter', () => clearTimeout(hideTimer)); tooltip.addEventListener('mouseleave', hideTerm);
document.addEventListener('keydown', event => { if (event.key === 'Escape') hideTerm(); });
document.addEventListener('pointerdown', event => { if (!event.target.closest('[data-term],#term-tooltip')) hideTerm(); });
window.addEventListener('resize', hideTerm); window.addEventListener('scroll', () => { if (activeTerm && document.activeElement === activeTerm) showTerm(activeTerm); else hideTerm(); }, {passive:true});
async function copy(text, button) {
  try { await navigator.clipboard.writeText(text); $('#copy-status').textContent = 'Copied to clipboard.'; if (button) { button.textContent = 'Copied'; setTimeout(() => button.textContent = 'Copy code',1600); } }
  catch { $('#copy-status').textContent = 'Clipboard unavailable. Select and copy the text manually.'; }
}
document.querySelectorAll('pre').forEach(pre => { const text = pre.textContent, button = document.createElement('button'); button.className = 'copy-code'; button.textContent = 'Copy code'; button.addEventListener('click', () => copy(text,button)); pre.prepend(button); });
document.querySelectorAll('.heading-link').forEach(link => link.addEventListener('click', () => copy(link.href)));
let zoom = 1, baseWidth = 600;
function applyZoom() { const visual = $('.image-stage').firstElementChild; if (visual) visual.style.width = `${baseWidth*zoom}px`; $('#zoom-level').textContent = `${Math.round(zoom*100)}%`; }
document.querySelectorAll('article figure').forEach(figure => {
  const visual = figure.querySelector('img,.image-slot,.image-placeholder,[data-image],.image-open'); if (!visual) return;
  const button = visual.matches('button') ? visual : document.createElement('button');
  let imageTrigger;
  button.classList.add('viewer-open');
  if (button !== visual) { button.textContent = 'Open figure viewer'; figure.append(button); }
  if (visual.tagName === 'IMG') {
    visual.tabIndex = 0; visual.setAttribute('role','button'); visual.setAttribute('aria-label',`Open figure viewer: ${visual.alt || 'documentation figure'}`);
    visual.addEventListener('click',() => {imageTrigger = visual; button.click();}); visual.addEventListener('keydown',event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault();imageTrigger = visual;button.click();}});
  }
  button.addEventListener('click', () => {
    const stage = $('.image-stage'); stage.replaceChildren(); let copy;
    const src = visual.dataset.src || figure.dataset.src;
    if (src) { copy = document.createElement('img'); copy.src = src; copy.alt = visual.getAttribute('aria-label') || figure.querySelector('figcaption')?.textContent || 'Documentation figure'; } else { copy = visual.cloneNode(true); copy.removeAttribute('id'); if (copy.tagName === 'BUTTON') { const div = document.createElement('div'); div.className = 'image-placeholder'; div.innerHTML = copy.innerHTML; copy = div; } }
    if (copy.tagName === 'IMG') { copy.removeAttribute('role'); copy.removeAttribute('tabindex'); copy.removeAttribute('aria-label'); copy.addEventListener('error', () => { stage.textContent = 'This figure could not be loaded. Check the image path and rebuild the manual.'; }); }
    $('#image-title').textContent = visual.dataset.imageTitle || figure.dataset.imageTitle || 'Figure viewer';
    stage.append(copy); $('.viewer-caption').textContent = figure.querySelector('figcaption')?.textContent || 'Reserved for a future documentation screenshot.';
    openDialog(imageDialog, imageTrigger || button); imageTrigger = null; baseWidth = Math.max(180,stage.clientWidth-42); zoom = 1; applyZoom(); stage.scrollTo(0,0);
  });
});
document.querySelectorAll('[data-zoom]').forEach(button => button.addEventListener('click', () => { zoom = button.dataset.zoom === 'reset' ? 1 : Math.max(.5,Math.min(4,zoom + (button.dataset.zoom === 'in' ? .25 : -.25))); applyZoom(); }));
const observer = new IntersectionObserver(entries => { for (const entry of entries) if (entry.isIntersecting) { document.querySelectorAll('.toc-rail a[aria-current]').forEach(a => a.removeAttribute('aria-current')); const link = document.querySelector(`.toc-rail a[href="#${CSS.escape(entry.target.id)}"]`); link?.setAttribute('aria-current','location'); } }, {rootMargin:'-100px 0px -65% 0px'});
document.querySelectorAll('article h2[id],article h3[id]').forEach(heading => observer.observe(heading));
window.addEventListener('scroll', () => { if (scrollY < 100) document.querySelectorAll('.toc-rail a[aria-current]').forEach(link => link.removeAttribute('aria-current')); }, {passive:true});
document.querySelectorAll('.threshold-example').forEach(container => {
  container.innerHTML = '<h3>Try two selection thresholds</h3><p>Illustrative values — not analysis results. A row is selected only when its adjusted p-value is at or below the limit and its absolute log2 fold change is at or above the minimum.</p><div class="example-controls"><label>Adjusted p-value limit <input type="number" min="0" max="1" step="0.01" value="0.05" data-p></label><label>Minimum absolute log2 fold change <input type="number" min="0" max="10" step="0.25" value="1" data-fc></label></div><div class="table-wrap" tabindex="0" role="region" aria-label="Illustrative threshold selections"><table><thead><tr><th>Example row</th><th>Adjusted p-value</th><th>log2 fold change</th><th>Selection</th></tr></thead><tbody></tbody></table></div><p class="example-status" role="status"></p>';
  const rows = [{name:'A',p:.01,fc:2},{name:'B',p:.03,fc:.5},{name:'C',p:.15,fc:-1.5},{name:'D',p:.001,fc:-1}];
  const render = () => { const pInput = container.querySelector('[data-p]'), fcInput = container.querySelector('[data-fc]'), status = container.querySelector('.example-status'); if (!pInput.checkValidity() || !fcInput.checkValidity() || pInput.value === '' || fcInput.value === '') { status.textContent = 'Enter a p-value limit from 0 to 1 and a fold-change minimum from 0 to 10.'; return; } const p = Number(pInput.value), fc = Number(fcInput.value); let count = 0; container.querySelector('tbody').innerHTML = rows.map(row => {const selected = row.p <= p && Math.abs(row.fc) >= fc; count += Number(selected); return `<tr><td>${row.name}</td><td>${row.p}</td><td>${row.fc}</td><td>${selected ? 'Selected' : 'Not selected'}</td></tr>`;}).join(''); status.textContent = `${count} of ${rows.length} illustrative rows selected. Changing these limits changes selection, not the underlying estimates.`; };
  container.addEventListener('input', render); render();
});
document.querySelectorAll('.walkthrough').forEach(container => {
  const routes = window.walkthroughRoutes || []; if (!routes.length) return;
  container.innerHTML = '<div class="route-picker"><label for="walkthrough-route">What are you starting with?</label><select id="walkthrough-route"></select><p class="route-summary"></p><p class="route-skipped"></p></div><div class="walkthrough-layout"><nav aria-label="Analysis steps"><p class="flow-hint">Scroll steps horizontally, or use Next step below →</p><ol class="flow-steps"></ol></nav><section class="step-detail" aria-labelledby="step-title" tabindex="-1"><p class="step-progress" role="status"></p><h2 id="step-title"></h2><div class="step-body"></div><div class="step-actions"><button data-step="previous">Previous step</button><button data-step="next">Next step</button></div></section></div>';
  const select = container.querySelector('select'), list = container.querySelector('.flow-steps'); let route = routes[0], current = 0;
  for (const item of routes) { const option = document.createElement('option'); option.value = item.id; option.textContent = item.label; select.append(option); }
  function renderStep(focus = false) {
    const step = route.steps[current];
    container.querySelector('.step-progress').textContent = `Step ${current+1} of ${route.steps.length}`;
    container.querySelector('#step-title').textContent = step.title;
    const body = container.querySelector('.step-body'); body.replaceChildren();
    for (const [key,label] of Object.entries({purpose:'Purpose',inputs:'What you need',settings:'Key settings',result:'Expected result',check:'Before continuing'})) {
      if (!step[key]) continue; const section = document.createElement('div'), heading = document.createElement('h3'), paragraph = document.createElement('p');
      heading.textContent = label; paragraph.textContent = Array.isArray(step[key]) ? step[key].join(' ') : step[key]; section.append(heading,paragraph); body.append(section);
    }
    if (step.href) { const link = document.createElement('a'); link.href = step.href; link.textContent = 'Read the full chapter →'; body.append(link); }
    list.querySelectorAll('button').forEach((button,index) => { if(index === current) button.setAttribute('aria-current','step'); else button.removeAttribute('aria-current'); });
    if (list.scrollWidth > list.clientWidth) list.scrollTo({left:list.children[current].getBoundingClientRect().left-list.getBoundingClientRect().left+list.scrollLeft,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    container.querySelector('[data-step="previous"]').disabled = current === 0;
    container.querySelector('[data-step="next"]').disabled = current === route.steps.length-1;
    if (focus) {
      const detail = container.querySelector('.step-detail'); detail.focus({preventScroll:true});
      if (innerWidth <= 600) detail.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    }
  }
  function renderRoute() {
    container.querySelector('.route-summary').textContent = route.summary;
    container.querySelector('.route-skipped').textContent = route.skipped ? `Route boundaries: ${route.skipped}` : '';
    list.replaceChildren();
    route.steps.forEach((step,index) => { const li = document.createElement('li'), button = document.createElement('button'), number = document.createElement('span'), title = document.createElement('span'); number.className = 'step-number'; number.textContent = index+1; title.textContent = step.title; button.append(number,title); button.addEventListener('click',() => {current = index; renderStep(true);}); li.append(button); list.append(li); });
    renderStep();
  }
  select.addEventListener('change',() => {route = routes.find(item => item.id === select.value); current = 0; renderRoute();});
  container.querySelectorAll('[data-step]').forEach(button => button.addEventListener('click',() => { current += button.dataset.step === 'next' ? 1 : -1; renderStep(true); }));
  renderRoute();
});
