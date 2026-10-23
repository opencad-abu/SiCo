; Workspace-owned auxiliary tests. This helper never opens an editor or asks for task mode.
procedure(aiTestWorkspaceLibrary()
  prog((atwWorkspace atwPath atwLibrary atwObserved)
    atwWorkspace=simplifyFilename(getWorkingDir())
    unless(and(stringp(atwWorkspace) isDir(atwWorkspace))
      error("Test workspace is unavailable"))
    atwPath=strcat(atwWorkspace "/SicoTest")
    ; Resolve links: a local-looking symlink must not redirect probes elsewhere.
    unless(simplifyFilename(atwPath)==atwPath
      error("SicoTest path resolves outside its workspace location"))
    atwLibrary=ddGetObj("SicoTest")
    unless(atwLibrary
      when(or(isDir(atwPath) isFile(atwPath))
        error("SicoTest path exists but is not a registered library"))
      unless(isWritable(atwWorkspace) error("Test workspace is not writable"))
      atwLibrary=ddCreateLib("SicoTest" atwPath)
      unless(atwLibrary error("Cannot create workspace SicoTest library")))
    atwObserved=ddGetObjReadPath(atwLibrary)
    unless(and(stringp(atwObserved) simplifyFilename(atwObserved)==atwPath)
      error("SicoTest library resolves outside the current workspace; do not rebind it"))
    unless(ddIsObjWritable(atwLibrary) error("Workspace SicoTest library is not writable"))
    return(atwLibrary)))
