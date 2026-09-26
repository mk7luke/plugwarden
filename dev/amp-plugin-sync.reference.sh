#!/usr/bin/env bash
set -euo pipefail

# =========================
# AMP Multi-Instance Plugin Sync (datastore-based)
# Source-of-truth: one instance's /Minecraft/plugins
# Target: all instances under BASE except proxy + user excludes
# =========================

BASE="/mnt/storage_ssd/ssd-live"
SOURCE_INSTANCE="elChapo01"
PROXY_INSTANCE="M0-proxy01"
REL_PLUGINS="Minecraft/plugins"

# Defaults
DRY_RUN=0
AUTO_YES=0
INSTALL_IF_MISSING=0
DO_DELETE=0
MAKE_BACKUPS=0
EXCLUDES_CSV=""

# What to sync/delete
PLUGIN_FILES=()   # jars (and any files) directly under plugins/
FOLDERS=()        # plugin data folders directly under plugins/
PATHS=()          # specific file/subfolder paths relative to plugins/ (e.g. Essentials/config.yml)

usage() {
  cat <<'EOF'
Usage:
  amp-plugin-sync [options]

Options:
  --plugin <file.jar>        Sync a plugin jar/file from source plugins/ to targets plugins/
                             (can be specified multiple times)
  --folder <FolderName>      Sync a plugin folder from source plugins/ to targets plugins/
                             (can be specified multiple times)
  --path <rel/path>          Sync a specific file or subfolder inside plugins/
                             e.g. --path Essentials/config.yml
                                  --path Essentials/messages
                                  --path LuckPerms/config.yml
                             (can be specified multiple times)

  --install                  Allow copying to targets even if missing
                             (default = only update if exists on target)
  --delete                   Delete the specified --plugin/--folder/--path from targets instead of syncing
  --exclude <a,b,c>          Comma-separated instance names to exclude (in addition to proxy)
  --dry-run                  Show what would happen (rsync --dry-run), do not modify files
  --backup                   Create .bak.<timestamp> backup(s) before overwriting (default: OFF)
  --yes                      Do not prompt for confirmation
  -h, --help                 Show help

Examples:
  # Sync only specific Essentials config files (safe: avoids userdata)
  amp-plugin-sync --path Essentials/config.yml --path Essentials/messages

  # Update Essentials folder config on servers that already have it (WARNING: includes userdata unless excluded)
  amp-plugin-sync --folder Essentials

  # Push a jar update (only where the jar already exists)
  amp-plugin-sync --plugin EssentialsX-2.21.2.jar

  # Add a new plugin jar + folder everywhere
  amp-plugin-sync --plugin SomePlugin.jar --folder SomePlugin --install

  # Delete a plugin everywhere (except excluded)
  amp-plugin-sync --plugin XRayGuard.jar --folder XRayGuard --delete

  # Delete a specific subfolder (example)
  amp-plugin-sync --path Essentials/userdata --delete

  # Preview changes
  amp-plugin-sync --path Essentials/config.yml --dry-run

  # Apply with backups enabled
  amp-plugin-sync --path Essentials/config.yml --backup
EOF
}

