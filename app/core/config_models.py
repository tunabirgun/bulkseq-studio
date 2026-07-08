from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.constants import APP_VERSION, WORKFLOW_VERSION


class ProjectConfig(BaseModel):
    name: str
    working_directory: str
    created_at: str = Field(default_factory=lambda: date.today().isoformat())
    app_version: str = APP_VERSION
    workflow_version: str = WORKFLOW_VERSION


class InputConfig(BaseModel):
    type: Literal["fastq", "sra", "mixed", "count_matrix", "microarray", "deseq2_results"] = "fastq"
    layout: Literal["paired", "single", "mixed", "unknown"] = "unknown"
    samples: str = "config/samples.tsv"
    sra_accessions: str = "config/sra_accessions.txt"
    # When type == count_matrix: a user-supplied gene x sample counts table; the
    # pipeline ingests it (skipping download/QC/alignment/featureCounts) into the
    # canonical results/counts/counts.txt and runs DESeq2 -> figures -> enrichment.
    count_matrix: str | None = None
    # When type == deseq2_results: a user-supplied DESeq2 results table; the pipeline
    # ingests it (skipping download/QC/alignment/featureCounts/DESeq2) into the canonical
    # results/deseq2/deseq2_results.csv and runs enrichment -> figures -> PPI. Outputs that
    # require raw/normalized counts (PCA, sample correlation, count heatmaps, GOI) are skipped.
    deseq2_results: str | None = None


class MicroarrayConfig(BaseModel):
    # GEO/GSE microarray input (input.type == "microarray"). The pipeline ingests
    # a normalized expression matrix (GEOquery series matrix, or RMA from raw CEL)
    # and runs limma -> the canonical DESeq2-shaped results so figures/enrichment/
    # GOI stay backend-agnostic. No genome alignment or reference is involved.
    gse_accession: str | None = None
    platform: str | None = None  # GEO platform id (GPL...)
    # geo_series_matrix / affy_cel download from GEO; local_matrix ingests a user-supplied
    # gene x sample expression matrix (any platform, already processed) — no GEO/network.
    source: Literal["geo_series_matrix", "affy_cel", "local_matrix"] = "geo_series_matrix"
    # Path to a local gene x sample expression matrix (used when source == "local_matrix").
    expression_matrix: str | None = None
    # auto: trust the submitter matrix (skip re-normalizing); rma: affy::rma on CEL.
    normalization: Literal["auto", "rma", "none"] = "auto"
    # auto: detect whether values are already log2 (GEO2R quantile heuristic).
    log2_transform: Literal["auto", "yes", "no"] = "auto"


class ReferenceConfig(BaseModel):
    mode: Literal["preset", "custom", "unset"] = "unset"
    organism_name: str = "unset"
    strain: str | None = None
    package_id: str | None = None
    source: str | None = None
    release: str | None = None
    genome_fasta: str | None = None
    annotation_file: str | None = None
    annotation_format: Literal["gtf", "gff3", "unset"] = "unset"
    genome_fasta_url: str | None = None
    annotation_gtf_url: str | None = None
    transcriptome_fasta: str | None = None
    protein_fasta: str | None = None
    star_index: str | None = None
    hisat2_index: str | None = None
    salmon_index: str | None = None
    genome_md5: str | None = None
    annotation_md5: str | None = None
    genome_size_category: str = "custom"


