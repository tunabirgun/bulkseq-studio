"""Translate desktop FASTQ paths at the WSL execution boundary."""
from __future__ import annotations

import re


def wsl_input_path(value: str, distro: str | None = None) -> str:
    text = str(value)
    portable = text.replace("\\", "/")
    match = re.match(r"^//wsl(?:\.localhost|\$)/([^/]+)(?:/(.*))?$", portable, re.IGNORECASE)
    if match:
        if distro and match[1].casefold() != distro.casefold():
            raise ValueError(
                f"FASTQ path belongs to WSL distribution '{match[1]}', but the project runs "
                f"in '{distro}'. Select a file in the project's distribution."
            )
        return "/" + (match[2] or "")
    if portable.lower().startswith(("/wsl.localhost/", "/wsl$/")):
        raise ValueError("A WSL UNC path needs two leading slashes: //wsl.localhost/<distribution>/...")
    if portable.startswith("//"):
        raise ValueError("A Windows network-share FASTQ path cannot be used inside WSL.")
    drive = re.match(r"^([A-Za-z]):/(.*)$", portable)
    if drive:
        return f"/mnt/{drive[1].lower()}/{drive[2]}"
    if re.match(r"^[A-Za-z]:", portable):
        raise ValueError("Use an absolute Windows FASTQ path, such as C:/reads/sample.fastq.gz.")
    return text if text.startswith("/") else portable
