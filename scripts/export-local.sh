#!/usr/bin/env bash
set -euo pipefail

export_dir="${KIVRA_EXPORT_DIR:-"${HOME}/kivra-export"}"
image="${KIVRA_IMAGE:-kivra-sync:local}"
docker_user="$(id -u):$(id -g)"

# Rootless Docker maps container root to the current host user.
if docker info --format '{{json .SecurityOptions}}' | grep -q '"name=rootless"'; then
  docker_user="0:0"
fi

mode="rw"
run_args=(-it)
if [[ "${1:-}" == "--verify-only" ]]; then
  mode="ro"
  run_args=(--network none)
  if [[ ! -d "${export_dir}" ]]; then
    printf '%s\n' 'The export directory does not exist.' >&2
    exit 1
  fi
else
  (umask 077; mkdir -p -- "${export_dir}")
fi

export_dir="$(cd -- "${export_dir}" && pwd)"
exec docker run --rm --log-driver none \
  --user "${docker_user}" --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  "${run_args[@]}" -v "${export_dir}:/data:${mode}" \
  "${image}" --base-dir /data "${@}"
