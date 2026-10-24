## What this changes

<!-- One behaviour. If it needs a new service, dependency or network route, say why here. -->

## Evidence

<!-- Commands you ran and their real output. "Tests pass" alone is not evidence. -->

```sh
```

## Boundary check

- [ ] No untrusted role gains a Docker socket, host path, credential, network route or approval right.
- [ ] Nothing is described as secure without stating the scope it covers.
- [ ] Unverified parts are labelled unverified.
- [ ] Sealed evidence, the accepted cockpit design and the ParcelDesk design are untouched, or the reason is stated above.

## Failure behaviour

<!-- What happens when the backend is missing, the container is killed, the disk is full, the collector refuses? -->

## Rollback

<!-- How to undo this without losing ledger data. -->
