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


class PdfLatexCompileError(RuntimeError):
    """Raised when a configured LaTeX engine fails to compile a paper."""

    def __init__(
        self,
        *,
        engine: str,
        debug_dir: Path,
        details: str,
    ) -> None:
        self.engine = engine
        self.debug_dir = debug_dir
        self.details = details

        super().__init__(
            f"{engine} PDF 编译失败。"
            f"调试文件已保存到: {debug_dir}\n"
            f"编译器输出:\n{details}"
        )


def export_paper_pdf(
    paper: PaperPreviewRead,
    *,
    teacher_version: bool,
    preferred_engine: str = "auto",
    timeout_seconds: float = 60,
) -> PaperPdfResult:
    engine, binary = _find_engine(preferred_engine)

    # Explicit ReportLab selection remains supported.
    # When no LaTeX engine is installed, preserve the previous compatibility
    # behavior for now. The important production invariant below is:
    # once a LaTeX engine is selected, a compile failure must not be hidden
    # behind a "successful" ReportLab PDF.
    if engine is None or binary is None:
        return PaperPdfResult(
            content=render_paper_pdf(
                paper,
                teacher_version=teacher_version,
            ),
            renderer="reportlab",
            warning="未找到 Tectonic 或 XeLaTeX，已使用兼容 PDF 渲染器",
        )

    latex = render_paper_latex(
        paper,
        teacher_version=teacher_version,
    )

    # Do NOT silently fall back to ReportLab here.
    #
    # A failed LaTeX compile usually means the generated mathematical document
    # is invalid. Returning a ReportLab PDF would turn a real rendering failure
    # into an apparent success while exposing raw LaTeX source to the user.
    content = _compile_latex(
        latex,
        engine=engine,
        binary=binary,
        timeout_seconds=timeout_seconds,
    )

    return PaperPdfResult(
        content=content,
        renderer=engine,
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
            command = [
                binary,
                "--outdir",
                str(workdir),
                str(source),
            ]
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

        completed: subprocess.CompletedProcess[str] | None = None
        passes = 2 if engine == "xelatex" else 1

        try:
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

        except subprocess.TimeoutExpired as error:
            debug_dir = _preserve_failure_artifacts(
                workdir=workdir,
                source=source,
                engine=engine,
                stdout=_coerce_process_output(error.stdout),
                stderr=_coerce_process_output(error.stderr),
                reason=f"compile timeout after {timeout_seconds} seconds",
            )

            raise PdfLatexCompileError(
                engine=engine,
                debug_dir=debug_dir,
                details=f"编译超时：超过 {timeout_seconds} 秒。",
            ) from error

        output = workdir / "paper.pdf"

        if completed is None or completed.returncode != 0 or not output.is_file():
            stdout = completed.stdout if completed is not None else ""
            stderr = completed.stderr if completed is not None else ""

            log_path = workdir / "paper.log"
            log_text = (
                log_path.read_text(encoding="utf-8", errors="replace")
                if log_path.is_file()
                else ""
            )

            details = _build_compile_error_details(
                stdout=stdout,
                stderr=stderr,
                log_text=log_text,
            )

            debug_dir = _preserve_failure_artifacts(
                workdir=workdir,
                source=source,
                engine=engine,
                stdout=stdout,
                stderr=stderr,
                reason=details,
            )

            raise PdfLatexCompileError(
                engine=engine,
                debug_dir=debug_dir,
                details=details,
            )

        return output.read_bytes()


def _build_compile_error_details(
    *,
    stdout: str,
    stderr: str,
    log_text: str,
) -> str:
    """Return the most useful tail of the compiler diagnostics."""

    sections: list[str] = []

    if stderr.strip():
        sections.append(
            "=== STDERR ===\n"
            + _tail(stderr.strip(), 4000)
        )

    if stdout.strip():
        sections.append(
            "=== STDOUT ===\n"
            + _tail(stdout.strip(), 6000)
        )

    if log_text.strip():
        sections.append(
            "=== paper.log ===\n"
            + _tail(log_text.strip(), 8000)
        )

    if not sections:
        return "unknown LaTeX compile error"

    return "\n\n".join(sections)


def _preserve_failure_artifacts(
    *,
    workdir: Path,
    source: Path,
    engine: str,
    stdout: str,
    stderr: str,
    reason: str,
) -> Path:
    """
    Copy failed compile artifacts out of TemporaryDirectory.

    The returned directory survives after _compile_latex() exits, so the real
    paper.tex and compiler log can be inspected locally.
    """

    debug_dir = Path(
        tempfile.mkdtemp(prefix=f"calculus-agent-{engine}-failed-")
    )

    shutil.copy2(source, debug_dir / "paper.tex")

    log_path = workdir / "paper.log"
    if log_path.is_file():
        shutil.copy2(log_path, debug_dir / "paper.log")

    aux_path = workdir / "paper.aux"
    if aux_path.is_file():
        shutil.copy2(aux_path, debug_dir / "paper.aux")

    (debug_dir / "stdout.txt").write_text(
        stdout or "",
        encoding="utf-8",
    )
    (debug_dir / "stderr.txt").write_text(
        stderr or "",
        encoding="utf-8",
    )
    (debug_dir / "error.txt").write_text(
        reason or "unknown LaTeX compile error",
        encoding="utf-8",
    )

    return debug_dir


def _coerce_process_output(value: str | bytes | None) -> str:
    if value is None:
        return ""

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return value


def _tail(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value

    return "...<truncated>...\n" + value[-limit:]
