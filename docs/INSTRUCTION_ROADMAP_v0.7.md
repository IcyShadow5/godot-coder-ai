# Instruction and agent roadmap

> This draft comes from the v0.7 phase and is still the basis for
> the planned stages. Instruction tuning and agents are deliberately not yet
> milestones of v0.10.x - only when the domain base and the verifier loop
> are in place. The current state on this is in `ROADMAP.md`.

## Stage 1 - Domain pretraining

The base model learns GDScript, Godot APIs, project structures and common
implementation patterns from clean source code. More raw code improves the
language and domain base but alone does not produce a reliable
task assistant.

## Stage 2 - Supervised instruction tuning

Requires verified pairs of:

- instruction and desired code
- buggy code and repair
- project context, plan and patch
- task, tests and verified result

The loss should later only lie on the assistant answer. The v0.7 seed work
generates deterministic seed tasks for this but does not yet mark them as
training-ready replacement for curated tasks.

## Stage 3 - Verifier and repair loop

Generated solutions are evaluated with Godot, tests and static checks.
Successful results can be adopted as high-quality retraining data;
failed outputs stay separate and are not blindly trained.
The building blocks already grow today: the managed-process runner,
the Godot parser check with fallback and the error-rate abort are essentially
the same infrastructure a verifier needs later.

## Stage 4 - Agent runtime

An agent is more than the language model. It needs limited tools and
a controlled flow:

1. Read files and project status
2. Break the task into verifiable steps
3. Create a patch
4. Run Godot or tests
5. Evaluate errors and repair in a limited way
6. Report changes and evidence

This stage may only follow on a stable instruction and verifier base.
