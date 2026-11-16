"""Test-side entry point for the production batch collector.

The runner lives in :mod:`cadai.template_collect`; this module keeps the
historical import surface and command-line form used by the migration
harnesses (``python template_capture_pilot.py --queue ... --batch ...``).
"""

from cadai.template_collect import (  # noqa: F401  (re-export for harness scripts)
    ASSETS,
    CAPTURE_SCHEMA,
    MODULES,
    attach,
    batch_targets,
    capture_group,
    classification_map,
    classify_failure,
    extract,
    main,
    result_map,
    run,
    source_list,
    target_key,
)

if __name__ == "__main__":
    raise SystemExit(main())
