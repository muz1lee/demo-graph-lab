# Security and public-release policy

This repository is a public, sanitized source repository. It must not contain
credentials, internal service tokens, simulator assets, task/scene ground
truth, model checkpoints, generated run artifacts, or third-party code whose
license forbids redistribution.

## Ground-truth firewall

Generated policies may use only:

- task instructions and demonstration evidence;
- sensor-derived perception results with provenance;
- robot-observable state and action feedback;
- allowlisted, task-agnostic priors.

Generated policies must not read scene or asset libraries, simulator entity
state, exact poses or dimensions, evaluator predicates, target bindings, or
values derived from those sources. Oracle evaluation runs in a separate
trusted process, and its artifacts must never be supplied to policy
generation, selection, recovery, or execution.

## Release checklist

Before every public push:

1. stage only explicit allowlisted paths; never use `git add .`;
2. scan tracked files for credentials and internal endpoints;
3. reject model weights, datasets, run outputs, and files larger than 10 MiB;
4. verify source manifests for imported components;
5. run the unit, integration, and ground-truth firewall tests.

Security-sensitive runtime configuration belongs in `configs/local/`, which is
ignored. Committed configuration files must be examples with placeholder
values only.

