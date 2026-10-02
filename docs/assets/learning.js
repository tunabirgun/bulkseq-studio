(() => {
  const root=document.querySelector('[data-learning-data]');
  if(!root) return;
  const data=JSON.parse(root.dataset.learningData),core=LearningCore,lessons=core.lessons;
  const picker=root.querySelector('#lesson-picker'),previous=root.querySelector('[data-lesson-previous]'),next=root.querySelector('[data-lesson-next]'),progress=root.querySelector('[data-lesson-progress]');
  const motion=matchMedia('(prefers-reduced-motion: reduce)'),animations=new Set();
  const states=Object.fromEntries(Object.entries(core.defaults).map(([id,state])=>[id,{...state}]));
  const phone=matchMedia('(max-width:540px)'),enlarged={};
  let active=0;
  function move(element,frames) {
    if(motion.matches||!element.animate)return;
    const animation=element.animate(frames,{duration:260,easing:'cubic-bezier(.2,.7,.2,1)'});animations.add(animation);
    animation.addEventListener('finish',()=>animations.delete(animation),{once:true});
    animation.addEventListener('cancel',()=>animations.delete(animation),{once:true});
  }
  function draw(panel) {
    const id=panel.dataset.lesson,result=panel.querySelector('.lesson-result');
    const scrollLeft=result.querySelector('.lesson-figure')?.scrollLeft||0;
    const positions=new Map([...result.querySelectorAll('[data-motion-key]')].map(n=>[n.dataset.motionKey,{x:+n.dataset.x,y:+n.dataset.y,width:+n.getAttribute('width'),height:+n.getAttribute('height')} ]));
    result.innerHTML=core.renderResult(id,data,{...states[id],compact:phone.matches&&!enlarged[id]},Boolean(enlarged[id]),true);
    result.querySelector('.lesson-figure').scrollLeft=scrollLeft;
    result.querySelectorAll('[data-motion-key]').forEach(element=>{
      const old=positions.get(element.dataset.motionKey),target=element.querySelector('[data-motion-target]');
      if(old&&target)move(target,[{transform:`translate(${old.x-Number(element.dataset.x)}px,${old.y-Number(element.dataset.y)}px)`},{transform:'translate(0,0)'}]);
      if(old&&element.tagName==='rect'){
        const width=+element.getAttribute('width'),height=+element.getAttribute('height');
        element.style.transformBox='fill-box';element.style.transformOrigin='left bottom';
        if(width&&height)move(element,[{transform:`scale(${old.width/width},${old.height/height})`},{transform:'scale(1,1)'}]);
      }
    });
    const readout=panel.querySelector('[data-lesson-readout]');
    readout.textContent='Values describe the selected setting. Focus the figure and use arrow keys, or select a mark, to inspect values.';
    if(id==='engines') {
      const estimate=panel.querySelector('[data-setting="estimate"]');
      estimate.disabled=states[id].engine!=='deseq2';
      if(estimate.disabled){states[id].estimate='raw';estimate.value='raw';}
    }
  }
  function inspect(panel,item) {
    panel.querySelectorAll('.is-inspected').forEach(n=>n.classList.remove('is-inspected'));
    if(!item)return;item.classList.add('is-inspected');panel.querySelector('[data-lesson-readout]').textContent=item.dataset.inspect;
    const scroller=panel.querySelector('.lesson-figure'),box=item.getBoundingClientRect(),view=scroller.getBoundingClientRect();
    if(box.left<view.left)scroller.scrollLeft+=box.left-view.left-16;
    else if(box.right>view.right)scroller.scrollLeft+=box.right-view.right+16;
  }
  root.querySelectorAll('.lesson-panel:not([data-lesson="volcano"])').forEach(panel=>{
    const id=panel.dataset.lesson;
    panel.querySelectorAll('[data-setting]').forEach(control=>{
      control.disabled=false;
      const change=()=>{
        states[id][control.dataset.setting]=control.value;
        const output=panel.querySelector(`[data-value-for="${control.dataset.setting}"]`);if(output)output.textContent=control.type==='range'&&control.step!=='1'?Number(control.value).toFixed(2):control.value;
        draw(panel);
      };
      control.addEventListener(control.tagName==='SELECT'?'change':'input',change);
    });
    const reset=panel.querySelector('[data-lesson-reset]');reset.disabled=false;
    reset.addEventListener('click',()=>{
      states[id]={...core.defaults[id]};panel.querySelectorAll('[data-setting]').forEach(control=>{control.value=states[id][control.dataset.setting];const output=panel.querySelector(`[data-value-for="${control.dataset.setting}"]`);if(output)output.textContent=control.value;});draw(panel);
    });
    panel.addEventListener('pointermove',event=>{const item=event.target.closest('[data-inspect]');if(item)inspect(panel,item)});
    panel.addEventListener('click',event=>{
      if(event.target.closest('[data-figure-size]')){enlarged[id]=!enlarged[id];draw(panel);panel.querySelector('[data-figure-size]').focus({preventScroll:true});return;}
      const item=event.target.closest('[data-inspect]');if(item)inspect(panel,item);
    });
    panel.addEventListener('keydown',event=>{
      if(!event.target.matches('.lesson-svg')||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
      event.preventDefault();const items=[...panel.querySelectorAll('[data-inspect]')],current=items.findIndex(n=>n.classList.contains('is-inspected'));
      const index=event.key==='Home'?0:event.key==='End'?items.length-1:current<0?0:Math.max(0,Math.min(items.length-1,current+(['ArrowLeft','ArrowUp'].includes(event.key)?-1:1)));
      inspect(panel,items[index]);
    });
    draw(panel);
  });
  function choose(id,writeHash=true) {
    const index=lessons.findIndex(lesson=>lesson.id===id);if(index<0)return;active=index;
    root.querySelectorAll('.lesson-panel').forEach(panel=>{const selected=panel.dataset.lesson===id;panel.classList.toggle('is-active',selected);panel.hidden=!selected});
    picker.value=id;previous.disabled=index===0;next.disabled=index===lessons.length-1;progress.textContent=`${index+1} / ${lessons.length}`;
    if(writeHash)history.replaceState(null,'',`#lesson-${id}`);
  }
  picker.disabled=false;picker.addEventListener('change',()=>choose(picker.value));previous.addEventListener('click',()=>choose(lessons[active-1].id));next.addEventListener('click',()=>choose(lessons[active+1].id));
  root.classList.add('lessons-ready');
  const fromHash=location.hash.replace('#lesson-','');choose(lessons.some(l=>l.id===fromHash)?fromHash:lessons[0].id,false);
  window.addEventListener('hashchange',()=>{const id=location.hash.replace('#lesson-','');if(lessons.some(l=>l.id===id))choose(id,false)});
  motion.addEventListener('change',event=>{if(event.matches){animations.forEach(animation=>animation.cancel());animations.clear();}});
  phone.addEventListener('change',()=>root.querySelectorAll('.lesson-panel:not([data-lesson="volcano"])').forEach(draw));
})();
