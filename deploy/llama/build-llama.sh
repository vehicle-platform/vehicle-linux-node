#!/usr/bin/env sh
# Build llama.cpp `llama-server` from a pinned release tag and install it to /usr/local/bin
# (D-121). Run on the Pi 5 (arm64); nothing is downloaded by CI. Needs git, cmake and a C++
# compiler:  sudo apt install -y git cmake build-essential
#
#   sh deploy/llama/build-llama.sh                 # or: sh deploy/install.sh --with-llama
#
# Bumping the version = change both values below in a PR (tag AND the commit it peels to).
set -eu
LLAMA_CPP_REPO="https://github.com/ggml-org/llama.cpp"   # MIT
LLAMA_CPP_TAG="v1.0.0"
LLAMA_CPP_COMMIT="b1b770d90d94cad9e9ea5b993bdbc383a522fb28"
PREFIX="${PREFIX:-/usr/local}"
for tool in git cmake c++; do
  command -v "$tool" >/dev/null 2>&1 || { echo "missing: $tool (sudo apt install -y git cmake build-essential)"; exit 1; }
done
src=$(mktemp -d)
trap 'rm -rf "$src"' EXIT
git clone --quiet --depth 1 --branch "$LLAMA_CPP_TAG" "$LLAMA_CPP_REPO" "$src/llama.cpp"
head=$(git -C "$src/llama.cpp" rev-parse HEAD)
[ "$head" = "$LLAMA_CPP_COMMIT" ] || { echo "tag $LLAMA_CPP_TAG is $head, expected $LLAMA_CPP_COMMIT: refusing to build"; exit 1; }
# GGML_NATIVE=ON tunes for the Pi 5's Cortex-A76 (NEON, dotprod); libcurl is not needed (no -hf downloads).
# LLAMA_BUILD_UI=OFF + LLAMA_USE_PREBUILT_UI=OFF: v1.0.0 otherwise downloads the web-UI assets at
# build time (a Hugging Face bucket whose pinned version is gone, so the build fails). Our unit
# runs `--no-webui`, so the UI is not needed and nothing is fetched while building.
cmake -S "$src/llama.cpp" -B "$src/build" -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON \
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON \
  -DBUILD_SHARED_LIBS=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF
cmake --build "$src/build" --config Release --target llama-server -j "$(nproc)"
sudo install -m 0755 "$src/build/bin/llama-server" "$PREFIX/bin/llama-server"
"$PREFIX/bin/llama-server" --version 2>&1 | head -2 || true
echo "installed $PREFIX/bin/llama-server ($LLAMA_CPP_TAG)"
