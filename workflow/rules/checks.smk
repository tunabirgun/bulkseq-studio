rule validate_project:
    input:
        "config/config.yaml",
        "config/samples.tsv"
    output:
        "checks/00_project_setup.json"
    benchmark:
        "benchmarks/00_project_setup.tsv"
    shell:
        "python workflow/scripts/validate_project.py --config {input[0]} --samples {input[1]} --out {output}"

rule aggregate_sanity_checks:
    input:
        "checks/00_project_setup.json"
    output:
        "checks/sanity_checks.txt"
    benchmark:
        "benchmarks/sanity_checks.tsv"
    shell:
        "python workflow/scripts/aggregate_sanity_checks.py --checks checks --out {output}"
