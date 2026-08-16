from calculus_agent.workbench.math_normalization import math_suspicious_issues, normalize_math_format


def test_aligned_piecewise_is_normalized_without_touching_values():
    source = r"""$$\left\{\begin{aligned}
&-x,&|x|>1,\\
&0,&|x|=1,\\
&x,&|x|<1.
\end{aligned}\right.$$"""
    result = normalize_math_format(source)
    assert r"\begin{cases}" in result
    assert "-x" in result and "|x|>1" in result
    assert "&-x" not in result


def test_conflicting_ordinary_limits_are_warning_only():
    source = r"$$\lim_{x\to-1}f(x)=1$$ $$\lim_{x\to-1}f(x)=-1$$"
    issues = math_suspicious_issues(source)
    assert len(issues) == 1
    assert issues[0].field == "math_semantics"


def test_ocr_broken_subscript_is_normalized():
    assert normalize_math_format(r"$\pmb{x}*{0}=x*{0}$") == r"$x_{0}=x_{0}$"


def test_display_aligned_is_split_into_independent_math_blocks():
    result = normalize_math_format(r"$$\begin{aligned}&a\\&=b\end{aligned}$$")
    assert result.count("$$") == 4
    assert "&" not in result
    assert "aligned" not in result
    assert "a" in result and "=b" in result


def test_inline_aligned_is_converted_to_mathml_supported_array():
    result = normalize_math_format(r"$\begin{aligned}&a\\&=b\end{aligned}$")
    assert r"\begin{array}{rl}" in result
    assert r"\end{array}" in result
    assert "aligned" not in result


def test_only_repeated_escaped_underscores_are_normalized_as_blank():
    source = r"填空：\_\_\_\_；变量名：test\_value"

    result = normalize_math_format(source)

    assert result == r"填空：____；变量名：test\_value"
