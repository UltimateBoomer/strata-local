# Local Strata server

Run PolyStrata's OpenAI- and Anthropic-compatible API (PolyStrata is built on Strata) in rootless Podman with an
AMD HIP image. This follows `../llamacpp-local`: `make up` builds the image,
waits for a one-shot model initializer, then recreates the authenticated server.
The endpoint is `http://127.0.0.1:9931/v1` by default; set
`STRATA_BIND_ADDRESS` to publish on another interface.

The default is **Qwen3.8-Flash-Next Unsloth UD-IQ1_M** (Dynamic V3.1, about 1 bit), with CPU image encoding,
200000 context, INT8 KV and DFlash2 drafting (no MTP layer loaded). It keeps every expert of the model.
The same server also serves Qwen3.8-Flash-Next UD-IQ3_XXS and **Inkling 2 Mini** (Unsloth UD-Q4_K_XL), loading the
one a request names ([Switching models](#switching-models)).

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
- 32 GB RAM or more, 16 GB VRAM and **at least 200 GB free SSD space**, plus
  space for the ROCm build image. The Qwen GGUFs total about 152 GB (UD-IQ1_M
  68.0 GB, UD-IQ3_XXS 83.6 GB) and Inkling 2 Mini's 17.6 GB; MTP needs downloaded
  and prepared copies.
- Outbound GitHub/Hugging Face/package registry access. No Strata or ROCm host installation is required.
- A host hard memlock limit that permits unlimited locking. Rootless Podman
  cannot raise a host hard limit; check `ulimit -Hl` if container creation fails.

## Start

The image is built from a checkout of our PolyStrata fork, `../PolyStrata` by default
(`STRATA_SRC`, below), on its `inkling-amd` branch (Inkling 2 Mini on an AMD card).

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

Use your stored API key as a Bearer token and request model `q1`
(`qwen3.8-flash-next-unsloth-ud-iq1_m`), `q3` or `inkling`. `/v1/models` lists all three.
`make down` preserves the model volume and credentials.

### Switching models

One server holds three models and loads the one a request names (PolyStrata's
`"switch_on_request"`, its `docs/BACKENDS.md`, "Changing the model"):

| request `model` | model | engine | files |
|---|---|---|---|
| `q1`, `qwen3.8-flash-next-unsloth-ud-iq1_m` | Qwen3.8-Flash-Next, Unsloth UD-IQ1_M (Dynamic V3.1) | `strata` | `config/{model,server}-q1.json` |
| `q3`, `qwen3.8-flash-next-ud-iq3_xxs` | Qwen3.8-Flash-Next, Unsloth UD-IQ3_XXS (Dynamic V3.1) | `strata` | `config/{model,server}-q3.json` |
| `inkling`, `inkling-2-mini-ud-q4_k_xl` | Inkling 2 Mini, Unsloth UD-Q4_K_XL | `strata-poly` | `config/{model,server}-inkling.json` |

```sh
make up                              # starts with q1; a request for q3 or inkling switches
make up STRATA_VARIANT=inkling       # starts with Inkling 2 Mini instead
```

A request that names another of the three waits for the running request, stops
the loaded engine and starts the other (only one model is in memory at a time),
then is answered by it with that model's tokenizer, chat template and sampling.
`POST /v1/load {"model": "q3"}` switches by hand; a request that names no model,
or a name none of them has, is answered by the loaded one. `make init` downloads
and prepares all three. `STRATA_MODEL=<name>` still serves
`config/server-<name>.json` alone.

At each start `scripts/serve.py` writes one PolyStrata run config per model,
`/tmp/strata-run/strata-<name>.json`, from `config/` (`scripts/run_configs.py`):
`config/server.json` holds what the Qwen models share and lists the models
(`"models"`), each model's file adds its own keys over it, and a file that names
its own `"exe"` (Inkling) is another engine and takes only the server's keys. The
`engine` and `engine_env` fields are written out as `args` and `env` (below).
PolyStrata's server lists the run configs beside the one it starts with and
switches between them.

Measured on pc26 (RX 7800 XT 16 GB, Ryzen 7 7800X3D, 32 GB RAM) on 2026-11-23
through the API, one request at a time, the model's own sampling and thinking:

| | writes | reads a 7-8K-token prompt |
|---|---|---|
| q1 (UD-IQ1_M) | 32-38 tokens/s | about 1,100 tokens/s |
| q3 (UD-IQ3_XXS) | 16-17 tokens/s | about 940 tokens/s |
| inkling (UD-Q4_K_XL, 200K context) | 47-49 tokens/s | about 490-530 tokens/s |

#### Inkling 2 Mini

Thinking Machines' Inkling 2 Mini runs on PolyStrata's own engine, `strata-poly`,
which the image builds next to `strata`. Its HIP build needs our fork's
`inkling-amd` branch (two fixes for ROCm 7.0; without them it does not compile, and
then faults on the GPU at the first token). The model is Unsloth's UD-Q4_K_XL
GGUF (17.6 GB), from the `Mainline_Support` folder of `unsloth/Inkling-2-Mini-GGUF`
(the layout llama.cpp and strata-poly read; the top-level files are another
layout), pinned to the commit PolyStrata's setup uses. Unsloth names the file
`.gguf_file`; `"rename"` in `config/model-inkling.json` saves it as `.gguf`.
`make init` builds its tokenizer folder from the GGUF (PolyStrata's
`tools/strata_tokenizer.py`).

UD-Q4_K_XL does not fit whole in 16 GB of VRAM: the card holds part of its
experts and strata-poly reads the rest from RAM. The run config is the one
PolyStrata's setup writes for an AMD card (`--kv-whole`: the attention caches take
their VRAM at load; one request at a time) with a 200,000-token context and
`--prompt-batch 2048`. The model takes 1,048,576 tokens, but its attention caches
need 32 KB a token in VRAM: measured on pc26 with `strata-poly --tokens` (3 tokens
in, 64 written), 32K leaves about 3,000 of its 3,072 routed experts on the card and
writes about 61 tokens/s; 128K 1,606 experts, 59 tokens/s; 200K 1,006-1,162, 49;
256K 770, 55; 320K 354, 44; 384K none, 22; 512K and 1M do not load (no VRAM for
the caches). The larger the context, the fewer experts stay on the card. Reading
an 8,000-token prompt at 200K: 445 tokens/s with the engine's 1,024 tokens at a
time, 534 with `--prompt-batch 2048` (writing 49.0 and 48.6 tokens/s); 4096, a
larger staging buffer (`--stage-mib 4096`) and `--lock-experts` read no faster. Pictures
are off: PolyStrata's picture tower is a CUDA program. Inkling 2 Mini thinks
before it answers unless the request sets `reasoning_effort` to `none`, so give
it room in `max_tokens`. The engine writes the experts it used to
`/data/pack-inkling/expert-profile.txt` and caches those first on the next start.

#### Qwen models

UD-IQ3_XXS is Unsloth's Dynamic V3.1 file (2026-11-04, 83.6 GB in four shards):
its experts mix IQ2_S, IQ3_XXS and IQ4_XS gate/up projections with IQ4_NL and
Q2_0 downs, and its fourth shard is the IQ4_NL n-gram table. It
is packed with `--compat-bf16` and read from its GGUF shards in place.

UD-IQ1_M (`q1`) is Unsloth's Dynamic V3.1 file from the same revision (68.0 GB in
three shards; its third shard is the n-gram table). It replaced ISTA-DASLab's Q2_0 on
2026-11-10 and is packed the same way as UD-IQ3_XXS. Its picture reader is
ISTA-DASLab's `mmproj-Qwen3.8-Flash-Next-BF16.gguf` (a `sources` entry in
`config/model-q1.json`). Unsloth's UD-IQ1_S cannot be used: it mixes two expert
formats in one layer, which Strata refuses.

