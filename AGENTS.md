# Notes for agents

- This repository is the local deployment of our PolyStrata fork (the image recipe, configs and scripts); the fork's
  source is a separate checkout, `../PolyStrata` by default (`STRATA_SRC`), on its `inkling-amd` branch. The image is
  built from that checkout's working tree, so an uncommitted change to the engines or the server there is what
  `make up` deploys.
- `config/` is not read by the server as it is: `scripts/run_configs.py` turns it into one PolyStrata run config per
  model at each start (`serve.py`). Test a model change with long prompts too: a prompt-reading fault can leave short
  prompts working.
- Shut down the deployment before performing any build: an image build, or a build of the fork's engine for testing.
  `make sync` and `make up` do (`make down`, or `make stop-server`, stops it alone). The deploy host runs it as the
  Quadlet unit `strata-local-server` (`STRATA_SERVER=quadlet`); `make status` shows its container.
- Development happens on other machines; the deploy host (`DEPLOY_HOST` in `.env`, which mise loads; not committed)
  runs and tests the server and has no checkout. Do not write a machine's name, address or connection into tracked
  files: they go in `.env` (`.env.example`). Off the host, the make targets work there through its Podman socket
  (`DEPLOY_CONNECTION`): builds upload `STRATA_SRC` (which must have `inkling-amd` checked out) and config/ goes into
  its volume. Only the Quadlet unit (its file, `systemctl --user`) goes over SSH; keep everything else on the socket.
  `make sync` deploys the working trees (stop, build, start); `make status` checks the server. README: Develop here,
  deploy on a remote host.
- `CONTAINER_CONNECTION` (README: Remote host) points `make`, `podman` and `podman compose` at another machine. The
  Makefile sets it to `DEPLOY_CONNECTION` off the deploy host; a plain `podman` still talks to this machine unless you
  set it.