class WorkflowConfig(BaseModel):
    fastqc_pre_trim: bool = True
    trimming: bool = True
    # Trimmer for the adapter/quality step (count-based fastq/sra route). fastp is the
    # default; Trim Galore and Trimmomatic are opt-in alternatives that emit the same
    # trimmed reads, so the rest of the pipeline is unchanged.
    trimmer: Literal["fastp", "trim-galore", "trimmomatic"] = "fastp"
    fastqc_post_trim: bool = True
    rrna_filtering: bool = False
    # rRNA filtering tool (only used when rrna_filtering is on). SortMeRNA (reference-based,
    # default) or RiboDetector (reference-free, no database download).
    rrna_tool: Literal["sortmerna", "ribodetector"] = "sortmerna"
    # Contamination screening (FastQ Screen): optional QC that reports the % of reads matching
    # a panel of reference genomes (a report, not a filter). Off by default.
    contamination_screen: bool = False
    aligner: Literal["STAR", "HISAT2", "Salmon"] = "STAR"
    quantifier: Literal["featureCounts", "STAR_GeneCounts", "Salmon_tximport"] = "featureCounts"
    enrichment: bool = True
    figures: bool = True
    # GSVA sample-level gene-set activity scores. Organism-safe: runs only on the
    # user-supplied custom gene sets (gene_sets.custom_gene_sets), never a bundled
    # human collection, so it is valid for non-model organisms.
    gsva: bool = False
    # RSeQC extended alignment QC (read distribution + gene-body coverage). Needs a
    # genome BAM, so it is unavailable on the Salmon route.
    rseqc: bool = False
    # Differential-expression engine for count-based routes (fastq/sra/count_matrix).
    # DESeq2 is the default; limma-voom is an opt-in cross-check that emits the same
    # canonical artifacts. Microarray uses limma-trend and deseq2-results uploads
    # bypass DE, regardless of this value.
    de_engine: Literal["DESeq2", "limma-voom", "edgeR"] = "DESeq2"
    # Mitochondrial + chloroplast/plastid genes: keep them, discard them before DE, or
    # separate them into their own count subset (and run the main DE on nuclear genes only).
    organellar_genes: Literal["keep", "discard", "separate"] = "keep"


class FastpConfig(BaseModel):
    detect_adapter_for_pe: bool = True
    qualified_quality_phred: int = 15
    unqualified_percent_limit: int = 40
    length_required: int = 36
    trim_poly_g: bool = False
    # 3' poly-X (poly-A/poly-T) trimming, useful for 3'-biased / degraded libraries.
    trim_poly_x: bool = False


class TrimmomaticConfig(BaseModel):
    # Sliding-window quality trim (SLIDINGWINDOW:size:quality) and end quality
    # (LEADING/TRAILING). Min length reuses fastp.length_required. Defaults match the
    # widely used Trimmomatic PE recipe.
    sliding_window_size: int = 4
    sliding_window_quality: int = 15
    leading: int = 3
    trailing: int = 3


class RibodetectorConfig(BaseModel):
    # chunk_size * 1024 reads per batch (memory/speed trade-off). ensure: which class
    # is kept with high confidence (norrna keeps high-confidence non-rRNA reads).
    chunk_size: int = 256
    ensure: Literal["norrna", "rrna", "both", "none"] = "norrna"


class ContaminationConfig(BaseModel):
    # FastQ Screen: number of reads subsampled per sample for the screen (faster than
    # screening every read; the default is FastQ Screen's own).
    subset: int = 100000
    # Path to a user-provided FastQ Screen config (fastq_screen.conf) pointing at the bowtie2
    # genome indexes to screen against. Required when contamination screening is enabled; the
    # built-in --get_genomes auto-download is not used (broken upstream, multi-GB panel).
    conf: str | None = None


class SortmernaConfig(BaseModel):
    paired_mode: str = "paired_in"
    database: str | None = None


class StarConfig(BaseModel):
    twopass_mode: bool = False
    # Read filters. Defaults equal STAR's own defaults (stock behaviour); tighten
    # for ENCODE long-RNA with multimap_nmax=20 and mismatch_nover_read_lmax=0.04.
    multimap_nmax: int = 10
    mismatch_nover_read_lmax: float = 1.0
    extra: str = ""


class FeatureCountsConfig(BaseModel):
    feature_type: str = "exon"
    attribute_type: str = "gene_id"
    strandedness: int = 0

    @field_validator("strandedness")
    @classmethod
    def valid_strandedness(cls, value: int) -> int:
        if value not in {0, 1, 2}:
            raise ValueError("featureCounts strandedness must be 0, 1, or 2.")
        return value


class Contrast(BaseModel):
    name: str = "treated_vs_control"
    factor: str = "condition"
    numerator: str = "treated"
    denominator: str = "control"


