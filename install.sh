#!/usr/bin/env bash
# PlugWarden bootstrap: clone (or update) the repo, then run setup.sh.
#   curl -fsSL https://raw.githubusercontent.com/mk7luke/plugwarden/main/install.sh | sudo bash
#   ... | sudo bash -s -- --dir /srv/plugwarden --mode local     (options are passed to setup.sh)
set -euo pipefail

REPO=https://github.com/mk7luke/plugwarden.git
DIR=/opt/plugwarden

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

ARGS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --dir) [[ $# -ge 2 && -n $2 ]] || die "--dir needs a path"; DIR=$2; shift ;;
    --dir=*) DIR=${1#*=} ;;
    *) ARGS+=("$1") ;;
  esac
  shift
done

(( EUID == 0 )) || die "run as root: curl -fsSL https://raw.githubusercontent.com/mk7luke/plugwarden/main/install.sh | sudo bash"
command -v git >/dev/null 2>&1 || die "git is not installed (Debian/Ubuntu: apt-get install -y git)"
command -v docker >/dev/null 2>&1 || die "Docker is not installed. Install it, then re-run:
    curl -fsSL https://get.docker.com | sh"

if [[ -d $DIR/.git ]]; then
  echo "Updating $DIR"
  git -C "$DIR" pull --ff-only
elif [[ -e $DIR ]]; then
  die "$DIR exists but is not a PlugWarden checkout; move it away or pass --dir PATH"
else
  echo "Cloning PlugWarden into $DIR"
  git clone --depth 1 "$REPO" "$DIR"
fi

cd "$DIR"
# When piped into bash, stdin is the script itself; give setup.sh the terminal so it can ask questions.
if [[ -t 0 ]]; then
  exec ./setup.sh ${ARGS[@]+"${ARGS[@]}"}
elif { : </dev/tty; } 2>/dev/null; then
  exec ./setup.sh ${ARGS[@]+"${ARGS[@]}"} </dev/tty
else
  exec ./setup.sh ${ARGS[@]+"${ARGS[@]}"}
fi
