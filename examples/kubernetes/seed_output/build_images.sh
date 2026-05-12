#!/usr/bin/env bash
set -euo pipefail
export DOCKER_BUILDKIT="${SEED_DOCKER_BUILDKIT:-0}"
PARALLELISM="${SEED_BUILD_PARALLELISM:-1}"
if ! [[ "${PARALLELISM}" =~ ^[0-9]+$ ]]; then PARALLELISM=1; fi
export REGISTRY_PUSH_RETRIES="${SEED_REGISTRY_PUSH_RETRIES:-5}"
if ! [[ "${REGISTRY_PUSH_RETRIES}" =~ ^[0-9]+$ ]]; then REGISTRY_PUSH_RETRIES=5; fi
export REGISTRY_PUSH_BACKOFF_SECONDS="${SEED_REGISTRY_PUSH_BACKOFF_SECONDS:-5}"
if ! [[ "${REGISTRY_PUSH_BACKOFF_SECONDS}" =~ ^[0-9]+$ ]]; then REGISTRY_PUSH_BACKOFF_SECONDS=5; fi
export REGISTRY_PUSH_TIMEOUT_SECONDS="${SEED_REGISTRY_PUSH_TIMEOUT_SECONDS:-180}"
if ! [[ "${REGISTRY_PUSH_TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]]; then REGISTRY_PUSH_TIMEOUT_SECONDS=180; fi
export SEED_IMAGE_DISTRIBUTION_MODE="${SEED_IMAGE_DISTRIBUTION_MODE:-registry}"
if [[ "${SEED_IMAGE_DISTRIBUTION_MODE}" != "registry" && "${SEED_IMAGE_DISTRIBUTION_MODE}" != "preload" ]]; then export SEED_IMAGE_DISTRIBUTION_MODE="registry"; fi
export SEED_DOCKER_MAX_CONCURRENT_UPLOADS="${SEED_DOCKER_MAX_CONCURRENT_UPLOADS:-1}"
if ! [[ "${SEED_DOCKER_MAX_CONCURRENT_UPLOADS}" =~ ^[0-9]+$ ]]; then export SEED_DOCKER_MAX_CONCURRENT_UPLOADS=1; fi
export SEED_DOCKER_IO_MIRROR_ENDPOINT="${SEED_DOCKER_IO_MIRROR_ENDPOINT:-https://docker.m.daocloud.io}"
MIRROR_HOST="${SEED_DOCKER_IO_MIRROR_ENDPOINT#http://}"
MIRROR_HOST="${MIRROR_HOST#https://}"
export REGISTRY_PREFIX="192.168.2.152:30500"
export REGISTRY_LOCAL_ENDPOINT="${SEED_REGISTRY_LOCAL_ENDPOINT:-}"

docker_pull() {
  local image="$1"
  if command -v timeout >/dev/null 2>&1; then
    timeout 180s docker pull "$image"
  else
    docker pull "$image"
  fi
}

mirror_image_name() {
  local image="$1"
  if [[ -z "${MIRROR_HOST}" ]]; then
    echo "$image"
    return 0
  fi
  if [[ "$image" == *"/"* ]]; then
    echo "${MIRROR_HOST}/${image}"
  else
    echo "${MIRROR_HOST}/library/${image}"
  fi
}

ensure_image_present() {
  local image="$1"
  if docker image inspect "$image" >/dev/null 2>&1; then
    return 0
  fi
  local mirror
  mirror="$(mirror_image_name "$image")"
  if [[ "$mirror" != "$image" ]]; then
    if docker_pull "$mirror" >/dev/null 2>&1; then
      docker tag "$mirror" "$image" >/dev/null 2>&1 || true
      return 0
    fi
  fi
  if docker_pull "$image" >/dev/null 2>&1; then
    return 0
  fi
  echo "[build_images] ERROR: cannot pull base image: $image" >&2
  echo "[build_images] Hint: set SEED_DOCKER_IO_MIRROR_ENDPOINT to a reachable mirror" >&2
  return 1
}

ensure_docker_daemon_config() {
  if [[ "${SEED_IMAGE_DISTRIBUTION_MODE}" == "preload" ]]; then return 0; fi
  if [[ -z "${REGISTRY_PREFIX}" ]]; then return 0; fi
  if [[ "$(id -u)" != "0" ]]; then return 0; fi
  if ! command -v systemctl >/dev/null 2>&1; then return 0; fi
  mkdir -p /etc/docker
  local result
  result="$(python3 - <<'PY'
import json
from pathlib import Path
import os

registry = os.environ.get('REGISTRY_PREFIX', '')
mirror = os.environ.get('SEED_DOCKER_IO_MIRROR_ENDPOINT', '')
max_uploads = os.environ.get('SEED_DOCKER_MAX_CONCURRENT_UPLOADS', '1')
try:
    max_uploads_value = max(1, int(max_uploads))
except Exception:
    max_uploads_value = 1
p = Path('/etc/docker/daemon.json')
data = {}
if p.exists():
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        data = {}

before = json.dumps(data, sort_keys=True)
insec = set(data.get('insecure-registries', []) or [])
if registry and '/' not in registry:
    insec.add(registry)
    if ':' in registry:
        port = registry.rsplit(':', 1)[1]
        insec.add(f'127.0.0.1:{port}')
        insec.add(f'localhost:{port}')
data['insecure-registries'] = sorted(insec)

mirrors = list(data.get('registry-mirrors', []) or [])
if mirror and mirror not in mirrors:
    mirrors.append(mirror)
data['registry-mirrors'] = mirrors
data['max-concurrent-uploads'] = max_uploads_value

after = json.dumps(data, sort_keys=True)
if after != before:
    p.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    print('changed')
else:
    print('unchanged')
PY
)"
  if [[ "${result}" == "changed" ]]; then
    systemctl restart docker >/dev/null 2>&1 || true
    sleep 2
  fi
}

export REGISTRY_ENDPOINT="${REGISTRY_PREFIX%%/*}"
if [[ -z "${REGISTRY_LOCAL_ENDPOINT}" ]] && [[ "${REGISTRY_ENDPOINT}" == *:* ]] && [[ "${REGISTRY_ENDPOINT}" != 127.0.0.1:* ]] && [[ "${REGISTRY_ENDPOINT}" != localhost:* ]]; then REGISTRY_LOCAL_ENDPOINT="127.0.0.1:${REGISTRY_ENDPOINT##*:}"; fi
export REGISTRY_PROBE_ENDPOINT="${REGISTRY_LOCAL_ENDPOINT:-${REGISTRY_ENDPOINT}}"
registry_probe() {
  if [[ -z "${REGISTRY_PROBE_ENDPOINT}" ]]; then return 0; fi
  if command -v curl >/dev/null 2>&1; then
    curl -m 5 -fsS "http://${REGISTRY_PROBE_ENDPOINT}/v2/" >/dev/null
  elif command -v wget >/dev/null 2>&1; then
    wget -q -T 5 -O /dev/null "http://${REGISTRY_PROBE_ENDPOINT}/v2/"
  else
    return 0
  fi
}

wait_for_registry() {
  local retries="${1:-6}"
  local attempt=1
  while [ "${attempt}" -le "${retries}" ]; do
    if registry_probe; then
      return 0
    fi
    sleep "${attempt}"
    attempt=$((attempt + 1))
  done
  return 1
}

local_push_image_ref() {
  local image="$1"
  if [[ -z "${REGISTRY_LOCAL_ENDPOINT}" ]] || [[ -z "${REGISTRY_PREFIX}" ]]; then
    echo "${image}"
    return 0
  fi
  local suffix="${image#${REGISTRY_PREFIX}/}"
  if [[ "${suffix}" == "${image}" ]]; then
    echo "${image}"
    return 0
  fi
  echo "${REGISTRY_LOCAL_ENDPOINT}/${suffix}"
}

docker_push_with_timeout() {
  local image="$1"
  if command -v timeout >/dev/null 2>&1; then
    timeout "${REGISTRY_PUSH_TIMEOUT_SECONDS}" docker push "${image}"
  else
    docker push "${image}"
  fi
}

retry_push() {
  local image="$1"
  if [[ -z "${REGISTRY_PREFIX}" ]]; then return 0; fi
  local attempt=1
  while [ "${attempt}" -le "${REGISTRY_PUSH_RETRIES}" ]; do
    if ! wait_for_registry 3; then
      echo "[build_images] registry probe failed before push attempt ${attempt}/${REGISTRY_PUSH_RETRIES}: ${REGISTRY_PROBE_ENDPOINT}" >&2
    fi
    if docker_push_with_timeout "${image}"; then
      return 0
    fi
    if [ "${attempt}" -ge "${REGISTRY_PUSH_RETRIES}" ]; then
      break
    fi
    local sleep_seconds=$((REGISTRY_PUSH_BACKOFF_SECONDS * attempt))
    echo "[build_images] retrying push ${image} in ${sleep_seconds}s (${attempt}/${REGISTRY_PUSH_RETRIES})" >&2
    sleep "${sleep_seconds}"
    attempt=$((attempt + 1))
  done
  echo "[build_images] ERROR: push failed after ${REGISTRY_PUSH_RETRIES} attempts: ${image}" >&2
  return 1
}

seedemu_build_and_push() {
  local image="$1"
  local context_dir="$2"
  docker build -t "${image}" "${context_dir}"
  if [[ "${SEED_IMAGE_DISTRIBUTION_MODE}" == "preload" ]]; then
    return 0
  fi
  if [[ -n "${REGISTRY_PREFIX}" ]]; then
    local push_image
    push_image="$(local_push_image_ref "${image}")"
    if [[ "${push_image}" != "${image}" ]]; then
      docker tag "${image}" "${push_image}"
    fi
    retry_push "${push_image}"
  fi
}

seedemu_copy_and_push_image() {
  local source_image="$1"
  local target_image="$2"
  ensure_image_present "${source_image}"
  docker tag "${source_image}" "${target_image}"
  if [[ "${SEED_IMAGE_DISTRIBUTION_MODE}" == "preload" ]]; then
    return 0
  fi
  if [[ -n "${REGISTRY_PREFIX}" ]]; then
    local push_image
    push_image="$(local_push_image_ref "${target_image}")"
    if [[ "${push_image}" != "${target_image}" ]]; then
      docker tag "${target_image}" "${push_image}"
    fi
    retry_push "${push_image}"
  fi
}
export -f registry_probe wait_for_registry local_push_image_ref docker_push_with_timeout retry_push seedemu_build_and_push seedemu_copy_and_push_image

prepare_dummy_image() {
  local base_image="$1"
  local dummy_tag="$2"
  if docker image inspect "$dummy_tag" >/dev/null 2>&1; then
    return 0
  fi
  if ! docker image inspect "$base_image" >/dev/null 2>&1; then
    local ctx="base_images/${dummy_tag}"
    if [ -f "${ctx}/Dockerfile" ]; then
      echo "[build_images] building base image: ${base_image} (from ${ctx})" >&2
      # Pre-pull common upstream bases via mirror+tag to avoid Docker Hub outages.
      ensure_image_present "ubuntu:20.04"
      docker build -t "${base_image}" "${ctx}"
    else
      ensure_image_present "$base_image"
    fi
  fi
  mkdir -p dummies
  local df="dummies/${dummy_tag}.Dockerfile"
  printf 'FROM %s\n' "$base_image" > "$df"
  docker build -t "$dummy_tag" -f "$df" dummies >/dev/null
}

load_prefetched_images() {
  if [ ! -d prefetched_images ]; then
    return 0
  fi
  local tarball
  shopt -s nullglob
  for tarball in prefetched_images/*.tar; do
    echo "[build_images] loading prefetched image: ${tarball}" >&2
    docker load -i "${tarball}" >/dev/null
  done
  shopt -u nullglob
}

ensure_docker_daemon_config
load_prefetched_images
echo "[build_images] preparing base-image dummies"
prepare_dummy_image "handsonsecurity/seedemu-multiarch-base:buildx-latest" "98a2693c996c2294358552f48373498d"
prepare_dummy_image "handsonsecurity/seedemu-multiarch-router:buildx-latest" "39e016aa9e819f203ebc1809245a5818"

JOBS_FILE="$(mktemp)"
cleanup() { rm -f "${JOBS_FILE}"; }
trap cleanup EXIT
cat > "${JOBS_FILE}" <<'JOBS'
seedemu_build_and_push 192.168.2.152:30500/brdnode_3_r1:latest ./brdnode_3_r1
seedemu_build_and_push 192.168.2.152:30500/rnode_3_r2:latest ./rnode_3_r2
seedemu_build_and_push 192.168.2.152:30500/rnode_3_r3:latest ./rnode_3_r3
seedemu_build_and_push 192.168.2.152:30500/brdnode_3_r4:latest ./brdnode_3_r4
seedemu_build_and_push 192.168.2.152:30500/hnode_150_web:latest ./hnode_150_web
seedemu_build_and_push 192.168.2.152:30500/hnode_150_dns:latest ./hnode_150_dns
seedemu_build_and_push 192.168.2.152:30500/brdnode_150_router0:latest ./brdnode_150_router0
seedemu_build_and_push 192.168.2.152:30500/hnode_151_web:latest ./hnode_151_web
seedemu_build_and_push 192.168.2.152:30500/hnode_151_dns:latest ./hnode_151_dns
seedemu_build_and_push 192.168.2.152:30500/brdnode_151_router0:latest ./brdnode_151_router0
seedemu_build_and_push 192.168.2.152:30500/hnode_152_web:latest ./hnode_152_web
seedemu_build_and_push 192.168.2.152:30500/brdnode_152_router0:latest ./brdnode_152_router0
seedemu_build_and_push 192.168.2.152:30500/rs_ix_ix100:latest ./rs_ix_ix100
seedemu_build_and_push 192.168.2.152:30500/rs_ix_ix101:latest ./rs_ix_ix101
JOBS
if [ "${PARALLELISM}" -le 1 ]; then
  while IFS= read -r cmd; do
    [ -z "${cmd}" ] && continue
    echo "+ ${cmd}"
    eval "${cmd}"
  done < "${JOBS_FILE}"
else
  awk 'NF' "${JOBS_FILE}" | xargs -P "${PARALLELISM}" -d '\n' -I {} bash -lc 'set -euo pipefail; cmd="{}"; echo "+ ${cmd}"; eval "${cmd}"'
fi
