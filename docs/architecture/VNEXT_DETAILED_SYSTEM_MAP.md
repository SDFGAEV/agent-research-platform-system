# vNext Detailed System Map

The target architecture is intentionally fine-grained. A node exists only where there is a distinct responsibility, lifecycle, durable authority, effect authority, identity domain or replaceable provider boundary.

## Recursive rules

- Parent owns composition of children, never child implementation state.
- Each node has exactly one primary authority declaration.
- Observation nodes cannot mutate durable domain authority.
- Provider selection happens only in composition.
- API packages contain contracts/identities/ports, not workers, locks, persistence mutation or provider branching.
- This tree is established before implementation migration; old modules are not used to define the new boundaries.

## Artifact System

- **catalog** — `artifact_catalog` — owns: artifact metadata and logical identity catalog; must not own: content bytes mutation.
- **content** — `artifact_content` — owns: immutable content storage and content digest identity; must not own: business metadata.
- **lineage** — `artifact_lineage` — owns: artifact lineage and provenance relations; must not own: scientific result truth.
- **reference** — `artifact_reference` — owns: references, aliases and cross-system artifact pointers; must not own: content mutation.
- **retention** — `artifact_retention` — owns: retention, pinning and garbage-collection policy; must not own: business state semantics.
  - **relation** — `artifact_lineage_edge` — owns: immutable artifact lineage edge identity; must not own: scientific result semantics.

## Data System

Data parent dependency contract: `data -> artifact, platform, scope`. Cross-query composition may consume immutable Artifact identity/catalog facts, while Artifact remains the producer authority for immutable content identity.

- **dataset** — `dataset_authority` — owns: dataset identity, schema references and lifecycle; must not own: dataset physical storage implementation.
- **fact** — `fact_authority` — owns: durable fact envelopes and authoritative fact writes; must not own: business-specific state transitions.
- **projection** — `projection_authority` — owns: derived read models and projection lifecycle; must not own: source-of-truth mutation.
- **query** — `query_contracts` — owns: read query contracts spanning non-authoritative projections; must not own: durable writes.
- **record** — `record_authority` — owns: generic record envelopes and record identity; must not own: artifact content bytes.
- **state** — `state_authority` — owns: canonical mutable state and state-store contracts; must not own: disposable projections.
  - **cross** — `cross_query` — owns: cross-authority read composition and query federation; must not own: writes and authority mutation.

## Environment System

- **binding** — `environment_binding` — owns: binding environment specs to scopes/runs/participants; must not own: artifact storage.
- **embodied** — `embodied_environment` — owns: embodiment/episode/event contracts and provider-facing trajectory interaction; must not own: generic environment authority, experiment semantics, model serving, telemetry storage or vendor SDK internals.
- **catalog** — `environment_catalog` — owns: environment catalog and versioned definitions; must not own: resource capacity.
- **instance** — `environment_instance` — owns: environment instance identity, readiness and lifecycle; must not own: host supervision implementation.
- **python** — `python_environment` — owns: Python runtime/interpreter environment contracts; must not own: generic process supervisor.
- **resolution** — `environment_resolution` — owns: resolve logical environment requirements to concrete instance plan; must not own: process lifecycle.
- **runtime** — `environment_runtime_contract` — owns: environment runtime adapter contracts; must not own: environment catalog authority.
- **specification** — `environment_spec` — owns: environment definition and immutable spec identity; must not own: live host process state.
  - **identity** — `environment_instance_identity` — owns: environment instance identity and provenance; must not own: host process lifecycle.
  - **readiness** — `environment_readiness` — owns: environment readiness observations/contract; must not own: authoritative process health.
  - **digest** — `environment_digest` — owns: exact environment specification identity and digest; must not own: resource resolution.
  - **schema** — `environment_schema` — owns: environment requirement schema and canonical forms; must not own: environment instance lifecycle.

## Execution System

- **admission** — `admission_decision` — owns: hierarchical execution quotas, identity-aware admission decisions and lease accounting; must not own: scheduling order/fairness, executor lifecycle or model/environment truth.
- **capability** — `capability_catalog` — owns: capability declarations and invocation contracts; must not own: provider implementation.
- **command** — `command_intent` — owns: typed execution commands and command routing; must not own: human UI and provider-specific control.
- **operation** — `operation_state` — owns: operation identity, lifecycle and result envelopes; must not own: failure taxonomy and recovery authority.
- **scheduling** — `schedule_intent` — owns: priority, aging, fairness and deterministic scheduling order; must not own: live resource/admission state, quotas or executor lifecycle.
- **workflow** — `workflow_state` — owns: workflow definitions and orchestration semantics; must not own: process supervision.

