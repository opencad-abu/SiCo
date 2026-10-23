; PDK maintenance adapter: fixed diagnostic operations, no executable request code.
unless(boundp('aiPdkCdfOwned) aiPdkCdfOwned=makeTable("aiPdkCdfOwned" nil))
procedure(aiPdkCdfWorkspace(apvWorkspace)
  unless(and(stringp(apvWorkspace) simplifyFilename(apvWorkspace)==simplifyFilename(getWorkingDir()))
    error("CDF validation workspace differs from current project")))
procedure(aiPdkCdfInspect(apvTarget apvNames apvToken apvWorkspace)
  prog((apvText)
    aiPdkCdfWorkspace(apvWorkspace)
    apvText=aiCdfInspect(apvTarget nil nil apvNames 0 64 strcat("cdf:" apvToken))
    when(aiCdfCaptures[strcat("cdf:" apvToken)]
      aiPdkCdfOwned[strcat("cdf:" apvToken)]=t)
    return(aiCdfPut(apvText strcat("cdf-transfer:" apvToken)))))
procedure(aiPdkCdfCase(apvRef apvRequest apvTarget apvRows apvTransition apvToken apvWorkspace)
  prog((apvText apvTask)
    aiPdkCdfWorkspace(apvWorkspace)
    unless(and(length(apvRows)<=16 length(apvTransition)<=16)
      error("CDF validation phase exceeds 16 parameters"))
    ; Auxiliary tests are always background by project policy, independent of design task mode.
    apvTask="task:pdk_cdf_validation"
    aiCircuitTasks[apvTask]="background"
    unless(aiPdkCdfOwned[apvRef] error("CDF capture is not owned by this validation adapter"))
    unwindProtect(
      apvText=aiCdfProbe(apvRef apvTask apvRequest apvTarget apvRows
        "builtin:cdf-callbacks:v1" strcat("cdf-probe:" apvToken) apvTransition)
      progn(
        when(aiCdfCaptures[apvRef]
          remove(apvRef aiCdfCaptures) aiCdfCaptureCount=aiCdfCaptureCount-1)
        remove(apvRef aiPdkCdfOwned)))
    return(aiCdfPut(apvText strcat("cdf-transfer:" apvToken)))))
