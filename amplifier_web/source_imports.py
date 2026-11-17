"""A helper process that imports a module from a prepared source leaves the source unchanged."""
import sys


async def prepare(bundle, **options):
    """Foundation's preparation, for a helper process that then imports the module.

    Foundation puts the prepared source first on ``sys.path``. On a host whose
    environment names the shared source store (a Windows installation) that is
    an object of the store; else it is a checkout in Foundation's cache. Python
    would write ``__pycache__`` beside each file that the process imports from
    there, and Foundation counts every such file as a change of the source
    (``sources.shared.clean_checkout``). It refuses a changed store object from
    then on ("Shared source contents changed"): no conversation with that
    module starts, and a component update fails. POSIX seals the folders of a
    store object. A read-only attribute on a Windows folder does not stop a new
    entry.

    So from this call on the process writes no bytecode. Workers and update
    stages have that rule for their whole life, from
    ``update_sources.store_environment``. A helper has the host's environment,
    and it keeps the bytecode of everything that it imported from its own
    environment before this call: conversation workers read that bytecode and
    write none themselves.
    """
    sys.dont_write_bytecode = True
    return await bundle.prepare(**options)
