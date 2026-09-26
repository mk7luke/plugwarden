#!/usr/bin/env bash
# PlugWarden setup: finds your AMP datastore, writes .env, starts the container.
# Safe to re-run. Never writes inside the datastore and never touches AMP instances.
# Usage: ./setup.sh [--dry-run] [--yes] [--mode cf|local] ...   (see --help)
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

CONTAINER=lgt-amp-sync
PORT=8078
ORIG_ARGS=("$@")

# Test hooks (not for normal use):
#   PLUGWARDEN_SETUP_NO_SUDO=1        run everything as the current user, never sudo
#   PLUGWARDEN_SETUP_AMPINSTMGR=path  ampinstmgr to use; set but empty = pretend it is not installed
#   PLUGWARDEN_SETUP_AMP_HOME=dir     home of the AMP user (for the .ampdata fallback scan)
NO_SUDO=${PLUGWARDEN_SETUP_NO_SUDO:-0}

YES=0 DRY_RUN=0 SKIP_CF_CHECK=0
AMP_USER=amp DATASTORE="" MODE="" CF_TEAM="" CF_AUD="" PUBLIC_HOST="" CONTACT="" CONTACT_SET=0

# ------------------------------------------------------------------ output

if [[ -t 1 && -z ${NO_COLOR:-} ]]; then
  C_B=$'\e[1m' C_DIM=$'\e[2m' C_RED=$'\e[31m' C_GRN=$'\e[32m' C_YEL=$'\e[33m' C_BLU=$'\e[34m' C_R=$'\e[0m'
else
  C_B="" C_DIM="" C_RED="" C_GRN="" C_YEL="" C_BLU="" C_R=""
fi
step() { printf '\n%s==> %s%s\n' "$C_BLU$C_B" "$*" "$C_R"; }
ok()   { printf '  %s✓%s %s\n' "$C_GRN" "$C_R" "$*"; }
note() { printf '  %s\n' "$*"; }
warn() { printf '  %s! %s%s\n' "$C_YEL" "$*" "$C_R" >&2; }
die()  { printf '\n%sError:%s %s\n' "$C_RED$C_B" "$C_R" "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
PlugWarden setup: detects your AMP datastore, writes .env and starts the container.

Usage: ./setup.sh [options]

  --dry-run           detect and show what would be written/done; change nothing
  -y, --yes           non-interactive: accept defaults and overwrite .env without asking
  --amp-user NAME     the user AMP runs as (default: amp)
  --datastore PATH    AMP datastore to manage (default: detected via ampinstmgr)
  --mode cf|local     cf    = Cloudflare Access login (recommended)
                      local = no login, reachable only through an SSH tunnel
  --cf-team DOMAIN    Cloudflare Access team domain, e.g. yourteam.cloudflareaccess.com
  --cf-aud TAG        Application Audience (AUD) tag of your Access application
  --hostname HOST     public hostname your Cloudflare Tunnel routes to this server
  --contact EMAIL     contact sent to Modrinth/Hangar/GitHub in the User-Agent (optional)
  --skip-cf-check     don't verify the team domain online
  -h, --help          show this help

Examples:
  sudo ./setup.sh
  sudo ./setup.sh --yes --mode local
  sudo ./setup.sh --yes --mode cf --cf-team yourteam.cloudflareaccess.com \
       --cf-aud 0123abcd... --hostname plugins.example.com --contact you@example.com
EOF
}

# ------------------------------------------------------------------ arguments

