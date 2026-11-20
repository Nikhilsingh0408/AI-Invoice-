"""Package tracked project files and non-ignored additions, excluding local secrets."""
import argparse
from pathlib import Path
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
    ).decode().split("\0")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(names) - {""}):
            relative = Path(name)
            path = root / relative
            if any(part in {".git", ".aws", ".codex", ".agents", "__pycache__"} for part in relative.parts):
                continue
            if relative.name.startswith(".env") and not relative.name.endswith(".example"):
                continue
            if path.is_symlink() or not path.is_file() or path.resolve() == output:
                continue
            archive.write(path, Path("ai-invoice-auditor-group-14") / relative)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Archive integrity check failed")
        print(f"Created {output} ({len(archive.namelist())} files)")


if __name__ == "__main__":
    main()
