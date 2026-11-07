# Reviewed sandbox image recipe

Build from an exact Python base image digest and a hash-checked dependency lock:

```sh
./sandbox-image/build.sh python:3.12-slim@sha256:<64-hex-base-digest> local/researchos-python:3.12
```

The checked-in lock has no third-party packages. Add reviewed exact package versions and approved wheel hashes before building an image that offers dependencies. `pip --require-hashes` refuses unpinned transitive dependencies. The Dockerfile adds no startup service; ResearchOS supplies its own sleeping keeper process and runs submitted Python with `docker exec`.

Docker assigns a local image ID during build. For ResearchOS, publish the reviewed build to a registry and use the registry's returned `repository@sha256:<manifest-digest>` in `RESEARCHOS_SANDBOX_IMAGE` and in the JSON manifest. Pull that exact reference onto each execution host before running; ResearchOS always uses `--pull=never`. The manifest remains an operator attestation and does not inspect installed package contents. Keep the base reference, lock, image build logs, and registry digest together for audit and rebuilds.

This recipe improves repeatability; it is not a signed-build or supply-chain verification system. A registry rebuild may produce a different image digest and must receive a new manifest.