## Experimentation System

- **branch** — `branch_state` — owns: run branching and branch lineage; must not own: generic artifact lineage.
- **checkpoint** — `checkpoint_state` — owns: checkpoint identity, binding and lifecycle; must not own: artifact content storage.
- **experiment** — `experiment_state` — owns: experiment definitions, variants and experiment lifecycle; must not own: runtime process state.
- **run** — `run_state` — owns: run identity, frozen run contract and run lifecycle; must not own: server supervision internals.
- **study** — `study_state` — owns: study definitions, hypotheses and study lifecycle; must not own: method implementation internals.
- **variant** — `variant_state` — owns: experiment variants, assignments and comparison semantics; must not own: model deployment internals.
  - **identity** — `run_identity` — owns: run identity, immutable manifest and parent links; must not own: live execution state.
  - **lifecycle** — `run_lifecycle` — owns: run lifecycle state and transitions; must not own: runtime server lifecycle.
  - **manifest** — `run_manifest` — owns: frozen run contract and exact dependencies; must not own: runtime mutable state.

## Governance System

- **architecture** — `architecture_policy` — owns: architecture rules, dependencies and invariants; must not own: business state.
- **quality** — `quality_policy` — owns: quality gates, audits and invariants as descriptive policy; must not own: runtime business control.
- **release** — `release_authority` — owns: release identities, manifests, verification and promotion semantics; must not own: runtime process state.
- **schema** — `schema_authority` — owns: schema/version declarations for contracts and records; must not own: domain state mutation.
- **security** — `security_policy` — owns: security/redaction/classification policy; must not own: scientific method semantics.
- **system_registry** — `system_topology` — owns: recursive system topology and ownership declarations; must not own: runtime orchestration.
  - **authority** — `authority_policy` — owns: authority uniqueness and boundary policy; must not own: authority mutation.
  - **dependency** — `dependency_policy` — owns: allowed dependency graph and import ownership rules; must not own: runtime behavior.

## Model System

- **asset** — `model_asset` — owns: immutable model asset identity and provenance; must not own: artifact byte storage.
- **assignment** — `model_assignment` — owns: assign models to scope/run/participant roles; must not own: serving process lifecycle.
- **catalog** — `model_catalog` — owns: model families/revisions catalog and metadata; must not own: live deployment state.
- **deployment** — `model_deployment` — owns: deployment identity, exact closure and lifecycle contract; must not own: server process implementation.
- **qualification** — `model_qualification` — owns: model/runtime/host qualification evidence and compatibility claims; must not own: live capacity snapshots.
- **request** — `model_request` — owns: model request identity, exact input contract and response envelope; must not own: business result semantics.
- **serving** — `model_serving` — owns: serving endpoint contract and request routing semantics; must not own: model catalog metadata.
- **stack** — `model_stack` — owns: model stack composition and runtime build identity; must not own: server health.
  - **family** — `model_family` — owns: model family identity and metadata; must not own: revision deployment state.
  - **revision** — `model_revision` — owns: versioned model revision identity; must not own: mutable serving state.
  - **closure** — `deployment_closure` — owns: exact deployment closure across model, stack, runtime and artifact identities; must not own: server runtime health.
  - **input** — `request_input` — owns: exact request input identity and canonicalization; must not own: serving process lifecycle.
  - **output** — `request_output` — owns: response envelope and response artifact references; must not own: business metric semantics.
  - **endpoint** — `serving_endpoint` — owns: serving endpoint identity and exposure contract; must not own: request result truth.

## Observability System

