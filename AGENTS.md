# Repository instructions

Use Chinese for replies. Preserve technical names in their original form.

Use English for code, comments, docstrings, log messages, and new filenames. Prefer simple, reliable implementations and reuse existing modules, conventions, and dependencies.

Keep public documentation focused on implemented behavior and measured results. Use concise, factual wording. Localized interface content follows the selected interface language.

Keep code limited to functional implementation and concise technical explanations of inputs, outputs, constraints, algorithms, formulas, data definitions, boundary conditions, state transitions, or non-obvious behavior. Keep project background, requirements, progress, change history, debugging narratives, design comparisons, experiment conclusions, and delivery summaries out of code, comments, and docstrings.

Put all new design explanations, plans, development records, environment records, experiment reports, evaluations, and optimization summaries under project_notes/. Update existing topical files. Create new records only when substantive content exists. Keep explanatory Markdown under project_notes/ local and ignored by Git. Keep README.md in English and README_CN.md in Simplified Chinese, each self-contained with the project introduction, setup, experiment commands, results, and licensing. Link public evidence to versioned machine-readable artifacts rather than ignored Markdown.

Update both README translations together. Keep their commands, configuration examples, technical identifiers, numeric results, and evidence links equivalent, with working language navigation in both files. After reviewing translation consistency, refresh the reviewed file hashes in configs/readme_sync.json and run python scripts/check_readme_sync.py. The distribution check must enforce README synchronization.

Keep local maintenance inventories and one-off cleanup outputs ignored by Git. Consolidate obsolete notes only after retaining unique information and updating references.

Preserve functional Markdown used as prompts, skills, test fixtures, or package metadata, and preserve mandatory legal and attribution files. Keep runtime HTML templates under finevolab/templates/. Keep optional experiment-report Markdown templates local under project_notes/experiments/; machine-readable results and completion checks must work without them. Before ignoring or moving a document, inspect its runtime, build, test, and packaging references. Explicitly allowlist required Markdown in .gitignore.

Track reproducibility inputs and evidence: source code, dependency locks, functional configuration, prompts, frozen datasets and manifests, raw source snapshots permitted for redistribution, scoring code, prediction records, and representative verification receipts. Keep credentials, personal configuration, generated reports, user uploads, dependency installations, downloaded model caches, and rebuildable vector arrays out of Git. Record model revisions and checkpoint identities, and document rebuild commands in README. Ignore rules do not remove files already tracked; retain local copies when removing explanatory documents from the index. Do not rewrite Git history or push without explicit authorization.

Keep functional configuration, prompts, financial data, indexes, and test fixtures in their respective application directories. Preserve upstream license, attribution, and trademark files. Before relocating existing files, inspect runtime, build, and documentation references. Preserve mandatory legal notices when modifying upstream files.

Use the existing Miniconda installation and conda run for the finevolab-upstream and finevolab Python 3.11 environments. Record actual validation results at each milestone, and associate experiments with run IDs, data versions, code commits, and configurations.
