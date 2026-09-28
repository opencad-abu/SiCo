"""Generate the protected SKILL worker invocation."""

def catalog_worker_script():
    from cadcontext import worker_call
    return worker_call("cadRuntimeCatalog") + "exit()\n"
