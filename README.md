# hermes-proactive-core

A thin, fail-closed proactive event/policy/state layer for the user's VPS-hosted Hermes deployment.

## Project status

- Production Hermes remains unchanged by this repository bootstrap.
- Phase 1–2 has been completed and tested offline in the existing IdeaForge workspace.
- Production rollout must proceed through shadow/canary gates before notifications or actions are enabled.
- `main` is the stable baseline. Development work belongs on dedicated branches and must not be merged without explicit approval.

## Safety defaults

The Proactive Core is designed to be silent by default, deterministic first, and unable to bypass explicit risk gates. High-risk actions require user approval. Production notifications and actions remain disabled until their respective acceptance gates pass.