The Qwen models draft with DFlash2 alone (`"drafting": "dflash2"`, and in
`config/server.json`'s `engine` `"draft": "dflash2"`, `"draft_fallback": "none"`,
`"spec": 6`, `"draft_conf": 0.7`): the MTP layer is not loaded, which leaves about
1 GB more VRAM for the expert cache. `--mtp` stays in the arguments; the engine
reads only the draft vocabulary from it. PolyStrata's server chooses the drafter
from `"drafting"` and keeps the pinned drafter file named in `"user_args"`; its
start log says what it chose (`drafting: DFlash2 alone`). `make init` fetches the
drafter once, into `/data/dflash2` (`"dflash2": true` in `config/model-q1.json`).

Each model keeps part of its experts on the card and the rest in RAM, within
`--resident-budget-gib 22`; whatever does not fit is read from the SSD unbuffered
(`STRATA_UNBUFFERED_LOAD=1`). IQ3_XXS's experts are larger, so more of them come
from the SSD and it writes about half as fast as UD-IQ1_M.

### As a systemd service (Quadlet)

On the machine itself, the server can run as a Podman Quadlet unit of your user's systemd manager instead of a
compose container. It then starts at boot (with `loginctl enable-linger`) and restarts when it fails:

```sh
make init                            # credentials, config volume, image and models, as for make up
make down                            # the compose server, if it runs: the unit uses the same port
make quadlet                         # write ~/.config/containers/systemd/strata-local-server.container
systemctl --user start strata-local-server
```

