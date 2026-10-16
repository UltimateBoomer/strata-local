# Strata prefill investigation — 2026-10-13

The best immediate experiment is to replace `--prefill 512` with `--prefill auto`. The most concrete build issue is that the running binary has **no HIP MMQ kernels**. The strongest observed system bottleneck is **expert-weight storage traffic and active swapping on a 32 GiB host**. These findings suggest several useful experiments, but no candidate speedup has yet been measured on this stack.

This investigation read the live container, engine source, logs, process memory, and host counters. It did not restart the server, change serving configuration, or submit benchmark requests: an existing request was processing a 42,868-token prompt during inspection.

## What is actually running

| Item | Observed value |
| --- | --- |
| Runtime | Rootless Podman Compose, one healthy server |
| Engine | 0.1.43, reported by `/v1/status` and latest startup |
| Model | Original Qwen3.8-Flash-Next Q2_0, 24,576 expert pairs |
| GPU | RX 7800 XT, gfx1101, 16 GiB |
| CPU | Ryzen 7 7800X3D, eight physical cores |
| Host memory | 30.9 GiB usable; approximately 16 GiB swap occupied |
| Prefill | Fixed 512-token chunks |
| Serving concurrency | One request |
| Context | Live engine argv and status: 120,000 tokens |
| Expert cache | 4,360 pairs, 5.61 GiB VRAM |
| Resident expert complement | 19.63 GiB pageable RAM |
| Expert complement needed | 26.03 GiB; approximately 6.4 GiB falls back to files |
| VRAM reserve | 4,096 MiB |
| KV | INT8, no streaming |
| ROCm libraries | hipBLASLt 1.0.0; hipBLAS 3.0.2; rocBLAS 5.0.2, ROCm 7.0.2 packages |
| PCIe link | 16 GT/s ×16; no obvious link-width bottleneck |

The engine process had approximately 23.4 GiB RSS and **3.2 GiB swapped out**. A short `vmstat` sample showed roughly 10–12 MiB/s swap-in and one interval around 47 MiB/s swap-out. Memory pressure stalled some tasks about 12% of the preceding minute. Swap occupancy alone would not prove a current problem; the active paging and engine swap do.

Completed fresh prompts in the latest engine session:

| Tokens actually read | Engine prefill rate | Prefill time |
| ---: | ---: | ---: |
| 72,110 | 85.8 tok/s | 840.8 s |
| 46,355 | 92.8 tok/s | 499.3 s |
| 7,792 | 54.5 tok/s | 142.9 s |
| 34,664 | 41.2 tok/s | 842.0 s |

These are production observations with different prompts and memory conditions, not controlled comparisons. The 72K request caused about 299 GB of physical storage reads; the 34K request caused about 61.6 GB and 11.8 million major faults. The I/O counters cover the entire request, including decode, so they do not isolate prefill I/O. They nevertheless establish substantial file-tier activity. Small cached suffixes report lower tok/s partly because fixed overhead is divided by very few new tokens; those rates should not be compared directly with fresh long prompts.

## Recommended experiments, in order

### 1. Increase the prefill chunk

In `config/server.json`, change only the value following `--prefill`:

```json
"--prefill", "auto"
```

This engine's auto planner fits prompt buffers into a loan from the expert cache, normally with an 8,192-token ceiling. It balances the chunk and expert-upload ring; the chosen chunk is a capacity decision, not proof of the fastest setting. Inspect its startup log to see the actual size. Compare fixed 1,024, 2,048 and 4,096 if auto is slower or creates memory-pressure outliers.

Larger chunks amortize expert reads/uploads and GPU launches over more tokens. Fixed 512 also stays below the source's normal 1,024-token threshold for streaming all experts through the prompt path. This is particularly relevant to the observed long prompts and file reads. The tradeoffs are larger buffers, more expert-cache borrowing, and possibly slower transitions back to decode. Keep the current desktop VRAM reserve for the initial experiment.

### 2. Build the missing HIP MMQ path

Add to the engine CMake invocation in `Containerfile`:

```text
-DSTRATA_PREFILL_MMQ=ON
```

The option defaults to OFF in the running source tree and is absent from this Containerfile. Binary inspection confirms the live function `strata::prefill::mmq::built()` consists of `xor %eax,%eax; ret`: it returns false. The current build therefore uses the FP16 expert path. Setting the runtime variable alone cannot enable kernels that were not compiled.

Q2_0 is supported by the MMQ implementation. With kernels compiled, the inspected 0.1.43 source enables its expert MMQ plan unless `STRATA_PREFILL_MMQ=0`; use explicit `STRATA_PREFILL_MMQ=1` for the candidate and `0` for its control. Build an alternate image tag, preserving the running image for rollback, and keep model, runtime libraries, context and chunk settings constant. MMQ quantizes activations and can change numerical output; validate representative completed tasks as well as throughput.

Before rebuilding, resolve dependency reproducibility: the workspace Containerfile selects llama.cpp `8345f333951c661d166b00e6f9362e553768f292`, while the running Strata source's `third_party/ggml/VERSION.txt` and CMake dependency pin specify `3cf03257f219afbe7334045ff7c6a06ac68c627d`. That metadata does not establish which dependency the live binary used. Use the engine revision's matched dependency for a controlled build, recording any dependency correction separately from the MMQ comparison.

### 3. Address RAM pressure

The full expert complement already exceeds the resident budget by 6.4 GiB, before conversations and other runtime allocations grow. This is the strongest measured constraint.

