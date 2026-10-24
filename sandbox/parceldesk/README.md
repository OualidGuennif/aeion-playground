# ParcelDesk sandbox — task contract

This directory is the real workspace an agent receives. It is copied per mission
into `runtime/sandbox/runs/<mission_id>` and committed to a local git repository
before the agent starts.

## Scope

- Editable: `app/**`, `tests/**`
- Protected: `policy/**`, `.github/**`, `AGENTS.md`, `secrets/**`

A patch that touches a protected path is denied by the gate. The agent cannot
widen its own authority.

## Contract

`quote(grams: int) -> int` accepts integer grams from 1 to 30 000 and returns
integer cents: 500 cents for the first started kilogram, 300 cents for every
additional started kilogram.

The baseline is defective. The test suite encodes the contract and fails until
the defect is repaired. Run it with:

```bash
python -m pytest -q
```

Do not commit, push, install packages or contact the network. The operator
seals the evidence packet after the gate.
