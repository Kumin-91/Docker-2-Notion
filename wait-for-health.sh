#!/bin/sh
set -eu

container_id=${1:?container ID required}
deadline=$(($(date +%s) + 300))
format='{{.State.Status}} {{.RestartCount}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'

timed_out() {
    printf 'Container %s did not become healthy within 300 seconds.\n' "$container_id" >&2
    exit 1
}

# Bound a stalled Docker request without requiring a separate timeout utility.
inspect_container() {
    docker inspect --format "$format" "$container_id" 2>/dev/null &
    inspect_pid=$!
    while kill -0 "$inspect_pid" 2>/dev/null; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            kill -KILL "$inspect_pid" 2>/dev/null || :
            wait "$inspect_pid" 2>/dev/null || :
            return 124
        fi
        sleep 1
    done
    wait "$inspect_pid"
}

while :; do
    [ "$(date +%s)" -lt "$deadline" ] || timed_out

    if state=$(inspect_container); then
        remaining=$((deadline - $(date +%s)))
        [ "$remaining" -gt 0 ] || timed_out
        case "$state" in
            'running 0 healthy')
                printf 'Container %s is healthy.\n' "$container_id"
                exit 0
                ;;
            'running 0 starting') ;;
            *)
                printf 'Container %s failed health validation (status, restarts, health: %s).\n' "$container_id" "$state" >&2
                exit 1
                ;;
        esac
    else
        case "$?" in
            124) timed_out ;;
            *)
                printf 'Could not inspect container %s.\n' "$container_id" >&2
                exit 1
                ;;
        esac
    fi

    if [ "$remaining" -gt 5 ]; then
        remaining=5
    fi
    sleep "$remaining"
done
