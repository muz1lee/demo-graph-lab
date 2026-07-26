# Feedback Repair

Use this evidence when an earlier ASPIRE-KW iteration generated a policy-valid YAML candidate but execution failed.

The next candidate should be conditioned on three evidence sources:

- Top-K history: which candidates succeeded or failed.
- Failure signature: the observed runtime or verifier failure.
- Negative evidence: strategy families that already failed.

Repair rules:

1. Change the mechanism that plausibly caused the failure; do not only rename the candidate.
2. If execution fails before object interaction, simplify toward public high-level skills.
3. If execution reaches the action but verifier fails, improve task labels, output binding, or final assertion.
4. If the failure is service availability, avoid adding more service dependencies.
5. If dry-run passes but real execution fails, keep static structure stable and change only runtime-relevant steps.
6. Treat feedback and instrumentation gaps separately from behavior failures. Adding a verifier, assertion, or extra trace point after a failed action is not a mechanism-level repair for that failed action.
7. If the first failed action is an acquisition, grounding, or motion action, repair the acquisition/grounding/motion mechanism itself, choose a more mature public high-level skill, or report a gap. Do not claim that post-action verification fixes the failed prerequisite.

The prompt should treat negative evidence as a warning, not as a hard ban. Reusing a failed family requires a concrete reason.
