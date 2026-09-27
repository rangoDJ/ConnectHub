#!/usr/bin/env bash
# Print the next release version (X.Y.Z, no "v") for a merge to main.
#
#   next-version.sh <latest-tag> <pr-branch> <pr-labels>
#
# <latest-tag>  highest existing vX.Y.Z tag, or empty for the first release
# <pr-branch>   head branch of the merged PR, or empty for a direct push
# <pr-labels>   the PR's labels, comma-separated
#
# Bump: major for the release:major label, minor for release:minor or a
# feature/ branch, patch for everything else.
set -euo pipefail

latest="${1:-}"
branch="${2:-}"
labels=",${3:-},"

if [[ -z "$latest" ]]; then
    latest="v0.0.0"
fi
if [[ ! "$latest" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    echo "not a vX.Y.Z tag: $latest" >&2
    exit 1
fi
major="${BASH_REMATCH[1]}" minor="${BASH_REMATCH[2]}" patch="${BASH_REMATCH[3]}"

if [[ "$labels" == *",release:major,"* ]]; then
    echo "$((major + 1)).0.0"
elif [[ "$labels" == *",release:minor,"* || "$branch" == feature/* ]]; then
    echo "$major.$((minor + 1)).0"
else
    echo "$major.$minor.$((patch + 1))"
fi
