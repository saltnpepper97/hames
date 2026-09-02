#!/usr/bin/env bash

set -Eeuo pipefail

repository_url="${HAMES_REPOSITORY_URL:-https://github.com/saltnpepper97/hames.git}"
repository_ref="${HAMES_REF:-main}"

for command_name in git uv cargo install; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'error: required command not found: %s\n' "$command_name" >&2
    exit 1
  fi
done

script_path="${BASH_SOURCE[0]:-}"
if [[ -n "$script_path" && -f "$script_path" ]]; then
  script_directory="$(cd -- "$(dirname -- "$script_path")" && pwd)"
else
  script_directory=""
fi
if [[ -f "$script_directory/pyproject.toml" && -f "$script_directory/Cargo.toml" ]]; then
  source_directory="$script_directory"
  local_checkout=true
else
  local_checkout=false
  data_directory="${XDG_DATA_HOME:-$HOME/.local/share}"
  source_directory="${HAMES_INSTALL_ROOT:-$data_directory/hames/source}"
  if [[ -d "$source_directory/.git" ]]; then
    installed_repository_url="$(git -C "$source_directory" remote get-url origin)"
    if [[ "$installed_repository_url" != "$repository_url" ]]; then
      printf 'error: %s belongs to %s, not %s\n' \
        "$source_directory" "$installed_repository_url" "$repository_url" >&2
      exit 1
    fi
    printf 'Updating Hames (%s)...\n' "$repository_ref"
    git -C "$source_directory" fetch --depth 1 origin "$repository_ref"
    git -C "$source_directory" merge --ff-only FETCH_HEAD
  elif [[ -e "$source_directory" ]]; then
    printf 'error: install path exists and is not a Hames checkout: %s\n' \
      "$source_directory" >&2
    exit 1
  else
    printf 'Downloading Hames (%s)...\n' "$repository_ref"
    mkdir -p -- "$(dirname -- "$source_directory")"
    git clone --depth 1 --branch "$repository_ref" "$repository_url" "$source_directory"
  fi
fi

tool_bin_directory="${HAMES_BIN_DIR:-$(uv tool dir --bin)}"
mkdir -p -- "$tool_bin_directory"

printf 'Installing the Hames backend...\n'
if [[ "$local_checkout" == true ]]; then
  uv sync --locked --project "$source_directory"
else
  uv sync --locked --no-dev --project "$source_directory"
fi

printf 'Building the Hames terminal client...\n'
cargo build --release --locked --manifest-path "$source_directory/Cargo.toml"
install -m 0755 "$source_directory/target/release/hames" "$tool_bin_directory/hames"

printf '\nHames installed to %s/hames\n' "$tool_bin_directory"
case ":$PATH:" in
  *":$tool_bin_directory:"*) ;;
  *) printf 'Add %s to PATH, then run: hames setup\n' "$tool_bin_directory" ;;
esac