- **capture** — `capture_observation` — owns: raw byte/event/process capture contracts; must not own: semantic log interpretation.
- **diagnostic** — `diagnostic_view_contract` — owns: operator-facing diagnostic correlation contracts; must not own: failure/state authority.
- **logging** — `log_observation` — owns: structured logs, context, sinks, stores, queries, retention and capture; must not own: failure taxonomy and recovery.
- **projection** — `observation_projection` — owns: observation projections/indexes and read models; must not own: source-of-truth mutation.
- **status** — `status_observation` — owns: health/status observations and status projections; must not own: authoritative lifecycle state.
- **telemetry** — `telemetry_observation` — owns: metrics/events/counters and telemetry routing; must not own: durable domain state.
- **tracing** — `trace_observation` — owns: trace/span identity and propagation; must not own: business operation truth.
  - **correlation** — `diagnostic_correlation` — owns: cross-system correlation graph for diagnostic references; must not own: causal authority.
  - **query** — `diagnostic_query` — owns: operator/debug query language over observation sources; must not own: source mutation.
  - **snapshot** — `diagnostic_snapshot` — owns: portable diagnostic snapshots assembled from existing authorities; must not own: new business truth.
  - **capture** — `raw_capture` — owns: raw process/stream/event capture before semantic logging; must not own: semantic event taxonomy.
  - **context** — `log_context` — owns: diagnostic context construction and propagation metadata; must not own: log record persistence and query.
  - **projection** — `log_projection` — owns: derived log indexes and projections; must not own: source log truth.
  - **query** — `log_query` — owns: log query contracts and filtering; must not own: log writes.
  - **record** — `log_record_schema` — owns: structured log schema, normalization and identity; must not own: sink routing and storage.
  - **retention** — `log_retention` — owns: retention, archival and deletion policy for logs; must not own: failure retention and artifact retention.
  - **routing** — `log_routing` — owns: log routing rules and fan-out decisions; must not own: log storage mutation.
  - **sink** — `log_sink_delivery` — owns: sink contracts and delivery lifecycle; must not own: query/index semantics.
  - **storage** — `log_storage` — owns: durable or volatile log storage backends; must not own: log schema policy.
  - **health** — `health_observation` — owns: health observations and health snapshots; must not own: authoritative lifecycle transitions.
  - **lifecycle_view** — `lifecycle_projection` — owns: read-only lifecycle status views; must not own: lifecycle state authority.
  - **event** — `telemetry_event` — owns: structured telemetry event definitions and emission contracts; must not own: durable facts.
  - **metric** — `telemetry_metric` — owns: metric definitions, aggregation and metric identity; must not own: business result truth.
  - **context** — `trace_context` — owns: trace/span context creation and attachment; must not own: business operation state.
  - **propagation** — `trace_propagation` — owns: cross-process trace propagation contracts; must not own: trace storage.
  - **storage** — `trace_storage` — owns: trace/span storage backends; must not own: trace identity semantics.

## Operator System

- **audit** — `operator_audit` — owns: audit/reporting views across system authorities; must not own: new durable truth.
- **command** — `operator_commands` — owns: operator command intent and command result contracts; must not own: domain command execution.
- **incident** — `operator_incident_view` — owns: incident triage and incident work surfaces; must not own: incident authority.
- **maintenance** — `operator_maintenance` — owns: maintenance workflows and administrative actions; must not own: provider internals.
- **query** — `operator_queries` — owns: operator read/query contracts; must not own: durable state mutation.
  - **intent** — `operator_command_intent` — owns: human command intents and authorization context; must not own: command execution side effects.
  - **search** — `operator_search` — owns: human-readable search and filtering over read-side projections; must not own: authoritative writes.

## Participant System

- **agent** — `agent_identity` — owns: agent participant contracts and provider-independent agent identity; must not own: model serving lifecycle.
- **binding** — `participant_binding` — owns: binding participants to scopes, methods, environments or models; must not own: provider internals.
- **capability** — `participant_capability` — owns: participant capability declarations and exposure; must not own: execution capability implementation.
- **definition** — `participant_definition` — owns: participant identities and types; must not own: execution session state.
- **method** — `method_participant_binding` — owns: method participant binding contracts; must not own: method implementation itself.
- **session** — `participant_session` — owns: participant session identity and lifecycle contract; must not own: server/process implementation.

## Platform System

- **configuration** — `platform_configuration` — owns: platform configuration sources and frozen configuration snapshots; must not own: domain configuration semantics.
- **identity** — `platform_identity` — owns: platform identity and immutable platform metadata; must not own: workspace/project/run identity.
- **lifecycle** — `platform_lifecycle` — owns: platform startup/shutdown/readiness semantics; must not own: service/process lifecycle.

## Portfolio System

- **membership** — `portfolio_membership` — owns: portfolio-level ownership and membership records; must not own: runtime participant sessions.
- **program** — `program_metadata` — owns: research program metadata and project grouping; must not own: study semantics.
- **project** — `project_metadata` — owns: project metadata, configuration references and lifecycle; must not own: experiment/run execution state.
- **workspace** — `workspace_metadata` — owns: workspace metadata and lifecycle; must not own: generic scope tree authority.

