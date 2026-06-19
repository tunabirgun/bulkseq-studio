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
    type: Literal["fastq", "sra", "mixed", "count_matrix"] = "fastq"
    layout: Literal["paired", "single", "mixed", "unknown"] = "unknown"
    samples: str = "config/samples.tsv"
    sra_accessions: str = "config/sra_accessions.txt"


class ReferenceConfig(BaseModel):
    mode: Literal["preset", "custom", "unset"] = "unset"
    organism_name: str = "unset"
    strain: str | None = None
    package_id: str | None = None
    genome_fasta: str | None = None
    annotation_file: str | None = None
    annotation_format: Literal["gtf", "gff3", "unset"] = "unset"
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
    fastqc_post_trim: bool = True
    rrna_filtering: bool = False
    repair_pairs: bool = False
    aligner: Literal["STAR", "HISAT2", "Salmon"] = "STAR"
    quantifier: Literal["featureCounts", "STAR_GeneCounts", "Salmon_tximport", "htseq-count"] = "featureCounts"
    differential_expression: Literal["DESeq2", "edgeR", "limma-voom"] = "DESeq2"
    enrichment: bool = True
    figures: bool = True
    custom_gene_list_analysis: bool = True


class FastpConfig(BaseModel):
    detect_adapter_for_pe: bool = True
    qualified_quality_phred: int = 15
    unqualified_percent_limit: int = 40
    length_required: int = 36
    trim_poly_g: bool = False


class SortmernaConfig(BaseModel):
    enabled: bool = False
    paired_mode: str = "paired_in"
    database: str | None = None


class StarConfig(BaseModel):
    sjdb_overhang: str | int = "auto"
    genomeSAindexNbases: str | int = "auto"
    twopass_mode: bool = False
    outSAMtype: str = "BAM SortedByCoordinate"
    quantMode: str = "GeneCounts"


class FeatureCountsConfig(BaseModel):
    feature_type: str = "exon"
    attribute_type: str = "gene_id"
    strandedness: int = 0
    count_read_pairs: bool = True

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
    lfc_shrinkage: bool = True
    shrinkage_method: str = "apeglm"


class GeneSetsConfig(BaseModel):
    custom_gene_list: str | None = None
    custom_gene_sets: str | None = None
    functional_annotation_table: str | None = None
    background_gene_list: str | None = None


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
    reference: ReferenceConfig = Field(default_factory=ReferenceConfig)
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)
    fastp: FastpConfig = Field(default_factory=FastpConfig)
    sortmerna: SortmernaConfig = Field(default_factory=SortmernaConfig)
    star: StarConfig = Field(default_factory=StarConfig)
    featurecounts: FeatureCountsConfig = Field(default_factory=FeatureCountsConfig)
    deseq2: Deseq2Config = Field(default_factory=Deseq2Config)
    gene_sets: GeneSetsConfig = Field(default_factory=GeneSetsConfig)
    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)
    rule_threads: RuleThreads = Field(default_factory=RuleThreads)
    rule_memory_gb: RuleMemoryGb = Field(default_factory=RuleMemoryGb)


def default_config(project_name: str, project_root: Path) -> AppConfig:
    return AppConfig(project=ProjectConfig(name=project_name, working_directory=str(project_root)))
