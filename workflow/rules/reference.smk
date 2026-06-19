rule validate_reference:
    input:
        "checks/00_project_setup.json"
    output:
        "checks/05_reference_validation.json"
    benchmark:
        "benchmarks/05_reference_validation.tsv"
    shell:
        "python workflow/scripts/validate_reference.py --config config/config.yaml --out {output}"

rule star_index:
    input:
        "checks/05_reference_validation.json"
    output:
        "references/star_index.done"
    benchmark:
        "benchmarks/star_index.tsv"
    shell:
        "python workflow/scripts/touch_report.py --out {output} --message 'STAR index placeholder. Use STAR --runMode genomeGenerate in production.'"
