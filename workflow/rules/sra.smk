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
        import hashlib
        import shutil
        import subprocess
        import urllib.request

        target = str(output[0])
        url = URL_BY_TARGET.get(target)
        expected_md5 = str(MD5_BY_TARGET.get(target, "")).strip().lower()
        if not url:
            raise ValueError(f"No download URL known for {target}")
        if not url.startswith(("http://", "https://", "ftp://")):
            url = "https://" + url
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(log[0], "w", encoding="utf-8") as handle:
            handle.write(f"DOWNLOAD {url} -> {target}\n")

        def _via_urllib():
            with urllib.request.urlopen(url, timeout=180) as response, open(target, "wb") as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)

        # aria2c opens many parallel connections (ENA throttles per connection), which is far
        # faster than a single urllib stream, and resumes partial files. Fall back to urllib
        # when aria2c is not installed, so the download route still works everywhere.
        aria2 = shutil.which("aria2c")
        if aria2:
            out_dir = os.path.dirname(target) or "."
            out_name = os.path.basename(target)
            cmd = [aria2, "-x", "16", "-s", "16", "-j", "1", "-c", "--file-allocation=none",
                   "--max-tries=5", "--retry-wait=5", "--console-log-level=warn",
                   "--summary-interval=0", "-d", out_dir, "-o", out_name, url]
            with open(log[0], "a", encoding="utf-8") as handle:
                rc = subprocess.call(cmd, stdout=handle, stderr=subprocess.STDOUT)
            if rc != 0 or not (os.path.exists(target) and os.path.getsize(target) > 0):
                with open(log[0], "a", encoding="utf-8") as handle:
                    handle.write(f"aria2c exit {rc}; falling back to urllib\n")
                _via_urllib()
        else:
            _via_urllib()

        # Verify integrity against ENA's checksum so a fast/parallel download that was truncated
        # or corrupted is caught (a scientific-validity guarantee, not just a convenience).
        if expected_md5:
            digest = hashlib.md5()
            with open(target, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            got = digest.hexdigest().lower()
            if got != expected_md5:
                os.remove(target)
                raise ValueError(
                    f"MD5 mismatch for {target}: expected {expected_md5}, got {got}. "
                    f"The download was corrupted; re-run to retry.")
            with open(log[0], "a", encoding="utf-8") as handle:
                handle.write(f"MD5 OK ({got})\n")

        # Record the checksum outcome for the results report (per-file sidecar, no DAG output).
        status_dir = os.path.join("results", "qc", "checksums")
        os.makedirs(status_dir, exist_ok=True)
        base = os.path.basename(target)
        with open(os.path.join(status_dir, base + ".txt"), "w", encoding="utf-8") as handle:
            if expected_md5:
                handle.write(f"PASS\t{base}\t{expected_md5}\n")
            else:
                handle.write(f"NO_CHECKSUM\t{base}\t\n")
