const ExplorerCore = (() => {
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = value => Number(value).toFixed(2);
  const color = (z, cap) => {
    const t = Math.min(1, Math.abs(z) / cap), end = z < 0 ? [183,72,38] : [55,87,175];
    return `rgb(${end.map(v => Math.round(248 + (v - 248) * t)).join(',')})`;
  };
  function heatmap(data, count, cap) {
    const header = '<span class="heat-gene">Gene</span>' + data.samples.map(s => `<span class="heat-sample">${esc(s)}</span>`).join('');
    const rows = data.genes.slice(0, count).map((gene, i) => `<span class="heat-gene">${esc(gene)}</span>` + data.samples.map((sample, j) => `<button type="button" class="heat-cell" data-cell="${i},${j}" tabindex="${i || j ? -1 : 0}" style="background:${color(data.z[i][j],cap)}" aria-label="${esc(gene)}, ${esc(sample)}: z-score ${number(data.z[i][j])}, rlog expression ${number(data.values[i][j])}"></button>`).join('')).join('');
    return `<div class="heat-grid" aria-label="Row z-scores across samples">${header}${rows}</div>`;
  }
  function graphStats(data, cutoff) {
    const edges = data.edges.filter(edge => edge.score >= cutoff);
    const neighbors = new Map(data.nodes.map(node => [node.id,new Set()]));
    edges.forEach(e => {neighbors.get(e.source).add(e.target); neighbors.get(e.target).add(e.source);});
    const seen = new Set(); let components = 0;
    for (const node of data.nodes) {
      if (seen.has(node.id)) continue;
      components++; const stack = [node.id]; seen.add(node.id);
      while (stack.length) for (const id of neighbors.get(stack.pop())) if (!seen.has(id)) {seen.add(id); stack.push(id);}
    }
    return {edges,neighbors,components,isolates:[...neighbors.values()].filter(set => !set.size).length};
  }
  function network(data, cutoff) {
    const positions = new Map(data.nodes.map((node,i) => [node.id,{x:i<6?110:250,y:42+(i%6)*58}]));
    const edges = data.edges.map((e,i) => {const a=positions.get(e.source),b=positions.get(e.target),bend=(a.x<180?1:-1)*(30+Math.abs(a.y-b.y)/5),path=a.x===b.x?`M${a.x},${a.y} Q${a.x+bend},${(a.y+b.y)/2} ${b.x},${b.y}`:`M${a.x},${a.y} L${b.x},${b.y}`;return `<path data-edge="${i}" d="${path}" class="network-edge" style="opacity:${e.score>=cutoff?1:0}"/>`;}).join('');
    const nodes = data.nodes.map((node,i) => {const p=positions.get(node.id);return `<g class="network-node" data-node="${i}" role="button" tabindex="${i?'-1':'0'}" aria-label="Inspect ${esc(node.label)}"><rect class="network-hitbox" x="${i<6?12:228}" y="${p.y-22}" width="120" height="44"/><circle class="network-target" cx="${p.x}" cy="${p.y}" r="22"/><circle class="network-dot" cx="${p.x}" cy="${p.y}" r="8"/><text x="${p.x+(i<6?-20:20)}" y="${p.y+5}" text-anchor="${i<6?'end':'start'}">${esc(node.label)}</text></g>`;}).join('');
    return `<svg class="network-figure" viewBox="0 0 360 378" role="group" aria-label="STRING functional association network"><title>Fixed public STRING network; lines are associations, not measured interactions in these samples.</title>${edges}${nodes}</svg>`;
  }
  function forestDomain(data) {
    const limits = data.scenarios.flatMap(s => [...s.effects.flatMap((effect,i) => [effect-1.959963984540054*s.se[i],effect+1.959963984540054*s.se[i]]), ...s.fits.flatMap(f => [f.ciLow,f.ciHigh])]);
    return [Math.floor(Math.min(0,...limits)), Math.ceil(Math.max(0,...limits))];
  }
  const position = (value, domain) => 100*(value-domain[0])/(domain[1]-domain[0]);
  function forest(data) {
    const domain=forestDomain(data),scenario=data.scenarios[0],fit=scenario.fits.find(f=>f.omitted===null);
    const row=(label,index,estimate,low,high,weight=0) => `<div class="forest-row" data-study="${index}"><span class="study-label">${esc(label)}</span><span class="estimate-bar"><i class="forest-zero" style="left:${position(0,domain)}%"></i><i class="forest-interval${index==='pooled'?' forest-diamond':''}" style="left:${position(low,domain)}%;width:${position(high,domain)-position(low,domain)}%"></i>${index==='pooled'?'':`<i class="forest-point" style="left:${position(estimate,domain)}%;--point-size:${28*Math.sqrt(weight)}px"></i>`}</span><span class="estimate-values">${number(estimate)} [${number(low)}, ${number(high)}]</span></div>`;
    const rows=data.studies.map((name,i)=>row(name,i,scenario.effects[i],scenario.effects[i]-1.959963984540054*scenario.se[i],scenario.effects[i]+1.959963984540054*scenario.se[i],fit.weights[i])).join('');
    const ticks=Array.from({length:domain[1]-domain[0]+1},(_,i)=>domain[0]+i).map(v=>`<span style="left:${position(v,domain)}%">${v}</span>`).join('');
    return `<div class="forest-figure" role="group" aria-label="Study effects and pooled 95 percent confidence intervals">${rows}${row('Pooled','pooled',fit.estimate,fit.ciLow,fit.ciHigh)}<div class="forest-row forest-axis"><span></span><span class="estimate-bar">${ticks}</span></div><p class="forest-axis-title">log2 fold change (B vs A), 95% confidence intervals</p></div>`;
  }
  return {esc,number,color,heatmap,graphStats,network,forestDomain,position,forest};
})();
if (typeof module !== 'undefined') module.exports = ExplorerCore;
if (typeof window !== 'undefined') window.ExplorerCore = ExplorerCore;
