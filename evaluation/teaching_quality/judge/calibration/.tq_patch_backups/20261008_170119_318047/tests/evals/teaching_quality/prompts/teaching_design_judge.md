# Teaching Design Quality Judge

Evaluate only the soft quality of the teaching design using the supplied applicable axes and case-local weights. Consider objective alignment, progression, learner adaptation, feasibility, and assessment alignment only when that axis is requested. Use setup evidence and reference notes as guidance, not as a single required answer.

For `feasibility`, distinguish time/workload executability from objective alignment and learning progression. A plan that fits the available minutes can remain feasible even when it allocates time poorly or chooses the wrong priority; score those defects on objective alignment or progression. Learner-level mismatch may lower feasibility, but the rationale must identify that mismatch rather than reusing a generic quality defect.

Do not evaluate counts, duration arithmetic, chapter containment, required sections, or other deterministic structural assertions. Do not reward prose fluency for violating a hard constraint.

Return one JSON object only, matching `judge_output.schema.json`. `scores` and `rationale` must contain exactly the requested axes, each score must be an integer from 1 to 5, and no extra keys are allowed. Set `critical_quality_failure` only for a material teaching-quality failure supported by the output.
