# Contributing to BulkSeq Studio

Documentation corrections, reproducible bug reports and focused code changes are welcome. Use [Discussions](https://github.com/tunabirgun/bulkseq-studio/discussions) for questions and early ideas; open an [issue](https://github.com/tunabirgun/bulkseq-studio/issues/new/choose) when there is a specific defect or proposed feature to track.

## Report a problem

Include the application version, operating system and WSL distribution if applicable, input route, steps to reproduce, expected behavior and actual behavior. For a path problem, describe whether the project and inputs are on a Windows drive or the WSL filesystem. Use a synthetic path or a bundled example instead of exposing your own directory names.

Share only the smallest relevant, sanitized log excerpt. Do not upload raw sequencing data, patient or donor information, private sample identifiers, credentials or entire project folders. Screenshots can expose these details too. A synthetic example or a link to already public data is preferable.

## Propose a change

Discuss substantial changes before implementing them. State the user problem, the affected workflow and how the change can be checked. For a scientific-method change, describe the model or quantity being estimated, cite the method, and distinguish a proposed improvement from a demonstrated result.

Keep pull requests focused and preserve unrelated files. Explain the before/after behavior and the tests you ran. A bug fix should include a regression that fails on the original defect and passes after the correction. Do not weaken a check just to make a failing test green.

## Set up a development environment

Use Python 3.12 for parity with CI. From the repository root, create a separate environment with `python -m venv .venv`, then activate it with `.venv\Scripts\Activate.ps1` on Windows or `source .venv/bin/activate` on Linux. Install the reviewed application dependencies with `python -m pip install -r requirements.txt -c constraints.txt` and the test runner with `python -m pip install "pytest>=8.2"`. Report-example tests additionally use `python -m pip install -r docs_src/reproduction/report-requirements.txt`.

Run the affected tests with `python -m pytest -q tests/<test_file>.py`, replacing the placeholder with the relevant file. Run the full suite with `python -m pytest -q` before proposing changes that cross modules. Tests that need an unavailable R or platform runtime may skip locally; report those skips. Hosted CI runs the platform matrix and mandatory R checks. Some tests and analysis checks require the configured Linux backend; installing the GUI's Python packages alone does not provide it.

Windows analysis uses WSL2; native Linux is also supported. macOS tests cover the GUI/configuration layer, not a supported macOS analysis backend. See the [setup guide](https://tunabirgun.github.io/bulkseq-studio/guide.html) for backend requirements. Do not replace existing research environments to run tests.

## Documentation and scientific outputs

Edit handbook content in `docs_src/`, then run `node docs_src/build.mjs` and include the corresponding generated `docs/` changes. Do not edit generated pages alone. Keep examples clearly labelled as synthetic, illustrative or biological, as appropriate; a rendered report is not proof of a completed analysis.

Changes to workflow inputs, formulas, statistics, identifiers, annotations or filtering need checks against an independent reference and meaningful negative cases. State which results could change and which biological examples were actually rerun. Package agreement and passing unit tests do not establish biological accuracy.

Keep application/workflow versions, installer metadata, documentation and `CITATION.cff` aligned when preparing a release. Record changes in the changelog; scientific-output changes must be identified explicitly. Maintainers handle release publication after the required checks pass.

## Repository hygiene

Include only source, documentation and appropriate small test fixtures. Keep raw data, credentials, machine-specific configuration, private manuscripts, generated run outputs and local review material out of commits. Check the staged diff and run `git diff --check` before submitting. Contributions are made under the project's [MIT License](LICENSE).
