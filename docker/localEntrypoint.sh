#!/bin/bash
set -e

# Ensure Git considers the mounted folder safe
git config --global --add safe.directory /work

# Execute the original entrypoint command
exec "$@"
