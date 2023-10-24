#!/bin/bash

set -eo pipefail

# This script is provided to ensure builds of firmware included in the repository are byte for byte
# reproducible regardless of the host OS or the packages installed.
#
# Using this script is not a requirement to work on the firmware; installation instructions for
# the prerequisites for a selection of operating systems are included in the documentation.

TOPDIR=$(dirname $(dirname $(readlink -f $0)))

# We always want to have a matching container version for any given
# historical version of firmware, so we either pull a container from the
# GitHub container registry -- or, we build it locally.
GITVER=$(git describe --always --dirty --exclude='*' --abbrev=7)
IMAGE_NAME=glasgow/firmware-build:g$GITVER

if [ -z "$(docker images -q $IMAGE_NAME)" ]; then
	echo "no Docker image named $IMAGE_NAME -- building it (did you mean to docker pull it first, if you are trying to reproduce a build from CI?)"
	docker build -t $IMAGE_NAME "$TOPDIR/software/docker"
	if ! [ -z "$WE_ARE_IN_CI" ]; then
		# docker push it to ghcr.io with creds that are stored...
		true
	fi
fi

exec docker run --rm --user $(id -u) -v "$TOPDIR:/work" --rm $IMAGE_NAME