need_arg() { [[ $# -ge 2 && -n $2 ]] || die "$1 needs a value (see --help)"; }
while [[ $# -gt 0 ]]; do
  case $1 in
    --*=*) set -- "${1%%=*}" "${1#*=}" "${@:2}"; continue ;;
    -y|--yes) YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --skip-cf-check) SKIP_CF_CHECK=1 ;;
    --amp-user) need_arg "$@"; AMP_USER=$2; shift ;;
    --datastore) need_arg "$@"; DATASTORE=$2; shift ;;
    --mode) need_arg "$@"; MODE=$2; shift ;;
    --cf-team) need_arg "$@"; CF_TEAM=$2; shift ;;
    --cf-aud) need_arg "$@"; CF_AUD=$2; shift ;;
    --hostname) need_arg "$@"; PUBLIC_HOST=$2; shift ;;
    --contact) [[ $# -ge 2 ]] || die "--contact needs a value"; CONTACT=$2; CONTACT_SET=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done
case $MODE in ""|cf|local) ;; *) die "--mode must be 'cf' or 'local', not '$MODE'" ;; esac

INTERACTIVE=0
if (( !YES )); then
  [[ -t 0 ]] || die "no terminal for questions. Re-run with --yes plus flags (see --help)."
  INTERACTIVE=1
fi

ask() {  # ask VAR "question" [default]
  local answer=""
  if (( INTERACTIVE )); then
    read -r -p "  $2${3:+ [$3]}: " answer || true
  fi
  printf -v "$1" '%s' "${answer:-${3:-}}"
}
confirm() {  # confirm "question" -> 0 on yes (default yes)
  (( YES )) && return 0
  local answer=""
  read -r -p "  $1 [Y/n]: " answer || true
  [[ ! $answer =~ ^[Nn] ]]
}

# ------------------------------------------------------------------ privileges

