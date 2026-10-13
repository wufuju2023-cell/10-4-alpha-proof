#!/usr/bin/env bash
# 推送前密钥/隐私检查（只扫本仓库工作区，排除 .git）。
# 用法：bash scripts/check_secrets.sh
set -u
cd "$(dirname "$0")/.."
SELF="check_secrets.sh"
hits=0
report() { echo "[HIT] $1"; hits=$((hits + 1)); }

# 1) 常见 token 前缀（模式拆开写，避免本脚本自匹配）
for pat in 'ghp_''[A-Za-z0-9]{20,}' 'github_pat_''[A-Za-z0-9_]{20,}' 'sk-''[A-Za-z0-9]{20,}' 'hf_''[A-Za-z0-9]{20,}' 'AKIA''[0-9A-Z]{16}' 'xox[bap]-''[A-Za-z0-9-]{10,}'; do
  while IFS= read -r line; do
    [ -n "$line" ] && report "$line"
  done < <(grep -RInE "$pat" . --exclude-dir=.git --exclude="$SELF" 2>/dev/null || true)
done

# 2) 私钥块
while IFS= read -r line; do
  [ -n "$line" ] && report "$line"
done < <(grep -RIn "PRIVATE KEY" . --exclude-dir=.git --exclude="$SELF" 2>/dev/null || true)

# 3) 敏感文件名
while IFS= read -r f; do
  [ -n "$f" ] && report "suspicious file: $f"
done < <(find . -path ./.git -prune -o -type f \( -name ".env" -o -name "*.env" -o -name "*credential*" -o -name "*.pem" -o -name "id_rsa*" -o -name "id_ed25519*" -o -name "*token*" \) -print 2>/dev/null || true)

# 4) 大文件（>5MB，防误传权重）
while IFS= read -r f; do
  [ -n "$f" ] && report "large file: $f"
done < <(find . -path ./.git -prune -o -type f -size +5M -print 2>/dev/null || true)

if [ "$hits" -gt 0 ]; then
  echo "检查失败：发现 $hits 处可疑内容，请先处理再推送。"
  exit 1
fi
echo "检查通过：未发现 token/密钥/大文件。"
