# Environment assignment isolation

## Ownership

Noetrium owns the provider-neutral lifecycle contract:
EnvironmentAssignmentIdentity identifies one assignment and
EnvironmentAssignmentIsolationPort prepares/finalizes an isolated
environment instance and returns a durability-bearing isolation receipt.

Noetrium does not decide whether a scientific task succeeded. It owns
lifecycle, identity, recovery, storage, and provider binding.

Concrete providers own the isolation mechanism:

- Minecraft uses world cuts, branch workdirs, server lifecycle, endpoint binding, and action recovery.
- GUI/Web/Software/Text World providers may use browser profiles, containers, repositories, or process sandboxes.
- A provider must not reuse a mutable global session for two assignments.

SEM owns scientific assignment selection, method policy, task goals, success
predicates, metric definitions, benchmark mapping, and claim status. SEM may
consume the generic isolation port, but must not reimplement provider state
restoration inside scientific task logic.
