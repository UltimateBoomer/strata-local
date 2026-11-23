# Notes for agents

- This repository is the local deployment of our Strata fork (the image recipe, configs and scripts); the fork's
  source is a separate checkout, `../Strata` by default (`STRATA_SRC`). The image is built from that checkout's working
  tree, so an uncommitted change to the engine, the server or the console there is what `make up` deploys.
- Shut down the deployment before performing any build: an image build here, or a build of the fork's engine for
  testing. It runs on this machine (pc26) either as the compose server (`make down`, back with `make up`) or as the
  Quadlet unit (`systemctl --user stop strata-local-server`, back with `systemctl --user start strata-local-server`);
  check which with `podman ps` and `systemctl --user status strata-local-server`.
- `CONTAINER_CONNECTION` (README: Remote host) points `make`, `podman` and `podman compose` at another machine; it is
  not set by default.
