from evaluation.teaching_quality.runners.validate_semantic_contrast_set import (
    validate_semantic_contrast_set,
)


def test_semantic_contrast_set_has_five_balanced_assertion_types():
    validate_semantic_contrast_set()
