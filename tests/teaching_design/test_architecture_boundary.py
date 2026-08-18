from pathlib import Path


def test_teaching_design_domain_does_not_depend_on_agent_layer():
    package = Path("src/calculus_agent/teaching_design")
    offenders = []

    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "calculus_agent.agent" in text or "from ..agent" in text:
            offenders.append(str(path))

    assert offenders == [], (
        "TeachingDesign domain must not depend on Agent layer: "
        + ", ".join(offenders)
    )
