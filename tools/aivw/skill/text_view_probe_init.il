; Create only the disposable library used by the AIVW text-view probe.
let((libraryName libraryPath libraryObject)
  libraryName=getShellEnvVar("AIVW_PROBE_LIBRARY")
  libraryPath=getShellEnvVar("AIVW_PROBE_LIBRARY_PATH")
  unless(and(libraryName libraryPath)
    error("AIVW text-view probe library environment is incomplete"))
  libraryObject=ddGetObj(libraryName)
  unless(libraryObject
    libraryObject=ddCreateLib(libraryName libraryPath))
  unless(and(libraryObject ddIsObjWritable(libraryObject))
    error("AIVW disposable probe library is unavailable or not writable"))
  printf("AIVW_TEXT_VIEW_LIBRARY_READY library=%s path=%s\n"
    libraryName ddGetObjWritePath(libraryObject))
)
exit(0)
