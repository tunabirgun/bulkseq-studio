# Download raw FASTQ files from the ENA URLs recorded in samples.tsv.
# One generic rule keyed on URL_BY_TARGET (built in the Snakefile).


rule download_fastq:
    output:
        "data/raw/{prefix}.fastq.gz",
    benchmark:
        "benchmarks/download_{prefix}.tsv"
    log:
        "logs/download_{prefix}.log",
    run:
        import urllib.request

        target = str(output[0])
        url = URL_BY_TARGET.get(target)
        if not url:
            raise ValueError(f"No download URL known for {target}")
        if not url.startswith(("http://", "https://", "ftp://")):
            url = "https://" + url
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(log[0], "w", encoding="utf-8") as handle:
            handle.write(f"DOWNLOAD {url} -> {target}\n")
        with urllib.request.urlopen(url, timeout=180) as response, open(target, "wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