# ---------- Parse args ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --plugin)
      [[ $# -ge 2 ]] || { echo "Missing value for --plugin"; exit 1; }
      PLUGIN_FILES+=("$2"); shift 2 ;;
    --folder)
      [[ $# -ge 2 ]] || { echo "Missing value for --folder"; exit 1; }
      FOLDERS+=("$2"); shift 2 ;;
    --path)
      [[ $# -ge 2 ]] || { echo "Missing value for --path"; exit 1; }
      PATHS+=("$2"); shift 2 ;;
    --install)
      INSTALL_IF_MISSING=1; shift ;;
    --delete)
      DO_DELETE=1; shift ;;
    --exclude)
      [[ $# -ge 2 ]] || { echo "Missing value for --exclude"; exit 1; }
      EXCLUDES_CSV="$2"; shift 2 ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    --backup)
      MAKE_BACKUPS=1; shift ;;
    --yes)
      AUTO_YES=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1 ;;
  esac
done

if [[ ${#PLUGIN_FILES[@]} -eq 0 && ${#FOLDERS[@]} -eq 0 && ${#PATHS[@]} -eq 0 ]]; then
  echo "Error: you must specify at least one --plugin and/or --folder and/or --path"
  echo
  usage
  exit 1
fi

if [[ ! -d "$BASE" ]]; then
  echo "Error: BASE path not found: $BASE"
  exit 1
fi

SRC_PLUGINS="${BASE}/${SOURCE_INSTANCE}/${REL_PLUGINS}"
if [[ ! -d "$SRC_PLUGINS" ]]; then
  echo "Error: source plugins directory not found: $SRC_PLUGINS"
  exit 1
fi

# ---------- Build excludes set ----------
declare -A EXCLUDE_SET=()
EXCLUDE_SET["$PROXY_INSTANCE"]=1

if [[ -n "$EXCLUDES_CSV" ]]; then
  IFS=',' read -r -a _ex <<< "$EXCLUDES_CSV"
  for e in "${_ex[@]}"; do
    e="$(echo "$e" | xargs)" # trim
    [[ -n "$e" ]] && EXCLUDE_SET["$e"]=1
  done
fi

# ---------- Discover instances ----------
mapfile -t ALL_DIRS < <(find "${BASE}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)

TARGETS=()
for d in "${ALL_DIRS[@]}"; do
  [[ "$d" == "$SOURCE_INSTANCE" ]] && continue
  [[ -n "${EXCLUDE_SET[$d]:-}" ]] && continue

  if [[ -d "${BASE}/${d}/${REL_PLUGINS}" ]]; then
    TARGETS+=("$d")
  fi
done

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  echo "No target instances found."
  exit 1
fi

# ---------- Plan output ----------
echo
echo "=== amp-plugin-sync PLAN ==="
echo "BASE            : ${BASE}"
echo "Source instance : ${SOURCE_INSTANCE}"
echo "Source plugins  : ${SRC_PLUGINS}"
echo "Mode            : $([[ $DO_DELETE -eq 1 ]] && echo "DELETE" || echo "SYNC")"
echo "Install missing : $([[ $INSTALL_IF_MISSING -eq 1 ]] && echo "YES" || echo "NO (update existing only)")"
echo "Dry run         : $([[ $DRY_RUN -eq 1 ]] && echo "YES" || echo "NO")"
echo "Backups         : $([[ $MAKE_BACKUPS -eq 1 ]] && echo "YES" || echo "NO")"
echo

if [[ ${#PLUGIN_FILES[@]} -gt 0 ]]; then
  echo "Plugin file(s):"
  for p in "${PLUGIN_FILES[@]}"; do echo "  - ${p}"; done
fi
if [[ ${#FOLDERS[@]} -gt 0 ]]; then
  echo "Folder(s):"
  for f in "${FOLDERS[@]}"; do echo "  - ${f}"; done
fi
if [[ ${#PATHS[@]} -gt 0 ]]; then
  echo "Path(s):"
  for p in "${PATHS[@]}"; do echo "  - ${p}"; done
fi

echo
echo "Targets (${#TARGETS[@]}):"
for t in "${TARGETS[@]}"; do echo "  - ${t}"; done
echo "=========================="
echo

if [[ $AUTO_YES -eq 0 ]]; then
  read -r -p "Proceed? (y/N) " ans
  case "${ans,,}" in
    y|yes) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
fi

# ---------- Helpers ----------
rsync_common=( -avh )
[[ $DRY_RUN -eq 1 ]] && rsync_common+=( --dry-run )

# For full folder syncs, we want exact match, including deletions.
rsync_folder_mirror=( "${rsync_common[@]}" --delete )

sync_file_if_applicable() {
  local inst="$1"
  local filename="$2"
  local src="${SRC_PLUGINS}/${filename}"
  local dest_dir="${BASE}/${inst}/${REL_PLUGINS}"
  local dest="${dest_dir}/${filename}"

  if [[ ! -f "$src" ]]; then
    echo "  [SOURCE MISSING] $filename"
    return 0
  fi

  if [[ $INSTALL_IF_MISSING -eq 0 && ! -f "$dest" ]]; then
    echo "  [SKIP missing on target] $filename"
    return 0
  fi

  # Backup existing file before overwrite (only if --backup and not dry-run)
  if [[ $MAKE_BACKUPS -eq 1 && $DRY_RUN -eq 0 && -f "$dest" ]]; then
    cp -a "$dest" "${dest}.bak.$(date +%Y%m%d-%H%M%S)"
  fi

  rsync "${rsync_common[@]}" "$src" "$dest_dir/"
  echo "  [OK] synced file: $filename"
}

sync_folder_if_applicable() {
  local inst="$1"
  local folder="$2"
  local src="${SRC_PLUGINS}/${folder}/"
  local dest_base="${BASE}/${inst}/${REL_PLUGINS}"
  local dest="${dest_base}/${folder}/"

  if [[ ! -d "${SRC_PLUGINS}/${folder}" ]]; then
    echo "  [SOURCE MISSING] folder $folder"
    return 0
  fi

  if [[ $INSTALL_IF_MISSING -eq 0 && ! -d "$dest" ]]; then
    echo "  [SKIP missing on target] folder $folder"
    return 0
  fi

  # Backup existing folder before overwrite (only if --backup and not dry-run)
  if [[ $MAKE_BACKUPS -eq 1 && $DRY_RUN -eq 0 && -d "$dest" ]]; then
    local backup="${dest%/}.bak.$(date +%Y%m%d-%H%M%S)"
    cp -a "${dest%/}" "$backup"
  fi

  mkdir -p "$dest"
  rsync "${rsync_folder_mirror[@]}" "$src" "$dest"
  echo "  [OK] synced folder (mirror): $folder"
}

# Sync a specific file or subfolder inside plugins/.
# Intentionally does NOT use --delete; this is for surgical config sync.
sync_path_if_applicable() {
  local inst="$1"
  local relpath="$2"

  # Normalize leading slashes
  relpath="${relpath#/}"

  local src="${SRC_PLUGINS}/${relpath}"
  local dest="${BASE}/${inst}/${REL_PLUGINS}/${relpath}"

  if [[ ! -e "$src" ]]; then
    echo "  [SOURCE MISSING] path $relpath"
    return 0
  fi

  # If install is off, require target to already have that file/dir
  if [[ $INSTALL_IF_MISSING -eq 0 && ! -e "$dest" ]]; then
    echo "  [SKIP missing on target] path $relpath"
    return 0
  fi

  mkdir -p "$(dirname "$dest")"

  if [[ -d "$src" ]]; then
    mkdir -p "$dest"
    rsync "${rsync_common[@]}" "$src/" "$dest/"
    echo "  [OK] synced folder path: $relpath"
  else
    # Backup existing file before overwrite (only if --backup and not dry-run)
    if [[ $MAKE_BACKUPS -eq 1 && $DRY_RUN -eq 0 && -f "$dest" ]]; then
      cp -a "$dest" "${dest}.bak.$(date +%Y%m%d-%H%M%S)"
    fi
    rsync "${rsync_common[@]}" "$src" "$dest"
    echo "  [OK] synced file path: $relpath"
  fi
}

delete_item_if_exists() {
  local inst="$1"
  local item="$2"
  local path="${BASE}/${inst}/${REL_PLUGINS}/${item}"

  if [[ -e "$path" ]]; then
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "  [DRY] would delete: $item"
    else
      rm -rf "$path"
      echo "  [OK] deleted: $item"
    fi
  else
    echo "  [SKIP not present] $item"
  fi
}

# ---------- Execute ----------
echo "Applying to targets..."
for inst in "${TARGETS[@]}"; do
  echo
  echo "==> $inst"
  if [[ $DO_DELETE -eq 1 ]]; then
    for p in "${PLUGIN_FILES[@]}"; do delete_item_if_exists "$inst" "$p"; done
    for f in "${FOLDERS[@]}"; do delete_item_if_exists "$inst" "$f"; done
    for pa in "${PATHS[@]}"; do delete_item_if_exists "$inst" "$pa"; done
  else
    for p in "${PLUGIN_FILES[@]}"; do sync_file_if_applicable "$inst" "$p"; done
    for f in "${FOLDERS[@]}"; do sync_folder_if_applicable "$inst" "$f"; done
    for pa in "${PATHS[@]}"; do sync_path_if_applicable "$inst" "$pa"; done
  fi
done

echo
echo "=== DONE ==="
echo "Action : $([[ $DO_DELETE -eq 1 ]] && echo "DELETE" || echo "SYNC")"
echo "Plugins: ${PLUGIN_FILES[*]:-(none)}"
echo "Folders: ${FOLDERS[*]:-(none)}"
echo "Paths  : ${PATHS[*]:-(none)}"
echo "Targets: ${#TARGETS[@]} instance(s)"
echo
echo "Reminder: reload/restart servers to apply changes if needed."
