# Local Strata server

Run Strata's OpenAI- and Anthropic-compatible API in rootless Podman with an
AMD HIP image. This follows `../llamacpp-local`: `make up` builds the image,
waits for a one-shot model initializer, then recreates the authenticated server.
The default endpoint is `http://192.168.0.66:9931/v1`, using the configured
Compose host port.

The default is **Qwen3.8-Flash-Next Q2_0**, with CPU image encoding,
120000 context, INT8 KV and MTP drafting. It uses the original full-expert model.
See [supported models](https://github.com/Niko1221/Strata/blob/main/docs/MODELS.md).

## Requirements

- Linux x86-64, Podman, `podman-compose`, `crun`, Python 3, and systemd 256 or
  newer (`systemd-creds`, `varlinkctl`).
- A supported AMD GPU; the image defaults to `gfx1101` (RX 7700/7800 XT).
  Set `HIP_ARCHITECTURES` when building for another supported card. See
  [upstream HIP documentation](https://github.com/Niko1221/Strata/blob/main/docs/AMD_HIP.md).
- Host amdgpu driver with `/dev/kfd` and `/dev/dri` accessible to your user.
  Your user must belong to the device-owning groups (usually `render` and
  `video`); log in again after changing membership. `keep-groups` requires crun.
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
curl --fail http://192.168.0.66:9931/health
make down
```

Use your stored API key as a Bearer token and request model
`qwen3.8-flash-next-q2_0`. The server also exposes `/v1/models`.
`make down` preserves the model volume and credentials.

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
and is mounted directly at `/config/server.json`. The initializer does not
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

Conversation parking is on disk, through the local engine patch in
[`patches/`](patches/README.md): `--conversation-cache-dir /data/convcache` in
`args` writes each conversation the engine switches away from to the `strata-data`
volume, and restores it when a request continues it, avoiding repeated prompt
processing (about 1 s instead of about 47 s for a 22K-token prompt). Parked
conversations survive restarts. `--conversation-cache-mib 16384` caps the files'
total size, removing the oldest first (also at startup after the cap is lowered),
and `--conversation-cache-slots 0` removes the slot limit. A file is about 0.7 GB
per 22K tokens, up to about 2.5 GB at full context. Restoring briefly reads the
file into RAM; the default 2560 MiB free-RAM floor still applies, and a snapshot
that cannot fit falls back to ordinary prompt processing. Files from another model
or engine configuration are removed at startup, so changing settings that affect
the cached state (such as `--kv` or `--max-context`) discards them. `/metrics`
exposes usage, restores and evictions under `conversation_cache`.

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
| `STRATA_BIND_ADDRESS` | `192.168.0.66` | Host interface; use `127.0.0.1` for local access |
| `STRATA_PORT` | `9931` | Published host port |
| `STRATA_GPU` | `0` | HIP device ordinal; an integrated GPU may occupy 0 |
| `HIP_ARCHITECTURES` | `gfx1101` | Image build target |
| `BUILD_JOBS` | `2` | Limit compiler memory use |
| `STRATA_VERIFY_EXISTING` | `0` | Set to `1` to checksum existing GGUFs during initialization |

The image pins Strata v0.1.40.3 commit `d5ea7133741e67743c0e886bb426c0ce8d69cf6c`,
llama.cpp commit `3cf03257f219afbe7334045ff7c6a06ac68c627d` (the release's upstream
dependency pin), and the ROCm 7.0.2 development image tag. The base tag
is not digest pinned. This Strata revision includes the HIP-compatible
shared-memory alignment fix upstream; no build-time source patch is needed.
hipBLAS, rocBLAS and hipBLASLt development packages are
installed inside the image; the base image's HIP compiler alone is insufficient
for Strata's BLAS dependency. GPU containers disable SELinux label separation to permit
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

This stops the Strata server and runs upstream `tools/calibrate.py` against
`config/server.json` in a temporary container with the same GPU and model mounts.
Stop other GPU inference servers first. Calibration measures PCIe share, draft
confidence, CPU worker count, and adaptive expert-cache settings; it can take
15–30 minutes or longer. It prints recommended settings and measurements without
editing the configuration. Apply the desired settings to `config/server.json`,
then restart serving with `podman compose up -d --no-deps strata-server`.
The server stays stopped if calibration finishes or fails.

For a small sequential performance sample against a running server:

```sh
python3 scripts/bench.py --label baseline > /tmp/strata-baseline.json
```

This reads the API key from its systemd credential or `STRATA_API_KEY`, performs one warmup
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
