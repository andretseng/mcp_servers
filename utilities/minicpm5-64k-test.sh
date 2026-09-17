#!/usr/bin/env bash
set -u -o pipefail

SERVER="${LLAMA_SERVER:-llama-server}"
MODEL_PROFILE="${MODEL_PROFILE:-minicpm5}"
case "$MODEL_PROFILE" in
  qwen25)
    PROFILE_MODEL_REF='Qwen/Qwen2.5-1.5B-Instruct-GGUF:Q4_K_M'
    PROFILE_USE_YARN=1
    PROFILE_ORIG_CTX=32768
    PROFILE_TAG='qwen25-1.5b-q4km'
    ;;
  minicpm5)
    PROFILE_MODEL_REF='openbmb/MiniCPM5-1B-GGUF:Q4_K_M'
    PROFILE_USE_YARN=0
    PROFILE_ORIG_CTX=131072
    PROFILE_TAG='minicpm5-1b-q4km'
    ;;
  *)
    printf 'FAIL: unsupported MODEL_PROFILE: %s\n' "$MODEL_PROFILE" >&2
    exit 1
    ;;
esac
MODEL_REF="${MODEL_REF:-$PROFILE_MODEL_REF}"
MODEL_TAG="${MODEL_TAG:-$PROFILE_TAG}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"
THREADS="${THREADS:-4}"
MAX_TOKENS="${MAX_TOKENS:-2048}"
MIN_INPUT_TOKENS="${MIN_INPUT_TOKENS:-60000}"
REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-7200}"
PROMPT_FILE="${PROMPT_FILE:-}"
CTX=65536
ORIG_CTX="${ORIG_CTX:-$PROFILE_ORIG_CTX}"
USE_YARN_64K="${USE_YARN_64K:-$PROFILE_USE_YARN}"
OUT_DIR="${OUT_DIR:-/tmp/${MODEL_TAG}-64k-$(date +%Y%m%d-%H%M%S)}"
LOG="$OUT_DIR/server.log"
REQUEST="$OUT_DIR/request.json"
RESPONSE="$OUT_DIR/response.json"
SAMPLES="$OUT_DIR/samples.tsv"
BASELINE="$OUT_DIR/baseline.tsv"
RUN_START="$OUT_DIR/run-start.txt"
RUN_END="$OUT_DIR/run-end.txt"
KERNEL_WINDOW="$OUT_DIR/kernel-window.txt"
ALERTS="$OUT_DIR/kernel-alerts.txt"
SERVER_PID=""
MONITOR_PID=""
TEST_START_ISO=""

mkdir -p "$OUT_DIR"