- On current hardware, test `STRATA_RESIDENT_HEADROOM_GIB=8` versus the default 4, retaining `STRATA_RESIDENT_PIN=0`. A smaller anonymous resident copy leaves more room for file cache and runtime growth. It also sends more experts to the file tier, so this is an experiment with an uncertain outcome. Keep other applications' memory usage comparable.
- If alternating chats are uncommon, compare the 2 GiB conversation cache with a smaller budget or disabled cache. Current logs show approximately 1.85 GB of parked state, and earlier 2.28–2.30 GiB snapshots exceeded the budget. Parking helps avoid re-prefill across chats, but consumes scarce RAM; disabling it can hurt alternating-chat latency substantially.
- More RAM is the direct capacity remedy if retaining this full-expert model and long contexts. **64 GiB is a sensible target to evaluate** against the observed 26 GiB complement plus runtime, page cache, parked chats and desktop. No hardware speedup is established by this investigation.

Do not force the disabled CPU prefill share on this memory-starved configuration. Startup explicitly says its file-backed experts need 6.4 GiB while only 4.4 GiB is available for them. Whole-complement pinning and KV streaming into host RAM are also poor first experiments here.

### 4. Calibrate hipBLASLt for this card and library

There is no shipped gfx1101 tuning table and no `STRATA_HIPBLASLT_TUNING` variable in the live process. Dense prompt projections therefore use the ordinary hipBLAS route.

Build `tune_hipblaslt` in a retained build directory, following upstream's tuning instructions. Calibrate on this RX 7800 XT with the same libraries as the engine; for the current hipBLASLt the table must identify **gfx1101 / 100000**. Include shapes relevant to the selected chunks and model. Mount the resulting table into the container and set `STRATA_HIPBLASLT_TUNING` in the native server `env` object. Compare with the variable absent and check fallback diagnostics.

The existing gfx1100 tables are unsuitable for this gfx1101 card. Upstream reports 898–953 tok/s on another RX 7800 XT, but that run used Coder IQ1_M, a Ryzen 5950X, 121 GiB RAM and hipBLASLt 1.4.1. It establishes that this GPU has more potential; it is not a target or speedup estimate for this Q2_0 stack. Consider a ROCm upgrade as its own experiment after obtaining a controlled baseline.

### 5. Test optional kernels after the preceding changes

The inspected source includes gfx1101 Q2_0 fused WMMA expert kernels behind **`STRATA_PF_FUSED=1`**, compiled with the MMQ build. Compare them against MMQ with larger chunks; they are opt-in on HIP and require correctness evaluation. Do not confuse this with `STRATA_HIP_WMMA=1`, whose prompt-attention optimization is intended for gfx12, not this card.

`STRATA_DENSE_MMQ=1` is another candidate after MMQ is built. Some dense K-quant formats additionally need `-DSTRATA_MMQ_KQUANTS=ON`; measure it separately from the expert path. Published RDNA2 gains do not establish a gain on gfx1101.

## Measurement and rollout

1. Wait for the server to be idle and run baseline → candidate → restored baseline, restarting between arms. Keep the same model, prompt set, reasoning settings, output cap and background load.
2. Measure fresh 4K and 16K prompts, plus a representative 32K–64K prompt and cached follow-ups. Use at least three repeats, reporting first-use and warmed results separately. Unique prefixes prevent KV reuse but do not make filesystem caches cold.
3. Use engine `read tokens / prompt milliseconds` for prefill, and measure API TTFT separately. The local `scripts/bench.py` only measures API latency and usage; it does not isolate engine prefill and its 300-second timeout is too short for some observed workloads.
4. The image contains `tools/hip/bench_prefill.py`, which pairs completed engine log records with API requests and rejects fresh prompts that reuse tokens. Its 360-second request timeout also needs increasing for the long workload. Do not include cancelled timing records, queue time, or full prompt counts containing reused tokens in prefill throughput.
5. Record engine/image identity, effective argv, selected auto chunk, expert cache size, VRAM/GTT, engine swap, swap-in/out, major faults, physical reads, decode rate and completed-task quality. A faster prefill that causes desktop spill or repeated decode cache refills may lose overall.

For configuration-only arms, the existing documented command is:

```sh
podman compose up -d --no-deps --force-recreate strata-server
```

It interrupts active work, so run it only during the benchmark window. It avoids rerunning model preparation.

## Configuration drift to resolve before comparisons

- README describes two slots, a 3 GiB VRAM reserve and v0.1.40.3; the live stack uses one slot, 4 GiB and v0.1.43.
- The mounted JSON currently has top-level `max_context: 120000` but `--max-context 200000`; the live process still has 120000 in argv. File contents can change after engine startup. Align both fields and record the effective value when testing. Reducing context can free expert-cache VRAM, but would sacrifice the observed ~100K-token workload.
- `--pool-workers 0` already selects seven workers plus the host thread, using all eight physical cores. `parallel` is already 1. Neither has an obvious initial prefill win.
- MTP primarily improves decode; changing `--spec`, `--pcie-frac`, or `--adapt-every` is lower priority for this prefill investigation. Likewise, CPU vision settings affect image encoding rather than text prefill.

## Sources

- [Strata HIP build, tuning, and card-specific guidance](https://github.com/Niko1221/Strata/blob/7695859aa13afb248bdb4d36f9b2e95707db079d/docs/AMD_HIP.md)
- [Strata CMake options and dependency pin](https://github.com/Niko1221/Strata/blob/7695859aa13afb248bdb4d36f9b2e95707db079d/CMakeLists.txt)
- [Upstream HIP prefill benchmark method and evidence](https://github.com/Niko1221/Strata/blob/7695859aa13afb248bdb4d36f9b2e95707db079d/docs/AMD_HIP_PERFORMANCE.md)

Local evidence came from the running container's `/data/strata.log`, `/v1/status`, `/proc` memory/argv, linked binary symbols/disassembly, source tree, library package versions, and host `/sys` and `/proc` counters. Historical IQ1_M log sessions were excluded from the current-model measurements above.
