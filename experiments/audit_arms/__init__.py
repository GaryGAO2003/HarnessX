"""audit_arms -- self-contained offline implementations of audit-line T1 arms.

Idea ⑧ (racing acceptance gate) and idea ⑩ (cold-start router shrinkage).
Stdlib only; no dependency on experiments.variant_pool or branch analysis code.
Modules are also importable by bare name via sys.path self-insertion (see the
sim CLIs and tests/conftest.py), matching the repo's analysis-script idiom.
"""
