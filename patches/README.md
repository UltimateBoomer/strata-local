# Strata patches

The `Containerfile` applies every `strata-*.patch` here, in name order, to the pinned
`STRATA_REVISION` before compiling. The build fails if one does not apply, so rebase
the patches whenever the revision changes.

- `strata-disk-conversation-cache.patch` adds `--conversation-cache-dir DIR`: parked
  conversations are written to `DIR/park-N.conv` (the session-file format) with a small
  `park-N.meta` skeleton for matching, instead of being kept in RAM. The files survive
  restarts and are bound to the model and engine configuration; files from another one
  are removed at start. `--conversation-cache-mib` then caps the files' total size
  (oldest pruned first, also at start), and `--conversation-cache-slots 0` removes the
  slot limit. Parking streams K/V from the device into the file, so only the running
  state and checkpoints are copied to RAM; restoring still reads the whole file into
  RAM briefly, subject to `--conversation-cache-min-free-mib`. One GPU with `--mtp`
  only; otherwise parking stays in RAM.