ME=$(id -un)
as_root() {
  if (( EUID == 0 || NO_SUDO )); then "$@"; else sudo -- "$@"; fi
}
as_amp() {  # run a command as the AMP user
  if (( NO_SUDO )) || [[ $ME == "$AMP_USER" ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -H -u "$AMP_USER" -- "$@"
  else
    runuser -u "$AMP_USER" -- env HOME="$AMP_HOME" "$@"
  fi
}

# ------------------------------------------------------------------ preflight

printf '%sPlugWarden setup%s%s\n' "$C_B" "$C_R" "$( ((DRY_RUN)) && printf ' %s(dry run: nothing will be changed)%s' "$C_DIM" "$C_R")"
step "Checking this server"

[[ $(uname -s) == Linux ]] || die "PlugWarden setup runs on Linux (the host AMP runs on)."
(( BASH_VERSINFO[0] >= 4 )) || die "bash 4 or newer is required."

if (( EUID != 0 && !NO_SUDO )); then
  command -v sudo >/dev/null 2>&1 || die "run this as root (sudo is not installed)."
  if (( !DRY_RUN )); then
    note "Re-running with sudo (needed to read the AMP datastore and run Docker)..."
    exec sudo -- bash "$SCRIPT_DIR/setup.sh" "${ORIG_ARGS[@]}"
  fi
fi
ok "Linux, $( ((EUID == 0)) && echo root || echo "user $ME")"

soft_fail() {  # fatal for a real run, a warning in --dry-run
  if (( DRY_RUN )); then warn "$1"; else die "$1"; fi
}

DOCKER=()
if ! command -v docker >/dev/null 2>&1; then
  soft_fail "Docker is not installed. Install it with the official script, then re-run:
    curl -fsSL https://get.docker.com | sh"
elif docker info >/dev/null 2>&1; then
  DOCKER=(docker)
elif (( EUID != 0 )) && command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  DOCKER=(sudo -n docker)
else
  soft_fail "Docker is installed but not running (or not accessible). Start it: systemctl enable --now docker"
fi
if (( ${#DOCKER[@]} )); then
  if compose_v=$("${DOCKER[@]}" compose version --short 2>/dev/null); then
    ok "Docker $("${DOCKER[@]}" version --format '{{.Server.Version}}' 2>/dev/null || echo "?"), Compose $compose_v"
  else
    soft_fail "Docker Compose v2 ('docker compose') is missing. Install the plugin, then re-run:
    apt-get install docker-compose-plugin     (Debian/Ubuntu; dnf install docker-compose-plugin on Fedora/RHEL)"
    DOCKER=()
  fi
fi

# Port 8078 on loopback: free, or already served by this checkout's container.
EXISTING_DIR="" EXISTING_STATE=""
if (( ${#DOCKER[@]} )); then
  if info=$("${DOCKER[@]}" ps -a --filter "name=^${CONTAINER}\$" \
      --format '{{.Label "com.docker.compose.project.working_dir"}}|{{.State}}' 2>/dev/null) && [[ -n $info ]]; then
    EXISTING_DIR=${info%%|*} EXISTING_STATE=${info#*|}
  fi
fi
if [[ -n $EXISTING_DIR && $EXISTING_DIR != "$SCRIPT_DIR" ]]; then
  soft_fail "A PlugWarden container ($CONTAINER, $EXISTING_STATE) already exists from $EXISTING_DIR.
    Run setup.sh from that checkout instead, or remove it first: (cd $EXISTING_DIR && docker compose down)"
fi
if timeout 2 bash -c ": >/dev/tcp/127.0.0.1/$PORT" 2>/dev/null; then
  if [[ $EXISTING_DIR == "$SCRIPT_DIR" && $EXISTING_STATE == running ]]; then
    ok "Port $PORT is served by this PlugWarden (it will be updated)"
  elif [[ -n $EXISTING_DIR && $EXISTING_STATE == running ]]; then
    warn "Port $PORT is used by the PlugWarden container from $EXISTING_DIR"
  else
    soft_fail "Port $PORT on 127.0.0.1 is already in use by another program. Free it first (see: ss -ltnp 'sport = :$PORT')."
  fi
else
  ok "Port $PORT on 127.0.0.1 is free"
fi

# ------------------------------------------------------------------ AMP user

step "Looking for AMP"
pw_entry=$(getent passwd "$AMP_USER" || true)
[[ -n $pw_entry ]] || die "there is no user '$AMP_USER'. Pass the user AMP runs as with --amp-user NAME
    (hint: ls -ld /home/*/.ampdata)"
AMP_UID=$(id -u "$AMP_USER") AMP_GID=$(id -g "$AMP_USER")
AMP_HOME=${PLUGWARDEN_SETUP_AMP_HOME:-$(cut -d: -f6 <<<"$pw_entry")}
(( AMP_UID != 0 )) || die "the AMP user must not be root; pass the user AMP runs as with --amp-user NAME"
ok "AMP user $AMP_USER (uid $AMP_UID, gid $AMP_GID)"

find_ampinstmgr() {
  if [[ -n ${PLUGWARDEN_SETUP_AMPINSTMGR+x} ]]; then printf '%s' "$PLUGWARDEN_SETUP_AMPINSTMGR"; return; fi
  command -v ampinstmgr 2>/dev/null && return
  [[ -x /opt/cubecoders/amp/ampinstmgr ]] && printf '%s' /opt/cubecoders/amp/ampinstmgr
  return 0
}
AMPINSTMGR=$(find_ampinstmgr)

# Parse ampinstmgr "Key │ Value" blocks into: name<TAB>module<TAB>data path
parse_blocks() {
  awk -F'│' '
    function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
    function flush() { if (name != "") print name "\t" mod "\t" path; name = mod = path = "" }
    { gsub(/\r/, ""); gsub(/\033\[[0-9;]*m/, "") }
    NF < 2 { next }
    { k = trim($1); v = trim($2) }
    k == "Instance ID" { flush() }
    k == "Instance Name" { if (name != "") flush(); name = v }
    k == "Module" { mod = v }
    k == "Data Path" { path = v }
    END { flush() }'
}
# Parse the "ampinstmgr -t" table into: name<TAB>module (names ending in "..." are truncated; skipped)
parse_table() {
  awk -F'│' '
    function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
    { gsub(/\r/, "") }
    NF >= 3 && trim($1) != "Instance Name" && trim($1) !~ /\.\.\.$/ { print trim($1) "\t" trim($3) }'
}

MC_PATHS=()   # data paths of Minecraft instances reported by ampinstmgr
if [[ -n $AMPINSTMGR ]]; then
  if list=$(as_amp "$AMPINSTMGR" -l 2>/dev/null); then
    ok "ampinstmgr found ($AMPINSTMGR)"
    rows=$(parse_blocks <<<"$list")
    if [[ -z $rows ]]; then  # older ampinstmgr: table of names, then -i per instance
      rows=""
      while IFS=$'\t' read -r name module; do
        [[ ${module,,} == *minecraft* ]] || continue
        rows+=$(as_amp "$AMPINSTMGR" -i "$name" 2>/dev/null | parse_blocks || true)$'\n'
      done < <(as_amp "$AMPINSTMGR" -t 2>/dev/null | parse_table)
    fi
    while IFS=$'\t' read -r name module path; do
      [[ -n $name && ${module,,} == *minecraft* ]] || continue
      if [[ -z $path ]]; then
        path=$(as_amp "$AMPINSTMGR" -i "$name" 2>/dev/null | parse_blocks | awk -F'\t' 'NR == 1 { print $3 }') || true
      fi
      if [[ $path == /* ]]; then MC_PATHS+=("${path%/}"); else warn "no Data Path for instance $name; skipped"; fi
    done <<<"$rows"
  else
    warn "ampinstmgr -l failed as $AMP_USER; scanning folders instead"
  fi
else
  note "ampinstmgr not found; scanning folders instead"
fi

# probe PATH... -> "path<TAB>platform" ("-" = no Minecraft/plugins). Runs as the AMP user, read-only.
# shellcheck disable=SC2016  # expanded by the inner bash
PROBE='for i; do d=$i/Minecraft
  if [ ! -d "$d/plugins" ]; then printf "%s\t-\n" "$i"; continue; fi
  p=unknown
  if [ -e "$d/velocity.toml" ] || compgen -G "$d/velocity*.jar" >/dev/null; then p=Velocity
  elif [ -e "$d/purpur.jar" ]; then p=Purpur
  elif [ -e "$d/paperclip.jar" ] || compgen -G "$d/paper*.jar" >/dev/null; then p=Paper
  elif [ -e "$d/fabric.jar" ]; then p=Fabric
  elif compgen -G "$d/spigot*.jar" >/dev/null; then p=Spigot
  fi
  printf "%s\t%s\n" "$i" "$p"; done'
probe() { (( $# )) || return 0; as_amp bash -c "$PROBE" probe "$@" 2>/dev/null || true; }
# shellcheck disable=SC2016
subdirs() { as_amp bash -c 'for d in "$1"/*/; do [ -d "$d" ] && printf "%s\n" "${d%/}"; done; true' _ "$1" 2>/dev/null || true; }

declare -A DS_COUNT=() PLATFORM=()
DS_ORDER=() SKIPPED=()
while IFS=$'\t' read -r path platform; do
  [[ -n $path ]] || continue
  if [[ $platform == - ]]; then SKIPPED+=("$path"); continue; fi
  ds=$(dirname -- "$path")
  [[ -n ${DS_COUNT[$ds]+x} ]] || { DS_COUNT[$ds]=0; DS_ORDER+=("$ds"); }
  DS_COUNT[$ds]=$(( DS_COUNT[$ds] + 1 ))
  PLATFORM[$path]=$platform
done < <(probe ${MC_PATHS[@]+"${MC_PATHS[@]}"})

scan_datastore() {  # fill PLATFORM for Minecraft instances directly under $1
  local path platform dirs
  mapfile -t dirs < <(subdirs "$1")
  while IFS=$'\t' read -r path platform; do
    [[ -n $path && $platform != - ]] && PLATFORM[$path]=$platform
  done < <(probe ${dirs[@]+"${dirs[@]}"})
  return 0
}

OTHER_DS=()
if [[ -n $DATASTORE ]]; then
  [[ $DATASTORE == / ]] || DATASTORE=${DATASTORE%/}
  [[ -n ${DS_COUNT[$DATASTORE]+x} ]] || scan_datastore "$DATASTORE"
  for ds in ${DS_ORDER[@]+"${DS_ORDER[@]}"}; do [[ $ds == "$DATASTORE" ]] || OTHER_DS+=("$ds"); done
elif (( ${#DS_ORDER[@]} )); then
  DATASTORE=${DS_ORDER[0]}
  for ds in "${DS_ORDER[@]}"; do (( DS_COUNT[$ds] > DS_COUNT[$DATASTORE] )) && DATASTORE=$ds; done
  for ds in "${DS_ORDER[@]}"; do [[ $ds == "$DATASTORE" ]] || OTHER_DS+=("$ds"); done
else
  DATASTORE=$AMP_HOME/.ampdata/instances
  note "Scanning $DATASTORE (AMP's default datastore)"
  scan_datastore "$DATASTORE"
fi

INSTANCES=()
for path in "${!PLATFORM[@]}"; do
  [[ $(dirname -- "$path") == "$DATASTORE" ]] && INSTANCES+=("$path")
done
(( ${#INSTANCES[@]} )) || die "no Minecraft servers with a Minecraft/plugins folder found in $DATASTORE.
    Pass your AMP datastore (the folder that holds the instance folders) with --datastore PATH,
    and the user AMP runs as with --amp-user NAME."
mapfile -t INSTANCES < <(printf '%s\n' "${INSTANCES[@]}" | sort)

[[ $DATASTORE =~ ^/[A-Za-z0-9._@+/-]+$ ]] || die "the datastore path '$DATASTORE' contains characters Docker Compose can't mount
    (allowed: letters, digits and . _ @ + - /)."

step "Found"
printf '  %-12s %s\n' "Datastore" "$DATASTORE" "AMP user" "$AMP_USER (uid $AMP_UID, gid $AMP_GID)"
printf '  %-12s %s\n' "Servers" "${#INSTANCES[@]} Minecraft server(s)"
printf '    %s%-28s %s%s\n' "$C_DIM" "NAME" "PLATFORM" "$C_R"
for path in "${INSTANCES[@]}"; do
  printf '    %-28s %s\n' "$(basename -- "$path")" "${PLATFORM[$path]}"
done
for path in ${SKIPPED[@]+"${SKIPPED[@]}"}; do
  [[ $(dirname -- "$path") == "$DATASTORE" ]] && note "${C_DIM}skipped $(basename -- "$path"): no Minecraft/plugins folder${C_R}"
done
if (( ${#OTHER_DS[@]} )); then
  warn "Minecraft servers also live in other datastores; PlugWarden manages one datastore per install:"
  for ds in "${OTHER_DS[@]}"; do warn "  $ds (${DS_COUNT[$ds]} server(s))"; done
  warn "Using $DATASTORE. Pick another with --datastore PATH."
fi

# ------------------------------------------------------------------ access mode

env_get() {  # env_get KEY FILE -> last active value
  [[ -r $2 ]] || return 0
  sed -n "s/^$1=//p" "$2" | tail -n1
}
OLD_ENV=""
[[ -e .env ]] && OLD_ENV=.env
if [[ -n $OLD_ENV && ! -r $OLD_ENV ]]; then
  warn ".env exists but is not readable as $ME; its current values can't be shown"
fi

step "Access"
if [[ -z $MODE ]]; then
  case $(env_get LGT_AUTH "$OLD_ENV") in none) MODE=local ;; *) MODE=cf ;; esac
  if (( INTERACTIVE )); then
    note "How will you reach PlugWarden?"
    note "  1) Cloudflare Access: log in at your own hostname through a Cloudflare Tunnel (recommended)"
    note "  2) Local only: no login; reachable only through an SSH tunnel to this server"
    choice=""
    ask choice "Choose 1 or 2" "$( [[ $MODE == local ]] && echo 2 || echo 1)"
    case $choice in 2|local) MODE=local ;; *) MODE=cf ;; esac
  elif [[ -z $(env_get LGT_AUTH "$OLD_ENV") && -z $CF_TEAM$CF_AUD$PUBLIC_HOST ]]; then
    die "choose how PlugWarden is reached: --mode local (SSH tunnel only), or
    --mode cf --cf-team yourteam.cloudflareaccess.com --cf-aud TAG --hostname plugins.example.com"
  fi
fi

HOST_RE='^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$'
norm_team() {
  local t=${1#https://}; t=${t#http://}; t=${t%%/*}; t=${t,,}
  [[ -z $t || $t == *.* ]] || t=$t.cloudflareaccess.com
  printf '%s' "$t"
}
check_team() {  # 0 when https://TEAM/cdn-cgi/access/certs lists keys
  (( SKIP_CF_CHECK )) && return 0
  command -v curl >/dev/null 2>&1 || { warn "curl not found; can't verify the team domain"; return 0; }
  local certs
  certs=$(curl -fsS --max-time 10 "https://$1/cdn-cgi/access/certs" 2>/dev/null) || return 1
  [[ $certs == *'"keys"'* ]]
}

if [[ $MODE == cf ]]; then
  CF_TEAM=$(norm_team "${CF_TEAM:-$(env_get LGT_CF_TEAM_DOMAIN "$OLD_ENV")}")
  CF_AUD=${CF_AUD:-$(env_get LGT_CF_AUD "$OLD_ENV")}
  PUBLIC_HOST=${PUBLIC_HOST:-$(env_get LGT_HOSTNAME "$OLD_ENV")}
  # the .env.example placeholders are not defaults
  [[ $CF_TEAM == yourteam.cloudflareaccess.com ]] && CF_TEAM=""
  [[ $CF_AUD == paste-the-* ]] && CF_AUD=""
  [[ $PUBLIC_HOST == plugins.example.com ]] && PUBLIC_HOST=""
  (( INTERACTIVE )) && note "Cloudflare Zero Trust → Access → Applications → your app. Team domain: Settings → Custom Pages."
  while :; do
    ask CF_TEAM "Team domain (yourteam.cloudflareaccess.com)" "$CF_TEAM"
    CF_TEAM=$(norm_team "$CF_TEAM")
    if [[ ! $CF_TEAM =~ $HOST_RE ]]; then
      (( INTERACTIVE )) || die "--cf-team is missing or invalid (e.g. yourteam.cloudflareaccess.com)"
      warn "That doesn't look like a domain."; continue
    fi
    if check_team "$CF_TEAM"; then
      (( SKIP_CF_CHECK )) || ok "https://$CF_TEAM/cdn-cgi/access/certs lists signing keys"
      break
    fi
    (( INTERACTIVE )) || die "https://$CF_TEAM/cdn-cgi/access/certs did not return signing keys. Check the team domain
    (or pass --skip-cf-check if this server can't reach Cloudflare right now)."
    warn "https://$CF_TEAM/cdn-cgi/access/certs did not return signing keys. Check the team domain."
  done
  (( INTERACTIVE )) && note "AUD tag: your Access application → Overview → Application Audience (AUD) Tag."
  while :; do
    ask CF_AUD "Application Audience (AUD) tag" "$CF_AUD"
    [[ $CF_AUD =~ ^[A-Za-z0-9]{16,128}$ ]] && break
    (( INTERACTIVE )) || die "--cf-aud is missing or invalid (the 64-character AUD tag of your Access application)"
    warn "The AUD tag is a long string of letters and digits (64 characters)."
  done
  while :; do
    ask PUBLIC_HOST "Public hostname (e.g. plugins.example.com)" "$PUBLIC_HOST"
    PUBLIC_HOST=${PUBLIC_HOST,,}; PUBLIC_HOST=${PUBLIC_HOST#https://}; PUBLIC_HOST=${PUBLIC_HOST%%/*}
    [[ $PUBLIC_HOST =~ $HOST_RE ]] && break
    (( INTERACTIVE )) || die "--hostname is missing or invalid (the hostname your Cloudflare Tunnel routes here)"
    warn "That doesn't look like a hostname."
  done
  ok "Cloudflare Access: $PUBLIC_HOST (team $CF_TEAM)"
else
  ok "Local only: no login, published on 127.0.0.1:$PORT (reach it through an SSH tunnel)"
fi

(( CONTACT_SET )) || CONTACT=$(env_get LGT_CONTACT "$OLD_ENV")
if (( INTERACTIVE && !CONTACT_SET )); then
  ask CONTACT "Contact email for Modrinth/Hangar/GitHub API requests (optional, Enter to skip)" "$CONTACT"
fi
case $CONTACT in *[[:space:]\"\'\\\$\`#]*) die "--contact must be a single word such as you@example.com" ;; esac

# ------------------------------------------------------------------ .env

SETS=("AMP_DATASTORE=$DATASTORE" "LGT_BASE_OVERRIDE=$DATASTORE"
      "PLUGWARDEN_UID=$AMP_UID" "PLUGWARDEN_GID=$AMP_GID")
if [[ $MODE == cf ]]; then
  SETS+=("LGT_AUTH=cf-access" "LGT_CF_TEAM_DOMAIN=$CF_TEAM" "LGT_CF_AUD=$CF_AUD" "LGT_HOSTNAME=$PUBLIC_HOST")
  UNSETS=(LGT_AUTH_ALLOW_INSECURE)
else
  # the container binds 0.0.0.0 behind a 127.0.0.1-only port, so auth "none" needs ALLOW_INSECURE,
  # and the app refuses it whenever LGT_HOSTNAME is set
  SETS+=("LGT_AUTH=none" "LGT_AUTH_ALLOW_INSECURE=1")
  UNSETS=(LGT_CF_TEAM_DOMAIN LGT_CF_AUD LGT_HOSTNAME)
fi
if [[ -n $CONTACT ]]; then SETS+=("LGT_CONTACT=$CONTACT"); else UNSETS+=(LGT_CONTACT); fi

# Rewrite KEY= lines of the base file (existing .env, else .env.example); keep everything else.
render_env() {
  PW_SETS=$(printf '%s\n' "${SETS[@]}") PW_UNSETS=$(printf '%s\n' "${UNSETS[@]}") awk '
    BEGIN {
      n = split(ENVIRON["PW_SETS"], a, "\n")
      for (i = 1; i <= n; i++) if (a[i] != "") {
        k = a[i]; sub(/=.*/, "", k); val[k] = substr(a[i], length(k) + 2); order[++no] = k
      }
      m = split(ENVIRON["PW_UNSETS"], b, "\n")
      for (i = 1; i <= m; i++) if (b[i] != "") drop[b[i]] = 1
    }
    {
      line = $0; key = line; sub(/^#[ \t]*/, "", key)
      if (key ~ /^[A-Z_][A-Z0-9_]*=/) {
        sub(/=.*/, "", key)
        if (key in val) {
          if (key in done) { if (line ~ /^#/) print line; next }
          print key "=" val[key]; done[key] = 1; next
        }
        if ((key in drop) && line !~ /^#/) { print "#" line; next }
      }
      print line
    }
    END {
      for (i = 1; i <= no; i++) if (!(order[i] in done)) {
        if (!hdr) { print ""; print "# Set by setup.sh"; hdr = 1 }
        print order[i] "=" val[order[i]]
      }
    }'
}
if [[ -n $OLD_ENV && -r $OLD_ENV ]]; then base=$OLD_ENV; else base=.env.example; fi
[[ -r $base ]] || die "$base is missing; run setup.sh from the PlugWarden checkout"
NEW_ENV=$(render_env <"$base")

step "Configuration"
if (( DRY_RUN )); then
  if [[ -n $OLD_ENV && -r $OLD_ENV ]]; then
    if [[ $(cat "$OLD_ENV") == "$NEW_ENV" ]]; then ok ".env is already up to date"
    else note "Would update .env:"; diff -u --label .env --label ".env (new)" "$OLD_ENV" - <<<"$NEW_ENV" || true; fi
  fi
  note "----- .env (would write, chmod 600) -----"
  printf '%s\n' "$NEW_ENV"
  note "----- end .env -----"
  note "Would create $SCRIPT_DIR/data owned by $AMP_UID:$AMP_GID (chmod 700)"
  note "Would run: docker compose up -d --build   (only the $CONTAINER container)"
  printf '\n%sDry run finished. Nothing was changed.%s\n' "$C_B" "$C_R"
  exit 0
fi

write_env=1
if [[ -n $OLD_ENV ]]; then
  if [[ $(cat "$OLD_ENV") == "$NEW_ENV" ]]; then
    ok ".env is already up to date"; write_env=0
  else
    note "Changes to .env:"
    diff -u --label .env --label ".env (new)" "$OLD_ENV" - <<<"$NEW_ENV" || true
    confirm "Write these changes to .env?" || die "left .env unchanged; nothing was started."
  fi
fi
if (( write_env )); then
  tmp=$(umask 077 && mktemp "$SCRIPT_DIR/.env.XXXXXX")
  printf '%s\n' "$NEW_ENV" >"$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" .env
  ok "Wrote .env (chmod 600)"
fi

# State dir: owned by the AMP user (the container runs as it), private.
mkdir -p data
if [[ $(stat -c %u:%g data) != "$AMP_UID:$AMP_GID" ]]; then
  as_root chown -R "$AMP_UID:$AMP_GID" data
fi
as_root chmod 700 data
ok "State folder $SCRIPT_DIR/data (owner $AMP_UID:$AMP_GID, chmod 700)"

# ------------------------------------------------------------------ start

step "Starting PlugWarden (docker compose up -d --build)"
"${DOCKER[@]}" compose up -d --build

note "Waiting for it to become healthy..."
healthy=0
for _ in $(seq 1 60); do
  state=$("${DOCKER[@]}" inspect -f '{{.State.Status}} {{.RestartCount}}' "$CONTAINER" 2>/dev/null || echo "missing 0")
  if [[ $state != running\ 0 ]]; then break; fi
  if [[ $("${DOCKER[@]}" inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$CONTAINER" 2>/dev/null) == healthy ]] ||
     { command -v curl >/dev/null 2>&1 && [[ $(curl -fsS --max-time 3 "http://127.0.0.1:$PORT/healthz" 2>/dev/null) == *'"ok"'* ]]; }; then
    healthy=1; break
  fi
  sleep 2
done
if (( !healthy )); then
  "${DOCKER[@]}" logs --tail 20 "$CONTAINER" 2>&1 | sed 's/^/    /' >&2 || true
  die "PlugWarden did not come up (container: ${state:-unknown}). Last log lines are above;
    full logs: docker logs $CONTAINER    After fixing .env, re-run: sudo ./setup.sh"
fi
ok "PlugWarden is running and healthy"

step "Next steps"
if [[ $MODE == cf ]]; then
  note "1. In your Cloudflare Tunnel, add a public hostname:"
  note "     $PUBLIC_HOST  →  http://localhost:$PORT"
  note "   (cloudflared config.yml: - hostname: $PUBLIC_HOST"
  note "                               service: http://localhost:$PORT)"
  note "2. Make sure your Access application covers $PUBLIC_HOST."
  note "3. Open ${C_B}https://$PUBLIC_HOST${C_R} and log in."
else
  host=$(hostname -f 2>/dev/null || hostname)
  note "1. From your computer, open an SSH tunnel to this server:"
  note "     ${C_B}ssh -L $PORT:127.0.0.1:$PORT ${SUDO_USER:-$ME}@$host${C_R}"
  note "2. Open ${C_B}http://localhost:$PORT${C_R} in your browser while the tunnel is open."
fi
note ""
note "PlugWarden is read-only until you act: nothing changes on your servers until you deploy or update."
note "Start with ${C_B}Check updates${C_R}. Re-run this script any time; it keeps your settings."
