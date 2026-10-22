#!/bin/sh
# Fill a clone of your Hugging Face Space with this repository, ready to commit and push.
#
#   sh deploy/huggingface/prepare_space.sh ../greentrace-space
#
# Copies the files git tracks at HEAD (never .env, logs, data/raw or local memory), leaves out the
# design mockups (not needed to run), puts the Space card in place of README.md, and marks the
# binary files for Git LFS, which Hugging Face requires. It never commits or pushes: you do.
set -eu

target="${1:?usage: sh deploy/huggingface/prepare_space.sh <path to your Space clone>}"
if [ ! -d "$target/.git" ]; then
  echo "error: $target is not a git clone of your Space (git clone https://huggingface.co/spaces/<you>/greentrace)" >&2
  exit 2
fi
root=$(git rev-parse --show-toplevel)
if [ -n "$(git -C "$root" status --porcelain)" ]; then
  echo "note: uncommitted changes are NOT copied; only HEAD $(git -C "$root" rev-parse --short HEAD) is" >&2
fi

space_rules=""
if [ -f "$target/.gitattributes" ]; then space_rules=$(cat "$target/.gitattributes"); fi  # the Space's own LFS rules
git -C "$root" archive --format=tar HEAD -- . ':(exclude)design' | tar -x -C "$target"
cp "$root/deploy/huggingface/README.md" "$target/README.md"
if [ -n "$space_rules" ]; then printf '%s\n' "$space_rules" >> "$target/.gitattributes"; fi
cat >> "$target/.gitattributes" <<'EOF'
# Hugging Face keeps binary files in Git LFS
*.jpg filter=lfs diff=lfs merge=lfs -text
*.png filter=lfs diff=lfs merge=lfs -text
*.gz filter=lfs diff=lfs merge=lfs -text
*.woff2 filter=lfs diff=lfs merge=lfs -text
EOF
echo "ready: $target holds GreenTrace at $(git -C "$root" rev-parse --short HEAD); now commit and push it from there"
