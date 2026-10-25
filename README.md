# Local Strata server

Run Strata's OpenAI- and Anthropic-compatible API in rootless Podman with an
AMD HIP image. This follows `../llamacpp-local`: `make up` builds the image,
waits for a one-shot model initializer, then recreates the authenticated server.
The endpoint is `http://127.0.0.1:9931/v1` by default; set
`STRATA_BIND_ADDRESS` to publish on another interface.

The default is **Qwen3.8-Flash-Next Q2_0**, with CPU image encoding,
200000 context, INT8 KV and MTP drafting. It uses the original full-expert model.
See [supported models](https://github.com/Niko1221/Strata/blob/main/docs/MODELS.md).

## Requirements

- Linux x86-64, Podman, `podman-compose`, Python 3, and systemd 256 or
  newer (`systemd-creds`, `varlinkctl`).
- A supported AMD GPU; the image defaults to `gfx1101` (RX 7700/7800 XT).
  Set `HIP_ARCHITECTURES` when building for another supported card. See
  [upstream HIP documentation](https://github.com/Niko1221/Strata/blob/main/docs/AMD_HIP.md).
- Host amdgpu driver with `/dev/kfd` and the `/dev/dri/renderD*` nodes readable
  and writable by everyone, the default on current Fedora (`ls -l` shows
  `crw-rw-rw-`). Supplementary groups such as `render` do not reach rootless
  containers, so group-only access is not enough.
- About 48 GB RAM, 16 GB VRAM and **at least 140 GB free SSD space**, plus
  space for the ROCm build image. The two GGUFs total about 66.4 GB; packing
  duplicates the expert weights; MTP needs downloaded and prepared copies.
- Outbound GitHub/Hugging Face/package registry access. No Strata or ROCm host installation is required.
- A host hard memlock limit that permits unlimited locking. Rootless Podman
  cannot raise a host hard limit; check `ulimit -Hl` if container creation fails.

## Start

```sh
make set-api-key
# Optional for public models:
make set-hf-token
make up
make logs
```

Each store command prompts without echoing the value or placing it in shell
history; enter an empty HF token to remove it, and run a command again to
replace its value. The values are user-scoped
[systemd credentials](https://www.freedesktop.org/software/systemd/man/latest/systemd-creds.html),
encrypted to `${XDG_CONFIG_HOME:-$HOME/.config}/strata-local/credentials/<name>.cred`
with the host's credential key and, when present, the TPM. Only your user (or
root) can decrypt them, and only on this machine. No keyring, login or unlock
step is involved, so this works on a headless host and at boot. A credential is
bound to its name, so swapping the files does not swap the secrets. A firmware
or TPM change can make the files undecryptable; store the values again if that
happens. Earlier versions kept the values in the GNOME keyring; with that
keyring unlocked, `make migrate-keyring-secrets` copies them over once.

Credentials reach the containers as external Podman shell-driver secrets, which
decrypt the files when each container is created. Rootless Podman runs the
driver in its user namespace, where your user appears as root and
`systemd-creds decrypt` would try the TPM directly and be denied, so the driver
asks the system credential service over Varlink instead; the service still sees
your real UID. The optional absent HF token mounts as an empty value.
The server wrapper reads `/run/secrets/strata_api_key` into `STRATA_API_KEY`;
the API key is never written to model configuration or passed in argv.
Re-run `make up` after changing credentials. Keep this checkout at its current
path because the driver uses absolute script paths. If you move it, remove the
two registrations by their full IDs from `podman secret ls`, then run
`make secrets` again. Repeated startup reuses existing registrations with
`podman secret create --ignore`; changing a credential needs only container
recreation, not secret replacement. Podman stores only registration IDs under
`${XDG_DATA_HOME:-$HOME/.local/share}/strata-local/podman-secret-ids`.

The first start compiles Strata and downloads/prepares the model; allow ample
time. Subsequent starts check GGUF sizes and reuse them and completed packs.
Set `STRATA_VERIFY_EXISTING=1 make up` to also verify existing GGUF SHA-256
checksums (including the vision projector); this reads each entire file.
Interrupted GGUF downloads resume from `.partial` files. Their sizes and SHA-256
hashes come from the fixed Hugging Face revision's LFS metadata and are verified
before publication. MTP uses upstream's pinned tensor downloads and verification.
Model preparation calls individual packing tools; it never executes
`setup.py` or `setup.sh`. A failure leaves the initializer unsuccessful and
prevents `make up` from launching the server.

```sh
podman compose ps
curl --fail http://127.0.0.1:9931/health
make down
```

Use your stored API key as a Bearer token and request model
`qwen3.8-flash-next-q2_0`. The server also exposes `/v1/models`.
`make down` preserves the model volume and credentials.

### Switching models

The image carries two engines, and `STRATA_MODEL` picks the model `make up`
serves:

```sh
make up                          # Qwen3.8-Flash-Next, Q2_0 and IQ3_XXS variants (config/server.json)
make up STRATA_MODEL=deepseek    # DeepSeek-V4.1-Flash IQ1_M (config/model-deepseek.json, config/server-deepseek.json)
```

Any other name selects `config/model-<name>.json` and `config/server-<name>.json`,
linked as `/data/model-<name>` and `/data/pack-<name>`. Every model stays
downloaded and packed, so switching back is quick. `make up` without the
variable serves Qwen again; to keep another model across runs, set
`STRATA_MODEL` in `mise.local.toml`. Only one model runs at a time: each takes
the whole GPU and most of the RAM.

#### Qwen variants

The Qwen server holds two variants of the same model and switches between them
while it runs (the fork's `"models"`, documented in Strata's
`docs/BATCHING.md`). `config/server.json` is the common config; each variant
adds its own pack and shards:

| request `model` | variant | files |
|---|---|---|
| `qwen3.8-flash-next-q2_0`, `q2`, `qwen`, `qwen3.8-flash-next` | Q2_0 | `config/{model,server}-q2.json` |
| `qwen3.8-flash-next-iq3_xxs`, `q3` | IQ3_XXS | `config/{model,server}-q3.json` |

Another model name is served by whichever variant is loaded. A switch takes
about as long as an engine start; the scheduler keeps a variant loaded while
its requests are queued and switches once a request for the other has waited
`model_max_wait_s` (120 s). There is no default variant: the server starts with
`q2` (the first listed, or `STRATA_VARIANT`) and stays on whichever is loaded
(`"default_model"` with `"default_idle_s"` would switch back to one when idle).
The conversation in the engine is saved to `/data/sessions` when its variant is
switched away and restored when it comes back; the parked conversations
(`/data/convcache`) are not kept across a switch. `make up STRATA_VARIANT=q3`
starts with IQ3_XXS, and `POST /v1/load {"model": "q3"}` switches by hand.
`make init` downloads and packs every variant (IQ3_XXS: 76 GB more).

The variants place their experts differently, because IQ3_XXS's experts do not
fit in pc26's RAM beside the GPU's share (33 GB outside VRAM, against Q2_0's
25 GB):

| | Q2_0 | IQ3_XXS |
|---|---|---|
| GPU expert cache | 5760 experts (7.4 GiB) | 4300 (7.0 GiB) |
| experts in RAM | 22 GiB budget (`--resident-budget-gib`) | 24 GiB budget |
| the rest | read from the SSD unbuffered (`STRATA_UNBUFFERED_LOAD=1`) | the same |
| prompt chunk | `auto` (8192) | `auto` (8192) |
| prompt reading, 29-34K tokens | 987 tokens/s | 919 tokens/s |
| answer speed | 43 tokens/s | 18-20 tokens/s |

Measured on pc26 on 2026-10-25. IQ3_XXS with Q2_0's earlier settings (its whole
remainder page-locked, the rest read through the page cache, 2048-token chunks)
re-read the experts several times over per prompt chunk and read prompts at
about 30 tokens/s. Q2_0 with every expert in RAM read 832 tokens/s with
2048-token chunks and 1057 with 8192, but then left no RAM free for the picture
reader and session saves; the 22 GiB budget keeps about 5 GB free. Larger
budgets answer faster on IQ3_XXS (26 GiB: 25 tokens/s) but leave no RAM free.

DeepSeek-V4.1-Flash (ISTA-DASLab's 1.75 bpw set, 81920 context) is upstream
Strata's `strata-dsv41` engine, built here for gfx1101 although upstream ships
it for gfx12 only (its kernel tests pass on gfx1101). It needs about 240 GB of
disk: the shards, the 111 GB n-gram table read from the SSD while it answers,
the DSpark drafter (left unloaded on this card) and a metadata shard of Unsloth's
set. Its settings are upstream's tuned defaults: the tuner found nothing faster
on pc26. Request model `deepseek-v4.1-flash`. Thinking is on by default
(`reasoning_effort: "none"` turns it off). Measured on pc26 through the API on
2026-10-25 against Qwen in its configuration (greedy, thinking off):

| | DeepSeek IQ1_M | Qwen Q2_0 |
|---|---|---|
| answer speed | 4.4-5.7 tokens/s | 55-63 tokens/s |
| prompt reading, 8K / 28K / 58K tokens | 147 / 220 / 235 tokens/s | 839 / 853 / 1140 tokens/s |
| next turn of a 58K-token conversation | 9.5 s to the first token | 0.55 s |

With 32 GB of RAM most of its 115 GB of experts are read from the SSD, so it
is for occasional questions rather than agents.

### Remote host

The same targets deploy to another machine's rootless Podman over SSH; no
checkout is needed there. Add a connection with the remote user, address and
socket path written out (Podman does not apply `~/.ssh/config` aliases), then
select it with `CONTAINER_CONNECTION`:

```sh
podman system connection add pc26 ssh://podman-svc@192.168.0.66/run/user/962/podman/podman.sock
export CONTAINER_CONNECTION=pc26
STRATA_BIND_ADDRESS=0.0.0.0 HIP_ARCHITECTURES=gfx1101 make up
make logs
```

Builds upload this checkout as the build context and compile on the remote
host, so set `HIP_ARCHITECTURES` for its GPU. `make up` also imports `config/`
into the remote `strata-local-config` volume. `STRATA_BIND_ADDRESS`
names a remote host address; without it the server is reachable only from the
remote host itself. `make secrets` decrypts the credentials here
and stores them with the remote's default Podman secret driver, replacing them on
each run: on the remote host they are readable by its Podman user and root, not
encrypted to a TPM. An absent HF token is stored as a newline.

One-time setup on the remote host, as an administrator, for a service user such
as `podman-svc` (UID 962 here):

- subuid and subgid ranges (`usermod --add-subuids ... --add-subgids ...`),
  then `podman system migrate` as that user;
- lingering and the API socket: `loginctl enable-linger podman-svc` and
  `systemctl --user -M podman-svc@ enable --now podman.socket`;
- an unlimited memlock limit for its user manager, for example
  `LimitMEMLOCK=infinity` in `/etc/systemd/system/user@962.service.d/memlock.conf`;
  rootless Podman silently lowers the requested limit to the hard limit;
- `systemctl --user -M podman-svc@ enable podman-restart.service`, so the
  `unless-stopped` server starts again at boot;
- SSH forwarding to the socket allowed for that user, including SELinux.

## Configuration

### Images

Vision is enabled by default. The image includes `strata-vision` compiled for
the CPU, and the initializer downloads the matching BF16 projector from the
same pinned original-model repository revision, verifying its size and SHA-256. This
adds roughly 1 GB of model storage and CPU encoder memory. The language model
still uses HIP; image encoding does not consume GPU VRAM.

Send images through OpenAI-style `image_url` inputs (data URLs or remote URLs),
or Anthropic image blocks. For Pi, set the model's `input` to
`["text", "image"]`. Images are encoded once and cached for reuse during the
server session. CPU encoding adds latency on the first use of an image.

In `config/server.json`, `vision.threads` controls encoder threads (default 8); `vision.max_tokens`
controls image resolution through its token budget (default 300, matching
upstream's CPU setting). Increase the image budget if small text or fine detail
is lost, at the cost of CPU time and context usage. For text-only serving, remove the `vision` object and `--vision` from `args`.
Optionally remove `vision_file` from `config/model.json` to skip the projector
download. Re-run `make up` after changing these settings.

### Text generation

Edit `config/server.json`, which uses Strata's native server configuration format
and is imported with `config/model.json` into the `strata-local-config`
volume mounted read-only at `/config` (`make config`; `make up` runs it).
Importing overwrites both files but leaves files deleted from `config/` in the
volume; run `podman volume rm strata-local-config` with the containers removed
to clear them. The initializer does not
rewrite it or translate settings into engine arguments. Engine options belong
in the native `args` array: `--prefill`, `--pool-workers`, `--vram-reserve-mib`,
and `--adapt-every` can be edited there. `--adapt-every` is currently `0`
(disabled); `1` adapts every decoding round. Keep top-level `max_context` and
`--max-context` in `args` consistent. Contexts above 262144 require explicit
rope scaling configured in Strata.

`parallel` is a native top-level setting, currently `1` (one request at a time).
Larger values add batch slots that use VRAM otherwise available to the expert
cache and can reduce per-request speed. The engine may reduce the requested
count to fit available memory; `/v1/status` reports the effective count in
`concurrency.serving`. See the
[upstream batching guide](https://github.com/Niko1221/Strata/blob/main/docs/BATCHING.md).

Conversation parking is on disk, through our fork's `--conversation-cache-dir`
(see the Strata pin below): `--conversation-cache-dir /data/convcache` in
`args` writes each conversation the engine switches away from to the `strata-data`
volume, and restores it when a request continues it, avoiding repeated prompt
processing (about 1 s instead of about 47 s for a 22K-token prompt). The K/V is
streamed between the file and the GPU, so a restore holds only the running state
and checkpoints in RAM; `--conversation-cache-min-free-mib 1024` lowers the
free-RAM floor from its 2560 MiB default (the locked experts leave only 2-3 GB free,
so the default refused parking past about 70K tokens), and a
conversation that cannot be parked or read back falls back to ordinary prompt
processing. A conversation read back keeps its file, and parking it again updates
that file in place: only the new K/V, the new checkpoints and the running state are
written (about 240 MB instead of the whole 0.9-1.1 GB file for an 18K-token
conversation on pc26). A sibling conversation that shares a parked one's system
prompt and tools borrows that prefix straight from its file. Files are 0.75-1.1 GB
at 18K tokens (each checkpoint adds about 118 MB) and about 3.4 GB at 166K tokens.
`--conversation-cache-mib 16384` caps what the files hold, removing the oldest
first, and `--conversation-cache-slots 0` removes the slot limit.
`--conversation-cache-min-tokens 4096` keeps short side requests (a client's title
or summary call) from being parked, so they cannot push a long conversation out,
and `--conversation-cache-skip-tail 4096` does not park a conversation whose client
only rewrote its last reply (re-rendered without its reasoning). Parked
conversations last as long as the engine: every park file is removed when it
starts. `/metrics` exposes usage, restores and evictions under `conversation_cache`.

Manual session files are also enabled: `slot_save_path` is `/data/sessions`, so
`POST /slots/0?action=save|restore` with `{"filename": "NAME.bin"}` saves or
restores the current conversation. Strata never deletes these files.

Re-run `make up` to apply these settings.

`config/model.json` controls downloads and optional preparation. The initializer
has no repository, architecture, shard-count, projector, profile, or MTP rules.
For example, downloading a single GGUF needs only:

```json
{
  "repository": "owner/model-GGUF",
  "revision": "main",
  "files": ["model-Q4_K_M.gguf"]
}
```

`revision` defaults to `main`; branches and tags are resolved to a commit before
any downloads. Pin a commit for reproducible starts. `files` explicitly selects
all required shards; optional `vision_file` selects any matching projector.
Files require Hugging Face LFS size and SHA-256 metadata. They are stored by
basename under `/data/model`, so selected filenames must have unique basenames.
The manifest preserves the resolved repository metadata.

Optional `prepare` is an ordered list of argument arrays invoking Python scripts
from `/opt/strata/tools`; no shell is used. Optional `copies` is a list of
`{"source": "...", "target": "..."}` objects. Arguments and copy paths support
`{model}`, `{pack}`, `{root}`, and `{first_file}` placeholders. Preparation is
cached per resolved model and complete configuration, and a failed command
prevents publication of the stable symlinks. With no preparation configured,
the initializer only downloads files and creates an empty pack directory.

The supplied configuration explicitly packs the current model, prepares its
MTP artifacts under `/data/pack/mtp/rt`, and copies its expert profile. Change
or remove those steps when switching models. Changing preparation configuration
creates a fresh pack directory while reusing downloaded model files. Download
directories depend only on repository, resolved revision, and the shard list.
Files from older cache directories with matching manifest checksums are reused
through hard links; full verification remains opt-in for existing files.
Update `config/server.json` to match the new native model, tokenizer, pack,
profile, vision, and drafting paths/options. Model compatibility and required
packing tools are determined by Strata; downloading a model does not make an
unsupported architecture runnable. Re-run `make up` after configuration changes.

`fit_max_tokens` defaults to `true`: when Pi requests more output tokens than
the context has room for, Strata reduces the output allowance and preserves the
entire prompt. A prompt that leaves no output space still fails. Thinking and
the answer share the reduced allowance, so reaching it can still cut a reply
short. Set this option to `false` to restore strict rejection.
The default leaves 3072 MiB VRAM for the desktop and uses resident low-RAM
experts with pageable HIP host copies. These are starting settings, not measured
performance claims on this machine.

Compose accepts these environment overrides (or an ignored `.env` file):

| Variable | Default | Purpose |
| --- | --- | --- |
| `STRATA_BIND_ADDRESS` | `127.0.0.1` | Host interface to publish on; `0.0.0.0` for all, including loopback |
| `STRATA_PORT` | `9931` | Published host port |
| `STRATA_GPU` | `0` | HIP device ordinal; an integrated GPU may occupy 0 |
| `HIP_ARCHITECTURES` | `gfx1101` | Image build target |
| `BUILD_JOBS` | `2` | Limit compiler memory use |
| `STRATA_VERIFY_EXISTING` | `0` | Set to `1` to checksum existing GGUFs during initialization |

The image builds Strata from our fork,
[UltimateBoomer/Strata](https://github.com/UltimateBoomer/Strata), at the commit
in the `Containerfile`'s `STRATA_REVISION`: upstream
[Niko1221/Strata](https://github.com/Niko1221/Strata) v0.2.2 plus the disk-backed
conversation cache described above. To change the engine, push a commit to the fork and update
`STRATA_REVISION` (or pass `--build-arg STRATA_REVISION=...`). The llama.cpp
commit is the one that Strata revision pins in `third_party/ggml/VERSION.txt`.
The ROCm 7.0.2 development image tag is not digest pinned.
hipBLAS, rocBLAS and hipBLASLt development packages are
installed inside the image; the base image's HIP compiler alone is insufficient
for Strata's BLAS dependency. Another ROCm builds with
`--build-arg ROCM_IMAGE=docker.io/rocm/dev-ubuntu-24.04:10.1.0-full --build-arg ROCM_DEV_PACKAGES=`
(ROCm 10's `-full` images carry those packages already); it was measured slower here, see
[hipBLASLt tuning](#hipblaslt-tuning). GPU containers disable SELinux label separation to permit
device access and retain your supplementary groups; model storage is a named
volume. `make logs` shows API and model-loading status. The full engine log is
persisted at `/data/strata.log`; inspect it with
`podman compose exec strata-server tail -n 100 /data/strata.log`.

## Validation

To calibrate Strata's decode settings for this PC with the existing image and
prepared model:

```sh
make calibrate
```

This stops the Strata server and runs upstream `tools/calibrate.py` against the
served model's run config (for the Qwen variants, `config/server.json` with the
variant's own file over it: `STRATA_VARIANT`, default `q2`) in a temporary
container with the same GPU and model mounts.
Stop other GPU inference servers first. Calibration measures PCIe share, draft
confidence, CPU worker count, and adaptive expert-cache settings; it can take
15–30 minutes or longer. It prints recommended settings and measurements without
editing the configuration. Apply the desired settings to `config/server.json`
(or the variant's file when they differ by variant), then restart serving with
`make up`.
The server stays stopped if calibration finishes or fails.

### hipBLASLt tuning

Strata reads a prompt's dense matrix products through hipBLASLt only with a tuning
table for this GPU architecture and the image's exact hipBLASLt version; otherwise it
falls back to plain hipBLAS. Upstream ships none for gfx1101, so
`config/gfx1101-hipblaslt-100000.txt` (hipBLASLt 1.0.0, ROCm 7.0.2) was calibrated on
pc26's RX 7800 XT and `STRATA_HIPBLASLT_TUNING` in `config/server.json` points at it.
After changing the ROCm image or the card, regenerate it:

```sh
make tune
```

This stops the server, builds the image, and runs `tune_hipblaslt` over the 43 GEMM
shapes of upstream's gfx1100 table, measuring every solution id on this card (about two
minutes). It writes `config/<arch>-hipblaslt-<version>.txt`; point
`STRATA_HIPBLASLT_TUNING` at that file and `make up`. The engine refuses a table for
another version, logging it, and reads prompts on plain hipBLAS again.

Measured on pc26 on 2026-10-20 (Q2_0, this configuration; medians of 2-3 fresh prompts
after a warm-up; repeating the first arm at the end agreed within 0.6%):

| Image | Prompt tok/s at 9K / 34K / 70K tokens | Decode tok/s |
| --- | --- | --- |
| ROCm 7.0.2 | 467 / 465 / 459 | 47-56 |
| ROCm 7.0.2 + table | **816 / 813 / 796** | 49-54 |
| ROCm 10.1.0 | 453 / 453 / 448 | 51-54 |
| ROCm 10.1.0 + its table (hipBLASLt 1.4.1) | 768 / 768 / 754 | 50-55 |

The table makes prompts about 1.75x faster with decode unchanged; ROCm 10.1.0 was
2-6% slower than 7.0.2 with or without one, so the image stays on 7.0.2. The table costs
the expert cache up to about 40 slots of VRAM (hipBLASLt's workspace).

For a small sequential performance sample against a running server:

```sh
python3 scripts/bench.py --label baseline > /tmp/strata-baseline.json
```

`--url` defaults to `http://127.0.0.1:9931`; pass the server's address when it is
bound elsewhere. This reads the API key from its systemd credential or `STRATA_API_KEY`, performs one warmup
and three short/three longer fresh-prefix requests, capped at 128 output tokens.
It records wall time, time to first streamed content/reasoning, usage, and the
local server configuration. Keep the server running that configuration and
avoid other requests during comparisons. These are API latency measurements,
not isolated engine prefill/decode rates; early stopping and output differences
can affect comparisons.

Following [upstream HIP guidance](https://github.com/Niko1221/Strata/blob/main/docs/AMD_HIP.md),
compare the current `--prefill 512` with `--prefill auto` first, changing only
one setting and restarting the server between samples. Keep the existing desktop
VRAM reserve. hipBLASLt tuning tables must match both the GPU architecture and
installed hipBLASLt version; do not select a table just by GPU family.
Use `podman compose up -d --no-deps --force-recreate strata-server` after editing
server settings to avoid rerunning model preparation. Restore the baseline
and repeat it to check for warmup effects before accepting a change.

`make check` runs focused downloader/configuration tests, shell syntax checks,
and Compose parsing. Host Python needs `requests` for these tests.
The previous v0.1.38 image build passed on 2026-10-03, including the gfx1101 HIP compile
and executable link. A container smoke check resolved all shared libraries and
ran the engine, server and model/MTP packing tools' help commands successfully.
The CPU vision encoder build also passed, with no GPU runtime libraries linked.
The previous Coder model and matching projector loaded successfully, and an authenticated image
completion correctly identified a synthetic red image as "Red".
Check `/health`, then an authenticated chat completion after `make up`; a healthy
short-context start does not verify full-window inference or performance.
