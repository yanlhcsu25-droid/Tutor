from calculus_agent.verifier.service import verify_answer


def test_verifies_normalized_exact_answer_without_symbolic_engine() -> None:
    result = verify_answer(" $1$ ", ["1"])
    assert result.status == "verified"
    assert result.method == "normalized-exact"


def test_verifies_symbolically_equivalent_fraction() -> None:
    result = verify_answer("1/2", [r"\frac{2}{4}"])
    assert result.status == "verified"
    assert result.method == "sympy-equivalence"