## Reliability System

Section 42 scaffold contraction treats causal/timeline diagnostics, failure catalog/descriptor/envelope/fingerprint/materialization/taxonomy, incident/policy, reconciliation facets, and recovery evidence/plan/replay as facets of retained Reliability authorities rather than independently registered systems.

- **diagnostics** — `diagnostic_queries` — owns: read-side cross-system correlation and root-cause views; must not own: durable authority mutation.
- **effect** — `effect_authority` — owns: external effect intent, outcome certainty and reconciliation state; must not own: process/server ownership.
- **failure** — `failure_authority` — owns: failure taxonomy, envelopes, fingerprints and semantic versions; must not own: diagnostic UI and operator policy.
- **forensics** — `forensic_authority` — owns: durable evidence bundles, causal evidence and forensic indexes; must not own: business result semantics.
- **recovery** — `recovery_authority` — owns: recovery plans, exact replay/reconcile and recovery lifecycle; must not own: provider storage internals.
  - **execution** — `recovery_execution` — owns: recovery execution lifecycle and effect handoff; must not own: failure taxonomy.

## Resource System

Resource catalog identity remains a semantic facet of the retained Resource authority; the empty `resource/catalog` shell is not a separate registered system.

- **allocation** — `resource_allocation` — owns: resource allocation intents and allocations; must not own: execution workflow semantics.
- **compute** — `compute_inventory` — owns: compute resource identity, capacities and provider facts; must not own: environment packaging.
- **directory** — `directory_inventory` — owns: managed filesystem/directory identity and lifecycle; must not own: artifact immutable content.
- **lease** — `resource_lease` — owns: lease identity, acquisition, renewal and release; must not own: server lifecycle.
- **resolution** — `resource_resolution` — owns: resource resolution policies and resolved bindings; must not own: environment/model identity.

## Runtime System

Runtime control/history, process identity/launch/lifecycle, session binding/identity, and supervision remain typed semantics of retained Runtime parent authorities; empty child shells are not separate catalog authorities.

- **host** — `host_runtime_state` — owns: live host identity and runtime host attachment; must not own: resource catalog metadata.
- **process** — `process_state` — owns: process identity, launch contract and lifecycle; must not own: experiment semantics.
- **server** — `server_state` — owns: server identity, lifecycle and health contract; must not own: model serving truth.
- **service** — `service_state` — owns: managed service identity, registration and lifecycle; must not own: scientific truth.
- **session** — `runtime_session` — owns: runtime session identity and host/process bindings; must not own: participant scientific semantics.
  - **supervision** — `process_supervision` — owns: process health/reconcile loops; must not own: durable runtime history storage.
  - **health** — `server_health_contract` — owns: runtime server health contracts; must not own: observability health storage.
  - **identity** — `server_identity` — owns: stable server identity and deployment attachment; must not own: live health.
  - **lifecycle** — `server_lifecycle` — owns: server lifecycle state and transitions; must not own: process internals.
- **toolchain** — `runtime_toolchain` — owns: verified host toolchain acquisition, materialization, identity and receipts; must not own: environment scenarios, experiment protocols, or project policy.

## Scientific semantics convergence

`Scientific` is no longer an independent platform system. ROLE03 Trial/Study convergence folds reusable research-design, measurement, analysis and trial-protocol semantics into `experimentation/study`; concrete method/agent/provider behavior remains behind Participant and downstream project contracts. The historical `noetrium_platform.research.scientific/**` shell is deleted rather than retained as a compatibility authority.

## Scope System

- **hierarchy** — `scope_hierarchy` — owns: parent/child relationships, ancestry, descendants; must not own: project business fields.
- **identity** — `scope_identity` — owns: stable scope identities and typed scope kinds; must not own: portfolio metadata.
- **membership** — `scope_membership` — owns: membership of entities in scopes; must not own: participant sessions.
- **ownership** — `scope_ownership` — owns: generic owner links and owner-path rules; must not own: portfolio business metadata.
- **path** — `scope_path` — owns: canonical scope paths and resolution; must not own: domain-specific routing.
- **resolution** — `scope_resolution` — owns: resolve a scope reference to canonical scope path; must not own: domain-specific lookup semantics.
