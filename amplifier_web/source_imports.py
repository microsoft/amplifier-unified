"""A helper process that imports a module from a prepared source leaves the source unchanged."""
import os
import sys


async def prepare(bundle, **options):
    """Foundation's preparation, for a helper process that then imports the module.

    Foundation puts the prepared source first on ``sys.path``. When the
    environment names the shared source store (the host of a Windows
    installation has that name) the source is an object of the store; else it
    is a checkout in Foundation's cache. Python would write ``__pycache__``
    beside each file that the process imports from there, and Foundation counts
    every such file as a change of the source
    (``sources.shared.clean_checkout``). It refuses a changed store object from
    then on ("Shared source contents changed"): no conversation with that
    module starts, and a component update fails. POSIX seals the folders of a
    store object. A read-only attribute on a Windows folder does not stop a new
    entry.

    So from this call on an import in the process writes no bytecode. A Python
    process that it starts afterwards gets ``PYTHONDONTWRITEBYTECODE`` with the
    helper's environment, and an import there writes none either. Code that
    calls ``py_compile`` or ``compileall`` itself still writes. A child that is started with ``-E`` or
    ``-I``, or with an environment that leaves the setting out, ignores it.

    Call this only in a helper process that ends after its one task. Both
    settings stay for the rest of the process. Called in the host's own
    process, they would reach every child of the host, and no helper would
    keep bytecode. Workers and update stages have the rule for their whole
    life, from ``update_sources.store_environment``.

    A helper keeps the bytecode of what it imported from its own environment
    before this call. It keeps none for anything that it imports after it, for
    example the prepared module and each package that the module is the first
    to import.
    """
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    return await bundle.prepare(**options)
