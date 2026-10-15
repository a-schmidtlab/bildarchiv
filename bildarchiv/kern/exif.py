"""Aufrufe von exiftool."""

import json
import os
import shutil
import subprocess
import tempfile

BASIS = ["exiftool", "-charset", "filename=utf8", "-api", "largefilesupport=1"]


def vorhanden():
    return shutil.which("exiftool") is not None


def version():
    try:
        return subprocess.run(["exiftool", "-ver"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return None


def run(args, files=()):
    """exiftool mit Argumentdatei für die Dateiliste (Umlaute, Leerzeichen, lange Listen)."""
    argfile = None
    try:
        cmd = [*BASIS, *args]
        if files:
            with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False, encoding="utf-8") as f:
                f.write("\n".join(files) + "\n")
                argfile = f.name
            cmd += ["-@", argfile]
        return subprocess.run(cmd, capture_output=True, text=True)
    finally:
        if argfile:
            os.unlink(argfile)


def lies_json(args, files):
    p = run(["-j", "-q", "-q", *args], files)
    try:
        return json.loads(p.stdout) if p.stdout.strip() else []
    except json.JSONDecodeError:
        return []


def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
