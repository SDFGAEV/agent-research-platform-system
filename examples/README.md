# Noetrium examples

The examples in this directory are small, deterministic entry points into public Noetrium contracts. They intentionally avoid private test helpers, hidden state, API keys, and machine-specific infrastructure.

## Quickstart: inspect the environment catalog

Run `python -m examples.quickstart_environment_catalog` to enumerate the six
canonical environment families and inspect each implementation's explicit
available/contract_only status. Benchmark, replay, synthetic, tool and
multi-agent concepts are shown only as exclusions; they are not catalog entries.

## Quickstart: compile a reproducible experiment plan

Run:

```bash
python -m examples.quickstart_experiment_plan
```

The example freezes two study variants into a `StudyProtocol`, binds each variant to an explicit provider identity, compiles an `ExperimentPlan`, and verifies that its protocol, binding, and plan digests remain consistent.

## Quickstart: durable memory

Run `python -m examples.quickstart_durable_memory` to write an episodic memory item to SQLite WAL, close the store, reopen it, and verify the exact content digest.

## Quickstart: foreign framework bridge

Run `python -m examples.quickstart_langgraph_bridge` to normalize a LangGraph-shaped dict state and decision without installing or importing LangGraph.

## Quickstart: interrupt and resume

Run `python -m examples.quickstart_graph_interrupt` to pause a typed state graph at a checkpoint and resume the same thread with an explicit value.

## Quickstart: transport-backed multi-agent

Run `python -m examples.quickstart_multi_agent_transport` to keep the deterministic topology/causal checks while routing node work through an injected transport and membership port.

## Durable lifecycle and recovery

`ReferenceReActMethod` and `ReferencePlanAndSolveMethod` accept an `ExecutionContext` plus a progress port. Use `JsonlReferenceAgentProgress` when a local run must survive process failure; checkpoints and lifecycle events are append-only and digest-addressed.

`CompiledStateGraph.history(thread_id)` exposes the complete checkpoint lineage, and `checkpoint_id=` replays from an exact historical snapshot. `SQLiteMultiAgentJournal` provides the same crash-durable latest-checkpoint boundary for coordinator transcripts.

## Quickstart: reuse a method component

Run `python -m examples.quickstart_agent_components` to execute a public ReAct loop with an explicit Tool Registry. Replace the downstream policy or the whole method without editing Platform source. The reusable component layers and the higher multi-agent tier are documented in [`docs/architecture/COMPONENT_LAYERS.md`](../docs/architecture/COMPONENT_LAYERS.md).

Expected shape:

```text
study=noetrium-quickstart
variants=control,treatment
repetitions=3
protocol_digest=<sha256>
plan_digest=<sha256>
plan_consistent=true
```
