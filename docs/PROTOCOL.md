# External project protocol, draft v0

This is a proposed Kestrel protocol, not an existing community standard. Implement the smallest useful contract and publish a conformance test kit.

## 1. Integration principle

A project supplies a versioned sidecar manifest that describes commands, input/output schemas, environment requirements, data references, and available checks. Kestrel does not import the project's Python modules into the controller or demand that it adopt a trainer interface.

The sidecar may be in the user's private lab directory. Registration need not modify the project. An onboarding agent may inspect code and propose a sidecar; active execution and dependency installation require separate authorization and sandboxing.

Treat repository Git configuration as untrusted too: disable executable hooks, fsmonitor commands, filters/smudging, recursive submodule actions and similar behavior during metadata/source resolution. Do not allow a supposedly read-only Git inspection to invoke repository-selected programs.

## 2. Capabilities, not mandatory lifecycle functions

An adapter declares any subset of capabilities such as:

- inspect_static
- validate_candidate
- execute
- predict
- resume
- verify_outputs
- evaluate
- check_proof

Not every project has training, prediction, or checkpointing. Missing capabilities remain explicit. The framework must not label evidence independently evaluated when the project only returns self-reported metrics.

The independently registered evaluator is a separate identity from the candidate adapter. A candidate cannot acquire evaluator trust by declaring an `evaluate` command. Controller approval binds to the exact evaluator artifact and analysis protocol.

## 3. Process boundary

Invoke an argument vector, not an interpolated shell string. Run inside the selected sandbox. Use a known workspace root and controlled environment. For protocol-aware adapters, stdin contains one JSON request; stdout contains the machine response; logs go to stderr. Large data are passed as read-only files referenced by artifact identities, not embedded into prompts.

For an existing command that cannot speak JSON, supply a wrapper launched in the sandbox. The wrapper converts declared inputs/outputs; it is untrusted project execution, not privileged controller code.

A request includes protocol_version, task_id, attempt_id, contract_digest, operation, input_artifacts, output_directory, and operation_parameters. A response includes protocol_version, attempt_id, status, produced_artifacts, and structured diagnostics. The controller checks size limits, schemas, matching attempt identity, and safe relative paths.

Worker-supplied artifact digests are hints. A trusted ingestion step recalculates digests from safe regular files, rejects symlink/path traversal and incomplete writes, and moves or copies completed artifacts to controller-owned storage. Workers cannot choose arbitrary host output locations.

## 4. Completion

A zero exit status, valid response, complete files, verified digests, and required checks are all needed. A response saying “success” with missing artifacts fails. Publication is atomic: stage outputs, validate, then register completion. On partial failure, retain diagnostic artifacts separately without presenting them as a valid result.

## 5. Compatibility

Adapters declare protocol major/minor version and capability versions. Unknown major versions fail before execution. Minor optional features must be negotiated, not ignored when they affect safety or science. A conformance command should exercise schema validation, failure reporting, output-path restrictions, and a declared tiny test operation.

## 6. Environment and data resolution

Resolve environment lockfiles or image digests before the experiment. Dataset acquisition/build is a separate budgeted stage with its own source, license and network policy. Never assume dependency setup is harmless; build/install scripts may execute code. Treat them as untrusted workloads without holdouts, private credentials, or sensitive data.

Immutable data may be supplied through read-only mounts. Source code and secret-bearing host files are never recursively mounted by convenience. Secrets needed for acquisition belong to a restricted service or proxy, not to every experiment process.

## 7. Imported evidence

A manual import may include existing output tables, model checkpoints, or proof logs. Record its source and limitations. Do not invent missing seeds, versions, or environment details. Imported and independently re-executed evidence have distinct assurance fields.

## 8. Example location

See `examples/project.sidecar.yaml`. All identifiers and paths in that file are illustrative; placeholder identities must fail production validation until resolved.
