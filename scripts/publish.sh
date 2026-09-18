#!/usr/bin/env bash
# Publish webget to PyPI, with pre-flight checks and an optional TestPyPI run.
#
# Why a script: publishing is irreversible. The version we upload becomes
# permanent the moment it lands, so every check that can fail should fail
# BEFORE the upload, not after.
#
# Usage:
#   scripts/publish.sh --testpypi --token pypi-XXXX   # dry-run on TestPyPI
#   scripts/publish.sh --pypi                          # production, uses $UV_PUBLISH_TOKEN
#   scripts/publish.sh --pypi --token pypi-XXXX
#
# TestPyPI and PyPI have SEPARATE accounts and tokens. A production token
# gets a 403 against test.pypi.org, which is expected, not a bug.

set -euo pipefail

cd "$(dirname "$0")/.."

TARGET=""
TOKEN="${UV_PUBLISH_TOKEN:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --testpypi) TARGET="testpypi" ;;
    --pypi) TARGET="pypi" ;;
    --token) TOKEN="${2:-}"; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -z "$TARGET" ]; then
  echo "error: pick --testpypi or --pypi" >&2
  exit 2
fi
if [ -z "$TOKEN" ]; then
  echo "error: no token. Set UV_PUBLISH_TOKEN or pass --token." >&2
  exit 2
fi

VERSION="$(grep -m1 '^version' pyproject.toml | sed 's/.*"\(.*\)".*/\1/')"
PKG="$(grep -m1 '^name' pyproject.toml | sed 's/.*"\(.*\)".*/\1/')"

echo "==> $PKG $VERSION -> $TARGET"

# --- pre-flight ------------------------------------------------------------
if [ -n "$(git status --porcelain)" ]; then
  echo "error: working tree dirty. Commit first so the artifact maps to a SHA." >&2
  exit 1
fi
BRANCH="$(git branch --show-current)"
if [ "$TARGET" = "pypi" ] && [ "$BRANCH" != "main" ]; then
  echo "error: production publish must run from main (on '$BRANCH')." >&2
  exit 1
fi
SHA="$(git rev-parse --short HEAD)"
echo "    commit $SHA on $BRANCH"

CHANGELOG_HEAD="$(grep -m1 '^## \[' CHANGELOG.md)"
case "$CHANGELOG_HEAD" in
  *"$VERSION"*) echo "    CHANGELOG top entry matches $VERSION" ;;
  *) echo "error: CHANGELOG top entry is '$CHANGELOG_HEAD', expected $VERSION." >&2; exit 1 ;;
esac

# Build from a clean dist so a stale artifact from an earlier version can
# never be uploaded by accident.
rm -rf dist/
uv build >/dev/null
WHEEL="$(ls dist/*.whl)"
echo "    built $(basename "$WHEEL")"

# Assert the wheel metadata agrees with pyproject before uploading.
uv run --no-project python - "$WHEEL" "$PKG" "$VERSION" <<'PY'
import sys, zipfile
wheel, want_name, want_ver = sys.argv[1], sys.argv[2], sys.argv[3]
z = zipfile.ZipFile(wheel)
meta = z.read(next(n for n in z.namelist() if n.endswith("METADATA"))).decode()
fields = dict(
    line.split(": ", 1) for line in meta.splitlines() if ": " in line[:40]
)
got_name, got_ver = fields.get("Name", ""), fields.get("Version", "")
assert got_name == want_name, f"wheel name {got_name!r} != {want_name!r}"
assert got_ver == want_ver, f"wheel version {got_ver!r} != {want_ver!r}"
print(f"    metadata ok: {got_name} {got_ver}")
PY

# Refuse to upload a version that already exists: the upload would 400, but
# failing here is clearer and saves an authenticated round trip.
INDEX_URL="https://pypi.org"
[ "$TARGET" = "testpypi" ] && INDEX_URL="https://test.pypi.org"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$INDEX_URL/pypi/$PKG/$VERSION/json")"
if [ "$CODE" = "200" ]; then
  echo "error: $PKG $VERSION already exists on $INDEX_URL." >&2
  echo "       PyPI versions are immutable; bump the version instead." >&2
  exit 1
fi
echo "    version free on $INDEX_URL (HTTP $CODE)"

# --- publish ---------------------------------------------------------------
if [ "$TARGET" = "testpypi" ]; then
  echo "==> uploading to TestPyPI"
  uv publish --publish-url https://test.pypi.org/legacy/ --token "$TOKEN"
  echo "==> done. verify:  pip install -i https://test.pypi.org/simple/ $PKG==$VERSION"
else
  echo "==> uploading to PyPI (PERMANENT)"
  uv publish --token "$TOKEN"
  echo "==> done. Next: tag the release and push it:"
  echo "      git tag -a v$VERSION -m \"v$VERSION\" && git push origin v$VERSION"
fi
