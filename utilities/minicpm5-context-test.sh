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
    echo "ERROR: unsupported MODEL_PROFILE: $MODEL_PROFILE"
    exit 1
    ;;
esac
MODEL_REF="${MODEL_REF:-$PROFILE_MODEL_REF}"
MODEL_TAG="${MODEL_TAG:-$PROFILE_TAG}"
ORIG_CTX="${ORIG_CTX:-$PROFILE_ORIG_CTX}"
USE_YARN_64K="${USE_YARN_64K:-$PROFILE_USE_YARN}"

if ! command -v "$SERVER" >/dev/null 2>&1; then
  echo "ERROR: llama-server not found: $SERVER"
  exit 1
fi
if ! command -v ss >/dev/null 2>&1; then
  echo 'ERROR: ss is required for the socket-level port check.'
  exit 1
fi
if [[ "$MODEL_REF" != "$PROFILE_MODEL_REF" || "$USE_YARN_64K" != "$PROFILE_USE_YARN" || "$ORIG_CTX" != "$PROFILE_ORIG_CTX" ]]; then
  echo 'ERROR: model profile mismatch; use the fail-closed MODEL_PROFILE settings.'
  exit 1
fi

HELP=$("$SERVER" --help 2>&1 || true)
YARN_READY=1
if [ "$USE_YARN_64K" -eq 1 ]; then
  for FLAG in '--rope-scaling' '--rope-scale' '--yarn-orig-ctx'; do
    if ! grep -Fq -- "$FLAG" <<< "$HELP"; then
      echo "WARNING: llama-server does not advertise $FLAG; 64K will be blocked."
      YARN_READY=0
    fi
  done
  if ! grep -Fqi -- 'expands context by a factor of N' <<< "$HELP"; then
    echo 'WARNING: current llama-server help does not confirm --rope-scale factor semantics; 64K will be blocked.'
    YARN_READY=0
  fi
fi
METRICS_ARGS=()
if grep -Fq -- '--metrics' <<< "$HELP"; then
  METRICS_ARGS=(--metrics)
else
  echo 'WARNING: llama-server does not advertise --metrics; metrics endpoint will be skipped.'
fi
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

if ss -H -ltn | awk '$4 ~ /:8080$/ {found=1} END {exit !found}'; then
  echo 'ERROR: port 8080 is already occupied; stop the existing llama-server first.'
  exit 1
fi

read_temp() {
  for ZONE in /sys/class/thermal/thermal_zone*/temp; do
    if [ -r "$ZONE" ]; then
      awk '{printf "%.1f C\n", $1 / 1000}' "$ZONE"
      return
    fi
  done

  TEMP=$(vcgencmd measure_temp 2>/dev/null || true)
  if [ -n "$TEMP" ]; then
    echo "$TEMP"
    return
  fi
  echo 'temperature unavailable'
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

ALL_LEVELS_OK=1
for CTX in 4096 8192 16384 32768 65536; do
  echo "===== context $CTX ====="
  LOG="/tmp/${MODEL_TAG}-${CTX}.log"
  REQUEST="/tmp/${MODEL_TAG}-${CTX}.request.json"
  RESPONSE="/tmp/${MODEL_TAG}-${CTX}.response.json"
  ROPE_ARGS=()
  if [ "$CTX" -eq 65536 ]; then
    if [ "$USE_YARN_64K" -eq 1 ]; then
      if [ "$YARN_READY" -ne 1 ]; then
        echo 'FAIL: context 65536 requires YaRN flags; this binary cannot be used for the 64K gate.'
        echo "binary: $(command -v "$SERVER")"
        "$SERVER" --version 2>&1 || true
        echo 'relevant help options:'
        grep -Ei 'ctx|rope|yarn|metrics' <<< "$HELP" || true
        ALL_LEVELS_OK=0
        break
      fi
      ROPE_ARGS=(--rope-scaling yarn --rope-scale "$((CTX / ORIG_CTX))" --yarn-orig-ctx "$ORIG_CTX")
      echo "64K gate: YaRN scale=$((CTX / ORIG_CTX)), original context=$ORIG_CTX"
    else
      echo '64K gate: no YaRN flags; confirm this model metadata reports native context >= 65536'
    fi
  fi

  jq -n --arg model "$MODEL_REF" \
    '{model:$model,messages:[{role:"user",content:"回覆 OK，並說明目前 context 測試值。"}],temperature:0.2,max_tokens:64,stream:false}' \
    >"$REQUEST"

  "$SERVER" \
    -hf "$MODEL_REF" \
    --host 127.0.0.1 \
    --port 8080 \
    -t 4 \
    -c "$CTX" \
    -np 1 \
    "${VERBOSITY_ARGS[@]}" \
    "${METRICS_ARGS[@]}" \
    "${ROPE_ARGS[@]}" >"$LOG" 2>&1 &
  PID=$!

  READY=0
  for WAIT in $(seq 1 300); do
    if ! kill -0 "$PID" 2>/dev/null; then
      echo "FAIL: llama-server exited before context $CTX became ready"
      tail -n 40 "$LOG"
      ALL_LEVELS_OK=0
      break
    fi
    HEALTH_CODE=$(curl -s --max-time 2 -o /dev/null -w '%{http_code}' \
      http://127.0.0.1:8080/health || true)
    if [ "$HEALTH_CODE" = "200" ]; then
      READY=1
      break
    fi
    sleep 1
  done

  if [ "$READY" -ne 1 ]; then
    echo "FAIL: context $CTX did not become ready"
    tail -n 40 "$LOG"
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
    ALL_LEVELS_OK=0
    continue
  fi

  if [ "$MODEL_PROFILE" = 'minicpm5' ]; then
    META="/tmp/${MODEL_TAG}-${CTX}.native-context-metadata"
    grep -Ei 'llama[.]context_length|n_ctx_train|context' "$LOG" > "$META" || true
    if ! native_context_ok "$META"; then
      echo 'FAIL: MiniCPM native context metadata was not saved or does not show >= 65536'
      echo "metadata evidence: $META"
      tail -n 40 "$META" 2>/dev/null || true
      kill "$PID" 2>/dev/null || true
      wait "$PID" 2>/dev/null || true
      ALL_LEVELS_OK=0
      break
    fi
  fi

  if ! curl -fsS --max-time 600 http://127.0.0.1:8080/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d "@$REQUEST" >"$RESPONSE"; then
    echo "FAIL: context $CTX chat request failed; inspect $LOG and $RESPONSE"
    ALL_LEVELS_OK=0
  elif ! jq -e '.choices[0].message.content' "$RESPONSE" >/dev/null; then
    echo "FAIL: context $CTX response has no choices[0].message.content"
    jq . "$RESPONSE" || true
    ALL_LEVELS_OK=0
  else
    jq -r '.choices[0].message.content' "$RESPONSE"
  fi

  free -h
  swapon --show
  read_temp
  curl -fsS --max-time 10 http://127.0.0.1:8080/metrics \
    >"/tmp/${MODEL_TAG}-${CTX}.metrics" 2>/dev/null || true
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    wait "$PID" 2>/dev/null || true
  fi
  echo "===== context $CTX done ====="
done
if [ "$ALL_LEVELS_OK" -eq 1 ]; then
  echo 'REQUEST_SWEEP_OK: all context levels completed the short smoke request.'
else
  echo 'REQUEST_SWEEP_FAILED: one or more context levels did not complete.'
  exit 1
fi
