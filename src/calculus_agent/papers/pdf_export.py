from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile

from calculus_agent.papers.latex_renderer import render_paper_latex
from calculus_agent.papers.renderer import render_paper_pdf
from calculus_agent.schemas import PaperPreviewRead


@dataclass(frozen=True)
class PaperPdfResult:
    content: bytes
    renderer: str
    warning: str | None = None


def export_paper_pdf(
    paper: PaperPreviewRead,
    *,
    teacher_version: bool,
    preferred_engine: str = "auto",
    timeout_seconds: float = 60,
) -> PaperPdfResult:
    engine, binary = _find_engine(preferred_engine)
    if engine is None or binary is None:
        return PaperPdfResult(
            content=render_paper_pdf(paper, teacher_version=teacher_version),
            renderer="reportlab",
            warning="未找到 Tectonic 或 XeLaTeX，已使用兼容 PDF 渲染器",
        )
    latex = render_paper_latex(paper, teacher_version=teacher_version)
    try:
        return PaperPdfResult(
            content=_compile_latex(
                latex,
                engine=engine,
                binary=binary,
                timeout_seconds=timeout_seconds,
            ),
            renderer=engine,
        )
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        return PaperPdfResult(
            content=render_paper_pdf(paper, teacher_version=teacher_version),
            renderer="reportlab",
            warning=f"{engine} 编译失败，已降级：{error}",
        )


def _find_engine(preferred: str) -> tuple[str | None, str | None]:
    if preferred not in {"auto", "tectonic", "xelatex", "reportlab"}:
        raise ValueError(f"Unsupported PDF engine: {preferred}")
    if preferred == "reportlab":
        return None, None
    names = [preferred] if preferred != "auto" else ["tectonic", "xelatex"]
    for name in names:
        binary = shutil.which(name) or _known_binary(name)
        if binary:
            return name, binary
    return None, None


def _known_binary(name: str) -> str | None:
    candidates = [Path("/Library/TeX/texbin") / name]
    texlive_root = Path("/usr/local/texlive")
    if texlive_root.is_dir():
        candidates.extend(
            sorted(
                texlive_root.glob(f"*basic/bin/*/{name}"),
                reverse=True,
            )
        )
    return next((str(path) for path in candidates if path.is_file()), None)


def _compile_latex(
    latex: str,
    *,
    engine: str,
    binary: str,
    timeout_seconds: float,
) -> bytes:
    with tempfile.TemporaryDirectory(prefix="math-paper-latex-") as directory:
        workdir = Path(directory)
        source = workdir / "paper.tex"
        source.write_text(latex, encoding="utf-8")
        if engine == "tectonic":
            command = [binary, "--outdir", str(workdir), str(source)]
        elif engine == "xelatex":
            command = [
                binary,
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-output-directory={workdir}",
                str(source),
            ]
        else:
            raise ValueError(f"Unsupported LaTeX engine: {engine}")
        completed = None
        passes = 2 if engine == "xelatex" else 1
        for _ in range(passes):
            completed = subprocess.run(
                command,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                break
        output = workdir / "paper.pdf"
        if completed is None or completed.returncode != 0 or not output.is_file():
            details = (completed.stderr or completed.stdout or "unknown error").strip()
            raise RuntimeError(details[-800:])
        return output.read_bytes()
