# Download raw FASTQ files from the ENA URLs recorded in samples.tsv.
# One generic rule keyed on URL_BY_TARGET (built in the Snakefile).


rule download_fastq:
    output:
        "data/raw/{prefix}.fastq.gz",
    benchmark:
        "benchmarks/download_{prefix}.tsv"
    log:
        "logs/download_{prefix}.log",
    resources:
        # Cap how many downloads run at once (when the runner provides `downloads`), so many
        # samples don't open a flood of connections to ENA. Unlimited if not provided.
        downloads=1,
    retries: 3
    run:
        import hashlib
        import shutil
        import subprocess
        import time
        import urllib.request

        target = str(output[0])
        url = URL_BY_TARGET.get(target)
        expected_md5 = str(MD5_BY_TARGET.get(target, "")).strip().lower()
        if not url:
            raise ValueError(f"No download URL known for {target}")
        if not url.startswith(("http://", "https://", "ftp://")):
            url = "https://" + url
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        out_dir = os.path.dirname(target) or "."
        out_name = os.path.basename(target)

        with open(log[0], "w", encoding="utf-8") as handle:
            handle.write(f"DOWNLOAD {url} -> {target}\n")

        def _log(msg):
            with open(log[0], "a", encoding="utf-8") as handle:
                handle.write(msg + "\n")

        def _ok():
            return os.path.exists(target) and os.path.getsize(target) > 0

        def _via_aria2():
            # Modest connection count: several files download in parallel, so a high per-file
            # count floods ENA's per-IP limit and it refuses connections. -c resumes a partial
            # file, so a retry continues from where a refused transfer stopped, not from zero.
            cmd = [aria2, "-x", "4", "-s", "4", "-j", "1", "-c", "--file-allocation=none",
                   "--max-tries=3", "--retry-wait=10", "--timeout=60",
                   "--console-log-level=warn", "--summary-interval=0",
                   "-d", out_dir, "-o", out_name, url]
            with open(log[0], "a", encoding="utf-8") as handle:
                return subprocess.call(cmd, stdout=handle, stderr=subprocess.STDOUT)

        def _via_urllib():
            with urllib.request.urlopen(url, timeout=180) as response, open(target, "wb") as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)

        # Retry with exponential backoff. ENA transiently refuses connections under load;
        # aria2's -c resumes the partial file on each retry, so a download that reached 97%
        # before a refusal finishes on the next attempt instead of failing the run.
        aria2 = shutil.which("aria2c")
        ok = False
        for attempt in range(1, 6):
            try:
                if aria2:
                    rc = _via_aria2()
                    ok = (rc == 0 and _ok())
                    if not ok:
                        _log(f"aria2c attempt {attempt} failed (rc={rc}); will retry")
                else:
                    _via_urllib()
                    ok = _ok()
            except Exception as exc:
                _log(f"attempt {attempt} error: {exc}")
                ok = False
            if ok:
                break
            time.sleep(min(60, 10 * attempt))
        if not ok and aria2:
            # Last resort: a single urllib stream (most compatible), retried a few times.
            _log("aria2c exhausted; falling back to a single-stream download")
            try:
                os.remove(target + ".aria2")
            except OSError:
                pass
            for attempt in range(1, 4):
                try:
                    _via_urllib()
                    if _ok():
                        ok = True
                        break
                except Exception as exc:
                    _log(f"urllib fallback attempt {attempt} error: {exc}")
                time.sleep(15 * attempt)
        if not ok:
            raise ValueError(
                f"Download failed after retries for {target} ({url}). This is usually ENA "
                f"refusing connections under load — re-run to resume from the partial file.")

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