`make quadlet` only installs the unit (it reloads systemd but starts nothing); manage the server with
`systemctl --user start|stop|restart|status strata-local-server`, its log with `podman logs -f strata-local-server`.
The unit is `quadlet/strata-local-server.container.in`, filled in with `STRATA_BIND_ADDRESS`, `STRATA_PORT`,
`STRATA_MODEL`, `STRATA_VARIANT` and `STRATA_GPU` (for example `STRATA_VARIANT=q3 make quadlet`); run `make quadlet`
again after changing them, then restart. A restart makes a fresh container, so it also picks up a rebuilt image and
`make config`. The unit uses the same image, credentials, `strata-local-config` volume, model volume
(`strata-local_strata-data`) and port as `make up`: stop it before `make up`, `make calibrate`, `make tune` or a build.
The model initializer is not part of the unit (it asks Hugging Face on every run), so a start at boot serves the models
already prepared. Systemd cannot `enable` or `disable` a generated unit; to stop it starting at boot, run
`make quadlet-uninstall`, which stops the server and removes the unit (the volumes, image and credentials stay). `make quadlet` refuses to run with `CONTAINER_CONNECTION` set:
the unit has to be installed on the remote host itself.

The unit asks for an unlimited memlock limit, which rootless Podman lowers to your user manager's hard limit. Check
it with `systemctl show user@$(id -u).service -p LimitMEMLOCK`, and raise it as described for the service user below.

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

Builds upload the Strata checkout (`STRATA_SRC`, its working tree) and this
directory as the build contexts and compile on the remote host, so set `HIP_ARCHITECTURES` for its GPU. `make up` also imports `config/`
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
is lost, at the cost of CPU time and context usage. For text-only serving, remove the `vision` object and `engine.vision`.
Optionally remove `vision_file` from `config/model.json` to skip the projector
download. Re-run `make up` after changing these settings.

### Text generation

Edit `config/server.json`, which uses Strata's native server configuration format
and is imported with `config/model.json` into the `strata-local-config`
volume mounted at `/config` (`make config`; `make up` runs it). The server reads
these files there directly. `make config` refuses to overwrite a file changed in
the volume since the last import: `make pull-config` copies those changes back into
`config/` (review them with `git diff`), and `make config FORCE=1` discards them. Importing leaves
files deleted from `config/` in the volume; run `podman volume rm
strata-local-config` with the containers removed to clear them.

