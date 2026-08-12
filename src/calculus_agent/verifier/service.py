import re

from calculus_agent.schemas import VerificationResult


def verify_answer(actual: str, expected: list[str]) -> VerificationResult:
    if not expected:
        return VerificationResult(
            status="unsupported", method="no-reference", expected=[], actual=actual
        )
    normalized_actual = _normalize(actual)
    for candidate in expected:
        if normalized_actual == _normalize(candidate):
            return VerificationResult(
                status="verified",
                method="normalized-exact",
                expected=expected,
                actual=actual,
                details=["规范化后的答案完全一致"],
            )
    symbolic = _symbolic_equivalent(actual, expected)
    if symbolic is True:
        return VerificationResult(
            status="verified",
            method="sympy-equivalence",
            expected=expected,
            actual=actual,
            details=["SymPy判定表达式等价"],
        )
    if symbolic is None:
        return VerificationResult(
            status="unsupported",
            method="sympy-unsupported",
            expected=expected,
            actual=actual,
            details=["答案格式超出当前符号验证器覆盖范围"],
        )
    return VerificationResult(
        status="conflict",
        method="sympy-equivalence",
        expected=expected,
        actual=actual,
        details=["模型答案与数据集参考答案不等价"],
    )


def _symbolic_equivalent(actual: str, expected: list[str]) -> bool | None:
    try:
        from sympy import simplify, sympify
        from sympy.parsing.sympy_parser import parse_expr
    except ImportError:
        return None
    try:
        left = parse_expr(_to_sympy(actual), evaluate=True)
        for value in expected:
            right = sympify(_to_sympy(value))
            if simplify(left - right) == 0:
                return True
        return False
    except Exception:
        return None


def _to_sympy(value: str) -> str:
    value = value.strip().strip("$")
    value = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", value)
    value = value.replace("^", "**").replace("\\pi", "pi")
    return value


def _normalize(value: str) -> str:
    return re.sub(r"[\s$，。；：、,.]", "", value).lower()
