from __future__ import annotations

import shlex


def parse_star_extra(value: str) -> list[str]:
    if not isinstance(value, str) or "\0" in value:
        raise ValueError("star.extra must be text without null characters.")
    try:
        arguments = shlex.split(value, comments=False, posix=True)
    except ValueError as exc:
        raise ValueError("star.extra requires balanced quotes and complete escapes.") from exc
    if any(not argument for argument in arguments):
        raise ValueError("star.extra does not support empty quoted arguments.")
    if any("\r" in argument or "\n" in argument for argument in arguments):
        raise ValueError("star.extra arguments must not contain embedded line breaks.")
    options = {argument.split("=", 1)[0] for argument in arguments if argument.startswith("--")}
    if "--" in options:
        raise ValueError("star.extra does not support bare -- or --= options.")
    if any(any(character.isspace() for character in option) for option in options):
        raise ValueError("star.extra option names must not contain whitespace.")
    reserved = {"--readFilesCommand", "--sysShell", "--parametersFiles", "--readFilesPrefix", "--readFilesManifest"}
    blocked = sorted(options & reserved)
    if blocked:
        raise ValueError("star.extra cannot set command-execution options: " + ", ".join(blocked))
    return arguments
