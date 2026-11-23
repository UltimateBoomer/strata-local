ARG ROCM_IMAGE=docker.io/rocm/dev-ubuntu-24.04:7.0.2
FROM $ROCM_IMAGE

# This checkout, our fork of Niko1221/Strata: upstream v0.2.19 plus the disk-backed conversation cache (park files updated in place, read back by streaming, borrowed from), the turn slice (a conversation keeps the engine between its tool calls) and request priorities (fair share in bands, preemption) and model variants (one server switches between quantizations of one model, and between model families) and load images (a variant's expert load kept in load order on disk, read sequentially) and sessions (a request names its conversation; only sessions' turns are parked, a client yields and drops by request) and parked conversations kept across restarts and variant switches (one budget for every variant's files) and a web console at / in place of the chat page (the turn scheduler, the variants and each parked conversation on the Monitor) and engine settings per request (strata_tune: tail_skip, kva, route_prior, route_resident; variants of one engine switched without a restart).
ARG HIP_ARCHITECTURES=gfx1101
ARG BUILD_JOBS=8
# The BLAS development packages the base image lacks; ROCm 10's -full images already carry them (as amdrocm-*).
ARG ROCM_DEV_PACKAGES="hipblas-dev rocblas-dev hipblaslt-dev"
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates git python3 python3-venv libatomic1 libgomp1 \
    $ROCM_DEV_PACKAGES \
    && rm -rf /var/lib/apt/lists/*
# The build context is the Strata checkout (compose.yaml, the Makefile's STRATA_SRC), its working tree as it is: nothing
# is fetched from the fork. The context "local" is this repository (strata-local): the scripts installed below.
# Each step copies only what it reads, so a change to the server (terminal/) or to these scripts rebuilds
# in seconds; the engines are compiled again only when their sources change.
WORKDIR /opt/strata
# Use the llama.cpp revision pinned by this checkout.
COPY engine/third_party/ggml/VERSION.txt engine/third_party/ggml/
RUN LLAMA_REVISION="$(awk 'NR == 1 { print $1; exit }' engine/third_party/ggml/VERSION.txt)" \
    && test "${#LLAMA_REVISION}" -eq 40 \
    && git init /opt/llama.cpp \
    && git -C /opt/llama.cpp remote add origin https://github.com/ggml-org/llama.cpp.git \
    && git -C /opt/llama.cpp fetch --depth 1 --progress origin "$LLAMA_REVISION" \
    && git -C /opt/llama.cpp checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/llama.cpp rev-parse HEAD)" = "$LLAMA_REVISION"
COPY terminal/requirements.txt terminal/requirements-build.txt terminal/
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r terminal/requirements.txt -r terminal/requirements-build.txt
ENV PATH=/opt/venv/bin:/opt/rocm/bin:$PATH \
    STRATA_GGUF_PY=/opt/llama.cpp/gguf-py
# The engine's sources (engine/: its CMake, include/, src/, tests/ and third_party/). The built programs go into
# engine/ beside them (/opt/strata/engine/strata: the run configs' "exe"), with strata-inkling, the Inkling-2-Mini
# engine (config/server-inkling.json), built for the same architectures (STRATA_HIP_ARCHS).
# engine/'s CMake also reads two of the Terminal's folders: tools/hip (the BLAS tuners) and tools/vision (its HIP kernels).
COPY engine engine/
COPY terminal/tools/hip terminal/tools/hip/
COPY terminal/tools/vision terminal/tools/vision/
RUN cmake -S engine -B build-hip -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH=/opt/rocm \
    -DSTRATA_ENABLE_HIP=ON -DSTRATA_ENABLE_CUDA=OFF \
    -DSTRATA_PREFILL_MMQ=ON \
    -DSTRATA_BUILD_TESTS=OFF -DSTRATA_PORTABLE=ON \
    -DCMAKE_HIP_ARCHITECTURES="$HIP_ARCHITECTURES" \
    -DSTRATA_GGML_DIR=/opt/llama.cpp \
    -DSTRATA_HIP_ARCHS="$HIP_ARCHITECTURES" \
    && cmake --build build-hip --target strata strata-inkling tune_hipblaslt -j "$BUILD_JOBS" \
    && cp build-hip/strata build-hip/tune_hipblaslt build-hip/src/inkling2/strata-inkling engine/ \
    && rm -rf build-hip /opt/llama.cpp/.git
# The picture reader's sources are the Terminal's (terminal/tools/vision, copied above).
RUN cmake -S terminal/tools/vision -B build-vision -DCMAKE_BUILD_TYPE=Release \
    -DLLAMA_DIR=/opt/llama.cpp -DSTRATA_VISION_CUDA=OFF -DSTRATA_PORTABLE=ON \
    && cmake --build build-vision --target strata-vision -j "$BUILD_JOBS" \
    && cp build-vision/bin/strata-vision engine/ \
    && rm -rf build-vision
# The engine has no RUNPATH; ROCm 10's images, unlike ROCm 7's, do not register /opt/rocm/lib with the loader.
RUN echo /opt/rocm/lib > /etc/ld.so.conf.d/zz-strata-rocm.conf && ldconfig \
    && ldd engine/strata engine/strata-inkling | (! grep 'not found')
# The rest of the tree: the server (terminal/serve), the Terminal's modules it imports, and the model preparation tools.
COPY . ./
COPY --from=local scripts/model-init.py scripts/serve.py scripts/calibrate.py scripts/tune-hipblaslt.sh \
    /usr/local/lib/strata-local/
ARG STRATA_COMMIT=unknown
# STRATA_BUILD_ID: the last commit to the engine's sources (the Makefile's ENGINE_SOURCES)
ARG STRATA_BUILD_ID=unknown
LABEL org.opencontainers.image.revision=$STRATA_COMMIT org.strata.engine-sources=$STRATA_BUILD_ID
ENTRYPOINT ["python", "/usr/local/lib/strata-local/serve.py"]
