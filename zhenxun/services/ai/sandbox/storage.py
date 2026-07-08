import hashlib
from pathlib import PurePath, PurePosixPath
from typing import Final

_INSTALL_MARKER: Final[str] = "INSTALL_VFS_HELPER_V1"

_RESOLVE_WORKSPACE_PATH_SCRIPT: Final[str] = """
#!/bin/sh
set -eu

root="$1"
candidate="$2"
for_write="$3"
max_symlink_depth=64

case "$for_write" in
    0|1) ;;
    *)
        printf 'for_write must be 0 or 1\\n' >&2
        exit 64
        ;;
esac

resolve_path() {
    path="$1"
    depth="${2:-0}"
    seen="${3:-}"
    if [ "$path" = "/" ]; then
        printf '/\\n'
        return 0
    fi

    if [ "$depth" -ge "$max_symlink_depth" ]; then
        printf 'symlink resolution depth exceeded: %s\\n' "$path" >&2
        exit 112
    fi

    if [ -d "$path" ]; then
        (
            cd "$path"
            pwd -P
        )
        return 0
    fi

    parent=${path%/*}
    base=${path##*/}
    if [ -z "$parent" ] || [ "$parent" = "$path" ]; then
        parent="/"
    fi

    resolved_parent=$(resolve_path "$parent" "$depth" "$seen")
    candidate_path="$resolved_parent/$base"
    if [ -L "$candidate_path" ]; then
        case ":$seen:" in
            *":$candidate_path:"*)
                printf 'symlink resolution depth exceeded: %s\\n' "$candidate_path" >&2
                exit 112
                ;;
        esac
        target=$(readlink "$candidate_path")
        next_depth=$((depth + 1))
        next_seen="${seen}:$candidate_path"
        case "$target" in
            /*) resolve_path "$target" "$next_depth" "$next_seen" ;;
            *) resolve_path "$resolved_parent/$target" "$next_depth" "$next_seen" ;;
        esac
        return 0
    fi

    printf '%s\\n' "$candidate_path"
}

resolved_candidate=$(resolve_path "$candidate" 0)
resolved_root=$(resolve_path "$root" 0)

case "$resolved_candidate" in
    "$resolved_root"|"$resolved_root"/*)
        printf '%s\\n' "$resolved_candidate"
        exit 0
        ;;
esac

printf 'workspace escape: %s\\n' "$resolved_candidate" >&2
exit 111
""".strip()


class VfsHelperScript:
    """VFS 运行时探针"""

    def __init__(self, name: str, content: str):
        self.name = name
        self.content = content
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        self.install_path = (
            PurePosixPath("/tmp/zhenxun-sandbox/bin") / f"{name}-{digest}"
        )

    def install_command(self) -> list[str]:
        tmp_template = f"{self.install_path}.tmp.$$"
        heredoc = (
            f"ZHENXUN_VFS_HELPER_{self.install_path.name.upper().replace('-', '_')}"
        )
        script = f"""
# {_INSTALL_MARKER}
set -eu
dest="$1"
tmp="{tmp_template}"
mkdir -p -- "$(dirname -- "$dest")"
cat > "$tmp" <<'{heredoc}'
{self.content}
{heredoc}
chmod 0555 "$tmp"
rm -f -- "$dest"
mv -f -- "$tmp" "$dest"
"""
        return ["sh", "-c", script.strip(), "sh", str(self.install_path)]


RESOLVE_PATH_HELPER = VfsHelperScript(
    "resolve-workspace-path", _RESOLVE_WORKSPACE_PATH_SCRIPT
)


def coerce_posix_path(path: str | PurePath) -> PurePosixPath:
    if isinstance(path, PurePath):
        path = path.as_posix()
    else:
        path = path.replace("\\", "/")
    return PurePosixPath(path)