read_temp_c() {
  local raw zone

  for zone in /sys/class/thermal/thermal_zone*/temp; do
    if [[ -r "$zone" ]]; then
      awk '{printf "%.1f\n", $1 / 1000}' "$zone"
      return
    fi
  done

  raw=$(vcgencmd measure_temp 2>/dev/null || true)
  if [[ "$raw" =~ temp=([0-9]+([.][0-9]+)?) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return
  fi
  printf 'NA\n'
}

native_context_ok() {
  awk '
    {
      line = $0
      if (line ~ /llama[.]context_length|n_ctx_train/) {
        sub(/^.*(llama[.]context_length|n_ctx_train)/, "", line)
        gsub(/[^0-9]+/, " ", line)
        count = split(line, fields, /[[:space:]]+/)
        for (i = 1; i <= count; i++) {
          if (fields[i] ~ /^[0-9]+$/ && fields[i] + 0 >= 65536) {
            found = 1
          }
        }
      }
    }
    END { exit(found ? 0 : 1) }
  ' "$1"
}

sample_once() {
  local now mem_used mem_available swap_used rss_kb temp_c
  now=$(date '+%Y-%m-%dT%H:%M:%S%z')
  mem_used=$(free -b | awk '/^Mem:/{print $3; exit}')
  mem_available=$(free -b | awk '/^Mem:/{print $7; exit}')
  swap_used=$(free -b | awk '/^Swap:/{print $3; exit}')
  rss_kb=$(awk '/^VmRSS:/{print $2; exit}' "/proc/${SERVER_PID}/status" 2>/dev/null || true)
  temp_c=$(read_temp_c)
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$now" "${mem_used:-NA}" "${mem_available:-NA}" \
    "${swap_used:-NA}" "${rss_kb:-NA}" "$temp_c" >> "$SAMPLES"
}

monitor_resources() {
  printf 'timestamp\tmem_used_bytes\tmem_available_bytes\tswap_used_bytes\tserver_rss_kb\ttemp_c\n' > "$SAMPLES"
  while kill -0 "$SERVER_PID" 2>/dev/null; do
    sample_once
    sleep 1
  done
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [[ -n "$MONITOR_PID" ]] && kill -0 "$MONITOR_PID" 2>/dev/null; then
    wait "$MONITOR_PID" 2>/dev/null || true
  fi
  sample_once 2>/dev/null || true

  date -Is > "$RUN_END"
  printf 'KERNEL_EVIDENCE_PENDING: run from the administrator shell:\n' > "$KERNEL_WINDOW"
  printf 'sudo journalctl -k --since "$(cat "$RUN_START")" --until "$(cat "$RUN_END")" > "%s"\n' \
    "$KERNEL_WINDOW"
  printf 'KERNEL_EVIDENCE_PENDING: administrator must capture the test window and inspect OOM/thermal/throttling events.\n' > "$ALERTS"

  printf '\n=== final memory ===\n'
  free -h || true
  printf '\n=== swap ===\n'
  swapon --show || true
  printf '\nArtifacts: %s\n' "$OUT_DIR"
  printf 'Request result is not the resource gate; inspect baseline.tsv, samples.tsv, metrics.txt, kernel-window.txt and kernel-alerts.txt.\n'
  if [[ "$status" -eq 0 ]]; then
    printf 'REQUEST_OK\n'
  else
    printf 'REQUEST_FAILED\n'
  fi
  printf 'RESOURCE_REVIEW_PENDING\n'
  exit "$status"
}
trap cleanup EXIT INT TERM

for command_name in curl jq free swapon grep ss; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'FAIL: required command not found: %s\n' "$command_name" >&2
    exit 1
  fi
done
if ! command -v "$SERVER" >/dev/null 2>&1; then
  printf 'FAIL: llama-server not found: %s\n' "$SERVER" >&2
  exit 1
fi
if [[ "$HOST" != '127.0.0.1' || "$PORT" != '8080' ]]; then
  printf 'FAIL: the 64K gate is loopback-only and fixed to 127.0.0.1:8080.\n' >&2
  exit 1
fi
if [[ "$MODEL_REF" != "$PROFILE_MODEL_REF" || "$USE_YARN_64K" != "$PROFILE_USE_YARN" || "$ORIG_CTX" != "$PROFILE_ORIG_CTX" ]]; then
  printf 'FAIL: model profile mismatch; use the fail-closed MODEL_PROFILE settings.\n' >&2
  exit 1
fi
for numeric_name in MAX_TOKENS MIN_INPUT_TOKENS REQUEST_TIMEOUT; do
  numeric_value="${!numeric_name}"
  if ! [[ "$numeric_value" =~ ^[0-9]+$ ]] || (( numeric_value <= 0 )); then
    printf 'FAIL: %s must be a positive integer: %s\n' "$numeric_name" "$numeric_value" >&2
    exit 1
  fi
done

HELP=$("$SERVER" --help 2>&1 || true)
VERBOSITY_ARGS=()
if grep -Fq -- '-lv' <<< "$HELP"; then
  VERBOSITY_ARGS=(-lv 4)
elif grep -Fq -- '--verbosity' <<< "$HELP"; then
  VERBOSITY_ARGS=(--verbosity 4)
elif grep -Fq -- '--verbose' <<< "$HELP"; then
  VERBOSITY_ARGS=(--verbose)
else
  echo 'WARNING: llama-server has no recognized verbosity option; native metadata may be unavailable.'
fi
for flag in '--metrics'; do
  if ! grep -Fq -- "$flag" <<< "$HELP"; then
    printf 'FAIL: llama-server does not advertise %s; stop and inspect the build.\n' "$flag" >&2
    printf 'binary: %s\n' "$(command -v "$SERVER")" >&2
    "$SERVER" --version >&2 || true
    printf 'relevant help options:\n' >&2
    grep -Ei 'ctx|rope|yarn|metrics' <<< "$HELP" >&2 || true
    exit 1
  fi
done
if [[ "$USE_YARN_64K" -eq 1 ]]; then
  for flag in '--rope-scaling' '--rope-scale' '--yarn-orig-ctx'; do
    if ! grep -Fq -- "$flag" <<< "$HELP"; then
      printf 'FAIL: llama-server does not advertise %s; stop and inspect the build.\n' "$flag" >&2
      printf 'binary: %s\n' "$(command -v "$SERVER")" >&2
      "$SERVER" --version >&2 || true
      printf 'relevant help options:\n' >&2
      grep -Ei 'ctx|rope|yarn|metrics' <<< "$HELP" >&2 || true
      exit 1
    fi
  done
  if ! grep -Fqi -- 'expands context by a factor of N' <<< "$HELP"; then
    printf 'FAIL: current llama-server help does not confirm --rope-scale N expands by factor N.\n' >&2
    exit 1
  fi
fi

if ss -H -ltn | awk '$4 ~ /:8080$/ {found=1} END {exit !found}'; then
  printf 'FAIL: port %s is occupied; stop the existing server first.\n' "$PORT" >&2
  exit 1
fi

if [[ -z "$PROMPT_FILE" ]]; then
  printf 'FAIL: PROMPT_FILE is required for the 64K gate; a short prompt is smoke test only.\n' >&2
  exit 1
fi
if [[ ! -r "$PROMPT_FILE" ]]; then
  printf 'FAIL: PROMPT_FILE is not readable: %s\n' "$PROMPT_FILE" >&2
  exit 1
fi

jq -n --arg model "$MODEL_REF" --rawfile content "$PROMPT_FILE" --arg max_tokens "$MAX_TOKENS" \
  '{model:$model,messages:[{role:"user",content:$content}],temperature:0.2,max_tokens:($max_tokens|tonumber),stream:false}' \
  > "$REQUEST"

printf 'timestamp\tmem_used_bytes\tmem_available_bytes\tswap_used_bytes\ttemp_c\n' > "$BASELINE"
BASELINE_MEM_USED=$(free -b | awk '/^Mem:/{print $3; exit}')
BASELINE_MEM_AVAILABLE=$(free -b | awk '/^Mem:/{print $7; exit}')
BASELINE_SWAP_USED=$(free -b | awk '/^Swap:/{print $3; exit}')
BASELINE_TEMP=$(read_temp_c)
printf '%s\t%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" \
  "${BASELINE_MEM_USED:-NA}" "${BASELINE_MEM_AVAILABLE:-NA}" \
  "${BASELINE_SWAP_USED:-NA}" "$BASELINE_TEMP" >> "$BASELINE"
TEST_START_ISO=$(date -Is)
printf '%s\n' "$TEST_START_ISO" > "$RUN_START"

ROPE_ARGS=()
if [[ "$USE_YARN_64K" -eq 1 ]]; then
  ROPE_ARGS=(--rope-scaling yarn --rope-scale "$((CTX / ORIG_CTX))" --yarn-orig-ctx "$ORIG_CTX")
  printf 'Starting context=%s with YaRN scale=%s, original context=%s\n' "$CTX" "$((CTX / ORIG_CTX))" "$ORIG_CTX"
else
  printf 'Starting context=%s without YaRN; verify native context metadata first.\n' "$CTX"
fi
"$SERVER" \
  -hf "$MODEL_REF" \
  --host "$HOST" \
  --port "$PORT" \
  -t "$THREADS" \
  -c "$CTX" \
  -np 1 \
  "${VERBOSITY_ARGS[@]}" \
  "${ROPE_ARGS[@]}" \
  --metrics > "$LOG" 2>&1 &
SERVER_PID=$!
monitor_resources &
MONITOR_PID=$!

READY=0
for WAIT in $(seq 1 600); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    printf 'FAIL: server exited before becoming ready.\n' >&2
    tail -n 80 "$LOG" >&2 || true
    exit 1
  fi
  HTTP_CODE=$(curl -s --max-time 2 -o /dev/null -w '%{http_code}' \
    "http://${HOST}:${PORT}/health" || true)
  if [[ "$HTTP_CODE" == "200" ]]; then
    READY=1
    break
  fi
  sleep 1
done

if [[ "$READY" -ne 1 ]]; then
  printf 'FAIL: 64K server did not become ready within 600 seconds.\n' >&2
  tail -n 80 "$LOG" >&2 || true
  exit 1
fi

printf 'health: HTTP 200\n'
if [[ "$MODEL_PROFILE" == 'minicpm5' ]]; then
  META="$OUT_DIR/native-context-metadata.txt"
  grep -Ei 'llama[.]context_length|n_ctx_train|context' "$LOG" > "$META" || true
  if ! native_context_ok "$META"; then
    printf 'FAIL: MiniCPM native context metadata was not saved or does not show >= 65536.\n' >&2
    printf 'metadata evidence: %s\n' "$META" >&2
    tail -n 40 "$META" >&2 || true
    exit 1
  fi
fi
TOKEN_RESPONSE="$OUT_DIR/input_tokens.json"
if ! curl -fsS --max-time "$REQUEST_TIMEOUT" \
  "http://${HOST}:${PORT}/v1/chat/completions/input_tokens" \
  -H 'Content-Type: application/json' \
  -d "@$REQUEST" > "$TOKEN_RESPONSE"; then
  printf 'FAIL: server could not count input tokens; inspect %s.\n' "$LOG" >&2
  exit 1
fi
INPUT_TOKENS=$(jq -r '.input_tokens // empty' "$TOKEN_RESPONSE")
if ! [[ "$INPUT_TOKENS" =~ ^[0-9]+$ ]]; then
  printf 'FAIL: input_tokens was not returned by the server.\n' >&2
  jq . "$TOKEN_RESPONSE" >&2 || true
  exit 1
fi
if (( INPUT_TOKENS < MIN_INPUT_TOKENS )); then
  printf 'FAIL: input_tokens=%s is below the near-full-context minimum=%s.\n' \
    "$INPUT_TOKENS" "$MIN_INPUT_TOKENS" >&2
  exit 1
fi
if (( INPUT_TOKENS + MAX_TOKENS > CTX )); then
  printf 'FAIL: input_tokens=%s plus max_tokens=%s exceeds context=%s.\n' \
    "$INPUT_TOKENS" "$MAX_TOKENS" "$CTX" >&2
  exit 1
fi
printf 'input_tokens: %s (minimum %s); max_tokens: %s; context: %s\n' \
  "$INPUT_TOKENS" "$MIN_INPUT_TOKENS" "$MAX_TOKENS" "$CTX"
if ! curl -fsS --max-time "$REQUEST_TIMEOUT" \
  "http://${HOST}:${PORT}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d "@$REQUEST" > "$RESPONSE"; then
  printf 'FAIL: chat request failed; inspect %s.\n' "$LOG" >&2
  exit 1
fi
if ! jq -e '.choices[0].message.content' "$RESPONSE" >/dev/null; then
  printf 'FAIL: response has no choices[0].message.content.\n' >&2
  jq . "$RESPONSE" >&2 || true
  exit 1
fi

jq -r '.choices[0].message.content' "$RESPONSE"
curl -fsS --max-time 10 "http://${HOST}:${PORT}/metrics" > "$OUT_DIR/metrics.txt" 2>/dev/null || true
printf 'REQUEST_OK: 64K server started and completed the counted near-full-context chat request.\n'
printf 'Note: this is REQUEST_OK only; inspect baseline.tsv, samples.tsv, metrics.txt, kernel-window.txt and kernel-alerts.txt before marking the resource gate passed.\n'
exit 0
