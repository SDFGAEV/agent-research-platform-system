# Environment Provider Contract for New Projects

This document defines the ROLE05-owned environment/provider seam intended for downstream project scaffolds and clean-room generated-project tests.

## Public imports

Downstream projects should depend on:

- `noetrium_platform.capabilities.environment.api.ExecutionContext`
- `noetrium_platform.capabilities.environment.api.EffectReceipt`
- `noetrium_platform.capabilities.environment.api.EffectClass`
- `noetrium_platform.capabilities.environment.api.EffectCertainty`
- `noetrium_platform.capabilities.environment.api.EnvironmentProviderPort`
- `noetrium_platform.capabilities.environment.api.EnvironmentProviderCapabilities`
- `noetrium_platform.capabilities.environment.api.EnvironmentCapability`
- `noetrium_platform.capabilities.environment.api.EnvironmentSession`
- `noetrium_platform.capabilities.environment.api.EnvironmentSessionDiagnostics`
- `noetrium_platform.capabilities.environment.api.EnvironmentDiagnosticsPort`
- `noetrium_platform.capabilities.environment.api.EnvironmentCapabilityUnsupported`
- `noetrium_platform.capabilities.environment.api.EnvironmentConformanceProbe`
- `noetrium_platform.capabilities.environment.api.EnvironmentProviderConformanceReceipt`
- `noetrium_platform.capabilities.environment.api.verify_environment_provider_conformance`

Projects must not import provider-private runtime state, Minecraft bridge internals, state-machine checkpoint codecs, or platform service locators.
Provider-author tests should obtain the canonical execution context through `noetrium_platform.capabilities.environment.api.ExecutionContext`; downstream source must not import `noetrium_platform.foundation.kernel.kernel` directly. This is a public alias of the same Platform type, not a second context authority.

## Embodied environment peer

具身智能与 Minecraft 都是 Environment 的同级 provider family。具身侧可以接入真实机器人、仿真器、数据集回放、策略服务或硬件控制器，但这些细节只存在于 `environment.embodied` 的 composition/provider 边界。上层实验、运行控制、资源、指标和诊断只依赖下面的通用 EnvironmentProviderPort。

具身事件通过 `RegistryBoundEmbodiedTrajectorySink` 进入统一 raw observation lake：原始字节、事件顺序、episode、sensor/action 维度、来源、状态、时间和拓扑身份均保留；指标和 benchmark 结果是可重算的下游投影。平台不把任何机器人 SDK、仿真器或 VLA 模型实现塞进核心。

## Provider shape

An environment provider exposes only three things at the project boundary:

1. immutable `EnvironmentIdentity`;
2. typed optional-capability declaration;
3. `open_session(session_id=..., services=...)` returning the public `EnvironmentSession` contract.

`observe`, `act`, and `close` are baseline session behavior. Snapshot, restore, reconciliation, and diagnostics are optional capabilities that must be declared explicitly.

## Unsupported capability semantics

A provider must not make consumers discover optional behavior with `hasattr`, broad exception handling, or silent no-op fallbacks.

If a capability is absent from `EnvironmentProviderCapabilities`, the corresponding session method must fail with `EnvironmentCapabilityUnsupported` carrying the exact capability identity. The conformance runner checks this fail-closed behavior for snapshot/restore and, when an action receipt is available, reconciliation.

`UNKNOWN` external-effect state is not success. Providers that declare reconciliation support must reconcile against their authoritative action/effect state and preserve request identity. A facade must not convert an unknown or unproven effect into a confirmed one.

## Typed diagnostics

Providers that declare `DIAGNOSTICS` expose `EnvironmentDiagnosticsPort.diagnostics_snapshot()`.

The snapshot binds:

- session identity;
- immutable environment identity;
- current environment generation;
- ready/closed state;
- exact capability declaration;
- optional canonical state digest;
- optional evidence references.

Diagnostics are inspection data. They do not become lifecycle, action, checkpoint, or scientific authority.

## Minimal non-Minecraft reference provider

`noetrium_platform.capabilities.environment.composition.reference_counter_environment()` is the public Platform-owned clean-room reference composition. The implementation remains owned under `environment.providers`; downstream project source imports only the public `environment.api` contracts and, when it needs the bundled runnable reference, the public `environment.composition` factory.

It is deliberately tiny: a deterministic counter with `increment` and non-mutating `reject` actions. It uses the generic state-machine runtime, so the example exercises real action identity, effect receipts, snapshot/restore, reconciliation, diagnostics, and close semantics without requiring Java, Node, Minecraft, a server, or a benchmark-specific world.

Platform-owned and generated-project doctor/conformance tests may instantiate the reference through `noetrium_platform.capabilities.environment.composition` to prove the generic seam. A downstream project may instead implement its own provider against `noetrium_platform.capabilities.environment.api`; neither route requires importing `environment.providers` or runtime internals.

Provider conformance is exercised with:

```bash
python -m pytest -q tests/test_typed_environment_provider_npe_v1.py
```

The same test is required on Windows and on the Linux Platform validation node for an exact source revision.

## Artifact, data, and observation surfaces

New projects should reuse existing ROLE05 public contracts instead of creating project-local evidence schemas:

- `noetrium_platform.evidence.artifact.catalog.api.ArtifactRecord` carries content SHA-256, scope, producer identity, lineage, media type, and retention declaration;
- run evidence should use a `ScopeIdentity(ScopeKind.RUN, run_id)` so the storage/catalog record carries explicit run lineage without claiming scientific acceptance;
- `noetrium_platform.evidence.data.dataset.api.DatasetVersion` carries dataset content digest and scope;
- `noetrium_platform.evidence.observability.api.EventEnvelope` is tagged `SIDE_PLANE_OBSERVATION` and is never primary operational/scientific authority;
- `noetrium_platform.evidence.observability.status.api.SubsystemSnapshot` is a read-only project/doctor projection with evidence refs and stable reason codes.

ROLE03 remains the owner of run lifecycle, checkpoint policy, persisted run-control bytes, and scientific evidence finalization. ROLE05 artifact records prove storage/content identity and lineage; they do not decide whether a scientific claim is acceptable.

ROLE06 owns the common project facade, scaffold/template, and doctor/inspection commands. Those consumers should import the ROLE05 contracts above rather than define parallel environment, artifact, evidence, or diagnostics schemas.

## Provider-author checklist

Before handing a new adapter to project composition:

- freeze a non-empty environment identity with canonical artifact SHA-256;
- declare optional capabilities before opening a session;
- make unsupported optional operations fail with `EnvironmentCapabilityUnsupported`;
- preserve action identity across retries and reconciliation;
- never mutate authoritative state on a rejected action;
- make snapshot restore validate before mutation;
- expose typed diagnostics only as a read-only projection;
- run the provider conformance suite on every supported operating system;
- keep benchmark/task/scientific policy in the downstream project.