Engine options are fields of the `engine` object rather than an argv list: each
key is an option without its dashes, `-` written as `_`
(`"conversation_cache_mib": 16384` is `--conversation-cache-mib 16384`); `true`
is a bare flag (`"resident_experts": true`), and `false` or `null` leaves the
option out. `engine_env` does the same for the engine's `STRATA_` environment
variables (`"unbuffered_load": true` is `STRATA_UNBUFFERED_LOAD=1`; `true`
is `1`). `scripts/run_configs.py` expands both, in the common file and in each
model's, into the `args` and `env` of the run configs PolyStrata's server starts
the engine with. A model's option replaces the common one of the same name. Raw `args` and `env`
still work beside them, but naming an option in both is an error. Keep top-level
`max_context` and `engine.max_context` consistent. Contexts above 262144 require
explicit rope scaling configured in Strata.

`engine_env.gfx11_wmma_defaults` is `0`: Strata 0.2.19 turns seven matrix-core
switches on by default on gfx11 (`PF_GEMM PF_F16 PF_DM16 PF_P1A PF_P1A_L2
DFLASH_WMMA HIP_FAST_DEC`). With them on pc26 (2026-11-23), both Unsloth UD-IQ1_M
and UD-IQ3_XXS read every prompt longer than about 70 tokens as garbage (asked to
repeat a message, the model called it corrupted), and with `HIP_FAST_DEC` alone
they wrote garbage from the first token. The other gfx11 defaults it lists at start
stay on.

Sampling is per model: each Qwen model's file has its own `sampling` (the defaults
for the fields a request leaves out; a request's own values win), and the common
`config/server.json` has none. Both Qwen models start with the model card's
thinking set (temperature 1.0, top_p 0.95, top_k 20); Inkling 2 Mini with
PolyStrata's defaults for it.

