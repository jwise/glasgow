#!/bin/bash

set -eo pipefail
cd /work

set -x

sdcc --version

# Clean all build products; they may have been built using a different compiler.
make -C vendor/libfx2/firmware/library clean
make -C firmware clean

# Build the artifact.
make -C vendor/libfx2/firmware/library all MODELS=medium
make -C firmware all

# Deploy the artifact.
cp firmware/glasgow.ihex software/glasgow/device/firmware.ihex
