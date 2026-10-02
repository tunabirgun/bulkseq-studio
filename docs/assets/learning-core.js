const LearningCore = (() => {
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = value => Number.isFinite(value) ? Number(value.toPrecision(3)).toString() : 'Not estimated';
  const svg = (label, body, height=480) => `<svg class="lesson-svg" viewBox="0 0 720 ${height}" role="img" tabindex="0" aria-label="${esc(label)}"><title>${esc(label)}</title>${body}</svg>`;
  const text = (x,y,value,anchor='start',extra='') => `<text x="${x}" y="${y}" text-anchor="${anchor}" ${extra}>${esc(value)}</text>`;
  const inspect = message => `data-inspect="${esc(message)}"`;
  const metrics = entries => `<dl class="parameter-stats lesson-metrics">${entries.map(([name,value,detail])=>`<div><dt>${esc(name)}</dt><dd>${esc(value)}</dd><small>${esc(detail||'')}</small></div>`).join('')}</dl>`;
  const group = (key,x,y,body) => `<g data-motion-key="${key}" data-x="${x}" data-y="${y}" transform="translate(${x} ${y})"><g data-motion-target>${body}</g></g>`;
  const color = value => {
    const low=[216,112,78],mid=[247,247,242],high=[72,92,199],t=Math.min(1,Math.abs(value)),end=value<0?low:high;
    return `rgb(${mid.map((v,i)=>Math.round(v+(end[i]-v)*t)).join(',')})`;
  };
  const lessons = [
    {id:'design',title:'Experimental design',description:'See when the model can separate the effects you ask it to estimate.',link:'design.html'},
    {id:'filtering',title:'DESeq2 low-count prefilter',description:'See which genes pass the count rule before DESeq2 model fitting.',link:'analysis.html'},
    {id:'normalization',title:'Normalization',description:'Separate sequencing depth from composition effects.',link:'engines.html'},
    {id:'pca',title:'PCA and sample QC',description:'Explore the sample variation captured by different feature sets.',link:'outputs.html'},
    {id:'engines',title:'Model fitting and shrinkage',description:'Compare real method fits on the same simulated counts.',link:'engines.html'},
    {id:'volcano',title:'Volcano plot',description:'Explore effect size, adjusted p-values and selection thresholds.',link:'design.html#thresholds'},
    {id:'heatmap',title:'Expression heatmap',description:'Explore within-gene patterns, displayed genes and colour limits.',link:'outputs.html'},
    {id:'enrichment',title:'Enrichment',description:'See why the eligible background changes an over-representation test.',link:'enrichment.html'},
    {id:'ppi',title:'STRING association network',description:'Filter displayed associations without changing the underlying evidence.',link:'enrichment.html'},
  ];
  const defaults = {design:{structure:'balanced',adjust:'1'},filtering:{threshold:'10'},normalization:{mode:'median',gene:'N2'},pca:{genes:'100'},engines:{engine:'deseq2',estimate:'raw'},heatmap:{count:'8',cap:'2.5'},enrichment:{background:'100'},ppi:{score:'0.4',direction:'all'}};
  const select = (id,key,label,options,state) => `<label class="lesson-control"><span>${esc(label)}</span><select data-setting="${key}" id="${id}-${key}" disabled>${options.map(([value,title])=>`<option value="${value}"${String(state[key])===String(value)?' selected':''}>${esc(title)}</option>`).join('')}</select></label>`;
  const range = (id,key,label,min,max,step,state) => `<label class="lesson-control"><span>${esc(label)} <output data-value-for="${key}">${esc(state[key])}</output></span><input type="range" data-setting="${key}" id="${id}-${key}" min="${min}" max="${max}" step="${step}" value="${state[key]}" disabled></label>`;
  function controls(id,data,state) {
    if(id==='design') return select(id,'structure','Sample structure',[['balanced','Balanced batches'],['confounded','Confounded batches'],['paired','Paired samples']],state)+select(id,'adjust','Model terms',[['1','Include batch / donor'],['0','Condition only']],state);
    if(id==='filtering') return range(id,'threshold','Minimum count per qualifying sample',0,50,1,state);
    if(id==='normalization') return select(id,'mode','Display',[['raw','Raw counts'],['total','Library-total scaling'],['median','DESeq2 median-ratio scaling']],state)+select(id,'gene','Example gene',data.normalization.genes.map(g=>[g,g]),state);
    if(id==='pca') return select(id,'genes','Most variable genes used',Object.keys(data.pca).map(n=>[n,n]),state);
    if(id==='engines') return select(id,'engine','Analysis engine',[['deseq2','DESeq2'],['edger','edgeR quasi-likelihood'],['voom','limma-voom']],state)+select(id,'estimate','Effect estimate',[['raw','Unshrunken'],['shrunken','apeglm (DESeq2 only)']],state);
    if(id==='heatmap') return select(id,'count','Top genes by adjusted p-value',[[4,'4'],[8,'8'],[12,'12']],state)+select(id,'cap','Colour cap: ± row z-score',[[.5,'0.5'],[1,'1.0'],[1.5,'1.5'],[2.5,'2.5']],state);
    if(id==='enrichment') return select(id,'background','Background universe',[['100','Eligible tested features (100)'],['1000','Incorrect: add 900 ineligible features']],state);
    if(id==='ppi') return range(id,'score','Minimum displayed combined score (0–1)',0,1,.05,state)+select(id,'direction','Expression-direction view',[['all','All proteins'],['up','Higher expression only'],['down','Lower expression only']],state);
    return '';
  }
  function design(data,state) {
    const row=data.design[`${state.structure}:${state.adjust}`],left=170,top=80,cw=440/row.labels.length,ch=state.compact?48:36;
    let body=row.labels.map((name,i)=>text(left+(i+.5)*cw,48,state.compact?name.replace('Intercept','Int.').replace('Batch ','B').replace('Donor ','D').replace('B vs A','B–A'):name,'middle')).join('');
    row.matrix.forEach((values,r)=>{
      body+=text(left-22,top+(r+.5)*ch,data.samples[r],'end','dominant-baseline="middle"');
      values.forEach((v,c)=>{body+=`<g class="design-cell" ${inspect(`${data.samples[r]}, ${row.labels[c]}: ${v}`)}><rect x="${left+c*cw+2}" y="${top+r*ch+2}" width="${cw-4}" height="${ch-4}" fill="${v?'#cfd8f1':'#f5f3ee'}"/>${text(left+(c+.5)*cw,top+(r+.5)*ch,v,'middle','dominant-baseline="middle"')}</g>`});
    });
    body+=text(360,top+row.matrix.length*ch+52,row.formula,'middle');
    const note=!row.estimable?'Condition and batch cannot be separated by this model.':row.omitted_structure?'The model omits the paired or batch structure. Full rank does not make that choice appropriate.':'Full rank is necessary for estimation; it does not establish a sound biological design.';
    return {plot:svg('Design matrix: '+row.formula,body,state.compact?560:480),metrics:metrics([['Model',row.estimable?'Full rank':'Not estimable'],['Rank / columns',`${row.rank} / ${row.parameters}`],['Residual df',row.residual_df]]),note};
  }
  function filtering(data,state) {
    const d=data.filtering,t=+state.threshold,support=d.counts.map(row=>row.filter(v=>v>=t).length),kept=support.filter(n=>n>=d.required_samples).length;
    const left=125,right=665,top=40,step=state.compact?48:29,max=d.samples.length,bottom=top+d.shown_indices.length*step+24;
    let body=Array.from({length:max+1},(_,i)=>text(left+i/max*(right-left),bottom+20,i,'middle')).join('');
    d.shown_indices.forEach((index,r)=>{const y=top+r*step,width=support[index]/max*(right-left);body+=text(left-18,y+12,d.genes[index],'end','dominant-baseline="middle"')+`<rect data-motion-key="filter-${index}" x="${left}" y="${y}" width="${width}" height="23" class="${support[index]>=d.required_samples?'mark-blue':'mark-muted'}" ${inspect(`${d.genes[index]}: ${support[index]} of ${max} samples have at least ${t} counts; ${support[index]>=d.required_samples?'eligible':'excluded'}`)}/>`;});
    const cut=left+d.required_samples/max*(right-left);body+=`<path class="lesson-cutoff" d="M${cut} 24V${bottom-12}"/>`+text(360,bottom+(state.compact?76:54),state.compact?'Qualifying samples':'Samples meeting the count requirement','middle');
    return {plot:svg('DESeq2 low-count prefilter: qualifying sample counts',body,bottom+(state.compact?104:68)),metrics:metrics([['Eligible genes',`${kept} / ${d.genes.length}`],['Required samples',d.required_samples,'Smallest condition group'],['Minimum count',t,'Per qualifying sample']]),note:`Bars show ${d.shown_indices.length} low-abundance examples; the count summarizes all input genes. This DESeq2 prefilter is separate from independent filtering of results. edgeR and limma-voom use design-aware filterByExpr, so their eligible genes can differ.`};
  }
  function normalization(data,state) {
    const d=data.normalization,index=d.genes.indexOf(state.gene),raw=d.counts[index];
    const factors=state.mode==='median'?d.size_factors:state.mode==='total'?d.total_factors:raw.map(()=>1);
    const values=raw.map((v,i)=>v/factors[i]);
    const maximum=Math.max(...raw,...raw.map((v,i)=>v/d.size_factors[i]),...raw.map((v,i)=>v/d.total_factors[i]))*1.15;
    const bottom=360,top=48,left=state.compact?150:120,cw=state.compact?125:135;
    let body=`<path class="lesson-axis" d="M${state.compact?110:80} ${top}V${bottom}H690"/>`;
    [0,.5,1].forEach(t=>{body+=text(state.compact?98:68,bottom-t*(bottom-top)+5,num(maximum*t),'end')});
    values.forEach((v,i)=>{const height=v/maximum*(bottom-top),labels=[['Base'],['2×','depth'],['Same','depth'],state.compact?['Compo-','sition']:['Composition']][i];body+=`<g ${inspect(`${d.samples[i]}: raw ${raw[i]}, factor ${num(factors[i])}, displayed ${num(v)}`)}><rect data-motion-key="normalization-${i}" x="${left+i*cw}" y="${bottom-height}" width="70" height="${height}" class="${i===3?'mark-orange':'mark-blue'}"/>${text(left+i*cw+35,bottom-height-12,num(v),'middle')}${labels.map((label,line)=>text(left+i*cw+35,bottom+(state.compact?44:28)+line*(state.compact?44:26),label,'middle')).join('')}</g>`});
    body+=text(360,state.compact?510:438,state.mode==='raw'?'Raw read counts':state.compact?'Scaled counts':'Counts divided by the selected size factor','middle');
    return {plot:svg('Normalization example for '+state.gene,body,state.compact?550:480),metrics:metrics([['Gene',state.gene],['Libraries',raw.length],['Display',state.mode==='median'?'Median ratio':state.mode==='total'?'Total scaling':'Raw counts']]),note:'One library doubles every count; another changes only N1. Inspect N2 to see how total scaling can spread a composition effect to an unchanged gene.'};
  }
  function pca(data,state) {
    const d=data.pca[state.genes],scores=Object.values(data.pca).flatMap(item=>item.scores),sx=Math.max(...scores.map(p=>Math.abs(p[0])))*1.25,sy=Math.max(...scores.map(p=>Math.abs(p[1])))*1.5;
    const x=v=>110+(v+sx)/(2*sx)*550,y=v=>350-(v+sy)/(2*sy)*290;
    let body=`<path class="lesson-axis" d="M110 45V350H670"/><path class="lesson-grid-line" d="M${x(0)} 45V350M110 ${y(0)}H670"/>`;
    const points=d.scores.map(p=>({x:x(p[0]),y:y(p[1])})),placed=[];
    points.forEach((point,i)=>{
      const width=data.samples[i].length*(state.compact?20:10),height=state.compact?48:24,pad=6;
      const candidates=[18,36,54,72,90,108,126,144,162,180].flatMap(radius=>[0,-Math.PI/2,Math.PI/2,Math.PI,-Math.PI/4,Math.PI/4,-3*Math.PI/4,3*Math.PI/4].map(angle=>({x:point.x+radius*Math.cos(angle)-width/2,y:point.y+radius*Math.sin(angle)-height/2,width,height})));
      const overlap=(a,b)=>a.x<b.x+b.width+pad&&a.x+a.width+pad>b.x&&a.y<b.y+b.height+pad&&a.y+a.height+pad>b.y;
      const label=candidates.find(box=>box.x>116&&box.x+width<664&&box.y>48&&box.y+height<344&&!placed.some(other=>overlap(box,other))&&!points.some(p=>overlap(box,{x:p.x-7,y:p.y-7,width:14,height:14})));
      if(!label)throw new Error('PCA label placement has no collision-free position');
      placed.push(label);
      const lx=Math.max(label.x,Math.min(point.x,label.x+width)),ly=Math.max(label.y,Math.min(point.y,label.y+height));
      body+=group(`sample-${i}`,point.x,point.y,`<g ${inspect(`${data.samples[i]}: PC1 ${num(d.scores[i][0])}, PC2 ${num(d.scores[i][1])}`)}><path class="pca-leader" d="M0 0L${lx-point.x} ${ly-point.y}"/><circle r="7" class="${data.samples[i][0]==='A'?'mark-blue':'mark-orange'}"/>${text(label.x-point.x,label.y+height/2-point.y,data.samples[i],'start','class="pca-label" dominant-baseline="middle"')}</g>`);
    });
    body+=text(390,420,`PC1 (${num(d.percent[0])}%${state.compact?'':' variance'})`,'middle')+text(state.compact?38:26,210,`PC2 (${num(d.percent[1])}%${state.compact?'':' variance'})`,'middle',`transform="rotate(-90 ${state.compact?38:26} 210)"`);
    return {plot:svg('PCA from the most variable transformed genes',body),metrics:metrics([['Genes used',d.count],['PC1 variance',num(d.percent[0])+'%'],['PC2 variance',num(d.percent[1])+'%']]),note:`The ${Object.keys(data.pca).join(', ')} gene settings are teaching examples. BulkSeq Studio defaults to 500 most variable genes, limited by retained genes. PCA is recomputed here for each set; separation is diagnostic, not a treatment-effect test. Axis signs are arbitrary.`};
  }
  function engines(data,state) {
    const d=data.engines[state.engine],rows=d.genes;
    const shrunken=Object.fromEntries(data.engines.shrinkage.map(r=>[r.id,r.log2fc]));
    const useShrink=state.engine==='deseq2'&&state.estimate==='shrunken';
    const max=Math.ceil(Math.max(1,...Object.entries(data.engines).filter(([k])=>k!=='shrinkage').flatMap(([,v])=>v.genes.map(r=>Math.abs(r.log2fc||0))))+.3);
    const x=v=>150+(v+max)/(2*max)*470;
    let body=`<path class="lesson-grid-line" d="M${x(0)} 36V${state.compact?446:374}"/>`;
    rows.forEach((row,i)=>{const y=60+i*(state.compact?52:38),value=useShrink?shrunken[row.id]:row.log2fc;body+=text(125,y,row.id,'end','dominant-baseline="middle"');if(value===null||value===undefined){body+=text(180,y,state.compact?'Not estimated':'Not estimated after filtering');return;}body+=`<path class="lesson-grid-line" d="M150 ${y}H620"/>`;if(useShrink)body+=`<path class="estimate-change" d="M${x(row.log2fc)} ${y}H${x(value)}"/><circle cx="${x(row.log2fc)}" cy="${y}" r="5" class="unshrunk-point"/>`;body+=group(row.id,x(value),y,`<circle r="7" class="${value>=0?'mark-blue':'mark-orange'}" ${inspect(`${row.id}: log2FC ${num(value)}; model adjusted p ${num(row.padj)}`)}/>`)});
    [-max,0,max].forEach(v=>body+=text(x(v),state.compact?490:410,v,'middle'));body+=text(385,state.compact?550:452,state.compact?'log2FC: B vs A':'Estimated log2 fold change: B vs A','middle');
    return {plot:svg('Precomputed differential-expression effect estimates',body,state.compact?590:480),metrics:metrics([['Engine',state.engine==='deseq2'?'DESeq2':state.engine==='edger'?'edgeR':'limma-voom'],['Genes retained',d.tested,'Method-specific filtering'],['Estimates',useShrink?'apeglm':'Unshrunken']]),note:useShrink?'Open circles are unshrunken estimates; filled points are apeglm estimates. BulkSeq’s main up/down lists use unshrunken effects.':'These are real fits on the same simulated counts, with each method’s filtering and normalization. Differences do not establish that one method is universally better.'};
  }
  function dendrogram(tree,order,leafPosition,rootPosition,row=true) {
    const n=order.length,positions=new Map(order.map((leaf,i)=>[leaf,leafPosition(i)])),maximum=Math.max(...tree.map(r=>r[2]),1e-12);
    let paths='';
    tree.forEach(([a,b,height],index)=>{
      const pa=positions.get(a),pb=positions.get(b),depth=rootPosition(height/maximum),point=row?[depth,(pa[1]+pb[1])/2]:[(pa[0]+pb[0])/2,depth];
      paths+=row?`<path d="M${pa[0]} ${pa[1]}H${depth}V${pb[1]}H${pb[0]}"/>`:`<path d="M${pa[0]} ${pa[1]}V${depth}H${pb[0]}V${pb[1]}"/>`;
      positions.set(n+index,point);
    });
    return `<g class="heat-tree">${paths}</g>`;
  }
  function heatmap(data,state) {
    const d=data.heatmap,n=+state.count,cap=+state.cap,s=d.states[`${n}:${cap}`],left=180,top=120,cw=500/d.samples.length,ch=state.compact?48:36,height=top+n*ch+(state.compact?120:96);
    let body=dendrogram(s.row_tree,s.row_order,i=>[158,top+(i+.5)*ch],t=>158-t*58,true)+dendrogram(s.column_tree,s.column_order,i=>[left+(i+.5)*cw,60],t=>60-t*46,false);
    s.column_order.forEach((c,i)=>{body+=text(left+(i+.5)*cw,94,d.samples[c],'middle')+`<rect x="${left+i*cw+2}" y="104" width="${cw-4}" height="7" class="${d.samples[c][0]==='A'?'mark-blue':'mark-orange'}"/>`});
    s.row_order.forEach((r,i)=>{
      body+=text(88,top+(i+.5)*ch,d.genes[r],'end','dominant-baseline="middle"');
      s.column_order.forEach((c,j)=>{const z=d.z[r][c],shown=Math.max(-cap,Math.min(cap,z));body+=group(`heat-${r}-${c}`,left+j*cw,top+i*ch,`<rect width="${cw-2}" height="${ch-2}" fill="${color(shown/cap)}" ${inspect(`${d.genes[r]} / ${d.samples[c]}: transformed ${num(d.values[r][c])}; row z ${num(z)}; displayed ${num(shown)}`)}/>`)});
    });
    const legendY=top+n*ch+30;
    for(let i=0;i<60;i++)body+=`<rect x="${250+i*4}" y="${legendY}" width="4.1" height="12" fill="${color(i/59*2-1)}"/>`;
    body+=text(250,legendY+(state.compact?64:36),-cap,'middle')+text(370,legendY+(state.compact?64:36),'0','middle')+text(490,legendY+(state.compact?64:36),cap,'middle');
    return {plot:svg('Row-scaled expression heatmap with Ward clustering',body,height),metrics:metrics([['Genes displayed',n,'Ranked by adjusted p'],['Colour limit','±'+cap,'Row z-score'],['Clipped cells',s.clipped_cells,'Original z-scores retained']]),note:'Colour is relative within each gene, not absolute abundance across genes. BulkSeq clips row z-scores before Ward clustering, so changing the cap can also change ordering.'};
  }
  function enrichment(data,state) {
    const rows=data.enrichment[state.background],max=Math.ceil(Math.max(...Object.values(data.enrichment).flat().map(r=>-Math.log10(r.padj))));
    const left=140,right=655;
    let body=`<path class="lesson-axis" d="M${left} 50V340H${right}"/>`;
    rows.forEach((row,i)=>{const y=95+i*90,x=left+(-Math.log10(row.padj))/max*(right-left);body+=text(left-20,y,row.name,'end','dominant-baseline="middle"')+`<path class="lesson-grid-line" d="M${left} ${y}H${right}"/>`+group(`set-${i}`,x,y,`<circle r="${6+row.overlap}" class="mark-blue" ${inspect(`${row.name}: ${row.overlap}/${row.foreground} selected; ${row.term_size}/${row.universe} background; adjusted p ${num(row.padj)}`)}/>`)});
    [0,max/2,max].forEach(v=>body+=text(left+v/max*(right-left),372,num(v),'middle'));body+=text(395,426,state.compact?'−log₁₀(adjusted p)':'−log₁₀(BH-adjusted enrichment p)','middle');
    return {plot:svg('Over-representation changes with the background universe',body),metrics:metrics([['Selected features',rows[0].foreground,'Held fixed'],['Background',rows[0].universe],['Sets tested',rows.length,'BH correction across all sets']]),note:state.background==='1000'?'Incorrect counterexample: adding 900 unmeasured or ineligible features makes enrichment look stronger. These features could not enter the foreground and do not belong in its background.':'Use eligible tested features mapped through the selected annotation route. The foreground, sets and overlaps are fixed here; larger dots mean more selected features overlap the set.'};
  }
  function network(data,state) {
    const d=data.network,floor=+state.score,visible=d.nodes.filter(n=>state.direction==='all'||(state.direction==='up'?n.log2fc>0:n.log2fc<0)),ids=new Set(visible.map(n=>n.id));
    const edges=d.edges.filter(e=>e.score>=floor&&ids.has(e.source)&&ids.has(e.target));
    const degree=Object.fromEntries(visible.map(n=>[n.id,0]));edges.forEach(e=>{degree[e.source]++;degree[e.target]++});
    const position=Object.fromEntries(d.nodes.map((n,i)=>{const a=-Math.PI/2+i/d.nodes.length*Math.PI*2;return[n.id,{x:360+168*Math.cos(a),y:(state.compact?270:236)+168*Math.sin(a),lx:360+(state.compact?230:208)*Math.cos(a),ly:(state.compact?270:236)+(state.compact?230:208)*Math.sin(a),a}]}));
    let body=edges.map(e=>{const a=position[e.source],b=position[e.target];return `<line data-edge-source="${e.source}" data-edge-target="${e.target}" data-score="${e.score}" class="ppi-edge" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke-width="${1+e.score*2}" ${inspect(`${e.source}—${e.target}: synthetic combined association score ${e.score}; not a p-value or calibrated probability`)} />`}).join('');
    visible.forEach(n=>{const p=position[n.id];body+=`<g data-protein="${n.id}" ${inspect(`${n.id}: log2FC ${num(n.log2fc)}, ${degree[n.id]} displayed neighbours`)}><circle cx="${p.x}" cy="${p.y}" r="12" class="${n.log2fc>0?'mark-blue':'mark-orange'}"/>${text(p.lx,p.ly+5,n.id,'middle')}</g>`});
    return {plot:svg('Synthetic STRING association network filtered by combined score',body,state.compact?560:480),metrics:metrics([['Visible proteins',visible.length],['Associations',edges.length,'Combined score ≥ '+num(floor)],['Isolated proteins',visible.filter(n=>degree[n.id]===0).length,'At this display threshold']]),note:'Scores illustrate STRING combined association evidence, not p-values or calibrated probabilities. Positions stay fixed; distance is not a biological quantity. Associations need not be direct physical binding. Filtering this saved example does not query STRING or recover omitted edges.'};
  }
  const renderers={design,filtering,normalization,pca,engines,heatmap,enrichment,ppi:network};
  function renderResult(id,data,state,enlarged=false,interactive=false) {
    const result=renderers[id](data,state);
    return `<div class="figure-view-controls"><button type="button" data-figure-size aria-pressed="${enlarged}" aria-controls="figure-${id}"${interactive?'':' disabled'}>${enlarged?'Fit figure':'Enlarge figure'}</button><p>${enlarged?'Swipe horizontally to explore.':'Full figure shown.'} Tap a mark to inspect values.</p></div><div id="figure-${id}" class="lesson-figure ${enlarged?'is-enlarged':'is-fit'}${state.compact?' is-compact':''}" role="region" aria-label="${esc(lessons.find(lesson=>lesson.id===id).title)} figure">${result.plot}</div>${result.metrics}<p class="lesson-note">${esc(result.note)}</p><p class="lesson-readout" data-lesson-readout role="status">Focus the figure and use arrow keys, or select a mark, to inspect values.</p>`;
  }
  function renderPanel(id,data) {
    const lesson=lessons.find(item=>item.id===id),state=defaults[id];
    return `<section class="lesson-panel${id==='design'?' is-active':''}" id="lesson-${id}" data-lesson="${id}" aria-labelledby="lesson-title-${id}"><header class="lesson-heading"><p class="eyebrow">Reproducible synthetic example</p><h2 id="lesson-title-${id}">${esc(lesson.title)}</h2><p>${esc(lesson.description)}</p></header><div class="lesson-controls">${controls(id,data,state)}<button type="button" data-lesson-reset disabled>Reset</button></div><div class="lesson-result">${renderResult(id,data,state)}</div><a class="lesson-reference" href="${lesson.link}">Read the full guide ↗</a></section>`;
  }
  return {lessons,defaults,esc,renderPanel,renderResult};
})();
if (typeof module !== 'undefined') module.exports = LearningCore;
