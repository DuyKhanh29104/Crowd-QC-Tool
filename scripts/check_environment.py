"""Read-only environment survey; run with the interpreter used for inference."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30, errors="replace")
        return {"exit_code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc)}


def survey():
    packages = {}
    for name in ("torch", "torchvision", "ultralytics", "av", "numpy", "opencv-python"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    usage = shutil.disk_usage(Path.cwd())
    result = {"recorded_at": datetime.now(timezone.utc).isoformat(), "os": platform.platform(),
              "python": sys.version, "executable": sys.executable, "prefix": sys.prefix,
              "base_prefix": sys.base_prefix, "packages": packages,
              "disk": {"path": str(Path.cwd().anchor), "total_bytes": usage.total, "free_bytes": usage.free},
              "gpu": command(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.free", "--format=csv,noheader"]),
              "ffmpeg": command(["ffmpeg", "-version"]), "ffprobe": command(["ffprobe", "-version"])}
    if platform.system() == "Windows":
        result["memory"] = command(["powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory | ConvertTo-Json -Compress"])
    try:
        import torch
        result["torch"] = {"version": torch.__version__, "cuda_runtime": torch.version.cuda,
                           "cuda_available": torch.cuda.is_available()}
        if torch.cuda.is_available():
            result["torch"]["device_name"] = torch.cuda.get_device_name(0)
            result["torch"]["capability"] = list(torch.cuda.get_device_capability(0))
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        # Tensor smoke test only; detector inference is evidenced by spike_autolabel.
        with torch.inference_mode():
            value = (torch.ones((16, 16), device=device) @ torch.ones((16, 16), device=device))[0, 0].item()
        result["tensor_smoke"] = {"device": device, "value": value, "passed": value == 16}
    except Exception as exc:
        result["torch_error"] = str(exc)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args()
    result = survey()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "torch": result.get("torch"), "tensor_smoke": result.get("tensor_smoke")}, indent=2))
    if args.require_cuda and not (result.get("torch", {}).get("cuda_available") and
            result.get("tensor_smoke", {}).get("device") == "cuda:0" and
            result.get("tensor_smoke", {}).get("passed")):
        raise SystemExit("CUDA validation failed; no silent CPU fallback.")


if __name__ == "__main__":
    main()
