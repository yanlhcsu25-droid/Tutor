from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil


def get_mps_status() -> dict:
    try:
        import torch

        return {
            "torch_version": torch.__version__,
            "mps_built": torch.backends.mps.is_built(),
            "mps_available": torch.backends.mps.is_available(),
        }
    except Exception as exc:
        return {
            "error": repr(exc),
        }


def process_tree_rss_mb(process: psutil.Process) -> float:
    total = 0

    try:
        processes = [process] + process.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        processes = [process]

    for proc in processes:
        try:
            total += proc.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return total / 1024 / 1024


def stream_output(pipe):
    for line in iter(pipe.readline, ""):
        print(line, end="")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)

    parser.add_argument(
        "--backend",
        default="hybrid-engine",
        choices=[
            "pipeline",
            "vlm-engine",
            "hybrid-engine",
        ],
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("mineru_output"),
    )

    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="0-based start page",
    )

    parser.add_argument(
        "--end",
        type=int,
        default=1,
        help="0-based end page; default=1 means first two pages",
    )

    args = parser.parse_args()

    pdf = args.pdf.expanduser().resolve()
    output = args.output.expanduser().resolve()

    if not pdf.exists():
        raise FileNotFoundError(pdf)

    mineru_bin = shutil.which("mineru")

    if mineru_bin is None:
        raise RuntimeError(
            "找不到 mineru，请先安装：uv pip install -U 'mineru[all]'"
        )

    if output.exists():
        shutil.rmtree(output)

    output.mkdir(parents=True)

    try:
        mineru_version = importlib.metadata.version("mineru")
    except Exception:
        mineru_version = "unknown"

    command = [
        mineru_bin,
        "-p",
        str(pdf),
        "-o",
        str(output),
        "-b",
        args.backend,
        "-s",
        str(args.start),
        "-e",
        str(args.end),
        "-f",
        "true",
        "-t",
        "false",
    ]

    # pipeline 模式可以明确告诉 OCR 是中文文档
    if args.backend == "pipeline":
        command += ["-l", "ch"]

    print("=" * 70)
    print("MinerU Benchmark")
    print("=" * 70)

    print("PDF:", pdf)
    print("Backend:", args.backend)
    print("Pages:", f"{args.start} ~ {args.end}")
    print("MinerU:", mineru_version)
    print("Platform:", platform.platform())
    print("Machine:", platform.machine())
    print("Python:", sys.version.split()[0])
    print("MPS:", get_mps_status())
    print()
    print("Command:")
    print(" ".join(command))
    print("=" * 70)

    start_time = time.perf_counter()

    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    ps_proc = psutil.Process(proc.pid)

    reader = threading.Thread(
        target=stream_output,
        args=(proc.stdout,),
        daemon=True,
    )
    reader.start()

    peak_rss_mb = 0.0
    samples = []

    while proc.poll() is None:
        rss = process_tree_rss_mb(ps_proc)

        peak_rss_mb = max(peak_rss_mb, rss)

        samples.append(
            {
                "elapsed_sec": round(time.perf_counter() - start_time, 2),
                "rss_mb": round(rss, 2),
            }
        )

        time.sleep(0.5)

    reader.join(timeout=5)

    elapsed = time.perf_counter() - start_time

    markdown_files = list(output.rglob("*.md"))

    result = {
        "pdf": str(pdf),
        "backend": args.backend,
        "start_page": args.start,
        "end_page": args.end,
        "page_count": args.end - args.start + 1,
        "elapsed_sec": round(elapsed, 2),
        "seconds_per_page": round(
            elapsed / (args.end - args.start + 1),
            2,
        ),
        "peak_rss_mb": round(peak_rss_mb, 2),
        "mineru_version": mineru_version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "mps": get_mps_status(),
        "return_code": proc.returncode,
        "markdown_files": [
            str(p.relative_to(output))
            for p in markdown_files
        ],
        "memory_samples": samples,
    }

    report_path = output / "benchmark.json"

    report_path.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print("=" * 70)
    print("RESULT")
    print("=" * 70)

    print(f"总耗时: {elapsed:.2f} 秒")
    print(
        f"平均耗时: "
        f"{result['seconds_per_page']:.2f} 秒/页"
    )
    print(
        f"峰值 RSS: "
        f"{peak_rss_mb / 1024:.2f} GB"
    )
    print(f"Markdown 文件数: {len(markdown_files)}")

    for path in markdown_files:
        print("Markdown:", path)

    print("报告:", report_path)

    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()