#!/usr/bin/env python3
# Keep / discard / separate mitochondrial and chloroplast (organellar) genes before
# differential expression. Organellar genes (especially highly expressed mt/plastid
# transcripts) inflate library size and skew DESeq2 size-factor normalization; this
# step optionally removes them from the count matrix and, in "separate" mode, writes
# the organellar subset plus a per-sample organellar-fraction summary.
#
# Organellar contigs are identified from the genome FASTA headers (description matching
# mitochondrion/chloroplast/plastid/apicoplast, or a short contig name such as MT / Pt).
# Genes are mapped to their contig from the counts.txt Chr column (featureCounts) or,
# when that is unavailable (Salmon writes Chr=NA), from the GTF.
import argparse
import re
import shutil
from pathlib import Path

ORG_DESC = re.compile(r"mitochond|chloroplast|plastid|apicoplast|kinetoplast", re.I)
PLASTID = re.compile(r"chloroplast|plastid", re.I)
MITO = re.compile(r"mitochond", re.I)
# Short organellar contig names used by Ensembl / UCSC-style assemblies.
ORG_NAME = {"mt", "chrm", "m", "mtdna", "pt", "chrpt", "mito", "mitochondrion",
            "mitochondrion_genome", "chloroplast", "chloroplast_genome", "plastid"}


def organellar_contigs(genome_fa):
    # Map each organellar contig id -> "mito" | "plastid" | "organellar".
    out = {}
    if not genome_fa or not Path(genome_fa).exists():
        return out
    with open(genome_fa, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith(">"):
                continue
            header = line[1:].rstrip()
            cid = header.split()[0] if header.split() else ""
            is_org = bool(ORG_DESC.search(header)) or cid.lower() in ORG_NAME
            if not is_org:
                continue
            if PLASTID.search(header) or cid.lower() in ("pt", "chrpt", "chloroplast", "plastid"):
                out[cid] = "plastid"
            elif MITO.search(header) or cid.lower() in ("mt", "chrm", "m", "mtdna", "mito"):
                out[cid] = "mito"
            else:
                out[cid] = "organellar"
    return out


def gene_contig_from_gtf(gtf, gene_ids):
    # gene_id -> seqname (first occurrence) from the GTF.
    want = set(gene_ids)
    out = {}
    if not gtf or not Path(gtf).exists():
        return out
    with open(gtf, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 9:
                continue
            m = re.search(r'gene_id "([^"]+)"', parts[8])
            if not m:
                continue
            gid = m.group(1)
            if gid in want and gid not in out:
                out[gid] = parts[0]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--counts", required=True)
    ap.add_argument("--genome", default="")
    ap.add_argument("--gtf", default="")
    ap.add_argument("--mode", required=True, choices=["keep", "discard", "separate"])
    ap.add_argument("--out-counts", required=True)
    ap.add_argument("--organellar-dir", default="")
    ap.add_argument("--log", default="")
    a = ap.parse_args()

    logs = []

    def log(msg):
        logs.append(msg)

    with open(a.counts, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    comment = [ln for ln in lines if ln.startswith("#")]
    body = [ln for ln in lines if not ln.startswith("#")]
    header = body[0]
    rows = body[1:]
    cols = header.split("\t")
    chr_idx = cols.index("Chr") if "Chr" in cols else 1
    sample_cols = cols[6:]  # Geneid Chr Start End Strand Length <samples...>

    gene_ids = [r.split("\t")[0] for r in rows]
    org_contigs = organellar_contigs(a.genome)
    log(f"organellar contigs from genome: {org_contigs if org_contigs else 'none found'}")

    # gene -> contig: prefer the Chr column; fall back to the GTF when Chr is unusable.
    chr_vals = [r.split("\t")[chr_idx] if len(r.split("\t")) > chr_idx else "NA" for r in rows]
    chr_usable = any(v not in ("", "NA") for v in chr_vals)
    gtf_map = {}
    if not chr_usable:
        gtf_map = gene_contig_from_gtf(a.gtf, gene_ids)
        log(f"Chr column unusable (Salmon); mapped {len(gtf_map)} genes to contigs via GTF")

    def gene_kind(i):
        # returns "mito" | "plastid" | "organellar" | None
        contigs = []
        if chr_usable:
            contigs = [c for c in chr_vals[i].split(";") if c]
        else:
            g = gtf_map.get(gene_ids[i])
            if g:
                contigs = [g]
        kinds = {org_contigs[c] for c in contigs if c in org_contigs}
        if not kinds:
            return None
        if "plastid" in kinds:
            return "plastid"
        if "mito" in kinds:
            return "mito"
        return "organellar"

    flags = [gene_kind(i) for i in range(len(rows))]
    org_idx = [i for i, f in enumerate(flags) if f is not None]
    n_mito = sum(1 for f in flags if f == "mito")
    n_plastid = sum(1 for f in flags if f == "plastid")
    log(f"organellar genes: {len(org_idx)} (mito={n_mito}, plastid={n_plastid}, other={len(org_idx)-n_mito-n_plastid})")

    Path(a.out_counts).parent.mkdir(parents=True, exist_ok=True)

    if a.mode == "keep" or not org_idx:
        shutil.copyfile(a.counts, a.out_counts)
        if a.mode != "keep":
            log("no organellar genes detected; nuclear counts == input")
    else:
        keep_rows = [rows[i] for i in range(len(rows)) if flags[i] is None]
        with open(a.out_counts, "w", encoding="utf-8") as out:
            for c in comment:
                out.write(c + "\n")
            out.write(header + "\n")
            out.write("\n".join(keep_rows) + "\n")
        log(f"wrote nuclear counts: {len(keep_rows)} genes (removed {len(org_idx)} organellar)")

    if a.mode == "separate" and a.organellar_dir:
        od = Path(a.organellar_dir)
        od.mkdir(parents=True, exist_ok=True)
        org_rows = [rows[i] for i in org_idx]
        with open(od / "organellar_counts.txt", "w", encoding="utf-8") as out:
            for c in comment:
                out.write(c + "\n")
            out.write(header + "\n")
            if org_rows:
                out.write("\n".join(org_rows) + "\n")
        # per-sample organellar fraction (a standard QC metric)
        def col_sums(idx_list):
            sums = [0] * len(sample_cols)
            for i in idx_list:
                parts = rows[i].split("\t")
                for j in range(len(sample_cols)):
                    try:
                        sums[j] += int(float(parts[6 + j]))
                    except (ValueError, IndexError):
                        pass
            return sums
        total = col_sums(range(len(rows)))
        mito = col_sums([i for i in org_idx if flags[i] == "mito"])
        plastid = col_sums([i for i in org_idx if flags[i] == "plastid"])
        org_tot = col_sums(org_idx)
        with open(od / "organellar_summary.tsv", "w", encoding="utf-8") as out:
            out.write("sample\ttotal_counts\tmito_counts\tplastid_counts\torganellar_counts\t"
                      "mito_pct\tplastid_pct\torganellar_pct\n")
            for j, s in enumerate(sample_cols):
                tt = total[j] or 1
                out.write(f"{s}\t{total[j]}\t{mito[j]}\t{plastid[j]}\t{org_tot[j]}\t"
                          f"{100*mito[j]/tt:.3f}\t{100*plastid[j]/tt:.3f}\t{100*org_tot[j]/tt:.3f}\n")
        log(f"separate: wrote organellar_counts.txt ({len(org_rows)} genes) + organellar_summary.tsv")

    text = "\n".join(logs)
    print(text)
    if a.log:
        Path(a.log).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
