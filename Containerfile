FROM docker.io/rocm/dev-ubuntu-24.04:7.0.2

# Our fork of Niko1221/Strata: upstream v0.1.45 plus the disk-backed conversation cache, read back by streaming and borrowed from.
ARG STRATA_REPOSITORY=https://github.com/UltimateBoomer/Strata.git
ARG STRATA_REVISION=5d0a9e786c5d275b3f1f8bb1a48e95a20094b918
ARG HIP_ARCHITECTURES=gfx1101
ARG BUILD_JOBS=8
# Use the llama.cpp revision pinned by the selected Strata checkout.
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates git python3 python3-venv libatomic1 libgomp1 \
    hipblas-dev rocblas-dev hipblaslt-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/strata
RUN git init . && git remote add origin "$STRATA_REPOSITORY" \
    && git fetch --depth 1 origin "$STRATA_REVISION" && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$STRATA_REVISION" \
    && LLAMA_REVISION="$(awk 'NR == 1 { print $1; exit }' third_party/ggml/VERSION.txt)" \
    && test "${#LLAMA_REVISION}" -eq 40 \
    && git init /opt/llama.cpp \
    && git -C /opt/llama.cpp remote add origin https://github.com/ggml-org/llama.cpp.git \
    && git -C /opt/llama.cpp fetch --depth 1 --progress origin "$LLAMA_REVISION" \
    && git -C /opt/llama.cpp checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/llama.cpp rev-parse HEAD)" = "$LLAMA_REVISION"
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
ENV PATH=/opt/venv/bin:/opt/rocm/bin:$PATH \
    STRATA_GGUF_PY=/opt/llama.cpp/gguf-py
RUN cmake -S . -B build-hip -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH=/opt/rocm \
    -DSTRATA_ENABLE_HIP=ON -DSTRATA_ENABLE_CUDA=OFF \
    -DSTRATA_PREFILL_MMQ=ON \
    -DSTRATA_BUILD_TESTS=OFF -DSTRATA_PORTABLE=ON \
    -DCMAKE_HIP_ARCHITECTURES="$HIP_ARCHITECTURES" \
    -DSTRATA_GGML_DIR=/opt/llama.cpp \
    && cmake --build build-hip --target strata -j "$BUILD_JOBS" \
    && mkdir engine && cp build-hip/strata engine/ \
    && rm -rf build-hip .git /opt/llama.cpp/.git
RUN cmake -S tools/vision -B build-vision -DCMAKE_BUILD_TYPE=Release \
    -DLLAMA_DIR=/opt/llama.cpp -DSTRATA_VISION_CUDA=OFF -DSTRATA_PORTABLE=ON \
    && cmake --build build-vision --target strata-vision -j "$BUILD_JOBS" \
    && cp build-vision/bin/strata-vision engine/ \
    && rm -rf build-vision
COPY scripts/model-init.py scripts/serve.py /usr/local/lib/strata-local/
ENTRYPOINT ["python", "/usr/local/lib/strata-local/serve.py"]