class Deseq2Config(BaseModel):
    design_formula: str = "~ condition"
    reference_level: dict[str, str] = Field(default_factory=lambda: {"condition": "control"})
    contrasts: list[Contrast] = Field(default_factory=lambda: [Contrast()])
    alpha: float = 0.05
    lfc_threshold: float = 1.0
    min_count: int = 10
    lfc_shrinkage: bool = True
    shrinkage_method: str = "apeglm"

    @field_validator("lfc_threshold")
    @classmethod
    def nonneg_lfc(cls, value: float) -> float:
        if value < 0:
            raise ValueError("deseq2.lfc_threshold must be >= 0 (0 disables the fold-change filter).")
        return value

    @field_validator("alpha")
    @classmethod
    def valid_alpha(cls, value: float) -> float:
        if not 0 < value < 1:
            raise ValueError("deseq2.alpha must be between 0 and 1.")
        return value

    @field_validator("shrinkage_method")
    @classmethod
    def valid_shrinkage(cls, value: str) -> str:
        # The three lfcShrink() estimators. apeglm + ashr are conda packages in the
        # full env; restricting the value stops a typo or unsupported method from
        # reaching run_deseq2.R and erroring mid-run.
        allowed = {"apeglm", "ashr", "normal"}
        if value not in allowed:
            raise ValueError(f"deseq2.shrinkage_method must be one of {sorted(allowed)}.")
        return value


class GeneSetsConfig(BaseModel):
    custom_gene_list: str | None = None
    custom_gene_sets: str | None = None
    functional_annotation_table: str | None = None
    background_gene_list: str | None = None


class EnrichmentConfig(BaseModel):
    # Overrides the organism-derived enrichment database/keytype/KEGG code. Left
    # empty (None), the workflow falls back to the organism mapping in
    # workflow/rules/enrichment.smk. Distinct from workflow.enrichment (on/off).
    # Microarray mode sets keytype = SYMBOL (GPL annotation maps to gene symbols).
    orgdb: str | None = None
    keytype: str | None = None
    kegg_organism: str | None = None
    # backend default 'clusterprofiler' keeps the auto OrgDb->gprofiler->none chain;
    # setting 'gprofiler' forces the g:Profiler GO route even when an OrgDb loads.
    backend: Literal["clusterprofiler", "gprofiler"] = "clusterprofiler"
    gprofiler_organism: str | None = None


class PpiConfig(BaseModel):
    # Protein-protein interaction network (STRING) built from the DE / genes-of-
    # interest set. STRINGdb contacts string-db.org on every run (no offline mode),
    # so the rule degrades to empty outputs + a check when it is unreachable or the
    # organism has no STRING taxid.
    enabled: bool = True
    score_threshold: int = 400  # STRING combined-score cutoff (400 medium, 700 high)
    taxon: int | None = None    # NCBI taxid override; else derived from the organism
    seed_source: Literal["de", "goi"] = "de"
    string_version: str = "12.0"
    max_seed_genes: int = 400   # cap the DE seed set sent to STRING
    hub_label_count: int = 15   # how many top hub proteins to label on the figure


