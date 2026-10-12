# gw-orchestrate

Work planning, dispatch, placement, stage advancement, gates and integration over
resolved `gw_kernel.workspace` primitives. Public callables live in qualified
modules; the package exports no facade API.

## Platform

The gate runner detaches with `start_new_session=True` on POSIX and
`CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows. Gate commands run
through `sh -c` on POSIX and `%COMSPEC% /c` on Windows; configure commands for
the runner's host shell. Liveness uses the shared filesystem lock seam.
