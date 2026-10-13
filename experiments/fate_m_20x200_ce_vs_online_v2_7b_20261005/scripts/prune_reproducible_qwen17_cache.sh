#!/usr/bin/env bash
set -Eeuo pipefail

experiment_root=${1:-/mnt/workspace/experiments/fate_m_20x200_ce_vs_online_v2_7b_20261005}
target=/mnt/workspace/full_modelscope/model_cache/Qwen3-1.7B-Base
archive_root=/mnt/workspace/_archive/blobs
receipt_dir="$experiment_root/runs/preflight/storage-clean-20261005-qwen17-cache"
manifest="$receipt_dir/links.tsv"

mkdir -p "$receipt_dir"
printf 'modelscope_ui_before=89.1G/100G\nstarted_at=%s\ntarget=%s\n' \
  "$(date -Is)" "$target" > "$receipt_dir/intent.txt"

if [[ ! -e "$target" ]]; then
  printf 'status=already_absent\ncompleted_at=%s\n' "$(date -Is)" > "$receipt_dir/DONE"
  exit 0
fi

resolved_target=$(realpath -e "$target")
if [[ "$resolved_target" != "$target" ]]; then
  echo "Refusing unexpected target: $resolved_target" >&2
  exit 2
fi
if [[ ! -d "$archive_root" ]]; then
  echo "Archive root missing: $archive_root" >&2
  exit 2
fi
if pgrep -af "$target" > "$receipt_dir/processes.txt"; then
  echo "Refusing to remove an in-use cache; see $receipt_dir/processes.txt" >&2
  exit 3
fi

# The archive keeps the original extension, so its name is not exactly the
# content hash.  Resolve the matching archive entry by device+inode instead of
# reconstructing a filename from SHA-256.  Validation is a complete first pass:
# no namespace entry is removed until every file has exactly one archive peer.
unexpected_nodes=$(find "$target" -xdev -mindepth 1 ! -type d ! -type f -print)
if [[ -n "$unexpected_nodes" ]]; then
  printf '%s\n' "$unexpected_nodes" > "$receipt_dir/unexpected-nodes.txt"
  echo "Refusing target containing non-file/non-directory nodes" >&2
  exit 4
fi

printf 'bytes\tsha256\tdevice\tinode\tactive_path\tarchive_path\tlinks_before\n' > "$manifest"
file_count=0
while IFS= read -r -d '' active_path; do
  bytes=$(stat -c '%s' "$active_path")
  device=$(stat -c '%d' "$active_path")
  inode=$(stat -c '%i' "$active_path")
  links=$(stat -c '%h' "$active_path")
  sha256=$(sha256sum "$active_path" | cut -d' ' -f1)

  mapfile -d '' archive_matches < <(
    find "$archive_root" -xdev -type f -samefile "$active_path" -print0
  )
  if [[ "$links" != "2" || "${#archive_matches[@]}" != "1" ]]; then
    printf 'active_path=%s links=%s archive_matches=%s\n' \
      "$active_path" "$links" "${#archive_matches[@]}" \
      >> "$receipt_dir/validation-errors.txt"
    echo "Unexpected hardlink topology for $active_path" >&2
    exit 4
  fi
  archive_path=${archive_matches[0]}
  resolved_archive=$(realpath -e "$archive_path")
  case "$resolved_archive" in
    "$archive_root"/*) ;;
    *) echo "Refusing unexpected archive path: $resolved_archive" >&2; exit 4 ;;
  esac
  if [[ ! "$active_path" -ef "$resolved_archive" ]]; then
    echo "Archive peer changed during validation: $resolved_archive" >&2
    exit 4
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$bytes" "$sha256" "$device" "$inode" "$active_path" \
    "$resolved_archive" "$links" >> "$manifest"
  file_count=$((file_count + 1))
  printf 'phase=validate files=%s latest=%s\n' "$file_count" "$active_path"
done < <(find "$target" -xdev -type f -print0 | sort -z)

if [[ "$file_count" == "0" ]]; then
  echo "Refusing empty target" >&2
  exit 4
fi

before_bytes=$(du -sx -B1 "$target" | cut -f1)
apparent_bytes=$(awk -F '\t' 'NR > 1 { total += $1 } END { printf "%.0f", total }' "$manifest")
{
  printf 'validated_file_count=%s\n' "$file_count"
  printf 'allocated_target_bytes=%s\n' "$before_bytes"
  printf 'expected_physical_reclaim_bytes=%s\n' "$apparent_bytes"
} >> "$receipt_dir/intent.txt"
printf 'phase=validate_complete files=%s expected_reclaim_bytes=%s\n' \
  "$file_count" "$apparent_bytes"

# Re-check every exact inode before removing any entry.  This prevents a cache
# mutation between the validation and deletion passes from widening scope.
tail -n +2 "$manifest" | while IFS=$'\t' read -r bytes sha256 device inode active_path archive_path links_before; do
  case "$active_path" in
    "$target"/*) ;;
    *) echo "Refusing unexpected active path: $active_path" >&2; exit 5 ;;
  esac
  [[ "$(stat -c '%d' "$active_path")" == "$device" ]]
  [[ "$(stat -c '%i' "$active_path")" == "$inode" ]]
  [[ "$(stat -c '%s' "$active_path")" == "$bytes" ]]
  [[ "$(stat -c '%h' "$active_path")" == "$links_before" ]]
  [[ "$active_path" -ef "$archive_path" ]]
done

tail -n +2 "$manifest" | while IFS=$'\t' read -r bytes sha256 device inode active_path archive_path links_before; do
  rm -f -- "$active_path"
done
find "$resolved_target" -xdev -depth -type d -empty -delete
printf 'phase=active_links_removed files=%s\n' "$file_count"

if [[ -e "$target" ]]; then
  echo "Target still contains entries after exact-file deletion" >&2
  exit 6
fi

tail -n +2 "$manifest" | while IFS=$'\t' read -r bytes sha256 device inode active_path archive_path links_before; do
  [[ "$(stat -c '%d' "$archive_path")" == "$device" ]]
  [[ "$(stat -c '%i' "$archive_path")" == "$inode" ]]
  [[ "$(stat -c '%h' "$archive_path")" == "1" ]]
  rm -f -- "$archive_path"
done
printf 'phase=archive_links_removed files=%s\n' "$file_count"

sync

{
  printf 'status=complete\n'
  printf 'completed_at=%s\n' "$(date -Is)"
  printf 'validated_file_count=%s\n' "$file_count"
  printf 'allocated_target_bytes=%s\n' "$before_bytes"
  printf 'expected_physical_reclaim_bytes=%s\n' "$apparent_bytes"
  printf 'target_present_after=%s\n' "$(test -e "$target" && echo yes || echo no)"
  printf 'archive_entries_present_after=%s\n' "$(tail -n +2 "$manifest" | cut -f6 | while read -r path; do test -e "$path" && echo yes; done | grep -q yes && echo yes || echo no)"
  printf 'manifest_sha256=%s\n' "$(sha256sum "$manifest" | cut -d' ' -f1)"
} > "$receipt_dir/DONE"
cat "$receipt_dir/DONE"