`parallel` is a native top-level setting, currently `1` (one request at a time).
Larger values add batch slots that use VRAM otherwise available to the expert
cache and can reduce per-request speed. The engine may reduce the requested
count to fit available memory; `/v1/status` reports the effective count in
`concurrency.serving`. See the
[upstream batching guide](https://github.com/Niko1221/Strata/blob/main/docs/BATCHING.md).

Conversations are parked in RAM, as upstream Strata does: the engine keeps up to
four conversations it switched away from and restores one when a request continues
it. Its budget is sized from the free RAM; on pc26 the experts leave too little,
and the engine turns parking off at start (`conversation cache off: not enough free
RAM`). A conversation that continues from where the engine stands needs no parking.
`--prompt-cache 2` keeps two checkpoints per conversation (the end of the system
prompt and the start of the last assistant turn) instead of 6. A switch to another
model starts it with no conversation.

Manual session files (`POST /slots/0?action=save|restore`) are off: set
`slot_save_path` (for example to `/data/sessions`) to turn them on. They are
whole-file saves of the current conversation that Strata never deletes.

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

Optional `rename` maps a repository file to the name it is saved as (Inkling's
`.gguf_file` as `.gguf`). Optional `prepare` is an ordered list of argument arrays
invoking Python scripts from `/opt/strata/terminal/tools`; no shell is used. Optional `copies` is a list of
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
| `BUILD_JOBS` | `8` | Parallel compile jobs; lower it to limit compiler memory use |
| `STRATA_VERIFY_EXISTING` | `0` | Set to `1` to checksum existing GGUFs during initialization |
| `STRATA_SRC` | `../PolyStrata` | The PolyStrata checkout the image is built from |

This repository is the deployment of our PolyStrata fork,
[UltimateBoomer/PolyStrata](https://github.com/UltimateBoomer/PolyStrata) (upstream
[simplexai-labs/PolyStrata](https://github.com/simplexai-labs/PolyStrata), which is
[Niko1221/Strata](https://github.com/Niko1221/Strata) plus its own engine and server).
The fork holds the source, this repository the image recipe, configs and scripts.
Until 2026-11-23 it built our Strata fork (UltimateBoomer/Strata), whose server had
the turn scheduler, priorities, disk-parked conversations and the console; those are
not in PolyStrata. The image builds the checkout at `STRATA_SRC` (default `../PolyStrata`) as its working tree is, uncommitted
changes included, so a change is tested with `make up` before it is committed or
pushed. The `Containerfile` copies the engines' sources (`engine/`, and the
picture reader's `terminal/tools/vision/`) and compiles `strata` and `strata-poly` in
trees of their own and the rest of
the tree after, so a change to the server or the web page (`terminal/`) rebuilds in
seconds and only an engine change recompiles. The image is labelled with the
commit (`org.opencontainers.image.revision`, `-dirty` for uncommitted changes) and
the last commit to the engine's sources (`org.strata.engine-sources`). The
llama.cpp commit is the one the checkout pins in `engine/third_party/ggml/VERSION.txt`.
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

### Web page

PolyStrata's chat page is served at `http://<host>:9931/`, with a model list that
switches between the three models. The run config is not edited from the page:
change `config/` and run `make up`.

## Validation

To calibrate Strata's decode settings for this PC with the existing image and
prepared model:

```sh
make calibrate
```

This stops the Strata server and runs upstream `terminal/tools/calibrate.py` against the
run config of one model (`STRATA_VARIANT`, default `q1`), made as
`scripts/serve.py` makes it, in a temporary
container with the same GPU and model mounts.
Stop other GPU inference servers first. Calibration measures PCIe share, draft
confidence, CPU worker count, and adaptive expert-cache settings; it can take
15–30 minutes or longer. It prints recommended settings and measurements without
editing the configuration. Apply the desired settings to `config/server.json`
(or the model's file when they differ by model), then restart serving with
`make up`.
The server stays stopped if calibration finishes or fails.

### hipBLASLt tuning

Both engines read a prompt's matrix products through hipBLASLt only with a tuning
table for this GPU architecture and the image's exact hipBLASLt version
(`STRATA_HIPBLASLT_TUNING`, `engine_env.hipblaslt_tuning` in a model's config);
otherwise they fall back to plain hipBLAS. A table's rows are matrix shapes, so each
model has its own, calibrated on pc26's RX 7800 XT (hipBLASLt 1.0.0, ROCm 7.0.2):

| Model | Engine | Shapes | Table | Named in |
| --- | --- | --- | --- | --- |
| Qwen3.8-Flash-Next (q1, q3) | strata | `scripts/hipblaslt-shapes/qwen.txt` | `config/gfx1101-hipblaslt-100000-qwen.txt` | `config/server.json` |
| Inkling 2 Mini | strata-poly | `scripts/hipblaslt-shapes/inkling.txt` | `config/gfx1101-hipblaslt-100000-inkling.txt` | `config/server-inkling.json` |

After changing the ROCm image or the card, regenerate them:

```sh
make tune
```

This stops the server, builds the image, and runs `tune_hipblaslt` over each model's
shapes, measuring every solution id on this card (a few minutes a model). It writes
`config/<arch>-hipblaslt-<version>-<model>.txt` for every model; point each model's
`hipblaslt_tuning` at its file if the name changed, then `make up`. The engines refuse a
table for another version, logging it, and read prompts on plain hipBLAS again. To tune
another model, add `scripts/hipblaslt-shapes/<model>.txt` (one `tune_hipblaslt --case`
a line: `dtype,T,N,K,ldy`) and name its table in the model's config.

strata-poly takes the table in our PolyStrata fork only (`src/poly/hip_lt.cpp`): in a
HIP build its quantized prompt products are dequantized matrices multiplied by hipBLAS,
and rocBLAS's own kernels for Inkling's shapes are 3-6x slower than the tuned solutions.
Measured on pc26 on 2026-11-23 (Inkling 2 Mini UD-Q4_K_XL, `--prompt-batch 2048`, one
run each): 8K / 32K-token prompts at 477 / 426 tok/s without the table, 916 / 765 with
it, and 953 / 801 with `ROCBLAS_USE_HIPBLASLT=1` too (rocBLAS's hipBLASLt backend for
the attention products), as `config/server-inkling.json` runs it.

For Qwen:

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
