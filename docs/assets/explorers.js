(() => {
  const core=window.ExplorerCore;
  const dataFor=root=>JSON.parse(root.querySelector('[data-explorer-data]').textContent);
  document.querySelectorAll('.figure-explorer').forEach(root=>root.querySelectorAll('input,select,[data-explorer-reset]').forEach(control=>control.disabled=false));
  const tabs=[...document.querySelectorAll('.figure-tabs [role="tab"]')];
  function selectTab(tab) {
    tabs.forEach(button=>{const active=button===tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;document.getElementById(button.getAttribute('aria-controls')).hidden=!active;});
  }
  tabs.forEach((tab,i)=>{
    tab.addEventListener('click',()=>selectTab(tab));
    tab.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(i+(event.key==='ArrowLeft'?-1:1)+tabs.length)%tabs.length;
      selectTab(tabs[next]);tabs[next].focus();
    });
  });
  if(tabs.length)selectTab(tabs[0]);
  document.querySelectorAll('[data-heatmap]').forEach(root=>{
    const data=dataFor(root),count=root.querySelector('[data-heat-count]'),cap=root.querySelector('[data-heat-cap]'),canvas=root.querySelector('[data-heat-canvas]'),readout=root.querySelector('[data-heat-readout]');
    let selected=null;
    function inspect(button) {
      canvas.querySelectorAll('.heat-cell').forEach(cell=>{cell.tabIndex=cell===button?0:-1;cell.classList.toggle('is-inspected',cell===button);});
      selected=button.dataset.cell.split(',').map(Number);const [i,j]=selected,z=data.z[i][j],clipped=Math.abs(z)>Number(cap.value);
      readout.textContent=`${data.genes[i]}, sample ${data.samples[j]}: rlog expression ${core.number(data.values[i][j])}; row z-score ${core.number(z)}.${clipped?' Color is clipped; the value is unchanged.':''}`;
    }
    function update(rebuild=false) {
      if(rebuild){canvas.innerHTML=core.heatmap(data,Number(count.value),Number(cap.value));if(selected?.[0]>=Number(count.value))selected=null;}
      canvas.querySelectorAll('.heat-cell').forEach(cell=>{const [i,j]=cell.dataset.cell.split(',').map(Number);cell.style.background=core.color(data.z[i][j],Number(cap.value));});
      root.querySelector('[data-heat-cap-value]').value=cap.value;
      root.querySelector('[data-low-label]').textContent='−'+cap.value;root.querySelector('[data-high-label]').textContent='+'+cap.value;
      root.querySelector('[data-heat-note]').textContent=`${count.value} of ${data.genes.length} example genes shown, in adjusted-p order. Color limit ±${cap.value}; expression values stay fixed.`;
      if(selected)inspect(canvas.querySelector(`[data-cell="${selected.join(',')}"]`));
      else readout.textContent='Select a cell to inspect its expression and z-score. Arrow keys move between cells.';
    }
    canvas.addEventListener('click',event=>{const cell=event.target.closest('[data-cell]');if(cell)inspect(cell);});
    canvas.addEventListener('focusin',event=>{if(event.target.matches('[data-cell]'))inspect(event.target);});
    canvas.addEventListener('keydown',event=>{
      const cell=event.target.closest('[data-cell]');if(!cell||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
      event.preventDefault();let [i,j]=cell.dataset.cell.split(',').map(Number);
      if(event.key==='ArrowLeft')j=Math.max(0,j-1);if(event.key==='ArrowRight')j=Math.min(data.samples.length-1,j+1);
      if(event.key==='ArrowUp')i=Math.max(0,i-1);if(event.key==='ArrowDown')i=Math.min(Number(count.value)-1,i+1);
      if(event.key==='Home')j=0;if(event.key==='End')j=data.samples.length-1;
      canvas.querySelector(`[data-cell="${i},${j}"]`).focus();
    });
    count.addEventListener('change',()=>update(true));cap.addEventListener('input',()=>update());
    root.querySelector('[data-explorer-reset]').addEventListener('click',()=>{count.value='8';cap.value='2.5';selected=null;update(true);});
    update();
  });
  document.querySelectorAll('[data-network]').forEach(root=>{
    const data=dataFor(root),slider=root.querySelector('[data-network-score]'),nodes=[...root.querySelectorAll('[data-node]')],lines=[...root.querySelectorAll('[data-edge]')];
    let selected=null;
    function update() {
      const score=Number(slider.value),stats=core.graphStats(data,score);
      root.querySelector('[data-network-score-value]').value=String(score);
      lines.forEach((line,i)=>{const edge=data.edges[i],visible=edge.score>=score,incident=selected===null||edge.source===selected||edge.target===selected;line.style.opacity=visible?(incident?1:.16):0;line.classList.toggle('is-highlighted',visible&&selected!==null&&incident);});
      nodes.forEach((node,i)=>{const id=data.nodes[i].id;node.classList.toggle('is-selected',id===selected);node.classList.toggle('is-neighbor',selected!==null&&stats.neighbors.get(selected).has(id));node.setAttribute('aria-label',`${data.nodes[i].label}: degree ${stats.neighbors.get(id).size}`);});
      root.querySelector('[data-network-summary]').textContent=`${data.nodes.length} proteins · ${stats.edges.length} edges · ${stats.components} connected component${stats.components===1?'':'s'} · ${stats.isolates} isolated`;
      const readout=root.querySelector('[data-network-readout]');
      if(selected!==null){const selectedNode=data.nodes.find(n=>n.id===selected),neighbors=data.nodes.filter(n=>stats.neighbors.get(selected).has(n.id)).map(n=>n.label);readout.textContent=`${selectedNode.label}: degree ${neighbors.length} at score ≥ ${score}.${neighbors.length?' Neighbors: '+neighbors.join(', ')+'.':' No retained neighbors in this snapshot.'}`;}
      else readout.textContent='Select a protein to inspect its neighbors. Arrow keys move between proteins.';
    }
    nodes.forEach((node,i)=>{
      const inspect=()=>{selected=data.nodes[i].id;nodes.forEach(n=>n.tabIndex=n===node?0:-1);update();};
      node.addEventListener('click',inspect);node.addEventListener('focus',inspect);
      node.addEventListener('keydown',event=>{
        if(['Enter',' '].includes(event.key)){event.preventDefault();inspect();return;}
        if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','Escape'].includes(event.key))return;
        event.preventDefault();if(event.key==='Escape'){selected=null;update();return;}
        const next=event.key==='Home'?0:event.key==='End'?nodes.length-1:(i+(['ArrowLeft','ArrowUp'].includes(event.key)?-1:1)+nodes.length)%nodes.length;nodes[next].focus();
      });
    });
    slider.addEventListener('input',update);
    root.querySelector('[data-explorer-reset]').addEventListener('click',()=>{slider.value='400';selected=null;nodes.forEach((node,i)=>node.tabIndex=i?-1:0);update();});update();
  });
  document.querySelectorAll('[data-forest]').forEach(root=>{
    const data=dataFor(root),scenarioInput=root.querySelector('[data-forest-scenario]'),omitInput=root.querySelector('[data-forest-omit]'),domain=core.forestDomain(data);
    function update() {
      const scenario=data.scenarios.find(s=>s.id===scenarioInput.value),omitted=omitInput.value==='all'?null:Number(omitInput.value),fit=scenario.fits.find(f=>f.omitted===omitted),baseline=scenario.fits.find(f=>f.omitted===null);
      const omittedIndex=omitted===null?null:omitted-1;
      const updateRow=(key,estimate,low,high,weight)=>{
        const row=root.querySelector(`[data-study="${key}"]`),interval=row.querySelector('.forest-interval');
        interval.style.left=core.position(low,domain)+'%';interval.style.width=(core.position(high,domain)-core.position(low,domain))+'%';
        const point=row.querySelector('.forest-point');if(point){point.style.left=core.position(estimate,domain)+'%';point.style.setProperty('--point-size',28*Math.sqrt(weight)+'px');}
        row.querySelector('.estimate-values').textContent=`${core.number(estimate)} [${core.number(low)}, ${core.number(high)}]`;
        row.classList.toggle('is-omitted',key===omittedIndex);row.setAttribute('aria-label',`${key==='pooled'?'Pooled':data.studies[key]}${key===omittedIndex?', omitted from pooled fit':''}: ${core.number(estimate)}, 95% confidence interval ${core.number(low)} to ${core.number(high)}`);
      };
      data.studies.forEach((name,i)=>updateRow(i,scenario.effects[i],scenario.effects[i]-1.959963984540054*scenario.se[i],scenario.effects[i]+1.959963984540054*scenario.se[i],fit.weights[i]));
      updateRow('pooled',fit.estimate,fit.ciLow,fit.ciHigh,1);
      root.querySelector('[data-forest-summary]').textContent=`${data.studies.length-(omitted===null?0:1)} studies included; between-study variance τ² = ${core.number(fit.tau2)}.`;
      root.querySelector('[data-forest-readout]').textContent=omitted===null?scenario.description:`${data.studies[omittedIndex]} is omitted from the pooled fit. The estimate changes from ${core.number(baseline.estimate)} to ${core.number(fit.estimate)}; the omitted study remains visible for comparison.`;
    }
    scenarioInput.addEventListener('change',update);omitInput.addEventListener('change',update);
    root.querySelector('[data-explorer-reset]').addEventListener('click',()=>{scenarioInput.value=data.scenarios[0].id;omitInput.value='all';update();});update();
  });
})();
