import sys
from pathlib import Path

from app.core.project import ProjectManager
from app.core.sra_metadata import fetch_ena_metadata, metadata_to_samples
from app.core.metadata import save_metadata

workdir = Path(sys.argv[1])
mgr = ProjectManager()
root = mgr.create_project("fgval", workdir)

# Fetch ENA metadata for the SRP039087 runs (dogfoods the new fetcher).
runs = ["SRR1179892", "SRR1179893", "SRR1179894", "SRR1179895", "SRR1179896", "SRR1179897"]
meta = fetch_ena_metadata(runs)
samples = metadata_to_samples(meta)
# Assign condition from the ENA sample_title (PH-1_spores_* / PH-1_mycelium_*).
samples["condition"] = ["spore" if "spore" in t.lower() else "mycelium" for t in samples["sample_title"]]
samples = samples.sort_values("condition").reset_index(drop=True)
save_metadata(samples, root / "config" / "samples.auto_generated.tsv")
save_metadata(samples, root / "config" / "samples.tsv")

cfg = mgr.load_config(root)
cfg.input.type = "sra"
cfg.input.layout = "paired"
# F. graminearum PH-1 NCBI RefSeq reference (matches the catalog entry).
base = "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/240/135/GCF_000240135.3_ASM24013v3/GCF_000240135.3_ASM24013v3"
cfg.reference.mode = "preset"
cfg.reference.organism_name = "Fusarium graminearum PH-1"
cfg.reference.source = "NCBI RefSeq"
cfg.reference.release = "ASM24013v3"
cfg.reference.package_id = "GCF_000240135.3"
cfg.reference.genome_size_category = "fungal"
cfg.reference.genome_fasta = "references/genome.fa"
cfg.reference.annotation_file = "references/annotation.gtf"
cfg.reference.annotation_format = "gtf"
cfg.reference.genome_fasta_url = base + "_genomic.fna.gz"
cfg.reference.annotation_gtf_url = base + "_genomic.gtf.gz"
cfg.workflow.aligner = "STAR"
cfg.workflow.quantifier = "featureCounts"
cfg.deseq2.design_formula = "~ condition"
cfg.deseq2.reference_level = {"condition": "mycelium"}
cfg.deseq2.contrasts[0].name = "spore_vs_mycelium"
cfg.deseq2.contrasts[0].factor = "condition"
cfg.deseq2.contrasts[0].numerator = "spore"
cfg.deseq2.contrasts[0].denominator = "mycelium"
mgr.save_config(root, cfg)
print("ROOT=" + str(root))
print(samples[["sample_id", "condition", "layout", "read_count"]].to_string(index=False))