class FigureConfig(BaseModel):
    # Visual style applied to all DESeq2 figures (workflow/scripts/make_figures.R).
    palette: Literal["Blue-Red", "Viridis", "Magma", "Plasma", "Cividis",
                     "Spectral", "Red-Yellow-Blue", "Greyscale"] = "Blue-Red"
    point_size: float = 2.5
    base_font_size: int = 12
    font_family: str = ""
    label_bold: bool = False
    title_bold: bool = False
    gene_symbol_italic: bool = True  # gene symbols italic (HGNC convention) on figure labels + report DE tables
    volcano_top_n: int = 15
    heatmap_top_n: int = 30
    pca_ntop: int = 500
    width_in: float = 6.0
    height_in: float = 5.0
    dpi: int = 300
    # UI display unit for the width/height fields; width_in/height_in stay the
    # canonical inches the R export uses (px is converted via dpi).
    dimension_unit: Literal["in", "cm", "px"] = "in"
    # Volcano de-squeeze (W2). volcano_y_cap 0 = auto (quantile); fractions/alphas
    # are read NULL-safe by getp() in make_figures.R, so defaults reproduce behaviour.
    volcano_y_cap: float = 0.0            # 0 = auto cap via quantile
    volcano_y_cap_quantile: float = 0.995
    volcano_cap_headroom: float = 0.10
    volcano_neglogp_floor: float = 320.0  # finite clamp for padj==0/Inf
    volcano_point_scale: float = 0.55     # sig point size = point_size x this
    volcano_point_alpha: float = 0.55
    scatter_alpha_fg: float = 0.8
    scatter_alpha_bg: float = 0.25
    pca_fixed_aspect: bool = False  # opt-in; coord_fixed squeezes PC1-dominant PCA
    sample_labels: bool = True  # per-sample text on PCA + sample-distance/correlation heatmaps; off declutters many-sample (microarray) runs
    heatmap_zlim: float = 2.5             # symmetric z cap (top-DEG)
    heatmap_cell_height: float = 12.0     # pt/row, pins heatmap aspect
    heatmap_fontsize_row: int = 0         # 0 = auto (base-4)
    heatmap_number_fontsize: int = 0      # 0 = auto (0.6 x base)
    heatmap_number_format: str = "%.2f"
    enrich_show_category: int = 15
    enrich_cnet_category: int = 5
    enrich_emap_category: int = 15
    enrich_label_wrap: int = 40
    gsea_line_color: str = ""             # "" = palette-derived
    ppi_layout: str = "fr"  # force-directed (Fruchterman-Reingold) default
    ppi_node_max_size: float = 11.0
    size_overrides: dict[str, tuple[float, float]] = Field(default_factory=dict)
    rasterize_points: bool = False        # OPTIONAL ggrastr (gated, default off)

    @field_validator("point_size", "width_in", "height_in",
                     "heatmap_zlim", "heatmap_cell_height", "ppi_node_max_size")
    @classmethod
    def positive_float(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Figure dimensions and point size must be positive.")
        return value

    @field_validator("base_font_size", "dpi", "volcano_top_n", "heatmap_top_n", "pca_ntop",
                     "enrich_show_category", "enrich_cnet_category", "enrich_emap_category",
                     "enrich_label_wrap")
    @classmethod
    def positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Figure counts, font size, and dpi must be positive integers.")
        return value


class ResourcesConfig(BaseModel):
    profile: Literal["low", "balanced", "high", "custom"] = "balanced"
    total_threads: int = 4
    total_memory_gb: int = 8
    temp_dir: str = "tmp"
    keep_intermediate: bool = False


class RuleThreads(BaseModel):
    fasterq_dump: int = 4
    fastqc: int = 1
    fastp: int = 4
    sortmerna: int = 4
    star_index: int = 4
    star_align: int = 4
    hisat2_align: int = 4
    salmon_quant: int = 4
    featurecounts: int = 4
    deseq2: int = 2
    multiqc: int = 1


class RuleMemoryGb(BaseModel):
    fasterq_dump: int = 8
    fastqc: int = 1
    fastp: int = 4
    sortmerna: int = 12
    star_index: int = 24
    star_align: int = 24
    hisat2_align: int = 8
    salmon_quant: int = 8
    featurecounts: int = 8
    deseq2: int = 12
    multiqc: int = 2


class AppConfig(BaseModel):
    project: ProjectConfig
    input: InputConfig = Field(default_factory=InputConfig)
    microarray: MicroarrayConfig = Field(default_factory=MicroarrayConfig)
    reference: ReferenceConfig = Field(default_factory=ReferenceConfig)
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)
    fastp: FastpConfig = Field(default_factory=FastpConfig)
    trimmomatic: TrimmomaticConfig = Field(default_factory=TrimmomaticConfig)
    ribodetector: RibodetectorConfig = Field(default_factory=RibodetectorConfig)
    contamination: ContaminationConfig = Field(default_factory=ContaminationConfig)
    sortmerna: SortmernaConfig = Field(default_factory=SortmernaConfig)
    star: StarConfig = Field(default_factory=StarConfig)
    featurecounts: FeatureCountsConfig = Field(default_factory=FeatureCountsConfig)
    deseq2: Deseq2Config = Field(default_factory=Deseq2Config)
    gene_sets: GeneSetsConfig = Field(default_factory=GeneSetsConfig)
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)
    ppi: PpiConfig = Field(default_factory=PpiConfig)
    figures_style: FigureConfig = Field(default_factory=FigureConfig)
    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)
    rule_threads: RuleThreads = Field(default_factory=RuleThreads)
    rule_memory_gb: RuleMemoryGb = Field(default_factory=RuleMemoryGb)


def default_config(project_name: str, project_root: Path) -> AppConfig:
    return AppConfig(project=ProjectConfig(name=project_name, working_directory=str(project_root)))
