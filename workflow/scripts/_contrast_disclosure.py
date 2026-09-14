"""Disclosure of the one contrast the pipeline actually analyses.

workflow/rules/deseq2.smk takes contrasts[0] and ignores the rest, so a project configured with
several comparisons produces one result set whose outputs name none of the others. The launch
warning, the run summary, the study design and the HTML report all state that in the same words
so the omission cannot be read as a full multi-contrast analysis.
"""

from __future__ import annotations

ANALYSED_LABEL = "Contrast analysed"
IGNORED_LABEL = "Configured but not analysed"
SINGLE_CONTRAST_SENTENCE = "Only the first configured contrast is analysed."
_GUIDANCE = ("Run each remaining comparison as its own project if you need its results, "
             "or remove it from the configuration.")


def contrast_label(contrast) -> str:
    """Name one configured contrast the way the reports name it."""
    if not isinstance(contrast, dict):
        return str(contrast)
    numerator = contrast.get("numerator")
    denominator = contrast.get("denominator")
    name = contrast.get("name")
    if numerator and denominator:
        label = f"{numerator} vs {denominator}"
        factor = contrast.get("factor")
        if factor:
            label += f" (factor: {factor})"
        if name:
            label += f" [{name}]"
        return label
    return str(name or contrast)


def split_contrasts(contrasts) -> tuple[str | None, list[str]]:
    """Return (analysed label, ignored labels) for a configured contrast list."""
    if not isinstance(contrasts, list) or not contrasts:
        return None, []
    return contrast_label(contrasts[0]), [contrast_label(c) for c in contrasts[1:]]


def ignored_text(contrasts) -> str | None:
    """Render the ignored contrasts, or None when every configured contrast is analysed."""
    _, ignored = split_contrasts(contrasts)
    return "; ".join(ignored) if ignored else None


def single_contrast_notice(contrasts) -> str | None:
    """One sentence for the launch log and the project-setup check, or None when not needed."""
    analysed, ignored = split_contrasts(contrasts)
    if not ignored:
        return None
    return (f"{SINGLE_CONTRAST_SENTENCE} {ANALYSED_LABEL}: {analysed}. "
            f"{IGNORED_LABEL}: {'; '.join(ignored)}. {_GUIDANCE}")
