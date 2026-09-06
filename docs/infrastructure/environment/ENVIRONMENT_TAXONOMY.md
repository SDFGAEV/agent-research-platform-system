# Noetrium environment taxonomy

## Boundary

An environment is an external world that an agent can observe and act on,
whose state evolves under actions, time, or exogenous events. The canonical
provider seam is observe, act, receipt/reconciliation, recovery, and close.

A category describes the world and its interaction semantics. An implementation
is a concrete simulator, server, hardware adapter, operating-system instance,
or application backend. Interfaces such as pixels, DOM, keyboard/mouse, CLI,
API, sensors, and actuators are orthogonal capabilities.

## Canonical categories

| ID | World | Typical surfaces |
| --- | --- | --- |
| minecraft | voxel/open-world game world | world API, visual, command |
| embodied | physical or simulated body-in-world | sensors, actuators, trajectories |
| gui | desktop/mobile operating-system world | pixels, accessibility tree, input |
| web | stateful browser/web-application world | DOM, pixels, navigation, HTTP |
| software | repository and software workspace | terminal, filesystem, tests |
| text_world | text-mediated stateful world | text commands and observations |

## Explicit exclusions

Benchmark and scenario suites belong to research/study. Replay and synthetic
execution are environment modes. Multi-agent topology belongs to orchestration.
Reinforcement learning is a learning method. Tools, MCP servers, and function
calling belong to agent capabilities. Python, Conda, Node, and containers are
execution runtimes and resource bindings.

These concepts may wrap or configure an environment, but they are never
registered as environment categories.
## Package layout

    capabilities/environment/
      api/                 generic provider and interaction contracts
      category/            exactly the six canonical categories
      minecraft/           Minecraft contracts and providers
      embodied/            embodied contracts and adapters
      gui/                 GUI contracts and future backends
      web/                 web contracts and future backends
      software/            software-workspace contracts and future backends
      text_world/          text-world contracts and future backends
      runtime/modes/       replay and synthetic construction modes
      catalog/instance/    environment lifecycle and binding infrastructure

Concrete benchmark names must not appear in category descriptors. A benchmark
adapter receives a category provider plus a task case and remains owned by the
research experimentation plane.

## Implementation status

The canonical catalog exposes every planned backend explicitly. The available
status means Noetrium ships the provider composition; contract_only means the
public category contract and provider seam are stable, but the external backend
must be supplied by a downstream project. Consumers must query implementation
status before attempting to open a provider; an unknown implementation ID is an
error, not an implicit fallback.
