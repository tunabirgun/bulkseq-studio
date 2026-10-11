import { readFileSync } from 'node:fs';

const versions = JSON.parse(readFileSync(new URL('./tool-versions.json', import.meta.url), 'utf8'));
const escape = value => String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);

const versionRows = versions.tools.map(({ tool, version }) => `<tr><th scope="row">${escape(tool)}</th><td>${escape(version)}</td></tr>`).join('');

export const sciencePage = {
  slug: 'science',
  title: 'Science',
  summary: 'How BulkSeq Studio selects tools and parameters, and how its multi-study analysis combines evidence without overstating it.',
  body: `<section aria-labelledby="science-overview">
    <h2 id="science-overview">Analysis routes</h2>
    <p>BulkSeq Studio keeps raw-read, count-matrix, microarray and supplied-result routes distinct. Read processing and quantification apply only to raw reads; a supplied counts matrix enters the shared count-analysis path; microarrays use limma; supplied differential-expression tables are normalized into the reporting path without refitting a count model. The project record states the route, settings and realized analysis software for each run.</p>
    <p>For raw RNA-seq, the workflow can inspect reads with FastQC and MultiQC, trim with fastp, Trim Galore or Trimmomatic, optionally screen contamination or rRNA, then use STAR or HISAT2 alignment with featureCounts, or Salmon plus tximport. These routes produce the same downstream count-table contract, but their read assignment strategies differ. Select a route that is justified by the library, reference and study question rather than expecting interchangeable results.</p>
    <details class="method-detail">
      <summary>Workflow execution and retrieval</summary>
      <p>Snakemake builds the dependency graph and records outputs, checks and logs for the selected route. It does not choose a biological model. For public FASTQ retrieval, the workflow uses aria2 with up to four connections per file and resumable retries when it is available, then falls back to a single stream. Downloaded reads are checked against the provider’s configured MD5 before analysis.</p>
    </details>
    <details class="method-detail">
      <summary>From reads to counts</summary>
      <p>FastQC measures per-read quality summaries before and after trimming; MultiQC aggregates the tool reports. fastp is the default adapter and quality trimmer. Its defaults define a qualified base as Phred 15, discard reads with more than 40% unqualified bases, and retain reads of at least 36 bases; poly-G and poly-X trimming are off. Those values change which reads enter counting. Trim Galore and Trimmomatic are alternatives: the shared quality and length settings carry over, while Trimmomatic also uses adapter clipping, leading/trailing quality 3 and a 4-base window at quality 15. The three routes emit the same file shape for later steps, not necessarily the same retained reads.</p>
      <p>Optional SortMeRNA builds an index from the configured rRNA database and sends rRNA-matching reads away from the alignment input; its paired-end mode defaults to <code>paired_in</code>. Optional RiboDetector is reference-free and defaults to <code>ensure = norrna</code> with chunk size 256. FastQ Screen samples up to 100,000 reads against a user-supplied reference panel and reports contamination; it does not remove reads. RSeQC adds genomic-distribution and gene-body-coverage diagnostics when a genome BAM exists.</p>
    </details>
    <details class="method-detail">
      <summary>Alignment and gene-level quantification</summary>
      <p>STAR maps reads to a genome and produces a coordinate-sorted BAM plus its own gene-count table. Its default route permits up to 10 mapping loci, allows a mismatch fraction of 1.0, and has two-pass splice-junction discovery off; enabling two-pass mode changes the alignment procedure and is slower. HISAT2 also maps to a genome and its BAM is counted with featureCounts. Salmon uses mapping-based transcript quantification directly from trimmed reads, with mapping validation and paired-end GC-bias correction when applicable; tximport then aggregates transcript estimates to genes with <code>lengthScaledTPM</code>.</p>
      <p>featureCounts sums reads over the configured annotation feature type and attribute, defaulting to <code>exon</code> and <code>gene_id</code>. The workflow determines strandedness per sample for genome-alignment routes; changing a fallback strandedness value is not a substitute for a compatible library and annotation. The associated methods are described by <a href="https://doi.org/10.1093/bioinformatics/bts635">Dobin et al. (STAR)</a>, <a href="https://doi.org/10.1038/s41587-019-0201-4">Kim et al. (HISAT2)</a>, <a href="https://doi.org/10.1038/nmeth.4197">Patro et al. (Salmon)</a>, <a href="https://doi.org/10.1093/bioinformatics/btt656">Liao, Smyth and Shi (featureCounts)</a> and <a href="https://doi.org/10.12688/f1000research.7563.1">Soneson, Love and Robinson (tximport)</a>.</p>
    </details>
  </section>

  <section aria-labelledby="parameters">
    <h2 id="parameters">Parameter choices</h2>
    <p>Start with a sample sheet that identifies the biological factor, its reference level, the planned numerator and denominator, and any defensible covariates. The default count-model settings are <code>~ condition</code>, control as the condition reference, adjusted-p threshold 0.05, raw |log2 fold change| screen 1, minimum count 10, and requested apeglm shrinkage. They are starting defaults, not universally optimal values.</p>
    <ul>
      <li><strong>Reference and annotation:</strong> Use a FASTA, GTF/GFF and gene identifier namespace that belong to the same assembly. Changing them can change the feature universe and read assignment.</li>
      <li><strong>Read processing:</strong> Enable trimming or optional QC only when the reads and study protocol support it. Trimming, rRNA handling and the aligner/quantifier change the input counts; the contamination screen is a report, not a filter.</li>
      <li><strong>Model:</strong> The design formula determines the fitted coefficient. Include known covariates only when they are measured, relevant and leave a full-rank design. An interaction changes the estimand; the routine contrast is a simple effect at the reference level unless an interaction coefficient is explicitly requested.</li>
      <li><strong>Calling lists:</strong> In the default DESeq2 route, <code>padj &lt; alpha</code> and the raw, unshrunken |log2 fold change| screen create the up- and down-regulated lists. Shrunk fold changes support stable display and ranking; they do not replace that list rule. The additional effect-size screen does not itself supply an FDR guarantee for the selected subset.</li>
    </ul>
    <p>Changing a display cutoff in a figure changes which already fitted rows are highlighted. It does not refit the model, change the p-values, or rescue a confounded design. Re-run the workflow after changing an analysis setting and compare the resulting project records.</p>
    <details class="method-detail">
      <summary>What the default count analysis fits</summary>
      <p>DESeq2 fits a negative-binomial count model. Its independent-filtering step determines the eligible adjusted-p family, and Benjamini–Hochberg adjustment is applied within that family; rows excluded from adjustment have <code>NA</code> adjusted p-values. The main implementation uses DESeq2’s Wald contrast; the separate threshold test uses <code>lfcThreshold</code> and answers a different question. apeglm shrinkage is requested for fold-change presentation; if that coefficient fit fails, the workflow records an ashr fallback. These procedures follow <a href="https://doi.org/10.1186/s13059-014-0550-8">Love, Huber and Anders (DESeq2)</a>, <a href="https://doi.org/10.1093/bioinformatics/bty895">Zhu, Ibrahim and Love (apeglm)</a>, <a href="https://doi.org/10.1093/biostatistics/kxw041">Stephens (ashr)</a> and <a href="https://doi.org/10.1111/j.2517-6161.1995.tb02031.x">Benjamini–Hochberg</a>.</p>
      <p>Optional limma-voom and edgeR-QLF routes use their own estimators and model parameterization. They are useful sensitivity analyses when their assumptions and contrast are appropriate; agreement or disagreement is evidence to inspect, not an automatic verdict. The implemented routes are based on <a href="https://doi.org/10.1093/nar/gkv007">limma-voom</a> and <a href="https://doi.org/10.1093/nar/gks042">edgeR</a>.</p>
    </details>
    <details class="method-detail">
      <summary>Normalization, filtering and microarray scope</summary>
      <p>DESeq2 prefilters genes with at least the configured count in at least the smallest contrast group, then estimates size factors and dispersions in its count model. limma-voom and edgeR-QLF instead use edgeR’s <code>filterByExpr</code> and TMM normalization; voom estimates observation-level precision weights before limma’s moderated fit, while edgeR fits a quasi-likelihood model. Neither alternative route performs DESeq2 fold-change shrinkage. Their implemented group-means parameterization supports additive covariates but rejects interaction and nesting terms.</p>
      <p>For microarrays, a GEO series matrix is treated as submitter-normalized expression data, whereas raw Affymetrix CEL files are processed with RMA. The workflow converts to a gene-level log2 matrix, retaining only unambiguous probe-to-symbol mappings and collapsing duplicate probes by MaxMean before limma. It does not make different array platforms, preprocessing histories or probe definitions comparable by itself.</p>
    </details>
  </section>

  <section aria-labelledby="meta-analysis">
    <h2 id="meta-analysis">Meta-analysis</h2>
    <p>Multi-study mode requires a <code>dataset</code> column and at least two studies with both selected contrast arms and at least two samples per arm. Each eligible study receives a full DESeq2 fit using only <code>~ selected_factor</code>; HTSFilter then filters that fitted object, and results are extracted with independent filtering disabled. The workflow does not refit DESeq2 after HTSFilter. A requested joint model is recorded separately and may be <code>~ selected_factor + dataset</code>; it is not the formula used for the per-study fits.</p>
    <p>Only genes shared by every eligible study and complete for p-value, unshrunken log2 fold change and its standard error are combined. Studies or genes that fail those requirements remain documented in the eligibility record instead of being silently treated as evidence for no effect.</p>
    <details class="method-detail">
      <summary>Combined p-values and the concordance rule</summary>
      <p>For a shared complete-case gene, the workflow combines the per-study two-sided Wald p-values by a replicate-weighted inverse-normal statistic. With total included-study sample count <em>n</em><sub>j</sub>, its weight is <em>w</em><sub>j</sub> = √(n<sub>j</sub> / Σn<sub>j</sub>). The statistic is <em>Z</em> = Σw<sub>j</sub> × <code>qnorm(1 − p<sub>j</sub>)</code>, and the combined upper-tail p-value is <code>pnorm(Z, lower.tail = FALSE)</code>. The implementation evaluates the quantile and tail probability directly, avoiding precision loss from explicitly forming <code>1 − p</code> for very small p-values. These weights use total selected samples only; they are not inverse-variance, study-quality or arm-balance weights.</p>
      <p>A combined adjusted p-value is calculated with Benjamini–Hochberg only for the family whose unshrunken effects have matching, nonzero signs in every included study. Rows with discordant or zero signs retain their raw combined p-value but have no <code>combined_padj</code> and are never meta-DEGs. Therefore <code>meta_sig</code> means <code>combined_padj &lt; alpha</code>; no log2-fold-change threshold is added. The matching-sign restriction is determined from the observed effects, so this documentation does not assert FDR calibration for arbitrary datasets. The p-value method is from <a href="https://doi.org/10.1186/1471-2105-15-91">Rau, Marot and Jaffrézic (2014)</a>; HTSFilter is described by <a href="https://doi.org/10.1093/bioinformatics/btt350">Rau et al. (2013)</a>.</p>
    </details>
    <details class="method-detail">
      <summary>Pooled effects are a second analysis</summary>
      <p>The workflow separately pools unshrunken log2 fold changes and their standard errors with <code>metafor</code>: fixed effect for two studies and DerSimonian–Laird random effects for three or more. Standard errors are floored at 1e-6 for the fit. Its <code>rem_padj</code> uses Benjamini–Hochberg across every estimable pooled-effect test, including discordant or zero-sign rows. This answers a pooled-effect hypothesis, not the inverse-normal combined-p hypothesis. With two studies, <code>tau2</code>, <code>I2</code> and <code>QEp</code> are deliberately <code>NA</code> because the workflow does not report heterogeneity fields with so little information. The default metafor inference is z-based Wald inference; pooled estimates and intervals are exploratory with few studies, where nominal coverage and error control can be unreliable. See <a href="https://search.r-project.org/CRAN/refmans/metafor/html/misc-recs.html">metafor’s recommendations</a>. The model implementation follows <a href="https://doi.org/10.18637/jss.v036.i03">Viechtbauer (2010)</a>.</p>
      <p>Results produced before v0.34.0 need recomputation before their pooled adjusted values are interpreted: <code>rem_padj</code> already existed, but now uses Benjamini–Hochberg across every estimable pooled test. The matching-direction combined calls are unchanged.</p>
    </details>
    <p>Neither a combined p-value nor a pooled effect proves replication, causality, independence of studies or biological consistency outside the included datasets. Inspect study eligibility, identifiers, reference compatibility, effect directions, covariates and heterogeneity before making those claims.</p>
  </section>

  <section aria-labelledby="annotation-reporting">
    <h2 id="annotation-reporting">Annotation and enrichment</h2>
    <p>The workflow annotates supported identifiers through GTF-derived fields and Bioconductor annotation resources, and can run GO, KEGG and custom-set enrichment with clusterProfiler. The optional g:Profiler route sends queries to its online service; its retrieved database content is time-dependent and is recorded with the run rather than assigned a package-lock version. Enrichment summarizes annotations among a selected gene set; it does not establish a pathway mechanism or validate a differential-expression result.</p>
    <details class="method-detail">
      <summary>Over-representation, ranked enrichment and sample-level scores</summary>
      <p>Over-representation analysis tests the selected up- or down-regulated list against its applicable annotated background. The background and foreground can shrink when identifiers are unmapped or ambiguous, so inspect the recorded effective populations rather than treating the configured list size as the tested universe. Ranked GSEA instead uses the complete eligible differential-expression table and the engine-specific signed statistic; it tests whether set members concentrate near either end of that ranking. g:Profiler supplies an ORA-only GO route, so it does not create the local ranked-GSEA result. Optional GSVA scores samples against a supplied custom GMT using the log-scale matrix already appropriate to the route: VST for DESeq2, logCPM for voom/edgeR, or log2 intensity for microarrays.</p>
    </details>
    <details class="method-detail">
      <summary>STRING and cross-study enrichment</summary>
      <p>The optional STRING network maps the selected genes to STRING identifiers, retrieves their association graph and keeps edges with <code>combined_score</code> at or above the configured threshold, default 400 on STRING’s 0–1000 scale. The score integrates physical and functional evidence; an edge is not proof of a direct physical interaction. The graph can be empty when mapping, organism coverage or the online service is unavailable, and the report records that state.</p>
      <p>In multi-study mode, cross-study enrichment compares per-study and convergent gene sets on one shared universe. Per-study enrichment is a separate opt-in step and is off by default because it runs enrichment for each eligible study. These summaries do not turn the combined result into an independent replication experiment.</p>
    </details>
  </section>

  <section aria-labelledby="tool-versions">
    <h2 id="tool-versions">Tool versions</h2>
    <p>These selected direct tools and main analysis or figure packages are pinned in the <a href="${escape(versions.lock_url)}">${escape(versions.release)} linux-64 full environment lock</a>. They identify the released workflow environment, not a claim that every platform or existing project ran these exact builds. The <a href="${escape(versions.lock_url)}">full lock</a> contains transitive packages; online database content is separate.</p>
    <table><thead><tr><th scope="col">Tool / package</th><th scope="col">Pinned version</th></tr></thead><tbody>${versionRows}</tbody></table>
  </section>`
};
